import os
import re
import json
import logging
import subprocess
import sys
import shutil
import textwrap
import random
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from config import OUTPUT_DIR

logger = logging.getLogger(__name__)

VIDEO_WIDTH = 1080
VIDEO_HEIGHT = 1920
FPS = 30
FONT_SIZE = 56
CAPTION_Y = int(VIDEO_HEIGHT * 0.80)
WORDS_PER_CHUNK = 4

MUSIC_DIR = os.path.join(os.path.dirname(__file__), "assets", "music")


def _ffmpeg():
    return shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"

def _ffprobe():
    return shutil.which("ffprobe") or "/opt/homebrew/bin/ffprobe"


HOOK_OVERLAY_DURATION = 1.5  # seconds the hook text stays on screen
LIKE_OVERLAY_START = 0.45   # fraction of video duration when like overlay appears
LIKE_OVERLAY_DURATION = 2.5 # seconds it stays visible
LIKE_OVERLAY_TEXT = "👍 Like if you'd be mad too"


def _extract_hook_text(narration: str) -> str:
    """Pull 3-5 words from the hook line to use as overlay text."""
    match = re.match(r'^\[.*?\]\s*"?(.+?)[".]', narration.strip())
    if not match:
        match = re.match(r'^\[.*?\]\s*(.+)', narration.strip())
    if not match:
        return ""
    hook = match.group(1).strip().rstrip('."')
    words = hook.split()
    # Take first 3 words, uppercase — keeps it tight within frame
    text = " ".join(words[:3]).upper()
    return text[:18] if len(text) > 18 else text


def _draw_like_overlay(frame: Image.Image, alpha_frac: float) -> Image.Image:
    """Draw 'Like if you'd be mad too' overlay centered in lower third."""
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 54)
    except Exception:
        font = ImageFont.load_default()

    text = LIKE_OVERLAY_TEXT
    alpha = int(255 * alpha_frac)
    overlay = Image.new("RGBA", frame.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    x = (VIDEO_WIDTH - text_w) // 2
    y = int(VIDEO_HEIGHT * 0.62)

    pad = 18
    draw.rounded_rectangle(
        [x - pad, y - pad, x + text_w + pad, y + text_h + pad],
        radius=14, fill=(30, 30, 30, int(180 * alpha_frac))
    )
    draw.text((x, y), text, font=font, fill=(255, 255, 255, alpha))

    return Image.alpha_composite(frame.convert("RGBA"), overlay).convert("RGB")


def _draw_hook_overlay(frame: Image.Image, text: str, alpha_frac: float) -> Image.Image:
    """Draw bold hook text centered in upper third of frame with fade-out."""
    if not text:
        return frame

    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 72)
    except Exception:
        font = ImageFont.load_default()

    alpha = int(255 * alpha_frac)
    overlay = Image.new("RGBA", frame.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    x = (VIDEO_WIDTH - text_w) // 2
    y = int(VIDEO_HEIGHT * 0.18)

    pad = 20
    draw.rounded_rectangle(
        [x - pad, y - pad, x + text_w + pad, y + text_h + pad],
        radius=16, fill=(0, 0, 0, int(160 * alpha_frac))
    )
    # Shadow
    for dx, dy in [(-2, 2), (2, 2), (-2, -2), (2, -2)]:
        draw.text((x + dx, y + dy), text, font=font, fill=(0, 0, 0, alpha))
    draw.text((x, y), text, font=font, fill=(255, 255, 255, alpha))

    return Image.alpha_composite(frame.convert("RGBA"), overlay).convert("RGB")


def assemble_video(narration: str, audio_path: str, scene_images: list[str], output_filename: str) -> str:
    """
    Assemble final video:
    - Each scene image shown for its proportional duration
    - Ken Burns zoom on each image
    - Synced word-level subtitles
    - Background music mix
    """
    Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)
    output_path = os.path.join(OUTPUT_DIR, output_filename)

    audio_duration = _get_audio_duration(audio_path)
    timed_chunks = _get_word_timestamps(audio_path, narration, audio_duration)
    image_schedule = _build_image_schedule(scene_images, audio_duration)
    music_track = _pick_music()
    hook_text = _extract_hook_text(narration)

    _render_video(narration, audio_path, audio_duration, timed_chunks, image_schedule, music_track, output_path, hook_text)

    logger.info(f"Video assembled: {output_path}")
    return output_path


def _get_word_timestamps(audio_path: str, script: str, duration: float) -> list[tuple]:
    """Run Whisper in subprocess for word timestamps. Falls back to estimated timing."""
    whisper_script = """
import whisper, json, sys, os
os.environ["PATH"] = "/opt/homebrew/bin:" + os.environ.get("PATH", "")
model = whisper.load_model("base")
result = model.transcribe(sys.argv[1], word_timestamps=True)
words = []
for seg in result.get("segments", []):
    for w in seg.get("words", []):
        words.append({"word": w["word"].strip(), "start": w["start"], "end": w["end"]})
print(json.dumps(words))
"""
    try:
        result = subprocess.run(
            [sys.executable, "-c", whisper_script, audio_path],
            capture_output=True, text=True, timeout=120
        )
        if result.returncode == 0:
            word_times = json.loads(result.stdout)
            chunks = []
            for i in range(0, len(word_times), WORDS_PER_CHUNK):
                group = word_times[i:i + WORDS_PER_CHUNK]
                text = " ".join(w["word"] for w in group)
                chunks.append((group[0]["start"], group[-1]["end"], text))
            return chunks
    except Exception as e:
        logger.warning(f"Whisper failed: {e}")

    # Fallback: estimate timing
    words = script.split()
    chunks_text = [" ".join(words[i:i+WORDS_PER_CHUNK]) for i in range(0, len(words), WORDS_PER_CHUNK)]
    wps = len(words) / max(duration, 1)
    result_chunks = []
    t = 0.0
    for chunk in chunks_text:
        dur = len(chunk.split()) / wps
        result_chunks.append((t, t + dur, chunk))
        t += dur
    return result_chunks


def _build_image_schedule(scene_images: list[str], duration: float) -> list[tuple]:
    """
    Divide duration evenly across scene images — each image gets equal screen time
    with alternating zoom directions. No looping, each image is a distinct scene.
    Returns [(start, end, path, zoom_dir)]
    """
    if not scene_images:
        return []

    n = len(scene_images)
    per_image = duration / n
    zoom_dirs = ["in", "out", "left", "right", "in", "out", "right", "left", "in", "left", "out", "right"]
    schedule = []

    for i, path in enumerate(scene_images):
        start = i * per_image
        end = (i + 1) * per_image
        zoom = zoom_dirs[i % len(zoom_dirs)]
        schedule.append((start, end, path, zoom))

    return schedule


def _apply_ken_burns(img: Image.Image, progress: float, direction: str, target_w: int, target_h: int) -> Image.Image:
    ZOOM = 0.10
    src_w, src_h = img.size

    if direction == "in":
        scale = 1.0 + ZOOM * progress
    elif direction == "out":
        scale = 1.0 + ZOOM * (1.0 - progress)
    else:
        scale = 1.0 + ZOOM * 0.5

    crop_w = int(src_w / scale)
    crop_h = int(src_h / scale)

    if direction == "left":
        cx = int((src_w - crop_w) * progress)
        cy = (src_h - crop_h) // 2
    elif direction == "right":
        cx = int((src_w - crop_w) * (1.0 - progress))
        cy = (src_h - crop_h) // 2
    else:
        cx = (src_w - crop_w) // 2
        cy = (src_h - crop_h) // 2

    cropped = img.crop((cx, cy, cx + crop_w, cy + crop_h))
    return cropped.resize((target_w, target_h), Image.LANCZOS)


def _render_video(narration, audio_path, duration, timed_chunks, image_schedule, music_track, output_path, hook_text=""):
    """Frame-by-frame render: Ken Burns images + subtitles → ffmpeg encoder."""

    # Pre-load and resize all scene images
    image_cache = {}
    for (start, end, path, zoom_dir) in image_schedule:
        if path not in image_cache:
            try:
                zoom_w = int(VIDEO_WIDTH * 1.12)
                zoom_h = int(VIDEO_HEIGHT * 1.12)
                img = Image.open(path).convert("RGB").resize((zoom_w, zoom_h), Image.LANCZOS)
                image_cache[path] = img
            except Exception as e:
                logger.warning(f"Could not load image {path}: {e}")
                image_cache[path] = Image.new("RGB", (VIDEO_WIDTH, VIDEO_HEIGHT), (15, 10, 25))

    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", FONT_SIZE)
        font_bold = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", FONT_SIZE + 8)
    except Exception:
        font = ImageFont.load_default()
        font_bold = font

    total_frames = int(duration * FPS)

    # FFmpeg encoder
    encode_cmd = [
        _ffmpeg(), "-y",
        "-f", "rawvideo", "-vcodec", "rawvideo",
        "-s", f"{VIDEO_WIDTH}x{VIDEO_HEIGHT}",
        "-pix_fmt", "rgb24", "-r", str(FPS),
        "-i", "pipe:0",
        "-i", audio_path,
    ]

    input_idx = 2
    filter_parts = ["[1:a]volume=1.3[voice]"]
    mix_inputs = ["[voice]"]

    if music_track:
        encode_cmd += ["-stream_loop", "-1", "-i", music_track]
        filter_parts.append(f"[{input_idx}:a]volume=0.08,atrim=duration={duration}[music]")
        mix_inputs.append("[music]")
        input_idx += 1

    n_mix = len(mix_inputs)
    filter_parts.append(f"{''.join(mix_inputs)}amix=inputs={n_mix}:duration=first:dropout_transition=0[aout]")

    encode_cmd += [
        "-filter_complex", ";".join(filter_parts),
        "-map", "0:v", "-map", "[aout]",
        "-c:v", "libx264", "-preset", "fast", "-crf", "22",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        output_path,
    ]

    encoder = subprocess.Popen(encode_cmd, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)

    for frame_idx in range(total_frames):
        t = frame_idx / FPS

        # Find current image
        current_img = None
        current_zoom = "in"
        img_start = 0.0
        img_end = duration

        for (start, end, path, zoom_dir) in image_schedule:
            if start <= t < end:
                current_img = image_cache.get(path)
                current_zoom = zoom_dir
                img_start = start
                img_end = end
                break

        if current_img is None:
            # Last frame fallback
            frame = Image.new("RGB", (VIDEO_WIDTH, VIDEO_HEIGHT), (15, 10, 25))
        else:
            img_duration = max(img_end - img_start, 0.001)
            progress = (t - img_start) / img_duration
            frame = _apply_ken_burns(current_img, progress, current_zoom, VIDEO_WIDTH, VIDEO_HEIGHT)

        # Scene 1 hook overlay — fades out after HOOK_OVERLAY_DURATION seconds
        if hook_text and t < HOOK_OVERLAY_DURATION:
            alpha_frac = max(0.0, 1.0 - (t / HOOK_OVERLAY_DURATION) * 0.5)
            frame = _draw_hook_overlay(frame, hook_text, alpha_frac)

        # Like overlay — appears at 45% through video, stays for LIKE_OVERLAY_DURATION seconds
        like_start = duration * LIKE_OVERLAY_START
        like_end = like_start + LIKE_OVERLAY_DURATION
        if like_start <= t < like_end:
            progress = (t - like_start) / LIKE_OVERLAY_DURATION
            # Fade in for first 20%, full opacity middle, fade out last 20%
            if progress < 0.2:
                alpha_frac = progress / 0.2
            elif progress > 0.8:
                alpha_frac = (1.0 - progress) / 0.2
            else:
                alpha_frac = 1.0
            frame = _draw_like_overlay(frame, alpha_frac)

        # Subtitle
        current_text = next((text for s, e, text in timed_chunks if s <= t < e), "")
        if current_text:
            frame = _draw_subtitle(frame, current_text, font)

        encoder.stdin.write(frame.tobytes())

    encoder.stdin.close()
    encoder.wait()


def _draw_subtitle(frame: Image.Image, text: str, font) -> Image.Image:
    """Draw subtitle text with pill background onto frame. Returns modified frame."""
    lines = textwrap.wrap(text, width=24)
    line_height = FONT_SIZE + 14
    total_h = len(lines) * line_height
    y = CAPTION_Y - total_h // 2

    for line in lines:
        # Measure text
        tmp_draw = ImageDraw.Draw(frame)
        bbox = tmp_draw.textbbox((0, 0), line, font=font)
        text_w = bbox[2] - bbox[0]
        x = (VIDEO_WIDTH - text_w) // 2
        pad = 12

        # Semi-transparent pill background
        bg = Image.new("RGBA", frame.size, (0, 0, 0, 0))
        bg_draw = ImageDraw.Draw(bg)
        bg_draw.rounded_rectangle(
            [x - pad, y - pad // 2, x + text_w + pad, y + FONT_SIZE + pad // 2],
            radius=10, fill=(0, 0, 0, 160)
        )
        frame = Image.alpha_composite(frame.convert("RGBA"), bg).convert("RGB")

        # Draw text on updated frame
        draw = ImageDraw.Draw(frame)
        draw.text((x, y), line, font=font, fill=(255, 255, 255))
        y += line_height

    return frame


def generate_thumbnail(title: str, scene_images: list[str], output_path: str) -> str:
    """Extract first scene image and overlay title as thumbnail."""
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    # Use second scene image (more dramatic than first)
    img_path = scene_images[1] if len(scene_images) > 1 else (scene_images[0] if scene_images else None)

    if img_path and os.path.exists(img_path):
        frame = Image.open(img_path).convert("RGB").resize((VIDEO_WIDTH, VIDEO_HEIGHT), Image.LANCZOS)
    else:
        frame = Image.new("RGB", (VIDEO_WIDTH, VIDEO_HEIGHT), (15, 10, 25))

    # Dark gradient overlay on bottom 45%
    overlay = Image.new("RGBA", (VIDEO_WIDTH, VIDEO_HEIGHT), (0, 0, 0, 0))
    ov_draw = ImageDraw.Draw(overlay)
    bar_top = int(VIDEO_HEIGHT * 0.55)
    for y in range(bar_top, VIDEO_HEIGHT):
        alpha = int(220 * (y - bar_top) / (VIDEO_HEIGHT - bar_top))
        ov_draw.line([(0, y), (VIDEO_WIDTH, y)], fill=(0, 0, 0, alpha))
    frame = Image.alpha_composite(frame.convert("RGBA"), overlay).convert("RGB")

    draw = ImageDraw.Draw(frame)

    # "AITA?" badge top left
    try:
        badge_font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 56)
        title_font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 68)
    except Exception:
        badge_font = ImageFont.load_default()
        title_font = badge_font

    badge_text = "AITA?"
    bbox = draw.textbbox((0, 0), badge_text, font=badge_font)
    bw = bbox[2] - bbox[0] + 32
    bh = bbox[3] - bbox[1] + 18
    draw.rounded_rectangle([40, 40, 40 + bw, 40 + bh], radius=14, fill=(220, 30, 30))
    draw.text((56, 49), badge_text, font=badge_font, fill=(255, 255, 255))

    # Title text anchored to bottom
    _draw_thumbnail_title(draw, title, title_font)

    frame.save(output_path, "JPEG", quality=92)
    return output_path


def _draw_thumbnail_title(draw, text, font):
    max_width = 960
    words = text.split()
    lines = []
    current = []
    for word in words:
        test = " ".join(current + [word])
        bbox = draw.textbbox((0, 0), test, font=font)
        if bbox[2] - bbox[0] <= max_width:
            current.append(word)
        else:
            if current:
                lines.append(" ".join(current))
            current = [word]
    if current:
        lines.append(" ".join(current))

    line_h = 78
    total_h = len(lines) * line_h
    y = VIDEO_HEIGHT - total_h - 60

    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        x = (VIDEO_WIDTH - (bbox[2] - bbox[0])) // 2
        for dx, dy in [(-3, 0), (3, 0), (0, -3), (0, 3), (-2, -2), (2, -2), (-2, 2), (2, 2)]:
            draw.text((x + dx, y + dy), line, font=font, fill=(0, 0, 0))
        draw.text((x, y), line, font=font, fill=(255, 255, 255))
        y += line_h


def _get_audio_duration(audio_path: str) -> float:
    result = subprocess.run(
        [_ffprobe(), "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", audio_path],
        capture_output=True, text=True
    )
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 55.0


def _pick_music() -> str | None:
    music_dir = Path(MUSIC_DIR)
    if not music_dir.exists():
        return None
    tracks = list(music_dir.glob("*.mp3")) + list(music_dir.glob("*.wav"))
    return str(random.choice(tracks)) if tracks else None
