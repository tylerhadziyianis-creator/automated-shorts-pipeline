import re
import requests
import logging
import random
import json
import fcntl
from datetime import datetime, timezone, timedelta
from config import MIN_POST_UPVOTES, MAX_POST_AGE_DAYS

SHARED_USED_IDS_FILE = "/Users/tyhadz/bots/shared_used_post_ids.json"


def _load_shared_used_ids() -> set:
    try:
        with open(SHARED_USED_IDS_FILE) as f:
            return set(json.load(f))
    except Exception:
        return set()


def _save_shared_used_id(post_id: str):
    try:
        with open(SHARED_USED_IDS_FILE, "r+") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            ids = json.load(f)
            if post_id not in ids:
                ids.append(post_id)
            f.seek(0)
            json.dump(ids, f)
            f.truncate()
            fcntl.flock(f, fcntl.LOCK_UN)
    except Exception as e:
        logger.warning(f"Could not update shared used IDs: {e}")

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.reddit.com/",
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)

MIN_WORDS = 150
MAX_WORDS = 800

# These phrases block a post when found in the title only (meta/update posts)
TITLE_SKIP_PHRASES = [
    "update:", "[update]", "meta ", "mod post",
    "trigger warning", "tw:", "cw:",
]

# These phrases block a post when found in title OR body
BODY_SKIP_PHRASES = [
    "mod post",
]

# Exact-word phrases (use regex word boundaries to avoid substring false-positives)
FLAGGED_WORD_PHRASES = [
    "meth", "heroin", "cocaine", "overdosed", "nude", "explicit",
]

# Substring phrases that are safe to match anywhere (multi-word, no false-positive risk)
FLAGGED_SUBSTRING_PHRASES = [
    # Self-harm / suicide
    "suicide", "suicidal", "self-harm", "self harm", "cutting myself",
    "kill myself", "killed myself", "wanted to die", "attempted suicide",
    "overdose", "od'd",
    # Sexual content
    "sexual abuse", "sexually abused", "rape", "raped", "molested", "molestation",
    "groomed", "grooming", "sex tape", "naked photo",
    "assault me sexually", "sexually assaulted",
    # Graphic violence / death
    "murdered", "murder", "stabbed", "shot and killed", "shot him", "shot her",
    "death threat", "threatened to kill", "beat me unconscious",
    # Minors in unsafe context
    "child abuse", "abused as a child", "molested as a kid",
]

_FLAGGED_WORD_RE = re.compile(
    r'\b(' + '|'.join(re.escape(w) for w in FLAGGED_WORD_PHRASES) + r')\b'
)


def _load_browser_cookies():
    """Load Reddit cookies from Chrome — no API keys needed."""
    try:
        import browser_cookie3
        cookies = browser_cookie3.chrome(domain_name=".reddit.com")
        SESSION.cookies.update(cookies)
        logger.info("Loaded Reddit cookies from Chrome")
        return True
    except Exception as e:
        logger.warning(f"Could not load browser cookies: {e}")
        return False


_load_browser_cookies()


def _fetch_subreddit(sort: str = "hot", time_filter: str = "week", limit: int = 50) -> list:
    """Fetch r/AITA posts via Reddit JSON API."""
    if sort == "top":
        url = f"https://www.reddit.com/r/AmItheAsshole/top.json?t={time_filter}&limit={limit}"
    else:
        url = f"https://www.reddit.com/r/AmItheAsshole/hot.json?limit={limit}"

    try:
        resp = SESSION.get(url, timeout=15)
        if resp.status_code == 429:
            logger.warning("Reddit rate limited")
            return []
        if resp.status_code != 200:
            logger.warning(f"Reddit returned {resp.status_code}")
            return []
        return resp.json().get("data", {}).get("children", [])
    except Exception as e:
        logger.error(f"Reddit fetch failed: {e}")
        return []


def get_aita_post(used_ids: set) -> dict | None:
    """
    Fetch a high-quality AITA post not yet used.
    Returns dict with id, title, body, upvotes, url or None if nothing suitable.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=MAX_POST_AGE_DAYS)

    # Pull from both hot and top/week
    children = _fetch_subreddit("hot", limit=50) + _fetch_subreddit("top", "week", limit=50) + _fetch_subreddit("top", "month", limit=50)

    # Deduplicate
    seen = set()
    unique = []
    for child in children:
        post = child.get("data", {})
        pid = post.get("id", "")
        if pid and pid not in seen:
            seen.add(pid)
            unique.append(post)

    # Tier 1: highest-performing topics (weddings, exes, family/in-laws)
    TIER1_KEYWORDS = [
        "wedding", "ex", "ex-wife", "ex-husband", "ex-girlfriend", "ex-boyfriend",
        "mother-in-law", "father-in-law", "mil", "fil", "in-laws", "in-law",
        "bride", "groom", "vows", "toast", "reception", "engagement",
        "uninvited", "family heirloom", "inheritance", "disowned", "cut off",
    ]

    # Tier 2: solid performers (money/entitlement, general family drama)
    TIER2_KEYWORDS = [
        "secret", "affair", "cheating", "betrayal", "entitlement", "audacity",
        "kicked out", "moved in", "savings", "rent", "demanded", "stole",
        "lied", "boundary", "ultimatum", "evicted", "sister", "brother",
        "parents", "mom", "dad", "entitled",
    ]

    # Workplace posts underperform — penalize them
    WORKPLACE_KEYWORDS = [
        "coworker", "boss", "manager", "office", "workplace", "job", "colleague",
        "employee", "employer", "hr ", "human resources", "fired", "work from home",
    ]

    def _priority_score(post):
        text = (post.get("title", "") + " " + post.get("selftext", "")).lower()
        tier1_hits = sum(1 for kw in TIER1_KEYWORDS if kw in text)
        tier2_hits = sum(1 for kw in TIER2_KEYWORDS if kw in text)
        workplace_hits = sum(1 for kw in WORKPLACE_KEYWORDS if kw in text)
        return post.get("score", 0) + (tier1_hits * 1000) + (tier2_hits * 400) - (workplace_hits * 600)

    unique.sort(key=_priority_score, reverse=True)

    shared_used = _load_shared_used_ids()
    all_used = used_ids | shared_used

    for post in unique:
        post_id = post.get("id", "")

        if post_id in all_used:
            continue
        if post.get("score", 0) < MIN_POST_UPVOTES:
            continue
        if not post.get("is_self"):
            continue

        body = post.get("selftext", "")
        if not body or body in ("[removed]", "[deleted]", ""):
            continue

        # Age check
        created = datetime.fromtimestamp(post.get("created_utc", 0), tz=timezone.utc)
        if created < cutoff:
            continue

        title_lower = post.get("title", "").lower()
        body_lower = body.lower()

        # Skip NSFW-tagged posts
        if post.get("over_18"):
            continue

        # Skip meta/update posts (title-only check to avoid blocking edit notes in body)
        if any(phrase in title_lower for phrase in TITLE_SKIP_PHRASES):
            continue

        # Skip content that would get YouTube channel flagged
        combined = title_lower + " " + body_lower
        if any(phrase in combined for phrase in FLAGGED_SUBSTRING_PHRASES):
            continue
        if _FLAGGED_WORD_RE.search(combined):
            continue

        # Must be AITA or WIBTA
        if "aita" not in title_lower and "wibta" not in title_lower:
            continue

        # Word count
        word_count = len(body.split())
        if word_count < MIN_WORDS or word_count > MAX_WORDS:
            continue

        logger.info(f"Selected: {post['title'][:70]} ({post['score']} upvotes)")
        _save_shared_used_id(post_id)
        return {
            "id": post_id,
            "title": post.get("title", ""),
            "body": body,
            "upvotes": post.get("score", 0),
            "url": f"https://reddit.com{post.get('permalink', '')}",
            "source": "reddit",
        }

    logger.warning("No suitable Reddit posts found — using fallback")
    return None


# Fallback stories when Reddit fetch fails
FALLBACK_STORIES = [
    {
        "id": "fallback_001",
        "title": "AITA for cutting off my sister after she told my husband my secret?",
        "body": """I (28F) have been keeping a secret from my husband (30M) for the entire 3 years of our marriage. Before we met I had a brief relationship with someone who turned out to be emotionally abusive. It ended badly and I never told my husband because I was ashamed and didn't think it was relevant.

Last month my sister (25F) was visiting us for dinner. We had a few drinks and she casually mentioned my ex's name in front of my husband. When my husband asked who that was, she told him everything. She said she thought he already knew and that she was just making conversation.

My husband was devastated. Not because of the relationship itself but because I had hidden it for three years. He said he felt like he didn't really know me. We spent two weeks barely talking and went to couples therapy to work through it.

I am furious at my sister. She knew this was a sensitive topic because I had specifically told her years ago that I didn't want to discuss it. She apologized and said it was a genuine mistake but I told her I need space from her right now.

My parents are calling me cruel for cutting my sister off over an accident. They say she feels terrible and that family comes first. But I feel like my marriage almost fell apart because of her carelessness.

AITA for refusing to speak to my sister until I'm ready?""",
        "upvotes": 15000,
        "url": "",
        "source": "fallback",
    },
    {
        "id": "fallback_002",
        "title": "AITA for refusing to pay for my brother's wedding after he uninvited my girlfriend?",
        "body": """My brother (32M) and his fiancée have been planning their wedding for a year. I (29M) have been contributing $5,000 to help cover costs because they asked and I wanted to support them.

Three weeks before the wedding my brother called me and said his fiancée no longer wants my girlfriend (27F) there. No explanation given beyond she makes the group uncomfortable. My girlfriend has been part of our family for two years and has never had a conflict with anyone.

I told my brother that if my girlfriend isn't welcome then neither am I. He said I was being dramatic and that I should just come alone for one day. I said no and also told him I was withdrawing my financial contribution since I was no longer a full guest.

He went ballistic. Said I was ruining his wedding over nothing and that money I promised is morally his now. Our parents are siding with him and saying I should just go alone to keep the peace. My girlfriend told me I didn't have to do this but I refuse to show her that she matters less than my brother's feelings.

AITA for pulling the money and skipping the wedding entirely?""",
        "upvotes": 22000,
        "url": "",
        "source": "fallback",
    },
    {
        "id": "fallback_003",
        "title": "AITA for telling my mother-in-law the truth about why we moved across the country?",
        "body": """My husband (34M) and I (32F) moved from New York to Seattle eight months ago. We told everyone it was for his job opportunity which was partially true. The real reason was his mother (62F).

She was showing up at our apartment multiple times per week unannounced. She would go through our fridge and cabinets and comment on everything. She once let herself in with a spare key while we were asleep and started cleaning our kitchen at 7am. When we took the key back she cried for a week and told the whole family we were cruel.

My husband tried talking to her gently several times. It never worked. The move was the only solution we could see. She believed the job story completely and we kept our distance to maintain boundaries.

Last week at a family dinner she made a comment about how excited she was to visit Seattle. She started planning a two week stay. I looked at my husband and he looked at his plate. I told her the truth. That the move was partly about needing space because the boundaries weren't being respected.

She broke down crying. My husband is upset with me for blindsiding him. His siblings are texting me calling me heartless. But she was about to plan a two week trip and I panicked.

AITA?""",
        "upvotes": 18000,
        "url": "",
        "source": "fallback",
    },
]
