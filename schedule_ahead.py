"""
Run this manually when you know you'll be offline during upcoming posting slots.
Generates videos now and uploads them as YouTube-scheduled (private + publishAt),
so YouTube publishes them automatically at the right time with no bot/internet needed.

Usage: python3 schedule_ahead.py
Generates one video for each of tomorrow's posting slots (from config.POSTING_HOURS).
"""
import sys
import json
import logging
from datetime import datetime, timezone, timedelta

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("schedule_ahead")

from config import POSTING_HOURS, STATE_FILE
from scheduler import load_state, save_state
from reddit_scraper import get_aita_post, FALLBACK_STORIES
from script_generator import generate_video_script
from tts_engine import generate_voiceover
from image_generator import generate_scene_images
from video_assembler import assemble_video, generate_thumbnail
from uploader import upload_youtube
import os
from pathlib import Path
from config import OUTPUT_DIR


def next_day_slot_times():
    now = datetime.now(timezone.utc)
    tomorrow = now.date() + timedelta(days=1)
    return [
        datetime(tomorrow.year, tomorrow.month, tomorrow.day, hour, 0, tzinfo=timezone.utc)
        for hour in sorted(POSTING_HOURS)
    ]


def generate_one(used_ids):
    post = get_aita_post(used_ids)
    if post is None:
        import random
        available = [s for s in FALLBACK_STORIES if s["id"] not in used_ids]
        post = random.choice(available or FALLBACK_STORIES)

    script_data = generate_video_script(post)
    title = script_data["title"]
    description = script_data["description"]
    narration = script_data["narration"]
    scenes = script_data["scenes"]
    hashtags = script_data["hashtags"]

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    safe_title = "".join(c for c in title if c.isalnum() or c in " _-").strip()[:35].strip()
    base_name = f"aita_{safe_title}_{timestamp}"

    audio_path = os.path.join(OUTPUT_DIR, f"{base_name}.mp3")
    audio_path = generate_voiceover(narration, audio_path)

    scene_images = generate_scene_images(scenes, base_name)

    video_path = assemble_video(narration, audio_path, scene_images, f"{base_name}.mp4")
    thumbnail_path = generate_thumbnail(title, scene_images, os.path.join(OUTPUT_DIR, f"{base_name}_thumb.jpg"))

    return post, {
        "title": title,
        "description": description,
        "video_path": video_path,
        "thumbnail_path": thumbnail_path,
        "hashtags": hashtags,
    }


def main():
    Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)
    state = load_state()
    used_ids = set(state.get("used_post_ids", []))

    slots = next_day_slot_times()
    logger.info(f"Scheduling {len(slots)} videos for: {[s.strftime('%Y-%m-%d %H:%M UTC') for s in slots]}")

    for slot_time in slots:
        logger.info(f"Generating video for slot {slot_time.strftime('%H:%M UTC')}...")
        try:
            post, video_data = generate_one(used_ids)
            used_ids.add(post["id"])

            publish_at = slot_time.strftime("%Y-%m-%dT%H:%M:%SZ")
            result = upload_youtube(
                video_data["video_path"],
                video_data["thumbnail_path"],
                video_data["title"],
                video_data["description"],
                video_data["hashtags"],
                publish_at=publish_at,
            )
            logger.info(f"Scheduled: {result['url']} -> publishes at {publish_at}")

            state["used_post_ids"] = list(used_ids)
            state["queue"].append({
                "video_data": video_data,
                "scheduled_time": slot_time.isoformat(),
                "uploaded": True,
                "uploaded_at": datetime.now(timezone.utc).isoformat(),
                "upload_result": result,
            })
            state["upload_count"] = state.get("upload_count", 0) + 1
            save_state(state)

        except Exception as e:
            logger.error(f"Failed to schedule video for {slot_time}: {e}", exc_info=True)

    logger.info("Done. Don't run the regular bot until you're back online, or it may generate extra videos for the same day.")


if __name__ == "__main__":
    main()
