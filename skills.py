"""Fill in skill groups by hand, then derive more — each one backed by evidence.

The derivation is the risky half. A model asked for "skills implied by this resume"
will happily return ones the user cannot defend in an interview, so every candidate
must carry a verbatim span from the user's own bullets or notes, and that span is
checked mechanically here rather than trusted.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import List

from pydantic import BaseModel

import llm
import ui
from profile import note_targets

DERIVE_SYSTEM = (
    "List skills the candidate demonstrably has, based only on the entries below. "
    "For each one, copy into `evidence` the exact span of text from a bullet or note "
    "that demonstrates it — verbatim, character for character, not a paraphrase. "
    "Use `direct` when the text names the skill, `implied` when the work clearly "
    "required it. Do not list a skill you cannot quote evidence for. Return the "
    "schema only."
)

# The evidence floor is asymmetric, because it guards against one thing only: a
# skill "proved" by an unrelated scrap of text ("Machine Learning" evidenced by
# "the"). That risk exists only when the evidence does NOT name the skill.
#   - Evidence that names the skill (as a whole word -- see `_names`) is on-topic
#     by construction, so NO length or word-count floor applies. This keeps short
#     real skills like "React", "Go", "SQL", "C++".
#   - Evidence that does NOT name the skill is the model asserting an implied
#     skill, and must carry enough context to support that inference, hence the
#     char and word minimums below.
MIN_EVIDENCE_CHARS = 15
MIN_EVIDENCE_WORDS = 3


def _names(name: str, evidence: str) -> bool:
    """True when `name` appears in `evidence` as a standalone token.

    Non-word lookarounds rather than \\b so names ending in punctuation match
    correctly: "C++", "C#", ".NET", "Node.js" all have non-word edge characters.
    This keeps "Go" from being "named" by "Going".
    """
    return re.search(rf"(?<!\w){re.escape(name)}(?!\w)", evidence, re.I) is not None


class SkillCandidate(BaseModel):
    skill: str
    group: str = ""
    evidence: str = ""
    source_ref: str = ""
    strength: str = "implied"


class DerivedSkills(BaseModel):
    candidates: List[SkillCandidate] = []


def corpus_pieces(content: dict) -> list:
    """Each bullet and each note as its own ``(ref, text)`` pair.

    Evidence is checked against these pieces individually, never a joined blob: a
    span that only exists because two bullets (or two unrelated entries) got
    concatenated proves nothing that any single source sentence says.

    `ref` comes from profile.note_targets, so it disambiguates (see
    profile.NOTE_TARGET_SEP) when two entries share a company or project title --
    otherwise a derived skill's evidence would attribute to "experience:Amazon"
    with no way to tell which of two Amazon entries actually said it.
    """
    pieces = []
    for kind, entry, ref in note_targets(content):
        if kind not in ("experience", "project"):
            continue
        for bullet in entry.get("bullets") or []:
            pieces.append((ref, bullet))
        if entry.get("notes"):
            pieces.append((ref, entry["notes"]))
    return pieces


def corpus_text(content: dict) -> str:
    """Every bullet and note, newline-joined. Derived from ``corpus_pieces`` so
    the two cannot drift."""
    return "\n".join(text for _, text in corpus_pieces(content))


def _existing(content: dict) -> set:
    return {e.strip().lower()
            for g in content.get("skills") or []
            for e in (g.get("entries") or [])}


def _payload(content: dict) -> str:
    entries = []
    for kind, key, match in (("experience", "experience", "company"),
                             ("project", "projects", "title")):
        for entry in content.get(key) or []:
            entries.append({"ref": f"{kind}:{entry.get(match, '')}",
                            "bullets": entry.get("bullets") or [],
                            "notes": entry.get("notes") or ""})
    groups = [g.get("label", "") for g in content.get("skills") or []]
    return json.dumps({"entries": entries, "existing_groups": groups},
                      separators=(",", ":"), ensure_ascii=False)


def derive(content: dict, *, backend: str, model: str, rejected=()) -> list:
    """Evidence-verified skill candidates not already present. Never raises."""
    try:
        with ui.spinner("Deriving skills from your experience"):
            data, _ = llm.complete_schema(
                backend=backend, model=model, system=DERIVE_SYSTEM,
                user=_payload(content)[:20000], schema=DerivedSkills, thinking="low")
    except Exception:
        return []

    pieces = corpus_pieces(content)
    have = _existing(content) | {r.strip().lower() for r in rejected}
    out, seen, dropped_short = [], set(), 0
    for c in data.get("candidates", []):
        name = (c.get("skill") or "").strip()
        evidence = (c.get("evidence") or "").strip()
        key = name.lower()
        if not name or not evidence or key in have or key in seen:
            continue
        # Computed once, used for both the floor and the `strength` coercion.
        names_itself = _names(name, evidence)
        # A span too short to demonstrate anything defeats the guard -- reject it
        # before the substring check. No floor when the span names the skill: it
        # is on-topic by construction (see the constants above).
        too_short = not names_itself and (
            len(evidence) < MIN_EVIDENCE_CHARS
            or len(evidence.split()) < MIN_EVIDENCE_WORDS)
        if too_short:
            dropped_short += 1
            continue
        # The guard: a case-SENSITIVE substring of one single piece. A span that
        # straddles a join boundary, or is reflowed, or was invented, fails here.
        # The matching piece also tells us the true source_ref -- the model's is
        # not trusted.
        ref = next((r for r, text in pieces if evidence in text), None)
        if ref is None:
            continue
        seen.add(key)
        # `direct` drives a pre-ticked checkbox, so it must be mechanical, not a
        # model assertion: honour it only when the skill name is in its evidence.
        strength = "direct" if names_itself else "implied"
        out.append({"skill": name, "group": (c.get("group") or "").strip(),
                    "evidence": evidence, "source_ref": ref, "strength": strength})
    if dropped_short:
        ui.hint(f"  Dropped {dropped_short} suggested skill(s): evidence too short "
                "to support the claim.")
    return out


def apply(content: dict, accepted: list) -> dict:
    """A copy of `content` with the accepted skills added to their groups."""
    out = json.loads(json.dumps(content))
    groups = out.setdefault("skills", [])
    by_label = {g.get("label", ""): g for g in groups}
    for c in accepted:
        label = c.get("group") or "Skills"
        group = by_label.get(label)
        if group is None:
            group = {"label": label, "entries": []}
            groups.append(group)
            by_label[label] = group
        entries = group.setdefault("entries", [])
        if c["skill"].strip().lower() not in {e.strip().lower() for e in entries}:
            entries.append(c["skill"])
    return out


def _fill(content: dict) -> dict:
    """Edit the groups by hand. No model call."""
    out = json.loads(json.dumps(content))
    groups = out.setdefault("skills", [])
    for g in groups:
        current = ", ".join(g.get("entries") or [])
        raw = ui.text(f"{g.get('label', 'Skills')} (comma-separated)", default=current)
        g["entries"] = [e.strip() for e in raw.split(",") if e.strip()]
    while ui.confirm("Add another skill group?", default=not groups):
        label = ui.text("Group name (e.g. Research, Engineering)").strip()
        if not label:
            break
        raw = ui.text(f"{label} (comma-separated)")
        groups.append({"label": label,
                       "entries": [e.strip() for e in raw.split(",") if e.strip()]})
    out["skills"] = [g for g in groups if g.get("entries")]
    return out


def run(root: Path, *, backend: str, model: str) -> None:
    """The interactive step: fill, then derive, then write."""
    import install                      # write_constant_key; imported here to avoid a cycle
    import merge                        # backup(); imported here to avoid a cycle, same reason

    cpath, path = root / "constant.json", root / "content.json"
    content = json.loads(path.read_text())
    constant = json.loads(cpath.read_text()) if cpath.exists() else {}

    if ui.confirm("Edit your skill groups by hand first?", default=True):
        content = _fill(content)

    rejected = constant.get("rejected_skills") or []
    candidates = derive(content, backend=backend, model=model, rejected=rejected)
    if not candidates:
        print("  No new skills to suggest.")
    else:
        accepted = ui.evidence_checkbox(
            "Derived from your experience — pick what's true:", candidates)
        content = apply(content, accepted)
        picked = {c["skill"].lower() for c in accepted}
        turned_down = [c["skill"] for c in candidates if c["skill"].lower() not in picked]
        # Suppressing a candidate forever is a heavy, invisible consequence for a
        # box left unticked -- only do it when the user says so explicitly.
        if turned_down and ui.confirm(
                f"Stop offering the {len(turned_down)} skill(s) you didn't pick?",
                default=False):
            install.write_constant_key(root, "rejected_skills", rejected + turned_down)
        print(f"  Added {len(accepted)} skill(s).")

    # A truncate-then-write with no snapshot: a Ctrl-C mid-write would otherwise leave
    # a truncated corpus with no way back, the exact loss this project's own author
    # already suffered once. merge.run and bootstrap_step both snapshot first.
    merge.backup(root)
    path.write_text(json.dumps(content, indent=2, ensure_ascii=False) + "\n")
