"""Decide which entry a note belongs to.

A note is only worth collecting if it lands somewhere it will be found again.
Before this, an unrecognised target meant the note went to a catch-all bucket or
was dropped -- so the answer the user typed disappeared from the entry it was
actually about. When the target is not certain, the user picks.
"""
from __future__ import annotations

import ui
from profile import NOTE_TARGET_SEP, note_targets

GENERAL_LABEL = "General notes (not about one entry)"
SKIP_LABEL = "Skip this note"


def entry_choices(profile) -> list:
    """[(target, label)] for every entry a note can attach to.

    `target` comes from profile.note_targets, so it is only ever disambiguated
    (with NOTE_TARGET_SEP) when this entry's plain "kind:name" form is shared by
    more than one entry -- see profile.py for why. Experience entries also carry
    a title in their label, so a promotion (two entries at the same company) gets
    a label the user can actually tell apart, e.g. "experience — Amazon (SDE III)".
    """
    out = []
    for kind, entry, target in note_targets(profile.content):
        match = {"experience": "company", "project": "title", "education": "institution"}[kind]
        name = entry.get(match)
        title = entry.get("title") if kind == "experience" else None
        label = f"{kind} — {name}" + (f" ({title})" if title else "")
        out.append((target, label))
    return out


def resolve_target(profile, proposed: str) -> str:
    """A valid note target. "" means skip.

    A target the model already got right is used without a prompt; anything else
    asks, rather than guessing on the user's behalf. The plain canonical form
    ("experience:<company>") is always accepted here even for a colliding company
    -- that is the documented fallback (Profile.append_note resolves it to the
    first matching entry), not a target this function needs to reject.
    """
    proposed = (proposed or "").strip()
    rows = note_targets(profile.content)
    exact = {t for _, _, t in rows}
    canonical = {t.split(NOTE_TARGET_SEP, 1)[0] for t in exact}
    if proposed in exact or proposed in canonical:
        return proposed
    if not ui.interactive():
        return "general"
    # Index-tag every option before presenting it, and resolve the pick by position,
    # not by label text: two experience entries at one company (a promotion) can
    # still render an identical label if neither carries a title, and a text lookup
    # (`next(t for t, lbl in choices if lbl == picked)`) would silently resolve
    # either pick to whichever one appears first in the list. ui.evidence_checkbox's
    # console branch and batch.select_targets use the same index-tag pattern for the
    # same reason.
    choices = entry_choices(profile)
    labels = [lbl for _, lbl in choices] + [GENERAL_LABEL, SKIP_LABEL]
    tagged = [f"{i + 1}. {lbl}" for i, lbl in enumerate(labels)]
    picked = ui.select("Which entry is this note about?", tagged)
    if picked not in tagged:
        return "general"
    idx = tagged.index(picked)
    if idx >= len(choices):
        return "" if labels[idx] == SKIP_LABEL else "general"
    return choices[idx][0]
