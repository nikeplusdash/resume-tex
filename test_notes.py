"""notes.py: every note lands on an entry the user chose, or on the general bucket."""
import types

import pytest

import notes

CONTENT = {
    "experience": [{"company": "Acme", "title": "Designer", "bullets": [], "notes": ""}],
    "projects": [{"title": "Transit Fare", "bullets": [], "notes": ""}],
    "education": [{"institution": "UW", "degree": "BFA"}],
}


def _profile(content=None):
    return types.SimpleNamespace(content=content if content is not None else CONTENT)


@pytest.fixture(autouse=True)
def _interactive(monkeypatch):
    # resolve_target only prompts on a TTY; pytest's stdin is not one, so every
    # test that exercises the picker must opt in. The non-interactive test
    # overrides this to assert the opposite.
    monkeypatch.setattr(notes.ui, "interactive", lambda: True)


def _pick(monkeypatch, contains: str):
    """Stub ui.select to return whichever tagged option contains `contains`."""
    monkeypatch.setattr(notes.ui, "select",
                        lambda msg, choices: next(c for c in choices if contains in c))


def test_entry_choices_covers_all_three_kinds():
    targets = [t for t, _ in notes.entry_choices(_profile())]
    assert "experience:Acme" in targets
    assert "project:Transit Fare" in targets
    assert "education:UW" in targets          # education had no note target before


def test_entry_choices_includes_title_for_experience():
    labels = dict(notes.entry_choices(_profile()))
    assert labels["experience:Acme"] == "experience — Acme (Designer)"


def test_known_specific_target_is_used_without_prompting(monkeypatch):
    monkeypatch.setattr(notes.ui, "select",
                        lambda *a, **k: pytest_fail_no_prompt())
    assert notes.resolve_target(_profile(), "project:Transit Fare") == "project:Transit Fare"


def pytest_fail_no_prompt():
    raise AssertionError("a known target must not prompt")


def test_general_target_prompts_for_a_choice(monkeypatch):
    _pick(monkeypatch, "Transit Fare")
    assert notes.resolve_target(_profile(), "general") == "project:Transit Fare"


def test_stale_target_prompts_for_a_choice(monkeypatch):
    # An entry renamed since the question was generated must not silently vanish.
    _pick(monkeypatch, "Acme")
    assert notes.resolve_target(_profile(), "project:Deleted Thing") == "experience:Acme"


def test_choosing_general_returns_general(monkeypatch):
    # "general" is also the non-interactive fallback, so a bare == "general"
    # assertion would pass even if the picker were deleted. Prove it was consulted.
    called = []

    def fake_select(msg, choices):
        called.append(choices)
        return next(c for c in choices if notes.GENERAL_LABEL in c)

    monkeypatch.setattr(notes.ui, "select", fake_select)
    assert notes.resolve_target(_profile(), "") == "general"
    assert called, "the picker must be consulted before falling back to general"
    assert any(notes.GENERAL_LABEL in c for c in called[0])
    assert any(notes.SKIP_LABEL in c for c in called[0])


def test_choosing_skip_returns_empty(monkeypatch):
    _pick(monkeypatch, notes.SKIP_LABEL)
    assert notes.resolve_target(_profile(), "") == ""


def test_non_interactive_falls_back_to_general_without_prompting(monkeypatch):
    monkeypatch.setattr(notes.ui, "interactive", lambda: False)
    monkeypatch.setattr(notes.ui, "select",
                        lambda *a, **k: pytest_fail_no_prompt())
    assert notes.resolve_target(_profile(), "general") == "general"


# ── Task: two entries at one company (a promotion) must be individually pickable ──

_PROMOTION = {
    "experience": [
        {"company": "Amazon", "title": "SDE I", "bullets": [], "notes": ""},
        {"company": "Amazon", "title": "SDE III", "bullets": [], "notes": ""},
    ],
}


def test_two_same_company_entries_get_distinguishable_labels():
    labels = [lbl for _, lbl in notes.entry_choices(_profile(_PROMOTION))]
    assert len(labels) == 2
    assert len(set(labels)) == 2            # not collapsed to one indistinguishable row
    assert "SDE I" in labels[0]
    assert "SDE III" in labels[1]


def test_picking_the_second_same_company_entry_resolves_to_its_own_target(monkeypatch):
    # Selecting the SECOND (senior) row must not silently resolve to the first
    # (junior) one via a text-collapsed lookup -- the picker is consulted with two
    # genuinely distinct, individually selectable options, and each resolves to a
    # target that is actually reachable back to the entry it names (see
    # test_note_addressing.py for the end-to-end proof via append_note/source_text).
    seen = {}

    def fake_select(msg, choices):
        seen["choices"] = choices
        return next(c for c in choices if "SDE III" in c)

    monkeypatch.setattr(notes.ui, "select", fake_select)
    target = notes.resolve_target(_profile(_PROMOTION), "")
    assert target == "experience:Amazon::1"
    # Both rows were genuinely offered, not deduplicated away.
    assert sum("Amazon" in c for c in seen["choices"]) == 2


def test_picking_the_first_same_company_entry_resolves_to_its_own_target(monkeypatch):
    monkeypatch.setattr(notes.ui, "select",
                        lambda msg, choices: next(c for c in choices if "SDE I)" in c))
    assert notes.resolve_target(_profile(_PROMOTION), "") == "experience:Amazon::0"


def test_unambiguous_entry_target_has_no_separator():
    targets = [t for t, _ in notes.entry_choices(_profile())]
    assert all(notes.NOTE_TARGET_SEP not in t for t in targets)
    assert "experience:Acme" in targets
