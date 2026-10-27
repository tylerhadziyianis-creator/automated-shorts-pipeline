"""
Performance tracker — logs uploaded videos and after 30+,
analyzes which story patterns perform best via YouTube Analytics.
Weights the Reddit scraper toward winning patterns.
"""
import json
import os
import logging
from datetime import datetime, timezone
from config import CREDENTIALS_FILE

logger = logging.getLogger(__name__)

PERFORMANCE_FILE = "./performance_log.json"
ANALYSIS_THRESHOLD = 30  # Analyze after this many uploads


def log_upload(video_id: str, title: str, post_id: str, story_tags: list[str]):
    """Record an uploaded video for later performance analysis."""
    data = _load()
    data["videos"].append({
        "video_id": video_id,
        "title": title,
        "post_id": post_id,
        "story_tags": story_tags,
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
        "views": None,
        "retention": None,
        "fetched_at": None,
    })
    _save(data)


def fetch_and_analyze(min_videos: int = ANALYSIS_THRESHOLD) -> dict | None:
    """
    Pull view counts from YouTube Analytics for logged videos.
    Returns weighted story preferences if enough data exists, else None.
    """
    data = _load()
    videos = [v for v in data["videos"] if v.get("video_id")]

    if len(videos) < min_videos:
        logger.info(f"Only {len(videos)} videos logged — need {min_videos} to analyze")
        return None

    try:
        youtube = _get_youtube_service()
        video_ids = [v["video_id"] for v in videos if not v.get("views")]

        if not video_ids:
            logger.info("All videos already have view data")
        else:
            # Fetch in batches of 50
            for i in range(0, len(video_ids), 50):
                batch = video_ids[i:i+50]
                response = youtube.videos().list(
                    part="statistics",
                    id=",".join(batch)
                ).execute()

                stats_map = {
                    item["id"]: item.get("statistics", {})
                    for item in response.get("items", [])
                }

                for v in data["videos"]:
                    if v["video_id"] in stats_map:
                        v["views"] = int(stats_map[v["video_id"]].get("viewCount", 0))
                        v["fetched_at"] = datetime.now(timezone.utc).isoformat()

            _save(data)

    except Exception as e:
        logger.warning(f"YouTube Analytics fetch failed: {e}")
        return None

    # Analyze which story tags correlate with high views
    tag_performance = {}
    for v in data["videos"]:
        views = v.get("views") or 0
        for tag in v.get("story_tags", []):
            if tag not in tag_performance:
                tag_performance[tag] = {"total_views": 0, "count": 0}
            tag_performance[tag]["total_views"] += views
            tag_performance[tag]["count"] += 1

    # Calculate average views per tag
    tag_averages = {}
    for tag, stats in tag_performance.items():
        if stats["count"] > 0:
            tag_averages[tag] = stats["total_views"] / stats["count"]

    if not tag_averages:
        return None

    # Sort by performance
    sorted_tags = sorted(tag_averages.items(), key=lambda x: x[1], reverse=True)
    top_tags = [tag for tag, _ in sorted_tags[:5]]

    logger.info(f"Top performing story types: {top_tags}")

    analysis = {
        "top_tags": top_tags,
        "tag_averages": tag_averages,
        "total_videos_analyzed": len([v for v in data["videos"] if v.get("views") is not None]),
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
    }

    data["latest_analysis"] = analysis
    _save(data)

    return analysis


def get_preferred_tags() -> list[str] | None:
    """Return top-performing story tags if analysis exists."""
    data = _load()
    analysis = data.get("latest_analysis")
    if not analysis:
        return None
    return analysis.get("top_tags")


def _load() -> dict:
    if os.path.exists(PERFORMANCE_FILE):
        with open(PERFORMANCE_FILE) as f:
            return json.load(f)
    return {"videos": [], "latest_analysis": None}


def _save(data: dict):
    with open(PERFORMANCE_FILE, "w") as f:
        json.dump(data, f, indent=2, default=str)


def _get_youtube_service():
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    creds = Credentials.from_authorized_user_file(CREDENTIALS_FILE)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return build("youtube", "v3", credentials=creds)
