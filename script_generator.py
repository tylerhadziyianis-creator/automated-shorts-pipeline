import json
import random
import re
import logging
import anthropic
from config import ANTHROPIC_API_KEY, CLAUDE_MODEL, SCENES_PER_VIDEO, WORDS_PER_VIDEO

logger = logging.getLogger(__name__)

# Alternates closer style between generations so comments and verdicts both appear
_last_closer = "verdict"


def _client():
    return anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


def generate_video_script(post: dict, _retry: bool = False, _closer: str = None) -> dict:
    """
    Takes a raw Reddit AITA post and returns a full video production package:
    - Rewritten narration script (~200 words, punchy)
    - Title optimized for CTR
    - Hashtags
    - Scene breakdown with Flux image prompts
    """
    title = post["title"]
    body = post["body"]

    logger.info(f"Generating script for: {title[:60]}")

    global _last_closer
    if _closer is None:
        _closer = "question" if _last_closer == "verdict" else "verdict"
    _last_closer = _closer

    scene_count = random.randint(SCENES_PER_VIDEO - 3, SCENES_PER_VIDEO + 2)
    word_count_target = random.randint(WORDS_PER_VIDEO - 25, WORDS_PER_VIDEO + 25)

    # Check if we have performance data to weight toward winning story types
    from performance_tracker import get_preferred_tags
    preferred_tags = get_preferred_tags()
    performance_hint = ""
    if preferred_tags:
        performance_hint = f"\n\nDATA INSIGHT: Based on channel performance, these story types get the most views: {', '.join(preferred_tags)}. Lean toward these patterns when structuring the narrative.\n"

    response = _client().messages.create(
        model=CLAUDE_MODEL,
        max_tokens=4096,
        messages=[{
            "role": "user",
            "content": f"""Produce a YouTube Shorts video package for the drama channel "Tell Me I'm Wrong." Return ONLY valid JSON.{performance_hint}

POST TITLE: {title}
POST BODY:
{body}

SAFETY (YouTube will remove violations):
- No suicide, self-harm, sexual assault, rape, graphic violence, drugs in narration
- Banned words in title/narration: kill, dead, death, die, murder, suicide, rape, abuse, naked, sex, explicit, violence, blood, drug, overdose, weapon, gun, knife, assault, shoot, attack
- Images: emotional scenes only, no weapons/nudity/blood/physical contact

NARRATION (~{word_count_target} words):
- Conversational, dramatic, like texting a friend. No "so basically" opener.
- Contractions always. Varied sentence rhythm — punchy fragments mixed with longer thoughts.
- STRUCTURE: Hook → Setup (10-15s) → MINI-HOOK at ~20s → Main conflict → Climax → Verdict. The mini-hook is a new piece of information that re-escalates tension just as the setup is losing momentum. Example: you're explaining context, then land a line like "And that's when I found out it gets so much worse." or "Here's the part nobody knows." — a single sentence that makes the viewer feel like the real story is just starting.
- Last 2-3 sentences: your personal take/verdict as creator, not a recap.
- End with a {_closer.upper()}: {"a BINARY forced-choice question — two specific options, no middle ground. Format: 'Was she wrong for X, or was he just Y?' / 'Is she the villain here, or is he just Z?' — make it impossible to stay neutral" if _closer == "question" else "a sharp opinion inviting pushback — stated strongly as a take, not a recap. Make it sound like you'd argue this point."}
- Final sentence after the closer: a natural follow CTA tied to the verdict — ALWAYS use "follow" not "subscribe" (this is Shorts). e.g. "Follow if you think I was right." / "Follow for more verdicts." / "Follow so you never miss one." — one short sentence, casual not corporate

HOOK (very first line):
- Format: [tag] Short punchy line (8-10 words max)
- Tags: [shouting] [whispering] [panicked] [desperate] [laughing] [crying]
- Must reference the SPECIFIC shocking moment from this story — not a vague tension statement. The viewer clicked because of one thing; the hook must make them feel that thing immediately.
- Must create TENSION not reveal outcome. Narrator in a tense moment RIGHT NOW, consequence unresolved.
- BAD: "Every single boundary they crossed" (vague) / quotes dialogue / describes a scene / spoils outcome
- GOOD examples: [whispering] "She had no idea I knew. And I was about to change that." / [panicked] "I had one second to decide. I didn't stay quiet." / [shouting] "Three months of this. And she just did it again."
- After hook, drop to normal narrator voice — no more tags

STYLE REFERENCE (match this energy):
"Look, I get it — pregnancy is exhausting. My wife is eight months along, and a month ago her best friend got married across the country. We couldn't go. Doctor said no traveling. Everyone got that. Here's where it gets messy though..."
Casual pivots, rhetorical checks, builds beat by beat. Listener feels let in on something.

SCENES ({scene_count} total, 2-4 sentences each):
Image prompt rules: photorealistic, cinematic lighting, end every prompt with "cinematic lighting, shallow depth of field, muted warm tones, ultra realistic, 9:16 portrait"

SCENE 1 — must stop the scroll:
- Waist-up or three-quarter shot of a person in strong emotional distress — NOT an extreme close-up, NOT a wide room shot
- Face clearly visible and expressive, takes up roughly 40-60% of the frame
- Body language must reinforce the emotion: hands on face, gripping a table, covering mouth, head in hands, leaning forward in disbelief, laughing with hands up
- Expression matches hook tag: [shouting]→raw anger, jaw tight / [whispering]→leaning in, eyes wide / [panicked]→hands to mouth, eyes wide / [desperate]→head in hands, shoulders dropped / [laughing]→genuine disbelief laughter / [crying]→face buried in hands or tears visible
- Cinematic, dramatic lighting — one strong light source from the side
- Person not clearly identifiable — slightly soft focus on face or shot from slight angle

SCENES 2+: backs of heads, silhouettes, hands, rooms from distance — no clear faces

MIDDLE SCENE (scene 6 or 7): This is the emotional peak of the story — the moment of confrontation, revelation, or breakdown. Make this image prompt the most visually dramatic of the entire video. A tense room, a slammed door, hands gripping a table, someone frozen in shock — whatever captures the single most charged moment. YouTube pulls its thumbnail from the video midpoint, so this scene must be cinematic and striking. Still no clear faces.

TITLE (pick most scroll-stopping):
Proven top-performing formats — use these as your template:
- Dollar amount + betrayal: "She Asked for a Loan. Then I Saw Her New Car 🚗 | AITA" / "I Spent $3,000 on His Wedding. Then They Made Me LIFT Tables 💸 | AITA"
- Firm rejection: "I Said NO to the Wedding. Now the Whole Family Is Mad 😤 | AITA" / "They Found a Babysitter. I Still Said NO 😤 | AITA"
- Boundary violation: "She Kissed the Baby ANYWAY. Behind Their Backs 😤 | AITA" / "His Wedding Toast Was About His EX 😳 | AITA"
- Action + consequence: "He Trashed Me Online. Then Asked for a FAVOR 😤 | AITA" / "I Cut Her Off. My Parents Are FURIOUS 🔥 | AITA"
SECONDARY (only for clear irony/impossible fact): "She Let Herself In at 7AM. We Were ASLEEP 😤 | AITA"
AVOID: open loops, vague drama ("Did ONE Thing"), no dollar amount when story has one
Rules: max 80 chars incl "| AITA", CAPITALIZE exactly ONE word, 1-2 emojis (💔🚨💸😳🔥😤💅👀🚗⌚ — never 💀🔪🩸🔫), must end "| AITA"

DESCRIPTION: 1-2 sentences referencing a specific story detail. End with unique call-to-comment.
PINNED COMMENT: Take a side, provoke debate, reference specific detail, under 20 words. End with "tell me I'm wrong" / "fight me in the comments" / similar variation.

Return this JSON:
{{
  "title": "...",
  "description": "...",
  "pinned_comment": "...",
  "narration": "...",
  "story_tags": ["tag1", "tag2"],
  "scenes": [{{"scene_num": 1, "narration_excerpt": "...", "image_prompt": "..."}}],
  "hashtags": ["#AITA", "#RelationshipAdvice", "#AmITheAsshole", "#Drama", "#Reddit", "#Storytime", "#Relationships", "#RedditStories", "#TellMeImWrong", "#Shorts"]
}}

JSON rules: no literal double-quotes inside strings (use single quotes), no unescaped backslashes."""
        }]
    )

    raw = response.content[0].text.strip()

    # Extract JSON
    json_match = re.search(r'\{.*\}', raw, re.DOTALL)
    if not json_match:
        raise ValueError(f"No JSON in script generator response: {raw[:200]}")

    json_text = json_match.group()
    # Claude occasionally drops the comma between array objects (e.g. between
    # scene entries) — repair "}<whitespace>{" -> "},<whitespace>{" before parsing.
    json_text = re.sub(r'\}(\s*)\{', r'},\1{', json_text)

    try:
        result = json.loads(json_text)
    except json.JSONDecodeError as e:
        snippet_start = max(0, e.pos - 80)
        snippet = json_text[snippet_start:e.pos + 80]
        logger.warning(f"JSON parse failed ({e}). Context around error: ...{snippet}...")
        if _retry:
            raise
        logger.warning("Retrying generation once")
        return generate_video_script(post, _retry=True)

    # Validate
    required = ["title", "description", "pinned_comment", "narration", "scenes", "hashtags"]
    for key in required:
        if key not in result:
            raise ValueError(f"Missing key in script output: {key}")

    word_count = len(result["narration"].split())
    logger.info(f"Script generated: {word_count} words | {len(result['scenes'])} scenes | Title: {result['title']}")

    return result
