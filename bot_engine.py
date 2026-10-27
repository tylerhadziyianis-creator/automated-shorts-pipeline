import os
import sys
import json
import logging
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from config import OUTPUT_DIR, DRY_RUN
from reddit_scraper import get_aita_post, FALLBACK_STORIES
from script_generator import generate_video_script
from scheduler import load_state, save_state, queue_video, get_due_item, mark_uploaded, should_generate
from uploader import upload_youtube
from telegram_notifier import notify_upload, notify_generating, notify_error
from performance_tracker import log_upload, fetch_and_analyze

logger = logging.getLogger(__name__)

_BOT_DIR = os.path.dirname(os.path.abspath(__file__))


class AITABot:
    def __init__(self):
        Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)
        Path("./logs").mkdir(parents=True, exist_ok=True)

    def run_cycle(self, force: bool = False):
        logger.info("=== AITA Bot Cycle Start ===")
        state = load_state()

        # Generate new content if queue is running low
        if should_generate(state):
            logger.info("Queue low — generating new video")
            self._generate_and_queue(state)
            state = load_state()  # Reload after generation so due-item check sees new queue

        # Upload due item
        if force:
            # Pick the first unuploaded item regardless of schedule
            due = next((i for i in state["queue"] if not i.get("uploaded")), None)
            if due:
                logger.info("Force upload: bypassing schedule check")
        else:
            due = get_due_item(state)

        if not due:
            logger.info("No uploads due right now")
        else:
            self._upload_item(state, due)
            # After a force upload, always generate a replacement to keep the queue healthy
            if force:
                state = load_state()
                logger.info("Force upload complete — generating replacement video")
                self._generate_and_queue(state)

        save_state(state)
        logger.info(f"=== Cycle complete. Total uploads: {state['upload_count']} ===")

    def generate_only(self):
        """Pre-generate a video without uploading. Called before posting slots."""
        state = load_state()
        if should_generate(state):
            logger.info("Pre-generating video for next posting slot...")
            self._generate_and_queue(state)
        else:
            logger.info("Pre-generation skipped — queue already has enough videos")

    def _generate_and_queue(self, state: dict):
        """Generate video in isolated subprocess, queue result."""
        used_ids = set(state.get("used_post_ids", []))

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tmp:
            output_json = tmp.name

        try:
            worker_path = os.path.join(_BOT_DIR, "generate_worker.py")
            cmd = [sys.executable, worker_path, json.dumps(list(used_ids)), output_json]

            logger.info("Launching generation worker...")
            result = subprocess.run(cmd, cwd=_BOT_DIR, timeout=1800)

            if result.returncode != 0:
                logger.error(f"Worker exited with code {result.returncode}")
                notify_error("Generation worker failed", f"Exit code {result.returncode}")
                return

            with open(output_json) as f:
                worker_output = json.load(f)

            video_data = worker_output.get("video_data")
            if not video_data:
                logger.error("Worker produced no video data")
                return

            notify_generating(video_data.get("title", ""))
            state = queue_video(state, video_data)
            save_state(state)

        except subprocess.TimeoutExpired:
            logger.error("Worker timed out")
            notify_error("Generation timed out", "Killed after 1200s")
        except Exception as e:
            logger.error(f"Generation failed: {e}", exc_info=True)
            notify_error("Generation failed", str(e))
        finally:
            try:
                os.unlink(output_json)
            except Exception:
                pass

    def _upload_item(self, state: dict, item: dict):
        video_data = item["video_data"]
        title = video_data.get("title", "Unknown")
        video_path = video_data.get("video_path", "")
        thumbnail_path = video_data.get("thumbnail_path", "")
        hashtags = video_data.get("hashtags", [])
        description = video_data.get("description", "Tell me I'm wrong... 👀\n\nDrop your verdict in the comments.")
        pinned_comment = video_data.get("pinned_comment")

        logger.info(f"Uploading: {title}")

        try:
            result = upload_youtube(video_path, thumbnail_path, title, description, hashtags, pinned_comment=pinned_comment)
            state = mark_uploaded(state, video_data, result)
            save_state(state)  # Save immediately after upload

            url = result.get("url", "")
            video_id = result.get("video_id", "")
            logger.info(f"Uploaded: {url}")
            notify_upload(title, url, state["upload_count"])

            # Log for performance analysis
            if video_id:
                try:
                    log_upload(
                        video_id=video_id,
                        title=title,
                        post_id=video_data.get("post_id", ""),
                        story_tags=video_data.get("story_tags", []),
                    )
                except Exception as e:
                    logger.warning(f"Performance log failed: {e}")

            # Run analysis every 30 uploads
            if state["upload_count"] % 30 == 0 and state["upload_count"] > 0:
                logger.info("Running performance analysis...")
                try:
                    fetch_and_analyze()
                except Exception as e:
                    logger.warning(f"Performance analysis failed: {e}")

        except Exception as e:
            logger.error(f"Upload failed for '{title}': {e}", exc_info=True)
            notify_error(f"Upload failed: {title[:50]}", str(e))
