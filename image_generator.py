import logging
import os
import time
import requests
import replicate
from pathlib import Path
from config import REPLICATE_API_TOKEN, OUTPUT_DIR

logger = logging.getLogger(__name__)

# Flux Schnell — fast, cheap, high quality
FLUX_MODEL = "black-forest-labs/flux-schnell:c846a69991daf4c0e5d016514849d14ee5b2e6846ce6b9d6f21369e564cfe51e"

# Style suffix appended to every prompt for visual consistency
STYLE_SUFFIX = "cinematic lighting, shallow depth of field, muted warm tones, ultra realistic, 9:16 portrait aspect ratio, no text, no watermarks"


def generate_scene_images(scenes: list[dict], base_filename: str) -> list[str]:
    """
    Generate one image per scene using Flux on Replicate.
    Returns list of local file paths in scene order.
    Falls back to solid color placeholder if generation fails.
    """
    os.environ["REPLICATE_API_TOKEN"] = REPLICATE_API_TOKEN
    image_paths = []

    for scene in scenes:
        scene_num = scene.get("scene_num", len(image_paths) + 1)
        raw_prompt = scene.get("image_prompt", "")

        # Clean prompt and append style suffix if not already there
        prompt = raw_prompt.strip()
        if "cinematic lighting" not in prompt.lower():
            prompt = f"{prompt}, {STYLE_SUFFIX}"

        output_path = os.path.join(OUTPUT_DIR, f"{base_filename}_scene{scene_num}.jpg")

        success = False
        for attempt in range(5):
            try:
                logger.info(f"Generating image for scene {scene_num} (attempt {attempt+1})...")
                image_url = _generate_flux_image(prompt)
                _download_image(image_url, output_path)
                image_paths.append(output_path)
                logger.info(f"Scene {scene_num} image saved: {output_path}")
                success = True
                break
            except Exception as e:
                err = str(e)
                if "429" in err or "throttled" in err.lower() or "rate limit" in err.lower():
                    wait = 12 * (attempt + 1)  # 12s, 24s, 36s, 48s, 60s
                    logger.warning(f"Rate limited on scene {scene_num} — waiting {wait}s before retry")
                    time.sleep(wait)
                else:
                    logger.warning(f"Scene {scene_num} failed (attempt {attempt+1}): {e} — retrying in 15s")
                    time.sleep(15)

        if not success:
            raise RuntimeError(f"Scene {scene_num} failed after all retries — aborting video to prevent blank frames")

        # Spacing between requests
        time.sleep(5)

    return image_paths


IMAGE_TIMEOUT_SECONDS = 90  # Max time to wait for a single Replicate prediction


def _generate_flux_image(prompt: str) -> str:
    """Run Flux Schnell on Replicate and return the image URL."""
    import concurrent.futures

    def _run():
        output = replicate.run(
            FLUX_MODEL,
            input={
                "prompt": prompt,
                "aspect_ratio": "9:16",
                "output_format": "jpg",
                "output_quality": 85,
                "num_inference_steps": 4,
            }
        )
        item = output[0] if isinstance(output, (list, tuple)) else output
        if hasattr(item, "url"):
            return item.url
        return str(item)

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(_run)
        try:
            return future.result(timeout=IMAGE_TIMEOUT_SECONDS)
        except concurrent.futures.TimeoutError:
            raise RuntimeError(f"Replicate prediction timed out after {IMAGE_TIMEOUT_SECONDS}s")


def _download_image(url: str, output_path: str):
    """Download image from URL to local path."""
    response = requests.get(url, timeout=60)
    response.raise_for_status()
    with open(output_path, "wb") as f:
        f.write(response.content)


