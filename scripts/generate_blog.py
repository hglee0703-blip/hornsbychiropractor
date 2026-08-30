#!/usr/bin/env python3
"""Daily auto SEO blog poster for hornsbychiropractor.com.

Pipeline (all free tools):
  1. Pick a topic (workflow input, or Gemini suggests a fresh one that does not
     duplicate existing blog/ posts).
  2. Generate a natural-sounding, reference-backed article with Gemini.
  3. Illustrate the post with hand-coded cute flat-cartoon SVG scenes
     (scripts/svg_illustrations.py) chosen by keyword-matching the article
     text — no external image API involved.
  4. Write blog/{slug}/index.html reusing the existing site chrome, prepend a
     card to blog/index.html, update/create sitemap.xml.
  5. Notify via Telegram (success or failure report).

Only dependency: requests (Gemini + Telegram only).

Usage:
  python scripts/generate_blog.py            # full pipeline (needs GEMINI_API_KEY)
  python scripts/generate_blog.py --dry-run  # no network; tests template assembly
"""

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

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
BLOG_DIR = REPO_ROOT / "blog"
ASSETS_IMG_DIR = REPO_ROOT / "assets" / "blog-images"
SITE_DOMAIN = "https://hornsbychiropractor.com"

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
TOPIC_INPUT = os.environ.get("TOPIC", "").strip()
FORCE = os.environ.get("FORCE", "true").strip().lower() not in ("0", "false", "no")

DRY_RUN = "--dry-run" in sys.argv

SYDNEY_TZ = ZoneInfo("Australia/Sydney")

GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
)

TIMEOUT_GEMINI = 120

# Existing posts used for internal linking + topic dedupe.
EXISTING_POSTS = {
    "lumbar-disc-injury-management": "Lumbar Disc Injury Management",
    "disc-protrusion-herniated-disc-sciatica-cortisone-injection-surgery": (
        "In-Depth Research of Disc protrusion, Herniated Disc, Sciatica, "
        "Cortisone Injection and Disc Surgeries"
    ),
}

INTERNAL_LINK_HINTS = (
    "- /blog/lumbar-disc-injury-management/ : practical lumbar disc management "
    "(McKenzie exercise, driving, walking, sitting posture, medication)\n"
    "- /blog/disc-protrusion-herniated-disc-sciatica-cortisone-injection-surgery/ : "
    "in-depth research on disc protrusion, sciatica, cortisone injections and surgery\n"
    "If genuinely relevant, include 1-2 internal links to these URLs inside the body."
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


def word_count(html_text: str) -> int:
    return len(strip_html(html_text).split())


def extract_existing_topics() -> list[str]:
    """Slugs of published posts under blog/."""
    topics = []
    if BLOG_DIR.exists():
        for child in sorted(BLOG_DIR.iterdir()):
            if child.is_dir():
                topics.append(child.name)
    return topics


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
# Gemini calls
# ---------------------------------------------------------------------------

TOPIC_SYSTEM_PROMPT = """You are the content strategist for Hornsby Chiropractor, \
a chiropractic clinic in Hornsby, NSW, Australia (author: Andy Lee).

Suggest ONE new blog post topic for local patients. Requirements:
- A question-style or how-to style topic that a real person around Hornsby would \
type into Google (e.g. "how to sleep with lower back pain", "best desk setup for neck pain").
- Relevant to chiropractic care: back pain, neck pain, posture, headaches, sciatica, \
sports injuries, ergonomics, sleep and pain, exercise and recovery, etc.
- It must NOT be substantially similar to any of the already-published topics listed below.
- Answer ONLY with the topic text itself. No quotes, no numbering, no explanation."""

ARTICLE_PROMPT_TEMPLATE = """You are Andy Lee, a chiropractor running a clinic in Hornsby, Sydney, Australia. \
Write a new blog post in ENGLISH for Australian readers for the site hornsbychiropractor.com.

TOPIC: {topic}
Today's date: {today}.

WRITING STYLE — CRITICAL, this must read like a real clinician wrote it, not an AI:
- Vary sentence length. Use occasional very short sentences ("It works." / "Most people get this wrong.").
- First-person clinical voice where appropriate: "In my clinic...", "Most patients tell me...", \
"I usually suggest...".
- Natural transitions, sometimes none at all. Do NOT start consecutive paragraphs the same way.
- BANNED AI-isms: do not use "Moreover", "Furthermore", "In addition", "In conclusion", \
"It's important to note", "delve", "landscape", "tapestry", "game-changer", "navigate the world of". \
Do not use em-dash pairs constantly. Do not write perfectly balanced triads everywhere.
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
- Include a short disclaimer paragraph stating this is general information only, not a diagnosis \
or treatment advice, and readers should consult a qualified health professional.

STRUCTURE:
- Total length: 900-1400 words (count only visible text).
- 4-6 <h2> section headings, each followed by 1-3 paragraphs.
- An FAQ section at the end with 3-4 questions as <h3> headings, each answered in 2-4 sentences. \
FAQ answers may cite sources too.
- {internal_links}

OUTPUT FORMAT — return ONLY a valid JSON object, no markdown fences, matching exactly:
{{
  "slug": "short-kebab-case-url-slug",
  "title": "SEO title, max 60 characters",
  "meta_description": "meta description, 150-160 characters",
  "category": "short category label like 'Lower back pain' or 'Neck & posture'",
  "intro_summary": "1-2 sentence summary used on the blog listing card, max 200 characters",
  "html_body": "<p>...</p><h2>...</h2>... full article HTML including FAQ section. \
Use only p, h2, h3, strong, em, ul, li, a tags. No h1 (the template adds it), no images.",
  "faq": [
    {{"question": "...", "answer": "..."}}
  ]
}}
The faq array must mirror the FAQ H3s in html_body."""


class GeminiError(Exception):
    pass


def gemini_generate(prompt: str, max_output_tokens: int = 8192) -> str:
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 1.0,
            "maxOutputTokens": max_output_tokens,
            "responseMimeType": "application/json" if '"slug"' in prompt else "text/plain",
        },
    }
    
    # Exponential backoff for transient errors (503, 429, 500, 502, 504)
    max_attempts = 5
    base_delay = 2  # seconds
    for attempt in range(1, max_attempts + 1):
        resp = requests.post(GEMINI_URL, json=payload, timeout=TIMEOUT_GEMINI)
        if resp.status_code == 200:
            data = resp.json()
            try:
                return data["candidates"][0]["content"]["parts"][0]["text"]
            except (KeyError, IndexError) as exc:
                raise GeminiError(f"Gemini returned no usable candidate: {json.dumps(data)[:400]}") from exc
        
        # Check if it's a retryable error
        if resp.status_code in (429, 500, 502, 503, 504):
            if attempt < max_attempts:
                delay = base_delay * (2 ** (attempt - 1))  # 2, 4, 8, 16 seconds
                log(f"Gemini HTTP {resp.status_code} (attempt {attempt}/{max_attempts}), retrying in {delay}s...")
                import time
                time.sleep(delay)
                continue
        
        # Non-retryable error or max attempts reached
        raise GeminiError(f"Gemini HTTP {resp.status_code}: {resp.text[:400]}")


def parse_article_json(raw: str) -> dict:
    text = raw.strip()
    # Strip markdown fences if present despite instructions.
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("No JSON object found in response")
    obj = json.loads(text[start : end + 1])
    required = ["slug", "title", "meta_description", "category", "intro_summary", "html_body"]
    missing = [k for k in required if not str(obj.get(k, "")).strip()]
    if missing:
        raise ValueError(f"Article JSON missing keys: {missing}")
    obj.setdefault("faq", [])
    if not isinstance(obj["faq"], list):
        obj["faq"] = []
    # image_prompts is no longer requested; ignore it if Gemini still returns one.
    obj.pop("image_prompts", None)
    return obj


def pick_topic(existing_slugs: list[str]) -> tuple[str, bool]:
    """Returns (topic, from_ai)."""
    if TOPIC_INPUT:
        return TOPIC_INPUT, False
    existing_list = "\n".join(f"- {s}" for s in existing_slugs) or "- (none yet)"
    prompt = TOPIC_SYSTEM_PROMPT + "\n\nAlready published topics:\n" + existing_list
    raw = gemini_generate(prompt, max_output_tokens=256).strip().strip('"').strip()
    if len(raw.split()) > 15:  # sanity check
        raw = " ".join(raw.split()[:12])
    if not raw:
        raise GeminiError("Gemini returned empty topic suggestion")
    return raw, True


def generate_article(topic: str) -> dict:
    internal_links = INTERNAL_LINK_HINTS if EXISTING_POSTS else ""
    prompt = ARTICLE_PROMPT_TEMPLATE.format(
        topic=topic,
        today=sydney_today(),
        internal_links=internal_links,
    )
    last_err = None
    for attempt in range(1, 4):
        try:
            raw = gemini_generate(prompt)
            return parse_article_json(raw)
        except (ValueError, KeyError, json.JSONDecodeError, GeminiError) as exc:
            last_err = exc
            log(f"Article generation attempt {attempt}/3 failed: {exc}")
    raise GeminiError(f"Gemini article generation failed after 3 attempts: {last_err}")


# ---------------------------------------------------------------------------
# Images (real paper-based: PubMed/PMC figures + academic citation cards)
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

    for n, ref_url in enumerate(ref_urls, start=1):
        ids = _resolve_ids(ref_url)
        pmid = ids.get("pmid", "")
        meta = _fetch_metadata(pmid)
        title = meta.get("title") or "PubMed publication"
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
    """Build the <figure class="post-figure"> block for one reference image."""
    caption = (
        f'Source: {img["citation"]}. '
        f'<a href="{img["ref_url"]}">{html.escape(img["license"])}</a>'
    )
    return (
        '<figure class="post-figure">\n'
        f'          <img src="{img["public_path"]}" '
        f'alt="{html.escape(img["alt"])}" loading="lazy">\n'
        f'          <figcaption>Source: {html.escape(img["citation"])}. '
        f'<a href="{img["ref_url"]}">{html.escape(img["license"])}</a>'
        "</figcaption>\n"
        "        </figure>"
    )


def insert_images_into_body(body: str, images: list[dict], title: str) -> str:
    """Insert reference images near the front and middle of the article."""
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


# ---------------------------------------------------------------------------
# Page assembly
# ---------------------------------------------------------------------------

PAGE_TEMPLATE = """<!doctype html>
<html lang="en">
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
    <meta name="twitter:card" content="summary_large_image">
    <script type="application/ld+json">
{blogposting_jsonld}
    </script>
    <script type="application/ld+json">
{faq_jsonld}
    </script>
    <link rel="stylesheet" href="/styles.css">
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
        <p class="post-meta"><time datetime="{date_iso}">{date_human}</time> · {category}</p>
{body_with_images}
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
    base_max = max_len - len(suffix)
    if len(plain) > base_max:
        cut = plain[:base_max]
        plain = (cut.rsplit(" ", 1)[0] if " " in cut else cut).rstrip()
    return plain + suffix


def build_blogposting_jsonld(article: dict, canonical: str, date_pub: str, og_image: str) -> str:
    data = {
        "@context": "https://schema.org",
        "@type": "BlogPosting",
        "headline": article["title"],
        "description": article["meta_description"],
        "author": {"@type": "Person", "name": "Andy Lee"},
        "publisher": {
            "@type": "Organization",
            "name": "Hornsby Chiropractor",
            "url": SITE_DOMAIN,
        },
        "datePublished": date_pub,
        "dateModified": date_pub,
        "url": canonical,
        "mainEntityOfPage": {"@type": "WebPage", "@id": canonical},
        "image": [og_image] if og_image else [],
        "keywords": article.get("category", ""),
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
    new_text = text[:tag_end] + card + text[tag_end:]
    listing.write_text(new_text, encoding="utf-8")
    return listing


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

    log(f"Generating article with {GEMINI_MODEL}...")
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

    log("Building paper-based reference images (PubMed/PMC)...")
    images, img_notes = build_reference_images(article)
    for note in img_notes:
        log(note)
    ok_count = sum(1 for i in images if i["ok"])
    fig_count = sum(1 for i in images if i["ok"] and i.get("kind") == "figure")
    card_count = sum(1 for i in images if i["ok"] and i.get("kind") == "card")
    log(f"Images: {fig_count} PMC figures + {card_count} paper cards.")

    chrome = extract_chrome(BLOG_DIR / "lumbar-disc-injury-management" / "index.html")
    post_path = write_post_page(article, images, chrome)
    listing_path = prepend_blog_card(article)
    sitemap_path = update_sitemap(slug)

    log(f"Wrote {post_path}")
    log(f"Updated {listing_path}")
    log(f"Updated {sitemap_path}")

    url = f"{SITE_DOMAIN}/blog/{slug}/"
    send_telegram(
        "✅ <b>New blog post published</b>\n\n"
        f"<b>{html.escape(article['title'])}</b>\n"
        f"🔗 {url}\n"
        f"🗂 Category: {html.escape(article['category'])}\n"
        f"📝 ~{wc} words · 🖼 {ok_count}/{len(images)} images\n"
        f"🤖 model: {GEMINI_MODEL}"
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
        "title": "Dry Run Test Post Title",
        "meta_description": "Sample meta description used only to verify template assembly in dry-run mode.",
        "category": "Testing",
        "intro_summary": "This is a dry-run summary used to verify the blog listing card insertion logic.",
        "html_body": (
            "<p>This is the intro paragraph of the dry run test article. It exists purely "
            "to check that images get inserted after the correct anchor elements.</p>"
            "<p>A second paragraph that acts as the primary image anchor point.</p>"
            "<h2>First Section Heading</h2><p>Section one body text with a "
            '<a href="https://www.healthdirect.gov.au/">reference link</a>.</p>'
            "<h2>Second Section Heading</h2><p>Section two body text.</p>"
            "<h3>What is a dry run?</h3><p>An answer explaining dry runs.</p>"
        ),
        "faq": [{"question": "What is a dry run?", "answer": "A test without side effects."}],
    }
    images = [
        {"ok": True, "kind": "figure",
         "public_path": "/assets/blog-images/dry-run-test-post-ref1.jpg",
         "alt": "Sample figure caption from a PMC open access article",
         "citation": 'Desouzart G et al. (2016). "Effects of sleeping position '
                     'on back pain in physically active seniors". Work',
         "license": "CC BY 4.0",
         "ref_url": "https://pubmed.ncbi.nlm.nih.gov/26835867/", "n": 1},
        {"ok": True, "kind": "card",
         "public_path": "/assets/blog-images/dry-run-test-post-paper-card-1.svg",
         "alt": 'Paper: "Effects of sleeping position on back pain" '
                '(Desouzart G et al., 2016)',
         "citation": 'Desouzart G et al. (2016). "Effects of sleeping position '
                     'on back pain in physically active seniors". Work',
         "license": "Open Access",
         "ref_url": "https://pubmed.ncbi.nlm.nih.gov/26835867/", "n": 1},
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
                "site header copied": 'class="site-header"' in text,
                "mobile menu copied": 'class="mobile-menu"' in text,
                "footer present": "<footer>" in text,
                "post-figure structure": '<figure class="post-figure">' in text,
                "figcaption present": "<figcaption>" in text,
                "source caption present": "Source: " in text,
                "reference image inserted": "/assets/blog-images/dry-run-test-post-" in text,
                "disclaimer": "general information only" in text,
            }
            for name, passed in checks.items():
                print(f"  [{'OK' if passed else 'FAIL'}] {name}")
            if not all(checks.values()):
                print("DRY RUN FAILED: some template checks did not pass")
                return 1
        elif path.name == "index.html" and path.parent == BLOG_DIR:
            ok = 'href="/blog/dry-run-test-post/"' in text
            print(f"  [{'OK' if ok else 'FAIL'}] new blog card prepended to listing")
            if not ok:
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
    log("DRY RUN PASSED — artifacts cleaned up.")
    return 0


_LISTING_BACKUP = None
_SITEMAP_BACKUP = None


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
            pass
    try:
        run_pipeline()
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