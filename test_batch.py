"""batch.py: score many, ask once, then generate unattended."""
from pathlib import Path

import pytest

import batch
import shell


@pytest.fixture
def session(tmp_path, monkeypatch):
    monkeypatch.setattr(batch.ui, "interactive", lambda: False)
    return shell.Session(root=tmp_path, backend="openclaw", model="m",
                         profile=None, output_dir=tmp_path / "out")


def test_expand_globs_files(tmp_path):
    for n in ("a.txt", "b.txt"):
        (tmp_path / n).write_text("Responsibilities: x")
    got = batch.expand([str(tmp_path / "*.txt")])
    assert sorted(Path(g).name for g in got) == ["a.txt", "b.txt"]


def test_expand_reads_a_directory(tmp_path):
    (tmp_path / "a.txt").write_text("Responsibilities: x")
    (tmp_path / "notes.md").write_text("ignored")
    got = batch.expand([str(tmp_path)])
    assert [Path(g).name for g in got] == ["a.txt"]


def test_expand_reads_a_url_list(tmp_path):
    p = tmp_path / "urls.txt"
    p.write_text("https://a.com/1\nhttps://b.com/2\n")
    assert batch.expand([str(p)]) == ["https://a.com/1", "https://b.com/2"]


def test_score_all_ranks_by_score(session, monkeypatch, tmp_path):
    scores = {"a.txt": 61, "b.txt": 72}
    monkeypatch.setattr(batch.shell, "jd_to_path", lambda a: Path(a))
    monkeypatch.setattr(batch, "_score_one", lambda src, session: {
        "source": src, "label": Path(src).name, "path": Path(src),
        "score": scores[Path(src).name], "gaps": [], "error": ""})
    got = batch.score_all([str(tmp_path / "a.txt"), str(tmp_path / "b.txt")], session=session)
    assert [r["label"] for r in got] == ["b.txt", "a.txt"]


def test_a_failed_source_is_excluded_but_the_batch_continues(session, monkeypatch, tmp_path):
    def one(src, session):
        if src.endswith("bad.txt"):
            return {"source": src, "label": "bad.txt", "path": None,
                    "score": None, "gaps": [], "error": "file not found"}
        return {"source": src, "label": "ok.txt", "path": Path(src),
                "score": 70, "gaps": [], "error": ""}
    monkeypatch.setattr(batch, "_score_one", one)
    got = batch.score_all([str(tmp_path / "bad.txt"), str(tmp_path / "ok.txt")], session=session)
    assert [r["label"] for r in got] == ["ok.txt"]


def test_select_targets_all(monkeypatch):
    rows = [{"label": "a", "score": 70}, {"label": "b", "score": 60}]
    monkeypatch.setattr(batch.ui, "select", lambda *a, **k: "all")
    assert batch.select_targets(rows) == rows


def test_select_targets_top_3(monkeypatch):
    rows = [{"label": str(i), "score": 90 - i} for i in range(5)]
    monkeypatch.setattr(batch.ui, "select", lambda *a, **k: "top 3")
    assert [r["label"] for r in batch.select_targets(rows)] == ["0", "1", "2"]


def test_select_targets_pick(monkeypatch):
    rows = [{"label": "a", "score": 70}, {"label": "b", "score": 60}, {"label": "c", "score": 50}]
    monkeypatch.setattr(batch.ui, "select", lambda *a, **k: "pick")
    monkeypatch.setattr(batch.ui, "checkbox", lambda *a, **k: ["2. b  60/100"])
    assert [r["label"] for r in batch.select_targets(rows)] == ["b"]


def test_select_targets_none_and_empty(monkeypatch):
    monkeypatch.setattr(batch.ui, "select", lambda *a, **k: "none")
    assert batch.select_targets([{"label": "a", "score": 70}]) == []
    # empty rows never reach ui.select
    assert batch.select_targets([]) == []


def test_select_targets_pick_disambiguates_same_basename_and_score(monkeypatch):
    rows = [{"label": "role.txt", "score": 60}, {"label": "role.txt", "score": 60}]
    monkeypatch.setattr(batch.ui, "select", lambda *a, **k: "pick")
    seen = {}

    def checkbox(msg, choices, **k):
        seen["choices"] = list(choices)
        return ["2. role.txt  60/100"]

    monkeypatch.setattr(batch.ui, "checkbox", checkbox)
    got = batch.select_targets(rows)
    assert seen["choices"] == ["1. role.txt  60/100", "2. role.txt  60/100"]
    assert got == [rows[1]]


def test_generation_failure_does_not_abort_the_rest(session, monkeypatch, capsys, tmp_path):
    done = []

    def run(args):
        if "bad" in args.jd_file:
            raise RuntimeError("latex blew up")
        done.append(args.jd_file)

    monkeypatch.setattr(batch.tailor, "run", run)
    rows = [{"label": "bad", "path": tmp_path / "bad.txt", "gaps": [], "score": 70},
            {"label": "good", "path": tmp_path / "good.txt", "gaps": [], "score": 60}]
    batch.generate(rows, session=session, answers={})
    assert done == [str(tmp_path / "good.txt")]
    out = capsys.readouterr().out
    assert "latex blew up" in out
    assert "1 failed" in out


def test_generation_survives_a_systemexit(session, monkeypatch, capsys, tmp_path):
    # tailor.run calls sys.exit(1) on a missing/empty JD -- SystemExit, not Exception.
    done = []

    def run(args):
        if "bad" in args.jd_file:
            raise SystemExit(1)
        done.append(args.jd_file)

    monkeypatch.setattr(batch.tailor, "run", run)
    rows = [{"id": 0, "label": "bad", "path": tmp_path / "bad.txt", "gaps": [], "score": 70},
            {"id": 1, "label": "good", "path": tmp_path / "good.txt", "gaps": [], "score": 60}]
    batch.generate(rows, session=session, answers={})
    assert done == [str(tmp_path / "good.txt")]
    assert "1 failed" in capsys.readouterr().out


def test_generate_passes_y_o_and_a_per_row_x(session, monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(batch.tailor, "run", lambda args: seen.append(args))
    rows = [{"id": 0, "label": "a.txt", "path": tmp_path / "a.txt", "gaps": [], "score": 70},
            {"id": 1, "label": "b.txt", "path": tmp_path / "b.txt", "gaps": [], "score": 60}]
    batch.generate(rows, session=session, answers={0: "context for A"})

    assert all(a.no_interactive for a in seen)                       # -y
    assert all(a.output_dir == str(session.output_dir) for a in seen)  # -o
    assert seen[0].context == "context for A"                        # -x, only for row 0
    assert seen[1].context is None
    assert [a.jd_file for a in seen] == [str(tmp_path / "a.txt"), str(tmp_path / "b.txt")]


def test_same_basename_sources_get_their_own_answers(session, monkeypatch, tmp_path):
    # Two postings both called role.txt: each must receive only its own -x context.
    rows = [
        {"id": 0, "label": "role.txt", "path": tmp_path / "A" / "role.txt",
         "gaps": [{"question": "q0"}], "score": 70},
        {"id": 1, "label": "role.txt", "path": tmp_path / "B" / "role.txt",
         "gaps": [{"question": "q1"}], "score": 60},
    ]
    monkeypatch.setattr(batch.ui, "confirm", lambda *a, **k: True)
    monkeypatch.setattr(batch.ui, "gap_table", lambda *a, **k: None)
    replies = iter(["answer zero", "answer one"])
    monkeypatch.setattr(batch.ui, "text", lambda *a, **k: next(replies))
    answers = batch.collect_answers(rows)
    assert set(answers) == {0, 1}
    assert "answer zero" in answers[0] and "answer one" in answers[1]

    seen = []
    monkeypatch.setattr(batch.tailor, "run", lambda args: seen.append(args))
    batch.generate(rows, session=session, answers=answers)
    assert "answer zero" in seen[0].context
    assert "answer one" in seen[1].context
    assert "answer one" not in seen[0].context


def test_collect_answers_returns_empty_when_no_gaps(monkeypatch):
    rows = [{"id": 0, "label": "a", "score": 70, "gaps": []}]
    # ui.confirm must not even be consulted
    monkeypatch.setattr(batch.ui, "confirm", lambda *a, **k: pytest.fail("asked with no gaps"))
    assert batch.collect_answers(rows) == {}


def test_score_one_internal_failure_yields_an_error_row_not_an_abort(session, monkeypatch, tmp_path):
    ok = tmp_path / "ok.txt"
    ok.write_text("Responsibilities: x")

    def jd_to_path(src):
        if src.endswith("bad.txt"):
            raise FileNotFoundError(src)
        return Path(src)

    monkeypatch.setattr(batch.shell, "jd_to_path", jd_to_path)
    monkeypatch.setattr(batch.llm, "score_model", lambda *a, **k: ("openclaw", "m"))
    monkeypatch.setattr(batch.tailor, "score_jd",
                        lambda *a, **k: {"overall_score": 55, "gap_questions": []})

    got = batch.score_all([str(tmp_path / "bad.txt"), str(ok)], session=session)
    assert [r["label"] for r in got] == ["ok.txt"]
    assert got[0]["score"] == 55


def test_cmd_batch_needs_a_corpus(session, capsys):
    # session fixture carries profile=None -- the realistic first-run state.
    batch.cmd_batch(session, ["some/glob"])
    assert "/setup" in capsys.readouterr().out
