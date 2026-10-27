import logging
import time
import os
import sys
import fcntl
from datetime import datetime, timezone, timedelta
from config import POSTING_HOURS, DRY_RUN
from bot_engine import AITABot

PRE_GEN_MINUTES = 120  # Start generating this many minutes before each posting slot

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(f"./logs/aita_bot_{datetime.now().strftime('%Y%m%d')}.log"),
    ],
)
logger = logging.getLogger(__name__)

LOCK_FILE = "/tmp/aita_bot.lock"


def acquire_lock():
    lock_fh = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        lock_fh.write(str(os.getpid()))
        lock_fh.flush()
        return lock_fh
    except BlockingIOError:
        logger.error("Another instance already running. Exiting.")
        sys.exit(1)


def verify_channel_auth():
    """Abort immediately if credentials are authenticated to the wrong Google account."""
    from config import CREDENTIALS_FILE, YOUTUBE_CHANNEL_ID
    if not os.path.exists(CREDENTIALS_FILE):
        logger.error("STARTUP ABORT: No credentials file found. Run auth first.")
        sys.exit(1)
    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        creds = Credentials.from_authorized_user_file(CREDENTIALS_FILE)
        youtube = build("youtube", "v3", credentials=creds)
        resp = youtube.channels().list(part="snippet", mine=True).execute()
        items = resp.get("items", [])
        if not items:
            logger.error("STARTUP ABORT: Could not retrieve channel from credentials.")
            sys.exit(1)
        authenticated_id = items[0]["id"]
        channel_name = items[0]["snippet"]["title"]
        if authenticated_id != YOUTUBE_CHANNEL_ID:
            logger.error(
                f"STARTUP ABORT: Credentials are for '{channel_name}' ({authenticated_id}), "
                f"expected {YOUTUBE_CHANNEL_ID}. Re-run auth with the correct Google account."
            )
            sys.exit(1)
        logger.info(f"Auth verified: '{channel_name}' ({authenticated_id}) ✓")
    except Exception as e:
        logger.error(f"STARTUP ABORT: Auth verification failed: {e}")
        sys.exit(1)


def main():
    lock = acquire_lock()
    verify_channel_auth()
    bot = AITABot()

    logger.info(f"AITA Bot started | DRY_RUN={DRY_RUN}")
    logger.info(f"Posting hours (UTC): {POSTING_HOURS}")

    # Seed last_run_hour so startup doesn't double-fire on a posting hour
    now = datetime.now(timezone.utc)
    last_run_hour = now.hour if now.hour in POSTING_HOURS else None
    last_pregen_slot = None
    last_upload_time = None
    bot.run_cycle()

    while True:
        now = datetime.now(timezone.utc)
        current_hour = now.hour

        # Upload cycle — fires at the top of each posting hour
        cooldown_ok = last_upload_time is None or (now - last_upload_time).total_seconds() > 1800
        if current_hour in POSTING_HOURS and current_hour != last_run_hour and cooldown_ok:
            last_run_hour = current_hour
            logger.info(f"=== Upload cycle | hour={current_hour} UTC ===")
            bot.run_cycle()
            last_upload_time = now
        elif current_hour in POSTING_HOURS and current_hour != last_run_hour and not cooldown_ok:
            last_run_hour = current_hour
            logger.info(f"=== Upload cycle skipped (cooldown) | hour={current_hour} UTC ===")


        if current_hour not in POSTING_HOURS:
            last_run_hour = None

        # Catch-up check — if a video became due *after* its hour's upload cycle
        # already ran (e.g. pre-gen failed and had to regenerate on the spot).
        # Guard with 30-min cooldown to prevent double-posting.
        from scheduler import load_state, get_due_item
        state = load_state()
        cooldown_ok = last_upload_time is None or (now - last_upload_time).total_seconds() > 1800
        if cooldown_ok and get_due_item(state) is not None:
            logger.info("=== Catch-up upload check ===")
            bot.run_cycle()
            last_upload_time = now

        # Pre-generation — fires PRE_GEN_MINUTES before each posting slot
        next_slot = _next_slot(now)
        minutes_away = (next_slot - now).total_seconds() / 60
        if minutes_away <= PRE_GEN_MINUTES and next_slot != last_pregen_slot:
            from scheduler import load_state, should_generate
            state = load_state()
            if should_generate(state):
                last_pregen_slot = next_slot
                logger.info(f"=== Pre-generation | {minutes_away:.0f}min before {next_slot.strftime('%H:%M UTC')} ===")
                bot.generate_only()

        time.sleep(30)


def _next_slot(now: datetime) -> datetime:
    """Return the next posting slot datetime."""
    for hour in sorted(POSTING_HOURS):
        slot = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        if slot > now:
            return slot
    tomorrow = now + timedelta(days=1)
    return tomorrow.replace(hour=sorted(POSTING_HOURS)[0], minute=0, second=0, microsecond=0)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--once":
        bot = AITABot()
        bot.run_cycle()
    elif len(sys.argv) > 1 and sys.argv[1] == "--force":
        verify_channel_auth()
        bot = AITABot()
        bot.run_cycle(force=True)
    else:
        main()
