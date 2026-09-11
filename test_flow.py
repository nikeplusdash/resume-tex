# test_flow.py  (step B section; more added in Tasks 8 and 9)
import json
import pytest
import tailor
import llm
from profile import Profile

CONSTANT = {"name": "Alex Rivera", "email": "a@x.com", "phone": "+1", "linkedin": "in/a",
            "location": "Seattle, WA", "portfolios": "example.com", "solo_worker": False,
            "banned_summary_anchors": [], "skills_title": "Skills"}
CONTENT = {
    "portfolio_link": "example.com", "summary": "s",
    "experience": [{"company": "Northgate", "title": "PD", "dates": "2023--", "location": "S", "notes": "", "bullets": ["b"]}],
    "projects": [{"title": "Transit Board", "notes": "", "bullets": ["b"]}],
    "skills": [{"label": "Design Skills", "entries": ["Figma"]}],
}

def _profile():
    return Profile.from_dicts(CONSTANT, CONTENT)

def _fake_schema_reply(payload):
    def fake(*, backend, model, system, user, schema, thinking=None, binary=None, timeout=None, on_retry=None):
        return schema.model_validate(payload).model_dump(exclude_none=True), llm.Reply(json.dumps(payload), finish="stop")
    return fake

GOOD = {
    "overall_score": 999,   # deliberately wrong -> must be recomputed
    "requirements": [
        {"requirement": "Kubernetes", "weight": 20, "status": "missing", "evidence": [], "gap_or_talking_point": "no evidence"},
        {"requirement": "Design systems", "weight": 80, "status": "direct", "evidence": ["Northgate"], "gap_or_talking_point": ""},
    ],
    "gap_questions": [
        {"question": "Have you run K8s in production?", "potential_points": 20, "target": "experience:Northgate", "target_requirement": "Kubernetes"},
        {"question": "Any infra work?", "potential_points": 500, "target": "nonsense:x", "target_requirement": "Kubernetes"},
    ],
}

def test_score_jd_recomputes_score_and_filters_gap_questions(monkeypatch):
    monkeypatch.setattr(llm, "complete_schema", _fake_schema_reply(GOOD))
    out = tailor.score_jd(_profile(), "jd text", backend="claude", model="claude-haiku-4-5")
    assert out["overall_score"] == 80          # 80 direct + 0 missing, /100
    assert len(out["gap_questions"]) == 1      # bad target dropped
    assert out["gap_questions"][0]["potential_points"] == 20

def test_score_jd_caps_gap_questions_at_three_keeping_highest_value(monkeypatch):
    payload = {
        "overall_score": 0,
        "requirements": [{"requirement": "R", "weight": 100, "status": "missing", "evidence": [], "gap_or_talking_point": ""}],
        "gap_questions": [
            {"question": f"Q{i}", "potential_points": i, "target": "general", "target_requirement": "R"}
            for i in (5, 30, 10, 25, 2)
        ],
    }
    monkeypatch.setattr(llm, "complete_schema", _fake_schema_reply(payload))
    out = tailor.score_jd(_profile(), "jd", backend="claude", model="m")
    assert [g["potential_points"] for g in out["gap_questions"]] == [30, 25, 10]


def test_score_jd_swallows_llm_error(monkeypatch):
    def boom(**k): raise llm.LLMError("down")
    monkeypatch.setattr(llm, "complete_schema", boom)
    out = tailor.score_jd(_profile(), "jd", backend="claude", model="claude-haiku-4-5")
    assert out == {"overall_score": None, "requirements": [], "gap_questions": [], "key_terms": []}

def test_score_jd_drops_education_questions_when_education_present(monkeypatch):
    payload = {
        "overall_score": 0,
        "requirements": [{"requirement": "Degree", "weight": 20, "status": "missing", "evidence": [], "gap_or_talking_point": ""}],
        "gap_questions": [
            {"question": "What is your bachelor's degree, institution, and graduation date?",
             "potential_points": 20, "target": "general", "target_requirement": "Degree"},
            {"question": "Which React projects can you document with dates?",
             "potential_points": 10, "target": "project:Transit Board", "target_requirement": "React"},
        ],
    }
    monkeypatch.setattr(llm, "complete_schema", _fake_schema_reply(payload))
    content = {**CONTENT, "education": [
        {"institution": "UW", "degree": "B.Des", "gpa": "3.8", "start": "2015", "end": "2019", "location": "S"}]}
    prof = Profile.from_dicts(CONSTANT, content)
    out = tailor.score_jd(prof, "Bachelor's degree required", backend="claude", model="claude-haiku-4-5")
    qs = [g["question"] for g in out["gap_questions"]]
    assert not any("bachelor" in q.lower() or "degree" in q.lower() for q in qs)
    assert any("React" in q for q in qs)

def test_score_jd_keeps_education_questions_when_no_education(monkeypatch):
    payload = {
        "overall_score": 0,
        "requirements": [{"requirement": "Degree", "weight": 20, "status": "missing", "evidence": [], "gap_or_talking_point": ""}],
        "gap_questions": [
            {"question": "What is your degree and institution?", "potential_points": 20,
             "target": "general", "target_requirement": "Degree"}],
    }
    monkeypatch.setattr(llm, "complete_schema", _fake_schema_reply(payload))
    out = tailor.score_jd(_profile(), "Bachelor's degree required", backend="claude", model="claude-haiku-4-5")
    assert len(out["gap_questions"]) == 1   # no education array -> the question stands


# ---- Task 8: distill_note() + collect_gap_answers() -------------------------
import ui


def test_distill_note_blank_answer_returns_empty(monkeypatch):
    monkeypatch.setattr(llm, "complete_text", lambda **k: (_ for _ in ()).throw(AssertionError("should not call")))
    assert tailor.distill_note("q", "   ", backend="claude", model="m") == ""


def test_distill_note_calls_model(monkeypatch):
    monkeypatch.setattr(llm, "complete_text", lambda **k: llm.Reply("Led K8s migration for 12 services.", finish="stop"))
    out = tailor.distill_note("Have you run K8s?", "yeah at Northgate we moved 12 svcs", backend="claude", model="m")
    assert out == "Led K8s migration for 12 services."


def test_collect_gap_answers_writes_notes_and_returns_folded(tmp_path, monkeypatch):
    (tmp_path / "constant.json").write_text(json.dumps(CONSTANT))
    (tmp_path / "content.json").write_text(json.dumps(CONTENT, indent=2))
    prof = Profile.load(tmp_path)
    gqs = [{"question": "K8s in prod?", "potential_points": 20, "target": "experience:Northgate", "target_requirement": "Kubernetes"},
           {"question": "Infra work?", "potential_points": 5, "target": "general", "target_requirement": "Infra"}]
    monkeypatch.setattr(ui, "interactive", lambda: True)
    monkeypatch.setattr(ui, "confirm", lambda *a, **k: True)
    monkeypatch.setattr(ui, "gap_table", lambda *a, **k: None)
    answers = iter(["ran 12 services on EKS", ""])   # second one skipped
    monkeypatch.setattr(ui, "editor", lambda *a, **k: next(answers))
    monkeypatch.setattr(tailor, "distill_note", lambda q, a, **k: f"NOTE:{a}" if a.strip() else "")
    monkeypatch.setattr(tailor, "route_note", lambda p, note, fb, **k: [fb] if fb else [])
    folded = tailor.collect_gap_answers(prof, gqs, backend="claude", model="m")
    assert folded == [("K8s in prod?", 20)]
    reloaded = json.loads((tmp_path / "content.json").read_text())
    assert reloaded["experience"][0]["notes"].endswith("NOTE:ran 12 services on EKS")
    assert "notes" not in reloaded or reloaded.get("notes") in ("", None)


def test_collect_gap_answers_folds_into_every_routed_entry(tmp_path, monkeypatch):
    (tmp_path / "constant.json").write_text(json.dumps(CONSTANT))
    (tmp_path / "content.json").write_text(json.dumps(CONTENT, indent=2))
    prof = Profile.load(tmp_path)
    gqs = [{"question": "cross-team work?", "potential_points": 12,
            "target": "experience:Northgate", "target_requirement": "Collaboration"}]
    monkeypatch.setattr(ui, "interactive", lambda: True)
    monkeypatch.setattr(ui, "confirm", lambda *a, **k: True)
    monkeypatch.setattr(ui, "editor", lambda *a, **k: "partnered with PMs and eng on Transit Board")
    monkeypatch.setattr(tailor, "distill_note", lambda q, a, **k: "NOTE:partnered with PMs and eng.")
    # route_note sends it to BOTH the experience and the project
    monkeypatch.setattr(tailor, "route_note",
                        lambda p, note, fb, **k: ["experience:Northgate", "project:Transit Board"])
    folded = tailor.collect_gap_answers(prof, gqs, backend="claude", model="m")
    assert folded == [("cross-team work?", 12)]
    saved = json.loads((tmp_path / "content.json").read_text())
    assert "NOTE:partnered" in saved["experience"][0]["notes"]
    assert "NOTE:partnered" in saved["projects"][0]["notes"]


def test_collect_gap_answers_skips_write_on_declined_note_confirmation(tmp_path, monkeypatch):
    # Spec §9: the distilled note is shown and confirmed before it is written.
    (tmp_path / "constant.json").write_text(json.dumps(CONSTANT))
    (tmp_path / "content.json").write_text(json.dumps(CONTENT, indent=2))
    prof = Profile.load(tmp_path)
    gqs = [{"question": "K8s in prod?", "potential_points": 20, "target": "experience:Northgate", "target_requirement": "Kubernetes"}]
    monkeypatch.setattr(ui, "interactive", lambda: True)
    # yes to "does this apply", NO to "save it to those entries"
    monkeypatch.setattr(ui, "confirm", lambda msg, **k: "Save it" not in msg)
    monkeypatch.setattr(ui, "gap_table", lambda *a, **k: None)
    monkeypatch.setattr(ui, "editor", lambda *a, **k: "ran 12 services on EKS")
    monkeypatch.setattr(tailor, "distill_note", lambda q, a, **k: f"NOTE:{a}" if a.strip() else "")
    monkeypatch.setattr(tailor, "route_note", lambda p, note, fb, **k: [fb] if fb else [])
    folded = tailor.collect_gap_answers(prof, gqs, backend="claude", model="m")
    assert folded == []
    reloaded = json.loads((tmp_path / "content.json").read_text())
    assert reloaded["experience"][0]["notes"] == ""


def test_collect_gap_answers_declined_when_no_questions(monkeypatch):
    # The "do you want to answer these" decision now lives in run(), upfront,
    # alongside every other yes/no question -- collect_gap_answers only short-circuits
    # on an empty list (or non-interactive) once it's actually called.
    monkeypatch.setattr(ui, "interactive", lambda: True)
    assert tailor.collect_gap_answers(_profile(), [], backend="claude", model="m") == []


# ---- Task 9: new flags + A->E orchestration (run) + education decision -------
import argparse
import sys


def test_argparse_has_new_flags():
    argv = ["tailor.py", "jd.txt", "-y", "--score-preset", "haiku"]
    old = sys.argv
    try:
        sys.argv = argv
        args = tailor.parse_args()
    finally:
        sys.argv = old
    assert args.no_interactive is True
    assert args.score_preset == "haiku"


def test_jd_requires_degree_detects_common_phrasings():
    for jd in ["Bachelor's degree from an accredited institution",
               "BS in Computer Science or related discipline",
               "requires a Master's degree", "undergraduate degree required",
               "Educational Qualifications: Bachelor's degree in Engineering"]:
        assert tailor.jd_requires_degree(jd) is True
    for jd in ["5+ years building web apps", "Strong React and TypeScript skills",
               "You will mentor other engineers"]:
        assert tailor.jd_requires_degree(jd) is False


def test_decide_education_matrix():
    edu = [{"institution": "UW", "degree": "B.Des", "gpa": "3.8", "start": "2015", "end": "2019", "location": "S"}]
    P = lambda has_edu: Profile.from_dicts(CONSTANT, {**CONTENT, **({"education": edu} if has_edu else {})})
    A = lambda **kw: type("A", (), {"education": False, "extended_education": False,
                                    "no_interactive": True, **kw})()
    # -ee / -e force include
    assert tailor.decide_education(P(True), "no degree talk", A(extended_education=True))[0] is True
    assert tailor.decide_education(P(True), "no degree talk", A(education=True))[0] is True
    # JD requires + data present -> include
    inc, line = tailor.decide_education(P(True), "Bachelor's degree required", A())
    assert inc is True and "JD requires a degree" in line
    # JD requires + no data -> exclude + warning
    inc, line = tailor.decide_education(P(False), "Bachelor's degree required", A())
    assert inc is False and "WARNING" in line
    # JD doesn't require, non-interactive -> exclude
    inc, line = tailor.decide_education(P(True), "React skills", A())
    assert inc is False and "omitted" in line


def test_decide_education_prompts_when_jd_silent_and_interactive(monkeypatch):
    edu = [{"institution": "UW", "degree": "B.Des", "gpa": "3.8", "start": "2015", "end": "2019", "location": "S"}]
    prof = Profile.from_dicts(CONSTANT, {**CONTENT, "education": edu})
    args = type("A", (), {"education": False, "extended_education": False, "no_interactive": False})()
    monkeypatch.setattr(ui, "interactive", lambda: True)
    monkeypatch.setattr(ui, "confirm", lambda *a, **k: True)
    inc, line = tailor.decide_education(prof, "React skills, no degree mentioned", args)
    assert inc is True and "your choice" in line


def _run_args(**over):
    base = dict(jd_file=None, extra_context_file=None, context=None, cv=False,
                preset=None, model=None, provider=None, score_preset=None,
                no_score=False, binary=None, timeout=60.0, thinking="medium",
                no_interactive=True, education=False, extended_education=False,
                location=None, recommendations=False, cover_letter=False,
                output_dir=None, max_revisions=2)
    base.update(over)
    return argparse.Namespace(**base)


def test_main_straight_through_when_no_interactive(tmp_path, monkeypatch, capsys):
    (tmp_path / "jd.txt").write_text("Senior Product Designer, patient experience. Seattle.")
    monkeypatch.setattr(tailor, "ROOT", tmp_path)
    monkeypatch.setattr(tailor.Profile, "load", classmethod(
        lambda cls, root, **k: Profile.from_dicts(CONSTANT, CONTENT, root=tmp_path, **k)))
    monkeypatch.setattr(llm, "resolve", lambda *a, **k: ("claude", "claude-sonnet-5"))
    monkeypatch.setattr(llm, "score_model", lambda *a, **k: ("claude", "claude-haiku-4-5"))
    monkeypatch.setattr(tailor, "score_jd", lambda *a, **k: {
        "overall_score": 70, "requirements": [],
        "gap_questions": [{"question": "q", "potential_points": 9,
                           "target": "general", "target_requirement": "r"}]})
    called = {}
    monkeypatch.setattr(tailor, "collect_gap_answers",
                        lambda *a, **k: called.setdefault("C", True) or [])
    monkeypatch.setattr(tailor, "tailor_content", lambda *a, **k: {
        "company_name": "Meridian", "job_title": "PD", "portfolio_link": "example.com",
        "summary": "s", "experience": [{"company": "N", "title": "PD", "dates": "d",
                                        "location": "l", "bullets": ["b"]}],
        "projects": [{"title": "P", "bullets": ["b"]}],
        "skills": [{"label": "Design Skills", "entries": ["Figma"]}],
        "match_analysis": {"requirements": [{"requirement": "x", "weight": 100, "status": "direct"}],
                           "overall_score": 0, "strongest_matches": [], "improvements": [],
                           "interview_talking_points": [], "inferred_skills": [],
                           "clarifying_questions": []}})
    monkeypatch.setattr(tailor, "compile_pdf", lambda data, *a, **k: (tmp_path / "out.pdf", data))
    monkeypatch.setattr(tailor, "check_integrity", lambda *a, **k: [])
    monkeypatch.setattr("sys.stdin", type("S", (), {
        "isatty": staticmethod(lambda: False), "read": staticmethod(lambda: "")})())

    tailor.run(_run_args(jd_file=str(tmp_path / "jd.txt"), output_dir=str(tmp_path)))

    assert "C" not in called                       # straight-through skipped gap questions
    out = capsys.readouterr().out
    assert "70" in out and "baseline" in out.lower()
    assert "Education" in out                       # D0 education line printed


def _run_with_scores(tmp_path, monkeypatch, *, score_returns, no_score=False, max_revisions=2,
                     capture_tailor=None):
    """Drive tailor.run with score_jd returning each dict in `score_returns` in
    order (baseline call, then one after re-score per generation attempt).
    Returns (report_kwargs, calls)."""
    (tmp_path / "jd.txt").write_text("Senior Product Designer. Seattle.")
    monkeypatch.setattr(tailor, "ROOT", tmp_path)
    monkeypatch.setattr(tailor.Profile, "load", classmethod(
        lambda cls, root, **k: Profile.from_dicts(CONSTANT, CONTENT, root=tmp_path, **k)))
    monkeypatch.setattr(llm, "resolve", lambda *a, **k: ("claude", "claude-sonnet-5"))
    monkeypatch.setattr(llm, "score_model", lambda *a, **k: ("claude", "claude-haiku-4-5"))
    calls = {"score": 0, "tailor": 0, "rubrics": []}
    it = iter(score_returns)

    def fake_score(*a, **k):
        calls["score"] += 1
        calls["rubrics"].append(k.get("rubric"))
        return next(it)
    monkeypatch.setattr(tailor, "score_jd", fake_score)

    def fake_tailor(*a, **k):
        calls["tailor"] += 1
        if capture_tailor is not None:
            capture_tailor.append(k)
        return {
            "company_name": "Meridian", "job_title": "PD", "portfolio_link": "example.com",
            "summary": f"attempt {calls['tailor']}",
            "experience": [{"company": "N", "title": "PD", "dates": "d", "location": "l", "bullets": ["b"]}],
            "projects": [{"title": "P", "bullets": ["b"]}],
            "skills": [{"label": "Design Skills", "entries": ["Figma"]}],
            "match_analysis": {"requirements": [{"requirement": "x", "weight": 100, "status": "direct"}],
                               "overall_score": 42, "strongest_matches": [], "improvements": [],
                               "interview_talking_points": [], "inferred_skills": [],
                               "clarifying_questions": []}}
    monkeypatch.setattr(tailor, "tailor_content", fake_tailor)
    monkeypatch.setattr(tailor, "compile_pdf", lambda data, *a, **k: (tmp_path / "out.pdf", data))
    monkeypatch.setattr(tailor, "check_integrity", lambda *a, **k: [])
    seen = {}
    monkeypatch.setattr(tailor.ui, "report", lambda **kw: seen.update(kw))
    monkeypatch.setattr("sys.stdin", type("S", (), {
        "isatty": staticmethod(lambda: False), "read": staticmethod(lambda: "")})())
    tailor.run(_run_args(jd_file=str(tmp_path / "jd.txt"), output_dir=str(tmp_path),
                         no_score=no_score, max_revisions=max_revisions))
    return seen, calls


def _sc(score, reqs=None, key_terms=None):
    return {"overall_score": score, "requirements": reqs or [], "gap_questions": [],
            "key_terms": key_terms or []}


def test_tailored_score_is_a_second_real_score_not_the_models_self_assessment(tmp_path, monkeypatch):
    # baseline call -> 86; after re-score of the shipped resume -> 90.
    # The report's `tailored` must be 90, NOT match_analysis.overall_score (42).
    seen, calls = _run_with_scores(tmp_path, monkeypatch, score_returns=[_sc(86), _sc(90)])
    assert calls["score"] == 2 and calls["tailor"] == 1
    assert seen["baseline"] == 86
    assert seen["tailored"] == 90


def test_after_score_uses_the_baselines_rubric(tmp_path, monkeypatch):
    reqs = [{"requirement": "Design systems", "weight": 60, "status": "direct",
             "evidence": ["Northgate"], "gap_or_talking_point": ""}]
    seen, calls = _run_with_scores(tmp_path, monkeypatch,
                                   score_returns=[_sc(86, reqs), _sc(90, reqs)])
    assert calls["rubrics"] == [None, reqs]      # baseline free-form, after fixed


def test_a_tailored_score_below_baseline_triggers_a_revision_with_feedback(tmp_path, monkeypatch):
    reqs_b = [{"requirement": "Design systems", "weight": 60, "status": "direct",
               "evidence": ["Northgate design system"], "gap_or_talking_point": ""}]
    reqs_a = [{"requirement": "Design systems", "weight": 60, "status": "adjacent",
               "evidence": [], "gap_or_talking_point": ""}]
    kws = []
    seen, calls = _run_with_scores(
        tmp_path, monkeypatch, capture_tailor=kws,
        score_returns=[_sc(86, reqs_b, ["design systems", "B2B"]), _sc(78, reqs_a), _sc(91, reqs_b)])
    assert calls["tailor"] == 2 and calls["score"] == 3
    assert seen["tailored"] == 91
    assert kws[0]["revision_notes"] == ""
    notes = kws[1]["revision_notes"]
    assert "Design systems" in notes and "direct" in notes and "adjacent" in notes
    assert "Northgate design system" in notes                 # the evidence it had
    assert "B2B" in notes                                     # the term that never landed
    assert kws[0]["key_terms"] == ["design systems", "B2B"]   # checklist reaches the model


def test_revisions_stop_at_max_and_ship_the_best_attempt(tmp_path, monkeypatch, capsys):
    seen, calls = _run_with_scores(
        tmp_path, monkeypatch, max_revisions=2,
        score_returns=[_sc(86), _sc(70), _sc(78), _sc(74)])
    assert calls["tailor"] == 3 and calls["score"] == 4
    assert seen["tailored"] == 78                               # best of three, not last
    assert "still scores 78" in capsys.readouterr().out


def test_max_revisions_zero_disables_the_loop(tmp_path, monkeypatch):
    seen, calls = _run_with_scores(tmp_path, monkeypatch, max_revisions=0,
                                   score_returns=[_sc(86), _sc(78)])
    assert calls["tailor"] == 1 and seen["tailored"] == 78


def test_report_gets_term_coverage_of_the_shipped_page(tmp_path, monkeypatch):
    # shipped summary is "attempt 1"; skills carry "Figma" -> Figma covered, B2B not.
    seen, _ = _run_with_scores(tmp_path, monkeypatch,
                               score_returns=[_sc(50, key_terms=["Figma", "B2B"]), _sc(60)])
    assert seen["coverage"] == {"covered": ["Figma"], "missing": ["B2B"]}


def test_no_score_flag_skips_both_score_calls(tmp_path, monkeypatch):
    seen, calls = _run_with_scores(tmp_path, monkeypatch, score_returns=[], no_score=True)
    assert calls["score"] == 0
    assert seen["baseline"] is None


# ---- Task 15: output-base default + no duplicate Match line ----------------
import pathlib


def _straight_through(tmp_path, monkeypatch, *, capture):
    """Mock everything the way test_main_straight_through_when_no_interactive does,
    but stub compile_pdf so it records the output_base it was handed."""
    (tmp_path / "jd.txt").write_text("Senior Product Designer, patient experience. Seattle.")
    monkeypatch.setattr(tailor, "ROOT", tmp_path)
    monkeypatch.setattr(tailor.Profile, "load", classmethod(
        lambda cls, root, **k: Profile.from_dicts(CONSTANT, CONTENT, root=tmp_path, **k)))
    monkeypatch.setattr(llm, "resolve", lambda *a, **k: ("claude", "claude-sonnet-5"))
    monkeypatch.setattr(llm, "score_model", lambda *a, **k: ("claude", "claude-haiku-4-5"))
    monkeypatch.setattr(tailor, "score_jd", lambda *a, **k: {
        "overall_score": 70, "requirements": [], "gap_questions": []})
    monkeypatch.setattr(tailor, "collect_gap_answers", lambda *a, **k: [])
    monkeypatch.setattr(tailor, "tailor_content", lambda *a, **k: {
        "company_name": "Meridian", "job_title": "PD", "portfolio_link": "example.com",
        "summary": "s", "experience": [{"company": "N", "title": "PD", "dates": "d",
                                        "location": "l", "bullets": ["b"]}],
        "projects": [{"title": "P", "bullets": ["b"]}],
        "skills": [{"label": "Design Skills", "entries": ["Figma"]}],
        "match_analysis": {"requirements": [{"requirement": "x", "weight": 100, "status": "direct"}],
                           "overall_score": 0, "strongest_matches": [], "improvements": [],
                           "interview_talking_points": [], "inferred_skills": [],
                           "clarifying_questions": []}})

    def fake_compile(data, output_dir, *a, **k):
        capture["base"] = pathlib.Path(output_dir)
        return (tmp_path / "out.pdf", data)
    monkeypatch.setattr(tailor, "compile_pdf", fake_compile)
    monkeypatch.setattr(tailor, "check_integrity", lambda *a, **k: [])
    monkeypatch.setattr("sys.stdin", type("S", (), {
        "isatty": staticmethod(lambda: False), "read": staticmethod(lambda: "")})())


def test_output_base_defaults_to_home_documents_applications(tmp_path, monkeypatch):
    monkeypatch.delenv("RESUME_TEX_OUTPUT_DIR", raising=False)
    # keep the run's real dir writes inside tmp_path while still exercising the
    # Path.home()/"Documents"/"Applications" default construction
    monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda cls: tmp_path))
    captured = {}
    _straight_through(tmp_path, monkeypatch, capture=captured)
    tailor.run(_run_args(jd_file=str(tmp_path / "jd.txt"), output_dir=None))
    assert captured["base"] == pathlib.Path.home() / "Documents" / "Applications"


def test_output_base_respects_explicit_override(tmp_path, monkeypatch):
    monkeypatch.delenv("RESUME_TEX_OUTPUT_DIR", raising=False)
    captured = {}
    _straight_through(tmp_path, monkeypatch, capture=captured)
    tailor.run(_run_args(jd_file=str(tmp_path / "jd.txt"),
                         output_dir=str(tmp_path / "custom")))
    assert captured["base"] == tmp_path / "custom"


def test_read_extra_context_y_flag_skips_interactive_prompt(monkeypatch):
    # -y (no_interactive) means "straight through": no blocking prompts even on a tty.
    monkeypatch.setattr("sys.stdin", type("S", (), {"isatty": staticmethod(lambda: True)})())
    monkeypatch.setattr("builtins.input", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("straight-through must not prompt for extra context")))
    args = _run_args(context=None, extra_context_file=None, no_interactive=True)
    assert tailor.read_extra_context(args) == ""


def test_run_no_duplicate_match_line(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("RESUME_TEX_OUTPUT_DIR", raising=False)
    captured = {}
    _straight_through(tmp_path, monkeypatch, capture=captured)
    tailor.run(_run_args(jd_file=str(tmp_path / "jd.txt"),
                         output_dir=str(tmp_path / "out")))
    out = capsys.readouterr().out
    match_lines = [ln for ln in out.splitlines() if ln.lstrip().startswith("Match")]
    assert len(match_lines) == 1                      # only ui.report renders the score now
    assert "untailored 1-page" in match_lines[0] and "JD-tailored" in match_lines[0]


# ── read_jd: interactive paste (regression: terminal flicker on long JD paste) ──

def test_read_jd_interactive_uses_editor_not_line_input(monkeypatch):
    # A pasted JD is one long unbroken line. input() sends it through the tty's
    # canonical line discipline (1024-byte cap on macOS) -> the paste flickers and
    # never completes. read_jd must delegate to ui.editor (raw-mode, bracketed paste).
    monkeypatch.setattr("sys.stdin", type("S", (), {"isatty": staticmethod(lambda: True)})())
    monkeypatch.setattr("builtins.input", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("read_jd must not read a pasted JD with input()")))
    long_jd = "Team Introduction " + "e-commerce growth " * 400  # ~7 KB, no newlines
    monkeypatch.setattr(tailor.ui, "editor", lambda *a, **k: long_jd)
    assert tailor.read_jd(None) == long_jd


def test_read_jd_piped_stdin_still_reads_whole_stream(monkeypatch):
    import io
    monkeypatch.setattr("sys.stdin", io.StringIO("piped jd body\nmore\n"))
    assert tailor.read_jd(None) == "piped jd body\nmore\n"


def test_known_note_targets_includes_education(tmp_path):
    profile = tailor.Profile.from_dicts(
        {"name": "A"},
        {"experience": [], "projects": [], "education": [{"institution": "UW"}]})
    assert "education:UW" in tailor._known_note_targets(profile)


# ---- generic-one-page baseline + gap floor --------------------------------

def test_generic_one_page_trims_to_a_one_page_shape_in_source_order():
    p = Profile.from_dicts(CONSTANT, {
        "experience": [{"company": f"E{i}", "bullets": ["b"]} for i in range(6)],
        "projects": [{"title": f"P{i}", "bullets": ["b"]} for i in range(9)],
        "skills": [{"label": "S", "entries": [str(n) for n in range(8)]}],
    })
    g = tailor._generic_one_page(p)
    assert [e["company"] for e in g.content["experience"]] == ["E0", "E1", "E2", "E3"]
    assert [x["title"] for x in g.content["projects"]] == ["P0", "P1", "P2"]
    assert g.content["skills"][0]["entries"] == ["0", "1", "2", "3", "4"]


def test_run_baseline_scores_the_generic_one_page_cut(tmp_path, monkeypatch):
    used = {}
    real = tailor._generic_one_page
    monkeypatch.setattr(tailor, "_generic_one_page",
                        lambda p: used.setdefault("called", True) or real(p))
    _, calls = _run_with_scores(tmp_path, monkeypatch, score_returns=[
        {"overall_score": 70, "requirements": [], "gap_questions": []},
        {"overall_score": 74, "requirements": [], "gap_questions": []}])
    assert used.get("called") is True           # baseline ran on the one-page cut
    assert calls["score"] == 2                  # baseline + after re-score


def test_score_jd_drops_gap_questions_below_the_point_floor(monkeypatch):
    payload = {
        "overall_score": 0,
        "requirements": [{"requirement": "R", "weight": 100, "status": "missing",
                          "evidence": [], "gap_or_talking_point": ""}],
        "gap_questions": [
            {"question": f"Q{i}", "potential_points": i, "target": "general", "target_requirement": "R"}
            for i in (3, 5, 7)          # all below GAP_POINT_FLOOR (8)
        ],
    }
    monkeypatch.setattr(llm, "complete_schema", _fake_schema_reply(payload))
    out = tailor.score_jd(_profile(), "jd", backend="claude", model="m")
    assert out["gap_questions"] == []       # nothing worth asking

    payload["gap_questions"].append(
        {"question": "big", "potential_points": 15, "target": "general", "target_requirement": "R"})
    monkeypatch.setattr(llm, "complete_schema", _fake_schema_reply(payload))
    out = tailor.score_jd(_profile(), "jd", backend="claude", model="m")
    assert [g["question"] for g in out["gap_questions"]] == ["big"]


# ---- route_note() ---------------------------------------------------------

def _route_reply(targets):
    def fake(*, backend, model, system, user, schema, thinking=None, binary=None, timeout=None, on_retry=None):
        return schema.model_validate({"targets": targets}).model_dump(), llm.Reply("{}", finish="stop")
    return fake


def test_route_note_returns_the_models_entries_filtered_to_real_targets(tmp_path, monkeypatch):
    (tmp_path / "constant.json").write_text(json.dumps(CONSTANT))
    (tmp_path / "content.json").write_text(json.dumps(CONTENT, indent=2))
    prof = Profile.load(tmp_path)
    monkeypatch.setattr(ui, "interactive", lambda: False)   # resolve_target -> canonical, no prompt
    monkeypatch.setattr(llm, "complete_schema",
                        _route_reply(["experience:Northgate", "project:Transit Board", "bogus:nope"]))
    out = tailor.route_note(prof, "a new fact", "general", backend="c", model="m")
    assert out == ["experience:Northgate", "project:Transit Board"]   # bogus dropped


def test_route_note_falls_back_to_the_gap_target_on_model_failure(tmp_path, monkeypatch):
    (tmp_path / "constant.json").write_text(json.dumps(CONSTANT))
    (tmp_path / "content.json").write_text(json.dumps(CONTENT, indent=2))
    prof = Profile.load(tmp_path)
    monkeypatch.setattr(ui, "interactive", lambda: False)
    def boom(**k):
        raise llm.LLMError("down")
    monkeypatch.setattr(llm, "complete_schema", boom)
    assert tailor.route_note(prof, "x", "experience:Northgate", backend="c", model="m") == ["experience:Northgate"]
    assert tailor.route_note(prof, "x", "", backend="c", model="m") == ["general"]


def test_route_note_dedupes(tmp_path, monkeypatch):
    (tmp_path / "constant.json").write_text(json.dumps(CONSTANT))
    (tmp_path / "content.json").write_text(json.dumps(CONTENT, indent=2))
    prof = Profile.load(tmp_path)
    monkeypatch.setattr(ui, "interactive", lambda: False)
    monkeypatch.setattr(llm, "complete_schema",
                        _route_reply(["experience:Northgate", "experience:Northgate"]))
    assert tailor.route_note(prof, "x", "general", backend="c", model="m") == ["experience:Northgate"]


# ---- JD key terms: extraction, checklist, coverage, rubric re-score ----------

def test_term_coverage_matches_verbatim_and_by_word_stem():
    content = {"summary": "Owns end-to-end design for a B2B marketplace.",
               "experience": [{"title": "PD", "bullets": ["Built the design system in Figma to improve usability."]}],
               "projects": [], "skills": [{"label": "Tools", "entries": ["Usability tests"]}]}
    out = tailor.term_coverage(content, ["end-to-end design", "design systems", "Usability Testing",
                                         "B2B", "user research", ""])
    assert out["covered"] == ["end-to-end design", "design systems", "Usability Testing", "B2B"]
    assert out["missing"] == ["user research"]


def test_score_jd_returns_deduped_key_terms(monkeypatch):
    payload = dict(GOOD, key_terms=["Figma", "figma", " design systems ", ""])
    monkeypatch.setattr(llm, "complete_schema", _fake_schema_reply(payload))
    out = tailor.score_jd(_profile(), "jd", backend="claude", model="m")
    assert out["key_terms"] == ["Figma", "design systems"]


def test_score_prompt_asks_for_key_terms():
    assert "key_terms" in tailor.SCORE_SYSTEM_PROMPT
    assert "exact wording" in tailor.SCORE_SYSTEM_PROMPT


def test_rubric_rescore_keeps_the_baselines_requirements_and_weights(monkeypatch):
    rubric = [{"requirement": "Kubernetes", "weight": 20, "status": "missing", "evidence": []},
              {"requirement": "Design systems", "weight": 80, "status": "adjacent", "evidence": []}]
    seen = {}
    def fake(*, backend, model, system, user, schema, **k):
        seen["schema"], seen["system"] = schema, system
        payload = {"requirements": [
            {"requirement": "K8s (renamed)", "weight": 50, "status": "missing", "evidence": [], "gap_or_talking_point": ""},
            {"requirement": "DS", "weight": 50, "status": "direct", "evidence": ["Northgate"], "gap_or_talking_point": ""}]}
        return schema.model_validate(payload).model_dump(), llm.Reply(json.dumps(payload), finish="stop")
    monkeypatch.setattr(llm, "complete_schema", fake)
    out = tailor.score_jd(_profile(), "jd", backend="claude", model="m", rubric=rubric)
    assert seen["schema"] is tailor.RescoreAnalysis
    assert "FIXED RUBRIC" in seen["system"] and "gap_questions" not in seen["system"]
    assert [r["requirement"] for r in out["requirements"]] == ["Kubernetes", "Design systems"]
    assert [r["weight"] for r in out["requirements"]] == [20, 80]
    assert out["overall_score"] == 80 and out["gap_questions"] == [] and out["key_terms"] == []


def test_rubric_rescore_swallows_llm_error(monkeypatch):
    def boom(**k): raise llm.LLMError("down")
    monkeypatch.setattr(llm, "complete_schema", boom)
    out = tailor.score_jd(_profile(), "jd", backend="claude", model="m", rubric=[{"requirement": "r", "weight": 100}])
    assert out["overall_score"] is None


def test_revision_feedback_names_slipped_requirements_and_missing_terms():
    before = [{"requirement": "A", "weight": 50, "status": "direct", "evidence": ["ev-a"]},
              {"requirement": "B", "weight": 50, "status": "adjacent", "evidence": []}]
    after = [{"requirement": "A", "weight": 50, "status": "missing", "evidence": []},
             {"requirement": "B", "weight": 50, "status": "direct", "evidence": ["x"]}]
    notes = tailor.revision_feedback(before, after, {"covered": [], "missing": ["B2B"]})
    assert '"A" fell from direct to missing' in notes and "ev-a" in notes
    assert '"B"' not in notes                        # improved rows are not complaints
    assert "B2B" in notes
    assert tailor.revision_feedback(before, before, {"missing": []}).startswith("- The page scored lower")


def test_tailor_content_puts_key_terms_and_revision_notes_in_the_user_message(monkeypatch):
    seen = {}
    def fake(*, backend, model, system, user, schema, **k):
        seen["user"] = user
        payload = {"job_title": "PD", "portfolio_link": "example.com", "summary": "s",
                   "experience": [], "projects": [], "skills": [],
                   "match_analysis": {"overall_score": 0, "requirements": [], "strongest_matches": [],
                                      "improvements": [], "interview_talking_points": [],
                                      "inferred_skills": [], "clarifying_questions": []}}
        return schema.model_validate(payload).model_dump(exclude_none=True), llm.Reply("{}", finish="stop")
    monkeypatch.setattr(llm, "complete_schema", fake)
    tailor.tailor_content(_profile(), "jd", backend="claude", model="m", thinking="low",
                          key_terms=["design systems", "B2B"], revision_notes="- fix A")
    assert "JD KEY TERMS" in seen["user"] and "- design systems\n- B2B" in seen["user"]
    assert "REVISION NOTES" in seen["user"] and "- fix A" in seen["user"]
    tailor.tailor_content(_profile(), "jd", backend="claude", model="m", thinking="low")
    assert "JD KEY TERMS" not in seen["user"] and "REVISION NOTES" not in seen["user"]


def test_match_report_lists_term_coverage_and_tailoring_notes():
    md = tailor.match_report_markdown(
        {"overall_score": 70, "requirements": [], "tailoring_notes": ["Ross: emphasized B2B work"]},
        company="C", job_title="J", coverage={"covered": ["Figma"], "missing": ["B2B"]})
    assert "## JD key terms carried: 1/2" in md and "- ✓ Figma" in md and "- ✗ B2B" in md
    assert "## What was emphasized or condensed" in md and "Ross: emphasized B2B work" in md
    plain = tailor.match_report_markdown({"overall_score": 70, "requirements": []}, company="C", job_title="J")
    assert "key terms" not in plain and "emphasized" not in plain


def test_argparse_has_max_revisions():
    assert tailor.parse_args(["x.txt"]).max_revisions == 2
    assert tailor.parse_args(["--max-revisions", "0", "x.txt"]).max_revisions == 0
