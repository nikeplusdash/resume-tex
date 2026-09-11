"""merge.py: adding a resume must never cost the notes already collected."""
import json

import merge

EXISTING = {
    "experience": [{"company": "Acme", "title": "Senior Designer", "dates": "2021-2023",
                    "bullets": ["shipped the design system"],
                    "notes": "40 components, 3 product teams"}],
    "projects": [{"title": "Transit Fare", "bullets": ["cut steps 7 to 4"], "notes": "12M riders"}],
    "skills": [{"label": "Design", "entries": ["Figma"]}],
    "education": [{"institution": "UW", "degree": "BFA"}],
}

PLAN = {
    "matched": [{"kind": "experience", "existing_ref": "Acme · Senior Designer",
                 "incoming_ref": "Acme · Senior Designer",
                 "new_bullets": ["ran a research programme"],
                 "conflicts": ["dates differ: 2021-2023 vs 2021-2024"]}],
    "added": [{"kind": "experience", "company": "Bolt", "title": "Design Lead",
               "bullets": ["led a team of 4"], "_ref": "Bolt · Design Lead"}],
}


def test_matched_entry_preserves_notes_verbatim():
    # The load-bearing test. Notes are the corpus tailoring quality depends on, and
    # a merge that quietly rewrites them is worse than no merge at all.
    out = merge.apply(EXISTING, PLAN)
    acme = [e for e in out["experience"] if e["company"] == "Acme"][0]
    assert acme["notes"] == "40 components, 3 product teams"


def test_matched_entry_appends_new_bullets():
    acme = [e for e in merge.apply(EXISTING, PLAN)["experience"] if e["company"] == "Acme"][0]
    assert acme["bullets"] == ["shipped the design system", "ran a research programme"]


def test_conflict_does_not_change_existing_values():
    acme = [e for e in merge.apply(EXISTING, PLAN)["experience"] if e["company"] == "Acme"][0]
    assert acme["dates"] == "2021-2023"      # the existing value wins; the conflict is reported


def test_unmatched_entry_is_added_with_empty_notes():
    bolt = [e for e in merge.apply(EXISTING, PLAN)["experience"] if e["company"] == "Bolt"][0]
    assert bolt["bullets"] == ["led a team of 4"]
    assert bolt["notes"] == ""
    assert "_ref" not in bolt                 # the plan-only key must not reach content.json


def test_apply_does_not_mutate_the_input():
    before = json.dumps(EXISTING, sort_keys=True)
    merge.apply(EXISTING, PLAN)
    assert json.dumps(EXISTING, sort_keys=True) == before


def test_duplicate_bullet_is_not_appended_twice():
    plan = {"matched": [{"kind": "experience", "existing_ref": "Acme · Senior Designer",
                         "new_bullets": ["shipped the design system"], "conflicts": []}],
            "added": []}
    acme = [e for e in merge.apply(EXISTING, plan)["experience"] if e["company"] == "Acme"][0]
    assert acme["bullets"] == ["shipped the design system"]


def test_skills_merge_by_case_insensitive_dedupe():
    out = merge.merge_skills(EXISTING, {"skills": [{"label": "Design", "entries": ["figma", "Sketch"]}]})
    design = [g for g in out if g["label"] == "Design"][0]
    assert design["entries"] == ["Figma", "Sketch"]


def test_backup_is_written_before_apply(tmp_path):
    (tmp_path / "content.json").write_text(json.dumps(EXISTING))
    written = merge.backup(tmp_path)
    assert [p.name for p in written] == [written[0].name]   # content.json only, as a list
    assert written[0].exists()
    assert json.loads(written[0].read_text()) == EXISTING


def test_plan_never_sends_notes_to_the_model(monkeypatch):
    # Notes are copied through in Python; there is no upside to a model seeing them
    # and a real risk of it rewriting them. The incoming resume must be non-empty so
    # this genuinely exercises the model call — an absence assertion is only
    # meaningful next to a presence assertion.
    sent = {}

    def capture(**k):
        sent["user"] = k["user"]
        return ({"matched": [], "added": []}, None)

    incoming = {"experience": [{"company": "Bolt", "title": "Design Lead",
                                "bullets": ["led a distinctive incoming bullet"],
                                "notes": "INCOMING_NOTES_SENTINEL_9F3"}]}
    monkeypatch.setattr(merge.llm, "complete_schema", capture)
    merge.plan(EXISTING, incoming, backend="openclaw", model="m")

    # The call actually happened and carried real content.
    assert sent.get("user")
    assert "led a distinctive incoming bullet" in sent["user"]
    # No notes from either side reached the model.
    assert "40 components" not in sent["user"]         # EXISTING experience notes
    assert "12M riders" not in sent["user"]            # EXISTING project notes
    assert "INCOMING_NOTES_SENTINEL_9F3" not in sent["user"]   # incoming entry notes


def test_plan_returns_empty_on_model_failure(monkeypatch):
    def boom(**k):
        raise merge.llm.LLMError("no model")
    monkeypatch.setattr(merge.llm, "complete_schema", boom)
    assert merge.plan(EXISTING, {"experience": []}, backend="openclaw", model="m") == {
        "matched": [], "added": []}


# ── F1: a matched ref that does not resolve must be reported, never silently dropped ──

def test_apply_warns_when_a_matched_ref_does_not_resolve(capsys):
    # The model returned a ref that drifted from entry_ref's middle-dot form. The user
    # already saw and confirmed this block in merge_diff; a bare `continue` would give
    # them silently less than they approved.
    plan = {"matched": [{"kind": "experience", "existing_ref": "Acme - Senior Designer",
                         "new_bullets": ["ran a research programme", "and a second"],
                         "conflicts": []}],
            "added": []}
    out = merge.apply(EXISTING, plan)
    acme = [e for e in out["experience"] if e["company"] == "Acme"][0]
    assert acme["bullets"] == ["shipped the design system"]      # nothing was placed
    assert acme["notes"] == "40 components, 3 product teams"     # notes still intact
    printed = capsys.readouterr().out
    assert "Acme - Senior Designer" in printed
    assert "2 bullet" in printed                                 # names how many were lost
    assert "1 matched" in printed and "only 0" in printed        # reconciliation summary


def test_apply_reconciliation_is_silent_when_everything_lands(capsys):
    merge.apply(EXISTING, PLAN)
    out = capsys.readouterr().out
    assert "could not place" not in out and "Note:" not in out


# ── F3: the backup name must not clobber an earlier snapshot ──

def test_backup_name_is_timestamped_not_a_fixed_bak(tmp_path):
    (tmp_path / "content.json").write_text(json.dumps(EXISTING))
    dest = merge.backup(tmp_path)[0]
    assert dest.name != "content.json.bak"
    assert dest.name.startswith("content.json.") and dest.name.endswith(".bak")
    assert dest.exists()


def test_backup_twice_keeps_both_snapshots(tmp_path):
    cj = tmp_path / "content.json"
    cj.write_text(json.dumps({"experience": [], "projects": [], "v": 1}))
    first = merge.backup(tmp_path)[0]
    cj.write_text(json.dumps({"experience": [], "projects": [], "v": 2}))
    second = merge.backup(tmp_path)[0]
    assert first != second
    assert json.loads(first.read_text())["v"] == 1        # the pre-first-merge state survives
    assert json.loads(second.read_text())["v"] == 2


def test_backup_include_constant_snapshots_both(tmp_path):
    (tmp_path / "content.json").write_text(json.dumps(EXISTING))
    (tmp_path / "constant.json").write_text(json.dumps({"portfolios": "x", "location_rules": []}))
    written = merge.backup(tmp_path, include_constant=True)
    assert len(written) == 2
    assert written[0].name.startswith("content.json.")    # content.json's snapshot is first
    assert written[1].name.startswith("constant.json.")
    const_baks = list(tmp_path.glob("constant.json.*.bak"))
    assert len(const_baks) == 1
    assert json.loads(const_baks[0].read_text())["portfolios"] == "x"


def test_backup_tolerates_a_missing_content_json_and_reports_only_what_it_saved(tmp_path):
    # constant.json exists independently of content.json — the guard and backup() must
    # not assume content.json is there.
    (tmp_path / "constant.json").write_text(json.dumps({"portfolios": "p", "location_rules": []}))
    written = merge.backup(tmp_path, include_constant=True)
    assert [p.name.split(".")[0] for p in written] == ["constant"]
    assert not list(tmp_path.glob("content.json*.bak"))
    # and with neither file present it is simply a no-op
    assert merge.backup(tmp_path / "empty", include_constant=True) == []


# ── F2: run() — every write happens here, and it had no test ──

import install  # noqa: E402


def _fake_bootstrap_extract(**payload):
    base = {"experience": [], "projects": [], "skills": [], "education": []}
    base.update(payload)
    return base


def _wire_run(monkeypatch, *, incoming, mergeplan, confirm=True, order=None):
    """Stub install.extract_resume_text + both complete_schema calls + ui.confirm."""
    monkeypatch.setattr(install, "extract_resume_text", lambda p: "resume text")

    def fake_complete_schema(**k):
        if k["schema"] is install.BootstrapExtract:
            return (incoming, None)
        if k["schema"] is merge.MergePlan:
            return (mergeplan, None)
        raise AssertionError(f"unexpected schema {k['schema']!r}")

    monkeypatch.setattr(merge.llm, "complete_schema", fake_complete_schema)
    if order is not None:
        import pathlib
        monkeypatch.setattr(merge.ui, "merge_diff", lambda p: order.append("merge_diff"))
        monkeypatch.setattr(merge.ui, "confirm",
                            lambda *a, **k: order.append("confirm") or confirm)
        real_backup = merge.backup
        monkeypatch.setattr(merge, "backup",
                            lambda *a, **k: order.append("backup") or real_backup(*a, **k))
        real_write = pathlib.Path.write_text

        def spy_write(self, *a, **k):
            if self.name == "content.json":
                order.append("write")
            return real_write(self, *a, **k)

        monkeypatch.setattr(pathlib.Path, "write_text", spy_write)
    else:
        monkeypatch.setattr(merge.ui, "confirm", lambda *a, **k: confirm)


def _seed_run_root(tmp_path):
    (tmp_path / "content.json").write_text(json.dumps(EXISTING, indent=2) + "\n")
    return tmp_path


INCOMING_RESUME = {
    "experience": [
        {"company": "Acme", "title": "Senior Designer", "bullets": ["ran a research programme"]},
        {"company": "Bolt", "title": "Design Lead", "bullets": ["led a team of 4"]},
    ],
    "projects": [],
    "skills": [{"label": "Design", "entries": ["Sketch"]}],
    "education": [{"institution": "MIT", "degree": "MS"}],
}

INCOMING_MERGEPLAN = {
    "matched": [{"kind": "experience", "existing_ref": "Acme · Senior Designer",
                 "incoming_ref": "Acme · Senior Designer",
                 "new_bullets": ["ran a research programme"], "conflicts": []}],
    "added": [1],   # integer index into the flattened incoming list -> Bolt
}


def test_run_happy_path_preserves_notes_and_writes_to_disk(tmp_path, monkeypatch):
    root = _seed_run_root(tmp_path)
    _wire_run(monkeypatch, incoming=INCOMING_RESUME, mergeplan=INCOMING_MERGEPLAN)
    result = merge.run(root, tmp_path / "cv.pdf", backend="openclaw", model="m")
    assert result is True

    on_disk = json.loads((root / "content.json").read_text())
    acme = [e for e in on_disk["experience"] if e["company"] == "Acme"][0]
    assert acme["notes"] == "40 components, 3 product teams"          # notes survive end to end
    assert acme["bullets"] == ["shipped the design system", "ran a research programme"]
    bolt = [e for e in on_disk["experience"] if e["company"] == "Bolt"][0]
    assert bolt["notes"] == "" and bolt["bullets"] == ["led a team of 4"]
    design = [g for g in on_disk["skills"] if g["label"] == "Design"][0]
    assert design["entries"] == ["Figma", "Sketch"]
    assert {e["institution"] for e in on_disk["education"]} == {"UW", "MIT"}
    assert list(root.glob("content.json.*.bak"))                      # a backup exists
    assert json.loads(list(root.glob("content.json.*.bak"))[0].read_text()) == EXISTING


def test_run_empty_plan_returns_false_and_writes_nothing(tmp_path, monkeypatch):
    root = _seed_run_root(tmp_path)
    before = (root / "content.json").read_text()
    reached = []
    _wire_run(monkeypatch, incoming=_fake_bootstrap_extract(),
              mergeplan={"matched": [], "added": []})
    monkeypatch.setattr(merge.ui, "merge_diff", lambda p: reached.append(p))
    assert merge.run(root, tmp_path / "cv.pdf", backend="openclaw", model="m") is False
    # got all the way to the empty-plan guard, not an earlier bail-out
    assert reached == [{"matched": [], "added": []}]
    assert (root / "content.json").read_text() == before
    assert not list(root.glob("content.json.*.bak"))


def test_run_declined_confirm_returns_false_and_leaves_file_byte_identical(tmp_path, monkeypatch):
    root = _seed_run_root(tmp_path)
    before = (root / "content.json").read_text()
    _wire_run(monkeypatch, incoming=INCOMING_RESUME, mergeplan=INCOMING_MERGEPLAN, confirm=False)
    assert merge.run(root, tmp_path / "cv.pdf", backend="openclaw", model="m") is False
    assert (root / "content.json").read_text() == before
    assert not list(root.glob("content.json.*.bak"))


def test_run_does_not_duplicate_an_identical_existing_education_entry(tmp_path, monkeypatch):
    root = _seed_run_root(tmp_path)
    incoming = dict(INCOMING_RESUME, education=[{"institution": "UW", "degree": "BFA"}])
    _wire_run(monkeypatch, incoming=incoming, mergeplan={"matched": [], "added": [1]})
    merge.run(root, tmp_path / "cv.pdf", backend="openclaw", model="m")
    edu = json.loads((root / "content.json").read_text())["education"]
    assert edu == [{"institution": "UW", "degree": "BFA"}]            # not duplicated


def test_run_shows_the_plan_before_confirm_and_backs_up_before_write(tmp_path, monkeypatch):
    root = _seed_run_root(tmp_path)
    order = []
    _wire_run(monkeypatch, incoming=INCOMING_RESUME, mergeplan=INCOMING_MERGEPLAN, order=order)
    merge.run(root, tmp_path / "cv.pdf", backend="openclaw", model="m")
    # the write to content.json is instrumented, so this pins the real property:
    # the user sees the plan, confirms it, a backup is taken, THEN the file is written.
    assert order == ["merge_diff", "confirm", "backup", "write"]
    assert order.index("backup") < order.index("write")
    assert list(root.glob("content.json.*.bak"))


def test_run_reports_cleanly_when_the_extract_call_raises(tmp_path, monkeypatch, capsys):
    root = _seed_run_root(tmp_path)
    before = (root / "content.json").read_text()
    monkeypatch.setattr(install, "extract_resume_text", lambda p: "resume text")

    def boom(**k):
        raise merge.llm.LLMError("model offline")

    monkeypatch.setattr(merge.llm, "complete_schema", boom)
    assert merge.run(root, tmp_path / "cv.pdf", backend="openclaw", model="m") is False
    assert "model offline" in capsys.readouterr().out
    assert (root / "content.json").read_text() == before          # nothing written, no backup
    assert not list(root.glob("content.json.*.bak"))
