#!/usr/bin/env python3
"""Automated SEO blog poster for hornsbychiropractor.com.

Pipeline:
  1. Pick a topic (workflow input, or OpenAI suggests a fresh one that does not
     duplicate existing blog/ posts).
  2. Generate and quality-check a natural, reference-backed article with OpenAI.
  3. Generate two restrained hand-drawn 2D editorial illustrations with local
     ComfyUI first, falling back to OpenAI, and store compressed WebP assets.
  4. Write blog/{slug}/index.html reusing the existing site chrome, prepend a
     card to blog/index.html, update/create sitemap.xml.
  5. Notify via Telegram (success or failure report).

Dependencies: requests, Pillow, tzdata.

Usage:
  python scripts/generate_blog.py            # full pipeline (needs OPENAI_API_KEY)
  python scripts/generate_blog.py --dry-run  # no network; tests template assembly
  python scripts/generate_blog.py --refresh-seo  # refresh schema + related links only
"""

import base64
import html
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urlparse
from zoneinfo import ZoneInfo

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import svg_illustrations
from blog_images import ComfyImageClient, generate_with_fallback

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
BLOG_DIR = REPO_ROOT / "blog"
ASSETS_IMG_DIR = REPO_ROOT / "assets" / "blog-images"
SITE_DOMAIN = "https://hornsbychiropractor.com"

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5.6")
OPENAI_IMAGE_MODEL = os.environ.get("OPENAI_IMAGE_MODEL", "gpt-image-2")
OPENAI_IMAGE_SIZE = os.environ.get("OPENAI_IMAGE_SIZE", "1536x1024")
OPENAI_IMAGE_QUALITY = os.environ.get("OPENAI_IMAGE_QUALITY", "medium")
OPENAI_IMAGE_FORMAT = os.environ.get("OPENAI_IMAGE_FORMAT", "webp").strip().lower()
OPENAI_IMAGE_COMPRESSION = max(
    0, min(100, int(os.environ.get("OPENAI_IMAGE_COMPRESSION", "82")))
)
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
TOPIC_INPUT = os.environ.get("TOPIC", "").strip()
FORCE = os.environ.get("FORCE", "true").strip().lower() not in ("0", "false", "no")
SCHEDULED_RUN = os.environ.get("SCHEDULED_RUN", "false").strip().lower() in ("1", "true", "yes")
POST_INTERVAL_DAYS = max(1, int(os.environ.get("POST_INTERVAL_DAYS", "3")))

DRY_RUN = "--dry-run" in sys.argv
REFRESH_SEO = "--refresh-seo" in sys.argv

SYDNEY_TZ = ZoneInfo("Australia/Sydney")

OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
OPENAI_IMAGES_URL = "https://api.openai.com/v1/images/generations"
TIMEOUT_OPENAI_TEXT = 240
TIMEOUT_OPENAI_IMAGE = 300

BOOKING_URL = (
    "https://aseschedule.com/book/10d766b5-81f6-43b1-9a09-0b7dc8404ce2/default/"
)
BLOG_NAME = "Hornsby Chiropractor Blog"
BLOG_DESCRIPTION = (
    "Practical, evidence-informed articles for Hornsby patients about back pain, "
    "neck pain, posture, movement and recovery."
)

SEO_STOP_WORDS = {
    "about", "after", "again", "against", "also", "and", "are", "article", "best", "can",
    "causes", "chiro", "chiropractic", "does", "for", "from", "guide", "have", "help",
    "helps", "hornsby", "how", "hurt", "hurts", "into", "its", "pain", "practical",
    "relief", "should", "simple", "that", "the", "their", "then", "this", "tips", "to",
    "what", "when", "where", "which", "while", "why", "with", "without", "your",
}

RELATED_TOPIC_GROUPS = (
    {"neck", "cervical", "whiplash", "headache", "headaches", "skull", "jaw"},
    {"shoulder", "shoulders", "upper", "backpack", "overhead"},
    {"back", "lower", "lumbar", "sciatica", "sciatic", "disc", "tailbone", "coccyx", "hip", "leg"},
    {"desk", "sitting", "computer", "phone", "driving", "commuting", "train", "posture", "backpack", "standing"},
    {"exercise", "running", "weights", "lifting", "gardening", "stretch", "sport", "hip"},
    {"sleep", "sleeping", "morning", "bed", "pillow", "pregnancy"},
)

# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def sydney_today() -> str:
    return datetime.now(SYDNEY_TZ).strftime("%Y-%m-%d")


def log(msg: str) -> None:
    print(f"[blog] {msg}", flush=True)


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9\s-]", "", text.lower()).strip()
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-{2,}", "-", slug).strip("-")
    return slug[:60].strip("-") or f"post-{sydney_today()}"


def strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", " ", text)


def clean_visible_text(text: str) -> str:
    """Return readable, whitespace-normalised text from a small HTML fragment."""
    return re.sub(r"\s+", " ", html.unescape(strip_html(text))).strip()


def word_count(html_text: str) -> int:
    return len(clean_visible_text(html_text).split())


def extract_existing_topics() -> list[str]:
    """Slugs of published posts under blog/."""
    topics = []
    if BLOG_DIR.exists():
        for child in sorted(BLOG_DIR.iterdir()):
            if child.is_dir():
                topics.append(child.name)
    return topics


def _meta_content(page: str, key: str) -> str:
    """Read a named or Open Graph meta value regardless of attribute order."""
    for tag in re.findall(r"<meta\b[^>]*>", page, flags=re.IGNORECASE | re.DOTALL):
        attrs = {
            name.lower(): html.unescape(value)
            for name, _quote, value in re.findall(
                r"([:\w-]+)\s*=\s*([\"'])(.*?)\2", tag, flags=re.DOTALL
            )
        }
        if attrs.get("name", "").lower() == key.lower():
            return attrs.get("content", "").strip()
        if attrs.get("property", "").lower() == key.lower():
            return attrs.get("content", "").strip()
    return ""


def _post_record(post_path: Path) -> dict | None:
    """Extract the SEO fields needed for linking and the Blog listing schema."""
    try:
        page = post_path.read_text(encoding="utf-8")
    except OSError:
        return None

    h1 = re.search(r"<h1\b[^>]*>(.*?)</h1>", page, flags=re.IGNORECASE | re.DOTALL)
    title = clean_visible_text(h1.group(1)) if h1 else ""
    if not title:
        return None

    slug = post_path.parent.name
    date_match = re.search(
        r"<time\b[^>]*datetime=[\"'](\d{4}-\d{2}-\d{2})[\"']",
        page,
        flags=re.IGNORECASE,
    )
    meta_match = re.search(
        r"<p\b[^>]*class=[\"'][^\"']*post-meta[^\"']*[\"'][^>]*>(.*?)</p>",
        page,
        flags=re.IGNORECASE | re.DOTALL,
    )
    meta_text = clean_visible_text(meta_match.group(1)) if meta_match else ""
    category = meta_text.rsplit("·", 1)[-1].strip() if "·" in meta_text else ""
    canonical_match = re.search(
        r"<link\b[^>]*rel=[\"']canonical[\"'][^>]*href=[\"']([^\"']+)",
        page,
        flags=re.IGNORECASE,
    )
    canonical = (
        canonical_match.group(1).strip()
        if canonical_match
        else f"{SITE_DOMAIN}/blog/{slug}/"
    )
    return {
        "slug": slug,
        "path": post_path,
        "url": f"/blog/{slug}/",
        "canonical": canonical,
        "title": title,
        "description": _meta_content(page, "description"),
        "category": category,
        "date": date_match.group(1) if date_match else "",
        "image": _meta_content(page, "og:image"),
    }


def extract_post_catalog() -> list[dict]:
    records = []
    for post_path in BLOG_DIR.glob("*/index.html"):
        record = _post_record(post_path)
        if record:
            records.append(record)
    return sorted(records, key=lambda item: (item["date"], item["title"]), reverse=True)


def _seo_tokens(text: str) -> set[str]:
    words = set(re.findall(r"[a-z0-9]+", html.unescape(text).lower()))
    return {word for word in words if len(word) >= 3 and word not in SEO_STOP_WORDS}


def _rank_related_posts(query: str, exclude_slug: str = "", limit: int = 8) -> list[dict]:
    query_tokens = _seo_tokens(query)
    scored = []
    for index, item in enumerate(extract_post_catalog()):
        if item["slug"] == exclude_slug:
            continue
        candidate_core_tokens = _seo_tokens(
            f"{item['title']} {item['category']} {item['slug']}"
        )
        candidate_description_tokens = _seo_tokens(item["description"])
        core_overlap = query_tokens & candidate_core_tokens
        description_overlap = query_tokens & candidate_description_tokens
        score = len(core_overlap) * 10 + min(len(description_overlap), 2)
        for group in RELATED_TOPIC_GROUPS:
            if query_tokens & group and candidate_core_tokens & group:
                score += 4
        # Preserve recency as a small tie-breaker without overpowering relevance.
        if score >= 4:
            scored.append((score, -index, item))
    scored.sort(key=lambda row: (row[0], row[1], row[2]["title"]), reverse=True)
    return [row[2] for row in scored[:limit]]


def build_internal_link_hints(topic: str) -> tuple[str, set[str]]:
    """Offer only real, contextually ranked site URLs to the writing model."""
    related = _rank_related_posts(topic, limit=10)
    candidates = [
        ("/services/", "Chiropractic services and what an appointment involves"),
        ("/directions/", "Hornsby clinic location and directions"),
        ("/#about", "About Andy Lee and Hornsby Chiropractor"),
        (BOOKING_URL, "Online appointment booking"),
    ]
    candidates.extend((item["url"], item["title"]) for item in related)
    lines = "\n".join(f"- {url} : {label}" for url, label in candidates)
    instruction = (
        "Choose 2-4 genuinely relevant links from the verified candidates below. "
        "Prioritise useful related articles and the services page. Use each URL at most once, "
        "with descriptive anchor text. A booking link may appear once near the end only when it "
        "fits naturally; do not force a sales call to action. Use the URLs exactly as supplied.\n"
        f"{lines}"
    )
    return instruction, {url for url, _label in candidates}


def latest_published_date():
    """Return the newest publication date embedded in an existing post."""
    dates = []
    for post_path in BLOG_DIR.glob("*/index.html"):
        try:
            page = post_path.read_text(encoding="utf-8")
        except OSError:
            continue
        match = re.search(r'<time\s+datetime="(\d{4}-\d{2}-\d{2})"', page)
        if not match:
            continue
        try:
            dates.append(datetime.fromisoformat(match.group(1)).date())
        except ValueError:
            continue
    return max(dates) if dates else None


def scheduled_publish_due() -> bool:
    """Allow scheduled publication only after the configured calendar interval."""
    latest = latest_published_date()
    if latest is None:
        return True
    today = datetime.now(SYDNEY_TZ).date()
    elapsed = (today - latest).days
    if elapsed < POST_INTERVAL_DAYS:
        remaining = POST_INTERVAL_DAYS - elapsed
        log(
            f"Scheduled run skipped: latest post is {latest.isoformat()} "
            f"({elapsed} day(s) ago); next post is due in {remaining} day(s)."
        )
        return False
    return True


# ---------------------------------------------------------------------------
# Telegram notification
# ---------------------------------------------------------------------------


def send_telegram(message: str) -> None:
    """Send an HTML-mode Telegram message. Never raises."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log("Telegram secrets not set - skipping notification.")
        return
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            data={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": message,
                "parse_mode": "HTML",
                "disable_web_page_preview": "false",
            },
            timeout=30,
        )
        if resp.status_code != 200:
            log(f"Telegram send failed ({resp.status_code}): {resp.text[:300]}")
        else:
            log("Telegram notification sent.")
    except Exception as exc:  # noqa: BLE001
        log(f"Telegram notification error: {exc}")


# ---------------------------------------------------------------------------
# OpenAI calls
# ---------------------------------------------------------------------------

TOPIC_SYSTEM_PROMPT = """You are the content strategist for Hornsby Chiropractor, \
a chiropractic clinic in Hornsby, NSW, Australia (author: Andy Lee).

Suggest ONE new blog post topic for local patients. Requirements:
- A specific, long-tail question or how-to search phrase that a real patient would \
type into Google (e.g. "how to sleep with lower back pain", "best desk setup for neck pain").
- Relevant to chiropractic care: back pain, neck pain, posture, headaches, sciatica, \
sports injuries, ergonomics, sleep and pain, exercise and recovery, etc.
- It must NOT be substantially similar to any of the already-published topics listed below.
- Vary the symptom, activity and search intent instead of producing another minor rewrite of a common topic.
- Use "Hornsby" only for a genuinely local-intent topic, not as a keyword added to every title.
- Answer ONLY with the topic text itself. No quotes, no numbering, no explanation."""

ARTICLE_PROMPT_TEMPLATE = """You are Andy Lee, a chiropractor running a clinic in Hornsby, Sydney, Australia. \
Write a new blog post in ENGLISH for Australian readers for the site hornsbychiropractor.com.

TOPIC: {topic}
Today's date: {today}.

WRITING STYLE — CRITICAL, this must read like a real clinician wrote it, not an AI:
- Write for one ordinary patient, not for an algorithm. Prefer plain, specific language over polish.
- Vary sentence and paragraph length naturally. A short sentence is fine when it earns its place.
- Use first-person clinical voice sparingly and only for general professional observations. Never invent a
specific patient, quotation, result, case history, or claim about what patients supposedly told you.
- Natural transitions, sometimes none at all. Do NOT start consecutive paragraphs the same way.
- BANNED AI-isms: do not use "Moreover", "Furthermore", "In addition", "In conclusion", \
"It's important to note", "delve", "landscape", "tapestry", "game-changer", "navigate the world of". \
Also avoid "you are not alone", "let's break it down", "the good news is", "when it comes to", and
"understanding X is the first step". Do not use em dashes. Do not write perfectly balanced triads everywhere.
- Do not open with a generic definition, a rhetorical question, or an exaggerated empathy hook. Start with a
concrete situation the reader recognises. Do not finish with a tidy recap of every section.
- Avoid sales language and certainty. Sound calm, useful and a little conversational, not chirpy.
- Australian English spelling (e.g. "practise" as verb, "programme" only if truly needed, \
"favourite", "realise").

MEDICAL ACCURACY — CRITICAL:
- Every medical fact, statistic, study finding or treatment-effect claim MUST have an inline \
reference link to a trustworthy source: PubMed/NCBI (pubmed.ncbi.nlm.nih.gov), Cochrane \
(cochranelibrary.com), Mayo Clinic, WebMD, Better Health Channel (.vic.gov.au), healthdirect \
(.gov.au), or other .gov.au authorities. Format references as inline <a href="..."> links right \
after the sentence they support (e.g. ...as shown in a Cochrane review (<a href="https://...">Cochrane, 2021</a>).).
- NEVER invent numbers, percentages or effect sizes. If unsure of exact figures, phrase \
qualitatively and still cite a real, well-known source URL you are confident exists.
- Do not add a disclaimer inside html_body; the page template adds the standard clinical disclaimer.

SEARCH INTENT AND SEO — NATURAL, NEVER STUFFED:
- Choose one 3-7 word primary search phrase that exactly describes the patient's intent.
- Use that primary keyword naturally in the title, short slug, meta description, first paragraph and \
one useful <h2>. Across the body, use the exact phrase only 2-5 times; use natural related wording elsewhere.
- The title must lead with the patient need rather than the clinic name. Keep it 45-60 characters when possible.
- The opening must address the search intent in the first 100 visible words without sounding like an SEO formula.
- Mention Hornsby, Sydney or Australian context only where it gives the reader useful local context.
- Never repeat a keyword just to satisfy a count, and never make medical promises for ranking purposes.

STRUCTURE:
- Total length: 1100-1450 words (count only visible text).
- 5-7 <h2> section headings in total, including the FAQ heading; each content heading is followed by 1-3 paragraphs.
- An FAQ section at the end with 3-4 questions as <h3> headings, each answered in 2-4 sentences. \
FAQ answers may cite sources too.
- {internal_links}

OUTPUT FORMAT — return ONLY a valid JSON object, no markdown fences, matching exactly:
{{
  "primary_keyword": "the single 3-7 word patient search phrase",
  "slug": "short-kebab-case-url-slug",
  "title": "SEO title, max 60 characters",
  "meta_description": "benefit-led meta description, 145-155 characters",
  "category": "short category label like 'Lower back pain' or 'Neck & posture'",
  "intro_summary": "1-2 sentence summary used on the blog listing card, max 200 characters",
  "html_body": "<p>...</p><h2>...</h2>... full article HTML including FAQ section. \
Use only p, h2, h3, strong, em, ul, li, a tags. No h1 (the template adds it), no images.",
  "faq": [
    {{"question": "...", "answer": "..."}}
  ],
  "image_prompts": [
    {{"scene": "specific everyday opening scene with people, setting, action and composition", \
"alt": "short literal description of what is visible, without keyword stuffing"}},
    {{"scene": "different practical middle scene with people, setting, action and composition", \
"alt": "short literal description of this different visible scene"}}
  ]
}}
The faq array must mirror the FAQ H3s in html_body. The two image prompts must be visually distinct,
medically sensible everyday scenes. Do not request text, labels, logos, x-rays or exposed anatomy in them.
Before returning JSON, silently edit out stock AI phrasing, repetition, overclaiming and invented anecdotes."""


class OpenAIError(Exception):
    pass


def _openai_headers() -> dict[str, str]:
    if not OPENAI_API_KEY:
        raise OpenAIError("OPENAI_API_KEY is not set")
    return {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }


def _response_text(data: dict) -> str:
    """Collect output_text parts without assuming a fixed output-array position."""
    chunks: list[str] = []
    for item in data.get("output", []):
        if item.get("type") != "message":
            continue
        for part in item.get("content", []):
            if part.get("type") == "output_text" and part.get("text"):
                chunks.append(part["text"])
    if not chunks:
        raise OpenAIError(f"OpenAI returned no usable text: {json.dumps(data)[:500]}")
    return "\n".join(chunks)


def openai_generate(prompt: str, max_output_tokens: int = 8192) -> str:
    payload = {
        "model": OPENAI_MODEL,
        "instructions": (
            "Follow the requested output format exactly. Be medically cautious, "
            "write in natural Australian English, and never fabricate personal experience."
        ),
        "input": prompt,
        "max_output_tokens": max_output_tokens,
    }

    max_attempts = 5
    base_delay = 2
    for attempt in range(1, max_attempts + 1):
        resp = requests.post(
            OPENAI_RESPONSES_URL,
            headers=_openai_headers(),
            json=payload,
            timeout=TIMEOUT_OPENAI_TEXT,
        )
        if resp.status_code == 200:
            return _response_text(resp.json())

        if resp.status_code in (429, 500, 502, 503, 504):
            if attempt < max_attempts:
                delay = base_delay * (2 ** (attempt - 1))
                log(f"OpenAI HTTP {resp.status_code} (attempt {attempt}/{max_attempts}), "
                    f"retrying in {delay}s...")
                import time
                time.sleep(delay)
                continue
        raise OpenAIError(f"OpenAI HTTP {resp.status_code}: {resp.text[:500]}")


def parse_article_json(raw: str) -> dict:
    text = raw.strip()
    # Strip markdown fences if present despite instructions.
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("No JSON object found in response")
    obj = json.loads(text[start : end + 1])
    required = [
        "primary_keyword", "slug", "title", "meta_description", "category",
        "intro_summary", "html_body",
    ]
    missing = [k for k in required if not str(obj.get(k, "")).strip()]
    if missing:
        raise ValueError(f"Article JSON missing keys: {missing}")
    obj.setdefault("faq", [])
    if not isinstance(obj["faq"], list):
        obj["faq"] = []
    raw_prompts = obj.get("image_prompts") or []
    if not isinstance(raw_prompts, list):
        raw_prompts = []
    prompts = []
    for item in raw_prompts[:2]:
        if isinstance(item, dict):
            scene = str(item.get("scene", "")).strip()
            alt = str(item.get("alt", "")).strip()
        else:  # Backward-compatible with older response shapes.
            scene = str(item).strip()
            alt = ""
        if scene:
            prompts.append({
                "scene": scene,
                "alt": alt or f"Hand-drawn scene about {obj['title']}",
            })
    while len(prompts) < 2:
        prompts.append({
            "scene": (
                f"An everyday Australian adult dealing with {obj['title']} in a calm, "
                "realistic home or work setting"
            ),
            "alt": f"Everyday scene illustrating {obj['title']}",
        })
    obj["image_prompts"] = prompts
    obj["primary_keyword"] = re.sub(
        r"\s+", " ", str(obj["primary_keyword"])
    ).strip(" \"'.,;:")
    return obj


def article_style_issues(article: dict) -> list[str]:
    """Catch obvious machine-like copy before it reaches the site."""
    visible = strip_html(article["html_body"])
    lower = visible.lower()
    banned = (
        "moreover", "furthermore", "in conclusion", "it's important to note",
        "it is important to note", "delve", "tapestry", "game-changer",
        "navigate the world of", "you are not alone", "you're not alone",
        "let's break it down", "the good news is", "when it comes to",
    )
    issues = [f"banned phrase: {phrase}" for phrase in banned if phrase in lower]
    wc = word_count(article["html_body"])
    if wc < 850 or wc > 1500:
        issues.append(f"visible word count {wc} is outside 850-1500")
    if visible.count("—"):
        issues.append("contains em dash")
    if "general information only" in lower:
        issues.append("html_body contains a duplicate disclaimer")
    return issues


def _normalise_for_match(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", clean_visible_text(text).lower()))


def _normalise_candidate_url(url: str) -> str:
    url = html.unescape(url.strip())
    if url.startswith("/"):
        url = url.split("#", 1)[0].split("?", 1)[0]
        return url.rstrip("/") + "/"
    return url.rstrip("/") + "/"


def article_seo_issues(article: dict, allowed_urls: set[str]) -> list[str]:
    """Reject structural SEO mistakes while allowing natural prose around the keyphrase."""
    issues: list[str] = []
    keyword = _normalise_for_match(article.get("primary_keyword", ""))
    if len(keyword.split()) < 3 or len(keyword.split()) > 7:
        issues.append("primary_keyword must contain 3-7 words")

    title = _normalise_for_match(article["title"])
    meta = _normalise_for_match(article["meta_description"])
    first_paragraph_match = re.search(
        r"<p\b[^>]*>(.*?)</p>", article["html_body"], flags=re.IGNORECASE | re.DOTALL
    )
    first_paragraph = _normalise_for_match(
        first_paragraph_match.group(1) if first_paragraph_match else ""
    )
    h2_values = [
        _normalise_for_match(value)
        for value in re.findall(
            r"<h2\b[^>]*>(.*?)</h2>",
            article["html_body"],
            flags=re.IGNORECASE | re.DOTALL,
        )
    ]

    if keyword:
        if keyword not in title:
            issues.append("primary keyword is missing from the title")
        if slugify(keyword) not in slugify(article["slug"]):
            issues.append("primary keyword words are missing from the slug")
        if keyword not in meta:
            issues.append("primary keyword is missing from the meta description")
        if keyword not in first_paragraph:
            issues.append("primary keyword is missing from the first paragraph")
        if not any(keyword in heading for heading in h2_values):
            issues.append("primary keyword is missing from an H2")
        exact_uses = _normalise_for_match(article["html_body"]).count(keyword)
        if exact_uses < 2 or exact_uses > 6:
            issues.append(f"primary keyword appears {exact_uses} times in the body; expected 2-6")

    if len(article["title"]) > 60:
        issues.append(f"title is {len(article['title'])} characters; maximum is 60")
    meta_len = len(article["meta_description"])
    if meta_len < 145 or meta_len > 155:
        issues.append(f"meta description is {meta_len} characters; expected 145-155")
    if len(article["intro_summary"]) > 200:
        issues.append("intro summary exceeds 200 characters")
    if len(h2_values) < 5 or len(h2_values) > 7:
        issues.append(f"article has {len(h2_values)} H2 headings; expected 5-7")
    h3_count = len(re.findall(r"<h3\b", article["html_body"], flags=re.IGNORECASE))
    if h3_count < 3 or h3_count > 4:
        issues.append(f"article has {h3_count} H3 headings; expected 3-4 FAQ questions")

    hrefs = re.findall(
        r"<a\b[^>]*href\s*=\s*([\"'])(.*?)\1",
        article["html_body"],
        flags=re.IGNORECASE | re.DOTALL,
    )
    contextual_links = [
        _normalise_candidate_url(url)
        for _quote, url in hrefs
        if url.strip().startswith("/") or url.strip().startswith(BOOKING_URL)
    ]
    allowed_normalised = {_normalise_candidate_url(url) for url in allowed_urls}
    unknown = sorted({url for url in contextual_links if url not in allowed_normalised})
    if unknown:
        issues.append("unverified internal URL(s): " + ", ".join(unknown))
    if len(contextual_links) < 2 or len(contextual_links) > 4:
        issues.append(f"article has {len(contextual_links)} contextual site links; expected 2-4")
    if len(contextual_links) != len(set(contextual_links)):
        issues.append("a contextual site link is repeated")
    return issues


def pick_topic(existing_slugs: list[str]) -> tuple[str, bool]:
    """Returns (topic, from_ai)."""
    if TOPIC_INPUT:
        return TOPIC_INPUT, False
    existing_list = "\n".join(f"- {s}" for s in existing_slugs) or "- (none yet)"
    prompt = TOPIC_SYSTEM_PROMPT + "\n\nAlready published topics:\n" + existing_list
    raw = openai_generate(prompt, max_output_tokens=256).strip().strip('"').strip()
    if len(raw.split()) > 15:  # sanity check
        raw = " ".join(raw.split()[:12])
    if not raw:
        raise OpenAIError("OpenAI returned empty topic suggestion")
    return raw, True


def generate_article(topic: str) -> dict:
    internal_links, allowed_urls = build_internal_link_hints(topic)
    prompt = ARTICLE_PROMPT_TEMPLATE.format(
        topic=topic,
        today=sydney_today(),
        internal_links=internal_links,
    )
    last_err = None
    for attempt in range(1, 4):
        try:
            attempt_prompt = prompt
            if last_err is not None:
                attempt_prompt += (
                    "\n\nThe previous draft failed the automated checks below. Regenerate the complete "
                    "JSON article and correct every issue without mentioning this feedback:\n- "
                    + str(last_err).replace("; ", "\n- ")
                )
            raw = openai_generate(attempt_prompt)
            article = parse_article_json(raw)
            issues = article_style_issues(article) + article_seo_issues(article, allowed_urls)
            if issues:
                raise ValueError("quality check failed: " + "; ".join(issues))
            return article
        except (ValueError, KeyError, json.JSONDecodeError, OpenAIError) as exc:
            last_err = exc
            log(f"Article generation attempt {attempt}/3 failed: {exc}")
    raise OpenAIError(f"OpenAI article generation failed after 3 attempts: {last_err}")


# ---------------------------------------------------------------------------
# Images (ComfyUI first, with OpenAI fallback)
# ---------------------------------------------------------------------------

IMAGE_STYLE_PROMPT = """Create a warm hand-drawn 2D editorial animation still for an
Australian chiropractic clinic's patient-education article. Use clean ink outlines,
simple cel shading, a restrained earthy palette, subtle paper grain and slight natural
asymmetry. It should feel commissioned by a human illustrator, not glossy, synthetic or
overproduced. Show believable adult proportions, hands, posture, furniture and everyday
Australian surroundings. Keep the mood calm and practical, never dramatic or frightening.
No words, captions, labels, logos, watermarks, UI elements, floating symbols, cutaway
anatomy, glowing pain effects, x-rays, surreal objects, photorealism, 3D-rendered style,
plastic skin, hyper-detail or excessive gradients.

Scene to illustrate: {scene}
"""


def _effective_image_format() -> str:
    if OPENAI_IMAGE_MODEL.startswith("gpt-image-") and OPENAI_IMAGE_FORMAT in {
        "png", "jpeg", "webp",
    }:
        return OPENAI_IMAGE_FORMAT
    return "png"


def _requested_image_dimensions() -> tuple[int | None, int | None]:
    match = re.fullmatch(r"(\d+)x(\d+)", OPENAI_IMAGE_SIZE.strip().lower())
    if not match:
        return None, None
    return int(match.group(1)), int(match.group(2))


def _generate_image_bytes(scene: str) -> bytes:
    image_format = _effective_image_format()
    payload = {
        "model": OPENAI_IMAGE_MODEL,
        "prompt": IMAGE_STYLE_PROMPT.format(scene=scene),
        "size": OPENAI_IMAGE_SIZE,
        "quality": OPENAI_IMAGE_QUALITY,
    }
    if OPENAI_IMAGE_MODEL.startswith("gpt-image-"):
        payload["output_format"] = image_format
        if image_format in {"webp", "jpeg"}:
            payload["output_compression"] = OPENAI_IMAGE_COMPRESSION
    max_attempts = 4
    for attempt in range(1, max_attempts + 1):
        resp = requests.post(
            OPENAI_IMAGES_URL,
            headers=_openai_headers(),
            json=payload,
            timeout=TIMEOUT_OPENAI_IMAGE,
        )
        if resp.status_code == 200:
            data = resp.json()
            try:
                return base64.b64decode(data["data"][0]["b64_json"], validate=True)
            except (KeyError, IndexError, ValueError) as exc:
                raise OpenAIError(
                    f"OpenAI image response had no decodable image: {json.dumps(data)[:500]}"
                ) from exc
        if resp.status_code in (429, 500, 502, 503, 504) and attempt < max_attempts:
            delay = 3 * (2 ** (attempt - 1))
            log(f"OpenAI image HTTP {resp.status_code} (attempt {attempt}/{max_attempts}), "
                f"retrying in {delay}s...")
            import time
            time.sleep(delay)
            continue
        raise OpenAIError(f"OpenAI image HTTP {resp.status_code}: {resp.text[:500]}")
    raise OpenAIError("OpenAI image generation exhausted all retries")


def build_generated_images(article: dict) -> tuple[list[dict], list[str]]:
    """Generate two original, compressed illustrations and page-ready metadata."""
    ASSETS_IMG_DIR.mkdir(parents=True, exist_ok=True)
    images: list[dict] = []
    notes: list[str] = []
    prompts = article.get("image_prompts") or []
    image_format = _effective_image_format()
    extension = "jpg" if image_format == "jpeg" else image_format
    client = ComfyImageClient()
    if not client.configured:
        log("ComfyUI bridge not configured; using OpenAI illustrations.")

    for n, prompt_item in enumerate(prompts[:2], start=1):
        if isinstance(prompt_item, dict):
            scene = str(prompt_item.get("scene", "")).strip()
            alt = str(prompt_item.get("alt", "")).strip()
        else:
            scene = str(prompt_item).strip()
            alt = ""
        filename = f"{article['slug']}-illustration-{n}.{extension}"
        destination = ASSETS_IMG_DIR / filename
        try:
            asset, fallback_note = generate_with_fallback(
                scene, IMAGE_STYLE_PROMPT, _generate_image_bytes, image_format,
                OPENAI_IMAGE_COMPRESSION, client, log,
            )
            if fallback_note:
                notes.append(fallback_note)
            destination.write_bytes(asset.content)
            images.append({
                "ok": True,
                "kind": "generated",
                "public_path": f"/assets/blog-images/{filename}",
                "alt": alt or f"Hand-drawn scene about {article['title']}",
                "caption": "Original editorial illustration for Hornsby Chiropractor.",
                "n": n,
                "width": asset.width,
                "height": asset.height,
                "provider": asset.provider,
            })
            log(f"Generated illustration {n} with {asset.provider}: {filename}")
        except Exception as exc:  # noqa: BLE001
            notes.append(f"illustration {n} failed: {type(exc).__name__}: {exc}")

    if not images:
        raise OpenAIError("No blog illustrations could be generated: " + "; ".join(notes))
    return images, notes


# ---------------------------------------------------------------------------
# Legacy reference-image helpers (kept for existing posts and dry-run compatibility)
# ---------------------------------------------------------------------------

NCBI_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
NCBI_PARAMS = {"tool": "hornsby-blogbot", "email": "admin@hornsbychiropractor.com"}
HTTP_UA = {"User-Agent": "blogbot/1.0 (https://hornsbychiropractor.com; "
                         "admin@hornsbychiropractor.com)"}
MAX_REFERENCE_IMAGES = 3
EUTILS_RATE_SECONDS = 0.4   # stay under the keyless 3 req/s limit

PUBMED_URL_RE = re.compile(r"pubmed\.ncbi\.nlm\.nih\.gov/(\d+)")
PMCID_URL_RE = re.compile(r"ncbi\.nlm\.nih\.gov/pmc/articles/(PMC\d+)")

CC_BY_RE = re.compile(r"creativecommons\.org/licenses/(by|by-sa)(/[\d.]+)?",
                      re.I)


def _http_get(url: str, binary: bool = False, timeout: int = 60):
    resp = requests.get(url, headers=HTTP_UA, timeout=timeout)
    resp.raise_for_status()
    return resp.content if binary else resp.text


def _http_get_json(url: str, timeout: int = 60) -> dict:
    import time

    time.sleep(EUTILS_RATE_SECONDS)
    return json.loads(_http_get(url, timeout=timeout))


def extract_reference_urls(html_body: str) -> list[str]:
    """Return up to MAX_REFERENCE_IMAGES PubMed URLs found in the body.

    Order preserved, duplicates removed; PubMed links are preferred over
    PMC-only ones (they resolve to metadata more reliably).
    """
    seen: set[str] = set()
    urls: list[str] = []
    for pattern in (PUBMED_URL_RE, PMCID_URL_RE):
        for match in pattern.finditer(html_body):
            url = (
                f"https://pubmed.ncbi.nlm.nih.gov/{match.group(1)}/"
                if pattern is PUBMED_URL_RE
                else f"https://www.ncbi.nlm.nih.gov/pmc/articles/{match.group(1)}/"
            )
            if url not in seen:
                seen.add(url)
                urls.append(url)
            if len(urls) >= MAX_REFERENCE_IMAGES:
                return urls
    return urls


def _resolve_ids(ref_url: str) -> dict:
    """Resolve a pubmed/PMC URL to {'pmid': ..., 'pmcid': ...}.

    PubMed URLs resolve via esummary db=pubmed (which also returns the
    PMCID). For PMC-only URLs the PMCID is parsed directly from the URL
    and the PMID is recovered via esummary db=pmc when possible.
    """
    ids: dict = {}
    pmid_m = PUBMED_URL_RE.search(ref_url)
    pmcid_m = PMCID_URL_RE.search(ref_url)
    try:
        if pmid_m:
            meta = _fetch_metadata(pmid_m.group(1))
            if meta.get("title"):
                ids["pmid"] = pmid_m.group(1)
                if meta.get("pmcid"):
                    ids["pmcid"] = meta["pmcid"]
        elif pmcid_m:
            ids["pmcid"] = pmcid_m.group(1)
            uid_digits = ids["pmcid"].replace("PMC", "")
            data = _http_get_json(
                f"{NCBI_BASE}/esummary.fcgi?db=pmc&id={uid_digits}"
                "&retmode=json&"
                + "&".join(f"{k}={v}" for k, v in NCBI_PARAMS.items())
            )
            rec = (data.get("result") or {}).get(uid_digits) or {}
            for aid in rec.get("articleids") or []:
                if aid.get("idtype") == "pmid" and aid.get("value"):
                    ids["pmid"] = str(aid["value"])
                    break
    except Exception as exc:  # noqa: BLE001
        log(f"ID resolution failed for {ref_url}: {type(exc).__name__}: {exc}")
    return ids


def _fetch_metadata(pmid: str) -> dict:
    """Title/authors/journal/year/doi/pmcid via esummary db=pubmed.

    (The idconv API is blocked from this host with HTTP 403, but the
    esummary record carries the same PMID→PMCID mapping.)
    """
    meta: dict = {}
    if not pmid:
        return meta
    try:
        data = _http_get_json(
            f"{NCBI_BASE}/esummary.fcgi?db=pubmed&id={pmid}&retmode=json&"
            + "&".join(f"{k}={v}" for k, v in NCBI_PARAMS.items())
        )
        rec = (data.get("result") or {}).get(str(pmid)) or {}
        meta["title"] = rec.get("title", "").rstrip(".")
        authors = [a["name"] for a in (rec.get("authors") or [])]
        shown = ", ".join(authors[:6])
        if len(authors) > 6:
            shown += " et al."
        meta["authors"] = shown
        meta["journal"] = rec.get("fulljournalname") or rec.get("source", "")
        meta["year"] = (rec.get("pubdate") or "")[:4]
        for aid in rec.get("articleids") or []:
            value = aid.get("value", "")
            if aid.get("idtype") == "pmc" and value.startswith("PMC"):
                meta["pmcid"] = value
            elif aid.get("idtype") == "pmcid" and "pmcid" not in meta:
                import re as _re

                match = _re.search(r"PMC\d+", value)
                if match:
                    meta["pmcid"] = match.group(0)
            elif aid.get("idtype") == "doi" and value:
                meta["doi"] = value
    except Exception as exc:  # noqa: BLE001
        log(f"esummary failed for PMID {pmid}: {type(exc).__name__}: {exc}")
    return meta


def _first_pmc_figure(pmc_uid: str) -> dict | None:
    """First CC-BY-licensed figure from a PMC article, downloaded as jpg.

    Returns {'local_path', 'label', 'caption', 'license'} or None.
    """
    uid_digits = pmc_uid.replace("PMC", "")
    try:
        xml_text = _http_get(
            f"{NCBI_BASE}/efetch.fcgi?db=pmc&id={uid_digits}&retmode=xml&"
            + "&".join(f"{k}={v}" for k, v in NCBI_PARAMS.items())
        )
    except Exception as exc:  # noqa: BLE001
        log(f"efetch db=pmc failed for {pmc_uid}: {type(exc).__name__}: {exc}")
        return None

    lic_match = CC_BY_RE.search(xml_text)
    if not lic_match:
        log(f"{pmc_uid}: no CC-BY license in PMC XML - skipping figure.")
        return None
    license_label = f"CC BY{lic_match.group(2) or ''}"

    fig_match = re.search(r'<fig\b[^>]*>(.*?)</fig>', xml_text, flags=re.DOTALL)
    if not fig_match:
        log(f"{pmc_uid}: no <fig> element in PMC XML.")
        return None
    fig_xml = fig_match.group(1)

    graphic_match = re.search(r'<graphic[^>]*xlink:href="([^"]+)"', fig_xml)
    label_match = re.search(r"<label>(.*?)</label>", fig_xml, flags=re.DOTALL)
    caption_match = re.search(r"<caption>(.*?)</caption>", fig_xml,
                              flags=re.DOTALL)
    clean = lambda s: html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s))).strip()  # noqa: E731

    graphic_name = (graphic_match.group(1) if graphic_match else "").strip()
    label = clean(label_match.group(1)) if label_match else ""
    caption = clean(caption_match.group(1))[:220] if caption_match else ""
    if not graphic_name:
        log(f"{pmc_uid}: first figure has no graphic file - skipped.")
        return None

    # Download via the live PMC article page (cdn blob URL).
    image_bytes = None
    try:
        page = _http_get(f"https://pmc.ncbi.nlm.nih.gov/articles/{pmc_uid}/")
        img_srcs = re.findall(r'<img[^>]+src="([^"]+)"', page)
        stem = graphic_name.rsplit(".", 1)[0]
        candidates = [s for s in img_srcs if stem in s]
        for src in candidates:
            url = src if src.startswith("http") else f"https://pmc.ncbi.nlm.nih.gov{src}"
            try:
                image_bytes = _http_get(url, binary=True)
                break
            except Exception:  # noqa: BLE001
                continue
    except Exception as exc:  # noqa: BLE001
        log(f"{pmc_uid}: PMC page scrape failed: {type(exc).__name__}: {exc}")

    if not image_bytes or not image_bytes[:3] == b"\xff\xd8\xff":
        log(f"{pmc_uid}: could not download figure jpg - skipped.")
        return None
    return {
        "image_bytes": image_bytes,
        "label": label,
        "caption": caption,
        "license": license_label,
    }


def build_reference_images(article: dict) -> tuple[list[dict], list[str]]:
    """Build real paper-based images for the article's references.

    For each PubMed/PMC reference (up to MAX_REFERENCE_IMAGES):
      * always an academic paper-card SVG;
      * plus the first CC-BY figure from PMC Open Access when available.

    Returns (images, notes). Each image dict:
      {ok, kind('figure'|'card'), public_path, alt, caption_source,
       citation, license, ref_url}
    """
    notes: list[str] = []
    slug = article["slug"]
    ASSETS_IMG_DIR.mkdir(parents=True, exist_ok=True)
    images: list[dict] = []

    ref_urls = extract_reference_urls(article["html_body"])
    if not ref_urls:
        log("No PubMed references found in body - no reference images.")
        return images, notes

    # Extract article topic/keywords for relevance filtering
    article_keywords = _extract_article_keywords(article)

    for n, ref_url in enumerate(ref_urls, start=1):
        ids = _resolve_ids(ref_url)
        pmid = ids.get("pmid", "")
        meta = _fetch_metadata(pmid)
        title = meta.get("title") or "PubMed publication"
        
        # Check relevance before processing
        if not _is_reference_relevant(title, meta.get("abstract", ""), article_keywords):
            log(f"Reference {n} skipped - not relevant to article topic: {title[:80]}")
            continue
            
        authors_short = meta.get("authors", "Unknown authors")
        # Short author form for captions/cards: "Desouzart G et al."
        first_author = authors_short.split(",")[0].strip() if authors_short else ""
        year = meta.get("year", "")
        journal = meta.get("journal", "")
        doi = meta.get("doi", "")
        citation = f'{first_author} et al. ({year}). "{title}". {journal}'.strip()

        # ---- figure (only for PMC Open Access with CC-BY license) --------
        pmc_uid = ids.get("pmcid")
        figure = _first_pmc_figure(pmc_uid) if pmc_uid else None

        if figure:
            filename = f"{slug}-ref{n}.jpg"
            dest = ASSETS_IMG_DIR / filename
            try:
                dest.write_bytes(figure["image_bytes"])
                ok = True
                log(f"Reference {n}: figure saved {filename} ({pmc_uid}, "
                    f"{figure['license']})")
            except Exception as exc:  # noqa: BLE001
                ok = False
                notes.append(f"reference {n} figure write failed: {exc}")
            if ok:
                images.append({
                    "ok": True,
                    "kind": "figure",
                    "public_path": f"/assets/blog-images/{filename}",
                    "alt": figure["caption"] or f"{title} — figure",
                    "citation": citation,
                    "license": figure["license"],
                    "ref_url": ref_url,
                    "n": n,
                })

        # ---- paper card SVG (one per reference) --------------------------
        card_filename = f"{slug}-paper-card-{n}.svg"
        card_dest = ASSETS_IMG_DIR / card_filename
        open_access = bool(pmc_uid)
        try:
            svg_illustrations.add_paper_card_svg(
                title=title, authors=authors_short, journal=journal,
                year=year, doi=doi, out_path=str(card_dest),
                open_access=open_access,
            )
            card_ok = True
        except Exception as exc:  # noqa: BLE001
            card_ok = False
            notes.append(f"reference {n} card failed: "
                         f"{type(exc).__name__}: {exc}")
        if card_ok:
            images.append({
                "ok": True,
                "kind": "card",
                "public_path": f"/assets/blog-images/{card_filename}",
                "alt": f'Paper: "{title}" ({first_author} et al., {year})',
                "citation": citation,
                "license": "Open Access" if open_access else "Publisher",
                "ref_url": ref_url,
                "n": n,
            })
            log(f"Reference {n}: paper card saved {card_filename}")

    return images, notes


def _reference_figure_html(img: dict) -> str:
    """Build a post figure for a generated illustration or reference image."""
    if img.get("kind") == "generated":
        figcaption = html.escape(img.get("caption", "Original editorial illustration."))
    else:
        figcaption = (
            f'Source: {html.escape(img["citation"])}. '
            f'<a href="{img["ref_url"]}">{html.escape(img["license"])}</a>'
        )
    dimension_attrs = ""
    if img.get("width") and img.get("height"):
        dimension_attrs = f' width="{int(img["width"])}" height="{int(img["height"])}"'
    if int(img.get("n", 2)) == 1:
        loading_attrs = ' loading="eager" fetchpriority="high" decoding="async"'
    else:
        loading_attrs = ' loading="lazy" decoding="async"'
    return (
        '<figure class="post-figure">\n'
        f'          <img src="{img["public_path"]}" '
        f'alt="{html.escape(img["alt"])}"{dimension_attrs}{loading_attrs}>\n'
        f'          <figcaption>{figcaption}</figcaption>\n'
        "        </figure>"
    )


def insert_images_into_body(body: str, images: list[dict], title: str) -> str:
    """Insert illustrations near the front and middle of the article."""
    usable = [im for im in images if im.get("ok")]
    if not usable:
        return body
    # Anchor points: after the first paragraph (front) and after a mid-article
    # h2 heading; extra images just append in order at whatever anchors exist.
    anchors: list[str] = []
    paragraphs = re.findall(r"<p>.*?</p>", body, flags=re.DOTALL)
    if paragraphs:
        anchors.append(paragraphs[0])
    h2s = re.findall(r"<h2>.*?</h2>", body, flags=re.DOTALL)
    if len(h2s) >= 2:
        anchors.append(h2s[1])
    elif h2s:
        anchors.append(h2s[0])
    fig_index = 0
    for img in usable:
        if fig_index >= len(anchors):
            break
        anchor = anchors[fig_index]
        figure = _reference_figure_html(img)
        body = body.replace(anchor, anchor + "\n        " + figure, 1)
        fig_index += 1
    return body


def build_related_articles_html(article: dict, max_count: int = 3) -> str:
    """Build a small, crawlable related-reading block using real published posts."""
    query = " ".join(
        str(article.get(key, ""))
        for key in ("primary_keyword", "title", "category", "description", "meta_description")
    )
    related = _rank_related_posts(
        query,
        exclude_slug=str(article.get("slug", "")),
        limit=max_count,
    )
    if not related:
        return ""
    items = "\n".join(
        f'            <li><a href="{item["url"]}">{html.escape(item["title"])}</a></li>'
        for item in related
    )
    return (
        '        <!-- related-posts:start -->\n'
        '        <aside class="related-posts" aria-labelledby="related-articles-heading">\n'
        '          <h2 id="related-articles-heading">Related articles</h2>\n'
        '          <ul>\n'
        f"{items}\n"
        '          </ul>\n'
        '        </aside>\n'
        '        <!-- related-posts:end -->'
    )


# ---------------------------------------------------------------------------
# Page assembly
# ---------------------------------------------------------------------------

PAGE_TEMPLATE = """<!doctype html>
<html lang="en-AU">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{seo_title}</title>
    <meta
      name="description"
      content="{meta_description}"
    >
    <link rel="canonical" href="{canonical}">
    <link rel="icon" href="/assets/icon-192.png" sizes="192x192" type="image/png">
    <meta property="og:type" content="article">
    <meta property="og:title" content="{og_title}">
    <meta property="og:description" content="{meta_description}">
    <meta property="og:url" content="{canonical}">
    <meta property="og:image" content="{og_image}">
    <meta property="og:site_name" content="Hornsby Chiropractor">
    <meta property="og:locale" content="en_AU">
    <meta name="twitter:card" content="summary_large_image">
    <meta name="twitter:title" content="{og_title}">
    <meta name="twitter:description" content="{meta_description}">
    <meta name="twitter:image" content="{og_image}">
    <script type="application/ld+json">
{blogposting_jsonld}
    </script>
    <script type="application/ld+json">
{faq_jsonld}
    </script>
    <link rel="stylesheet" href="/styles.css">
    <script src="/whatsapp.js" defer></script>
    <script src="/openai-pixel.js" defer></script>
  </head>
  <body>
{chrome}
{main}
    <a class="mobile-call" href="https://aseschedule.com/book/10d766b5-81f6-43b1-9a09-0b7dc8404ce2/default/">Book online</a>

    <footer>
      <p>&copy; 2026 Hornsby Chiropractor. All rights reserved.</p>
    </footer>
  </body>
</html>
"""

MAIN_TEMPLATE = """<main class="post-page">
      <article class="post-article">
        <a class="post-back" href="/blog/">Back to blog</a>
        <h1>{title}</h1>
        <p class="post-meta"><time datetime="{date_iso}">{date_human}</time> · \
<a href="/#about">Andy Lee, Chiropractor</a> · {category}</p>
{body_with_images}
{related_html}
        <p class="post-disclaimer"><em>Disclaimer: this article is general information only, \
not medical diagnosis or treatment advice. Every person is different — please consult a \
qualified health professional (like your local chiropractor or GP) before acting on anything \
you read here.</em></p>
      </article>
    </main>
"""

CHROME_FALLBACK = """<header class="site-header">
      <a class="brand" href="/" aria-label="Hornsby Chiropractor home">
        <img src="/assets/hornsby-logo-cropped.png" alt="Hornsby Chiropractor">
      </a>
      <nav aria-label="Primary navigation">
        <a href="/services/">Services</a>
        <a href="/#about">About</a>
        <a href="/#contact">Contact</a>
        <a href="/directions/">Directions</a>
        <a href="/blog/">Blog</a>
      </nav>
      <div class="header-actions">
        <a class="header-book" href="https://aseschedule.com/book/10d766b5-81f6-43b1-9a09-0b7dc8404ce2/default/">Book online</a>
      </div>
    </header>

    <details class="mobile-menu">
      <summary aria-label="Open menu"><span></span><span></span><span></span></summary>
      <div class="mobile-menu-links">
        <a href="/services/">Services</a>
        <a href="/#about">About</a>
        <a href="/#contact">Contact</a>
        <a href="/directions/">Directions</a>
        <a href="/blog/">Blog</a>
        <a href="https://aseschedule.com/book/10d766b5-81f6-43b1-9a09-0b7dc8404ce2/default/">Book online</a>
      </div>
    </details>"""


def extract_chrome(template_path: Path) -> str:
    """Copy header/nav/mobile-menu markup verbatim from an existing post."""
    try:
        source = template_path.read_text(encoding="utf-8")
    except OSError:
        return CHROME_FALLBACK
    match = re.search(r"(<header class=\"site-header\">.*?)\s*(?=<main)", source, flags=re.DOTALL)
    return match.group(1).rstrip() if match else CHROME_FALLBACK


def clamp_meta(text: str, limit: int, pad_to: int | None = None) -> str:
    """Trim to <=limit chars at a word boundary; optionally pad toward a range floor."""
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        cut = text[:limit]
        if " " in cut:
            cut = cut.rsplit(" ", 1)[0]
        text = cut.rstrip(",;:")
        if not text.endswith((".", "!", "?")):
            text += "…"
    return text


def ensure_title_len(title: str, max_len: int = 60) -> str:
    title = re.sub(r"\s+", " ", title).strip()
    suffix = " | Hornsby Chiropractor"
    plain = re.sub(r"\s*\|\s*(?:Hornsby Chiropractor|Hornsby Chiro)\s*$", "", title, flags=re.IGNORECASE)
    plain = plain.rstrip(" |-")
    # Use full 60 chars for title, only add suffix if there's room
    if len(plain) + len(suffix) + 1 <= max_len:
        return plain + suffix
    # If title + suffix exceeds max_len, truncate title to fit
    if len(plain) > max_len:
        cut = plain[:max_len]
        plain = (cut.rsplit(" ", 1)[0] if " " in cut else cut).rstrip()
    return plain


def build_blogposting_jsonld(article: dict, canonical: str, date_pub: str, og_image: str) -> str:
    data = {
        "@context": "https://schema.org",
        "@type": "BlogPosting",
        "headline": article["title"],
        "description": article["meta_description"],
        "inLanguage": "en-AU",
        "author": {
            "@type": "Person",
            "name": "Andy Lee",
            "jobTitle": "Chiropractor",
            "url": f"{SITE_DOMAIN}/#about",
        },
        "publisher": {
            "@type": "Organization",
            "name": "Hornsby Chiropractor",
            "url": SITE_DOMAIN,
            "logo": {
                "@type": "ImageObject",
                "url": f"{SITE_DOMAIN}/assets/hornsby-logo-cropped.png",
            },
        },
        "datePublished": date_pub,
        "dateModified": date_pub,
        "url": canonical,
        "mainEntityOfPage": {"@type": "WebPage", "@id": canonical},
        "isPartOf": {"@type": "Blog", "@id": f"{SITE_DOMAIN}/blog/#blog"},
        "image": [og_image] if og_image else [],
        "articleSection": article.get("category", ""),
        "keywords": ", ".join(
            value for value in (
                article.get("primary_keyword", ""), article.get("category", "")
            ) if value
        ),
    }
    return json.dumps(data, indent=2, ensure_ascii=False)


def build_faq_jsonld(article: dict) -> str:
    entities = []
    for item in article.get("faq", []):
        q = str(item.get("question", "")).strip()
        a = strip_html(str(item.get("answer", ""))).strip()
        if q and a:
            entities.append(
                {
                    "@type": "Question",
                    "name": q,
                    "acceptedAnswer": {"@type": "Answer", "text": a},
                }
            )
    data = {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": entities,
    }
    return json.dumps(data, indent=2, ensure_ascii=False)


def write_post_page(article: dict, images: list[dict], chrome: str) -> Path:
    slug = article["slug"]
    date_obj = datetime.now(SYDNEY_TZ)
    date_iso = date_obj.strftime("%Y-%m-%d")
    date_human = date_obj.strftime("%d %B %Y")
    canonical = f"{SITE_DOMAIN}/blog/{slug}/"

    first_img = next((im for im in images if im["ok"]), None)
    og_image = f"{SITE_DOMAIN}{first_img['public_path']}" if first_img else f"{SITE_DOMAIN}/assets/hornsby-logo-cropped.png"

    seo_title = ensure_title_len(article["title"])
    meta_description = clamp_meta(article["meta_description"], 155)
    category = html.escape(article["category"])

    body_with_images = insert_images_into_body(article["html_body"], images, article["title"])

    main_html = MAIN_TEMPLATE.format(
        title=html.escape(article["title"]),
        date_iso=date_iso,
        date_human=date_human,
        category=category,
        body_with_images=body_with_images,
        related_html=build_related_articles_html(article),
    )

    page = PAGE_TEMPLATE.format(
        seo_title=html.escape(seo_title),
        meta_description=html.escape(meta_description),
        canonical=canonical,
        og_title=html.escape(article["title"]),
        og_image=og_image,
        blogposting_jsonld=build_blogposting_jsonld(article, canonical, date_iso, og_image),
        faq_jsonld=build_faq_jsonld(article),
        chrome=chrome,
        main=main_html,
    )

    out_dir = BLOG_DIR / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "index.html"
    out_file.write_text(page, encoding="utf-8")
    return out_file


def build_blog_index_jsonld() -> str:
    posts = []
    for item in extract_post_catalog():
        post = {
            "@type": "BlogPosting",
            "headline": item["title"],
            "url": item["canonical"],
        }
        if item["description"]:
            post["description"] = item["description"]
        if item["date"]:
            post["datePublished"] = item["date"]
        if item["image"]:
            post["image"] = item["image"]
        posts.append(post)
    data = {
        "@context": "https://schema.org",
        "@type": "Blog",
        "@id": f"{SITE_DOMAIN}/blog/#blog",
        "url": f"{SITE_DOMAIN}/blog/",
        "name": BLOG_NAME,
        "description": BLOG_DESCRIPTION,
        "inLanguage": "en-AU",
        "image": f"{SITE_DOMAIN}/assets/hero-treatment-wide.jpg",
        "publisher": {
            "@type": "Organization",
            "name": "Hornsby Chiropractor",
            "url": SITE_DOMAIN,
            "logo": {
                "@type": "ImageObject",
                "url": f"{SITE_DOMAIN}/assets/hornsby-logo-cropped.png",
            },
        },
        "blogPost": posts,
    }
    return json.dumps(data, indent=2, ensure_ascii=False)


def _upsert_blog_index_schema(page: str) -> str:
    schema = build_blog_index_jsonld()
    block = f'    <script id="blog-schema" type="application/ld+json">\n{schema}\n    </script>'
    pattern = re.compile(
        r"\s*<script\b[^>]*id=[\"']blog-schema[\"'][^>]*>.*?</script>",
        flags=re.IGNORECASE | re.DOTALL,
    )
    if pattern.search(page):
        return pattern.sub("\n" + block, page, count=1)
    return page.replace("  </head>", block + "\n  </head>", 1)


def refresh_blog_index_schema() -> Path:
    listing = BLOG_DIR / "index.html"
    page = listing.read_text(encoding="utf-8")
    listing.write_text(_upsert_blog_index_schema(page), encoding="utf-8")
    return listing


def prepend_blog_card(article: dict) -> Path:
    listing = BLOG_DIR / "index.html"
    text = listing.read_text(encoding="utf-8")
    marker = '<section class="blog-list"'
    idx = text.find(marker)
    if idx == -1:
        raise RuntimeError("Could not find blog-list section in blog/index.html")
    tag_end = text.find(">", idx) + 1
    # Skip past the aria-label attribute close if it's part of the same tag.
    while text.find(">", idx, tag_end - 1) != -1 and False:
        break
    card = (
        f'\n        <a class="blog-card" href="/blog/{article["slug"]}/">\n'
        f'          <p class="eyebrow">{html.escape(article["category"])}</p>\n'
        f"          <h2>{html.escape(article['title'])}</h2>\n"
        f"          <p>\n"
        f"            {html.escape(clamp_meta(article['intro_summary'], 200))}\n"
        f"          </p>\n"
        f"        </a>"
    )
    new_text = _upsert_blog_index_schema(text[:tag_end] + card + text[tag_end:])
    listing.write_text(new_text, encoding="utf-8")
    return listing


def refresh_existing_related_posts() -> int:
    """Idempotently add a relevant three-link reading block to every post."""
    changed = 0
    marker_pattern = re.compile(
        r"\s*<!-- related-posts:start -->.*?<!-- related-posts:end -->",
        flags=re.DOTALL,
    )
    for item in extract_post_catalog():
        page = item["path"].read_text(encoding="utf-8")
        page_without_old = marker_pattern.sub("", page)
        related_html = build_related_articles_html(item)
        if not related_html:
            continue
        disclaimer_marker = '        <p class="post-disclaimer">'
        if disclaimer_marker in page_without_old:
            updated = page_without_old.replace(
                disclaimer_marker,
                related_html + "\n" + disclaimer_marker,
                1,
            )
        elif "      </article>" in page_without_old:
            updated = page_without_old.replace(
                "      </article>", related_html + "\n      </article>", 1
            )
        else:
            continue
        if updated != page:
            item["path"].write_text(updated, encoding="utf-8")
            changed += 1
    return changed


SITEMAP_HEADER = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
SITEMAP_FOOTER = "</urlset>\n"


def update_sitemap(slug: str) -> Path:
    sitemap = REPO_ROOT / "sitemap.xml"
    today = sydney_today()
    entry = (
        f"  <url>\n    <loc>{SITE_DOMAIN}/blog/{slug}/</loc>\n"
        f"    <lastmod>{today}</lastmod>\n  </url>\n"
    )
    if sitemap.exists():
        text = sitemap.read_text(encoding="utf-8")
        if f"/blog/{slug}/" in text:
            log("Sitemap already contains this URL.")
            return sitemap
        new_text = text.replace(SITEMAP_HEADER, SITEMAP_HEADER + entry) \
            if SITEMAP_HEADER in text else \
            text.replace("</urlset>", entry + "</urlset>")
        sitemap.write_text(new_text, encoding="utf-8")
    else:
        # No sitemap yet: create one seeded with core pages plus the new post.
        pages = [
            "/",
            "/services/",
            "/directions/",
            "/blog/",
            f"/blog/{slug}/",
            "/blog/lumbar-disc-injury-management/",
            "/blog/disc-protrusion-herniated-disc-sciatica-cortisone-injection-surgery/",
        ]
        body = SITEMAP_HEADER
        for p in pages:
            mod = today if p.startswith("/blog/") else today
            body += (
                f"  <url>\n    <loc>{SITE_DOMAIN}{p}</loc>\n"
                f"    <lastmod>{mod}</lastmod>\n  </url>\n"
            )
        body += SITEMAP_FOOTER
        sitemap.write_text(body, encoding="utf-8")
    return sitemap


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------


def run_pipeline() -> tuple[str, str]:
    """Full pipeline. Returns (published_url, title)."""
    existing = extract_existing_topics()

    log("Picking topic...")
    topic, from_ai = pick_topic(existing)
    log(f'Topic{" (AI)" if from_ai else " (manual)"}: {topic}')

    log(f"Generating article with {OPENAI_MODEL}...")
    article = generate_article(topic)

    # Deduplicate slug against existing folders.
    base_slug = slugify(article["slug"] or article["title"])
    slug = base_slug
    n = 2
    while slug in existing and (BLOG_DIR / slug).exists():
        slug = f"{base_slug}-{n}"
        n += 1
    if slug != base_slug:
        log(f"Slug collision: {base_slug} -> {slug}")
    article["slug"] = slug

    wc = word_count(article["html_body"])
    log(f"Article ready: '{article['title']}' (~{wc} words, slug={slug})")

    log(f"Generating illustrations: ComfyUI first, OpenAI ({OPENAI_IMAGE_MODEL}) fallback...")
    images, img_notes = build_generated_images(article)
    for note in img_notes:
        log(note)
    ok_count = sum(1 for i in images if i["ok"])
    log(f"Images: {ok_count}/{len(article['image_prompts'][:2])} generated illustrations.")

    chrome = extract_chrome(BLOG_DIR / "lumbar-disc-injury-management" / "index.html")
    post_path = write_post_page(article, images, chrome)
    related_updates = refresh_existing_related_posts()
    listing_path = prepend_blog_card(article)
    sitemap_path = update_sitemap(slug)

    log(f"Wrote {post_path}")
    log(f"Refreshed related reading on {related_updates} post(s)")
    log(f"Updated {listing_path}")
    log(f"Updated {sitemap_path}")

    url = f"{SITE_DOMAIN}/blog/{slug}/"
    send_telegram(
        "✅ <b>New blog post published</b>\n\n"
        f"<b>{html.escape(article['title'])}</b>\n"
        f"🔗 {url}\n"
        f"🗂 Category: {html.escape(article['category'])}\n"
        f"📝 ~{wc} words · 🖼 {ok_count}/{len(images)} images\n"
        f"🤖 models: {OPENAI_MODEL} + {OPENAI_IMAGE_MODEL}"
    )
    return url, article["title"]


def dry_run() -> int:
    """Assemble everything with fake data — no network calls."""
    log("DRY RUN: assembling templates with sample data (no network calls).")
    assert ensure_title_len("Hip Flexor Guide | Hornsby Chiro") == (
        "Hip Flexor Guide | Hornsby Chiropractor"
    )
    assert ensure_title_len("Hip Flexor Guide | Hornsby Chiropractor").count(
        "Hornsby Chiropractor"
    ) == 1
    article = {
        "slug": "dry-run-test-post",
        "primary_keyword": "dry run test post",
        "title": "Dry Run Test Post for Template Verification",
        "meta_description": (
            "Dry run test post for checking page structure, internal links, metadata and image "
            "placement before the automated Hornsby blog workflow publishes."
        ),
        "category": "Lower back pain",
        "intro_summary": "This is a dry-run summary used to verify the blog listing card insertion logic.",
        "html_body": (
            "<p>This dry run test post checks the page before publication. It exists purely "
            "to check that images get inserted after the correct anchor elements.</p>"
            "<p>A second paragraph links to the <a href=\"/services/\">clinic services</a> "
            "and acts as the primary image anchor point.</p>"
            "<h2>How the dry run test post is checked</h2><p>Section one body text with a "
            '<a href="https://www.healthdirect.gov.au/">reference link</a>.</p>'
            "<h2>Check the assembled page</h2><p>Section two body text links to the "
            '<a href="/blog/lumbar-disc-injury-management/">lumbar management guide</a>.</p>'
            "<h2>Check image placement</h2><p>Section three body text.</p>"
            "<h2>Check publishing metadata</h2><p>Section four body text.</p>"
            "<h2>Frequently asked questions</h2>"
            "<h3>What is a dry run?</h3><p>An answer explaining dry runs.</p>"
            "<h3>Does it call the API?</h3><p>No, the test remains offline.</p>"
            "<h3>Are temporary files kept?</h3><p>No, they are removed after validation.</p>"
        ),
        "faq": [
            {"question": "What is a dry run?", "answer": "A test without side effects."},
            {"question": "Does it call the API?", "answer": "No, the test remains offline."},
            {"question": "Are temporary files kept?", "answer": "No, they are removed."},
        ],
        "image_prompts": [
            {"scene": "A clinician checking a webpage layout", "alt": "Clinician checking a webpage"},
            {"scene": "A tidy desk with a publishing checklist", "alt": "Publishing checklist on a desk"},
        ],
    }
    allowed_test_urls = {"/services/", "/blog/lumbar-disc-injury-management/"}
    seo_issues = article_seo_issues(article, allowed_test_urls)
    assert not seo_issues, f"valid SEO fixture failed: {seo_issues}"
    missing_intro_keyword = dict(article)
    missing_intro_keyword["html_body"] = article["html_body"].replace(
        "dry run test post", "template verification", 1
    )
    assert any(
        "first paragraph" in issue
        for issue in article_seo_issues(missing_intro_keyword, allowed_test_urls)
    )
    unknown_link = dict(article)
    unknown_link["html_body"] = article["html_body"].replace(
        "/services/", "/blog/not-a-real-post/", 1
    )
    assert any(
        "unverified internal URL" in issue
        for issue in article_seo_issues(unknown_link, allowed_test_urls)
    )
    images = [
        {"ok": True, "kind": "generated",
         "public_path": "/assets/blog-images/dry-run-test-post-illustration-1.webp",
         "alt": "Hand-drawn sample illustration",
         "caption": "Original editorial illustration for Hornsby Chiropractor.",
         "width": 1536, "height": 1024, "n": 1},
        {"ok": True, "kind": "generated",
         "public_path": "/assets/blog-images/dry-run-test-post-illustration-2.webp",
         "alt": "Second hand-drawn sample illustration",
         "caption": "Original editorial illustration for Hornsby Chiropractor.",
         "width": 1536, "height": 1024, "n": 2},
    ]
    chrome = extract_chrome(BLOG_DIR / "lumbar-disc-injury-management" / "index.html")
    assert chrome.strip(), "Chrome extraction produced empty output"

    post_path = write_post_page(article, images, chrome)
    listing_path = prepend_blog_card(article)
    sitemap_path = update_sitemap(article["slug"])

    for path in (post_path, listing_path, sitemap_path):
        text = path.read_text(encoding="utf-8")
        print(f"  wrote {path.relative_to(REPO_ROOT)} ({len(text)} chars)")
        if path == post_path:
            checks = {
                "canonical": 'rel="canonical"' in text,
                "favicon": 'rel="icon"' in text,
                "og tags": 'property="og:title"' in text,
                "BlogPosting JSON-LD": '"@type": "BlogPosting"' in text,
                "FAQPage JSON-LD": '"@type": "FAQPage"' in text,
                "Australian language metadata": '"inLanguage": "en-AU"' in text,
                "visible clinician byline": "Andy Lee, Chiropractor" in text,
                "site header copied": 'class="site-header"' in text,
                "mobile menu copied": 'class="mobile-menu"' in text,
                "footer present": "<footer>" in text,
                "post-figure structure": '<figure class="post-figure">' in text,
                "figcaption present": "<figcaption>" in text,
                "illustration caption present": "Original editorial illustration" in text,
                "WebP image inserted": "/assets/blog-images/dry-run-test-post-illustration-" in text and ".webp" in text,
                "image dimensions": 'width="1536" height="1024"' in text,
                "first image prioritised": 'loading="eager" fetchpriority="high"' in text,
                "related articles": 'class="related-posts"' in text,
                "disclaimer": "general information only" in text,
            }
            for name, passed in checks.items():
                print(f"  [{'OK' if passed else 'FAIL'}] {name}")
            if not all(checks.values()):
                print("DRY RUN FAILED: some template checks did not pass")
                return 1
        elif path.name == "index.html" and path.parent == BLOG_DIR:
            listing_checks = {
                "new blog card prepended": 'href="/blog/dry-run-test-post/"' in text,
                "listing OG metadata": 'property="og:title"' in text,
                "one Blog JSON-LD schema": text.count('"@type": "Blog"') == 1,
            }
            for name, passed in listing_checks.items():
                print(f"  [{'OK' if passed else 'FAIL'}] {name}")
            if not all(listing_checks.values()):
                return 1
        else:
            ok = f"/blog/{article['slug']}/" in text
            print(f"  [{'OK' if ok else 'FAIL'}] sitemap contains new URL")
            if not ok:
                return 1

    # Clean up dry-run artifacts so git status stays clean.
    import shutil

    shutil.rmtree(BLOG_DIR / article["slug"], ignore_errors=True)
    _restore_listing(listing_path)
    _restore_sitemap(sitemap_path)
    log("DRY RUN PASSED - artifacts cleaned up.")
    return 0


_LISTING_BACKUP = None
_SITEMAP_BACKUP = None


def _extract_article_keywords(article: dict) -> set[str]:
    """Extract medical/condition keywords from article for relevance filtering."""
    text = f"{article.get('title', '')} {article.get('category', '')} {article.get('html_body', '')}"
    text = text.lower()
    
    # Medical/condition keywords relevant to chiropractic content
    keywords = {
        # Conditions
        'sciatica', 'back pain', 'lower back', 'lumbar', 'disc', 'herniated', 'protrusion',
        'neck pain', 'cervical', 'whiplash', 'headache', 'migraine',
        'scoliosis', 'posture', 'ergonomic', 'nerve', 'radiculopathy',
        'piriformis', 'spinal', 'vertebra', 'facet', 'spondylolisthesis',
        'stenosis', 'arthritis', 'degenerative', 'bulging', 'slipped disc',
        # Anatomy
        'spine', 'spinal cord', 'nerve root', 'sacroiliac', 'pelvis',
        'hip', 'leg pain', 'foot', 'numbness', 'tingling',
        # Treatments
        'adjustment', 'manipulation', 'mobilization', 'therapy', 'exercise',
        'stretch', 'rehabilitation', 'chiropractic', 'physical therapy',
        'decompression', 'traction', 'massage', 'acupuncture',
        # Activities
        'driving', 'sitting', 'commuting', 'desk', 'office', 'sleep', 'lifting',
        'running', 'sport', 'injury', 'accident', 'trauma',
    }
    
    found = set()
    for kw in keywords:
        if kw in text:
            found.add(kw)
    return found


def _is_reference_relevant(ref_title: str, ref_abstract: str, article_keywords: set[str]) -> bool:
    """Check if a reference is relevant to the article topic."""
    if not article_keywords:
        return True  # If no keywords extracted, allow all
    
    ref_text = f"{ref_title} {ref_abstract}".lower()
    
    # Check if any article keyword appears in reference
    matches = sum(1 for kw in article_keywords if kw in ref_text)
    
    # Require at least 1 keyword match for relevance
    return matches >= 1


def _snapshot_before_dry_run() -> None:
    global _LISTING_BACKUP, _SITEMAP_BACKUP
    listing = BLOG_DIR / "index.html"
    sitemap = REPO_ROOT / "sitemap.xml"
    _LISTING_BACKUP = listing.read_text(encoding="utf-8") if listing.exists() else None
    _SITEMAP_BACKUP = sitemap.read_text(encoding="utf-8") if sitemap.exists() else None


def _restore_listing(listing: Path) -> None:
    if _LISTING_BACKUP is not None:
        listing.write_text(_LISTING_BACKUP, encoding="utf-8")
    else:  # pragma: no cover
        listing.unlink(missing_ok=True)


def _restore_sitemap(sitemap: Path) -> None:
    if _SITEMAP_BACKUP is not None:
        sitemap.write_text(_SITEMAP_BACKUP, encoding="utf-8")
    else:  # pragma: no cover
        sitemap.unlink(missing_ok=True)


def main() -> int:
    if DRY_RUN:
        _snapshot_before_dry_run()
        try:
            return dry_run()
        finally:
            import shutil

            shutil.rmtree(BLOG_DIR / "dry-run-test-post", ignore_errors=True)
            _restore_listing(BLOG_DIR / "index.html")
            _restore_sitemap(REPO_ROOT / "sitemap.xml")
    if REFRESH_SEO:
        changed = refresh_existing_related_posts()
        refresh_blog_index_schema()
        log(f"SEO refresh complete: related blocks updated on {changed} post(s).")
        return 0
    if SCHEDULED_RUN and not scheduled_publish_due():
        github_output = os.environ.get("GITHUB_OUTPUT", "")
        if github_output:
            with Path(github_output).open("a", encoding="utf-8") as output_file:
                output_file.write("skipped=true\n")
        return 0
    try:
        published_url, post_title = run_pipeline()
        github_output = os.environ.get("GITHUB_OUTPUT", "")
        if github_output:
            clean_title = re.sub(r"[\r\n]+", " ", post_title).strip()
            with Path(github_output).open("a", encoding="utf-8") as output_file:
                output_file.write(f"post_title={clean_title}\n")
                output_file.write(f"published_url={published_url}\n")
        return 0
    except Exception as exc:  # noqa: BLE001
        err = f"{type(exc).__name__}: {exc}"
        log(f"FATAL: {err}")
        import traceback

        traceback.print_exc()
        try:
            send_telegram(
                "❌ <b>Daily blog generation FAILED</b>\n\n"
                f"<pre>{html.escape(err[:800])}</pre>\n"
                "Check the GitHub Actions run logs for details."
            )
        except Exception:  # noqa: BLE001
            pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
