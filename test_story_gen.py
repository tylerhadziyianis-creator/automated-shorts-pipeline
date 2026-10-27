"""
Test script: generates 3 original AITA stories using top Reddit titles as signal.
Run from ~/bots/aita-bot/
"""
import sys
import os
import json
import anthropic

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from config import ANTHROPIC_API_KEY
from reddit_scraper import _fetch_subreddit
from script_generator import generate_video_script


def get_top_titles(n=30) -> list[str]:
    children = _fetch_subreddit("hot", limit=50) + _fetch_subreddit("top", "week", limit=50)
    seen = set()
    titles = []
    for child in children:
        post = child.get("data", {})
        title = post.get("title", "")
        score = post.get("score", 0)
        pid = post.get("id", "")
        if pid in seen or score < 500:
            continue
        if "aita" not in title.lower() and "wibta" not in title.lower():
            continue
        seen.add(pid)
        titles.append((score, title))
    titles.sort(reverse=True)
    return [t for _, t in titles[:n]]


def generate_original_story(top_titles: list[str], story_num: int) -> dict:
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    titles_block = "\n".join(f"- {t}" for t in top_titles)

    response = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=1500,
        messages=[{
            "role": "user",
            "content": f"""You are writing original AITA stories for a YouTube Shorts channel.

Here are the top-performing AITA post titles on Reddit right now, ranked by upvotes:
{titles_block}

Study these titles to understand what conflict types and emotional hooks are resonating with audiences right now.

Write story #{story_num} — an original AITA story inspired by these patterns but with completely different characters, situation, and details. Do NOT copy or rephrase any of these titles. Create something new.

The story must:
- Be 200-350 words
- Have a clear conflict with a specific dollar amount, time frame, or relationship detail that makes it feel real
- End with "AITA?" or "So, AITA?"
- Sound like a real Reddit post — casual, slightly imperfect, emotionally raw
- Use one of these high-performing conflict types (pick whichever fits your story naturally): money dispute, family event exclusion, living situation, loyalty betrayal, secret revealed, entitlement, boundary violation

Return ONLY valid JSON:
{{
  "title": "The raw Reddit-style post title (not a YouTube title — just what you'd post on Reddit)",
  "body": "The full story text"
}}"""
        }]
    )

    raw = response.content[0].text.strip()
    import re
    match = re.search(r'\{.*\}', raw, re.DOTALL)
    if not match:
        raise ValueError(f"No JSON in response: {raw[:200]}")
    return json.loads(match.group())


def main():
    print("Fetching top Reddit titles...")
    top_titles = get_top_titles(30)
    print(f"Got {len(top_titles)} titles as signal\n")

    for i in range(1, 4):
        print(f"{'='*60}")
        print(f"GENERATING STORY {i}/3...")
        print('='*60)

        post = generate_original_story(top_titles, i)
        print(f"\nREDDIT TITLE: {post['title']}")
        print(f"\nSTORY BODY:\n{post['body']}\n")

        print("Generating full video script...")
        script = generate_video_script(post)

        print(f"\nYOUTUBE TITLE: {script['title']}")
        print(f"\nNARRATION ({len(script['narration'].split())} words):\n{script['narration']}")
        print(f"\nPINNED COMMENT: {script['pinned_comment']}")
        print(f"\nDESCRIPTION: {script['description']}")
        print()


if __name__ == "__main__":
    main()
