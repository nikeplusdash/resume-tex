"""Pre-fill a gap answer with what the user has already written — and nothing else.

A gap answer is distilled into content.json notes, where every later application
reuses it. That makes this the easiest place in the project to acquire a claim
nobody can defend: a fluent draft is accepted by reflex in a way a blank field
never is. So the draft is assembled from one entry's own text, every span it
claims to use is checked as a substring in Python, and anything unverifiable
returns nothing at all rather than something plausible.
"""
from __future__ import annotations

import re
from typing import List

from pydantic import BaseModel

import llm
import ui
from profile import NOTE_TARGET_SEP

# A fabricated metric is the highest-risk failure mode a draft can introduce, and the
# cheapest one to catch mechanically: it is the specific claim the applicant would have
# to defend in an interview if it slipped through. The substring check on `used` spans
# already guards prose, but a model can still paste a number into `draft` that never
# appeared in any cited span (or in the source at all) while wording the rest
# faithfully -- so every number-like token in the draft is checked against the source
# directly, on top of the span check below.
_NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?%?")

DRAFT_SYSTEM = (
    "Answer the question using ONLY the source text supplied. Every clause of `draft` "
    "must be supported by the source. Copy into `used` the exact spans of source text "
    "you relied on, verbatim, character for character. If the source does not answer "
    "the question, return an empty draft and an empty `used`. Never add facts, "
    "numbers, or context that is not in the source. Return the schema only."
)


class DraftAnswer(BaseModel):
    draft: str = ""
    used: List[str] = []


def source_text(profile, target: str) -> str:
    """Bullets and notes of the one entry `target` names. "" for general/unknown.

    `target` may be a plain "kind:name" or, when `name` collides across entries
    (a promotion), a disambiguated "kind:name::<index>" (see profile.NOTE_TARGET_SEP)
    -- an indexed target must read that specific entry's text, not whichever one
    with that name comes first, or a draft for the senior role would be built from
    the junior one's bullets.
    """
    target = (target or "").strip()
    if ":" not in target:
        return ""
    kind, _, rest = target.partition(":")
    if kind == "experience":
        key, match = "experience", "company"
    elif kind == "education":
        key, match = "education", "institution"
    else:
        key, match = "projects", "title"
    label, idx = rest, None
    if NOTE_TARGET_SEP in rest:
        label, _, idx_s = rest.partition(NOTE_TARGET_SEP)
        idx = int(idx_s) if idx_s.isdigit() else None
    matches = [e for e in (profile.content.get(key) or []) if (e.get(match) or "") == label]
    entry = None
    if idx is not None and 0 <= idx < len(matches):
        entry = matches[idx]
    elif matches:
        # The documented fallback for a plain, colliding target: the first
        # matching entry -- same as Profile.append_note's own fallback.
        entry = matches[0]
    if entry is None:
        return ""
    if kind == "education":
        # Education entries have no bullets; their source text is their
        # notes, plus any coursework list injected at render time.
        parts = [entry["notes"]] if entry.get("notes") else []
        coursework = entry.get("coursework")
        if isinstance(coursework, list):
            parts.extend(c for c in coursework if isinstance(c, str))
        return "\n".join(parts)
    parts = list(entry.get("bullets") or [])
    if entry.get("notes"):
        parts.append(entry["notes"])
    return "\n".join(parts)


def draft_for(question: str, target: str, profile, *, backend: str, model: str) -> tuple:
    """(draft, citation, used). ("", "", []) whenever the draft cannot be traced to the source.

    `used` is returned so the caller can show the verified spans the draft was built
    from -- the mechanically-checked evidence -- rather than asking the user to trust
    unverified prose, matching how skills.derive shows evidence rather than a claim.
    """
    source = source_text(profile, target)
    if not source:
        return "", "", []
    user = f"Source text:\n---\n{source}\n---\nQuestion: {question}"
    try:
        with ui.spinner("Checking what you have already written about this"):
            data, _ = llm.complete_schema(backend=backend, model=model,
                                          system=DRAFT_SYSTEM, user=user,
                                          schema=DraftAnswer, thinking="low")
    except Exception:
        return "", "", []

    draft = (data.get("draft") or "").strip()
    used = [u for u in (data.get("used") or []) if (u or "").strip()]
    if not draft or not used:
        return "", "", []
    # Every cited span must actually be in the user's own text.
    if any(u.strip() not in source for u in used):
        return "", "", []
    # And every number-like token in the draft must be too -- see _NUMBER_RE above.
    if any(n not in source for n in _NUMBER_RE.findall(draft)):
        return "", "", []
    return draft, f"draft (a suggestion assembled from the spans below · {target}):", used
