"""ui.py: rich prompts on a TTY, plain stdin/stdout when piped. No prompt reaches the model."""
import builtins
import contextlib
import io
import sys

import pytest

import ui


@pytest.fixture(autouse=True)
def _no_tty(monkeypatch):
    monkeypatch.setattr(ui, "interactive", lambda: False)


def test_confirm_fallback_reads_line(monkeypatch):
    monkeypatch.setattr(builtins, "input", lambda _="": "y")
    assert ui.confirm("go?") is True
    monkeypatch.setattr(builtins, "input", lambda _="": "")
    assert ui.confirm("go?", default=True) is True
    assert ui.confirm("go?", default=False) is False


def test_text_fallback_default(monkeypatch):
    monkeypatch.setattr(builtins, "input", lambda _="": "")
    assert ui.text("name?", default="Alex") == "Alex"
    monkeypatch.setattr(builtins, "input", lambda _="": "Sam")
    assert ui.text("name?", default="Alex") == "Sam"


def test_select_fallback_by_number(monkeypatch):
    monkeypatch.setattr(builtins, "input", lambda _="": "2")
    assert ui.select("pick", ["a", "b", "c"]) == "b"


def test_checkbox_fallback_by_indices(monkeypatch):
    monkeypatch.setattr(builtins, "input", lambda _="": "1,3")
    assert ui.checkbox("pick", ["a", "b", "c"]) == ["a", "c"]
    monkeypatch.setattr(builtins, "input", lambda _="": "")
    assert ui.checkbox("pick", ["a", "b", "c"]) == []


def test_editor_fallback_reads_to_eof(monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO("line one\nline two\n"))
    assert ui.editor("notes") == "line one\nline two"


def test_score_panel_and_report_print_without_rich(capsys):
    ui.score_panel(72, [{"requirement": "K8s", "status": "missing", "weight": 10}])
    ui.report(baseline=72, tailored=88, folded=[("Q1", 8)], improvements=["x"], questions=["y"], warnings=["z"])
    out = capsys.readouterr().out
    assert "72" in out and "88" in out and "1 gap area" in out


def test_score_panel_is_one_line_with_no_gaps(capsys):
    ui.score_panel(95, [{"requirement": "K8s", "status": "direct", "weight": 10}])
    out = capsys.readouterr().out.strip()
    assert out == "Baseline match : 95/100"


def test_confirm_is_an_inline_yes_no_pick_with_the_default_preselected_when_fullscreen(monkeypatch):
    monkeypatch.setattr(ui.console, "in_fullscreen", lambda: True)
    seen = {}

    def fake_ask_choice(label, values, **k):
        seen["values"] = values
        seen["default"] = k.get("default")
        return k.get("default")            # Enter on the pre-highlighted default
    monkeypatch.setattr(ui.console, "ask_choice", fake_ask_choice)
    assert ui.confirm("Proceed?", default=True) is True
    assert ui.confirm("Proceed?", default=False) is False
    assert seen["values"] == [(True, "Yes"), (False, "No")]


def test_text_routes_through_console_ask_when_fullscreen(monkeypatch):
    monkeypatch.setattr(ui.console, "in_fullscreen", lambda: True)
    monkeypatch.setattr(ui.console, "ask", lambda prompt, default="", **k: "typed")
    assert ui.text("Name?", default="Alex") == "typed"


def test_editor_routes_through_console_ask_multiline_when_fullscreen(monkeypatch):
    monkeypatch.setattr(ui.console, "in_fullscreen", lambda: True)
    seen = {}

    def fake_ask(label, default="", *, multiline=False):
        seen["multiline"] = multiline
        return "pasted text"
    monkeypatch.setattr(ui.console, "ask", fake_ask)
    assert ui.editor("Paste JD") == "pasted text"
    assert seen["multiline"] is True


def test_spinner_uses_console_fs_busy_when_fullscreen(monkeypatch):
    monkeypatch.setattr(ui.console, "in_fullscreen", lambda: True)
    entered = []

    @contextlib.contextmanager
    def fake_busy(msg):
        entered.append(msg)
        yield
    monkeypatch.setattr(ui.console, "fs_busy", fake_busy)
    with ui.spinner("Working"):
        pass
    assert entered == ["Working"]


def test_widgets_used_when_interactive(monkeypatch):
    monkeypatch.setattr(ui, "interactive", lambda: True)

    class FakeQ:
        def __init__(self, val):
            self._val = val

        def ask(self):
            return self._val

    monkeypatch.setattr(ui, "_q", type("M", (), {
        "confirm": staticmethod(lambda *a, **k: FakeQ(True)),
        "text": staticmethod(lambda *a, **k: FakeQ("typed")),
        "select": staticmethod(lambda *a, **k: FakeQ("b")),
        "checkbox": staticmethod(lambda *a, **k: FakeQ(["a", "c"])),
    }))
    assert ui.confirm("x") is True
    assert ui.text("x") == "typed"
    assert ui.select("x", ["a", "b"]) == "b"
    assert ui.checkbox("x", ["a", "b", "c"]) == ["a", "c"]


# ── Paths ────────────────────────────────────────────────────────────────────

def test_clean_path_strips_shell_quoting():
    assert ui.clean_path("'/a/b.pdf'") == "/a/b.pdf"
    assert ui.clean_path('"/a/b.pdf"') == "/a/b.pdf"
    assert ui.clean_path("  /a/b.pdf \n") == "/a/b.pdf"
    assert ui.clean_path("/a/My\\ File.pdf") == "/a/My File.pdf"
    assert ui.clean_path("") == ""
    # An apostrophe inside a name is not shell quoting.
    assert ui.clean_path("/a/Alex's CV.pdf") == "/a/Alex's CV.pdf"


def test_path_returns_none_when_blank(monkeypatch):
    monkeypatch.setattr(ui, "text", lambda *a, **k: "")
    assert ui.path("file?") is None


def test_path_expands_home_and_checks_existence(monkeypatch, tmp_path):
    f = tmp_path / "cv.pdf"; f.write_text("x")
    monkeypatch.setattr(ui, "text", lambda *a, **k: f"'{f}'")
    assert ui.path("file?") == f


def test_path_reprompts_until_the_file_exists(monkeypatch, tmp_path, capsys):
    f = tmp_path / "cv.pdf"; f.write_text("x")
    answers = iter([str(tmp_path / "nope.pdf"), str(f)])
    monkeypatch.setattr(ui, "interactive", lambda: True)
    monkeypatch.setattr(ui, "text", lambda *a, **k: next(answers))
    assert ui.path("file?") == f
    assert "No such file" in capsys.readouterr().out


def test_path_gives_up_once_when_not_interactive(monkeypatch, tmp_path):
    monkeypatch.setattr(ui, "text", lambda *a, **k: str(tmp_path / "nope.pdf"))
    assert ui.path("file?") is None


def test_path_can_accept_a_file_that_does_not_exist_yet(monkeypatch, tmp_path):
    target = tmp_path / "new.json"
    monkeypatch.setattr(ui, "text", lambda *a, **k: str(target))
    assert ui.path("where?", must_exist=False) == target


# ── Progress ─────────────────────────────────────────────────────────────────

def test_spinner_prints_a_line_when_not_interactive(capsys):
    with ui.spinner("Scoring"):
        pass
    assert "Scoring..." in capsys.readouterr().out


def test_spinner_still_yields_and_propagates(capsys):
    ran = []
    with ui.spinner("work"):
        ran.append(1)
    assert ran == [1]
    with pytest.raises(ValueError):
        with ui.spinner("work"):
            raise ValueError("boom")


def test_hint_prints_plainly_when_not_interactive(capsys):
    ui.hint("solo means no handoffs")
    assert "solo means no handoffs" in capsys.readouterr().out


def test_table_fallback_prints_rows(capsys):
    ui.table(["name", "score"], [["figma", "72"], ["stripe", "68"]])
    out = capsys.readouterr().out
    assert "figma" in out and "72" in out
    assert "stripe" in out and "68" in out


def _cand(skill, strength="direct"):
    return {"skill": skill, "evidence": "built a 40-component library",
            "source_ref": "experience:Acme", "strength": strength}


def test_evidence_checkbox_fallback_selects_by_index(monkeypatch, capsys):
    monkeypatch.setattr(builtins, "input", lambda _="": "2")
    picked = ui.evidence_checkbox("pick", [_cand("Design Systems"), _cand("GraphQL")])
    assert [c["skill"] for c in picked] == ["GraphQL"]
    out = capsys.readouterr().out
    # the evidence is the whole point of this widget -- it must be visible
    assert "40-component library" in out
    assert "experience:Acme" in out


def test_evidence_checkbox_empty_input_selects_nothing(monkeypatch):
    monkeypatch.setattr(builtins, "input", lambda _="": "")
    assert ui.evidence_checkbox("pick", [_cand("Design Systems")]) == []


def test_evidence_checkbox_delegates_to_console(monkeypatch):
    monkeypatch.setattr(ui, "interactive", lambda: True)
    monkeypatch.setattr(ui.console, "available", lambda: True)

    candidates = [_cand("Design Systems", strength="direct"),
                  _cand("GraphQL", strength="implied")]
    seen = {}

    def fake_pick(msg, options, *, multi=False, detail=None, precheck=None):
        seen["msg"] = msg
        seen["multi"] = multi
        seen["options"] = list(options)
        seen["detail"] = [detail(o) for o in options]
        seen["precheck"] = [precheck(o) for o in options]
        return [options[1]]                       # check the second option only

    monkeypatch.setattr(ui.console, "pick", fake_pick)
    result = ui.evidence_checkbox("pick skills", candidates)

    assert seen["multi"] is True
    # OPTIONS carry the skill names (an index prefix is acceptable, per batch.select_targets).
    assert [o.split(". ", 1)[-1] for o in seen["options"]] == ["Design Systems", "GraphQL"]
    # detail(option) is the evidence line for the RIGHT candidate.
    assert seen["detail"][0] == ui._evidence_line(candidates[0]).strip()
    assert seen["detail"][1] == ui._evidence_line(candidates[1]).strip()
    assert "40-component library" in seen["detail"][0]
    # precheck: True for a direct candidate, False for an implied one.
    assert seen["precheck"] == [True, False]
    # The return value is the original candidate DICTS matching what pick returned.
    assert result == [candidates[1]]
    assert result[0] is candidates[1]


def test_evidence_checkbox_console_addresses_duplicate_skill_names(monkeypatch):
    monkeypatch.setattr(ui, "interactive", lambda: True)
    monkeypatch.setattr(ui.console, "available", lambda: True)

    first = _cand("React", strength="direct")
    first["evidence"] = "built the design system in React"
    second = _cand("React", strength="implied")
    second["evidence"] = "reviewed a teammate's React PR"
    seen = {}

    def fake_pick(msg, options, *, multi=False, detail=None, precheck=None):
        seen["options"] = list(options)
        seen["detail"] = [detail(o) for o in options]
        seen["precheck"] = [precheck(o) for o in options]
        return [options[1]]                       # check the SECOND "React" only

    monkeypatch.setattr(ui.console, "pick", fake_pick)
    result = ui.evidence_checkbox("pick", [first, second])

    # Each candidate is individually addressable despite the shared skill string.
    assert len(seen["options"]) == 2
    assert seen["options"][0] != seen["options"][1]
    assert "built the design system" in seen["detail"][0]
    assert "reviewed a teammate" in seen["detail"][1]
    assert seen["precheck"] == [True, False]
    # Checking one returns exactly one candidate -- the right one.
    assert result == [second]
    assert result[0] is second


def test_merge_diff_shows_conflicts_and_counts(capsys):
    ui.merge_diff({
        "matched": [{"existing_ref": "Acme · Senior Designer",
                     "new_bullets": ["shipped a design system", "ran research"],
                     "conflicts": ["dates differ: 2021-2023 vs 2021-2024"]}],
        "added": [{"_ref": "Bolt · Design Lead"}],
    })
    out = capsys.readouterr().out
    assert "Acme" in out
    assert "+ 2 new bullets" in out
    assert "dates differ" in out
    assert "Bolt" in out


def test_table_handles_ragged_rows(capsys):
    # Row shorter than headers
    ui.table(["a", "b", "c"], [["x", "y"]])
    out = capsys.readouterr().out
    assert "x" in out and "y" in out
    # Empty row
    ui.table(["a", "b"], [[]])
    out = capsys.readouterr().out
    # Should not raise IndexError


def test_evidence_checkbox_precheck_coverage(monkeypatch, capsys):
    monkeypatch.setattr(ui, "interactive", lambda: True)

    checked_states = {}

    class FakeChoice:
        def __init__(self, label, value=None, checked=False):
            self.label = label
            self.value = value
            self.checked = checked
            checked_states[label] = checked

    class FakeQ:
        def __init__(self, val):
            self._val = val

        def ask(self):
            return self._val

    monkeypatch.setattr(ui, "_q", type("M", (), {
        "Choice": FakeChoice,
        "checkbox": staticmethod(lambda *a, **k: FakeQ([])),
    }))

    candidates = [_cand("Direct Skill", strength="direct"), _cand("Implied Skill", strength="implied")]
    ui.evidence_checkbox("pick", candidates)

    # Find the labels for each candidate
    labels = [f'{c["skill"]}\n{ui._evidence_line(c)}' for c in candidates]

    # Direct skill should be checked (precheck returns True for direct)
    assert checked_states[labels[0]] is True
    # Implied skill should not be checked (precheck returns False for implied)
    assert checked_states[labels[1]] is False


def test_evidence_checkbox_questionary_addresses_duplicate_labels(monkeypatch):
    # Same regression as test_evidence_checkbox_console_addresses_duplicate_skill_names,
    # for the questionary branch three lines below it: two candidates rendering an
    # identical label must still resolve independently, not collapse to one Choice.
    monkeypatch.setattr(ui, "interactive", lambda: True)
    monkeypatch.setattr(ui.console, "available", lambda: False)

    first = _cand("React", strength="direct")
    second = _cand("React", strength="direct")   # identical skill AND evidence -> identical label

    class FakeChoice:
        def __init__(self, label, value=None, checked=False):
            self.label = label
            self.value = value
            self.checked = checked

    made = []

    class FakeQ:
        def __init__(self, val):
            self._val = val

        def ask(self):
            return self._val

    def fake_checkbox(msg, choices):
        made.extend(choices)
        # "select" only the second Choice object, by its distinguishing value.
        return FakeQ([c.value for c in choices if c.value == 1])

    monkeypatch.setattr(ui, "_q", type("M", (), {
        "Choice": FakeChoice,
        "checkbox": staticmethod(fake_checkbox),
    }))

    result = ui.evidence_checkbox("pick", [first, second])

    assert len(made) == 2                        # both candidates got their own Choice
    assert result == [second]
    assert result[0] is second
