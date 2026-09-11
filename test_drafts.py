"""drafts.py: a draft answer may only restate what the user already wrote."""
import types

import drafts

CONTENT = {
    "experience": [{"company": "Acme", "bullets": ["shipped the design system"],
                    "notes": "40 components, 3 product teams"}],
    "projects": [{"title": "Transit Fare", "bullets": ["cut checkout steps from 7 to 4"],
                  "notes": "12M riders"}],
}


def _profile():
    return types.SimpleNamespace(content=CONTENT)


def test_source_text_is_scoped_to_the_target():
    text = drafts.source_text(_profile(), "project:Transit Fare")
    assert "12M riders" in text
    assert "40 components" not in text        # a different entry must not leak in


def test_general_target_has_no_source():
    assert drafts.source_text(_profile(), "general") == ""


def test_general_target_makes_no_model_call(monkeypatch):
    def boom(**k):
        raise AssertionError("should not call the model")
    monkeypatch.setattr(drafts.llm, "complete_schema", boom)
    assert drafts.draft_for("q", "general", _profile(), backend="openclaw", model="m") == ("", "", [])


def test_verified_draft_is_returned_with_a_citation(monkeypatch):
    monkeypatch.setattr(drafts.llm, "complete_schema", lambda **k: (
        {"draft": "12M riders; checkout cut from 7 steps to 4.",
         "used": ["12M riders", "cut checkout steps from 7 to 4"]}, None))
    text, cite, used = drafts.draft_for("scale?", "project:Transit Fare", _profile(),
                                        backend="openclaw", model="m")
    assert text.startswith("12M riders")
    assert "project:Transit Fare" in cite
    assert used == ["12M riders", "cut checkout steps from 7 to 4"]


def test_draft_with_a_number_absent_from_source_is_rejected(monkeypatch):
    # The 7-to-4 span is a verified, cited span -- but "40%" appears nowhere in the
    # source at all, so a naive span check alone would let this fabricated metric
    # straight through to the editor.
    monkeypatch.setattr(drafts.llm, "complete_schema", lambda **k: (
        {"draft": "Cut checkout steps from 7 to 4, a 40% reduction.",
         "used": ["cut checkout steps from 7 to 4"]}, None))
    assert drafts.draft_for("scale?", "project:Transit Fare", _profile(),
                            backend="openclaw", model="m") == ("", "", [])


def test_draft_with_only_source_numbers_survives(monkeypatch):
    monkeypatch.setattr(drafts.llm, "complete_schema", lambda **k: (
        {"draft": "12M riders; checkout cut from 7 steps to 4.",
         "used": ["12M riders", "cut checkout steps from 7 to 4"]}, None))
    text, cite, used = drafts.draft_for("scale?", "project:Transit Fare", _profile(),
                                        backend="openclaw", model="m")
    assert text
    assert used


def test_unsupported_span_yields_no_draft(monkeypatch):
    # The load-bearing guard: a fluent answer citing text the user never wrote is
    # exactly the fabrication this feature could otherwise introduce, and it would
    # be distilled into content.json where later applications reuse it.
    monkeypatch.setattr(drafts.llm, "complete_schema", lambda **k: (
        {"draft": "Served 50M riders across four countries.",
         "used": ["50M riders across four countries"]}, None))
    assert drafts.draft_for("scale?", "project:Transit Fare", _profile(),
                            backend="openclaw", model="m") == ("", "", [])


def test_empty_used_list_yields_no_draft(monkeypatch):
    monkeypatch.setattr(drafts.llm, "complete_schema", lambda **k: (
        {"draft": "Something plausible.", "used": []}, None))
    assert drafts.draft_for("scale?", "project:Transit Fare", _profile(),
                            backend="openclaw", model="m") == ("", "", [])


def test_model_failure_yields_no_draft(monkeypatch):
    def boom(**k):
        raise drafts.llm.LLMError("no model")
    monkeypatch.setattr(drafts.llm, "complete_schema", boom)
    assert drafts.draft_for("scale?", "project:Transit Fare", _profile(),
                            backend="openclaw", model="m") == ("", "", [])


def test_unknown_target_yields_no_draft(monkeypatch):
    monkeypatch.setattr(drafts.llm, "complete_schema",
                        lambda **k: ({"draft": "x", "used": ["x"]}, None))
    assert drafts.draft_for("q", "project:Nonexistent", _profile(),
                            backend="openclaw", model="m") == ("", "", [])


# ── Education targets (Task 13 made education:<institution> a valid note target) ──

def _edu_profile(edu):
    return types.SimpleNamespace(content={
        "experience": [{"company": "Acme", "bullets": ["shipped the design system"]}],
        "education": edu,
    })


def test_education_source_text_includes_notes_and_coursework():
    prof = _edu_profile([{"institution": "UW",
                          "notes": "senior capstone on transit wayfinding",
                          "coursework": ["Accessible Computing", "Prototyping Studio"]}])
    text = drafts.source_text(prof, "education:UW")
    assert "senior capstone on transit wayfinding" in text
    assert "Accessible Computing" in text
    assert "Prototyping Studio" in text
    assert "shipped the design system" not in text        # a different entry must not leak in


def test_education_target_yields_verified_draft(monkeypatch):
    prof = _edu_profile([{"institution": "UW",
                          "notes": "senior capstone on transit wayfinding"}])
    monkeypatch.setattr(drafts.llm, "complete_schema", lambda **k: (
        {"draft": "Senior capstone on transit wayfinding.",
         "used": ["senior capstone on transit wayfinding"]}, None))
    text, cite, used = drafts.draft_for("your capstone?", "education:UW", prof,
                                        backend="openclaw", model="m")
    assert text.startswith("Senior capstone")
    assert "education:UW" in cite


def test_education_target_without_notes_yields_no_draft(monkeypatch):
    prof = _edu_profile([{"institution": "UW", "degree": "B.Des"}])
    monkeypatch.setattr(drafts.llm, "complete_schema",
                        lambda **k: ({"draft": "x", "used": ["x"]}, None))
    assert drafts.draft_for("q", "education:UW", prof,
                            backend="openclaw", model="m") == ("", "", [])


def test_education_target_rejects_span_from_another_entry(monkeypatch):
    # The verbatim-substring guard must hold for education targets too: a span
    # borrowed from a different entry is exactly the fabrication route it blocks.
    prof = _edu_profile([{"institution": "UW", "notes": "capstone on wayfinding"}])
    monkeypatch.setattr(drafts.llm, "complete_schema", lambda **k: (
        {"draft": "Shipped the design system during my degree.",
         "used": ["shipped the design system"]}, None))
    assert drafts.draft_for("q", "education:UW", prof,
                            backend="openclaw", model="m") == ("", "", [])
