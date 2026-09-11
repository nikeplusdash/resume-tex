"""Add and edit content.json entries with model help but no fabrication.

/new builds one entry from a pasted blob: the model extracts the fields, you
correct them, it drafts sharper bullets, and a short clarifying loop captures
notes. /edit is a drill-down over content.json -- section, then entry, then the
scope you press Enter on: the whole entry, every bullet, one bullet, the header
fields, or the notes.

The corpus's `notes` are the part a model cannot regenerate (they came from the
user answering questions about their own work). So a model never overwrites them
here -- distilled notes append; the one replace path is an explicit human edit.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

from pydantic import create_model

import console
import install
import llm
import merge
import tailor
import ui


@dataclass(frozen=True)
class Section:
    name: str                       # user-facing + the /new argument
    key: str                        # content.json key
    schema: type                    # pydantic model for extraction
    fields: tuple                   # ((field, label, required: bool), ...)
    ref: Callable                   # entry dict -> one-line label
    rich: bool                      # has bullets + notes


SECTIONS: dict = {
    "experience": Section(
        "experience", "experience", tailor.ExperienceEntry,
        (("company", "Company", True), ("title", "Title", True),
         ("dates", "Dates (e.g. Nov 2025 - Present)", True),
         ("location", "Location", False),
         ("link", "Link (URL, optional)", False)),
        lambda e: f"{e.get('company', '?')} · {e.get('title', '?')}", True),
    "project": Section(
        "project", "projects", tailor.ProjectEntry,
        (("title", "Title", True), ("link", "Link (URL, optional)", False)),
        lambda e: e.get("title", "?"), True),
    "education": Section(
        "education", "education", tailor.EducationEntry,
        (("institution", "Institution", True), ("degree", "Degree", True),
         ("gpa", "GPA", False), ("start", "Start", False),
         ("end", "End", False), ("location", "Location", False)),
        lambda e: f"{e.get('institution', '?')} - {e.get('degree', '')}".strip(" -"), False),
    "certification": Section(
        "certification", "certifications", tailor.CertEntry,
        (("name", "Name", True), ("issuer", "Issuer", False), ("date", "Date", False)),
        lambda e: f"{e.get('name', '?')} ({e.get('issuer', '')})".strip(" ()"), False),
    "award": Section(
        "award", "awards", tailor.AwardEntry,
        (("title", "Title", True), ("body", "Awarding body", False), ("year", "Year", False)),
        lambda e: f"{e.get('title', '?')} - {e.get('year', '')}".strip(" -"), False),
    "publication": Section(
        "publication", "publications", tailor.PubEntry,
        (("title", "Title", True), ("venue", "Venue", False), ("year", "Year", False)),
        lambda e: f"{e.get('title', '?')} - {e.get('venue', '')}".strip(" -"), False),
    "language": Section(
        "language", "languages", tailor.LangEntry,
        (("language", "Language", True), ("proficiency", "Proficiency", False)),
        lambda e: f"{e.get('language', '?')} - {e.get('proficiency', '')}".strip(" -"), False),
}


def _lenient(schema: type) -> type:
    """A copy of `schema` with every field optional: str fields default "",
    list fields default []. A pasted blob rarely has every field, and a missing
    GPA must not fail the extraction call."""
    fields = {}
    for fname, info in schema.model_fields.items():
        if getattr(info.annotation, "__origin__", None) is list:
            fields[fname] = (List[str], [])
        else:
            fields[fname] = (Optional[str], "")
    return create_model(f"Lenient{schema.__name__}", **fields)


EXTRACT_ENTRY_SYSTEM = (
    "Extract the fields for ONE {section} entry from the text below. Copy facts "
    "verbatim -- company, title, dates, names, numbers, links. Never invent or "
    "infer a date, metric, title, or tool the text does not state; leave a field "
    "empty instead. For bullets: one achievement per line, past tense, lead with "
    "the action, keep every number exactly as written, no first person, no "
    "marketing words (spearheaded, robust, seamless, passionate). Return the "
    "schema only."
)

REFORM_BULLET_SYSTEM = (
    "Rewrite this resume bullet so it is tighter and more concrete. Use only "
    "facts already present in: the bullet itself, the entry's other bullets, its "
    "notes, and the applicant's hint. Keep every number exactly. Past tense, no "
    "first person, no marketing words (spearheaded, robust, seamless, leveraged, "
    "passionate). If nothing can be improved without inventing, return the bullet "
    "unchanged. Output the single bullet line only, nothing else."
)


def extract(section: Section, blob: str, *, backend: str, model: str) -> dict:
    """One model call: the fields for a single `section` entry, verbatim from
    `blob`. {} on a blank blob or any LLM failure -- /new then falls through to
    the manual field form."""
    blob = (blob or "").strip()
    if not blob:
        return {}
    try:
        with ui.spinner(f"Reading your {section.name} details"):
            data, _ = llm.complete_schema(
                backend=backend, model=model,
                system=EXTRACT_ENTRY_SYSTEM.format(section=section.name),
                user=blob[:20000], schema=_lenient(section.schema), thinking="low")
        return data
    except llm.LLMError as e:
        print(f"  couldn't read that automatically ({str(e)[:100]}) - fill it in below")
        return {}


def reform_bullets(bullets: list, entry: dict, *, backend: str, model: str,
                   hint: str = "") -> list:
    """Rewrite each bullet tighter, one model call apiece (bullets are short;
    batching adds failure surface for no gain). A bullet whose call fails is
    returned unchanged -- never lose one."""
    out = []
    all_bullets = entry.get("bullets") or bullets
    notes = (entry.get("notes") or "").strip() or "(none)"
    for i, bullet in enumerate(bullets, 1):
        others = "\n".join(f"- {b}" for b in all_bullets if b != bullet) or "(none)"
        user = (
            f"Bullet to rewrite:\n{bullet}\n\n"
            f"Other bullets in this entry:\n{others}\n\n"
            f"Notes for this entry:\n{notes}\n\n"
            f"Applicant's hint:\n{hint.strip() or '(none)'}"
        )
        try:
            with ui.spinner(f"Sharpening bullet {i}/{len(bullets)}"):
                reply = llm.complete_text(backend=backend, model=model,
                                          system=REFORM_BULLET_SYSTEM, user=user,
                                          thinking="low")
            out.append(reply.text.strip() or bullet)
        except llm.LLMError:
            print(f"  (kept bullet {i} as-is - model unavailable)")
            out.append(bullet)
    return out


# ── /new: paste → entry ─────────────────────────────────────────────────────

def _load(root: Path):
    path = Path(root) / "content.json"
    content = json.loads(path.read_text()) if path.exists() else {}
    return path, content


def _save(path: Path, content: dict) -> None:
    path.write_text(json.dumps(content, indent=2, ensure_ascii=False) + "\n")


def _append_note(entry: dict, text: str) -> None:
    """Newline-join, mirroring Profile.append_note: successive notes must not
    weld into one run-on the tailor prompt then reads as a single claim."""
    text = (text or "").strip()
    if not text:
        return
    existing = (entry.get("notes") or "").strip()
    entry["notes"] = f"{existing}\n{text}".strip() if existing else text


def _derive_links(entry: dict) -> None:
    """The template renders a link as \\href{link}{short_link}, so a bare `link`
    with no display text never shows. Fill `short_link` from the URL (scheme,
    www., trailing slash stripped) unless the applicant set their own."""
    link = (entry.get("link") or "").strip()
    if link and not (entry.get("short_link") or "").strip():
        entry["short_link"] = re.sub(r"^https?://(www\.)?", "", link).rstrip("/")
    if not link:
        entry.pop("link", None)
        entry.pop("short_link", None)


def _confirm_fields(section: Section, entry: dict):
    """Prompt for each field, seeded with what extract found. None if a required
    field is left blank twice."""
    out: dict = {}
    for fname, label, required in section.fields:
        val = ui.text(label, default=str(entry.get(fname, "") or "")).strip()
        if not val and required:
            val = ui.text(f"{label} (required)", default="").strip()
        if not val and required:
            print(f"  Need a {label.split(' (')[0].lower()}. Nothing saved.")
            return None
        if val:
            out[fname] = val
    _derive_links(out)
    return out


def _collect_bullets(section: Section, entry: dict, *, backend: str, model: str) -> list:
    found = list(entry.get("bullets") or [])
    kept = found
    if found:
        picked = console.ask_choice(
            "Keep which bullets?", [(b, b) for b in found],
            multi=True, default_values=found)
        kept = list(found) if picked is console.CHOICE_CANCELLED else (picked or [])
    extra = ui.editor("Add more bullets, one per line (Enter to skip):")
    kept = list(kept) + [ln.strip() for ln in extra.splitlines() if ln.strip()]
    if kept and ui.confirm("Let the model sharpen these bullets?", default=True):
        new = reform_bullets(kept, {**entry, "bullets": kept}, backend=backend, model=model)
        for old, fresh in zip(kept, new):
            if fresh != old:
                print(f"  - {old}\n  + {fresh}")
        accepted = console.ask_choice(
            "Take which rewrites?", [(n, n) for n in new],
            multi=True, default_values=new)
        if accepted is console.CHOICE_CANCELLED:
            accepted = []
        accepted = accepted or []
        # keep the original for every rewrite not accepted, in order
        kept = [n if n in accepted else o for o, n in zip(kept, new)]
    return kept


def _collect_notes(entry: dict, label: str, kind: str, *, backend: str, model: str) -> None:
    """Mutates entry["notes"]. A short clarifying loop: show what context helps,
    take a freeform answer, distil it to a source note, append. Blank answer ends it."""
    guidance = install.entry_prompts([(kind, entry, label)], backend=backend, model=model)
    questions = guidance.get(f"{kind}:{label}") or []
    while True:
        if questions:
            print("\n  Useful context here:")
            for q in questions:
                print(f"    · {q}")
        raw = ui.editor("Add context - constraints, real numbers, why (Enter when done):")
        if not raw.strip():
            return
        with ui.spinner("Distilling your note"):
            note = tailor.distill_note(label, raw, backend=backend, model=model)
        if note:
            _append_note(entry, note)
            print(f"  note added: {note}")
        questions = []          # only show the prompts once


def _preview(section: Section, entry: dict, *, title: str) -> None:
    lines = [f"{f}: {entry.get(f, '')}" for f, _, _ in section.fields]
    for b in entry.get("bullets") or []:
        lines.append(f"  - {b}")
    if entry.get("notes"):
        lines.append(f"notes: {entry['notes']}")
    ui.box(lines, title=title)


def cmd_new_skill(root: Path) -> bool:
    path, content = _load(root)
    groups = content.get("skills") or []
    rows = [(i, g.get("label", "?")) for i, g in enumerate(groups)]
    pick = console.ask_choice("Add skills to which group?",
                              rows + [("__new__", "+ new group")])
    if pick is console.CHOICE_CANCELLED:
        print("  Nothing added.")
        return False
    raw = ui.editor("Skills, one per line:")
    items = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    if not items:
        print("  Nothing added.")
        return False
    if pick == "__new__":
        label = ui.text("Group label").strip()
        if not label:
            print("  Nothing added - no label.")
            return False
        new_group = {"label": label, "entries": items}
    else:
        new_group = None
    if not ui.confirm("Save to content.json?", default=True):
        return False
    path, content = _load(root)
    merge.backup(root)
    content.setdefault("skills", [])
    if new_group is not None:
        content["skills"].append(new_group)
        print(f"  Added skill group: {new_group['label']}")
    else:
        content["skills"][pick]["entries"] = (
            content["skills"][pick].get("entries", []) + items)
        print(f"  Added {len(items)} skills to {content['skills'][pick]['label']}")
    _save(path, content)
    return True


def cmd_new(root: Path, *, backend: str, model: str, section: str | None = None) -> bool:
    """Paste a blob, confirm the fields the model pulled, keep/sharpen bullets
    (rich sections), distil notes, preview, back up, append. True iff written."""
    if section is None:
        section = console.ask_choice(
            "Add what?", [(n, n) for n in SECTIONS] + [("skill", "skill")])
        if section is console.CHOICE_CANCELLED:
            return False
    if section == "skill":
        return cmd_new_skill(root)
    if section not in SECTIONS:
        print(f"  Unknown section {section!r}. One of: {', '.join(SECTIONS)}, skill.")
        return False
    sec = SECTIONS[section]

    blob = ui.editor(
        f"Paste everything you have about this {sec.name} - a blurb, rough "
        "bullets, the role, dates, links. More is better (Enter to skip):")
    extracted = extract(sec, blob, backend=backend, model=model)

    entry = _confirm_fields(sec, extracted)
    if entry is None:
        return False
    if blob.strip() and "bullets" in extracted and sec.rich:
        entry["bullets"] = extracted["bullets"]

    if sec.rich:
        entry["bullets"] = _collect_bullets(sec, entry, backend=backend, model=model)
        entry.setdefault("notes", "")
        _collect_notes(entry, sec.ref(entry), sec.name, backend=backend, model=model)
        if not entry["notes"]:
            entry.pop("notes")

    _preview(sec, entry, title=f"new {sec.name}")
    if not ui.confirm("Add this to content.json?", default=True):
        return False
    path, content = _load(root)                 # re-read: never write over a stale copy
    merge.backup(root)
    content.setdefault(sec.key, []).append(entry)
    _save(path, content)
    print(f"  Added {sec.name}: {sec.ref(entry)}")
    return True


# ── /edit: drill-down over content.json ─────────────────────────────────────

_BACK = "← back"
_CANCEL = "← cancel"


def cmd_edit_skills(root: Path) -> bool:
    path, content = _load(root)
    groups = content.get("skills") or []
    if not groups:
        print("  No skill groups yet - /new skill first.")
        return False
    gi = console.ask_choice("Edit which group?",
                            [(i, g.get("label", "?")) for i, g in enumerate(groups)]
                            + [("back", _BACK)])
    if gi is console.CHOICE_CANCELLED or gi in ("back", ""):
        return False
    group = groups[gi]
    label = ui.text("Group label", default=group.get("label", "")).strip() or group.get("label", "")
    kept = console.ask_choice(
        "Keep which skills?", [(s, s) for s in group.get("entries", [])],
        multi=True, default_values=group.get("entries", []))
    if kept is console.CHOICE_CANCELLED:
        return False
    changed = label != group.get("label") or list(kept) != group.get("entries")
    if not changed:
        return False
    path, content = _load(root)
    merge.backup(root)
    content["skills"][gi] = {"label": label, "entries": list(kept)}
    _save(path, content)
    print(f"  Updated skill group: {label}")
    return True


def _pick_section(content: dict):
    """A section name, "skill", or "cancel". Empty corpus prints a hint and
    returns "cancel"."""
    rows = []
    for name, sec in SECTIONS.items():
        n = len(content.get(sec.key) or [])
        if n:
            rows.append((name, f"{sec.name} ({n})"))
    groups = content.get("skills") or []
    if groups:
        n = sum(len(g.get("entries") or []) for g in groups)
        rows.append(("skill", f"skills ({n} across {len(groups)} groups)"))
    if not rows:
        print("  Nothing to edit yet — /new or /add first.")
        return "cancel"
    choice = console.ask_choice("Edit what?", rows + [("cancel", _CANCEL)])
    return "cancel" if choice is console.CHOICE_CANCELLED else choice


def _pick_entry(sec: Section, items: list):
    """An int index into `items`, or "back"."""
    rows = [(i, sec.ref(e)) for i, e in enumerate(items)]
    choice = console.ask_choice(f"{sec.name} - pick one", rows + [("back", _BACK)])
    return "back" if choice is console.CHOICE_CANCELLED else choice


def _pick_scope(sec: Section, entry: dict):
    """One of "whole", "allbullets", f"bullet:{i}", "fields", "notes", "back".

    "Reform every bullet" is offered only when the entry actually has bullets --
    a rich entry hand-edited in a text editor can lack the key entirely, and the
    handler would then KeyError out of /edit.
    """
    bullets = entry.get("bullets") or []
    rows = [("whole", "▸ Whole entry")]
    if bullets:
        rows.append(("allbullets", "▸ Reform every bullet"))
    rows += [(f"bullet:{i}", f"• {b}") for i, b in enumerate(bullets)]
    rows += [("fields", "▸ Header fields"),
             ("notes", "▸ Notes / add context"),
             ("back", _BACK)]
    choice = console.ask_choice(f"{sec.ref(entry)} - edit what?", rows)
    return "back" if choice is console.CHOICE_CANCELLED else choice


def _edit_fields(sec: Section, entry: dict) -> bool:
    """Per-field ui.text seeded with the current value. True iff one changed."""
    changed = False
    for fname, label, _req in sec.fields:
        cur = str(entry.get(fname, "") or "")
        new = ui.text(label, default=cur).strip()
        if new != cur:
            entry[fname] = new
            changed = True
    if changed:
        _derive_links(entry)
    return changed


def _edit_one_bullet(entry: dict, idx: int, *, backend: str, model: str) -> bool:
    bullets = entry["bullets"]
    print(f"  rewriting: {bullets[idx]}")
    typed = ui.editor("Rewrite it yourself, or leave blank and let the model "
                      "sharpen it:").strip()
    new = typed or reform_bullets([bullets[idx]], entry, backend=backend, model=model)[0]
    if new != bullets[idx]:
        print(f"  - {bullets[idx]}\n  + {new}")
    label = entry.get("company") or entry.get("title") or "entry"
    action = console.ask_choice(f"{label} - bullet {idx + 1}",
                                [("save", "Save this rewrite"),
                                 ("delete", "Delete this bullet"), ("back", _BACK)])
    if action is console.CHOICE_CANCELLED:      # Ctrl-C: do not save, do not delete
        return False
    if action == "save":
        bullets[idx] = new
        return True
    if action == "delete":
        bullets.pop(idx)
        return True
    return False


def _edit_notes(entry: dict, sec: Section, *, backend: str, model: str) -> bool:
    """A model never overwrites notes: "direct" is a human ui.editor REPLACE,
    "questions" runs _collect_notes which APPENDS distilled notes."""
    how = console.ask_choice(
        f"{sec.ref(entry)} - notes",
        [("direct", "Edit the note text directly"),
         ("questions", "Answer questions → distilled note"), ("back", _BACK)])
    if how is console.CHOICE_CANCELLED:
        return False
    if how == "direct":
        before = entry.get("notes") or ""
        if console.in_fullscreen():
            # ask() pre-fills the buffer and returns "" on a genuine blank submit,
            # so select-all-delete actually clears the note. ui.editor's `or
            # default` would hand the old note back instead.
            new = console.ask("Notes (leave empty to clear):", before, multiline=True).strip()
        else:
            # Non-shell fallback: ui.editor returns `default` on a blank submit,
            # so clearing a note this way isn't possible here (known limitation).
            new = ui.editor("Notes (leave empty to clear):", default=before).strip()
        entry["notes"] = new
        if not new:
            entry.pop("notes", None)
        return new != before
    if how == "questions":
        before = entry.get("notes", "")
        entry.setdefault("notes", "")
        _collect_notes(entry, sec.ref(entry), sec.name, backend=backend, model=model)
        if not entry["notes"]:
            entry.pop("notes")
        return entry.get("notes", "") != before
    return False


def _edit_whole(sec: Section, entry: dict, *, backend: str, model: str) -> bool:
    dump = "\n".join(f"{f}: {entry.get(f, '')}" for f, _, _ in sec.fields)
    dump += "\n" + "\n".join(f"- {b}" for b in entry.get("bullets") or [])
    extra = ui.editor("Current entry below. Paste corrections or extra context, "
                      "then the model re-forms it (Enter to keep as-is):", default=dump)
    if extra.strip() == dump.strip():
        return False
    fresh = extract(sec, extra, backend=backend, model=model) or {}
    for fname, _label, _req in sec.fields:
        entry[fname] = ui.text(
            _label, default=str(fresh.get(fname) or entry.get(fname, ""))).strip()
    _derive_links(entry)
    if sec.rich:
        entry["bullets"] = _collect_bullets(
            sec, {**entry, "bullets": fresh.get("bullets") or entry.get("bullets") or []},
            backend=backend, model=model)
        entry.setdefault("notes", "")
        _collect_notes(entry, sec.ref(entry), sec.name, backend=backend, model=model)
        if not entry.get("notes", ""):          # matches cmd_new / _edit_notes
            entry.pop("notes", None)
    _preview(sec, entry, title=f"{sec.name} (edited)")
    return ui.confirm("Save these changes?", default=True)


def cmd_edit(root: Path, *, backend: str, model: str) -> bool:
    """section → entry → scope, drilling into content.json. The scope you press
    Enter on is what gets changed. True iff at least one write happened. Every
    write path re-reads `content` from disk, backs up once, mutates in place,
    saves, and prints a one-line summary."""
    _, content = _load(root)
    if not content:
        print("  Nothing to edit yet — /new or /add first.")
        return False
    wrote = False
    while True:                                          # section loop (exits on "cancel")
        _, content = _load(root)
        section = _pick_section(content)
        if section in ("cancel", ""):
            return wrote
        if section == "skill":
            wrote = cmd_edit_skills(root) or wrote
            continue
        sec = SECTIONS[section]
        while True:                                      # entry loop (exits on "back")
            path, content = _load(root)
            items = content.get(sec.key) or []
            if not items:
                break
            idx = _pick_entry(sec, items)
            if idx in ("back", ""):
                break
            if not sec.rich:                             # flat: straight to per-field edit
                entry = items[idx]
                if _edit_fields(sec, entry):
                    merge.backup(root)
                    _save(path, content)
                    wrote = True
                    print(f"  {sec.ref(entry)}: fields updated")
                continue
            while True:                                  # scope loop (exits on "back")
                path, content = _load(root)              # re-read before any write
                entry = (content.get(sec.key) or [])[idx]
                scope = _pick_scope(sec, entry)
                if scope in ("back", ""):
                    break
                changed = False
                if scope == "whole":
                    changed = _edit_whole(sec, entry, backend=backend, model=model)
                elif scope == "allbullets":
                    originals = entry.get("bullets") or []
                    if not originals:           # hand-edited entry with no bullets key
                        continue
                    new = reform_bullets(originals, entry,
                                         backend=backend, model=model)
                    for o, n in zip(originals, new):
                        if n != o:
                            print(f"  - {o}\n  + {n}")
                    take = console.ask_choice("Keep which rewrites?", [(n, n) for n in new],
                                              multi=True, default_values=new)
                    if take is console.CHOICE_CANCELLED:   # Ctrl-C: keep every original
                        continue
                    merged = [n if n in (take or []) else o
                              for o, n in zip(originals, new)]
                    changed = merged != originals
                    entry["bullets"] = merged
                elif scope.startswith("bullet:"):
                    changed = _edit_one_bullet(entry, int(scope.split(":")[1]),
                                               backend=backend, model=model)
                elif scope == "fields":
                    changed = _edit_fields(sec, entry)
                elif scope == "notes":
                    changed = _edit_notes(entry, sec, backend=backend, model=model)
                if changed:
                    merge.backup(root)
                    _save(path, content)
                    wrote = True
                    print(f"  {sec.ref(entry)}: saved")
