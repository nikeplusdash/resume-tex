import json as _json

import console
import entries
import install
import llm
import merge
import pytest
import tailor
import ui


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


def _text_reply(mapping):
    def fake(*, backend, model, system, user, thinking=None, binary=None, timeout=None):
        # Find the key that appears earliest in the user message (in "Bullet to rewrite:")
        best_match = None
        best_pos = float('inf')
        for key, val in mapping.items():
            pos = user.find(key)
            if pos >= 0 and pos < best_pos:
                best_match = val
                best_pos = pos
        if best_match:
            return llm.Reply(best_match, finish="stop")
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


# ── cmd_new: paste → entry ────────────────────────────────────────────────────

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
    monkeypatch.setattr(ui, "editor", lambda *a, **k: "")          # blank blob, blank extra bullets, blank clarify
    monkeypatch.setattr(ui, "text", lambda label, default="", **k:
                        "Transit Board" if "Title" in label else "")
    _choice = iter(["project", []])
    monkeypatch.setattr(console, "ask_choice", lambda *a, **k: next(_choice))
    monkeypatch.setattr(install, "entry_prompts", lambda *a, **k: {"project:Transit Board": []})
    monkeypatch.setattr(ui, "confirm", lambda *a, **k: True)
    monkeypatch.setattr(ui, "box", lambda *a, **k: None)
    monkeypatch.setattr(merge, "backup", lambda root, **k: [])

    assert entries.cmd_new(tmp_path, backend="c", model="m") is True
    saved = _json.loads((tmp_path / "content.json").read_text())
    assert saved["projects"][0]["title"] == "Transit Board"


# ── cmd_edit: drill-down over content.json ────────────────────────────────────
#
# Nav contract (calibrated to the real picker call sequence):
#   _pick_scope / _pick_entry loops exit on the literal "back";
#   the outer section loop exits on the literal "cancel".
# So every drill-in queue ends "back" (leave scope), "back" (leave entry
# picker), "cancel" (leave /edit) -- the brief's queues were one short and
# used "cancel" for the inner loops.

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
        0,                           # entry index
        "bullet:0",                  # scope: first bullet
        "save",                      # Save the rewrite
        "back",                      # leave scope picker
        "back",                      # leave entry picker
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
    _ec = iter(["experience", 0, "bullet:1", "delete", "back", "back", "cancel"]).__next__
    monkeypatch.setattr(console, "ask_choice", lambda *a, **k: _ec())
    monkeypatch.setattr(ui, "editor", lambda *a, **k: "ran tests")
    entries.cmd_edit(tmp_path, backend="c", model="m")
    saved = _json.loads((tmp_path / "content.json").read_text())
    assert saved["experience"][0]["bullets"] == ["did onboarding"]


def test_cmd_edit_header_fields(tmp_path, monkeypatch):
    _edit_content(tmp_path)
    monkeypatch.setattr(merge, "backup", lambda root, **k: [])
    _ec = iter(["experience", 0, "fields", "back", "back", "cancel"]).__next__
    monkeypatch.setattr(console, "ask_choice", lambda *a, **k: _ec())
    monkeypatch.setattr(ui, "text",
                        lambda label, default="", **k: "Acme Corp" if "Company" in label else default)
    entries.cmd_edit(tmp_path, backend="c", model="m")
    saved = _json.loads((tmp_path / "content.json").read_text())
    assert saved["experience"][0]["company"] == "Acme Corp"
    assert saved["experience"][0]["title"] == "PD"


def test_cmd_edit_notes_direct_edit_replaces(tmp_path, monkeypatch):
    _edit_content(tmp_path)
    monkeypatch.setattr(merge, "backup", lambda root, **k: [])
    _ec = iter(["experience", 0, "notes", "direct", "back", "back", "cancel"]).__next__
    monkeypatch.setattr(console, "ask_choice", lambda *a, **k: _ec())
    monkeypatch.setattr(ui, "editor", lambda label, default="", **k: "rewritten notes")
    entries.cmd_edit(tmp_path, backend="c", model="m")
    saved = _json.loads((tmp_path / "content.json").read_text())
    assert saved["experience"][0]["notes"] == "rewritten notes"


def test_cmd_edit_notes_questions_append(tmp_path, monkeypatch):
    _edit_content(tmp_path)
    monkeypatch.setattr(merge, "backup", lambda root, **k: [])
    monkeypatch.setattr(install, "entry_prompts", lambda *a, **k: {"experience:Acme · PD": ["q?"]})
    monkeypatch.setattr(tailor, "distill_note", lambda *a, **k: "12-week engagement.")
    _ec = iter(["experience", 0, "notes", "questions", "back", "back", "cancel"]).__next__
    monkeypatch.setattr(console, "ask_choice", lambda *a, **k: _ec())
    _eed = iter(["ran 12 weeks", ""]).__next__
    monkeypatch.setattr(ui, "editor", lambda *a, **k: _eed())
    entries.cmd_edit(tmp_path, backend="c", model="m")
    saved = _json.loads((tmp_path / "content.json").read_text())
    assert saved["experience"][0]["notes"] == "team of 4\n12-week engagement."


def test_pick_scope_omits_reform_every_bullet_without_a_bullets_key(monkeypatch):
    # A rich entry hand-edited in a text editor can lose the "bullets" key; the
    # "Reform every bullet" row would then KeyError out of /edit when picked.
    seen = {}
    monkeypatch.setattr(console, "ask_choice",
                        lambda label, rows, **k: seen.setdefault("rows", rows) or "back")
    entries._pick_scope(entries.SECTIONS["experience"], {"company": "Acme", "title": "PD"})
    values = [r[0] for r in seen["rows"]]
    assert "allbullets" not in values
    assert values[0] == "whole" and "fields" in values and "notes" in values


def test_cmd_edit_allbullets_on_entry_without_bullets_is_a_safe_noop(tmp_path, monkeypatch):
    _content(tmp_path, {"experience": [{"company": "Acme", "title": "PD", "dates": "2024"}]})
    hit = {"backup": False}
    monkeypatch.setattr(merge, "backup", lambda root, **k: hit.__setitem__("backup", True))
    monkeypatch.setattr(entries, "reform_bullets",
                        lambda *a, **k: pytest.fail("no bullets to reform"))
    _ec = iter(["experience", 0, "allbullets", "back", "back", "cancel"]).__next__
    monkeypatch.setattr(console, "ask_choice", lambda *a, **k: _ec())
    assert entries.cmd_edit(tmp_path, backend="c", model="m") is False
    assert hit["backup"] is False


def test_cmd_edit_ctrl_c_on_a_picker_exits_without_writing(tmp_path, monkeypatch):
    _edit_content(tmp_path)
    before = (tmp_path / "content.json").read_text()
    hit = {"backup": False}
    monkeypatch.setattr(merge, "backup", lambda root, **k: hit.__setitem__("backup", True))
    monkeypatch.setattr(console, "ask_choice", lambda *a, **k: console.CHOICE_CANCELLED)
    assert entries.cmd_edit(tmp_path, backend="c", model="m") is False
    assert hit["backup"] is False
    assert (tmp_path / "content.json").read_text() == before


def test_edit_one_bullet_ctrl_c_neither_saves_nor_deletes(monkeypatch):
    monkeypatch.setattr(entries, "reform_bullets", lambda b, e, **k: ["MODEL REWRITE"])
    monkeypatch.setattr(ui, "editor", lambda *a, **k: "")
    monkeypatch.setattr(console, "ask_choice", lambda *a, **k: console.CHOICE_CANCELLED)
    entry = {"company": "Acme", "bullets": ["orig one", "orig two"]}
    assert entries._edit_one_bullet(entry, 0, backend="c", model="m") is False
    assert entry["bullets"] == ["orig one", "orig two"]


def test_cmd_edit_allbullets_ctrl_c_on_keep_rewrites_writes_nothing(tmp_path, monkeypatch):
    # The "Keep which rewrites?" picker is multi with default_values = every
    # rewrite. Ctrl-C (CHOICE_CANCELLED) must keep the originals and skip the
    # write entirely -- no merge.backup, cmd_edit returns False.
    _edit_content(tmp_path)
    before = (tmp_path / "content.json").read_text()
    monkeypatch.setattr(entries, "reform_bullets",
                        lambda b, e, **k: ["REWRITE A", "REWRITE B"])
    hit = {"backup": False}
    monkeypatch.setattr(merge, "backup", lambda root, **k: hit.__setitem__("backup", True))
    _ec = iter([
        "experience", 0, "allbullets",
        console.CHOICE_CANCELLED,       # Ctrl-C on "Keep which rewrites?"
        "back", "back", "cancel",
    ]).__next__
    monkeypatch.setattr(console, "ask_choice", lambda *a, **k: _ec())
    assert entries.cmd_edit(tmp_path, backend="c", model="m") is False
    assert hit["backup"] is False
    assert (tmp_path / "content.json").read_text() == before


def test_collect_bullets_ctrl_c_on_accept_rewrites_keeps_the_originals(monkeypatch):
    monkeypatch.setattr(entries, "reform_bullets",
                        lambda b, e, **k: ["SHARPENED ONE", "SHARPENED TWO"])
    monkeypatch.setattr(ui, "editor", lambda *a, **k: "")        # no extra bullets
    monkeypatch.setattr(ui, "confirm", lambda *a, **k: True)     # yes, sharpen
    _ac = iter([
        ["one", "two"],                # "Keep which bullets?" -> keep both
        console.CHOICE_CANCELLED,       # Ctrl-C on "Take which rewrites?"
    ]).__next__
    monkeypatch.setattr(console, "ask_choice", lambda *a, **k: _ac())
    section = entries.SECTIONS["experience"]
    kept = entries._collect_bullets(section, {"bullets": ["one", "two"]},
                                    backend="c", model="m")
    assert kept == ["one", "two"]      # none of the rewrites applied


def test_cmd_edit_notes_direct_blank_clears_the_note(tmp_path, monkeypatch):
    _edit_content(tmp_path)
    backups = []
    monkeypatch.setattr(merge, "backup", lambda root, **k: backups.append(1) or [])
    _ec = iter(["experience", 0, "notes", "direct", "back", "back", "cancel"]).__next__
    monkeypatch.setattr(console, "ask_choice", lambda *a, **k: _ec())
    monkeypatch.setattr(ui, "editor", lambda *a, **k: "")   # select-all-delete -> blank
    assert entries.cmd_edit(tmp_path, backend="c", model="m") is True
    saved = _json.loads((tmp_path / "content.json").read_text())
    assert "notes" not in saved["experience"][0]
    assert backups == [1]


def test_cmd_edit_whole_entry_reforms_fields_bullets_and_notes(tmp_path, monkeypatch):
    _edit_content(tmp_path)
    monkeypatch.setattr(entries, "extract", lambda *a, **k: {
        "company": "Acme", "title": "Senior PD", "dates": "2024", "location": "NYC",
        "bullets": ["reworked onboarding"]})
    monkeypatch.setattr(entries, "reform_bullets",
                        lambda b, e, **k: ["Reworked onboarding, cut time 40%"])
    monkeypatch.setattr(install, "entry_prompts", lambda *a, **k: {})
    monkeypatch.setattr(tailor, "distill_note", lambda *a, **k: "Team of 4.")
    monkeypatch.setattr(ui, "box", lambda *a, **k: None)
    monkeypatch.setattr(ui, "text", lambda label, default="", **k: default)
    monkeypatch.setattr(ui, "confirm", lambda msg, **k: True)
    backups = []
    monkeypatch.setattr(merge, "backup", lambda root, **k: backups.append(1) or [])
    _ed = iter(["corrections and extra context", "", "ran a team of four", ""]).__next__
    monkeypatch.setattr(ui, "editor", lambda *a, **k: _ed())
    _ec = iter([
        "experience", 0, "whole",
        ["reworked onboarding"],                       # _collect_bullets keep
        ["Reworked onboarding, cut time 40%"],         # _collect_bullets accept
        "back", "back", "cancel",
    ]).__next__
    monkeypatch.setattr(console, "ask_choice", lambda *a, **k: _ec())

    assert entries.cmd_edit(tmp_path, backend="c", model="m") is True
    e = _json.loads((tmp_path / "content.json").read_text())["experience"][0]
    assert e["title"] == "Senior PD"                                   # re-extracted field
    assert e["bullets"] == ["Reworked onboarding, cut time 40%"]       # reformed
    assert e["notes"] == "team of 4\nTeam of 4."                       # appended, not replaced
    assert backups == [1]


def test_cmd_edit_whole_entry_declined_confirm_writes_nothing(tmp_path, monkeypatch):
    _edit_content(tmp_path)
    before = (tmp_path / "content.json").read_text()
    monkeypatch.setattr(entries, "extract", lambda *a, **k: {
        "company": "Acme", "title": "Senior PD", "dates": "2024", "location": "NYC",
        "bullets": ["reworked onboarding"]})
    monkeypatch.setattr(entries, "reform_bullets", lambda b, e, **k: list(b))
    monkeypatch.setattr(install, "entry_prompts", lambda *a, **k: {})
    monkeypatch.setattr(ui, "box", lambda *a, **k: None)
    monkeypatch.setattr(ui, "text", lambda label, default="", **k: default)
    monkeypatch.setattr(ui, "confirm",
                        lambda msg, **k: "Save these changes" not in msg)
    hit = {"backup": False}
    monkeypatch.setattr(merge, "backup", lambda root, **k: hit.__setitem__("backup", True))
    _ed = iter(["corrections and extra context", "", ""]).__next__
    monkeypatch.setattr(ui, "editor", lambda *a, **k: _ed())
    _ec = iter([
        "experience", 0, "whole",
        ["reworked onboarding"], ["reworked onboarding"],
        "back", "back", "cancel",
    ]).__next__
    monkeypatch.setattr(console, "ask_choice", lambda *a, **k: _ec())

    assert entries.cmd_edit(tmp_path, backend="c", model="m") is False
    assert hit["backup"] is False
    assert (tmp_path / "content.json").read_text() == before


def test_cmd_edit_reform_every_bullet_writes_accepted_subset(tmp_path, monkeypatch):
    _edit_content(tmp_path)
    monkeypatch.setattr(entries, "reform_bullets",
                        lambda b, e, **k: ["NEW onboarding", "NEW tests"])
    backups = []
    monkeypatch.setattr(merge, "backup", lambda root, **k: backups.append(1) or [])
    _ec = iter([
        "experience", 0, "allbullets",
        ["NEW onboarding"],                # accept only the first rewrite
        "back", "back", "cancel",
    ]).__next__
    monkeypatch.setattr(console, "ask_choice", lambda *a, **k: _ec())

    assert entries.cmd_edit(tmp_path, backend="c", model="m") is True
    saved = _json.loads((tmp_path / "content.json").read_text())
    assert saved["experience"][0]["bullets"] == ["NEW onboarding", "ran tests"]
    assert saved["education"][0]["gpa"] == "3.9"        # other entries untouched
    assert backups == [1]


def test_cmd_edit_flat_section_edits_fields_directly(tmp_path, monkeypatch):
    _edit_content(tmp_path)
    monkeypatch.setattr(merge, "backup", lambda root, **k: [])
    _ec = iter(["education", 0, "back", "cancel"]).__next__   # no scope step for flat
    monkeypatch.setattr(console, "ask_choice", lambda *a, **k: _ec())
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


# ── cmd_new_skill and cmd_edit_skills ─────────────────────────────────────────

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
    _it = iter([
        0,                       # pick group 0
        ["A", "C"],              # keep these entries (multi)
    ])
    monkeypatch.setattr(console, "ask_choice", lambda *a, **k: next(_it))
    monkeypatch.setattr(ui, "text", lambda label, default="", **k: "Research Methods")

    assert entries.cmd_edit_skills(tmp_path) is True
    saved = _json.loads((tmp_path / "content.json").read_text())
    assert saved["skills"][0] == {"label": "Research Methods", "entries": ["A", "C"]}


def test_derive_links_fills_short_link_from_the_url():
    e = {"link": "https://www.example.edu/program/"}
    entries._derive_links(e)
    assert e["short_link"] == "example.edu/program"

    kept = {"link": "https://x.com/a", "short_link": "my site"}
    entries._derive_links(kept)
    assert kept["short_link"] == "my site"            # an explicit display text is left alone

    empty = {"link": "", "short_link": "stale"}
    entries._derive_links(empty)
    assert "link" not in empty and "short_link" not in empty


def test_experience_and_project_sections_expose_a_link_field():
    assert any(f[0] == "link" for f in entries.SECTIONS["experience"].fields)
    assert any(f[0] == "link" for f in entries.SECTIONS["project"].fields)


def test_confirm_fields_derives_the_short_link(monkeypatch):
    answers = iter(["Acme", "PD", "2024", "", "https://acme.example/team"])
    monkeypatch.setattr(entries.ui, "text", lambda *a, **k: next(answers))
    out = entries._confirm_fields(entries.SECTIONS["experience"], {})
    assert out["link"] == "https://acme.example/team"
    assert out["short_link"] == "acme.example/team"
