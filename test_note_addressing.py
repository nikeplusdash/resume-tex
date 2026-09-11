"""End-to-end note addressing: notes.entry_choices -> Profile.append_note / drafts.source_text.

A promotion is two experience entries at one company (or, more rarely, two
projects sharing a title). Their plain "kind:name" target cannot say which entry
a note is about, so notes.entry_choices hands out a disambiguated
"kind:name::<index>" target ONLY when a collision exists (profile.NOTE_TARGET_SEP).
These tests prove the property that actually matters: a disambiguated target
picked for the SECOND entry is reachable back to that entry specifically, both
for writing a note (Profile.append_note) and for reading source text for a draft
(drafts.source_text) -- and that a single, unambiguous entry is entirely
unaffected: no "::" anywhere in sight.
"""
import json

import pytest

import drafts
import notes
import tailor
from profile import NOTE_TARGET_SEP, Profile

CONSTANT = {"name": "Alex Rivera", "email": "a@x.com", "phone": "+1", "linkedin": "in/a",
            "location": "Seattle, WA", "portfolios": "", "solo_worker": False,
            "banned_summary_anchors": [], "skills_title": "Skills"}


def _promotion_profile():
    content = {
        "portfolio_link": "", "summary": "",
        "experience": [
            {"company": "Amazon", "title": "SDE I", "dates": "2018-2020", "location": "S",
             "bullets": ["Built the junior thing."], "notes": ""},
            {"company": "Amazon", "title": "SDE III", "dates": "2020-2023", "location": "S",
             "bullets": ["Led the senior thing."], "notes": ""},
        ],
        "projects": [],
        "skills": [],
    }
    return Profile.from_dicts(CONSTANT, content)


def _two_projects_same_title_profile():
    content = {
        "portfolio_link": "", "summary": "",
        "experience": [],
        "projects": [
            {"title": "Redesign", "bullets": ["Shipped the first redesign."], "notes": ""},
            {"title": "Redesign", "bullets": ["Shipped the second redesign."], "notes": ""},
        ],
        "skills": [],
    }
    return Profile.from_dicts(CONSTANT, content)


# ── the picker offers a distinct, reachable target for each colliding entry ────

def _pick(monkeypatch, contains: str):
    monkeypatch.setattr(notes.ui, "interactive", lambda: True)
    monkeypatch.setattr(notes.ui, "select",
                        lambda msg, choices: next(c for c in choices if contains in c))


def test_picking_the_second_entry_reads_the_second_entrys_source_text(monkeypatch):
    profile = _promotion_profile()
    _pick(monkeypatch, "SDE III")
    target = notes.resolve_target(profile, "")
    assert target == "experience:Amazon::1"
    text = drafts.source_text(profile, target)
    assert "Led the senior thing." in text
    assert "Built the junior thing." not in text


def test_picking_the_first_entry_reads_the_first_entrys_source_text(monkeypatch):
    profile = _promotion_profile()
    _pick(monkeypatch, "SDE I)")     # closing paren excludes the "SDE III)" label
    target = notes.resolve_target(profile, "")
    assert target == "experience:Amazon::0"
    text = drafts.source_text(profile, target)
    assert "Built the junior thing." in text
    assert "Led the senior thing." not in text


def test_append_note_on_disambiguated_target_writes_only_the_second_entry(tmp_path):
    (tmp_path / "constant.json").write_text(json.dumps(CONSTANT))
    profile = _promotion_profile()
    (tmp_path / "content.json").write_text(json.dumps(profile.content))
    profile = Profile.load(tmp_path)

    profile.append_note("experience:Amazon::1", "Owned the migration to EKS.")

    reloaded = json.loads((tmp_path / "content.json").read_text())
    first, second = reloaded["experience"]
    assert first["notes"] == ""                                 # untouched
    assert second["notes"] == "Owned the migration to EKS."


def test_append_note_on_disambiguated_target_first_index_writes_only_the_first(tmp_path):
    (tmp_path / "constant.json").write_text(json.dumps(CONSTANT))
    profile = _promotion_profile()
    (tmp_path / "content.json").write_text(json.dumps(profile.content))
    profile = Profile.load(tmp_path)

    profile.append_note("experience:Amazon::0", "Shipped the first checkout flow.")

    reloaded = json.loads((tmp_path / "content.json").read_text())
    first, second = reloaded["experience"]
    assert first["notes"] == "Shipped the first checkout flow."
    assert second["notes"] == ""                                 # untouched


# ── an unambiguous single entry is entirely unaffected ─────────────────────────

def test_unambiguous_entry_uses_the_plain_canonical_form_throughout():
    content = {
        "portfolio_link": "", "summary": "",
        "experience": [{"company": "Northgate", "title": "PD", "dates": "d", "location": "S",
                        "bullets": ["Shipped the design system."], "notes": ""}],
        "projects": [], "skills": [],
    }
    profile = Profile.from_dicts(CONSTANT, content)
    targets = [t for t, _ in notes.entry_choices(profile)]
    assert targets == ["experience:Northgate"]
    assert NOTE_TARGET_SEP not in targets[0]
    assert drafts.source_text(profile, "experience:Northgate") == "Shipped the design system."


# ── the documented fallback: a plain, colliding target still validates and still
#    resolves to the FIRST entry -- pinned explicitly, not an accidental silent
#    failure ───────────────────────────────────────────────────────────────────

def test_plain_target_for_a_colliding_company_still_validates_in_known_note_targets():
    profile = _promotion_profile()
    known = tailor._known_note_targets(profile)
    assert "experience:Amazon" in known                 # the model only ever produces this
    assert "experience:Amazon::0" in known
    assert "experience:Amazon::1" in known


def test_plain_target_for_a_colliding_company_resolves_to_the_first_entry(tmp_path):
    (tmp_path / "constant.json").write_text(json.dumps(CONSTANT))
    profile = _promotion_profile()
    (tmp_path / "content.json").write_text(json.dumps(profile.content))
    profile = Profile.load(tmp_path)

    # A model-produced gap_questions[i]["target"] is always the plain form --
    # this is the documented, pinned fallback, not a bug.
    profile.append_note("experience:Amazon", "Some note from a plain target.")

    reloaded = json.loads((tmp_path / "content.json").read_text())
    first, second = reloaded["experience"]
    assert first["notes"] == "Some note from a plain target."
    assert second["notes"] == ""


def test_notes_resolve_target_accepts_a_plain_colliding_target_without_prompting(monkeypatch):
    # notes.resolve_target must not force a prompt for a target that is already
    # valid, even though it collides -- Profile.append_note's fallback handles it.
    monkeypatch.setattr(notes.ui, "select",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("a known target must not prompt")))
    profile = _promotion_profile()
    assert notes.resolve_target(profile, "experience:Amazon") == "experience:Amazon"


# ── two projects sharing a title behave the same way ────────────────────────────

def test_two_projects_sharing_a_title_are_individually_addressable(monkeypatch):
    profile = _two_projects_same_title_profile()
    targets = [t for t, _ in notes.entry_choices(profile)]
    assert targets == ["project:Redesign::0", "project:Redesign::1"]

    assert "Shipped the first redesign." in drafts.source_text(profile, "project:Redesign::0")
    assert "Shipped the second redesign." in drafts.source_text(profile, "project:Redesign::1")


def test_append_note_on_second_of_two_same_titled_projects(tmp_path):
    (tmp_path / "constant.json").write_text(json.dumps(CONSTANT))
    profile = _two_projects_same_title_profile()
    (tmp_path / "content.json").write_text(json.dumps(profile.content))
    profile = Profile.load(tmp_path)

    profile.append_note("project:Redesign::1", "Cut checkout steps from 7 to 4.")

    reloaded = json.loads((tmp_path / "content.json").read_text())
    first, second = reloaded["projects"]
    assert first["notes"] == ""
    assert second["notes"] == "Cut checkout steps from 7 to 4."
