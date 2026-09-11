"""Claude-Code-style prompts. questionary + rich on a TTY; plain I/O when piped.

Every function degrades so a non-interactive run (piped stdin, CI) still works and
never blocks. Tests monkeypatch `interactive` and, for the widget path, `_q`.
"""
from __future__ import annotations

import contextlib
import sys
from pathlib import Path

import console

try:                       # optional; absent -> fallback path only
    import questionary as _q
except Exception:          # pragma: no cover - import guard
    _q = None

try:
    from rich.console import Console as _Console
    from rich.panel import Panel as _Panel
    from rich.table import Table as _Table
    _console = _Console()
except Exception:          # pragma: no cover - import guard
    _console = None
    _Panel = None
    _Table = None


def interactive() -> bool:
    return sys.stdin.isatty()


def _use_widgets() -> bool:
    return interactive() and _q is not None


def _rich_ok() -> bool:
    """rich renders by writing straight to the real terminal, bypassing whatever
    captures print() output -- inside the full-screen shell that would corrupt the
    alt-screen buffer instead of landing in the output pane, so every rich-or-plain
    branch below must also fall to plain print() there."""
    return _console is not None and interactive() and not console.in_fullscreen()


# -- input -------------------------------------------------------------------
def _line(prompt: str, default_on_eof: str = "") -> str:
    """input() that treats end-of-input as "no answer" instead of a traceback.

    A piped or redirected run reaches EOF at the first unanswered prompt; crashing
    there would abandon a half-written config.
    """
    try:
        return input(prompt)
    except EOFError:
        print()
        return default_on_eof


def confirm(msg: str, *, default: bool = False) -> bool:
    if console.in_fullscreen():
        # An inline Yes/No pick with the default already highlighted -- one
        # keystroke (Enter) to take it, arrow-then-Enter to flip it. Nothing to
        # type, nothing to mistype.
        return bool(console.ask_choice(msg, [(True, "Yes"), (False, "No")], default=default))
    prompt = f"{msg} [{'Y/n' if default else 'y/N'}]"
    if _use_widgets():
        return bool(_q.confirm(msg, default=default).ask())
    ans = _line(f"{prompt} ").strip().lower()
    if not ans:
        return default
    return ans in ("y", "yes")


def text(msg: str, *, default: str = "", validate=None) -> str:
    if console.in_fullscreen():
        return console.ask(msg, default).strip() or default
    if _use_widgets():
        return _q.text(msg, default=default, validate=validate).ask() or default
    ans = _line(f"{msg}{f' [{default}]' if default else ''} ").strip()
    return ans or default


def clean_path(raw: str) -> str:
    """A path as a human actually types it.

    Dragging a file into a terminal, or copying one out of a shell history, brings
    along quotes and backslash-escaped spaces. Those are shell syntax, not part of the
    name — left in place they end up in the suffix, which is how a .pdf becomes an
    "unsupported .pdf'" file type.
    """
    raw = (raw or "").strip()
    for quote in ("'", '"'):
        if len(raw) >= 2 and raw[0] == quote and raw[-1] == quote:
            raw = raw[1:-1]
            break
    raw = raw.replace("\\ ", " ").strip()
    return raw


def path(msg: str, *, default: str = "", must_exist: bool = True):
    """Prompt for a filesystem path. Returns a Path, or None if left blank.

    Re-asks on a path that is not there rather than exiting the installer: a typo in
    one answer should not cost the user every answer before it.
    """
    while True:
        raw = clean_path(text(msg, default=default))
        if not raw:
            return None
        candidate = Path(raw).expanduser()
        if not must_exist or candidate.exists():
            return candidate
        print(f"  No such file: {candidate}")
        if not interactive():
            return None


def select(msg: str, choices: list) -> str:
    if interactive() and console.available():
        return console.pick(msg, list(choices))
    if _use_widgets():
        return _q.select(msg, choices=list(choices)).ask()
    print(msg)
    for i, c in enumerate(choices, 1):
        print(f"  {i}. {c}")
    while True:
        raw = _line("> ", default_on_eof="1").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(choices):
            return choices[int(raw) - 1]
        if not raw:                    # EOF or a bare Enter: take the first choice
            return choices[0]


def checkbox(msg: str, choices: list, *, default: tuple = ()) -> list:
    if interactive() and console.available():
        return console.pick(msg, list(choices), multi=True,
                            precheck=lambda c: c in default)
    if _use_widgets():
        Choice = getattr(_q, "Choice", None)
        if Choice is not None:
            opts = [Choice(c, checked=(c in default)) for c in choices]
        else:                              # pragma: no cover - questionary always ships Choice
            opts = list(choices)
        return list(_q.checkbox(msg, choices=opts).ask() or [])
    print(f"{msg}  (comma-separated numbers, Enter for none)")
    for i, c in enumerate(choices, 1):
        print(f"  {i}. {c}")
    raw = _line("> ").strip()
    picks = []
    for tok in raw.split(","):
        tok = tok.strip()
        if tok.isdigit() and 1 <= int(tok) <= len(choices):
            picks.append(choices[int(tok) - 1])
    return picks


def editor(msg: str, *, default: str = "") -> str:
    if console.in_fullscreen():
        return console.ask(msg, default, multiline=True).strip() or default
    if _use_widgets():
        # questionary's stock multiline binds Enter to a newline and Esc-then-Enter
        # to submit -- backwards for pasting a JD, where Enter-to-finish is what a
        # user expects and a newline is the rare case. Pasted text also arrives as
        # one bracketed-paste block, which prompt_toolkit inserts literally
        # (newlines included) regardless of these key bindings, so multi-line
        # pastes are unaffected -- this only changes what typing Enter by hand does.
        try:
            from prompt_toolkit import PromptSession
            from prompt_toolkit.key_binding import KeyBindings
        except Exception:                       # pragma: no cover - import guard
            return (_q.text(msg + " (Esc then Enter to finish)", default=default,
                            multiline=True).ask() or default).strip()

        bindings = KeyBindings()

        @bindings.add("enter")
        def _submit(event):
            event.current_buffer.validate_and_handle()

        @bindings.add("escape", "enter")   # Alt+Enter
        @bindings.add("c-j")               # Shift+Enter, on terminals that send LF for it
        def _newline(event):
            event.current_buffer.insert_text("\n")

        print(f"{msg}  (Enter to finish, Shift/Alt+Enter for a newline)")
        try:
            text = PromptSession(key_bindings=bindings, multiline=True).prompt(default=default)
        except (EOFError, KeyboardInterrupt):
            return default
        return (text or default).strip()
    print(f"{msg}  (end with Ctrl-D)")
    return sys.stdin.read().strip() or default


# -- progress ----------------------------------------------------------------
@contextlib.contextmanager
def spinner(msg: str):
    """Cover a slow call so the terminal never looks hung.

    Every model call in this project takes seconds to minutes. rich animates it on a
    TTY; anywhere else it is one printed line, which is what a log wants anyway.
    """
    if console.in_fullscreen():
        with console.fs_busy(msg):
            yield
        return
    if _rich_ok():
        with _console.status(f"[dim]{msg}...[/dim]", spinner="dots"):
            yield
        return
    print(f"{msg}...", flush=True)
    yield


def hint(msg: str) -> None:
    """A line of explanation under a prompt whose wording cannot carry it alone."""
    if _rich_ok():
        _console.print(f"[dim]{msg}[/dim]")
    else:
        print(msg)


# -- output ----------------------------------------------------------------
def score_panel(baseline: int, requirements: list) -> None:
    """One line: the score, plus how many requirements are not a direct fit.

    The full per-requirement fit/weight breakdown used to render as a table here,
    but a wall of rows the applicant can't act on directly (the gap questions,
    printed separately, are the actionable version of the same data) just delays
    getting to something they can respond to.
    """
    gaps = [r for r in requirements if r.get("status") not in ("direct", None)]
    suffix = f" ({len(gaps)} gap area{'s' if len(gaps) != 1 else ''})" if gaps else ""
    print(f"Baseline match : {baseline}/100{suffix}")


def _gap_attr(g, name, fallback=""):
    val = getattr(g, name, None)
    if val is None and isinstance(g, dict):
        val = g.get(name, fallback)
    return fallback if val is None else val


def gap_table(gap_questions: list) -> None:
    if _rich_ok():
        t = _Table(show_header=True, box=None)
        t.add_column("#")
        t.add_column("+pts", justify="right")
        t.add_column("question")
        for i, g in enumerate(gap_questions, 1):
            t.add_row(str(i), str(_gap_attr(g, "potential_points", 0)), str(_gap_attr(g, "question", "")))
        _console.print(t)
        return
    for i, g in enumerate(gap_questions, 1):
        pts = _gap_attr(g, "potential_points", 0)
        q = _gap_attr(g, "question", "")
        print(f"  Q{i} (+{pts}): {q}")


def report(*, baseline, tailored: int, folded: list, improvements: list,
           questions: list, warnings: list, coverage: dict = None) -> None:
    b = f"{baseline}" if baseline is not None else "n/a"
    delta = f" ({tailored - baseline:+d})" if baseline is not None else ""
    lines = [f"Match      : untailored 1-page {b}/100 -> JD-tailored {tailored}/100{delta}"]
    if coverage:
        cov, miss = coverage.get("covered") or [], coverage.get("missing") or []
        line = f"JD terms   : {len(cov)}/{len(cov) + len(miss)} carried"
        if miss:
            line += " · missing: " + ", ".join(miss[:6]) + (" …" if len(miss) > 6 else "")
        lines.append(line)
    for q, p in folded:
        lines.append(f"Incorporated: {q} (+{p})")
    if improvements:
        lines.append("\nTop places to improve or discuss:")
        lines += [f"  - {x}" for x in improvements[:3]]
    if questions:
        lines.append("\nQuestions that could improve this resume:")
        lines += [f"  ? {x}" for x in questions]
    if warnings:
        lines.append("\nReview before sending:")
        lines += [f"  ! {x}" for x in warnings]
    body = "\n".join(lines)
    if _rich_ok():
        _console.print(_Panel(body, title="Result"))
    else:
        print(body)


def table(headers: list, rows: list) -> None:
    """A plain grid. Extracted so the rich-or-plain branch is not written a third time."""
    if _rich_ok():
        t = _Table(show_header=True, box=None)
        for h in headers:
            t.add_column(str(h))
        for r in rows:
            t.add_row(*[str(c) for c in r])
        _console.print(t)
        return
    widths = [max(len(str(h)), *(len(str(r[i])) if i < len(r) else 0 for r in rows)) if rows else len(str(h))
              for i, h in enumerate(headers)]
    print("  ".join(str(h).ljust(w) for h, w in zip(headers, widths)))
    for r in rows:
        padded = list(r) + [""] * (len(headers) - len(r))
        print("  ".join(str(c).ljust(w) for c, w in zip(padded, widths)))


def box(lines, *, title: str = "") -> None:
    """A framed block. Plain Unicode box-drawing -- renders the same in the shell's
    output pane as on a terminal, no ANSI. Long lines are left as-is (the frame
    just widens); callers keep them short."""
    body = [str(x) for x in lines]
    inner = max([len(title) + 4] + [len(x) for x in body]) + 2
    if title:
        head = f"╭─ {title} " + "─" * (inner - len(title) - 3) + "╮"
    else:
        head = "╭" + "─" * inner + "╮"
    out = [head]
    out += [f"│ {x.ljust(inner - 2)} │" for x in body]
    out.append("╰" + "─" * inner + "╯")
    print("\n".join(out))


def _evidence_line(c: dict) -> str:
    ref = c.get("source_ref", "")
    weak = "" if c.get("strength") == "direct" else ", implied"
    return f'    ← "{c.get("evidence", "")}"  ({ref}{weak})'


def evidence_checkbox(msg: str, candidates: list, *, precheck=None) -> list:
    """Checkbox where every option carries the evidence that justifies it.

    A derived skill the user cannot trace back to something they actually wrote is a
    claim they will have to defend in an interview, so the evidence is shown at the
    point of choosing rather than buried in a log.
    """
    if not candidates:
        return []
    precheck = precheck or (lambda c: c.get("strength") == "direct")
    labels = [f'{c["skill"]}\n{_evidence_line(c)}' for c in candidates]
    if interactive() and console.available():
        # Key options by position, not by skill string: two candidates can share a
        # skill name (evidence_checkbox is public — skills.derive dedupes, other
        # callers need not), and a display-string key would show the second row the
        # first's evidence and return both when either is checked.
        tagged = [f"{i + 1}. {c['skill']}" for i, c in enumerate(candidates)]
        by_tag = dict(zip(tagged, candidates))
        picked = set(console.pick(
            msg, tagged, multi=True,
            detail=lambda t: _evidence_line(by_tag[t]).strip(),
            precheck=lambda t: precheck(by_tag[t])))
        return [c for t, c in zip(tagged, candidates) if t in picked]
    if _use_widgets():
        Choice = getattr(_q, "Choice", None)
        if Choice is not None:
            # Key by position, not by label text (mirrors the console branch above):
            # two candidates can render an identical label, and a `by_label` dict --
            # which collapses duplicate keys -- would show the second row the first's
            # evidence and resolve either pick to whichever came first.
            opts = [Choice(lbl, value=i, checked=precheck(c))
                    for i, (lbl, c) in enumerate(zip(labels, candidates))]
            picked = list(_q.checkbox(msg, choices=opts).ask() or [])
            return [candidates[i] for i in picked if isinstance(i, int) and 0 <= i < len(candidates)]
    print(f"{msg}  (comma-separated numbers, Enter for none)")
    for i, c in enumerate(candidates, 1):
        print(f"  {i}. {c['skill']}")
        print(_evidence_line(c))
    raw = _line("> ").strip()
    picks = []
    for tok in raw.split(","):
        tok = tok.strip()
        if tok.isdigit() and 1 <= int(tok) <= len(candidates):
            picks.append(candidates[int(tok) - 1])
    return picks


def merge_diff(plan: dict) -> None:
    """Show what a merge would do before it does it."""
    lines = []
    for m in plan.get("matched", []):
        lines.append(f"  {m.get('existing_ref', '')}")
        lines.append("    = matched existing")
        n = len(m.get("new_bullets") or [])
        if n:
            lines.append(f"    + {n} new bullets")
        for c in m.get("conflicts") or []:
            lines.append(f"    ! {c}")
    for a in plan.get("added", []):
        lines.append(f"  {a.get('_ref', '')}")
        lines.append("    + new entry")
    if not lines:
        lines.append("  Nothing to merge — every entry is already present.")
    body = "\n".join(lines)
    if _rich_ok():
        _console.print(_Panel(body, title="Merge plan"))
    else:
        print(body)
