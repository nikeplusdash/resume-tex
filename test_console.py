"""console.py: a bottom input region and full-screen pickers, both optional."""
import builtins

import pytest

import console
import ui


def test_available_is_false_off_tty(monkeypatch):
    monkeypatch.setattr(console.sys.stdin, "isatty", lambda: False)
    assert console.available() is False


def test_available_is_false_without_prompt_toolkit(monkeypatch):
    monkeypatch.setattr(console.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(console, "_pt", None)
    assert console.available() is False


def test_prompt_falls_back_to_input(monkeypatch):
    monkeypatch.setattr(console, "available", lambda: False)
    monkeypatch.setattr(builtins, "input", lambda _="": "/status")
    assert console.prompt("> ") == "/status"


def test_prompt_treats_eof_as_exit(monkeypatch):
    monkeypatch.setattr(console, "available", lambda: False)

    def eof(_=""):
        raise EOFError
    monkeypatch.setattr(builtins, "input", eof)
    assert console.prompt("> ") == "/exit"


def test_pick_falls_back_to_numbered_list(monkeypatch, capsys):
    monkeypatch.setattr(console, "available", lambda: False)
    monkeypatch.setattr(builtins, "input", lambda _="": "2")
    assert console.pick("choose", ["a", "b", "c"]) == "b"


def test_pick_multi_falls_back_to_indices(monkeypatch):
    monkeypatch.setattr(console, "available", lambda: False)
    monkeypatch.setattr(builtins, "input", lambda _="": "1,3")
    assert console.pick("choose", ["a", "b", "c"], multi=True) == ["a", "c"]


def test_pick_shows_detail_in_the_fallback(monkeypatch, capsys):
    monkeypatch.setattr(console, "available", lambda: False)
    monkeypatch.setattr(builtins, "input", lambda _="": "1")
    console.pick("choose", ["Replace it entirely"],
                 detail=lambda o: "loses the notes you have collected")
    # The consequence must be on screen at the moment of choosing, not in an
    # earlier hint that has already scrolled away.
    assert "loses the notes" in capsys.readouterr().out


def test_ui_select_still_works_when_console_unavailable(monkeypatch):
    # ui.select's existing contract must not change; test_ui.py depends on it.
    monkeypatch.setattr(ui, "interactive", lambda: False)
    monkeypatch.setattr(builtins, "input", lambda _="": "2")
    assert ui.select("pick", ["a", "b", "c"]) == "b"


def test_ui_select_delegates_to_console_when_available(monkeypatch):
    monkeypatch.setattr(ui, "interactive", lambda: True)
    monkeypatch.setattr(ui.console, "available", lambda: True)
    monkeypatch.setattr(ui.console, "pick", lambda msg, opts, **k: "b")
    assert ui.select("pick", ["a", "b", "c"]) == "b"


# ── full-screen bridge: no real Application/terminal, just the state machine ──
import threading
import time


def test_bridge_write_appends_to_output_and_log(tmp_path):
    log = tmp_path / "session.log"
    bridge = console._Bridge(log_path=log)
    bridge.write("hello\n")
    bridge.write("world\n")
    assert bridge.output.text == "hello\nworld\n"
    assert log.read_text().endswith("hello\nworld\n")


def test_bridge_ask_blocks_worker_thread_until_submitted():
    bridge = console._Bridge()
    result = {}

    def worker():
        result["answer"] = bridge.ask("Continue?", default="y")

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    # The worker is blocked in ask() until the UI thread "types" a reply and
    # submits -- there's no app running here, so _call() runs synchronously and
    # the question is visible once the worker thread has been scheduled (give it
    # a beat, the way the other bridge tests do -- t.start() does not guarantee
    # the target has run yet).
    deadline = time.time() + 2
    while bridge.mode != "question" and time.time() < deadline:
        time.sleep(0.001)
    assert t.is_alive()
    assert bridge.mode == "question"
    assert bridge.question_label == "Continue?"
    assert bridge.question_buf.text == ""        # default is shadow text, not pre-filled
    assert bridge.question_default == "y"        # exposed for the auto-suggest to show
    bridge.question_buf.text = "n"
    bridge._submit_question()
    t.join(timeout=2)
    assert result["answer"] == "n"
    assert bridge.mode == "busy"
    assert bridge.question_default == ""         # cleared so it can't leak to the next prompt


def test_bridge_ask_multiline_prefills_a_draft_instead_of_shadowing_it():
    bridge = console._Bridge()

    def worker():
        bridge.ask("A1", default="line one\nline two", multiline=True)

    threading.Thread(target=worker, daemon=True).start()
    deadline = time.time() + 2
    while bridge.mode != "question" and time.time() < deadline:
        time.sleep(0.001)
    assert bridge.question_buf.text == "line one\nline two"   # real pre-fill, editable
    assert bridge.question_default == ""
    bridge._cancel_question()


def test_bridge_ask_empty_submit_returns_empty_string_for_caller_to_default():
    # ui.confirm/text/editor each apply their own default when console.ask returns
    # "" -- which is exactly what the shadow text was previewing.
    bridge = console._Bridge()
    result = {}

    def worker():
        result["answer"] = bridge.ask("Name?", default="Alex")

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    deadline = time.time() + 2
    while bridge.mode != "question" and time.time() < deadline:
        time.sleep(0.001)
    bridge._submit_question()                    # Enter on an untouched field
    t.join(timeout=2)
    assert result["answer"] == ""


def test_default_suggest_shows_default_only_while_field_is_empty():
    bridge = console._Bridge()
    bridge.question_default = "y"
    sugg = bridge.question_buf.auto_suggest
    from prompt_toolkit.document import Document
    assert sugg.get_suggestion(bridge.question_buf, Document("")).text == "y"
    assert sugg.get_suggestion(bridge.question_buf, Document("n")) is None


def test_shell_suggest_completes_an_unambiguous_command():
    from prompt_toolkit.document import Document
    sugg = console._ShellSuggest(console.COMMANDS)
    assert sugg.get_suggestion(None, Document("/hel")).text == "p"      # -> /help
    assert sugg.get_suggestion(None, Document("/s")) is None            # ambiguous
    assert sugg.get_suggestion(None, Document("/help x")) is None       # past the command


def test_bridge_cancel_question_returns_empty_string():
    bridge = console._Bridge()
    result = {}

    def worker():
        result["answer"] = bridge.ask("Save this?")

    t = threading.Thread(target=worker)
    t.start()
    deadline = time.time() + 2
    while bridge.mode != "question" and time.time() < deadline:
        time.sleep(0.001)
    assert bridge.mode == "question"
    bridge._cancel_question()
    t.join(timeout=2)
    assert result["answer"] == ""


def test_bridge_busy_line_shows_and_clears_while_status_bar_stays_the_model():
    bridge = console._Bridge()
    assert bridge.status_text("openclaw/m") == "  openclaw/m"    # bottom bar: model only
    assert bridge.busy_line() == ""
    with bridge.busy("Scoring"):
        assert "Scoring" in bridge.busy_line()                   # spinner line: above input
        assert bridge.status_text("openclaw/m") == "  openclaw/m"
    assert bridge.busy_line() == ""


_META = [
    ("/status", "i", "what is loaded"),
    ("/skills", "s", "fill in your skills and derive more"),
    ("/setup", "", "re-run setup"),
    ("/recompile", "r", "re-render a saved application"),
    ("/edit", "e", "browse content.json"),
    ("/exit", "q", "leave"),
]


def _frags(completion):
    return [(f[0], f[1]) for f in completion.display]


def test_command_completer_matches_names_past_the_leading_slash():
    from prompt_toolkit.document import Document
    c = console._CommandCompleter(_META)
    got = {x.text for x in c.get_completions(Document("/s", 2), None)}
    assert got == {"/status", "/skills", "/setup"}


def test_command_completer_resolves_a_shortcut_and_never_gives_it_its_own_row():
    from prompt_toolkit.document import Document
    c = console._CommandCompleter(_META)
    comps = list(c.get_completions(Document("/i", 2), None))
    assert [x.text for x in comps] == ["/status"]          # /i -> /status, one row
    assert "".join(t for _s, t in _frags(comps[0])).startswith("/status")
    # typing "/e" surfaces both real /e... commands, no alias rows
    assert {x.text for x in c.get_completions(Document("/e", 2), None)} == {"/edit", "/exit"}


def test_command_completer_shows_the_description_as_meta_and_the_shortcut_on_the_row():
    from prompt_toolkit.document import Document
    c = console._CommandCompleter(_META)
    comp = next(x for x in c.get_completions(Document("/rec", 4), None) if x.text == "/recompile")
    assert comp.display_meta_text == "re-render a saved application"
    assert ("class:completion-menu.shortcut", "  ·r") in _frags(comp)


def test_command_completer_highlights_the_typed_prefix():
    from prompt_toolkit.document import Document
    c = console._CommandCompleter(_META)
    comp = next(x for x in c.get_completions(Document("/sta", 4), None) if x.text == "/status")
    assert _frags(comp)[0] == ("class:completion-menu.match", "/sta")
    assert _frags(comp)[1] == ("", "tus")


def test_command_completer_ignores_non_command_and_mid_argument_input():
    from prompt_toolkit.document import Document
    c = console._CommandCompleter(_META)
    assert list(c.get_completions(Document("hello", 5), None)) == []
    assert list(c.get_completions(Document("/jd path.txt", 12), None)) == []   # has a space


def test_default_command_meta_drops_the_aliases():
    names = {n for n, _s, _d in console._default_command_meta()}
    assert "/status" in names and "/recompile" in names
    assert names.isdisjoint({"/i", "/info", "/rc"})


def test_pick_routes_through_ask_choice_when_fullscreen(monkeypatch):
    monkeypatch.setattr(console, "in_fullscreen", lambda: True)
    seen = {}

    def fake_ask_choice(label, values, *, multi=False, default_values=None):
        seen["label"] = label
        seen["values"] = values
        seen["multi"] = multi
        return values[1][0]
    monkeypatch.setattr(console, "ask_choice", fake_ask_choice)
    assert console.pick("choose", ["a", "b", "c"]) == "b"
    assert seen["multi"] is False


# ── full-screen app: real prompt_toolkit Application, faked terminal ─────────
import asyncio


def _drive_shell(script, on_line=None):
    """Run build_app() headless and feed it `script(feed, bridge)` -- an async
    callback that sends keystrokes and inspects bridge state. Returns whatever
    the callback returns. `on_line` overrides the module `dispatch` (a test that
    needs to run its own command handler passes one in)."""
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input.defaults import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    async def main():
        with create_pipe_input() as inp, create_app_session(input=inp, output=DummyOutput()):
            bridge = console._Bridge()
            console._bridge = bridge
            app = console.build_app(bridge, on_line or dispatch, status="openclaw/terra")
            task = asyncio.ensure_future(app.run_async())
            await asyncio.sleep(0.2)

            async def feed(text, wait=0.35):
                inp.send_text(text)
                await asyncio.sleep(wait)

            try:
                return await script(feed, bridge)
            finally:
                try:
                    app.exit()
                except Exception:
                    pass                     # already exited via /exit
                await asyncio.wait_for(task, timeout=3)
                console._bridge = None

    events.clear()
    return asyncio.run(main())


events = []


def dispatch(line):
    events.append(("cmd", line))
    if line == "/ask":
        import ui
        events.append(("text", ui.text("Your name?", default="Alex")))
        events.append(("choice", console.ask_choice("Pick", [("a", "A"), ("b", "B")])))
    if line == "/editor":
        import ui
        events.append(("editor", ui.editor("Extra context (optional — Enter to skip)")))
        events.append(("confirm", ui.confirm("Cover letter?", default=True)))
    if line == "/multi":
        events.append(("multi", console.ask_choice(
            "Pick some", [("a", "A"), ("b", "B"), ("c", "C")], multi=True)))
    return line != "/exit"


def test_fullscreen_multiselect_toggles_until_the_submit_row():
    async def script(feed, bridge):
        await feed("/multi\r")
        assert bridge.mode == "question" and bridge.choice_widget is not None
        await feed(" ")                       # toggle A on
        await feed("j")                       # down to B  (j/k = vi nav on the list)
        await feed(" ")                       # toggle B on
        await feed(" ")                       # toggle B off again -- still not submitted
        await asyncio.sleep(0.1)
        assert bridge.mode == "question"      # nothing submitted yet
        await feed("j")                       # down to C
        await feed("j")                       # down to the Submit row
        await feed("\r")                      # submit
        await asyncio.sleep(0.2)
        assert bridge.mode == "command"
        await feed("/exit\r")

    _drive_shell(script)
    assert ("multi", ["a"]) in events        # only A survived the toggling


def test_fullscreen_multiline_field_submits_on_enter_newline_on_alt_enter():
    # ui.editor (extra context, a pasted JD, a gap draft) must be finishable
    # inside the shell -- a raw multi-line buffer binds Enter to a newline and
    # would leave the run stuck. Enter submits; Alt+Enter inserts the newline.
    async def script(feed, bridge):
        await feed("/editor\r")
        assert bridge.mode == "question" and bridge.question_multiline
        await feed("line one")
        await feed("\x1b\r")                              # Alt+Enter -> newline
        await feed("line two")
        await feed("\r")                                  # Enter -> submit
        await asyncio.sleep(0.2)
        assert bridge.mode == "question"                  # the Yes/No pick that follows
        assert bridge.choice_widget is not None
        await feed("\r")                                  # Enter takes the default (Yes)
        await asyncio.sleep(0.2)
        assert bridge.mode == "command"
        await feed("/exit\r")

    _drive_shell(script)
    assert ("editor", "line one\nline two") in events
    assert ("confirm", True) in events                    # default=True was highlighted


def test_fullscreen_runs_a_command_then_a_grouped_text_and_choice_question():
    async def script(feed, bridge):
        await feed("/ask\r")
        assert bridge.mode == "question" and bridge.choice_widget is None
        assert bridge.question_default == "Alex"          # shadow text, not pre-filled
        assert bridge.question_buf.text == ""
        await feed("\t")                                  # Tab accepts the shadow default
        assert bridge.question_buf.text == "Alex"
        await feed("\r")                                  # submit the text answer
        await asyncio.sleep(0.2)
        assert bridge.choice_widget is not None           # picker renders inline, no pop-up
        await feed("\r")                                  # Enter accepts the highlighted row
        await asyncio.sleep(0.2)
        assert bridge.mode == "command"                   # straight back to the prompt
        await feed("/exit\r")

    _drive_shell(script)
    assert ("text", "Alex") in events
    assert ("choice", "a") in events
    assert events[-1] == ("cmd", "/exit")


def test_fullscreen_ctrl_c_cancels_a_pending_question_without_hanging():
    async def script(feed, bridge):
        await feed("/ask\r")
        assert bridge.mode == "question"
        await feed("\x03")                                # Ctrl-C cancels the text question
        await asyncio.sleep(0.2)
        await feed("\x03")                                # and the choice that follows
        await asyncio.sleep(0.2)
        assert bridge.mode == "command"                   # control came back, no deadlock
        await feed("/exit\r")

    _drive_shell(script)
    assert events[-1] == ("cmd", "/exit")


def test_fullscreen_double_esc_cancels_a_pending_question():
    async def script(feed, bridge):
        await feed("/ask\r")
        assert bridge.mode == "question"
        await feed("\x1b\x1b")                            # double-Esc
        await asyncio.sleep(0.2)
        await feed("\x1b\x1b")                            # and the choice after it
        await asyncio.sleep(0.2)
        assert bridge.mode == "command"
        await feed("/exit\r")

    _drive_shell(script)
    assert events[-1] == ("cmd", "/exit")


def test_fullscreen_double_esc_aborts_a_running_command(monkeypatch):
    import time as _t
    killed = []
    import llm
    monkeypatch.setattr(llm, "terminate_active", lambda: killed.append(True) or 0)

    def slow_dispatch(line):
        events.append(("cmd", line))
        if line == "/slow":
            deadline = _t.time() + 5
            while _t.time() < deadline:       # a Python loop, so the async
                _t.sleep(0.02)               # KeyboardInterrupt lands promptly
            events.append(("finished", line))
        return line != "/exit"

    async def script(feed, bridge):
        await feed("/slow\r")
        await asyncio.sleep(0.3)
        assert bridge.mode == "busy" and bridge._worker is not None
        await feed("\x1b\x1b")                            # double-Esc aborts it
        await asyncio.sleep(0.4)
        assert bridge.mode == "command"                   # back at the prompt, not after 5s
        assert bridge._worker is None
        await feed("/exit\r")

    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input.defaults import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    async def main():
        with create_pipe_input() as inp, create_app_session(input=inp, output=DummyOutput()):
            bridge = console._Bridge()
            console._bridge = bridge
            app = console.build_app(bridge, slow_dispatch, status="s")
            task = asyncio.ensure_future(app.run_async())
            await asyncio.sleep(0.2)

            async def feed(text, wait=0.35):
                inp.send_text(text)
                await asyncio.sleep(wait)
            try:
                await script(feed, bridge)
            finally:
                try:
                    app.exit()
                except Exception:
                    pass
                await asyncio.wait_for(task, timeout=3)
                console._bridge = None

    events.clear()
    asyncio.run(main())
    assert killed == [True]                               # model subprocess was signalled
    assert ("finished", "/slow") not in events            # the run did not complete


def test_ui_widgets_ignore_console_when_not_interactive(monkeypatch):
    # console.available() reads sys.stdin.isatty() directly, so `pytest -s` would
    # otherwise send these three widgets down the console path and break their
    # existing tests.
    monkeypatch.setattr(ui, "interactive", lambda: False)
    monkeypatch.setattr(ui.console, "available", lambda: True)
    monkeypatch.setattr(ui.console, "pick",
                        lambda *a, **k: pytest.fail("must not reach console"))
    monkeypatch.setattr(builtins, "input", lambda _="": "2")
    assert ui.select("pick", ["a", "b", "c"]) == "b"


def test_fullscreen_new_experience_end_to_end(monkeypatch):
    import entries, install, merge
    monkeypatch.setattr(entries, "extract", lambda *a, **k: {
        "company": "Acme", "title": "PD", "dates": "2024", "location": "NYC",
        "bullets": ["did the thing"]})
    monkeypatch.setattr(entries, "reform_bullets", lambda b, e, **k: list(b))
    monkeypatch.setattr(install, "entry_prompts", lambda *a, **k: {"experience:Acme · PD": []})
    monkeypatch.setattr(merge, "backup", lambda *a, **k: [])
    written = {}
    monkeypatch.setattr(entries, "_save", lambda p, c: written.update(c))

    def disp(line):
        events.append(("cmd", line))
        if line == "/newexp":
            entries.cmd_new(__import__("pathlib").Path("/tmp/x"),
                            backend="c", model="m", section="experience")
        return line != "/exit"

    async def script(feed, bridge):
        # section is passed, so the first prompt is the paste editor
        await feed("/newexp\r")
        await asyncio.sleep(0.2)
        assert bridge.mode == "question"
        await feed("PD at Acme\r")            # blob
        await asyncio.sleep(0.3)
        # field confirms (company/title/dates/location/link) - Enter takes each default
        for _ in range(5):
            await feed("\r")
            await asyncio.sleep(0.15)
        # keep-bullets multi -> move to Submit row, submit
        await feed("j"); await feed("\r"); await asyncio.sleep(0.2)
        await feed("\r")                      # "add more bullets" editor -> blank
        await asyncio.sleep(0.2)
        await feed("\r")                      # "sharpen?" Yes/No default
        await asyncio.sleep(0.2)
        await feed("j"); await feed("\r"); await asyncio.sleep(0.2)   # accept-rewrites Submit
        await feed("\r")                      # clarify editor -> blank -> done
        await asyncio.sleep(0.2)
        await feed("\r")                      # "Add this?" Yes
        await asyncio.sleep(0.2)
        assert bridge.mode == "command"
        await feed("/exit\r")

    _drive_shell(script, disp)
    assert written.get("experience", [{}])[0].get("company") == "Acme"


def test_fullscreen_edit_drills_to_a_bullet_and_reforms(monkeypatch, tmp_path):
    import entries, merge, json as _j
    (tmp_path / "content.json").write_text(_j.dumps({
        "projects": [{"title": "Transit Board", "bullets": ["made a board", "shipped it"],
                      "notes": ""}]}))
    monkeypatch.setattr(entries, "reform_bullets", lambda b, e, **k: ["Built a live transit board"])
    monkeypatch.setattr(merge, "backup", lambda *a, **k: [])

    def disp(line):
        events.append(("cmd", line))
        if line == "/ed":
            entries.cmd_edit(tmp_path, backend="c", model="m")
        return line != "/exit"

    async def script(feed, bridge):
        await feed("/ed\r"); await asyncio.sleep(0.4)
        assert bridge.mode == "question"     # section picker is up
        # section picker: "projects (1)" is the only real row -> Enter
        await feed("\r"); await asyncio.sleep(0.3)
        # entry picker: "Transit Board" -> Enter
        await feed("\r"); await asyncio.sleep(0.3)
        # scope picker: Whole / Reform every / • made a board / • shipped it / ...
        await feed("j"); await feed("j"); await feed("\r")   # to "• made a board"
        await asyncio.sleep(0.3)
        await feed("\r")                     # editor: blank -> model sharpens
        await asyncio.sleep(0.3)
        await feed("\r")                     # action picker: "Save this rewrite" (first row)
        await asyncio.sleep(0.3)
        # back at scope picker -> go "← back" (last row; picker clamps)
        for _ in range(6):
            await feed("j")
        await feed("\r"); await asyncio.sleep(0.2)
        # entry picker again -> "← back"
        await feed("j"); await feed("\r"); await asyncio.sleep(0.2)
        # section picker -> "← cancel"
        for _ in range(3):
            await feed("j")
        await feed("\r"); await asyncio.sleep(0.2)
        assert bridge.mode == "command"
        await feed("/exit\r")

    _drive_shell(script, disp)
    saved = _j.loads((tmp_path / "content.json").read_text())
    assert saved["projects"][0]["bullets"][0] == "Built a live transit board"


def test_fullscreen_edit_ctrl_c_on_the_action_picker_does_not_save(monkeypatch, tmp_path):
    # Ctrl-C on _edit_one_bullet's action picker (first row is "Save") must NOT
    # save the model's rewrite -- it returns to the scope picker, no write.
    import entries, merge, json as _j
    (tmp_path / "content.json").write_text(_j.dumps({
        "projects": [{"title": "Transit Board", "bullets": ["made a board", "shipped it"],
                      "notes": ""}]}))
    monkeypatch.setattr(entries, "reform_bullets", lambda b, e, **k: ["REWRITTEN BY MODEL"])
    backups = []
    monkeypatch.setattr(merge, "backup", lambda *a, **k: backups.append(1) or [])

    def disp(line):
        events.append(("cmd", line))
        if line == "/ed":
            entries.cmd_edit(tmp_path, backend="c", model="m")
        return line != "/exit"

    async def script(feed, bridge):
        await feed("/ed\r"); await asyncio.sleep(0.4)
        await feed("\r"); await asyncio.sleep(0.3)          # section: projects (1)
        await feed("\r"); await asyncio.sleep(0.3)          # entry: Transit Board
        await feed("j"); await feed("j"); await feed("\r")  # scope: • made a board
        await asyncio.sleep(0.3)
        await feed("\r"); await asyncio.sleep(0.3)          # editor: blank -> model sharpens
        assert bridge.choice_widget is not None             # action picker is up
        await feed("\x03"); await asyncio.sleep(0.3)        # Ctrl-C on the action picker
        assert bridge.mode == "question"                    # back at the scope picker
        assert bridge.choice_widget is not None
        for _ in range(8):
            await feed("j")
        await feed("\r"); await asyncio.sleep(0.2)          # scope -> "← back"
        await feed("j"); await feed("\r"); await asyncio.sleep(0.2)   # entry -> "← back"
        for _ in range(3):
            await feed("j")
        await feed("\r"); await asyncio.sleep(0.2)          # section -> "← cancel"
        assert bridge.mode == "command"
        await feed("/exit\r")

    _drive_shell(script, disp)
    saved = _j.loads((tmp_path / "content.json").read_text())
    assert saved["projects"][0]["bullets"] == ["made a board", "shipped it"]
    assert backups == []


def test_fullscreen_edit_ctrl_c_on_the_keep_rewrites_multi_picker_writes_nothing(monkeypatch, tmp_path):
    # /edit -> "Reform every bullet" -> "Keep which rewrites?" is a MULTI picker
    # whose default_values is every model rewrite. Ctrl-C there must NOT write
    # those rewrites -- it returns to the scope picker, no backup, no save.
    import entries, merge, json as _j
    (tmp_path / "content.json").write_text(_j.dumps({
        "projects": [{"title": "Transit Board", "bullets": ["made a board", "shipped it"],
                      "notes": ""}]}))
    monkeypatch.setattr(entries, "reform_bullets",
                        lambda b, e, **k: ["REWRITE ONE", "REWRITE TWO"])
    backups = []
    monkeypatch.setattr(merge, "backup", lambda *a, **k: backups.append(1) or [])
    saves = []
    _real_save = entries._save
    monkeypatch.setattr(entries, "_save",
                        lambda p, c: saves.append(1) or _real_save(p, c))

    def disp(line):
        events.append(("cmd", line))
        if line == "/ed":
            events.append(("ret", entries.cmd_edit(tmp_path, backend="c", model="m")))
        return line != "/exit"

    async def script(feed, bridge):
        await feed("/ed\r"); await asyncio.sleep(0.4)
        await feed("\r"); await asyncio.sleep(0.3)          # section: projects (1)
        await feed("\r"); await asyncio.sleep(0.3)          # entry: Transit Board
        await feed("j"); await feed("\r"); await asyncio.sleep(0.3)   # scope: Reform every bullet
        assert bridge.choice_widget is not None             # "Keep which rewrites?" is up
        await feed("\x03"); await asyncio.sleep(0.3)        # Ctrl-C on the multi picker
        assert bridge.mode == "question"                    # back at the scope picker
        assert bridge.choice_widget is not None
        for _ in range(8):
            await feed("j")
        await feed("\r"); await asyncio.sleep(0.2)          # scope -> "back"
        await feed("j"); await feed("\r"); await asyncio.sleep(0.2)   # entry -> "back"
        for _ in range(3):
            await feed("j")
        await feed("\r"); await asyncio.sleep(0.2)          # section -> "cancel"
        assert bridge.mode == "command"
        await feed("/exit\r")

    _drive_shell(script, disp)
    saved = _j.loads((tmp_path / "content.json").read_text())
    assert saved["projects"][0]["bullets"] == ["made a board", "shipped it"]
    assert backups == []
    assert saves == []
    assert ("ret", False) in events


def test_bridge_output_scroll_drops_follow_and_new_writes_do_not_yank():
    b = console._Bridge()
    for i in range(120):
        b.write(f"line {i}\n")
    assert b.output_follow is True
    b.scroll_output(-40)
    assert b.output_follow is False
    row = b.output.document.cursor_position_row
    assert row < 119
    b.write("a new tail line\n")                       # must not jump the viewport
    assert b.output.document.cursor_position_row == row
    b.output_to_end()
    assert b.output_follow is True
    assert b.output.document.cursor_position_row >= b.output.document.line_count - 1


def test_bridge_scroll_back_to_bottom_restores_follow():
    b = console._Bridge()
    for i in range(60):
        b.write(f"L{i}\n")
    b.scroll_output(-30)
    assert b.output_follow is False
    b.scroll_output(10 ** 6)                            # way past the end
    assert b.output_follow is True


def test_fullscreen_view_text_pager_opens_scrolls_and_closes(monkeypatch):
    body = "\n".join(f"PAGER {i}" for i in range(400))

    def disp(line):
        events.append(("cmd", line))
        if line == "/v":
            console.view_text("the job description", body)
        return line != "/exit"

    async def script(feed, bridge):
        await feed("/v\r")
        await asyncio.sleep(0.3)
        assert bridge.view_widget is not None and bridge.view_widget in bridge.floats
        await feed("\x1b[B"); await feed("\x1b[B")      # scroll down
        await asyncio.sleep(0.15)
        await feed("q")                                 # close
        await asyncio.sleep(0.25)
        assert bridge.view_widget is None and bridge.view_widget not in bridge.floats
        assert bridge.mode == "command"
        await feed("/exit\r")

    _drive_shell(script, disp)
    assert events[-1] == ("cmd", "/exit")


def _find_buffer_window(container, buf):
    for child in getattr(container, "get_children", lambda: [])():
        found = _find_buffer_window(child, buf)
        if found is not None:
            return found
    ctrl = getattr(container, "content", None)
    if getattr(ctrl, "buffer", None) is buf:
        return container
    return None


def test_multiline_answer_field_grows_to_fit_wrapped_text_never_clips_to_one_row():
    events.clear()

    def disp(line):
        events.append(("cmd", line))
        if line == "/ed":
            import ui
            events.append(("ans", ui.editor("Q1: partnered with PMs?")))
        return line != "/exit"

    async def script(feed, bridge):
        await feed("/ed\r")
        await asyncio.sleep(0.3)
        assert bridge.mode == "question" and bridge.question_multiline
        qw = _find_buffer_window(_get_app_layout(bridge), bridge.question_buf)
        assert qw is not None
        empty_h = qw.render_info.window_height
        # one long line, no newline -- must wrap and grow the field, not scroll it
        await feed("As a product designer I partnered directly with product managers "
                   "and engineers across three teams on a shared roadmap for eight "
                   "months, running weekly syncs and making the tradeoffs visible in "
                   "a decision log everyone could comment on and revisit later.")
        await asyncio.sleep(0.3)
        grown_h = qw.render_info.window_height
        assert grown_h >= 3, f"multi-line field stayed at {grown_h} rows"
        assert grown_h > empty_h or empty_h >= 3    # grew, or already at the 3-row min
        assert grown_h <= 12                        # capped, then scrolls internally
        await feed("\r")
        await asyncio.sleep(0.2)
        assert bridge.mode == "command"
        await feed("/exit\r")

    _drive_shell(script, disp)
    assert any(k == "ans" and "partnered directly" in v for k, v in events)


def _get_app_layout(bridge):
    return bridge.app.layout.container


def test_fullscreen_multiselect_space_toggles_enter_submits_from_any_row(monkeypatch):
    events.clear()

    def disp(line):
        events.append(("cmd", line))
        if line == "/m":
            events.append(("multi", console.ask_choice(
                "Pick", [("a", "A"), ("b", "B"), ("c", "C"), ("d", "D")], multi=True)))
        return line != "/exit"

    async def script(feed, bridge):
        await feed("/m\r")
        await asyncio.sleep(0.3)
        assert bridge.mode == "question" and bridge.choice_widget is not None
        await feed(" ")            # toggle A
        await feed("j"); await feed("j")
        await feed(" ")            # toggle C
        await asyncio.sleep(0.1)
        assert bridge.mode == "question"          # not submitted yet
        await feed("\r")           # ENTER on a normal row submits the checked set
        await asyncio.sleep(0.2)
        assert bridge.mode == "command"
        await feed("/exit\r")

    _drive_shell(script, disp)
    assert ("multi", ["a", "c"]) in events


def test_fullscreen_multiselect_a_toggles_all(monkeypatch):
    events.clear()

    def disp(line):
        events.append(("cmd", line))
        if line == "/m":
            events.append(("multi", console.ask_choice(
                "Pick", [("a", "A"), ("b", "B"), ("c", "C")], multi=True)))
        return line != "/exit"

    async def script(feed, bridge):
        await feed("/m\r")
        await asyncio.sleep(0.3)
        await feed("a")            # select all
        await asyncio.sleep(0.1)
        await feed("\r")
        await asyncio.sleep(0.2)
        assert bridge.mode == "command"
        await feed("/exit\r")

    _drive_shell(script, disp)
    assert ("multi", ["a", "b", "c"]) in events


def test_bridge_fold_block_collapsed_then_toggle_expands_in_place():
    b = console._Bridge()
    b.write("before\n")
    b.fold("⎿ acme.com/job · 900 words", "LINE1\nLINE2\nLINE3")
    b.write("after\n")
    txt = b.output.text
    assert "before" in txt and "after" in txt
    assert "to expand" in txt and "LINE2" not in txt          # collapsed
    assert b.toggle_last_fold() is True
    txt = b.output.text
    assert "LINE1" in txt and "LINE2" in txt and "LINE3" in txt
    assert "to collapse" in txt
    b.toggle_last_fold()
    assert "LINE2" not in b.output.text                        # collapsed again
    assert "after" in b.output.text                            # trailing output intact


def test_bridge_toggle_last_fold_is_a_noop_without_a_fold():
    b = console._Bridge()
    b.write("just text\n")
    assert b.toggle_last_fold() is False


def test_fullscreen_ctrl_o_expands_and_collapses_the_fetched_jd_in_place():
    events.clear()

    def disp(line):
        events.append(("cmd", line))
        if line == "/j":
            console.fold("⎿ acme.com/role · 500 words", "JOBDESC_MARKER line\n" * 40)
        return line != "/exit"

    async def script(feed, bridge):
        await feed("/j\r")
        await asyncio.sleep(0.25)
        assert "to expand" in bridge.output.text and "JOBDESC_MARKER" not in bridge.output.text
        await feed("\x0f")                        # ctrl+o -> expand
        await asyncio.sleep(0.2)
        assert "JOBDESC_MARKER" in bridge.output.text and "to collapse" in bridge.output.text
        await feed("\x0f")                        # ctrl+o -> collapse
        await asyncio.sleep(0.2)
        assert "JOBDESC_MARKER" not in bridge.output.text
        await feed("/exit\r")

    _drive_shell(script, disp)
    assert events[-1] == ("cmd", "/exit")


def test_output_click_handler_toggles_the_fold_on_its_summary_line():
    # In a real terminal a click's MOUSE_DOWN moves the buffer cursor to the
    # clicked line; the handler then toggles if that line carries the fold hint.
    # Here we place the cursor on the summary line to stand in for that.
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input.defaults import create_pipe_input
    from prompt_toolkit.output import DummyOutput
    from prompt_toolkit.mouse_events import MouseEvent, MouseEventType, MouseButton
    from prompt_toolkit.data_structures import Point
    from prompt_toolkit.document import Document

    async def main():
        with create_pipe_input() as inp, create_app_session(input=inp, output=DummyOutput()):
            b = console._Bridge(); console._bridge = b
            b.fold("⎿ acme · 500 words", "CLICKBODY\n" * 30)
            app = console.build_app(b, lambda l: l != "/exit", status="s")
            t = asyncio.ensure_future(app.run_async()); await asyncio.sleep(0.2)

            def find_control(c):
                w = getattr(c, "content", None)
                if getattr(w, "buffer", None) is b.output:
                    return w
                for ch in getattr(c, "get_children", lambda: [])():
                    r = find_control(ch)
                    if r is not None:
                        return r
            oc = find_control(app.layout.container)
            assert oc.__class__.__name__ == "_OutputControl"

            b.output.set_document(Document(b.output.text, 0), bypass_readonly=True)  # cursor -> line 0
            try:
                ev = MouseEvent(Point(x=1, y=0), MouseEventType.MOUSE_UP, MouseButton.LEFT, frozenset())
            except TypeError:
                ev = MouseEvent(Point(x=1, y=0), MouseEventType.MOUSE_UP)
            assert "CLICKBODY" not in b.output.text
            oc.mouse_handler(ev)
            await asyncio.sleep(0.1)
            assert "CLICKBODY" in b.output.text          # summary-line click expanded it

            try:
                app.exit()
            except Exception:
                pass
            await asyncio.wait_for(t, 3)
        console._bridge = None

    asyncio.run(main())


def test_f2_status_note_appears_when_mouse_capture_is_off():
    b = console._Bridge()
    assert "mouse off" not in b.status_text("openclaw/m")
    b.mouse_on = False
    s = b.status_text("openclaw/m")
    assert "mouse off" in s and "F2" in s and "openclaw/m" in s


def test_expanding_a_fold_anchors_the_view_on_its_summary_line_not_the_tail():
    b = console._Bridge()
    b.write("line one\nline two\n")
    b.fold("⎿ acme · 500 words", "BODY\n" * 40)
    b.write("after the fold\n")
    assert b.output_follow and b.output.document.cursor_position == len(b.output.text)
    b.toggle_last_fold()
    doc = b.output.document
    assert "BODY" in doc.text
    assert doc.cursor_position_row == 2                     # the summary line, i.e. the top
    assert doc.current_line.startswith("⎿ acme")
    assert b.output_follow is False                         # reading; new output must not yank
    b.toggle_last_fold()                                    # collapse: stays put, no jump
    assert "BODY" not in b.output.text and b.output.document.cursor_position_row == 2


def test_wheel_events_scroll_the_output_pane_and_drop_tail_follow():
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input.defaults import create_pipe_input
    from prompt_toolkit.output import DummyOutput
    from prompt_toolkit.mouse_events import MouseEvent, MouseEventType, MouseButton
    from prompt_toolkit.data_structures import Point

    async def main():
        with create_pipe_input() as inp, create_app_session(input=inp, output=DummyOutput()):
            b = console._Bridge(); console._bridge = b
            b.write("".join(f"row {i}\n" for i in range(200)))
            app = console.build_app(b, lambda l: l != "/exit", status="s")
            t = asyncio.ensure_future(app.run_async()); await asyncio.sleep(0.2)
            assert b.output_window is not None and b.output_follow
            oc = b.output_window.content
            moved = []
            b.scroll_output = lambda n: moved.append(n)
            def ev(kind):
                try:
                    return MouseEvent(Point(x=0, y=0), kind, MouseButton.NONE, frozenset())
                except TypeError:
                    return MouseEvent(Point(x=0, y=0), kind)
            assert oc.mouse_handler(ev(MouseEventType.SCROLL_UP)) is None
            assert oc.mouse_handler(ev(MouseEventType.SCROLL_DOWN)) is None
            assert moved == [-3, 3]
            try:
                app.exit()
            except Exception:
                pass
            await asyncio.wait_for(t, 3)
        console._bridge = None

    asyncio.run(main())


def test_scroll_output_moves_the_viewport_top_and_restores_follow_at_the_bottom():
    b = console._Bridge()
    b.write("".join(f"row {i}\n" for i in range(50)))
    b.scroll_output(-10)                                    # no window yet: cursor row is the top
    assert b.output_follow is False
    assert b.output.document.cursor_position_row == 40
    b.scroll_output(-10 ** 9)
    assert b.output.document.cursor_position_row == 0
    b.scroll_output(10 ** 9)
    assert b.output_follow is True and b.output.document.cursor_position == len(b.output.text)


def test_submitting_a_command_snaps_the_pane_back_to_the_tail():
    events = []

    def disp(line):
        events.append(line)
        print("output for " + line)
        return line != "/exit"

    async def script(feed, bridge):
        bridge.write("".join(f"row {i}\n" for i in range(100)))
        await asyncio.sleep(0.1)
        bridge.scroll_output(-30)
        assert bridge.output_follow is False
        await feed("/status\r")
        await asyncio.sleep(0.3)
        assert bridge.output_follow is True
        assert bridge.output.document.cursor_position == len(bridge.output.text)
        await feed("/exit\r")

    _drive_shell(script, disp)
    assert events[-1] == "/exit"
