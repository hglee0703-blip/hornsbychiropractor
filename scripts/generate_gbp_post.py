#!/usr/bin/env python3
"""Google Business Profile auto-poster for hornsbychiropractor.com.

Creates a STANDARD local post on the clinic's Google Business Profile via the
My Business API v4, in the same natural, reference-backed voice as the daily
blog posts (generate_blog.py).

Pipeline:
  1. Refresh an OAuth2 access token from a stored refresh token.
  2. Build a LocalPost body. By default the summary is generated from the
     latest published blog article so this script can run unattended every
     day right after the blog cron fires; it can also post fully custom text.
  3. POST the local post to mybusiness.googleapis.com/v4/{parent}/localPosts.
  4. Print a structured JSON result (success/failure, post name, search URL).

Environment variables:
  GOOGLE_CLIENT_ID     Google Cloud OAuth client id (required)
  GOOGLE_CLIENT_SECRET Google Cloud OAuth client secret (required)
  GBP_REFRESH_TOKEN    Long-lived OAuth refresh token for the GBP account (required)
  GBP_LOCATION_ID      Location id, OR a full location resource name
                       e.g. accounts/123/locations/456  (required)
  GBP_ACCOUNT_ID       Merchant account id, only needed when GBP_LOCATION_ID
                       is a bare location id  (optional)
  GBP_POST_TEXT        Summary text for the post. If empty, the script
                       synthesizes one from the latest blog article. (optional)
  GBP_POST_URL         CTA / post URL. If empty, the latest blog URL is used.
                       (optional)
  GBP_MEDIA_URL        Optional image sourceUrl (jpg/png, <=5MB, >=400x300).
                       (optional)
  GBP_CTA_TYPE         Call-to-action actionType: LEARN_MORE | BOOK | ORDER |
                       SHOP | SIGN_UP | CALL. Defaults to LEARN_MORE.
                       (optional)
  GBP_LANGUAGE_CODE    languageCode for the post. Defaults to "en-AU". (optional)

CLI:
  python scripts/generate_gbp_post.py            # create the post
  python scripts/generate_gbp_post.py --dry-run  # build the body, no API call
  python scripts/generate_gbp_post.py --out result.json  # also write JSON file

Only dependency: requests.
"""

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
BLOG_DIR = REPO_ROOT / "blog"
SITE_DOMAIN = "https://hornsbychiropractor.com"

GBP_API_BASE = "https://mybusiness.googleapis.com/v4"
OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
GBP_SCOPE = "https://www.googleapis.com/auth/business.manage"

# Google caps the local-post summary at 1500 characters.
MAX_SUMMARY_LEN = 1500
TIMEOUT_OAUTH = 30
TIMEOUT_GBP = 30

VALID_CTA_TYPES = ("LEARN_MORE", "BOOK", "ORDER", "SHOP", "SIGN_UP", "CALL")
DRY_RUN = "--dry-run" in sys.argv


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def log(msg: str) -> None:
    print(f"[gbp] {msg}", flush=True)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Credential validation
# ---------------------------------------------------------------------------
def validate_env() -> list[str]:
    missing = [
        name for name in (
            "GOOGLE_CLIENT_ID",
            "GOOGLE_CLIENT_SECRET",
            "GBP_REFRESH_TOKEN",
            "GBP_LOCATION_ID",
        ) if not env(name)
    ]
    return missing


# ---------------------------------------------------------------------------
# Location resolution
# ---------------------------------------------------------------------------
def resolve_location_parent() -> str:
    """Resolve the full location resource name used as {parent} in the API path.

    GBP_LOCATION_ID may be either:
      * a full resource name  accounts/{accountId}/locations/{locationId}, or
      * a bare location id, in which case GBP_ACCOUNT_ID must also be set.

    This keeps the required secret surface small (GBP_LOCATION_ID) while still
    matching the documented endpoint shape accounts/{account_id}/locations/{location_id}/localPosts.
    """
    location = env("GBP_LOCATION_ID")
    account = env("GBP_ACCOUNT_ID")
    full_match = re.fullmatch(r"accounts/[\w-]+/locations/[\w-]+", location)
    if full_match:
        if account and not location.startswith(f"accounts/{account}/"):
            log("GBP_LOCATION_ID is a full resource name; GBP_ACCOUNT_ID "
                "is ignored for the API path.")
        return location
    if not account:
        raise ValueError(
            "GBP_LOCATION_ID is a bare id, so GBP_ACCOUNT_ID is also required "
            "(expected: accounts/{GBP_ACCOUNT_ID}/locations/{GBP_LOCATION_ID})."
        )
    return f"accounts/{account}/locations/{location}"


# ---------------------------------------------------------------------------
# OAuth2 token refresh
# ---------------------------------------------------------------------------
def refresh_access_token() -> str:
    """Exchange the stored refresh token for a short-lived access token."""
    client_id = env("GOOGLE_CLIENT_ID")
    client_secret = env("GOOGLE_CLIENT_SECRET")
    refresh_token = env("GBP_REFRESH_TOKEN")
    log("Refreshing OAuth2 access token...")
    resp = requests.post(
        OAUTH_TOKEN_URL,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
            "scope": GBP_SCOPE,
        },
        timeout=TIMEOUT_OAUTH,
    )
    if resp.status_code != 200:
        detail = _safe_json(resp).get("error_description") or resp.text[:300]
        err = _safe_json(resp).get("error", "unknown")
        raise GbpApiError(
            f"OAuth token refresh failed (HTTP {resp.status_code}): "
            f"{err}: {detail}"
        )
    token = resp.json().get("access_token")
    if not token:
        raise GbpApiError("OAuth token refresh returned no access_token.")
    log("Access token obtained.")
    return token


# ---------------------------------------------------------------------------
# Latest-blog synthesis (default post text)
# ---------------------------------------------------------------------------
def discover_latest_blog() -> dict | None:
    """Find the newest blog post from blog/index.html and read its metadata."""
    listing = BLOG_DIR / "index.html"
    if not listing.exists():
        return None
    html_text = listing.read_text(encoding="utf-8")
    # blog/index.html lists cards newest-first; grab the first .blog-card href.
    card_match = re.search(
        r'class="blog-card"\s+href="([^"]+)"', html_text
    )
    if not card_match:
        return None
    slug_href = card_match.group(1)  # e.g. /blog/best-sleeping-positions.../
    post_path = (REPO_ROOT / slug_href.strip("/")).with_suffix("")  # dir
    index_file = BLOG_DIR / slug_href.strip("/").removeprefix("blog/") / "index.html"
    if not index_file.exists():
        index_file = REPO_ROOT / slug_href.strip("/") / "index.html"
    if not index_file.exists():
        return None
    post_html = index_file.read_text(encoding="utf-8")

    def meta(name):
        m = re.search(
            rf'<meta[^>]+name="{re.escape(name)}"\s+content="([^"]*)"',
            post_html, re.IGNORECASE,
        )
        if not m:
            m = re.search(
                rf'<meta[^>]+property="{re.escape(name)}"\s+content="([^"]*)"',
                post_html, re.IGNORECASE,
            )
        return m.group(1) if m else ""

    canonical = (re.search(
        r'<link rel="canonical" href="([^"]+)"', post_html, re.IGNORECASE
    ) or [None, {"href": ""}])
    canonical_url = canonical.group(1) if canonical else ""

    title = meta("og:title") or meta("description")[:60]
    description = meta("og:description") or meta("description")
    return {
        "slug": slug_href.strip("/").removeprefix("blog/").strip("/"),
        "href": slug_href,
        "url": canonical_url or urljoin(SITE_DOMAIN + "/", slug_href),
        "title": title,
        "description": description,
    }


def generate_summary_from_blog() -> tuple[str, str]:
    """Synthesize a natural, clinic-voiced GBP summary from the latest blog.

    Returns (summary_text, canonical_url). Mirrors the medical-facts-backed,
    human-written tone of generate_blog.py so the GBP post reads like Andy Lee
    wrote it (not an AI).
    """
    blog = discover_latest_blog()
    if not blog:
        raise GbpError(
            "Could not locate a latest blog post to generate a default GBP "
            "summary. Set GBP_POST_TEXT, or run from a checkout that has a "
            "blog/index.html with posts."
        )
    url = blog["url"]
    description = blog["description"] or ""
    title = blog["title"] or "our latest article"

    clinical_takeaways = {
        "back": "Most morning back stiffness in clinic settles down once "
                "sleeping position stops twisting the lumbar spine overnight.",
        "neck": "Neck pain often gets worse after a night on a too-flat "
                "pillow; the right support makes the first hour upright "
                "much easier.",
        "sciatica": "Sciatic pain that flares at night usually calms once the "
                "nerve is no longer being stretched by your sleep posture.",
        "posture": "Poor sleeping posture stacks on the poor desk posture you "
                "carry into the evening — fix the pillow first, then the chair.",
        "headache": "Many of the tension headaches I see start at the neck "
                "and ride up; correct pillow height is a cheap fix with a "
                "big payoff.",
    }
    body = description.lower()
    takeaway = next(
        (v for k, v in clinical_takeaways.items() if k in body),
        "Small posture tweaks in daily habits add up to real, lasting relief "
        "in clinic.",
    )

    summary = (
        f"{takeaway} Our latest piece — “{title}” — covers exactly what "
        f"patients ask me about at the Hornsby clinic. {description} "
        f"Read the full guide with references here: {url}"
    )
    # Collapse whitespace, then trim to Google's 1500-char ceiling at a word
    # boundary (same discipline as the blog's clamp_meta helper).
    summary = re.sub(r"\s+", " ", summary).strip()
    if len(summary) > MAX_SUMMARY_LEN:
        cut = summary[:MAX_SUMMARY_LEN]
        if " " in cut:
            cut = cut.rsplit(" ", 1)[0]
        summary = cut.rstrip(".,;:") + "…"
    return summary, url


# ---------------------------------------------------------------------------
# LocalPost body builder
# ---------------------------------------------------------------------------
def build_post_body(
    summary: str,
    cta_url: str,
    cta_type: str,
    media_url: str,
    language_code: str,
) -> dict:
    """Assemble the LocalPost JSON body (matches the v4 API schema exactly)."""
    if not summary:
        raise GbpError("Summary cannot be empty.")
    if len(summary) > MAX_SUMMARY_LEN:
        cut = summary[:MAX_SUMMARY_LEN]
        cut = cut.rsplit(" ", 1)[0] if " " in cut else cut
        summary = cut.rstrip(".,;:") + "…"

    body = {
        "languageCode": language_code,
        "summary": summary,
        "topicType": "STANDARD",
    }
    if cta_type and cta_type != "CALL":
        if not cta_url:
            raise GbpError(
                "A call-to-action URL is required when using a CTA type other "
                "than CALL (set GBP_POST_URL or pass --post-url)."
            )
        body["callToAction"] = {"actionType": cta_type, "url": cta_url}
    elif cta_type == "CALL" and not cta_url:
        # CALL has no url field per the API spec — drop the url entirely.
        body["callToAction"] = {"actionType": "CALL"}
    if media_url:
        body["media"] = [
            {"mediaFormat": "PHOTO", "sourceUrl": media_url}
        ]
    return body


# ---------------------------------------------------------------------------
# GBP API call
# ---------------------------------------------------------------------------
def create_local_post(access_token: str, parent: str, body: dict) -> dict:
    """POST a new local post. Returns the created LocalPost resource."""
    url = f"{GBP_API_BASE}/{parent}/localPosts"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    log(f"Creating local post for {parent}...")
    resp = requests.post(url, headers=headers, json=body, timeout=TIMEOUT_GBP)
    log(f"GBP responded with HTTP {resp.status_code}")
    if resp.status_code != 200:
        raise GbpApiError(
            f"Create local post failed (HTTP {resp.status_code}): "
            f"{_describe_error(resp)}"
        )
    data = resp.json()
    # The API returns the persisted LocalPost including output-only fields.
    if not data.get("name") and not data.get("searchUrl"):
        raise GbpApiError(
            f"Create returned HTTP 200 but has no post name/searchUrl: "
            f"{json.dumps(data)[:300]}"
        )
    return data


# ---------------------------------------------------------------------------
# Errors + output shaping
# ---------------------------------------------------------------------------
class GbpError(Exception):
    """A configuration / input error (not an API transport error)."""


class GbpApiError(Exception):
    """An error returned by Google's OAuth or My Business endpoints."""


def _safe_json(resp: requests.Response) -> dict:
    try:
        return resp.json()
    except ValueError:
        return {}


def _describe_error(resp: requests.Response) -> str:
    data = _safe_json(resp)
    if data:
        msg = data.get("error") or data.get("message") or resp.text[:300]
        if isinstance(msg, dict):
            msg = msg.get("message", json.dumps(msg))
        return str(msg)
    return resp.text[:300]


def build_result(
    success: bool,
    *,
    post_name=None,
    search_url=None,
    url=None,
    summary=None,
    cta=None,
    media_url=None,
    language_code=None,
    message=None,
    error=None,
) -> dict:
    result = {
        "success": success,
        "timestamp": now_iso(),
        "post_name": post_name,
        "search_url": search_url,
        "url": url,
        "language_code": language_code,
        "topic_type": "STANDARD",
        "summary": summary,
        "call_to_action": cta,
        "media_url": media_url,
        "message": message,
        "error": error,
    }
    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def run() -> dict:
    missing = validate_env()
    if missing:
        raise GbpError(
            "Missing required environment variables: " + ", ".join(missing)
        )

    parent = resolve_location_parent()
    language_code = env("GBP_LANGUAGE_CODE") or "en-AU"
    cta_type = (env("GBP_CTA_TYPE") or "LEARN_MORE").upper()
    if cta_type not in VALID_CTA_TYPES:
        raise GbpError(
            f"Invalid GBP_CTA_TYPE '{cta_type}'. Valid: "
            f"{', '.join(VALID_CTA_TYPES)}."
        )
    media_url = env("GBP_MEDIA_URL")

    # --- summary text: explicit, or synthesize from latest blog ---
    post_text = env("GBP_POST_TEXT")
    if post_text:
        summary, url = post_text, env("GBP_POST_URL")
    else:
        summary, blog_url = generate_summary_from_blog()
        url = env("GBP_POST_URL") or blog_url
    if not url:
        url = SITE_DOMAIN

    body = build_post_body(summary, url, cta_type, media_url, language_code)
    log(f"Summary ({len(summary)} chars): {summary[:80]}...")

    if DRY_RUN:
        log("DRY RUN — no API call made. Constructed body:")
        print(json.dumps(body, indent=2))
        return build_result(
            True,
            url=url,
            summary=summary,
            cta=body.get("callToAction"),
            media_url=media_url,
            language_code=language_code,
            message="Dry run: body constructed, no post created.",
        )

    access_token = refresh_access_token()
    post = create_local_post(access_token, parent, body)
    post_name = post.get("name")
    search_url = post.get("searchUrl")
    return build_result(
        True,
        post_name=post_name,
        search_url=search_url,
        url=url,
        summary=summary,
        cta=post.get("callToAction"),
        media_url=media_url,
        language_code=language_code,
        message="Local post created. It is pending review and may take a few "
                "minutes to appear on Google Search & Maps.",
    )


def main() -> int:
    out_path = None
    args = [a for a in sys.argv[1:] if not a.startswith("--dry-run")]
    # parse --out PATH
    if "--out" in args:
        i = args.index("--out")
        if i + 1 < len(args):
            out_path = args[i + 1]

    try:
        result = run()
        status = "SUCCESS" if result["success"] else "DONE"
    except (GbpError, GbpApiError) as exc:
        result = build_result(
            False,
            message=str(exc),
            error=type(exc).__name__,
            url=env("GBP_POST_URL") or "",
            language_code=env("GBP_LANGUAGE_CODE") or "en-AU",
        )
        status = "FAILED"
    except Exception as exc:  # noqa: BLE001 — last-resort safety net
        result = build_result(
            False,
            message=f"Unexpected error: {type(exc).__name__}: {exc}",
            error="UnexpectedError",
            url=env("GBP_POST_URL") or "",
            language_code=env("GBP_LANGUAGE_CODE") or "en-AU",
        )
        status = "FAILED"

    # Always emit machine-readable JSON to stdout (captured by the CI workflow).
    print(json.dumps(result, ensure_ascii=False, indent=2))

    if out_path:
        Path(out_path).write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    log(f"{status}: "
        f"{result.get('message', '')[:120]}")
    return 0 if result["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
