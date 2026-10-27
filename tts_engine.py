import re
import requests
import logging
from config import ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ID

logger = logging.getLogger(__name__)

ELEVENLABS_URL = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}"

# Voice settings per acting direction tag
TAG_SETTINGS = {
    "shouting":   {"stability": 0.25, "similarity_boost": 0.75, "style": 0.90},
    "whispering": {"stability": 0.55, "similarity_boost": 0.85, "style": 0.30},
    "panicked":   {"stability": 0.20, "similarity_boost": 0.75, "style": 0.85},
    "confident":  {"stability": 0.55, "similarity_boost": 0.85, "style": 0.70},
    "cold":       {"stability": 0.70, "similarity_boost": 0.80, "style": 0.40},
    "desperate":  {"stability": 0.20, "similarity_boost": 0.75, "style": 0.90},
    "laughing":   {"stability": 0.30, "similarity_boost": 0.75, "style": 0.80},
    "crying":     {"stability": 0.25, "similarity_boost": 0.80, "style": 0.85},
}

# Default settings for narrator voice (no tag)
DEFAULT_SETTINGS = {"stability": 0.42, "similarity_boost": 0.80, "style": 0.60}


def generate_voiceover(script: str, output_path: str) -> str:
    """
    Generate ElevenLabs voiceover. Detects acting direction tag on first line,
    applies matching voice settings for the hook, then standard settings for the rest.
    Combines both into one audio file.
    """
    # Split hook line from rest of script
    hook_line, rest_of_script, hook_tag = _split_hook(script)

    if hook_line and hook_tag:
        logger.info(f"Hook tag detected: [{hook_tag}] — generating dramatic hook separately")
        hook_audio = output_path.replace(".mp3", "_hook.mp3")
        rest_audio = output_path.replace(".mp3", "_rest.mp3")

        _generate_segment(hook_line, hook_audio, TAG_SETTINGS.get(hook_tag, DEFAULT_SETTINGS))
        _generate_segment(rest_of_script, rest_audio, DEFAULT_SETTINGS)
        _combine_audio(hook_audio, rest_audio, output_path)

        # Cleanup temp files
        import os
        for f in [hook_audio, rest_audio]:
            try:
                os.unlink(f)
            except Exception:
                pass
    else:
        # No tag — generate full script with default settings
        clean = re.sub(r'\[.*?\]', '', script).strip()
        _generate_segment(clean, output_path, DEFAULT_SETTINGS)

    logger.info(f"TTS saved: {output_path}")
    return output_path


def _split_hook(script: str):
    """
    Extract the first tagged sentence and return (hook_text, rest_text, tag_name).
    Hook = everything up to and including the first sentence-ending punctuation.
    Rest = everything after that.
    Returns (None, full_script, None) if no tag found.
    """
    tag_match = re.match(
        r'^\[(shouting|whispering|panicked|confident|cold|desperate|laughing|crying)\]\s*',
        script.strip(), re.IGNORECASE
    )
    if not tag_match:
        return None, script, None

    tag = tag_match.group(1).lower()
    after_tag = script[tag_match.end():].strip()

    # Hook = first 1-2 sentences (up to second period/!/?)
    sentence_end = re.search(r'[.!?]["\']?\s+', after_tag)
    if sentence_end:
        # Take up to end of first sentence
        hook_line = after_tag[:sentence_end.end()].strip()
        rest = after_tag[sentence_end.end():].strip()
    else:
        # No sentence break — take first 15 words as hook
        words = after_tag.split()
        hook_line = " ".join(words[:15])
        rest = " ".join(words[15:])

    # Clean any remaining tags from rest
    rest = re.sub(r'\[.*?\]', '', rest).strip()

    return hook_line, rest, tag


def _generate_segment(text: str, output_path: str, voice_settings: dict):
    """Call ElevenLabs for a single segment."""
    if not text.strip():
        return

    # Strip characters ElevenLabs rejects — brackets, emoji, control chars
    text = re.sub(r'\[.*?\]', '', text)  # remove [tag] markers
    text = re.sub(r'[^\x00-\x7F]+', '', text)  # strip non-ASCII (emoji)
    text = text.strip()

    if not text:
        raise ValueError("TTS text is empty after sanitization")

    logger.info(f"ElevenLabs TTS — {len(text)} chars | stability={voice_settings['stability']}")

    headers = {
        "xi-api-key": ELEVENLABS_API_KEY,
        "Content-Type": "application/json",
    }
    payload = {
        "text": text,
        "model_id": "eleven_multilingual_v2",
        "voice_settings": {
            **voice_settings,
            "use_speaker_boost": True,
            "speed": 1.15,
        },
    }

    response = requests.post(ELEVENLABS_URL, json=payload, headers=headers, timeout=120)
    if not response.ok:
        logger.error(f"ElevenLabs error {response.status_code}: {response.text[:300]}")
    response.raise_for_status()

    with open(output_path, "wb") as f:
        f.write(response.content)


def _combine_audio(hook_path: str, rest_path: str, output_path: str):
    """Concatenate hook + rest audio using FFmpeg with a tiny pause between."""
    import subprocess
    import shutil

    ffmpeg = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"

    # Write concat list
    import tempfile, os
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write(f"file '{os.path.abspath(hook_path)}'\n")
        f.write(f"file '{os.path.abspath(rest_path)}'\n")
        concat_file = f.name

    subprocess.run(
        [ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", concat_file,
         "-c", "copy", output_path],
        capture_output=True, timeout=30
    )

    os.unlink(concat_file)
