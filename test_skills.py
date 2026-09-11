"""skills.py: a derived skill must trace back to something the user actually wrote."""
import json

import skills

CONTENT = {
    "experience": [{"company": "Acme", "title": "Designer",
                    "bullets": ["built a 40-component library"], "notes": "tokenised the theme layer"}],
    "projects": [{"title": "Transit Fare", "bullets": ["cut checkout steps from 7 to 4"], "notes": ""}],
    "skills": [{"label": "Design", "entries": ["Figma"]}],
}


def _cand(skill, evidence, group="Design", strength="direct"):
    return {"skill": skill, "group": group, "evidence": evidence,
            "source_ref": "experience:Acme", "strength": strength}


def test_corpus_includes_bullets_and_notes():
    text = skills.corpus_text(CONTENT)
    assert "40-component library" in text
    assert "tokenised the theme layer" in text
    assert "cut checkout steps" in text


def test_candidate_without_verbatim_evidence_is_dropped(monkeypatch):
    # The load-bearing guard. A model can produce a plausible skill with invented
    # evidence; if the span is not in the corpus, the skill is not real.
    payload = {"candidates": [
        _cand("Design Systems", "built a 40-component library"),
        _cand("Machine Learning", "led the ML platform team"),   # never written
    ]}
    monkeypatch.setattr(skills.llm, "complete_schema", lambda **k: (payload, None))
    got = skills.derive(CONTENT, backend="openclaw", model="m")
    assert [c["skill"] for c in got] == ["Design Systems"]


def test_evidence_from_a_note_counts(monkeypatch):
    payload = {"candidates": [_cand("Design Tokens", "tokenised the theme layer")]}
    monkeypatch.setattr(skills.llm, "complete_schema", lambda **k: (payload, None))
    assert [c["skill"] for c in skills.derive(CONTENT, backend="openclaw", model="m")] == ["Design Tokens"]


def test_already_present_skill_is_filtered(monkeypatch):
    payload = {"candidates": [_cand("figma", "built a 40-component library")]}
    monkeypatch.setattr(skills.llm, "complete_schema", lambda **k: (payload, None))
    assert skills.derive(CONTENT, backend="openclaw", model="m") == []


def test_rejected_skill_is_not_offered_again(monkeypatch):
    payload = {"candidates": [_cand("Design Systems", "built a 40-component library")]}
    monkeypatch.setattr(skills.llm, "complete_schema", lambda **k: (payload, None))
    got = skills.derive(CONTENT, backend="openclaw", model="m", rejected=["design systems"])
    assert got == []


def test_derive_returns_empty_on_model_failure(monkeypatch):
    def boom(**k):
        raise skills.llm.LLMError("no model")
    monkeypatch.setattr(skills.llm, "complete_schema", boom)
    assert skills.derive(CONTENT, backend="openclaw", model="m") == []


def test_apply_adds_to_existing_group():
    out = skills.apply(CONTENT, [_cand("Design Systems", "x", group="Design")])
    design = [g for g in out["skills"] if g["label"] == "Design"][0]
    assert design["entries"] == ["Figma", "Design Systems"]


def test_apply_creates_a_new_group():
    out = skills.apply(CONTENT, [_cand("Python", "x", group="Engineering")])
    labels = [g["label"] for g in out["skills"]]
    assert "Engineering" in labels
    eng = [g for g in out["skills"] if g["label"] == "Engineering"][0]
    assert eng["entries"] == ["Python"]


def test_apply_does_not_mutate_the_input():
    before = json.dumps(CONTENT, sort_keys=True)
    skills.apply(CONTENT, [_cand("Python", "x", group="Engineering")])
    assert json.dumps(CONTENT, sort_keys=True) == before


# --- fix round 1: the guard is only as strong as these properties -------------

MULTI = {
    "experience": [{"company": "Acme", "title": "Designer",
                    "bullets": ["shipped the design system foundations",
                                "migrated legacy screens to tokens"],
                    "notes": "ran the weekly design critique session"}],
    "projects": [{"title": "Transit Fare",
                  "bullets": ["cut checkout steps from seven to four"], "notes": ""}],
    "skills": [{"label": "Design", "entries": ["Figma"]}],
}


def _derive(content, payload, monkeypatch, **kw):
    monkeypatch.setattr(skills.llm, "complete_schema", lambda **k: (payload, None))
    return skills.derive(content, backend="openclaw", model="m", **kw)


def test_evidence_spanning_two_bullets_is_rejected(monkeypatch):
    # "design system foundations\nmigrated legacy" exists only in the joined blob,
    # never in one bullet -- finding A.
    span = "system foundations\nmigrated legacy screens"
    assert span in skills.corpus_text(MULTI)
    payload = {"candidates": [_cand("Bogus", span, group="Design")]}
    assert _derive(MULTI, payload, monkeypatch) == []


def test_evidence_spanning_two_entries_is_rejected(monkeypatch):
    span = "design critique session\ncut checkout steps"
    assert span in skills.corpus_text(MULTI)
    payload = {"candidates": [_cand("Bogus", span, group="Design")]}
    assert _derive(MULTI, payload, monkeypatch) == []


def test_short_evidence_is_rejected_even_though_it_is_in_the_corpus(monkeypatch):
    # "design system" is a verbatim substring of a real bullet but is under the
    # 15-char / 3-word floor -- finding B.
    assert "design system" in skills.corpus_text(MULTI)
    assert len("design system") < skills.MIN_EVIDENCE_CHARS
    payload = {"candidates": [_cand("Machine Learning", "design system", group="Design")]}
    assert _derive(MULTI, payload, monkeypatch) == []


def test_guard_is_case_sensitive(monkeypatch):
    # Same span, different case -- a reflowed / retyped span must not pass.
    payload = {"candidates": [_cand("Bogus", "Shipped The Design System Foundations",
                                    group="Design")]}
    assert _derive(MULTI, payload, monkeypatch) == []


def test_source_ref_is_set_from_the_containing_entry_and_overrides_the_model(monkeypatch):
    # Evidence is verbatim from the Transit Fare project; the model claims Acme.
    payload = {"candidates": [{"skill": "Checkout Flow", "group": "Design",
                               "evidence": "cut checkout steps from seven to four",
                               "source_ref": "experience:Acme", "strength": "direct"}]}
    got = _derive(MULTI, payload, monkeypatch)
    assert len(got) == 1
    assert got[0]["source_ref"] == "project:Transit Fare"


def test_strength_is_coerced_to_implied_when_name_absent_from_evidence(monkeypatch):
    payload = {"candidates": [
        _cand("Design Systems", "shipped the design system foundations", group="Design"),
        _cand("design system foundations", "shipped the design system foundations",
              group="Design"),
    ]}
    got = {c["skill"]: c["strength"] for c in _derive(MULTI, payload, monkeypatch)}
    assert got["Design Systems"] == "implied"          # name not in its evidence
    assert got["design system foundations"] == "direct"  # name is in its evidence


def _run_setup(tmp_path, monkeypatch, confirm):
    (tmp_path / "content.json").write_text(json.dumps(CONTENT))
    (tmp_path / "constant.json").write_text(json.dumps({}))
    monkeypatch.setattr(skills, "derive",
                        lambda *a, **k: [_cand("Skill One", "x"), _cand("Skill Two", "y")])
    monkeypatch.setattr(skills.ui, "evidence_checkbox", lambda msg, cands, **k: [cands[0]])
    monkeypatch.setattr(skills.ui, "confirm", confirm)
    import install
    written = []
    monkeypatch.setattr(install, "write_constant_key",
                        lambda root, key, value: written.append((key, value)))
    return written


def test_run_backs_up_content_json_before_writing(tmp_path, monkeypatch):
    # A Ctrl-C mid-write leaves content.json truncated; without a snapshot first
    # there is nothing to recover from, the exact loss this project's author
    # already suffered once. merge.run and bootstrap_step both snapshot first --
    # skills.run must too.
    _run_setup(tmp_path, monkeypatch, lambda msg, **k: False)
    before = {p.name for p in tmp_path.iterdir()}
    skills.run(tmp_path, backend="openclaw", model="m")
    after = {p.name for p in tmp_path.iterdir()}
    backups = after - before
    assert any(name.startswith("content.json.") and name.endswith(".bak") for name in backups)


def test_unticked_candidates_not_rejected_when_confirm_declined(tmp_path, monkeypatch):
    written = _run_setup(tmp_path, monkeypatch, lambda msg, **k: False)
    skills.run(tmp_path, backend="openclaw", model="m")
    assert written == []


def test_unticked_candidates_rejected_only_when_confirmed(tmp_path, monkeypatch):
    written = _run_setup(tmp_path, monkeypatch,
                         lambda msg, **k: msg.startswith("Stop offering"))
    skills.run(tmp_path, backend="openclaw", model="m")
    assert written == [("rejected_skills", ["Skill Two"])]


def test_apply_normalises_whitespace_when_deduping():
    out = skills.apply(CONTENT, [_cand("figma ", "x", group="Design")])
    design = [g for g in out["skills"] if g["label"] == "Design"][0]
    assert design["entries"] == ["Figma"]


# --- fix round 2: the evidence floor must not silently drop legitimate short
#     spans that name their own skill ------------------------------------------

RN = {
    "experience": [{"company": "Acme", "title": "Engineer",
                    "bullets": ["shipped the React Native app to both app stores"],
                    "notes": "owned the release train"}],
    "projects": [],
    "skills": [{"label": "Engineering", "entries": []}],
}


def test_short_span_that_names_its_skill_survives(monkeypatch):
    payload = {"candidates": [_cand("React Native", "React Native", group="Engineering")]}
    got = _derive(RN, payload, monkeypatch)
    assert [c["skill"] for c in got] == ["React Native"]
    assert got[0]["strength"] == "direct"


def test_trivial_span_not_naming_its_skill_is_still_rejected(monkeypatch):
    payload = {"candidates": [_cand("Machine Learning", "the", group="Engineering")]}
    assert _derive(RN, payload, monkeypatch) == []


def test_two_word_span_not_naming_its_skill_is_still_rejected(monkeypatch):
    # "unit testing" -- 12 chars, 2 words -- would pass the relaxed floor only if
    # it named the skill; it does not name "Kubernetes", so the full floor applies.
    payload = {"candidates": [_cand("Kubernetes", "unit testing", group="Engineering")]}
    assert _derive(RN, payload, monkeypatch) == []


def test_short_evidence_drop_is_announced_once(monkeypatch):
    calls = []
    monkeypatch.setattr(skills.ui, "hint", lambda m: calls.append(m))
    payload = {"candidates": [
        _cand("React Native", "React Native", group="Engineering"),   # survives
        _cand("Machine Learning", "the", group="Engineering"),        # short drop
        _cand("Kubernetes", "unit testing", group="Engineering"),     # short drop
    ]}
    got = _derive(RN, payload, monkeypatch)
    assert [c["skill"] for c in got] == ["React Native"]
    assert len(calls) == 1
    assert "2" in calls[0]


def test_no_hint_when_nothing_dropped_for_short_evidence(monkeypatch):
    calls = []
    monkeypatch.setattr(skills.ui, "hint", lambda m: calls.append(m))
    payload = {"candidates": [_cand("React Native", "React Native", group="Engineering")]}
    _derive(RN, payload, monkeypatch)
    assert calls == []


# --- fix round 3: short skill names + boundary-aware name matching -----------

SHORT = {
    "experience": [{"company": "Acme", "title": "Engineer",
                    "bullets": ["shipped React to production",
                                "wrote the renderer in C++",
                                "built the API in Node.js",
                                "Go to the store queue processor",
                                "Going to production took a week"],
                    "notes": ""}],
    "projects": [],
    "skills": [{"label": "Engineering", "entries": []}],
}


def test_short_name_react_survives_direct(monkeypatch):
    payload = {"candidates": [_cand("React", "React", group="Engineering")]}
    got = _derive(SHORT, payload, monkeypatch)
    assert [c["skill"] for c in got] == ["React"]
    assert got[0]["strength"] == "direct"


def test_two_char_name_go_survives_when_named(monkeypatch):
    payload = {"candidates": [_cand("Go", "Go", group="Engineering")]}
    got = _derive(SHORT, payload, monkeypatch)
    assert [c["skill"] for c in got] == ["Go"]


def test_go_is_not_self_named_by_going(monkeypatch):
    # "Going to production took a week" -- 30 chars, 6 words -- passes the 15/3
    # floor, so it survives, but "Go" is not a standalone token in it: implied.
    payload = {"candidates": [_cand("Go", "Going to production took a week", group="Engineering")]}
    got = _derive(SHORT, payload, monkeypatch)
    assert [c["skill"] for c in got] == ["Go"]
    assert got[0]["strength"] == "implied"


def test_punctuated_name_cpp_survives_direct(monkeypatch):
    payload = {"candidates": [_cand("C++", "wrote the renderer in C++", group="Engineering")]}
    got = _derive(SHORT, payload, monkeypatch)
    assert [c["skill"] for c in got] == ["C++"]
    assert got[0]["strength"] == "direct"


def test_dotted_name_nodejs_survives_direct(monkeypatch):
    payload = {"candidates": [_cand("Node.js", "built the API in Node.js", group="Engineering")]}
    got = _derive(SHORT, payload, monkeypatch)
    assert [c["skill"] for c in got] == ["Node.js"]
    assert got[0]["strength"] == "direct"
