import os
import random
import logging
import requests
from pathlib import Path
from config import YOUTUBE_CLIENT_SECRETS_FILE, CREDENTIALS_FILE, YOUTUBE_CHANNEL_ID, DRY_RUN

logger = logging.getLogger(__name__)

ENGAGEMENT_COMMENTS = [
    "NTA or YTA? Drop your verdict below 👇 Follow for more stories 🍿",
    "What would you have done? Let me know below 👇",
    "Be honest — NTA or YTA here? 👀",
    "Comment your verdict. Curious what you all think on this one 🍿",
    "Drop a comment — NTA or YTA?",
    "Tell me I'm wrong 👇",
]

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]


def _get_youtube_service():
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    creds = None
    if os.path.exists(CREDENTIALS_FILE):
        creds = Credentials.from_authorized_user_file(CREDENTIALS_FILE, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            print(f"\n{'='*50}")
            print(f"AUTHORIZE CHANNEL: Tell Me I'm Wrong")
            print(f"Channel ID: {YOUTUBE_CHANNEL_ID}")
            print(f"Select THIS account in the browser popup.")
            print(f"{'='*50}\n")
            flow = InstalledAppFlow.from_client_secrets_file(YOUTUBE_CLIENT_SECRETS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)

        with open(CREDENTIALS_FILE, "w") as f:
            f.write(creds.to_json())

    return build("youtube", "v3", credentials=creds)


def upload_youtube(video_path: str, thumbnail_path: str, title: str, description: str, hashtags: list[str], publish_at: str = None, pinned_comment: str = None) -> dict:
    if DRY_RUN:
        logger.info(f"[DRY RUN] Would upload: {title}")
        logger.info(f"[DRY RUN] Video: {video_path}")
        logger.info(f"[DRY RUN] Hashtags: {' '.join(hashtags)}")
        return {"status": "dry_run", "url": ""}

    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Video not found: {video_path}")

    from googleapiclient.http import MediaFileUpload

    youtube = _get_youtube_service()

    hashtag_str = " ".join(hashtags)
    full_description = f"{description}\n\n{hashtag_str}\n\n#Shorts"

    # Ensure title has #Shorts
    upload_title = title if "#Shorts" in title else f"{title} #Shorts"
    # Strip words YouTube would flag or demonetize
    _TITLE_BANNED = ["cunt", "fuck", "shit", "bitch", "ass", "dick", "pussy", "bastard", "whore", "slut"]
    import re as _re
    for word in _TITLE_BANNED:
        upload_title = _re.sub(rf'\b{word}\b', '***', upload_title, flags=_re.IGNORECASE)
    # Cap at 100 chars
    if len(upload_title) > 100:
        upload_title = upload_title[:97] + "..."

    status = {
        "privacyStatus": "private" if publish_at else "public",
        "selfDeclaredMadeForKids": False,
    }
    if publish_at:
        status["publishAt"] = publish_at  # ISO 8601 UTC, e.g. "2026-06-29T15:00:00Z"

    body = {
        "snippet": {
            "title": upload_title,
            "description": full_description,
            "tags": [h.lstrip("#") for h in hashtags] + ["Shorts", "AITA", "Reddit"],
            "categoryId": "22",  # People & Blogs
        },
        "status": status,
    }

    media = MediaFileUpload(video_path, mimetype="video/mp4", resumable=True, chunksize=1024*1024*5)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            logger.info(f"Upload progress: {int(status.progress() * 100)}%")

    video_id = response["id"]
    url = f"https://youtube.com/shorts/{video_id}"
    logger.info(f"YouTube upload success: {url}")

    # Upload thumbnail
    if thumbnail_path and os.path.exists(thumbnail_path):
        try:
            youtube.thumbnails().set(
                videoId=video_id,
                media_body=MediaFileUpload(thumbnail_path, mimetype="image/jpeg")
            ).execute()
            logger.info(f"Thumbnail uploaded for {video_id}")
        except Exception as e:
            logger.warning(f"Thumbnail upload failed: {e}")

    # Pin engagement comment
    try:
        _pin_engagement_comment(youtube, video_id, pinned_comment)
    except Exception as e:
        logger.warning(f"Comment pin failed: {e}")

    return {"status": "success", "url": url, "video_id": video_id}


def _pin_engagement_comment(youtube, video_id: str, pinned_comment: str = None):
    """Post and pin a verdict comment to drive engagement."""
    comment_text = pinned_comment if pinned_comment else random.choice(ENGAGEMENT_COMMENTS)
    comment_body = {
        "snippet": {
            "videoId": video_id,
            "topLevelComment": {
                "snippet": {
                    "textOriginal": comment_text
                }
            }
        }
    }
    comment = youtube.commentThreads().insert(
        part="snippet",
        body=comment_body
    ).execute()

    comment_id = comment["id"]
    youtube.comments().setModerationStatus(
        id=comment_id,
        moderationStatus="published",
        banAuthor=False
    ).execute()
    logger.info(f"Pinned engagement comment on {video_id}")
