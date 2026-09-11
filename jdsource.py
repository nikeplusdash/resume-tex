"""Resolve a job description from a local path or a URL.

A posting starts life as a URL, but the boards worth applying to differ wildly:
Greenhouse, Lever and Ashby serve static HTML, while LinkedIn and Workday render
in JS behind a login. Rather than guess, this module fetches what it can, checks
the result actually looks like a posting, and asks for a paste when it does not.
Handing a login wall to the generator would produce a confidently wrong resume,
which is worse than one more prompt.
"""
from __future__ import annotations

import html as _html
import json as _json
import re
import warnings
from pathlib import Path

# System Python here is linked against LibreSSL, not OpenSSL 1.1.1+; urllib3 v2
# warns about it on every import. It's an environment fact, not something a
# resume-generation run can act on, so it's noise at startup rather than signal.
warnings.filterwarnings("ignore", message=r".*urllib3 v2 only supports OpenSSL.*")

import requests
from bs4 import BeautifulSoup

import ui

MIN_CHARS = 300
_JD_KEYWORDS = re.compile(
    r"responsibilit|qualificat|requirement|you.ll|experience"
    r"|about (the|this) role|what you.ll|role overview|join (our|the) team", re.I)
_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/122.0 Safari/537.36")
# A browser sends more than a UA string; some boards (Workday, a few Greenhouse
# tenants) 403 a request that has only the UA. curl/8 is the second-try identity:
# boards that block "a headless-looking browser" often wave through an obvious
# script, and vice versa, so trying both covers most anti-bot rules.
_HEADERS = {
    "User-Agent": _UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
_FALLBACK_HEADERS = {"User-Agent": "curl/8.4.0", "Accept": "*/*"}
_STRIP_TAGS = ("script", "style", "nav", "header", "footer", "aside", "form")
TIMEOUT = 15.0


class JDError(Exception):
    """No usable job description could be obtained."""


def is_url(arg: str) -> bool:
    return str(arg).strip().lower().startswith(("http://", "https://"))


def looks_like_jd(text: str) -> str:
    """"" if the text passes as a posting, else the reason it failed."""
    text = (text or "").strip()
    if len(text) < MIN_CHARS:
        return f"too short ({len(text)} chars, need {MIN_CHARS})"
    # An SPA careers page (Eightfold, Phenom, some Workday tenants) serves its
    # whole app state as inline JSON/CSS. It's long and the keyword regex hits a
    # stray "experience" inside it, so without this it would sail through as the
    # "JD". Prose never starts with a brace/bracket, and never has one brace per
    # ~150 chars.
    if text[:1] in "{[":
        return "looks like page data (JSON/CSS), not posting text"
    if (text.count("{") + text.count("}")) / len(text) > 0.006:
        return "looks like embedded page data (JSON/CSS), not posting text"
    if not _JD_KEYWORDS.search(text):
        return "no job-description keywords (responsibilities, qualifications, requirements)"
    return ""


def _structured_jd(soup) -> str:
    """The posting text from a JSON-LD JobPosting block, or "".

    Greenhouse, Lever, Ashby and Workday all embed a
    <script type="application/ld+json"> JobPosting whose `description` is the full
    posting as escaped HTML -- present even when the visible <body> is a
    "you need to enable JavaScript" stub. This is the real JD, so it's trusted on
    length alone, like the meta-description fallback."""
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = _json.loads(tag.string or tag.get_text() or "")
        except Exception:
            continue
        nodes = data if isinstance(data, list) else [data]
        if isinstance(data, dict) and isinstance(data.get("@graph"), list):
            nodes = data["@graph"]
        for node in nodes:
            if not isinstance(node, dict):
                continue
            if "JobPosting" in str(node.get("@type", "")):
                desc = node.get("description") or ""
                if desc:
                    inner = BeautifulSoup(_html.unescape(desc), "html.parser")
                    lines = [ln.strip() for ln in inner.get_text("\n").splitlines()]
                    return "\n".join(ln for ln in lines if ln)
    return ""


def html_to_text(html: str) -> tuple:
    """(text, from_structured_fallback) -- the second value tells the caller this
    text already cleared its own bar and shouldn't be re-judged by the stricter,
    generic-page check meant for arbitrary scraped body text."""
    soup = BeautifulSoup(html, "html.parser")
    structured = _structured_jd(soup)      # before the strip loop removes <script>
    for tag in soup(list(_STRIP_TAGS)):
        tag.decompose()
    body = soup.find("main") or soup.find("article") or soup.body or soup
    text = body.get_text("\n")
    lines = [ln.strip() for ln in text.splitlines()]
    text = "\n".join(ln for ln in lines if ln)

    # JS-rendered boards (Ashby, Workday, some Greenhouse tenants) leave the body
    # empty behind an "enable JavaScript" placeholder but still expose the posting
    # in JSON-LD or the SEO meta description -- fall back to those before giving
    # up. Both are held to a length check only, not the full looks_like_jd()
    # keyword gate: they're written specifically for this one posting (not
    # arbitrary scraped page text), so they often never say "requirements" or
    # "responsibilities" even when they're clearly the real JD.
    if looks_like_jd(text):
        if len(structured.strip()) >= MIN_CHARS:
            return structured.strip(), True
        meta = (soup.find("meta", attrs={"name": "description"})
                or soup.find("meta", attrs={"property": "og:description"}))
        meta_text = (meta.get("content") or "").strip() if meta else ""
        if len(meta_text) >= MIN_CHARS:
            return meta_text, True
    return text, False


def fetch(url: str) -> tuple:
    with ui.spinner(f"Fetching {url}"):
        resp = requests.get(url, headers=_HEADERS, timeout=TIMEOUT, allow_redirects=True)
        # Anti-bot rules that reject the browser identity often pass a plain
        # script, so retry once the other way before surfacing the failure.
        if getattr(resp, "status_code", 200) in (403, 429):
            resp = requests.get(url, headers=_FALLBACK_HEADERS, timeout=TIMEOUT,
                                allow_redirects=True)
        resp.raise_for_status()
    return html_to_text(resp.text)


def _paste(reason: str) -> str:
    print(f"  ! {reason}")
    return (ui.editor("Paste the job description text instead (Enter to skip):") or "").strip()


def load_jd(arg: str) -> tuple:
    """(text, label) for a local path or a URL. Raises JDError when nothing usable."""
    arg = str(arg).strip()
    if not is_url(arg):
        path = Path(arg).expanduser()
        if not path.exists():
            raise JDError(f"file not found: {path}")
        return path.read_text(), path.name

    try:
        text, from_meta = fetch(arg)
    except Exception as e:                       # any transport or parse failure
        text = ""
        reason = f"could not fetch ({str(e)[:120]})"
    else:
        reason = "" if from_meta else looks_like_jd(text)

    if reason:
        text = _paste(reason)
        if looks_like_jd(text):
            raise JDError(f"no usable job description for {arg}")
    label = re.sub(r"^https?://(www\.)?", "", arg)[:60]
    return text, label


def read_url_list(path) -> list:
    """The lines of a file, if every non-blank one is a URL. Else []."""
    try:
        lines = [ln.strip() for ln in Path(path).read_text().splitlines()]
    except OSError:
        return []
    lines = [ln for ln in lines if ln]
    if lines and all(is_url(ln) for ln in lines):
        return lines
    return []
