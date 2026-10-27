import json
import os
import random
import logging
from datetime import datetime, timezone, timedelta
from config import STATE_FILE, POSTING_HOURS

logger = logging.getLogger(__name__)


def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {
        "queue": [],
        "used_post_ids": [],
        "upload_count": 0,
    }


def save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, default=str)


FALLBACK_IDS = {'fallback_001', 'fallback_002', 'fallback_003', 'fallback_b2_001', 'fallback_b2_002', 'fallback_b2_003'}


def queue_video(state: dict, video_data: dict) -> dict:
    """Schedule a video at the next available posting slot.
    If the queue is currently empty, schedule immediately so the first video
    uploads as soon as generation finishes."""
    post_id = video_data.get("post_id", "")
    if post_id in FALLBACK_IDS:
        logger.error(f"Refusing to queue fallback story: {post_id} — {video_data.get('title','')[:50]}")
        return state

    now = datetime.now(timezone.utc)
    lookback = now - timedelta(hours=12)
    existing_times = [
        datetime.fromisoformat(item["scheduled_time"])
        for item in state["queue"]
        if not item.get("uploaded") and datetime.fromisoformat(item["scheduled_time"]) >= lookback
    ]
    pending = [i for i in state["queue"] if not i.get("uploaded")]
    is_first_ever = not pending and state.get("upload_count", 0) == 0
    if is_first_ever:
        # Very first video — post immediately after generation
        post_time = now - timedelta(seconds=5)
    else:
        post_time = _next_posting_slot(existing_times, now)

    state["queue"].append({
        "video_data": video_data,
        "scheduled_time": post_time.isoformat(),
        "uploaded": False,
    })

    post_id = video_data.get("post_id", "")
    if post_id and post_id not in state["used_post_ids"]:
        state["used_post_ids"].append(post_id)

    logger.info(f"Queued video for {post_time.strftime('%Y-%m-%d %H:%M UTC')}: {video_data.get('title', '')[:50]}")
    return state


def get_due_item(state: dict) -> dict | None:
    """Return the single earliest due item within the last 2 hours, or None."""
    now = datetime.now(timezone.utc)
    window = timedelta(hours=2)
    candidates = []

    for item in state["queue"]:
        if item.get("uploaded"):
            continue
        scheduled = datetime.fromisoformat(item["scheduled_time"])
        if scheduled <= now:
            if now - scheduled <= window:
                candidates.append((scheduled, item))
            else:
                # Missed window — reschedule
                lookback = now - timedelta(hours=12)
                existing = [
                    datetime.fromisoformat(i["scheduled_time"])
                    for i in state["queue"]
                    if i is not item and not i.get("uploaded") and datetime.fromisoformat(i["scheduled_time"]) >= lookback
                ]
                new_slot = _next_posting_slot(existing, now)
                item["scheduled_time"] = new_slot.isoformat()
                logger.info(f"Rescheduled missed video to {new_slot.strftime('%Y-%m-%d %H:%M UTC')}")

    if not candidates:
        return None

    candidates.sort(key=lambda x: x[0])
    return candidates[0][1]


def mark_uploaded(state: dict, video_data: dict, result: dict) -> dict:
    title = video_data.get("title", "")
    for item in state["queue"]:
        if item["video_data"].get("title") == title and not item.get("uploaded"):
            item["uploaded"] = True
            item["upload_result"] = result
            item["uploaded_at"] = datetime.now(timezone.utc).isoformat()
            state["upload_count"] += 1
            break
    return state


def should_generate(state: dict) -> bool:
    """Generate if the next posting slot has no video queued for it."""
    pending = [i for i in state["queue"] if not i.get("uploaded")]
    if not pending:
        return True
    now = datetime.now(timezone.utc)
    # Don't generate if something is already due — upload first
    has_due = any(
        datetime.fromisoformat(i["scheduled_time"]) <= now
        for i in pending
    )
    if has_due:
        return False
    # Find the next posting slot and check if it's covered
    next_slot = _next_slot_time(now)
    slot_window = timedelta(hours=1)
    covered = any(
        abs(datetime.fromisoformat(i["scheduled_time"]) - next_slot) <= slot_window
        for i in pending
    )
    if not covered:
        return True
    # Also generate if buffer is thin (fewer than 3 ahead)
    return len(pending) < 3


def _next_slot_time(now: datetime) -> datetime:
    """Return the next upcoming posting slot datetime."""
    for hour in sorted(POSTING_HOURS):
        candidate = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        if candidate > now:
            return candidate
    tomorrow = now + timedelta(days=1)
    return tomorrow.replace(hour=sorted(POSTING_HOURS)[0], minute=0, second=0, microsecond=0)


def _nearest_posting_hour(t: datetime) -> int:
    """Map a (possibly jittered) timestamp back to the posting hour it belongs to."""
    def dist(h):
        t_min = t.hour * 60 + t.minute
        s_min = h * 60
        d = abs(t_min - s_min)
        return min(d, 24 * 60 - d)
    return min(POSTING_HOURS, key=dist)


def _next_posting_slot(existing_times: list, now: datetime) -> datetime:
    taken_slots = {(t.date(), _nearest_posting_hour(t)) for t in existing_times}
    candidate = now.replace(minute=0, second=0, microsecond=0)
    for _ in range(30):
        for hour in sorted(POSTING_HOURS):
            base_slot = candidate.replace(hour=hour, minute=0)
            slot_key = (base_slot.date(), hour)
            jittered = base_slot + timedelta(minutes=random.randint(-12, 12))
            if jittered > now and slot_key not in taken_slots:
                return jittered
        candidate += timedelta(days=1)
    return now + timedelta(hours=6)
