"""shell.py: a REPL that must survive anything one command does to it."""
import pytest

import shell


@pytest.fixture
def session(tmp_path, monkeypatch):
    monkeypatch.setattr(shell.ui, "interactive", lambda: False)
    return shell.Session(root=tmp_path, backend="openclaw", model="m",
                         profile=None, output_dir=tmp_path / "out")


def test_unknown_command_reports_and_continues(session, capsys):
    assert shell.dispatch(session, "/nope") is True
    assert "unknown command" in capsys.readouterr().out.lower()


def test_exit_ends_the_session(session):
    assert shell.dispatch(session, "/exit") is False


def test_blank_line_is_a_no_op(session):
    assert shell.dispatch(session, "   ") is True


def test_bare_path_is_treated_as_jd(session, monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(shell, "cmd_jd", lambda s, a: seen.append(a))
    shell.dispatch(session, "/Users/me/jd.txt")
    assert seen == [["/Users/me/jd.txt"]]


def test_bare_url_is_treated_as_jd_with_the_full_interactive_flow(session, monkeypatch):
    # A pasted job link runs the SAME flow as typing "/jd <url>" -- score, then
    # the questions, then generate. No auto -y (that fought the point of asking).
    seen = []
    monkeypatch.setattr(shell, "cmd_jd", lambda s, a: seen.append(a))
    shell.dispatch(session, "https://boards.greenhouse.io/acme/jobs/1")
    shell.dispatch(session, "/jd https://boards.greenhouse.io/acme/jobs/1")
    assert seen == [["https://boards.greenhouse.io/acme/jobs/1"],
                    ["https://boards.greenhouse.io/acme/jobs/1"]]


def test_a_bare_word_that_is_not_a_command_path_or_url_is_rejected(session, monkeypatch, capsys):
    called = []
    monkeypatch.setattr(shell, "cmd_jd", lambda s, a: called.append(a))
    assert shell.dispatch(session, "help") is True
    assert not called                                   # not routed to /jd
    assert "unknown command" in capsys.readouterr().out.lower()
    # a bare filename (has a dot) still routes to /jd
    shell.dispatch(session, "jd.txt")
    assert called == [["jd.txt"]]


def test_shortcuts_resolve_to_full_commands(session, monkeypatch):
    seen = []
    monkeypatch.setattr(shell, "cmd_status", lambda s, a: seen.append("status"))
    monkeypatch.setattr(shell, "cmd_recompile", lambda s, a: seen.append("recompile"))
    shell.dispatch(session, "/i")
    shell.dispatch(session, "/r")
    assert seen == ["status", "recompile"]


def test_a_raising_command_does_not_end_the_session(session, monkeypatch, capsys):
    def boom(s, a):
        raise RuntimeError("model exploded")
    monkeypatch.setattr(shell, "cmd_jd", boom)
    assert shell.dispatch(session, "/jd x.txt") is True
    assert "model exploded" in capsys.readouterr().out


def test_sys_exit_inside_a_command_does_not_end_the_session(session, monkeypatch, capsys):
    # tailor.read_jd and tailor.run call sys.exit(1) on a missing or empty JD. In a
    # one-shot script that is correct; in a REPL it would kill the session.
    def bail(s, a):
        raise SystemExit(1)
    monkeypatch.setattr(shell, "cmd_jd", bail)
    assert shell.dispatch(session, "/jd x.txt") is True


def test_keyboard_interrupt_aborts_the_command_only(session, monkeypatch, capsys):
    def interrupted(s, a):
        raise KeyboardInterrupt
    monkeypatch.setattr(shell, "cmd_jd", interrupted)
    assert shell.dispatch(session, "/jd x.txt") is True
    assert "aborted" in capsys.readouterr().out.lower()


def test_bare_path_with_spaces_routes_to_jd_intact(session, monkeypatch):
    # Dragging a file into a terminal produces a path with spaces. line.split() would
    # truncate it, so the bare line must reach cmd_jd whole, as one argument.
    seen = []
    monkeypatch.setattr(shell, "cmd_jd", lambda s, a: seen.append(a))
    shell.dispatch(session, "/Users/me/My Resume.pdf")
    assert seen == [["/Users/me/My Resume.pdf"]]


def _capture_run(monkeypatch):
    captured = {}
    monkeypatch.setattr(shell.tailor, "run", lambda args: captured.update(vars(args)))
    return captured


def test_bare_backslash_escaped_path_resolves_to_the_real_file(session, monkeypatch, tmp_path):
    # A backslash-escaped space is one form a terminal produces. ui.clean_path must
    # strip it in cmd_jd, or the path fails exists() as "JDError: file not found" --
    # the original bug that started this project.
    real = tmp_path / "My Resume.txt"
    real.write_text("Responsibilities: design.")
    monkeypatch.setattr(shell.jdsource, "load_jd", lambda a: (real.read_text(), real.name))
    captured = _capture_run(monkeypatch)
    shell.dispatch(session, str(real).replace(" ", r"\ "))
    assert captured["jd_file"] == str(real)


def test_bare_single_quoted_path_resolves_to_the_real_file(session, monkeypatch, tmp_path):
    # The other terminal form: a single-quoted drag. Quotes are shell syntax, not
    # part of the name.
    real = tmp_path / "My Resume.txt"
    real.write_text("Responsibilities: design.")
    monkeypatch.setattr(shell.jdsource, "load_jd", lambda a: (real.read_text(), real.name))
    captured = _capture_run(monkeypatch)
    shell.dispatch(session, "'" + str(real) + "'")
    assert captured["jd_file"] == str(real)


def test_a_url_is_not_quote_stripped(session, monkeypatch):
    seen = []
    monkeypatch.setattr(shell, "jd_to_path", lambda a: seen.append(a) or shell.Path("/x"))
    monkeypatch.setattr(shell.tailor, "run", lambda args: None)
    shell.cmd_jd(session, ["https://acme.com/jobs/1"])
    assert seen == ["https://acme.com/jobs/1"]


def test_explicit_output_dir_flag_beats_the_session_default(session, monkeypatch, tmp_path):
    p = tmp_path / "jd.txt"
    p.write_text("Responsibilities: design.")
    monkeypatch.setattr(shell, "jd_to_path", lambda a: p)
    captured = _capture_run(monkeypatch)
    session.output_dir = tmp_path / "session_out"
    shell.cmd_jd(session, [str(p), "-o", str(tmp_path / "user_out")])
    assert captured["output_dir"] == str(tmp_path / "user_out")


def test_reload_survives_a_missing_content_json(tmp_path, capsys):
    # Profile.load calls sys.exit(1) (SystemExit, not Exception) on a missing file.
    s = shell.Session(root=tmp_path, backend="openclaw", model="m")
    s.reload()
    assert s.profile is None
    assert "/setup" in capsys.readouterr().out


def test_main_exits_cleanly_when_no_cli_is_installed(monkeypatch, capsys):
    monkeypatch.setattr(shell, "_first_run", lambda root: False)

    def no_cli(*a, **k):
        raise shell.llm.LLMError("no supported CLI found on PATH")

    monkeypatch.setattr(shell.llm, "resolve", no_cli)
    with pytest.raises(SystemExit) as exc:
        shell.main()
    assert exc.value.code == 1
    assert "no supported CLI" in capsys.readouterr().out


def test_batch_is_routed_and_contained(session, monkeypatch, capsys):
    # batch.py exists as of Task 9. A fault raised anywhere on the /batch route must
    # be caught by dispatch -- the REPL keeps running and the error is printed.
    import batch

    def boom(args):
        raise RuntimeError("batch exploded")

    session.profile = object()          # get past cmd_batch's corpus guard
    monkeypatch.setattr(batch, "expand", boom)
    assert shell.dispatch(session, "/batch some/glob") is True
    assert "batch exploded" in capsys.readouterr().out


def test_help_lists_every_command(session, capsys):
    shell.dispatch(session, "/help")
    out = capsys.readouterr().out
    for cmd in ("/jd", "/batch", "/skills", "/add", "/setup", "/recompile", "/status", "/exit"):
        assert cmd in out


def test_jd_to_path_passes_a_local_file_through(tmp_path, monkeypatch):
    p = tmp_path / "jd.txt"
    p.write_text("Responsibilities: design things. " * 30)
    monkeypatch.setattr(shell.jdsource, "load_jd", lambda a: (p.read_text(), "jd.txt"))
    got = shell.jd_to_path(str(p))
    assert got.read_text() == p.read_text()


def test_jd_to_path_materialises_a_url(monkeypatch):
    monkeypatch.setattr(shell.jdsource, "load_jd", lambda a: ("fetched jd text", "acme.com/1"))
    got = shell.jd_to_path("https://acme.com/1")
    assert got.exists()
    assert got.read_text() == "fetched jd text"


def test_cmd_jd_builds_a_real_namespace(session, monkeypatch, tmp_path):
    p = tmp_path / "jd.txt"
    p.write_text("Responsibilities: design.")
    monkeypatch.setattr(shell, "jd_to_path", lambda a: p)
    captured = {}
    monkeypatch.setattr(shell.tailor, "run", lambda args: captured.update(vars(args)))
    shell.cmd_jd(session, [str(p)])
    # Built through tailor.parse_args, so every flag default comes along for free.
    assert captured["jd_file"] == str(p)
    assert captured["no_interactive"] is False
    assert captured["timeout"] == shell.tailor.llm.DEFAULT_TIMEOUT


def test_cmd_jd_passes_flags_through(session, monkeypatch, tmp_path):
    p = tmp_path / "jd.txt"
    p.write_text("Responsibilities: design.")
    monkeypatch.setattr(shell, "jd_to_path", lambda a: p)
    captured = {}
    monkeypatch.setattr(shell.tailor, "run", lambda args: captured.update(vars(args)))
    shell.cmd_jd(session, [str(p), "--cv", "-c"])
    assert captured["cv"] is True
    assert captured["cover_letter"] is True


def test_cmd_jd_with_no_argument_opens_the_paste_editor(session, monkeypatch, capsys):
    # bare /jd -> paste the JD straight in
    monkeypatch.setattr(shell.ui, "editor", lambda *a, **k: "")
    shell.cmd_jd(session, [])
    assert "nothing pasted" in capsys.readouterr().out.lower()

    captured = _capture_run(monkeypatch)
    monkeypatch.setattr(shell.ui, "editor",
                        lambda *a, **k: "Responsibilities: lead design. Qualifications: 5y.")
    shell.cmd_jd(session, ["--cv"])                    # no path, but a flag
    assert captured["cv"] is True
    assert captured["jd_file"] and captured["jd_file"].endswith(".txt")


# ── /new /edit /rc /recompile picker ──────────────────────────────────────

import time as _time


def test_new_and_edit_dispatch_to_entries(session, monkeypatch):
    seen = {}
    monkeypatch.setattr(shell.entries, "cmd_new",
                        lambda root, *, backend, model, section=None: seen.setdefault("new", (backend, section)) or True)
    monkeypatch.setattr(shell.entries, "cmd_edit",
                        lambda root, *, backend, model: seen.__setitem__("edit", backend) or False)
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


def test_recompile_picker_ctrl_c_cancels_without_recompiling(tmp_path, monkeypatch):
    monkeypatch.setattr(shell.ui, "interactive", lambda: True)
    app = tmp_path / "Acme" / "PD"
    app.mkdir(parents=True)
    monkeypatch.setattr(shell.recompile, "recent_apps",
                        lambda base: [{"dir": app, "ref": "Acme · PD",
                                       "mtime": _time.time(), "modes": ["resume"]}])
    monkeypatch.setattr(shell.console, "ask_choice",
                        lambda *a, **k: shell.console.CHOICE_CANCELLED)
    monkeypatch.setattr(shell.recompile, "recompile_folder",
                        lambda *a, **k: pytest.fail("must not recompile on Ctrl-C"))
    s = shell.Session(root=tmp_path, backend="c", model="m")
    shell.cmd_recompile(s, [])            # returns without recompiling = pass


def test_recompile_picker_base_dir_honours_the_env_var(tmp_path, monkeypatch):
    monkeypatch.setattr(shell.ui, "interactive", lambda: True)
    monkeypatch.setenv("RESUME_TEX_OUTPUT_DIR", str(tmp_path / "envtree"))
    seen = {}
    def _capture(base):
        seen["base"] = base
        return []
    monkeypatch.setattr(shell.recompile, "recent_apps", _capture)
    s = shell.Session(root=tmp_path, backend="c", model="m")   # no output_dir, no profile
    shell.cmd_recompile(s, [])
    assert str(seen["base"]) == str(tmp_path / "envtree")


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


def test_partial_command_resolves_to_its_first_match(session, monkeypatch):
    seen = []
    monkeypatch.setattr(shell, "cmd_recompile", lambda s, a: seen.append("recompile"))
    monkeypatch.setattr(shell, "cmd_skills", lambda s, a: seen.append("skills"))
    assert shell.dispatch(session, "/exi") is False        # /exi -> /exit
    shell.dispatch(session, "/rec")                         # -> /recompile
    shell.dispatch(session, "/s")                           # ambiguous -> first: /skills
    assert seen == ["recompile", "skills"]


def test_unresolvable_partial_is_still_unknown(session, capsys):
    shell.dispatch(session, "/zzz")
    assert "unknown command" in capsys.readouterr().out.lower()


def test_review_fetched_jd_is_skipped_when_not_interactive(monkeypatch):
    monkeypatch.setattr(shell.ui, "interactive", lambda: False)
    monkeypatch.setattr(shell.console, "pick",
                        lambda *a, **k: pytest.fail("must not prompt non-interactively"))
    assert shell._review_fetched_jd("the jd text", "acme.com/1") == "the jd text"


def test_review_fetched_jd_use_returns_the_text(monkeypatch, capsys):
    monkeypatch.setattr(shell.ui, "interactive", lambda: True)
    monkeypatch.setattr(shell.console, "pick", lambda *a, **k: "Use this JD")
    assert shell._review_fetched_jd("responsibilities: x " * 30, "acme.com/1").startswith("responsibilities")
    assert "acme.com/1" in capsys.readouterr().out          # the compact preview line printed


def test_review_fetched_jd_shows_the_jd_as_a_collapsible_block(monkeypatch, capsys):
    monkeypatch.setattr(shell.ui, "interactive", lambda: True)
    monkeypatch.setattr(shell.console, "pick", lambda *a, **k: "Use this JD")
    folds = []
    monkeypatch.setattr(shell.console, "fold",
                        lambda summary, full: folds.append((summary, full)) or True)
    text = "UNIQUE_MARKER responsibilities and qualifications " * 20
    assert shell._review_fetched_jd(text, "acme.com/1") == text
    assert folds and folds[0][1] == text                    # the whole JD went into a fold
    assert "acme.com/1" in folds[0][0] and "words" in folds[0][0]


def test_review_fetched_jd_falls_back_to_a_plain_dump_without_a_shell(monkeypatch, capsys):
    monkeypatch.setattr(shell.ui, "interactive", lambda: True)
    monkeypatch.setattr(shell.console, "pick", lambda *a, **k: "Use this JD")
    monkeypatch.setattr(shell.console, "fold", lambda *a, **k: False)   # no full-screen shell
    text = "UNIQUE_MARKER responsibilities " * 20
    shell._review_fetched_jd(text, "acme.com/1")
    assert "UNIQUE_MARKER" in capsys.readouterr().out


def test_review_fetched_jd_paste_replaces(monkeypatch):
    monkeypatch.setattr(shell.ui, "interactive", lambda: True)
    picks = iter(["Paste a replacement", "Use this JD"])
    monkeypatch.setattr(shell.console, "pick", lambda *a, **k: next(picks))
    monkeypatch.setattr(shell.ui, "editor", lambda *a, **k: "the corrected jd")
    assert shell._review_fetched_jd("wrong fetched blob", "acme.com/1") == "the corrected jd"


def test_review_fetched_jd_cancel_raises_and_dispatch_contains_it(session, monkeypatch, capsys):
    monkeypatch.setattr(shell.ui, "interactive", lambda: True)
    monkeypatch.setattr(shell.console, "pick", lambda *a, **k: "Cancel")
    monkeypatch.setattr(shell.jdsource, "load_jd", lambda a: ("blob", "acme.com/1"))
    # straight through jd_to_path -> raises JDCancelled
    with pytest.raises(shell.JDCancelled):
        shell.jd_to_path("https://acme.com/1")
    # and dispatch swallows it, session survives
    assert shell.dispatch(session, "https://acme.com/1") is True
    assert "cancelled" in capsys.readouterr().out.lower()
