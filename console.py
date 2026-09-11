"""A bottom-anchored input region and full-screen option pickers.

prompt_toolkit is already installed as questionary's own dependency, so this adds
capability without adding a package.

The important property is what this module does NOT do: it never redraws the
transcript. prompt_toolkit renders the input region below the cursor and lifts it
out of the way whenever something prints, so completed output lands in the
terminal's own scrollback where selection, search and copy still work. Only the
input region, and transiently a picker, belong to us.
"""
from __future__ import annotations

import sys
from pathlib import Path

try:
    import prompt_toolkit as _pt
    from prompt_toolkit.completion import Completer, Completion
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.patch_stdout import patch_stdout
    from prompt_toolkit.shortcuts import radiolist_dialog, checkboxlist_dialog
except Exception:                      # pragma: no cover - import guard
    _pt = None
    Completer = object              # so _CommandCompleter can still be defined

COMMANDS = ["/jd", "/batch", "/skills", "/new", "/edit", "/add", "/setup", "/recompile",
            "/rc", "/status", "/i", "/info", "/help", "/exit"]

# Names that are just a shorter way to type another command. They resolve in
# shell.dispatch but never get their own row in the completion menu.
_ALIASES = {"/rc", "/i", "/info", "/h", "/q"}


def _default_command_meta():
    """(name, shortcut, description) rows when the caller passes nothing --
    tests and the non-full-screen prompt(). Descriptions live in shell._HELP,
    which we can't import (cycle), so a bare fallback: canonical names only."""
    return [(c, "", "") for c in COMMANDS if c not in _ALIASES]


class _CommandCompleter(Completer):
    """One row per canonical command: its `/name`, its `·shortcut`, and its
    description as greyed meta -- like Claude Code's slash menu. Typing a
    shortcut (`/i`, `/rc`) matches its command; the shortcut is never its own
    row. The typed prefix is highlighted on each row."""

    def __init__(self, commands):
        # commands: [(name, shortcut_letter, description), ...]
        self._rows = [(n, (s or ""), (d or "")) for n, s, d in commands]

    def get_completions(self, document, complete_event):
        word = document.text_before_cursor
        if not word.startswith("/") or " " in word:
            return
        wl = word.lower()
        for name, short, desc in self._rows:
            alias = f"/{short}" if short else ""
            by_name = name.lower().startswith(wl)
            if not (by_name or (alias and alias.lower().startswith(wl))):
                continue
            hit = len(word) if by_name else 0        # chars of `name` the user typed
            disp = [("class:completion-menu.match", name[:hit]), ("", name[hit:])]
            if short:
                disp.append(("class:completion-menu.shortcut", f"  ·{short}"))
            yield Completion(name, start_position=-len(word),
                             display=disp, display_meta=desc)

# Returned by a single-select ask_choice when the user pressed Ctrl-C. A distinct
# sentinel (not row 0, not "") so a caller can tell "cancelled" from "picked the
# first row". pick() maps it back to options[0] / [] for its non-entries callers;
# entries.py and shell.py check for it explicitly.
CHOICE_CANCELLED = object()


def available() -> bool:
    return _pt is not None and sys.stdin.isatty()


def _fallback_line(message: str) -> str:
    try:
        return input(message)
    except EOFError:
        print()
        return "/exit"


def prompt(message: str = "> ", *, status: str = "", history_file=None, commands=None) -> str:
    """One line from the persistent input region. "/exit" on end-of-input."""
    if not available():
        return _fallback_line(message)

    bindings = KeyBindings()

    @bindings.add("escape", "enter")
    def _(event):                      # Alt+Enter inserts a newline; Enter submits
        event.current_buffer.insert_text("\n")

    session = _pt.PromptSession(
        history=FileHistory(str(history_file)) if history_file else None,
        completer=_CommandCompleter(commands or _default_command_meta()),
        complete_while_typing=False,
        key_bindings=bindings,
        bottom_toolbar=(lambda: status) if status else None,
        multiline=False,
    )
    try:
        with patch_stdout():
            return session.prompt(message)
    except EOFError:
        return "/exit"


def _fallback_pick(msg, options, *, multi, detail):
    print(f"{msg}" + ("  (comma-separated numbers, Enter for none)" if multi else ""))
    for i, o in enumerate(options, 1):
        print(f"  {i}. {o}")
        if detail is not None:
            text = detail(o)
            if text:
                print(f"     {text}")
    raw = _fallback_line("> ").strip()
    if multi:
        picks = []
        for tok in raw.split(","):
            tok = tok.strip()
            if tok.isdigit() and 1 <= int(tok) <= len(options):
                picks.append(options[int(tok) - 1])
        return picks
    if raw.isdigit() and 1 <= int(raw) <= len(options):
        return options[int(raw) - 1]
    return options[0]


def pick(msg: str, options: list, *, multi: bool = False, detail=None, precheck=None):
    """Choose from a fixed set of options, full-screen while open.

    Full-screen buys room for the consequence of each choice next to the list --
    "Replace it entirely" needs "loses the notes you have collected" visible at the
    moment of choosing, not in a hint printed three lines earlier.
    """
    options = list(options)
    if not options:
        return [] if multi else ""

    def label(o):
        text = detail(o) if detail is not None else ""
        return f"{o}\n    {text}" if text else str(o)

    # Inside the shell's own full-screen Application, radiolist_dialog/
    # checkboxlist_dialog can't be used: each opens a *second* Application, and
    # two Applications cannot both drive the same terminal from different
    # threads (the picker runs on the worker thread; the shell's Application
    # owns the terminal on the main thread). ask_choice() renders the same
    # RadioList/CheckboxList widget inline in the shell's input slot instead.
    # Checked first: in_fullscreen() implies a real tty, but a test can fake
    # one without the other.
    if in_fullscreen():
        values = [(o, label(o)) for o in options]
        default_values = [o for o in options if precheck and precheck(o)] if multi else None
        chosen = ask_choice(msg, values, multi=multi, default_values=default_values)
        # pick()'s callers (ui.select, checkbox, evidence_checkbox, the notes
        # target picker) predate the cancel sentinel and expect the old
        # contract: options[0] on a single-select bail, [] on a multi one.
        if chosen is CHOICE_CANCELLED:
            return [] if multi else options[0]
        return chosen

    if not available():
        return _fallback_pick(msg, options, multi=multi, detail=detail)

    values = [(o, label(o)) for o in options]

    if multi:
        chosen = checkboxlist_dialog(
            title="Resume-Tex", text=msg, values=values,
            default_values=[o for o in options if precheck and precheck(o)] or None).run()
        return list(chosen or [])
    chosen = radiolist_dialog(title="Resume-Tex", text=msg, values=values).run()
    return chosen if chosen is not None else options[0]


# ── full-screen shell ────────────────────────────────────────────────────────
"""
shell.py's persistent session renders here: a fixed layout matching the
terminal -- a scrolling output pane, one input slot below it, and a status bar
-- instead of the scrollback-preserving prompt() above. The input slot is the
same place throughout: a command line, or a highlighted answer field while a
running command waits on a text answer, or an inline option picker -- whichever
the moment calls for, so nothing on screen jumps. Leaving the alt-screen (on
/exit) erases everything the terminal showed, so every line written to the
output pane is duplicated into a plain-text transcript log: the trade this
design makes for looking like a real app instead of a scrolling shell.

Every command still runs through shell.dispatch(), which blocks -- on model
calls, on ui.py's confirm/text/editor prompts, sometimes both -- so it runs on
a worker thread while the Application's own event loop (main thread) keeps
rendering. ui.py's prompts detect in_fullscreen() and, instead of opening a
new questionary/prompt_toolkit session (which would fight this one for the
terminal), hand the question to the running _Bridge: a single-slot
request/response handoff guarded by a threading.Event. Only one command runs
at a time (input is disabled outside "command" mode), so the single slot is
never contended.
"""
import contextlib as _contextlib
import io as _io
import threading as _threading
import time as _time
from datetime import datetime as _datetime

try:
    from prompt_toolkit.application import Application as _Application, get_app as _get_app
    from prompt_toolkit.auto_suggest import (
        AutoSuggest as _AutoSuggest, AutoSuggestFromHistory as _AutoSuggestFromHistory,
        Suggestion as _Suggestion)
    from prompt_toolkit.buffer import Buffer as _Buffer
    from prompt_toolkit.document import Document as _Document
    from prompt_toolkit.filters import Condition as _Condition, has_focus as _has_focus
    from prompt_toolkit.history import FileHistory as _FileHistory
    from prompt_toolkit.key_binding import KeyBindings as _KeyBindings, merge_key_bindings as _merge_key_bindings
    from prompt_toolkit.key_binding.defaults import load_key_bindings as _load_key_bindings
    from prompt_toolkit.layout import Layout as _Layout
    from prompt_toolkit.layout.containers import (
        HSplit as _HSplit, VSplit as _VSplit, Window as _Window,
        ConditionalContainer as _ConditionalContainer, DynamicContainer as _DynamicContainer,
        Float as _Float, FloatContainer as _FloatContainer)
    from prompt_toolkit.layout.controls import BufferControl as _BufferControl, FormattedTextControl as _FormattedTextControl
    from prompt_toolkit.mouse_events import MouseEventType as _MouseEventType
    from prompt_toolkit.layout.dimension import Dimension as _D
    from prompt_toolkit.layout.margins import ScrollbarMargin as _ScrollbarMargin
    from prompt_toolkit.layout.menus import CompletionsMenu as _CompletionsMenu
    from prompt_toolkit.styles import Style as _Style
    from prompt_toolkit.widgets import (
        RadioList as _RadioList, CheckboxList as _CheckboxList,
        TextArea as _TextArea, Frame as _Frame)
except Exception:                      # pragma: no cover - import guard
    _Application = None

_SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
_SUBMIT = object()      # the synthetic last row of a multi-select: "done toggling"


_FOLD_HINT_OPEN = "▸ click / f4 / ctrl-o to expand"
_FOLD_HINT_SHUT = "▾ click / f4 / ctrl-o to collapse"


class _Fold:
    """A collapsible block in the output pane: a one-line summary that expands
    in place to `full`. Toggle by clicking the summary line, or f4 / ctrl-o."""

    __slots__ = ("summary", "full", "expanded")

    def __init__(self, summary: str, full: str, expanded: bool = False):
        self.summary = summary.rstrip("\n")
        self.full = full
        self.expanded = expanded

    def render(self) -> str:
        tag = _FOLD_HINT_SHUT if self.expanded else _FOLD_HINT_OPEN
        head = f"{self.summary}   {tag}\n"
        if not self.expanded:
            return head
        body = "\n".join("    " + ln for ln in self.full.splitlines())
        return head + body + "\n"

# The one input region: a highlighted answer field while a command waits on an
# answer, a plain command line otherwise; the picker rows and the completion
# menu highlight their focused row. Kept small on purpose -- this is a terminal
# app, not a themed one.
if _Application is not None:            # pragma: no branch
    _STYLE = _Style.from_dict({
        "prompt": "bold",
        "question": "bold ansicyan",
        "busy": "ansiyellow",
        "answer": "bg:ansibrightblack",      # the highlight on the answer field
        "sep": "ansibrightblack",
        "status": "reverse",
        "radio-checked": "bold ansigreen",
        "checkbox-checked": "bold ansigreen",
        "radio-selected": "reverse",
        "checkbox-selected": "reverse",
        # the slash-command menu, above the input
        "completion-menu": "bg:ansibrightblack",
        "completion-menu.completion": "bg:ansibrightblack",
        "completion-menu.completion.current": "bg:ansicyan #000000",
        "completion-menu.meta.completion": "bg:ansibrightblack #8a8a8a",
        "completion-menu.meta.completion.current": "bg:ansicyan #000000",
        "completion-menu.match": "bold underline",
        "completion-menu.shortcut": "#8a8a8a",
        "scrollbar.background": "bg:ansibrightblack",
        "scrollbar.button": "bg:#8a8a8a",
    })


class _ShellSuggest(_AutoSuggest):
    """Shadow text for the command line: complete a half-typed /command, else fall
    to the newest matching line from history. Right-arrow or Tab accepts it."""

    def __init__(self, commands):
        self._commands = list(commands)
        self._history = _AutoSuggestFromHistory()

    def get_suggestion(self, buffer, document):
        text = document.text
        if text.startswith("/") and len(text) > 1 and " " not in text:
            hits = [c for c in self._commands if c.startswith(text) and c != text]
            if len(hits) == 1:
                return _Suggestion(hits[0][len(text):])
            return None                # ambiguous -> let the completion menu show them
        if buffer is None:             # no history to fall back on (unit tests)
            return None
        return self._history.get_suggestion(buffer, document)


class _DefaultSuggest(_AutoSuggest):
    """Shadow text for the answer field: the question's default, shown until the
    applicant types over it. Accepting it (Tab / right-arrow) makes it editable."""

    def __init__(self, get_default):
        self._get_default = get_default

    def get_suggestion(self, buffer, document):
        default = self._get_default()
        if default and not document.text:
            return _Suggestion(default)
        return None


def _raise_in_thread(thread, exc_type) -> None:
    """Deliver `exc_type` to a running thread at its next bytecode. Used to unwind
    a command's worker on double-Esc -- the model subprocess is killed separately
    (llm.terminate_active), so the worker isn't stuck in a syscall when this lands."""
    import ctypes
    tid = getattr(thread, "ident", None)
    if not tid or not thread.is_alive():
        return
    n = ctypes.pythonapi.PyThreadState_SetAsyncExc(
        ctypes.c_long(tid), ctypes.py_object(exc_type))
    if n > 1:                              # hit more than one frame -> undo, it's unsafe
        ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_long(tid), None)


_bridge = None    # the running _Bridge, or None outside the full-screen shell


def in_fullscreen() -> bool:
    return _bridge is not None


def ask(label: str, default: str = "", *, multiline: bool = False) -> str:
    """Worker-thread call: show `label` in the question panel, block for an answer."""
    return _bridge.ask(label, default, multiline=multiline)


def ask_choice(label: str, values: list, *, multi: bool = False, default_values=None, default=None):
    """Worker-thread call: show an inline RadioList/CheckboxList, block for a choice."""
    return _bridge.ask_choice(label, values, multi=multi,
                              default_values=default_values, default=default)


def fs_busy(msg: str):
    """Worker-thread call: `with console.fs_busy("Scoring..."):` while full-screen."""
    return _bridge.busy(msg)


def can_view() -> bool:
    """True when view_text() has a running full-screen pager to open."""
    return _bridge is not None


def view_text(title: str, body: str) -> None:
    """Worker-thread call: open a scrollable pager over the shell showing `body`,
    block until the reader closes it (q / Esc / Enter)."""
    _bridge.view(title, body)


def fold(summary: str, full: str) -> bool:
    """Worker-thread call: print a collapsible block -- `summary` on one line,
    `full` hidden until alt+o / ctrl+o expands it in place. Returns False (and
    does nothing) outside the full-screen shell; the caller prints plainly then."""
    if _bridge is None:
        return False
    _bridge.fold(summary, full)
    return True


class _PaneWriter(_io.TextIOBase):
    """stdout, redirected into the output pane for the duration of one command."""

    def __init__(self, bridge):
        self._bridge = bridge

    def write(self, s: str) -> int:
        self._bridge.write(s)
        return len(s)

    def flush(self) -> None:
        pass


class _Bridge:
    def __init__(self, *, log_path=None, completer=None, history_file=None,
                 command_names=None):
        self.app = None
        self._log = open(log_path, "a", encoding="utf-8") if log_path else None

        history = _FileHistory(str(history_file)) if history_file else None
        self.command = _Buffer(multiline=False, completer=completer,
                               complete_while_typing=True, history=history,
                               auto_suggest=_ShellSuggest(command_names or COMMANDS))
        self.question_buf = _Buffer(
            multiline=_Condition(lambda: self.question_multiline),
            auto_suggest=_DefaultSuggest(lambda: self.question_default))
        self.output = _Buffer(read_only=True)
        self._blocks = []              # str chunks and _Fold objects, in order

        self.mode = "command"          # "command" | "question" | "busy"
        self.question_label = ""
        self.question_multiline = False
        self.question_default = ""      # shown as shadow text, not pre-filled
        self._answer = ""
        self._answer_event = _threading.Event()

        # The active picker widget, rendered inline above the input (not a pop-up
        # Float) so a choice reads like part of the same conversation.
        self.choice_widget = None
        self.choice_label = ""
        self._choice_result = None
        self.floats = []               # only the completion menu now
        self.busy_msg = None
        self.busy_frame = 0
        self._worker = None            # the thread running the current command, if any
        self.mouse_on = True           # F2 drops mouse capture so the terminal can select/copy

        # Output pane scroll: follow the tail until the reader scrolls up, then
        # stay put so new output doesn't yank them back down.
        self.output_follow = True
        self.output_window = None      # set by build_app; scroll_output anchors its viewport
        # The full-text pager (console.view_text), or None. A Float over everything.
        self.view_widget = None
        self._view_event = _threading.Event()

    # -- marshaling to the UI thread -----------------------------------------
    def _call(self, fn) -> None:
        if self.app is not None and self.app.loop is not None:
            self.app.loop.call_soon_threadsafe(fn)
        else:
            fn()

    def _invalidate(self) -> None:
        if self.app is not None:
            self.app.invalidate()

    # -- output ---------------------------------------------------------------
    def _rerender(self) -> None:
        """Flatten _blocks to the output buffer, preserving tail-follow / scroll."""
        text = "".join(b.render() if isinstance(b, _Fold) else b for b in self._blocks)
        doc = self.output.document
        at_tail = doc.cursor_position >= len(doc.text)
        pos = len(text) if (self.output_follow or at_tail) else min(doc.cursor_position, len(text))
        self.output.set_document(_Document(text, pos), bypass_readonly=True)
        self._invalidate()

    def write(self, text: str) -> None:
        if not text:
            return
        if self._log is not None:
            self._log.write(text)
            self._log.flush()

        def _append():
            if self._blocks and isinstance(self._blocks[-1], str):
                self._blocks[-1] += text
            else:
                self._blocks.append(text)
            self._rerender()
        self._call(_append)

    def fold(self, summary: str, full: str) -> None:
        """Worker-thread call: drop a collapsible block into the output pane.
        The full text expands in place on alt+o / ctrl+o."""
        if self._log is not None:
            self._log.write(f"{summary}\n{full}\n")
            self._log.flush()

        def _add():
            self._blocks.append(_Fold(summary, full))
            self._rerender()
        self._call(_add)

    def toggle_last_fold(self) -> bool:
        for idx in range(len(self._blocks) - 1, -1, -1):
            b = self._blocks[idx]
            if not isinstance(b, _Fold):
                continue
            b.expanded = not b.expanded
            # Anchor the viewport on the fold's summary line -- reading starts at
            # the top of the block, not at its tail (which is where tail-follow
            # would have landed), and collapsing it doesn't jump the view either.
            start = sum(len(x.render() if isinstance(x, _Fold) else x)
                        for x in self._blocks[:idx])
            self.output_follow = False
            self._rerender()
            self._anchor(start)
            return True
        return False

    def _anchor(self, offset: int) -> None:
        """Put the output line containing `offset` at the TOP of the viewport."""
        doc = self.output.document
        offset = max(0, min(offset, len(doc.text)))
        row = doc.text.count("\n", 0, offset)
        self.output.cursor_position = doc.translate_row_col_to_index(row, 0)
        if self.output_window is not None:
            self.output_window.vertical_scroll = row
        self._invalidate()

    def _viewport_rows(self) -> int:
        w = self.output_window
        ri = getattr(w, "render_info", None) if w is not None else None
        return ri.window_height if ri else 0

    def scroll_output(self, lines: int) -> None:
        """Move the output pane's viewport by `lines` (negative = up). The
        cursor line IS the top of the viewport, so a scroll moves the view
        the moment it is asked to (no dead ticks while a hidden cursor crosses
        the visible rows). Scrolling up drops tail-follow; reaching the bottom
        restores it."""
        doc = self.output.document
        top = (self.output_window.vertical_scroll if self.output_window is not None
               else doc.cursor_position_row)
        last_top = max(0, doc.line_count - max(1, self._viewport_rows()))
        row = max(0, min(top + lines, last_top))
        self.output_follow = row >= last_top
        if self.output_follow:
            self.output_to_end()
            return
        self._anchor(doc.translate_row_col_to_index(row, 0))

    def output_to_end(self) -> None:
        self.output_follow = True
        self.output.cursor_position = len(self.output.text)
        self._invalidate()

    # -- full-text pager (console.view_text) -----------------------------------
    def view(self, title: str, body: str) -> None:
        """Worker-thread call: open a scrollable pager over the layout showing
        `body`. Blocks until the reader closes it with q / Esc / Enter."""
        self._view_event.clear()
        area = _TextArea(text=body, read_only=True, scrollbar=True,
                         focusable=True, wrap_lines=True)
        kb = _KeyBindings()

        @kb.add("q", eager=True)
        @kb.add("escape", eager=True)
        @kb.add("enter", eager=True)
        def _close(event):
            self.view_widget = None
            if self.app is not None:
                self.app.layout.focus(self.command)
            self._invalidate()
            self._view_event.set()

        frame = _Frame(area, title=f"{title}   (↑↓ PgUp/PgDn scroll · q to close)",
                       key_bindings=kb)
        float_ = _Float(frame, top=1, bottom=1, left=2, right=2)

        def _show():
            self.view_widget = float_
            self.floats.append(float_)
            if self.app is not None:
                self.app.layout.focus(area)
            self._invalidate()
        self._call(_show)
        self._view_event.wait()

        def _drop():
            if float_ in self.floats:
                self.floats.remove(float_)
            self._invalidate()
        self._call(_drop)

    # -- text questions (confirm / text / editor) ----------------------------
    def ask(self, label: str, default: str = "", *, multiline: bool = False) -> str:
        """Worker-thread call. Blocks until the UI thread answers."""
        self._answer_event.clear()

        def _show():
            self.question_label = label
            self.question_multiline = multiline
            if multiline and default:
                # A multi-line default is a draft to edit (the gap-answer
                # pre-fill) -- pre-fill it for real; a shadow one line long can't
                # preview a paragraph anyway.
                self.question_default = ""
                self.question_buf.text = default
                self.question_buf.cursor_position = len(default)
                self.question_buf.suggestion = None
            else:
                # Single-line: the default is shadow text; Tab/right-arrow fills
                # it. Seed the suggestion so it's on screen immediately -- the
                # auto-suggest only recomputes on a text change, and an untouched
                # field never has one. It clears itself once the applicant types.
                self.question_default = default
                self.question_buf.text = ""
                self.question_buf.suggestion = _Suggestion(default) if default else None
            self.mode = "question"
            if self.app is not None:
                self.app.layout.focus(self.question_buf)
            self._invalidate()
        self._call(_show)
        self._answer_event.wait()
        return self._answer

    def _submit_question(self) -> None:
        """UI-thread call, from the question buffer's Enter handler.

        An empty answer is returned as "" -- ui.confirm/text/editor each fall back
        to their own default, which is exactly what the shadow text was showing.
        """
        self._answer = self.question_buf.text
        self._end_question()

    def _cancel_question(self) -> None:
        self._answer = ""
        self._end_question()

    def _end_question(self) -> None:
        self.question_buf.text = ""
        self.question_default = ""
        self.question_label = ""
        self.mode = "busy"
        if self.app is not None:
            self.app.layout.focus(self.command)
        self._invalidate()
        self._answer_event.set()

    # -- choice questions (confirm / select / checkbox / evidence_checkbox) --
    def ask_choice(self, label: str, values: list, *, multi: bool = False,
                   default_values=None, default=None):
        """Worker-thread call. values: [(value, display_text), ...].

        Renders inline just above the input, in the layout's normal flow. Arrow
        keys move; Ctrl-C cancels -- both single- and multi-select return
        CHOICE_CANCELLED so the caller can tell "cancelled" from any real pick
        (including "picked nothing" / "picked row 0").

        Single-select: Enter accepts the highlighted row; `default` starts the
        highlight there.
        Multi-select (the conventional bindings): ↑/↓ move, SPACE toggles the
        highlighted row, `a` toggles all, ENTER submits the checked set from any
        row. A "▸ done" row at the end submits too, for the mouse.
        """
        self._answer_event.clear()
        self._choice_result = list(default_values or []) if multi else (values[0][0] if values else "")
        if not values:
            return self._choice_result

        if multi:
            rows = list(values) + [(_SUBMIT, "  ▸ done")]
            widget = _CheckboxList(values=rows, default_values=list(default_values or []))
            _real = [v for v, _ in values]

            def _submit():
                self._choice_result = [v for v in widget.current_values if v is not _SUBMIT]
                self._end_choice()

            @widget.control.key_bindings.add(" ", eager=True)
            def _toggle(event):
                if widget.values[widget._selected_index][0] is not _SUBMIT:
                    widget._handle_enter()          # toggle just this row
                    event.app.invalidate()

            @widget.control.key_bindings.add("a", eager=True)
            def _toggle_all(event):
                on = set(widget.current_values)
                widget.current_values[:] = [] if on.issuperset(_real) else list(_real)
                event.app.invalidate()

            @widget.control.key_bindings.add("enter", eager=True)
            def _enter(event):
                _submit()
        else:
            widget = _RadioList(values=values)
            if default is not None:
                for idx, (val, _) in enumerate(values):
                    if val == default:
                        widget.current_value = val
                        widget._selected_index = idx
                        break

            # eager=True so Enter accepts instead of RadioList's own Enter (which
            # only moves the highlight). Read the row off _selected_index: the
            # arrow/j/k bindings move that, not current_value (which only
            # RadioList's own Enter updates, and we've shadowed it).
            @widget.control.key_bindings.add("enter", eager=True)
            def _accept(event):
                self._choice_result = widget.values[widget._selected_index][0]
                self._end_choice()

        @widget.control.key_bindings.add("c-c", eager=True)
        def _abort(event):
            # A distinct sentinel so the caller can tell "cancelled" from a real
            # pick -- "picked row 0" for single-select, "checked nothing" or
            # "checked default_values" for multi. Returning default_values here
            # would silently write the very rows the user hit Ctrl-C to reject.
            self._choice_result = CHOICE_CANCELLED
            self._end_choice()

        hint = "   (space toggles · a = all · enter submits)" if multi else ""

        def _show():
            self.question_label = label + hint
            self.choice_widget = widget
            self.mode = "question"
            if self.app is not None:
                self.app.layout.focus(widget)
            self._invalidate()
        self._call(_show)
        self._answer_event.wait()
        return self._choice_result

    def _end_choice(self) -> None:
        self.choice_widget = None
        self.question_label = ""
        self.mode = "busy"
        if self.app is not None:
            self.app.layout.focus(self.command)
        self._invalidate()
        self._answer_event.set()

    # -- abort (double-Esc) -------------------------------------------------------
    def abort(self) -> str:
        """Double-Esc. Returns what it did, for a one-line note to the pane:
        "" (nothing to abort), "question", or "command"."""
        if self.mode == "question":
            if self.choice_widget is not None:
                self._end_choice()
            else:
                self._cancel_question()
            return "question"
        w = self._worker
        if w is not None and w.is_alive():
            try:
                import llm
                llm.terminate_active()          # kill the model subprocess in flight
            except Exception:
                pass
            _raise_in_thread(w, KeyboardInterrupt)   # then unwind the rest of the run
            return "command"
        return ""

    # -- busy status ------------------------------------------------------------
    @_contextlib.contextmanager
    def busy(self, msg: str):
        self.busy_msg = msg
        self._invalidate()
        try:
            yield
        finally:
            self.busy_msg = None
            self._invalidate()

    def busy_line(self) -> str:
        """The transient info line shown just above the input while a model call
        runs -- e.g. "⠹ Scoring your CV against the JD...". "" when idle."""
        if not self.busy_msg:
            return ""
        frame = _SPINNER_FRAMES[self.busy_frame % len(_SPINNER_FRAMES)]
        return f"  {frame} {self.busy_msg}..."

    def status_text(self, extra: str) -> str:
        """The persistent bottom bar: the backend/model id, plus a note when
        mouse capture is off so drag-to-select works."""
        parts = [extra] if extra else []
        if not self.mouse_on:
            parts.append("mouse off — drag to select · F2 to restore")
        return "  " + "   ·   ".join(parts) if parts else ""


def build_app(bridge, dispatch, *, status: str):
    """Assemble the full-screen Application around an existing _Bridge.

    Split out from run_fullscreen so a test can drive it with
    Application.run_test() -- the layout, the mode-swapping input slot and the
    key bindings are exactly what ships, only the terminal is faked.
    """
    class _OutputControl(_BufferControl):
        """The output pane: the wheel scrolls it (three rows a tick, like a
        terminal), and a click on a fold's summary line toggles the fold. The
        pane is never the focused control (the input is), so the base class
        would ignore both -- handle them here from the event's own row."""

        def mouse_handler(self, mouse_event):
            et = mouse_event.event_type
            if et == _MouseEventType.SCROLL_UP:
                bridge.scroll_output(-3)
                return None
            if et == _MouseEventType.SCROLL_DOWN:
                bridge.scroll_output(3)
                return None
            if et == _MouseEventType.MOUSE_UP:
                # position.y is the buffer row the Window mapped the click to.
                lines = self.buffer.document.lines
                y = mouse_event.position.y
                line = lines[y] if 0 <= y < len(lines) else ""
                if _FOLD_HINT_OPEN in line or _FOLD_HINT_SHUT in line:
                    bridge.toggle_last_fold()
                    return None
            return NotImplemented

    output_window = _Window(_OutputControl(buffer=bridge.output), wrap_lines=True,
                            always_hide_cursor=True,
                            right_margins=[_ScrollbarMargin(display_arrows=True)])
    bridge.output_window = output_window

    # The zone directly above the separator carries whatever the moment is about:
    # the question being asked (wrapped, full text -- so the input below it is
    # never a bare "A1"), or the spinner line while a model call runs. Below the
    # separator is the input slot proper: command line, answer field, or picker,
    # always in the same place so nothing jumps.
    def _in_text_q() -> bool:
        return bridge.mode == "question" and bridge.choice_widget is None

    def _q_dim():
        # Grow to fit the WRAPPED content (dont_extend_height sizes the window to
        # its content; wrap_lines makes that the visual, not logical, line count),
        # so a long answer is never clipped to one scrolling row. Multi-line
        # fields keep a 3-row minimum for comfort and cap at 12, then scroll
        # internally with the cursor visible -- the Claude Code input feel.
        if bridge.question_multiline:
            return _D(min=3, max=12)
        return _D.exact(1)

    context_row = _ConditionalContainer(
        _HSplit([
            _ConditionalContainer(
                _Window(_FormattedTextControl(lambda: [("class:question", bridge.question_label)]),
                        wrap_lines=True, dont_extend_height=True),
                filter=_Condition(lambda: bridge.mode == "question" and bool(bridge.question_label))),
            _ConditionalContainer(
                _Window(_FormattedTextControl(lambda: [("class:busy", bridge.busy_line())]), height=1),
                filter=_Condition(lambda: bool(bridge.busy_msg))),
        ]),
        filter=_Condition(lambda: (bridge.mode == "question" and bool(bridge.question_label))
                          or bool(bridge.busy_msg)))

    command_row = _ConditionalContainer(
        _VSplit([
            _Window(_FormattedTextControl([("class:prompt", "> ")]), height=1,
                    dont_extend_width=True),
            _Window(_BufferControl(buffer=bridge.command, focusable=True), height=1),
        ]),
        filter=_Condition(lambda: bridge.mode != "question"))

    question_row = _ConditionalContainer(
        _Window(_BufferControl(buffer=bridge.question_buf, focusable=True),
                style="class:answer", height=_q_dim, wrap_lines=True,
                dont_extend_height=True),
        filter=_Condition(_in_text_q))

    choice_row = _ConditionalContainer(
        _DynamicContainer(lambda: bridge.choice_widget or _Window()),
        filter=_Condition(lambda: bridge.mode == "question" and bridge.choice_widget is not None))

    input_slot = _HSplit([command_row, question_row, choice_row])

    command_enabled = _Condition(lambda: bridge.mode == "command")

    def _submit_command(buf):
        line = buf.text
        buf.reset()
        if not line.strip():
            return False
        bridge.mode = "busy"
        bridge.output_to_end()        # new output lands at the tail; go there

        def worker():
            cont = True
            try:
                with _contextlib.redirect_stdout(_PaneWriter(bridge)):
                    cont = dispatch(line)
            except KeyboardInterrupt:            # double-Esc landed mid-command
                bridge.write("\n  Aborted.\n")
            finally:
                bridge._worker = None
                bridge.busy_msg = None
                bridge.mode = "command"
                bridge._invalidate()
            if not cont and bridge.app is not None:
                bridge.app.loop.call_soon_threadsafe(bridge.app.exit)
        t = _threading.Thread(target=worker, daemon=True)
        bridge._worker = t
        t.start()
        return False
    bridge.command.accept_handler = _submit_command

    def _submit_question_handler(buf):
        bridge._submit_question()
        return False
    bridge.question_buf.accept_handler = _submit_question_handler   # single-line Enter

    _q_focused = _has_focus(bridge.question_buf)

    status_window = _Window(_FormattedTextControl(lambda: bridge.status_text(status)),
                            height=1, style="class:status")

    root = _HSplit([
        output_window,
        context_row,
        # The slash-command menu sits here -- above the separator and the input,
        # never over the status bar. CompletionsMenu is a ConditionalContainer,
        # so it takes zero rows unless a completion is active.
        _CompletionsMenu(max_height=8, scroll_offset=1),
        _Window(height=1, char="─", style="class:sep"),
        input_slot,
        status_window,
    ])
    # Nothing floats now -- the choice picker renders inline in the input slot,
    # and the completion menu is a real row above the input.
    bridge.floats = []
    container = _FloatContainer(content=root, floats=bridge.floats)

    def _page_rows(_event) -> int:
        ri = output_window.render_info
        return (ri.window_height - 1) if ri else 0

    def _suggestion_ready() -> bool:
        app_ = _get_app()
        buf = app_.current_buffer
        return bool(buf.suggestion and buf.suggestion.text
                    and buf.document.is_cursor_at_the_end)

    kb = _KeyBindings()

    @kb.add("c-i", filter=_Condition(_suggestion_ready))       # Tab
    @kb.add("right", filter=_Condition(_suggestion_ready))
    def _(event):
        buf = event.current_buffer
        buf.insert_text(buf.suggestion.text)

    # A multi-line answer field (ui.editor: extra context, a pasted JD, a gap
    # draft) binds Enter to a newline by default, leaving no way to submit inside
    # the shell. Flip it: Enter submits, Alt+Enter adds a newline -- what
    # Enter-to-finish leads a user to expect, and consistent with the single-line
    # fields. A bracketed paste still arrives whole, newlines and all.
    @kb.add("enter", filter=_q_focused & _Condition(lambda: bridge.question_multiline))
    def _(event):
        bridge._submit_question()

    @kb.add("escape", "enter", filter=_q_focused)
    def _(event):
        event.current_buffer.insert_text("\n")

    # Scroll the output pane's history. Only while idle (not mid-question, no
    # pager open) so arrows/PageUp stay free for the picker and the pager.
    _can_scroll = _Condition(lambda: bridge.mode != "question" and bridge.view_widget is None)

    @kb.add("pageup", filter=_can_scroll)
    def _(event):
        bridge.scroll_output(-(_page_rows(event) or 10))

    @kb.add("pagedown", filter=_can_scroll)
    def _(event):
        bridge.scroll_output(_page_rows(event) or 10)

    @kb.add("c-home", filter=_can_scroll)
    def _(event):
        bridge.scroll_output(-10 ** 9)

    @kb.add("c-end", filter=_can_scroll)
    def _(event):
        bridge.output_to_end()

    # Expand / collapse the most recent collapsible block (the fetched JD) in
    # place. f4 is the reliable key -- prompt_toolkit's defaults already claim
    # ctrl-o three times (basic _ignore, emacs operate-and-get-next, vi), and
    # macOS terminals send "ø" for alt+o unless Option-as-Meta is on -- so ctrl-o
    # is added eager to win the conflict, and clicking the summary line also works
    # (see _OutputControl).
    @kb.add("f4", filter=_can_scroll)
    @kb.add("c-o", filter=_can_scroll, eager=True)
    def _(event):
        bridge.toggle_last_fold()

    @kb.add("c-c")
    def _(event):
        if bridge.mode == "question":
            if bridge.choice_widget is not None:
                # Cancel for both RadioList and CheckboxList: the sentinel lets
                # the caller distinguish Ctrl-C from a real (possibly empty) pick.
                bridge._choice_result = CHOICE_CANCELLED
                bridge._end_choice()
            else:
                bridge._cancel_question()
        elif bridge.mode == "command":
            event.app.exit()
        # "busy" with no question pending: use double-Esc to abort the command.

    # Double-Esc: back out of whatever is in progress. Cancels a pending question,
    # or aborts a running command -- kills its model subprocess and unwinds the
    # rest of the run -- and drops back to the command prompt. A no-op at an idle
    # prompt. (Single Esc stays free: it prefixes arrow keys and Alt+Enter.)
    @kb.add("escape", "escape")
    def _(event):
        did = bridge.abort()
        if did == "command":
            bridge.write("\n  Aborting…\n")

    @kb.add("c-d", filter=command_enabled)
    def _(event):
        if not bridge.command.text:
            event.app.exit()

    # F2 drops the app's mouse capture so the terminal's own click-drag select and
    # copy work on the whole pane; F2 again restores wheel-scroll and click.
    @kb.add("f2")
    def _(event):
        bridge.mouse_on = not bridge.mouse_on
        bridge._invalidate()

    app = _Application(
        layout=_Layout(container, focused_element=bridge.command),
        key_bindings=_merge_key_bindings([_load_key_bindings(), kb]),
        style=_STYLE,
        full_screen=True,
        mouse_support=_Condition(lambda: bridge.mouse_on),
    )
    bridge.app = app
    return app


def run_fullscreen(dispatch, *, status: str, history_file=None, log_path=None,
                   banner: str = "", commands=None) -> None:
    """The persistent shell's main loop.

    `dispatch(line)` is shell.dispatch bound to its Session; it returns False
    when the session should end. Blocks until then, or until Ctrl-C/Ctrl-D at
    an idle command prompt (both exit in parallel with typing /exit).
    `commands` is [(name, shortcut, description), ...] from shell._HELP -- it
    drives the completion menu and the shadow-text suggestions.
    """
    global _bridge
    if _Application is None:
        raise RuntimeError("prompt_toolkit is required for the full-screen shell")

    meta = commands or _default_command_meta()
    completer = _CommandCompleter(meta)
    bridge = _Bridge(log_path=log_path, completer=completer, history_file=history_file,
                     command_names=[n for n, _s, _d in meta])
    _bridge = bridge
    if banner:
        bridge.write(banner)

    app = build_app(bridge, dispatch, status=status)

    def _spin_tick():
        while not app.is_done:
            _time.sleep(0.12)
            if bridge.busy_msg:
                bridge.busy_frame += 1
                bridge._invalidate()
    _threading.Thread(target=_spin_tick, daemon=True).start()

    try:
        app.run()
    finally:
        _bridge = None
        if bridge._log is not None:
            bridge._log.close()
