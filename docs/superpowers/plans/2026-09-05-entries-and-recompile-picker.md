# /new + /edit Entry Management and /recompile Picker — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `/new` and `/edit` shell commands that build and change `content.json` entries with model help but no fabrication, and make `/recompile` (also `/rc`) offer a newest-first picker of saved applications.

**Architecture:** A new `entries.py` module owns both commands. A `SECTIONS` registry describes every editable part of `content.json`. `/new` is paste → extract (one model call against a lenient schema) → confirm fields → keep/drop/sharpen bullets → clarify into notes → preview → backup → append. `/edit` is a loop of inline `console.ask_choice` pickers drilling section → entry → scope, where the scope you press Enter on is what gets changed. `recompile.recent_apps()` scans the applications dir; `shell.cmd_recompile` shows a picker when given no path.

**Tech Stack:** Python 3.9, pydantic v2, prompt_toolkit (via `console`/`ui`), pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-05-entries-and-recompile-picker-design.md`

## Global Constraints

- **No new model calls in `/recompile`.** The picker only lists directories and reads mtimes.
- **A model never overwrites `notes`.** Distilled notes are appended (newline-joined). The only replace is the explicit `/edit` → "Edit the note text directly" choice, done by a human via `ui.editor`.
- **Extraction and bullet rewrites never invent facts.** Prompts state this; tests assert the prompt text carries the constraint.
- **Every write is preceded by `merge.backup(root)` exactly once and a `ui.confirm`.** A cancelled confirm or a blank required field means no backup and no write.
- **`content.json` is re-read from disk at the start of every write path**, never held from command start, so a concurrent `/skills` or `/edit` is not clobbered.
- **All model calls in tests are mocked** by patching `llm.complete_schema` / `llm.complete_text`. `conftest.py` already fails any test that reaches `llm._spawn`.
- **JSON is written** with `json.dumps(data, indent=2, ensure_ascii=False) + "\n"`.
- **`entries.py` depends one way only:** `shell → entries → {llm, ui, console, merge, tailor schemas, install.entry_prompts, tailor.distill_note, profile.Profile}`. Nothing imports `entries` except `shell` and the tests.

---

## File Structure

- **Create `entries.py`** — `Section` dataclass, `SECTIONS` registry, `_lenient()`, `EXTRACT_ENTRY_SYSTEM`, `REFORM_BULLET_SYSTEM`, `extract()`, `reform_bullets()`, `cmd_new()`, `cmd_edit()`, `cmd_new_skill()` / `cmd_edit_skills()`.
- **Create `test_entries.py`** — unit tests for all of the above with mocked model + patched `ui`/`console`.
- **Modify `recompile.py`** — add `recent_apps(base)`.
- **Modify `test_recompile.py`** — tests for `recent_apps`.
- **Modify `shell.py`** — `COMMANDS`, `_HELP`, `SHORTCUTS` (+ `/rc`), `cmd_new`, `cmd_edit` wrappers, `_ago()`, `cmd_recompile` picker branch; delete `cmd_project`.
- **Modify `test_shell.py`** — delete `cmd_project` tests; add `/new` `/edit` `/rc` dispatch + `/recompile` picker tests + `/help` assertions.
- **Modify `test_console.py`** — headless full-screen `/new` and `/edit` runs.

---

## Task 1: Section registry, lenient schema, prompt constants

**Files:**
- Create: `entries.py`
- Test: `test_entries.py`

**Interfaces:**
- Consumes: `tailor.{ExperienceEntry,ProjectEntry,EducationEntry,CertEntry,AwardEntry,PubEntry,LangEntry}` (pydantic v2 models).
- Produces:
  - `Section` frozen dataclass: `name:str, key:str, schema:type, fields:tuple[tuple[str,str,bool],...], ref:Callable[[dict],str], rich:bool`
  - `SECTIONS: dict[str, Section]` keyed by `Section.name`
  - `_lenient(schema: type) -> type` — a pydantic model, every field `Optional[str]=""` except list fields which become `List[str]=[]`
  - `EXTRACT_ENTRY_SYSTEM: str` (a `{section}` format field), `REFORM_BULLET_SYSTEM: str`

- [ ] **Step 1: Write the failing test**

```python
# test_entries.py
import entries
import tailor


def test_registry_covers_the_bulleted_and_flat_sections():
    names = set(entries.SECTIONS)
    assert {"experience", "project"} <= names
    assert {"education", "certification", "award", "publication", "language"} <= names
    # exactly experience + project are rich (bullets + notes)
    assert {n for n, s in entries.SECTIONS.items() if s.rich} == {"experience", "project"}


def test_every_registry_schema_is_a_real_tailor_schema():
    tailor_schemas = {
        tailor.ExperienceEntry, tailor.ProjectEntry, tailor.EducationEntry,
        tailor.CertEntry, tailor.AwardEntry, tailor.PubEntry, tailor.LangEntry,
    }
    for s in entries.SECTIONS.values():
        assert s.schema in tailor_schemas


def test_section_ref_builds_a_one_line_label():
    exp = entries.SECTIONS["experience"]
    assert exp.ref({"company": "Acme", "title": "PD"}) == "Acme · PD"
    proj = entries.SECTIONS["project"]
    assert proj.ref({"title": "Transit Board"}) == "Transit Board"


def test_lenient_makes_every_field_optional_with_empty_defaults():
    Lenient = entries._lenient(tailor.EducationEntry)
    m = Lenient.model_validate({"institution": "UM"})     # gpa/start/end omitted
    d = m.model_dump()
    assert d["institution"] == "UM"
    assert d["gpa"] == "" and d["start"] == "" and d["end"] == ""


def test_lenient_keeps_list_fields_as_lists():
    Lenient = entries._lenient(tailor.ExperienceEntry)
    m = Lenient.model_validate({"company": "Acme"})
    assert m.model_dump()["bullets"] == []


def test_prompt_constants_forbid_invention():
    assert "{section}" in entries.EXTRACT_ENTRY_SYSTEM
    assert "verbatim" in entries.EXTRACT_ENTRY_SYSTEM.lower()
    assert "never invent" in entries.EXTRACT_ENTRY_SYSTEM.lower()
    assert "only facts already present" in entries.REFORM_BULLET_SYSTEM.lower()
    assert "unchanged" in entries.REFORM_BULLET_SYSTEM.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest test_entries.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'entries'`

- [ ] **Step 3: Write minimal implementation**

```python
# entries.py
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
from profile import Profile


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
         ("location", "Location", False)),
        lambda e: f"{e.get('company', '?')} · {e.get('title', '?')}", True),
    "project": Section(
        "project", "projects", tailor.ProjectEntry,
        (("title", "Title", True), ("short_link", "Short link", False)),
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest test_entries.py -q`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add entries.py test_entries.py
git commit -m "feat(entries): section registry, lenient schema, prompt constants"
```

---

## Task 2: `extract()` — blob → entry dict

**Files:**
- Modify: `entries.py`
- Test: `test_entries.py`

**Interfaces:**
- Consumes: `Section`, `_lenient`, `EXTRACT_ENTRY_SYSTEM` (Task 1); `llm.complete_schema`, `llm.LLMError`.
- Produces: `extract(section: Section, blob: str, *, backend: str, model: str) -> dict` — the model's fields as a plain dict (via `model_dump`), `{}` on `LLMError` or empty blob.

- [ ] **Step 1: Write the failing test**

```python
# test_entries.py — add
import llm
import pytest


def _schema_reply(payload):
    def fake(*, backend, model, system, user, schema, thinking=None,
             binary=None, timeout=None, on_retry=None):
        return schema.model_validate(payload).model_dump(), llm.Reply("{}", finish="stop")
    return fake


def test_extract_maps_a_blob_to_a_section_dict(monkeypatch):
    payload = {"company": "Acme", "title": "Product Designer",
               "dates": "2024", "location": "", "bullets": ["Shipped the redesign"]}
    monkeypatch.setattr(llm, "complete_schema", _schema_reply(payload))
    out = entries.extract(entries.SECTIONS["experience"], "I was a PD at Acme in 2024",
                          backend="claude", model="m")
    assert out["company"] == "Acme"
    assert out["bullets"] == ["Shipped the redesign"]


def test_extract_returns_empty_on_blank_blob(monkeypatch):
    monkeypatch.setattr(llm, "complete_schema",
                        lambda **k: pytest.fail("must not call the model on an empty blob"))
    assert entries.extract(entries.SECTIONS["experience"], "   ", backend="c", model="m") == {}


def test_extract_swallows_llm_error(monkeypatch, capsys):
    def boom(**k):
        raise llm.LLMError("down")
    monkeypatch.setattr(llm, "complete_schema", boom)
    out = entries.extract(entries.SECTIONS["project"], "some project text",
                          backend="c", model="m")
    assert out == {}
    assert "fill it in" in capsys.readouterr().out


def test_extract_passes_the_section_name_into_the_prompt(monkeypatch):
    seen = {}

    def fake(*, backend, model, system, user, schema, **k):
        seen["system"] = system
        return schema.model_validate({}).model_dump(), llm.Reply("{}", finish="stop")
    monkeypatch.setattr(llm, "complete_schema", fake)
    entries.extract(entries.SECTIONS["certification"], "AWS cert 2023", backend="c", model="m")
    assert "certification entry" in seen["system"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest test_entries.py -q -k extract`
Expected: FAIL — `AttributeError: module 'entries' has no attribute 'extract'`

- [ ] **Step 3: Write minimal implementation**

```python
# entries.py — add after REFORM_BULLET_SYSTEM

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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest test_entries.py -q -k extract`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add entries.py test_entries.py
git commit -m "feat(entries): extract() - one model call, blob to entry dict"
```

---

## Task 3: `reform_bullets()` — sharpen bullets, never drop one

**Files:**
- Modify: `entries.py`
- Test: `test_entries.py`

**Interfaces:**
- Consumes: `REFORM_BULLET_SYSTEM` (Task 1); `llm.complete_text`, `llm.LLMError`.
- Produces: `reform_bullets(bullets: list[str], entry: dict, *, backend: str, model: str, hint: str = "") -> list[str]` — same length as `bullets`; a bullet the model can't reach is returned unchanged.

- [ ] **Step 1: Write the failing test**

```python
# test_entries.py — add

def _text_reply(mapping):
    def fake(*, backend, model, system, user, thinking=None, binary=None, timeout=None):
        for key, val in mapping.items():
            if key in user:
                return llm.Reply(val, finish="stop")
        return llm.Reply("UNMATCHED", finish="stop")
    return fake


def test_reform_bullets_rewrites_each_bullet(monkeypatch):
    monkeypatch.setattr(llm, "complete_text", _text_reply({
        "Did onboarding": "Cut onboarding time 40% by redesigning the first-run flow",
        "Ran tests": "Ran usability tests with 12 participants across 3 rounds",
    }))
    out = entries.reform_bullets(["Did onboarding", "Ran tests"], {"notes": ""},
                                 backend="c", model="m")
    assert out == ["Cut onboarding time 40% by redesigning the first-run flow",
                   "Ran usability tests with 12 participants across 3 rounds"]


def test_reform_bullets_keeps_a_bullet_the_model_cannot_reach(monkeypatch, capsys):
    calls = {"n": 0}

    def flaky(*, backend, model, system, user, **k):
        calls["n"] += 1
        if calls["n"] == 2:
            raise llm.LLMError("timeout")
        return llm.Reply("Sharper first bullet", finish="stop")
    monkeypatch.setattr(llm, "complete_text", flaky)
    out = entries.reform_bullets(["one", "two"], {"notes": ""}, backend="c", model="m")
    assert out == ["Sharper first bullet", "two"]
    assert "kept bullet 2" in capsys.readouterr().out


def test_reform_bullets_feeds_notes_and_other_bullets_as_context(monkeypatch):
    seen = {}

    def fake(*, backend, model, system, user, **k):
        seen["user"] = user
        return llm.Reply("x", finish="stop")
    monkeypatch.setattr(llm, "complete_text", fake)
    entries.reform_bullets(["target"], {"bullets": ["target", "sibling bullet"],
                                        "notes": "handled 2M requests/day"},
                           backend="c", model="m", hint="focus on scale")
    assert "sibling bullet" in seen["user"]
    assert "2M requests/day" in seen["user"]
    assert "focus on scale" in seen["user"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest test_entries.py -q -k reform`
Expected: FAIL — `AttributeError: module 'entries' has no attribute 'reform_bullets'`

- [ ] **Step 3: Write minimal implementation**

```python
# entries.py — add

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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest test_entries.py -q -k reform`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add entries.py test_entries.py
git commit -m "feat(entries): reform_bullets() - sharpen each, never drop one"
```

---

## Task 4: `cmd_new()` — paste → entry, with internal helpers

**Files:**
- Modify: `entries.py`
- Test: `test_entries.py`, `test_console.py`

**Interfaces:**
- Consumes: `SECTIONS`, `extract`, `reform_bullets` (Tasks 1-3); `console.ask_choice`, `ui.editor/text/confirm/box`, `merge.backup`, `install.entry_prompts`, `tailor.distill_note`.
- Produces:
  - `cmd_new(root: Path, *, backend: str, model: str, section: str | None = None) -> bool` — True iff `content.json` was written.
  - internal: `_confirm_fields(section, entry) -> dict | None` (None = aborted); `_collect_bullets(section, entry, *, backend, model) -> list[str]`; `_collect_notes(entry, label, *, backend, model) -> None` (mutates `entry["notes"]`); `_load(root) -> tuple[Path, dict]`; `_save(path, content)`; `_append_note(entry, text)`.

- [ ] **Step 1: Write the failing test**

```python
# test_entries.py — add
import json as _json


def _content(tmp_path, data):
    p = tmp_path / "content.json"
    p.write_text(_json.dumps(data))
    return p


def test_cmd_new_rich_flow_writes_a_full_entry(tmp_path, monkeypatch):
    _content(tmp_path, {"experience": []})
    monkeypatch.setattr(entries, "extract", lambda *a, **k: {
        "company": "Acme", "title": "PD", "dates": "2024", "location": "",
        "bullets": ["did onboarding"]})
    monkeypatch.setattr(entries, "reform_bullets",
                        lambda b, e, **k: ["Cut onboarding time 40%"])
    monkeypatch.setattr(install, "entry_prompts", lambda *a, **k: {"experience:Acme": ["scale?"]})
    monkeypatch.setattr(tailor, "distill_note", lambda *a, **k: "Team of 4; 12-week engagement.")

    choice = iter([
        "experience",              # section pick
        ["did onboarding"],        # keep-bullets multi
        ["Cut onboarding time 40%"],  # accept-rewrites multi
    ])
    monkeypatch.setattr(console, "ask_choice", lambda *a, **k: next(choice))
    editor = iter([
        "I was a PD at Acme",      # the paste blob
        "",                        # "add more bullets" -> none
        "Team of four, ran 12 weeks",  # clarify answer
        "",                        # clarify loop -> done
    ])
    monkeypatch.setattr(ui, "editor", lambda *a, **k: next(editor))
    monkeypatch.setattr(ui, "text", lambda label, default="", **k: default)  # keep extracted
    monkeypatch.setattr(ui, "confirm", lambda *a, **k: True)
    monkeypatch.setattr(ui, "box", lambda *a, **k: None)
    monkeypatch.setattr(merge, "backup", lambda root, **k: [tmp_path / "content.json.bak"])

    assert entries.cmd_new(tmp_path, backend="c", model="m") is True
    saved = _json.loads((tmp_path / "content.json").read_text())
    e = saved["experience"][0]
    assert e["company"] == "Acme"
    assert e["bullets"] == ["Cut onboarding time 40%"]
    assert e["notes"] == "Team of 4; 12-week engagement."


def test_cmd_new_flat_section_skips_bullets_and_notes(tmp_path, monkeypatch):
    _content(tmp_path, {})
    monkeypatch.setattr(entries, "extract", lambda *a, **k: {"institution": "UM", "degree": "MS"})
    monkeypatch.setattr(console, "ask_choice", lambda *a, **k: "education")
    monkeypatch.setattr(ui, "editor", lambda *a, **k: "MS at UM")
    monkeypatch.setattr(ui, "text", lambda label, default="", **k: default)
    monkeypatch.setattr(ui, "confirm", lambda *a, **k: True)
    monkeypatch.setattr(ui, "box", lambda *a, **k: None)
    monkeypatch.setattr(merge, "backup", lambda root, **k: [])
    monkeypatch.setattr(install, "entry_prompts",
                        lambda *a, **k: pytest.fail("flat section must not clarify"))

    assert entries.cmd_new(tmp_path, backend="c", model="m") is True
    saved = _json.loads((tmp_path / "content.json").read_text())
    assert saved["education"][0]["institution"] == "UM"
    assert "bullets" not in saved["education"][0]
    assert "notes" not in saved["education"][0]


def test_cmd_new_aborts_on_blank_required_field(tmp_path, monkeypatch, capsys):
    _content(tmp_path, {"projects": []})
    monkeypatch.setattr(entries, "extract", lambda *a, **k: {})
    monkeypatch.setattr(console, "ask_choice", lambda *a, **k: "project")
    monkeypatch.setattr(ui, "editor", lambda *a, **k: "")
    monkeypatch.setattr(ui, "text", lambda *a, **k: "")            # blank title, twice
    hit = {"backup": False}
    monkeypatch.setattr(merge, "backup", lambda root, **k: hit.__setitem__("backup", True))

    assert entries.cmd_new(tmp_path, backend="c", model="m") is False
    assert hit["backup"] is False
    assert _json.loads((tmp_path / "content.json").read_text())["projects"] == []
    assert "need a title" in capsys.readouterr().out.lower()


def test_cmd_new_manual_path_makes_no_model_call(tmp_path, monkeypatch):
    _content(tmp_path, {"projects": []})
    monkeypatch.setattr(llm, "complete_schema", lambda **k: pytest.fail("no model on manual path"))
    monkeypatch.setattr(console, "ask_choice", lambda *a, **k: "project")
    monkeypatch.setattr(ui, "editor", iter(["", ""]).__next__)     # blank blob, blank extra bullets
    monkeypatch.setattr(ui, "text", lambda label, default="", **k:
                        "Transit Board" if "Title" in label else "")
    monkeypatch.setattr(console, "ask_choice", iter(["project", []]).__next__)
    monkeypatch.setattr(install, "entry_prompts", lambda *a, **k: {"project:Transit Board": []})
    monkeypatch.setattr(ui, "confirm", lambda *a, **k: True)
    monkeypatch.setattr(ui, "box", lambda *a, **k: None)
    monkeypatch.setattr(merge, "backup", lambda root, **k: [])

    assert entries.cmd_new(tmp_path, backend="c", model="m") is True
    saved = _json.loads((tmp_path / "content.json").read_text())
    assert saved["projects"][0]["title"] == "Transit Board"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest test_entries.py -q -k cmd_new`
Expected: FAIL — `AttributeError: module 'entries' has no attribute 'cmd_new'`

- [ ] **Step 3: Write minimal implementation**

```python
# entries.py — add

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


def _confirm_fields(section: Section, entry: dict):
    """Prompt for each field, seeded with what extract found. None if a required
    field is left blank twice."""
    out = {}
    for fname, label, required in section.fields:
        val = ui.text(label, default=str(entry.get(fname, "") or "")).strip()
        if not val and required:
            val = ui.text(f"{label} (required)", default="").strip()
        if not val and required:
            print(f"  Need a {label.split(' (')[0].lower()}. Nothing saved.")
            return None
        if val:
            out[fname] = val
    return out


def _collect_bullets(section: Section, entry: dict, *, backend: str, model: str) -> list:
    found = list(entry.get("bullets") or [])
    kept = found
    if found:
        kept = console.ask_choice(
            "Keep which bullets?", [(b, b) for b in found],
            multi=True, default_values=found) or []
    extra = ui.editor("Add more bullets, one per line (Enter to skip):")
    kept += [ln.strip() for ln in extra.splitlines() if ln.strip()]
    if kept and ui.confirm("Let the model sharpen these bullets?", default=True):
        new = reform_bullets(kept, {**entry, "bullets": kept}, backend=backend, model=model)
        for old, fresh in zip(kept, new):
            if fresh != old:
                print(f"  - {old}\n  + {fresh}")
        accepted = console.ask_choice(
            "Take which rewrites?", [(n, n) for n in new],
            multi=True, default_values=new)
        # keep originals for the rewrites not accepted, in order
        kept = [n if n in (accepted or []) else o for o, n in zip(kept, new)]
    return kept


def _collect_notes(entry: dict, label: str, kind: str, *, backend: str, model: str) -> None:
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


def cmd_new(root: Path, *, backend: str, model: str, section: str = None) -> bool:
    _, content = _load(root)
    if section is None:
        section = console.ask_choice(
            "Add what?", [(n, n) for n in SECTIONS] + [("skill", "skill")])
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
    path, content = _load(root)                 # re-read: no stale write
    merge.backup(root)
    content.setdefault(sec.key, []).append(entry)
    _save(path, content)
    print(f"  Added {sec.name}: {sec.ref(entry)}")
    return True
```

Add a stub so imports resolve until Task 6:

```python
# entries.py — add near cmd_new
def cmd_new_skill(root: Path) -> bool:      # fleshed out in Task 6
    print("  /new skill is not wired yet.")
    return False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest test_entries.py -q -k cmd_new`
Expected: PASS (4 tests)

- [ ] **Step 5: Add the headless full-screen test**

```python
# test_console.py — add near the other _drive_shell tests

def test_fullscreen_new_experience_end_to_end(monkeypatch):
    import entries, tailor, install, merge, llm
    monkeypatch.setattr(entries, "extract", lambda *a, **k: {
        "company": "Acme", "title": "PD", "dates": "2024", "location": "NYC",
        "bullets": ["did the thing"]})
    monkeypatch.setattr(entries, "reform_bullets", lambda b, e, **k: list(b))
    monkeypatch.setattr(install, "entry_prompts", lambda *a, **k: {"experience:Acme · PD": []})
    monkeypatch.setattr(merge, "backup", lambda *a, **k: [])
    written = {}
    monkeypatch.setattr(entries, "_save", lambda p, c: written.update(c))

    def disp(line):
        events.append(("cmd", line))
        if line == "/newexp":
            entries.cmd_new(__import__("pathlib").Path("/tmp/x"),
                            backend="c", model="m", section="experience")
        return line != "/exit"

    async def script(feed, bridge):
        # section is passed, so first prompt is the paste editor
        await feed("/newexp\r")
        await asyncio.sleep(0.2)
        assert bridge.mode == "question"
        await feed("PD at Acme\r")            # blob
        await asyncio.sleep(0.3)
        # field confirms (company/title/dates/location) - Enter takes each default
        for _ in range(4):
            await feed("\r")
            await asyncio.sleep(0.15)
        # keep-bullets multi -> Submit row
        await feed("j"); await feed("\r"); await asyncio.sleep(0.2)
        await feed("\r")                      # "add more bullets" editor -> blank
        await asyncio.sleep(0.2)
        await feed("\r")                      # "sharpen?" Yes/No default
        await asyncio.sleep(0.2)
        await feed("j"); await feed("\r"); await asyncio.sleep(0.2)   # accept-rewrites Submit
        await feed("\r")                      # clarify editor -> blank -> done
        await asyncio.sleep(0.2)
        await feed("\r")                      # "Add this?" Yes
        await asyncio.sleep(0.2)
        assert bridge.mode == "command"
        await feed("/exit\r")

    _drive_shell(script)
    assert written.get("experience", [{}])[0].get("company") == "Acme"
```

Run: `python3 -m pytest test_console.py -q -k new_experience`
Expected: PASS (may need small `feed` count adjustments — align to the actual prompt sequence, not the exact counts above).

- [ ] **Step 6: Commit**

```bash
git add entries.py test_entries.py test_console.py
git commit -m "feat(entries): /new - paste to a full entry, model-assisted"
```

---

## Task 5: `cmd_edit()` — drill-down over content.json

**Files:**
- Modify: `entries.py`
- Test: `test_entries.py`, `test_console.py`

**Interfaces:**
- Consumes: everything from Tasks 1-4, plus `_confirm_fields`, `_collect_bullets`, `_collect_notes`, `_load`, `_save`, `_append_note`, `_preview`.
- Produces: `cmd_edit(root: Path, *, backend: str, model: str) -> bool` — True iff at least one write happened.

- [ ] **Step 1: Write the failing test**

```python
# test_entries.py — add

def _edit_content(tmp_path):
    return _content(tmp_path, {
        "experience": [{"company": "Acme", "title": "PD", "dates": "2024",
                        "location": "NYC", "bullets": ["did onboarding", "ran tests"],
                        "notes": "team of 4"}],
        "education": [{"institution": "UM", "degree": "MS", "gpa": "3.9",
                       "start": "2023", "end": "2025", "location": "MI"}],
    })


def test_cmd_edit_reforms_one_bullet_in_place(tmp_path, monkeypatch):
    _edit_content(tmp_path)
    monkeypatch.setattr(entries, "reform_bullets", lambda b, e, **k: ["Cut onboarding 40%"])
    monkeypatch.setattr(merge, "backup", lambda root, **k: [])
    choices = iter([
        "experience",                # section
        0,                           # entry index (see _pick_entry contract below)
        "bullet:0",                  # scope: first bullet
        "save",                      # Save the rewrite
        "cancel",                    # leave /edit
    ])
    monkeypatch.setattr(console, "ask_choice", lambda *a, **k: next(choices))
    monkeypatch.setattr(ui, "editor", lambda *a, **k: "")   # blank -> model sharpens

    assert entries.cmd_edit(tmp_path, backend="c", model="m") is True
    saved = _json.loads((tmp_path / "content.json").read_text())
    assert saved["experience"][0]["bullets"] == ["Cut onboarding 40%", "ran tests"]
    assert saved["experience"][0]["notes"] == "team of 4"          # untouched


def test_cmd_edit_deletes_a_bullet(tmp_path, monkeypatch):
    _edit_content(tmp_path)
    monkeypatch.setattr(merge, "backup", lambda root, **k: [])
    monkeypatch.setattr(console, "ask_choice",
                        iter(["experience", 0, "bullet:1", "delete", "cancel"]).__next__)
    monkeypatch.setattr(ui, "editor", lambda *a, **k: "ran tests")
    entries.cmd_edit(tmp_path, backend="c", model="m")
    saved = _json.loads((tmp_path / "content.json").read_text())
    assert saved["experience"][0]["bullets"] == ["did onboarding"]


def test_cmd_edit_header_fields(tmp_path, monkeypatch):
    _edit_content(tmp_path)
    monkeypatch.setattr(merge, "backup", lambda root, **k: [])
    monkeypatch.setattr(console, "ask_choice",
                        iter(["experience", 0, "fields", "cancel"]).__next__)
    monkeypatch.setattr(ui, "text",
                        lambda label, default="", **k: "Acme Corp" if "Company" in label else default)
    entries.cmd_edit(tmp_path, backend="c", model="m")
    saved = _json.loads((tmp_path / "content.json").read_text())
    assert saved["experience"][0]["company"] == "Acme Corp"
    assert saved["experience"][0]["title"] == "PD"


def test_cmd_edit_notes_direct_edit_replaces(tmp_path, monkeypatch):
    _edit_content(tmp_path)
    monkeypatch.setattr(merge, "backup", lambda root, **k: [])
    monkeypatch.setattr(console, "ask_choice",
                        iter(["experience", 0, "notes", "direct", "cancel"]).__next__)
    monkeypatch.setattr(ui, "editor", lambda label, default="", **k: "rewritten notes")
    entries.cmd_edit(tmp_path, backend="c", model="m")
    saved = _json.loads((tmp_path / "content.json").read_text())
    assert saved["experience"][0]["notes"] == "rewritten notes"


def test_cmd_edit_notes_questions_append(tmp_path, monkeypatch):
    _edit_content(tmp_path)
    monkeypatch.setattr(merge, "backup", lambda root, **k: [])
    monkeypatch.setattr(install, "entry_prompts", lambda *a, **k: {"experience:Acme · PD": ["q?"]})
    monkeypatch.setattr(tailor, "distill_note", lambda *a, **k: "12-week engagement.")
    monkeypatch.setattr(console, "ask_choice",
                        iter(["experience", 0, "notes", "questions", "cancel"]).__next__)
    monkeypatch.setattr(ui, "editor", iter(["ran 12 weeks", ""]).__next__)
    entries.cmd_edit(tmp_path, backend="c", model="m")
    saved = _json.loads((tmp_path / "content.json").read_text())
    assert saved["experience"][0]["notes"] == "team of 4\n12-week engagement."


def test_cmd_edit_flat_section_edits_fields_directly(tmp_path, monkeypatch):
    _edit_content(tmp_path)
    monkeypatch.setattr(merge, "backup", lambda root, **k: [])
    monkeypatch.setattr(console, "ask_choice",
                        iter(["education", 0, "cancel"]).__next__)   # no scope step
    monkeypatch.setattr(ui, "text",
                        lambda label, default="", **k: "4.0" if "GPA" in label else default)
    entries.cmd_edit(tmp_path, backend="c", model="m")
    saved = _json.loads((tmp_path / "content.json").read_text())
    assert saved["education"][0]["gpa"] == "4.0"


def test_cmd_edit_cancel_at_the_top_writes_nothing(tmp_path, monkeypatch):
    _edit_content(tmp_path)
    hit = {"backup": False}
    monkeypatch.setattr(merge, "backup", lambda root, **k: hit.__setitem__("backup", True))
    monkeypatch.setattr(console, "ask_choice", lambda *a, **k: "cancel")
    assert entries.cmd_edit(tmp_path, backend="c", model="m") is False
    assert hit["backup"] is False


def test_cmd_edit_empty_content_says_nothing_to_edit(tmp_path, monkeypatch, capsys):
    _content(tmp_path, {})
    assert entries.cmd_edit(tmp_path, backend="c", model="m") is False
    assert "nothing to edit" in capsys.readouterr().out.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest test_entries.py -q -k cmd_edit`
Expected: FAIL — `AttributeError: module 'entries' has no attribute 'cmd_edit'`

- [ ] **Step 3: Write minimal implementation**

The picker return contract (so tests and impl agree): `_pick_section` returns a
section name or `"cancel"`; `_pick_entry` returns an int index or `"back"`;
`_pick_scope` returns one of `"whole"`, `"allbullets"`, `f"bullet:{i}"`,
`"fields"`, `"notes"`, `"back"`.

```python
# entries.py — add

_BACK = "← back"
_CANCEL = "← cancel"


def _pick_section(content: dict):
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
        print("  Nothing to edit yet - /new or /add first.")
        return "cancel"
    return console.ask_choice("Edit what?", rows + [("cancel", _CANCEL)])


def _pick_entry(sec: Section, items: list):
    rows = [(i, sec.ref(e)) for i, e in enumerate(items)]
    return console.ask_choice(f"{sec.name} - pick one", rows + [("back", _BACK)])


def _pick_scope(sec: Section, entry: dict):
    rows = [("whole", "▸ Whole entry"),
            ("allbullets", "▸ Reform every bullet")]
    rows += [(f"bullet:{i}", f"• {b}") for i, b in enumerate(entry.get("bullets") or [])]
    rows += [("fields", "▸ Header fields"),
             ("notes", "▸ Notes / add context"),
             ("back", _BACK)]
    return console.ask_choice(f"{sec.ref(entry)} - edit what?", rows)


def _edit_fields(sec: Section, entry: dict) -> bool:
    changed = False
    for fname, label, _req in sec.fields:
        cur = str(entry.get(fname, "") or "")
        new = ui.text(label, default=cur).strip()
        if new != cur:
            entry[fname] = new
            changed = True
    return changed


def _edit_one_bullet(entry: dict, idx: int, *, backend: str, model: str) -> bool:
    bullets = entry["bullets"]
    typed = ui.editor("Rewrite it yourself, or leave blank and let the model "
                      "sharpen it:", default=bullets[idx]).strip()
    new = typed or reform_bullets([bullets[idx]], entry, backend=backend, model=model)[0]
    if new != bullets[idx]:
        print(f"  - {bullets[idx]}\n  + {new}")
    action = console.ask_choice(f"{entry.get('company') or entry.get('title')} - bullet {idx+1}",
                                [("save", "Save this rewrite"),
                                 ("delete", "Delete this bullet"), ("back", _BACK)])
    if action == "save":
        bullets[idx] = new
        return True
    if action == "delete":
        bullets.pop(idx)
        return True
    return False


def _edit_notes(entry: dict, sec: Section, *, backend: str, model: str) -> bool:
    how = console.ask_choice(
        f"{sec.ref(entry)} - notes",
        [("direct", "Edit the note text directly"),
         ("questions", "Answer questions → distilled note"), ("back", _BACK)])
    if how == "direct":
        new = ui.editor("Notes:", default=entry.get("notes", "")).strip()
        if new != (entry.get("notes") or ""):
            entry["notes"] = new
            return True
        return False
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
    for fname, label, _req in sec.fields:
        entry[fname] = ui.text(label, default=str(fresh.get(fname) or entry.get(fname, ""))).strip()
    if sec.rich:
        entry["bullets"] = _collect_bullets(sec, {**entry, "bullets": fresh.get("bullets")
                                                  or entry.get("bullets") or []},
                                            backend=backend, model=model)
        entry.setdefault("notes", "")
        _collect_notes(entry, sec.ref(entry), sec.name, backend=backend, model=model)
    _preview(sec, entry, title=f"{sec.name} (edited)")
    return ui.confirm("Save these changes?", default=True)


def cmd_edit(root: Path, *, backend: str, model: str) -> bool:
    _, content = _load(root)
    wrote = False
    while True:
        section = _pick_section(content)
        if section in ("cancel", ""):
            return wrote
        if section == "skill":
            wrote = cmd_edit_skills(root) or wrote
            _, content = _load(root)
            continue
        sec = SECTIONS[section]
        items = content.get(sec.key) or []
        idx = _pick_entry(sec, items)
        if idx in ("back", ""):
            continue
        while True:                                  # scope loop for this entry
            path, content = _load(root)              # re-read before any write
            entry = (content.get(sec.key) or [])[idx]
            if not sec.rich:
                changed = _edit_fields(sec, entry)
                if changed:
                    merge.backup(root); _save(path, content); wrote = True
                    print(f"  {sec.ref(entry)}: fields updated")
                break
            scope = _pick_scope(sec, entry)
            if scope in ("back", ""):
                break
            changed = False
            if scope == "whole":
                changed = _edit_whole(sec, entry, backend=backend, model=model)
            elif scope == "allbullets":
                new = reform_bullets(entry["bullets"], entry, backend=backend, model=model)
                for o, n in zip(entry["bullets"], new):
                    if n != o:
                        print(f"  - {o}\n  + {n}")
                take = console.ask_choice("Keep which rewrites?", [(n, n) for n in new],
                                          multi=True, default_values=new)
                merged = [n if n in (take or []) else o
                          for o, n in zip(entry["bullets"], new)]
                changed = merged != entry["bullets"]
                entry["bullets"] = merged
            elif scope.startswith("bullet:"):
                changed = _edit_one_bullet(entry, int(scope.split(":")[1]),
                                           backend=backend, model=model)
            elif scope == "fields":
                changed = _edit_fields(sec, entry)
            elif scope == "notes":
                changed = _edit_notes(entry, sec, backend=backend, model=model)
            if changed:
                merge.backup(root); _save(path, content); wrote = True
                print(f"  {sec.ref(entry)}: saved")
    return wrote
```

Add the skills stub for imports until Task 6:

```python
# entries.py — add
def cmd_edit_skills(root: Path) -> bool:     # fleshed out in Task 6
    print("  /edit skills is not wired yet.")
    return False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest test_entries.py -q -k cmd_edit`
Expected: PASS (8 tests). Adjust the `_pick_*` return contract if a test and the impl disagree on a literal — keep them identical.

- [ ] **Step 5: Add the headless full-screen test**

```python
# test_console.py — add

def test_fullscreen_edit_drills_to_a_bullet_and_reforms(monkeypatch, tmp_path):
    import entries, merge, json as _j
    (tmp_path / "content.json").write_text(_j.dumps({
        "projects": [{"title": "Transit Board", "bullets": ["made a board", "shipped it"],
                      "notes": ""}]}))
    monkeypatch.setattr(entries, "reform_bullets", lambda b, e, **k: ["Built a live transit board"])
    monkeypatch.setattr(merge, "backup", lambda *a, **k: [])

    def disp(line):
        events.append(("cmd", line))
        if line == "/ed":
            entries.cmd_edit(tmp_path, backend="c", model="m")
        return line != "/exit"

    async def script(feed, bridge):
        await feed("/ed\r"); await asyncio.sleep(0.3)
        # section picker: projects (1)  -> it's first row, Enter
        await feed("\r"); await asyncio.sleep(0.2)
        # entry picker: Transit Board -> Enter
        await feed("\r"); await asyncio.sleep(0.2)
        # scope picker: Whole / Reform every / • made a board / • shipped it / ...
        await feed("j"); await feed("j"); await feed("\r")   # to "• made a board"
        await asyncio.sleep(0.2)
        await feed("\r")                     # editor: blank -> model sharpens
        await asyncio.sleep(0.2)
        await feed("\r")                     # action picker: "Save this rewrite" (first row)
        await asyncio.sleep(0.2)
        # back at scope picker -> go "← back"
        for _ in range(6):
            await feed("j")
        await feed("\r"); await asyncio.sleep(0.2)
        # entry picker again -> "← back"
        await feed("j"); await feed("\r"); await asyncio.sleep(0.2)
        # section picker -> "← cancel"
        for _ in range(3):
            await feed("j")
        await feed("\r"); await asyncio.sleep(0.2)
        assert bridge.mode == "command"
        await feed("/exit\r")

    _drive_shell(script)
    saved = _j.loads((tmp_path / "content.json").read_text())
    assert saved["projects"][0]["bullets"][0] == "Built a live transit board"
```

Run: `python3 -m pytest test_console.py -q -k edit_drills`
Expected: PASS (adjust `j` counts to the real row order if needed).

- [ ] **Step 6: Commit**

```bash
git add entries.py test_entries.py test_console.py
git commit -m "feat(entries): /edit - drill-down browser over content.json"
```

---

## Task 6: skills path for `/new` and `/edit`

**Files:**
- Modify: `entries.py`
- Test: `test_entries.py`

**Interfaces:**
- Consumes: `console.ask_choice`, `ui.text`, `ui.editor`, `ui.confirm`, `merge.backup`, `_load`, `_save`.
- Produces: `cmd_new_skill(root: Path) -> bool` (replaces the stub); `cmd_edit_skills(root: Path) -> bool` (replaces the stub).

- [ ] **Step 1: Write the failing test**

```python
# test_entries.py — add

def test_cmd_new_skill_adds_entries_to_a_new_group(tmp_path, monkeypatch):
    _content(tmp_path, {"skills": [{"label": "Research Skills", "entries": ["Interviews"]}]})
    monkeypatch.setattr(merge, "backup", lambda root, **k: [])
    monkeypatch.setattr(console, "ask_choice", lambda *a, **k: "__new__")
    monkeypatch.setattr(ui, "text", lambda *a, **k: "Prototyping")
    monkeypatch.setattr(ui, "editor", lambda *a, **k: "Figma\nProtoPie\nFramer")
    monkeypatch.setattr(ui, "confirm", lambda *a, **k: True)

    assert entries.cmd_new_skill(tmp_path) is True
    saved = _json.loads((tmp_path / "content.json").read_text())
    assert saved["skills"][-1] == {"label": "Prototyping",
                                   "entries": ["Figma", "ProtoPie", "Framer"]}


def test_cmd_new_skill_appends_to_an_existing_group(tmp_path, monkeypatch):
    _content(tmp_path, {"skills": [{"label": "Research Skills", "entries": ["Interviews"]}]})
    monkeypatch.setattr(merge, "backup", lambda root, **k: [])
    monkeypatch.setattr(console, "ask_choice", lambda *a, **k: 0)   # first group
    monkeypatch.setattr(ui, "editor", lambda *a, **k: "Surveys\nDiary Studies")
    monkeypatch.setattr(ui, "confirm", lambda *a, **k: True)

    assert entries.cmd_new_skill(tmp_path) is True
    saved = _json.loads((tmp_path / "content.json").read_text())
    assert saved["skills"][0]["entries"] == ["Interviews", "Surveys", "Diary Studies"]


def test_cmd_edit_skills_renames_a_group_and_drops_entries(tmp_path, monkeypatch):
    _content(tmp_path, {"skills": [{"label": "Research", "entries": ["A", "B", "C"]}]})
    monkeypatch.setattr(merge, "backup", lambda root, **k: [])
    monkeypatch.setattr(console, "ask_choice", iter([
        0,                       # pick group 0
        ["A", "C"],              # keep these entries (multi)
    ]).__next__)
    monkeypatch.setattr(ui, "text", lambda label, default="", **k: "Research Methods")

    assert entries.cmd_edit_skills(tmp_path) is True
    saved = _json.loads((tmp_path / "content.json").read_text())
    assert saved["skills"][0] == {"label": "Research Methods", "entries": ["A", "C"]}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest test_entries.py -q -k skill`
Expected: FAIL — the stubs print and return False, so assertions fail.

- [ ] **Step 3: Write minimal implementation** (replace the two stubs)

```python
# entries.py — replace cmd_new_skill and cmd_edit_skills

def cmd_new_skill(root: Path) -> bool:
    path, content = _load(root)
    groups = content.get("skills") or []
    rows = [(i, g.get("label", "?")) for i, g in enumerate(groups)]
    pick = console.ask_choice("Add skills to which group?",
                              rows + [("__new__", "+ new group")])
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


def cmd_edit_skills(root: Path) -> bool:
    path, content = _load(root)
    groups = content.get("skills") or []
    if not groups:
        print("  No skill groups yet - /new skill first.")
        return False
    gi = console.ask_choice("Edit which group?",
                            [(i, g.get("label", "?")) for i, g in enumerate(groups)]
                            + [("back", _BACK)])
    if gi in ("back", ""):
        return False
    group = groups[gi]
    label = ui.text("Group label", default=group.get("label", "")).strip() or group.get("label", "")
    kept = console.ask_choice(
        "Keep which skills?", [(s, s) for s in group.get("entries", [])],
        multi=True, default_values=group.get("entries", []))
    changed = label != group.get("label") or list(kept) != group.get("entries")
    if not changed:
        return False
    path, content = _load(root)
    merge.backup(root)
    content["skills"][gi] = {"label": label, "entries": list(kept)}
    _save(path, content)
    print(f"  Updated skill group: {label}")
    return True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest test_entries.py -q -k skill`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add entries.py test_entries.py
git commit -m "feat(entries): /new skill and /edit skills paths"
```

---

## Task 7: `recompile.recent_apps()` and `_ago()`

**Files:**
- Modify: `recompile.py`
- Test: `test_recompile.py`

**Interfaces:**
- Produces: `recompile.recent_apps(base) -> list[dict]` — one dict per `<Company>/<Role>` folder holding `content.resume.json` or `content.cv.json`: `{"dir": Path, "ref": str, "mtime": float, "modes": list[str]}`, sorted by `mtime` descending. `[]` for a missing `base`.

- [ ] **Step 1: Write the failing test**

```python
# test_recompile.py — add
import os
import time
import recompile


def _app(base, company, role, *files):
    d = base / company / role
    d.mkdir(parents=True)
    for f in files:
        (d / f).write_text("{}")
    return d


def test_recent_apps_finds_only_recompilable_folders(tmp_path):
    _app(tmp_path, "Acme", "PD", "content.resume.json")
    _app(tmp_path, "Globex", "Lead", "content.resume.json", "content.cv.json")
    _app(tmp_path, "NoJson", "Role", "notes.txt")            # not recompilable
    apps = recompile.recent_apps(tmp_path)
    refs = {a["ref"] for a in apps}
    assert refs == {"Acme · PD", "Globex · Lead"}
    globex = next(a for a in apps if a["ref"] == "Globex · Lead")
    assert sorted(globex["modes"]) == ["cv", "resume"]
    acme = next(a for a in apps if a["ref"] == "Acme · PD")
    assert acme["modes"] == ["resume"]


def test_recent_apps_sorted_newest_first(tmp_path):
    a = _app(tmp_path, "Old", "R", "content.resume.json")
    b = _app(tmp_path, "New", "R", "content.resume.json")
    old = time.time() - 10_000
    os.utime(a / "content.resume.json", (old, old))
    apps = recompile.recent_apps(tmp_path)
    assert [x["ref"] for x in apps] == ["New · R", "Old · R"]


def test_recent_apps_missing_base_is_empty(tmp_path):
    assert recompile.recent_apps(tmp_path / "nope") == []


def test_recent_apps_underscores_become_spaces(tmp_path):
    _app(tmp_path, "Acme_Inc", "Senior_Product_Designer", "content.resume.json")
    assert recompile.recent_apps(tmp_path)[0]["ref"] == "Acme Inc · Senior Product Designer"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest test_recompile.py -q -k recent_apps`
Expected: FAIL — `AttributeError: module 'recompile' has no attribute 'recent_apps'`

- [ ] **Step 3: Write minimal implementation**

```python
# recompile.py — add (near _folder_json_name)

def recent_apps(base) -> list:
    """Saved applications under `base`, newest first. One dict per
    <Company>/<Role> folder that holds a content.resume.json or content.cv.json."""
    base = Path(base).expanduser()
    if not base.is_dir():
        return []
    out = []
    for role_dir in base.glob("*/*/"):
        if not role_dir.is_dir():
            continue
        modes = [m for m, fn in (("resume", "content.resume.json"),
                                 ("cv", "content.cv.json"))
                 if (role_dir / fn).is_file()]
        if not modes:
            continue
        mtime = max((role_dir / ("content.%s.json" % m)).stat().st_mtime for m in modes)
        ref = f"{role_dir.parent.name} · {role_dir.name}".replace("_", " ")
        out.append({"dir": role_dir, "ref": ref, "mtime": mtime, "modes": modes})
    out.sort(key=lambda a: a["mtime"], reverse=True)
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest test_recompile.py -q -k recent_apps`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add recompile.py test_recompile.py
git commit -m "feat(recompile): recent_apps() - saved applications, newest first"
```

---

## Task 8: Shell wiring — `/new`, `/edit`, `/rc`, `/recompile` picker, drop `/project`

**Files:**
- Modify: `shell.py` — `_HELP`, `COMMANDS`, `SHORTCUTS`, add `cmd_new`/`cmd_edit`/`_ago`, rework `cmd_recompile`, delete `cmd_project`.
- Modify: `test_shell.py` — delete `cmd_project` tests; add new ones.

**Interfaces:**
- Consumes: `entries.cmd_new`, `entries.cmd_edit` (Tasks 4-5); `recompile.recent_apps` (Task 7); `console.ask_choice`; `ui.interactive`.
- Produces: `shell.cmd_new(session, args)`, `shell.cmd_edit(session, args)`, `shell._ago(mtime) -> str`; `SHORTCUTS["/rc"] == "/recompile"`; `cmd_recompile` picker branch.

- [ ] **Step 1: Write the failing test**

```python
# test_shell.py — DELETE the six test_cmd_project_* tests, then add:

import time as _time


def test_new_and_edit_dispatch_to_entries(session, monkeypatch):
    seen = {}
    monkeypatch.setattr(shell.entries, "cmd_new",
                        lambda root, *, backend, model, section=None: seen.setdefault("new", (backend, section)) or True)
    monkeypatch.setattr(shell.entries, "cmd_edit",
                        lambda root, *, backend, model: seen.setdefault("edit", backend) or False)
    reloaded = {"n": 0}
    monkeypatch.setattr(shell.Session, "reload", lambda self: reloaded.__setitem__("n", reloaded["n"] + 1))
    shell.dispatch(session, "/new project")
    shell.dispatch(session, "/edit")
    assert seen["new"] == (session.backend, "project")
    assert seen["edit"] == session.backend
    assert reloaded["n"] == 1          # only /new returned True


def test_new_and_edit_shortcuts(session, monkeypatch):
    seen = []
    monkeypatch.setattr(shell.entries, "cmd_new", lambda *a, **k: seen.append("new") or False)
    monkeypatch.setattr(shell.entries, "cmd_edit", lambda *a, **k: seen.append("edit") or False)
    shell.dispatch(session, "/n")
    shell.dispatch(session, "/e")
    assert seen == ["new", "edit"]


def test_rc_is_a_shortcut_for_recompile(session, monkeypatch):
    seen = []
    monkeypatch.setattr(shell, "cmd_recompile", lambda s, a: seen.append(a))
    shell.dispatch(session, "/rc")
    assert seen == [[]]


def test_help_lists_new_and_edit_not_project(session, capsys):
    shell.dispatch(session, "/help")
    out = capsys.readouterr().out
    assert "/new" in out and "/edit" in out
    assert "/project" not in out


def test_recompile_with_no_path_shows_a_picker(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(shell.ui, "interactive", lambda: True)
    app = tmp_path / "Acme" / "PD"
    app.mkdir(parents=True)
    monkeypatch.setattr(shell.recompile, "recent_apps",
                        lambda base: [{"dir": app, "ref": "Acme · PD",
                                       "mtime": _time.time(), "modes": ["resume"]}])
    picked = {}
    monkeypatch.setattr(shell.console, "ask_choice", lambda msg, rows, **k: rows[0][0])
    monkeypatch.setattr(shell.recompile, "recompile_folder",
                        lambda d, **k: picked.setdefault("dir", d) or (d / "x.pdf"))
    s = shell.Session(root=tmp_path, backend="c", model="m")
    shell.cmd_recompile(s, [])
    assert picked["dir"] == app


def test_recompile_with_a_path_skips_the_picker(tmp_path, monkeypatch):
    monkeypatch.setattr(shell.ui, "interactive", lambda: True)
    monkeypatch.setattr(shell.recompile, "recent_apps",
                        lambda base: (_ for _ in ()).throw(AssertionError("picker must not run")))
    monkeypatch.setattr(shell.recompile, "recompile_folder", lambda d, **k: d / "x.pdf")
    s = shell.Session(root=tmp_path, backend="c", model="m")
    shell.cmd_recompile(s, [str(tmp_path / "some" / "dir")])   # no assertion error = pass


def test_ago_formats():
    assert shell._ago(_time.time()) == "just now"
    assert shell._ago(_time.time() - 3 * 86400) == "3d ago"
    assert shell._ago(_time.time() - 21 * 86400) == "3w ago"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest test_shell.py -q`
Expected: FAIL — new tests error (`shell.entries` missing, `_ago` missing, `/project` still listed).

- [ ] **Step 3: Write minimal implementation**

In `shell.py`:

1. Add `import entries` near the other imports.
2. Delete `cmd_project` entirely.
3. Replace the `_HELP` rows for `/project` — remove it; add `/new` and `/edit`:

```python
_HELP = [
    ("/jd",        "j", "<path|url> [flags]", "tailor a resume for one job description"),
    ("/batch",     "b", "<glob|dir|file>",    "score several, then generate the ones you pick"),
    ("/skills",    "s", "",                   "fill in your skills and derive more"),
    ("/new",       "n", "[section]",          "add one entry - model extracts, you confirm"),
    ("/edit",      "e", "",                   "browse content.json and change an entry or a bullet"),
    ("/add",       "a", "[path]",             "add another resume/CV to content.json"),
    ("/recompile", "r", "",                   "re-render a saved application (picker if no path)"),
    ("/status",    "i", "",                   "what is loaded"),
    ("/setup",     "",  "",                   "re-run setup"),
    ("/help",      "h", "",                   "this list"),
    ("/exit",      "q", "",                   "leave"),
]
SHORTCUTS = {f"/{s}": name for name, s, _, _ in _HELP if s}
SHORTCUTS["/rc"] = "/recompile"
```

4. `COMMANDS`: drop `"/project"`, add `"/new"` and `"/edit"`:

```python
COMMANDS = {
    "/jd": "cmd_jd",
    "/skills": "cmd_skills",
    "/new": "cmd_new",
    "/edit": "cmd_edit",
    "/add": "cmd_add",
    "/setup": "cmd_setup",
    "/recompile": "cmd_recompile",
    "/status": "cmd_status",
    "/help": "cmd_help",
}
```

5. Add the wrappers (near `cmd_add`):

```python
def cmd_new(session: Session, args: list) -> None:
    section = args[0] if args else None
    if entries.cmd_new(session.root, backend=session.backend, model=session.model,
                       section=section):
        session.reload()


def cmd_edit(session: Session, args: list) -> None:
    if entries.cmd_edit(session.root, backend=session.backend, model=session.model):
        session.reload()
```

6. `_ago` helper (near `_counts`):

```python
def _ago(mtime: float) -> str:
    secs = max(0, time.time() - mtime)
    if secs < 60:
        return "just now"
    for unit, size in (("w", 7 * 86400), ("d", 86400), ("h", 3600), ("m", 60)):
        if secs >= size:
            return f"{int(secs // size)}{unit} ago"
    return "just now"
```

Add `import time` at the top of `shell.py` if not present.

7. Rework `cmd_recompile`:

```python
def cmd_recompile(session: Session, args: list) -> None:
    """Re-render a saved application. With no path, pick one from the applications
    dir (newest first). A path skips the picker.

    Calls the library functions directly. recompile.main() parses sys.argv, which
    in a REPL is the shell's own argv, not the user's command.
    """
    import recompile
    import recompile_menu

    flags = {a for a in args if a.startswith("-")}
    positional = [a for a in args if not a.startswith("-")]
    mode = "cv" if "--cv" in flags else "resume"

    if positional:
        app_dir = Path(positional[0]).expanduser().resolve()
    elif ui.interactive():
        base = Path(session.output_dir or (session.profile.output_dir if session.profile else "")
                    or (Path.home() / "Documents" / "Applications")).expanduser()
        apps = recompile.recent_apps(base)
        if not apps:
            print(f"  No saved applications under {base}. Pass a folder path.")
            return
        rows = [(a["dir"], f"{a['ref']}   {_ago(a['mtime'])}   {'+'.join(a['modes'])}")
                for a in apps] + [(None, "← cancel")]
        app_dir = console.ask_choice("Recompile which application?", rows)
        if app_dir is None:
            return
        chosen = next(a for a in apps if a["dir"] == app_dir)
        if "--cv" not in flags and chosen["modes"] == ["cv"]:
            mode = "cv"
    else:
        app_dir = Path.cwd()

    if flags & {"-i", "--interactive"}:
        recompile_menu.run_menu(app_dir, root=session.root, mode=mode)
    print(f"  Recompiled: {recompile.recompile_folder(app_dir, root=session.root, mode=mode)}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest test_shell.py -q`
Expected: PASS. `test_help_lists_every_command` still passes (it checks `/jd /batch /skills /add /setup /recompile /status /exit` — all still in `_HELP`).

- [ ] **Step 5: Full suite**

Run: `python3 -m pytest -q`
Expected: PASS. Fix any `cmd_project` references left in other test files (grep `cmd_project`, `/project` across `test_*.py`).

- [ ] **Step 6: Commit**

```bash
git add shell.py test_shell.py
git commit -m "feat(shell): wire /new, /edit, /rc; /recompile picker; drop /project"
```

---

## Self-Review

**Spec coverage:**

| Spec section | Task |
|---|---|
| `entries.py` module + one-way deps | 1 |
| Section registry (7 sections + skill special-case) | 1, 6 |
| Lenient schema | 1 |
| `EXTRACT_ENTRY_SYSTEM`, `REFORM_BULLET_SYSTEM` | 1 |
| `/new` flow: pick, paste, extract, confirm fields, bullets, clarify→notes, preview, backup, append | 4 |
| `/new` manual path (empty blob, no model) | 4 |
| `/new` flat sections skip bullets/notes | 4 |
| `/edit` drill-down: section → entry → scope | 5 |
| `/edit` scopes: whole / every bullet / one bullet / fields / notes (direct=replace, questions=append) | 5 |
| `/edit` `← back` / `← cancel`, empty content message | 5 |
| skills path for `/new` and `/edit` | 6 |
| `reform_bullets` — per-bullet call, keep-on-failure | 3 |
| `merge.backup` once per write, re-read before write | 4, 5 |
| model never overwrites notes | 3, 5 (tests) |
| `recompile.recent_apps` | 7 |
| `/recompile` picker + mode inference + path bypass | 8 |
| `/rc` shortcut, `·n` `·e` | 8 |
| `/project` + `cmd_project` + tests removed | 8 |
| headless `/new` and `/edit` tests | 4, 5 |

No gaps.

**Placeholder scan:** No "TBD"/"handle edge cases"/"similar to Task N". The Task 4 and Task 5 headless tests note that `feed` counts may need aligning to the real prompt order — that is a calibration instruction, not a missing step; the assertions and setup are complete.

**Type consistency:**
- `cmd_new(root, *, backend, model, section=None) -> bool` — consistent across Tasks 4, 8, spec.
- `cmd_edit(root, *, backend, model) -> bool` — consistent across Tasks 5, 8, spec.
- `extract(section, blob, *, backend, model) -> dict` — Tasks 2, 4, 5.
- `reform_bullets(bullets, entry, *, backend, model, hint="") -> list` — Tasks 3, 4, 5.
- `recent_apps(base) -> list[dict{dir,ref,mtime,modes}]` — Tasks 7, 8.
- `_pick_section` → section name | `"cancel"`; `_pick_entry` → int | `"back"`; `_pick_scope` → `"whole"|"allbullets"|f"bullet:{i}"|"fields"|"notes"|"back"` — used identically in Task 5 impl and tests.
- `Section` field names (`name/key/schema/fields/ref/rich`) — Tasks 1, 4, 5, 6.

Consistent.

---

## Execution Handoff

Plan saved to `docs/superpowers/plans/2026-09-05-entries-and-recompile-picker.md`.
