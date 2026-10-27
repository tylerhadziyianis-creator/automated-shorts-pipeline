"""
Isolated generation worker — runs as subprocess so all PIL/Replicate/ElevenLabs
memory is freed on exit. Writes assembled video_data to output JSON path.
"""
import sys
import os
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("generate_worker")


def _generate_ai_premise() -> dict:
    """Ask Claude to invent a fresh AITA premise when Reddit pool is exhausted."""
    import anthropic, re, uuid
    from config import ANTHROPIC_API_KEY, CLAUDE_MODEL

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=512,
        messages=[{
            "role": "user",
            "content": """Invent a realistic, original AITA story premise. It must feel like a real Reddit post — specific details, relatable conflict, clear wrongdoer.

Themes that perform well: family entitlement, weddings, money/inheritance, betrayal, boundary violations, uninviting someone, secret-keeping, moving in/out.

Return JSON only:
{"title": "AITA for [specific action]?", "body": "2-4 paragraph story body, first person, 200-300 words, specific details, ends with asking if they're TA"}

No commentary, just JSON."""
        }]
    )
    raw = response.content[0].text.strip()
    match = re.search(r'\{.*\}', raw, re.DOTALL)
    if not match:
        raise RuntimeError("AI premise generation returned no JSON")
    data = json.loads(match.group())
    return {
        "id": f"ai_{uuid.uuid4().hex[:8]}",
        "title": data["title"],
        "body": data["body"],
        "upvotes": 0,
        "url": "",
        "source": "ai_generated",
    }


def main():
    if len(sys.argv) < 3:
        print("Usage: generate_worker.py '<used_ids_json>' '<output_path>'", file=sys.stderr)
        sys.exit(1)

    used_ids = set(json.loads(sys.argv[1]))
    output_path = sys.argv[2]

    from config import OUTPUT_DIR, DRY_RUN
    from reddit_scraper import get_aita_post
    from script_generator import generate_video_script
    from tts_engine import generate_voiceover
    from image_generator import generate_scene_images
    from video_assembler import assemble_video, generate_thumbnail

    Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)

    # Get story
    from reddit_scraper import _load_shared_used_ids, _save_shared_used_id
    post = get_aita_post(used_ids)
    if post is None:
        post = _generate_ai_premise()
        logger.info(f"Using AI-generated premise: {post['title'][:60]}")

    # Generate script + scenes
    script_data = generate_video_script(post)
    title = script_data["title"]
    description = script_data["description"]
    pinned_comment = script_data["pinned_comment"]
    narration = script_data["narration"]
    scenes = script_data["scenes"]
    hashtags = script_data["hashtags"]

    # Base filename
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    safe_title = "".join(c for c in title if c.isalnum() or c in " _-").strip()[:35].strip()
    base_name = f"aita_{safe_title}_{timestamp}"

    audio_path = os.path.join(OUTPUT_DIR, f"{base_name}.mp3")
    video_path = os.path.join(OUTPUT_DIR, f"{base_name}.mp4")
    thumbnail_path = os.path.join(OUTPUT_DIR, f"{base_name}_thumb.jpg")

    if DRY_RUN:
        logger.info(f"[DRY RUN] Skipping media generation for: {title}")
        # Write placeholder paths
        with open(output_path, "w") as f:
            json.dump({
                "video_data": {
                    "post_id": post["id"],
                    "title": title,
                    "narration": narration,
                    "hashtags": hashtags,
                    "story_tags": script_data.get("story_tags", []),
                    "video_path": video_path,
                    "thumbnail_path": thumbnail_path,
                    "audio_path": audio_path,
                    "description": description,
                    "pinned_comment": pinned_comment,
                    "dry_run": True,
                }
            }, f)
        logger.info(f"[DRY RUN] Output written to {output_path}")
        sys.exit(0)

    # Generate voiceover
    logger.info("Generating voiceover...")
    audio_path = generate_voiceover(narration, audio_path)

    # Generate scene images
    logger.info(f"Generating {len(scenes)} scene images via Flux...")
    scene_images = generate_scene_images(scenes, base_name)

    # Assemble video
    logger.info("Assembling video...")
    video_path = assemble_video(narration, audio_path, scene_images, f"{base_name}.mp4")

    # Generate thumbnail
    logger.info("Generating thumbnail...")
    thumbnail_path = generate_thumbnail(title, scene_images, thumbnail_path)

    # Write result
    with open(output_path, "w") as f:
        json.dump({
            "video_data": {
                "post_id": post["id"],
                "title": title,
                "narration": narration,
                "hashtags": hashtags,
                "story_tags": script_data.get("story_tags", []),
                "video_path": video_path,
                "thumbnail_path": thumbnail_path,
                "audio_path": audio_path,
                "description": description,
                "pinned_comment": pinned_comment,
            }
        }, f)

    logger.info(f"Worker done: {title}")
    sys.exit(0)


if __name__ == "__main__":
    main()
