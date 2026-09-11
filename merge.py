"""Add another resume or CV to an existing content.json without losing what is there.

content.json is the corpus every tailored application is built from, and its `notes`
fields are the part a model cannot regenerate — they came from the user answering
questions about their own work. So the merge is deliberately lopsided: the model may
propose which entries correspond and which bullets are new, but notes never leave
Python, conflicts are reported rather than resolved, and nothing is written until the
user has seen the plan.
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import List

from pydantic import BaseModel

import llm
import ui

MERGE_SYSTEM = (
    "Match incoming resume entries against existing ones. Two entries match when they "
    "describe the same role at the same organisation, or the same project, even if the "
    "wording differs. For a match, list only the incoming bullets that state something "
    "the existing bullets do not, and note any conflicting dates or titles. Entries "
    "with no counterpart go in `added`. Do not invent entries or bullets. Return the "
    "schema only."
)


class MergeMatch(BaseModel):
    kind: str = "experience"
    existing_ref: str = ""
    incoming_ref: str = ""
    new_bullets: List[str] = []
    conflicts: List[str] = []


class MergePlan(BaseModel):
    matched: List[MergeMatch] = []
    added: List[int] = []          # indices into the incoming entry list


def entry_ref(kind: str, entry: dict) -> str:
    if kind == "experience":
        return f"{entry.get('company', '')} · {entry.get('title', '')}".strip(" ·")
    return str(entry.get("title", ""))


def _strip_notes(content: dict) -> dict:
    """The corpus as the model sees it: bullets only, never notes."""
    out = {}
    for kind, key in (("experience", "experience"), ("project", "projects")):
        out[key] = [{"ref": entry_ref(kind, e), "bullets": e.get("bullets") or []}
                    for e in content.get(key) or []]
    return out


def plan(existing: dict, incoming: dict, *, backend: str, model: str) -> dict:
    """{"matched": [...], "added": [...]} — added entries carry a `_ref` for display."""
    flat_incoming = []
    for kind, key in (("experience", "experience"), ("project", "projects")):
        for e in incoming.get(key) or []:
            flat_incoming.append({"kind": kind, "key": key, "entry": e,
                                  "ref": entry_ref(kind, e)})
    if not flat_incoming:
        return {"matched": [], "added": []}

    user = json.dumps({
        "existing": _strip_notes(existing),
        "incoming": [{"index": i, "kind": f["kind"], "ref": f["ref"],
                      "bullets": f["entry"].get("bullets") or []}
                     for i, f in enumerate(flat_incoming)],
    }, separators=(",", ":"), ensure_ascii=False)
    try:
        with ui.spinner("Matching the new resume against your existing entries"):
            data, _ = llm.complete_schema(backend=backend, model=model, system=MERGE_SYSTEM,
                                          user=user[:20000], schema=MergePlan, thinking="low")
    except Exception:
        return {"matched": [], "added": []}

    added = []
    for i in data.get("added", []):
        if isinstance(i, int) and 0 <= i < len(flat_incoming):
            f = flat_incoming[i]
            added.append({**json.loads(json.dumps(f["entry"])),
                          "kind": f["kind"], "_ref": f["ref"]})
    return {"matched": data.get("matched", []), "added": added}


def merge_skills(existing: dict, incoming: dict) -> list:
    """Skill groups combined, deduped case-insensitively, existing spelling wins."""
    out = json.loads(json.dumps(existing.get("skills") or []))
    by_label = {g.get("label", ""): g for g in out}
    for g in incoming.get("skills") or []:
        target = by_label.get(g.get("label", ""))
        if target is None:
            target = {"label": g.get("label", ""), "entries": []}
            out.append(target)
            by_label[target["label"]] = target
        have = {e.lower() for e in target.get("entries") or []}
        for e in g.get("entries") or []:
            if e.lower() not in have:
                target.setdefault("entries", []).append(e)
                have.add(e.lower())
    return out


def apply(existing: dict, plan_dict: dict) -> dict:
    """A copy of `existing` with the plan applied. Notes are copied through untouched."""
    out = json.loads(json.dumps(existing))
    index = {}
    for kind, key in (("experience", "experience"), ("project", "projects")):
        for e in out.get(key) or []:
            index[entry_ref(kind, e)] = e

    matched = plan_dict.get("matched", [])
    placed = 0
    for m in matched:
        ref = m.get("existing_ref", "")
        target = index.get(ref)
        if target is None:
            # ui.merge_diff already showed this block as "= matched existing / + N new
            # bullets" and the user confirmed it. Dropping it on a bare `continue` would
            # give them silently less than the plan they approved. Say so; do not raise.
            n = len(m.get("new_bullets") or [])
            print(f"  ! could not place {n} bullet(s) for {ref!r}: no existing entry "
                  f"matches that reference. Nothing was changed for it.")
            continue
        placed += 1
        bullets = target.setdefault("bullets", [])
        have = {b.strip().lower() for b in bullets}
        for b in m.get("new_bullets") or []:
            if b.strip().lower() not in have:
                bullets.append(b)
                have.add(b.strip().lower())
        # notes and dates are left exactly as they were: conflicts are reported, not resolved

    for a in plan_dict.get("added", []):
        entry = {k: v for k, v in a.items() if k not in ("kind", "_ref")}
        entry.setdefault("notes", "")
        key = "experience" if a.get("kind") == "experience" else "projects"
        out.setdefault(key, []).append(entry)

    if placed != len(matched):
        print(f"  Note: the plan showed {len(matched)} matched "
              f"{'entry' if len(matched) == 1 else 'entries'}, but only {placed} "
              f"could be applied. See the warnings above.")
    return out


def _snapshot(src: Path) -> Path:
    """Timestamped, non-clobbering copy of `src`. A fixed `.bak` name would let a second
    merge overwrite the snapshot taken before the first — which is exactly why the
    original incident was unrecoverable."""
    src = Path(src)
    stamp = datetime.now().strftime("%Y-%m-%dT%H%M%S")
    dest = src.with_name(f"{src.name}.{stamp}.bak")
    n = 2
    while dest.exists():
        dest = src.with_name(f"{src.name}.{stamp}-{n}.bak")
        n += 1
    shutil.copyfile(src, dest)
    return dest


def backup(root: Path, *, include_constant: bool = False) -> List[Path]:
    """Snapshot content.json (and constant.json too, for callers about to rebuild both).

    Returns the paths actually written, content.json's first. Either file may be absent
    — only what exists is copied, so the caller can report exactly what it saved instead
    of claiming a backup that never happened.
    """
    root = Path(root)
    written: List[Path] = []
    if (root / "content.json").exists():
        written.append(_snapshot(root / "content.json"))
    if include_constant and (root / "constant.json").exists():
        written.append(_snapshot(root / "constant.json"))
    return written


def run(root: Path, resume_path, *, backend: str, model: str) -> bool:
    """Extract, plan, show, confirm, write. Returns whether anything was applied."""
    import install                      # extract_resume_text / BootstrapExtract

    root = Path(root)
    existing = json.loads((root / "content.json").read_text())
    text = install.extract_resume_text(Path(resume_path))
    try:
        with ui.spinner(f"Reading {Path(resume_path).name}"):
            incoming, _ = llm.complete_schema(
                backend=backend, model=model, system=install.BOOTSTRAP_SYSTEM,
                user=text[:20000], schema=install.BootstrapExtract, thinking="low")
    except Exception as exc:
        # Nothing is written yet — no backup, no file change. Report and back out
        # cleanly rather than crashing setup with a traceback (plan() already does this).
        print(f"  Could not read {Path(resume_path).name}: {exc}")
        return False

    p = plan(existing, incoming, backend=backend, model=model)
    ui.merge_diff(p)
    if not p["matched"] and not p["added"]:
        return False
    if not ui.confirm("Apply this merge?", default=True):
        print("  Nothing written.")
        return False

    saved = backup(root)          # content.json exists here — it was read above
    merged = apply(existing, p)
    merged["skills"] = merge_skills(existing, incoming)
    have_edu = {json.dumps(e, sort_keys=True) for e in merged.get("education") or []}
    for e in incoming.get("education") or []:
        if json.dumps(e, sort_keys=True) not in have_edu:
            merged.setdefault("education", []).append(e)
    (root / "content.json").write_text(json.dumps(merged, indent=2, ensure_ascii=False) + "\n")
    print(f"  Merged. Previous content.json saved to {saved[0].name}.")
    return True
