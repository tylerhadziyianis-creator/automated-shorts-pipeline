import requests
import logging
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

logger = logging.getLogger(__name__)


def send_message(text: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
        logger.info("Telegram notification sent")
    except Exception as e:
        logger.warning(f"Telegram failed: {e}")


def notify_upload(title: str, url: str, count: int):
    send_message(f"✅ <b>Tell Me I'm Wrong</b> — Video #{count} uploaded\n\n<b>{title}</b>\n\n{url}")


def notify_generating(title: str):
    send_message(f"🎬 Generating: <i>{title[:80]}</i>")


def notify_error(context: str, error: str):
    send_message(f"❌ <b>AITA Bot Error</b>\n{context}\n<code>{error[:200]}</code>")
