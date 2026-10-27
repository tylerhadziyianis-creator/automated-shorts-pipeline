import os
from dotenv import load_dotenv

load_dotenv()

# API Keys
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "pNInz6obpgDQGcFmaJgB")
REPLICATE_API_TOKEN = os.getenv("REPLICATE_API_TOKEN", "")

# YouTube
YOUTUBE_CLIENT_SECRETS_FILE = os.getenv("YOUTUBE_CLIENT_SECRETS_FILE", "client_secrets.json")
YOUTUBE_CHANNEL_ID = os.getenv("YOUTUBE_CHANNEL_ID", "")

# Reddit — uses Chrome browser cookies, no API keys needed

# Telegram
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# Bot mode
DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"

# Claude model
CLAUDE_MODEL = "claude-opus-4-5"

# Posting schedule — UTC hours (6x/day)
# 15=11am EDT, 17=1pm EDT, 19=3pm EDT, 21=5pm EDT, 23=7pm EDT, 1=9pm EDT
POSTING_HOURS = [13, 17, 21, 1]

# Content settings
SCENES_PER_VIDEO = 12       # ~7s per image — varied, higher energy
WORDS_PER_VIDEO = 290       # ~80 seconds at natural speech pace — enough for emotional arc
MIN_POST_UPVOTES = 500      # Minimum Reddit upvotes to consider a post
MAX_POST_AGE_DAYS = 7       # Only pull posts from the last week

# File paths
OUTPUT_DIR = "./output"
STATE_FILE = "./aita_state.json"
CREDENTIALS_FILE = "./youtube_credentials.json"
