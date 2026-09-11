#!/usr/bin/env python3
"""A persistent Resume-Tex session.

    python3 shell.py

One prompt loop rather than a script per application: content.json is loaded once
and stays warm, so the second job description of an evening costs a model call
rather than a cold start. On a real terminal this is a full-screen app (see
console.run_fullscreen): a fixed layout with a scrolling output pane, a question
panel that appears when a running command needs an answer, a command input and
a status bar, matching the terminal's own size. Everything printed is also
appended to .resume_shell.log, since leaving the alt-screen on /exit erases it
from the terminal itself. Off a real tty (piped, tests, CI) it falls back to a
plain line-by-line loop instead.

Nothing here reimplements the pipeline. Every command builds a real argv list and
hands it to tailor.parse_args, which means the shell inherits each flag default
instead of a hand-built Namespace that drifts as flags are added.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

if __name__ == "__main__":
    print("jobfeed — loading...", flush=True)

import console
import entries
import install
import jdsource
import llm
import recompile
import skills as skills_mod
import tailor
import ui
from profile import Profile

# figlet "Standard", the word "jobfeed"
BANNER = r"""
   _       _      __               _
  (_) ___ | |__  / _| ___  ___  __| |
  | |/ _ \| '_ \| |_ / _ \/ _ \/ _` |
  | | (_) | |_) |  _|  __/  __/ (_| |
 _/ |\___/|_.__/|_|  \___|\___|\__,_|
|__/
"""


@dataclass
class Session:
    root: Path
    backend: str
    model: str
    profile: Optional[Profile] = None
    output_dir: Optional[Path] = None

    def reload(self) -> None:
        """Pick up a content.json that a command has just rewritten.

        Profile.load -> _read_required calls sys.exit(1) on a missing or invalid
        constant.json / content.json. That raises SystemExit, which is NOT an
        Exception, so it must be caught explicitly here -- a first-run user who
        declines the bootstrap has no content.json yet, and startup's reload() has
        no other guard. Leave profile as None and point at the command that fixes it.
        """
        try:
            self.profile = Profile.load(self.root)
        except (SystemExit, Exception) as e:
            self.profile = None
            print(f"  No corpus loaded ({type(e).__name__}: {str(e)[:150]}). "
                  f"Run /setup to create content.json.")


class JDCancelled(Exception):
    """The applicant backed out of the fetched-JD review. Contained by dispatch."""


def _jd_preview_line(text: str, label: str) -> str:
    words = text.split()
    head = " ".join(words[:22])
    return f"  ⎿ {label} · {len(words):,} words · “{head}{'…' if len(words) > 22 else ''}”"


def _show_jd(text: str, label: str) -> None:
    """Drop the JD into the pane as a collapsible block (alt+o / ctrl+o expands
    it in place). Falls back to a bordered dump where there's no full-screen shell."""
    summary = _jd_preview_line(text, label)
    if not console.fold(summary, text):
        print("\n" + summary + "\n  ── full job description ──\n" + text + "\n  ── end ──")


_JD_REVIEW = {
    "Use this JD": "use",
    "Paste a replacement": "paste",
    "Cancel": "cancel",
}


def _review_fetched_jd(text: str, label: str) -> str:
    """Show the fetched JD as a collapsible block and let the applicant accept,
    replace, or cancel it. Returns the text to use. Raises JDCancelled on cancel.
    Non-interactively, returns `text` unchanged."""
    if not ui.interactive():
        return text
    _show_jd(text, label)
    while True:
        choice = _JD_REVIEW.get(console.pick("This job description —", list(_JD_REVIEW)), "use")
        if choice == "use":
            return text
        if choice == "cancel":
            raise JDCancelled(label)
        pasted = ui.editor("Paste the correct job description "
                           "(Enter to keep the fetched one):").strip()
        if pasted:
            text = pasted
            _show_jd(text, label + " (replaced)")


def jd_to_path(arg: str) -> Path:
    """A local path for the JD, fetching and materialising a URL when needed."""
    text, label = jdsource.load_jd(arg)
    if not jdsource.is_url(arg):
        return Path(arg).expanduser()
    text = _review_fetched_jd(text, label)
    tmp = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8")
    tmp.write(text)
    tmp.close()
    print(f"  Using {label} ({len(text.split()):,} words)")
    return Path(tmp.name)


def cmd_jd(session: Session, args: list) -> None:
    positional = [a for a in args if not a.startswith("-")]
    flags = [a for a in args if a.startswith("-")]

    if not positional:
        # No path or link -> paste the JD straight in.
        text = ui.editor("Paste the job description  (Enter to cancel)").strip()
        if not text:
            print("  Nothing pasted — cancelled.")
            return
        tmp = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8")
        tmp.write(text)
        tmp.close()
        print(f"  Using pasted JD ({len(text.split()):,} words)")
        path = Path(tmp.name)
    else:
        raw = positional[0]
        # A dragged-in or shell-history path arrives quoted or backslash-escaped;
        # those are shell syntax, not part of the name (the original ui.clean_path
        # bug). A URL must not be quote-stripped, so clean only local paths.
        path = jd_to_path(raw if jdsource.is_url(raw) else ui.clean_path(raw))

    # Session -o first, the user's own args after: argparse takes the last -o, so
    # an explicit "/jd x.txt -o DIR" must win over the session default. Forward
    # flags and any extra-context positional; the JD path goes last.
    extra_positional = positional[1:] if positional else []
    argv = (["-o", str(session.output_dir)] if session.output_dir else []) \
        + flags + extra_positional + [str(path)]
    tailor.run(tailor.parse_args(argv))


def cmd_skills(session: Session, args: list) -> None:
    skills_mod.run(session.root, backend=session.backend, model=session.model)
    session.reload()


def cmd_add(session: Session, args: list) -> None:
    import merge
    resume = Path(args[0]).expanduser() if args else ui.path(
        "Path to the resume to add (.pdf / .docx / .txt / .md)", must_exist=True)
    if not resume:
        print("  Skipped — no file given.")
        return
    merge.run(session.root, resume, backend=session.backend, model=session.model)
    session.reload()


def cmd_new(session: Session, args: list) -> None:
    """Add one entry to content.json: the model extracts fields from a pasted blob,
    you confirm, it drafts bullets. Reload only if an entry was actually written."""
    section = args[0] if args else None
    if entries.cmd_new(session.root, backend=session.backend, model=session.model,
                       section=section):
        session.reload()


def cmd_edit(session: Session, args: list) -> None:
    """Drill down content.json -- section, entry, scope -- and change one thing."""
    if entries.cmd_edit(session.root, backend=session.backend, model=session.model):
        session.reload()


def cmd_setup(session: Session, args: list) -> None:
    install.main()
    session.reload()


def cmd_recompile(session: Session, args: list) -> None:
    """Re-render a saved application from its edited JSON. With no path and an
    interactive terminal, pick one from the applications dir (newest first); a
    path skips the picker.

    Calls the library functions directly. recompile.main() parses sys.argv, which in
    a REPL is the shell's own argv, not the user's command.
    """
    import recompile_menu

    flags = {a for a in args if a.startswith("-")}
    positional = [a for a in args if not a.startswith("-")]
    mode = "cv" if "--cv" in flags else "resume"

    if positional:
        app_dir = Path(positional[0]).expanduser().resolve()
    elif ui.interactive():
        base = Path(session.output_dir or os.environ.get("RESUME_TEX_OUTPUT_DIR")
                    or (session.profile.output_dir if session.profile else "")
                    or (Path.home() / "Documents" / "Applications")).expanduser()
        apps = recompile.recent_apps(base)
        if not apps:
            print(f"  No saved applications under {base}. Pass a folder path.")
            return
        rows = [(a["dir"], f"{a['ref']}   {_ago(a['mtime'])}   {'+'.join(a['modes'])}")
                for a in apps] + [(None, "← cancel")]
        app_dir = console.ask_choice("Recompile which application?", rows)
        if app_dir is None or app_dir is console.CHOICE_CANCELLED:
            return
        chosen = next(a for a in apps if a["dir"] == app_dir)
        if "--cv" not in flags and chosen["modes"] == ["cv"]:
            mode = "cv"
    else:
        app_dir = Path.cwd()

    if flags & {"-i", "--interactive"}:
        recompile_menu.run_menu(app_dir, root=session.root, mode=mode)
    print(f"  Recompiled: {recompile.recompile_folder(app_dir, root=session.root, mode=mode)}")


def _ago(mtime: float) -> str:
    """A compact "3d ago" / "3w ago" for the recompile picker. Anything under a
    minute reads as "just now"."""
    secs = max(0, time.time() - mtime)
    if secs < 60:
        return "just now"
    for unit, size in (("w", 7 * 86400), ("d", 86400), ("h", 3600), ("m", 60)):
        if secs >= size:
            return f"{int(secs // size)}{unit} ago"
    return "just now"


def _counts(session: Session) -> tuple:
    content = session.profile.content if session.profile else {}
    return (
        len(content.get("experience") or []),
        len(content.get("projects") or []),
        sum(len(g.get("entries") or []) for g in content.get("skills") or []),
    )


def cmd_status(session: Session, args: list) -> None:
    """What is loaded, in a box. /status and its /i shortcut both land here."""
    exp, proj, skl = _counts(session)
    ui.box([
        f"experience   {exp}",
        f"projects     {proj}",
        f"skills       {skl}",
        f"backend      {session.backend} / {session.model}",
        f"output dir   {session.output_dir or '(default)'}",
    ], title="loaded")


def startup_summary(session: Session) -> None:
    """The launch box: the wordmark, where we are, and what is loaded."""
    exp, proj, skl = _counts(session)
    print(BANNER)
    ui.box([
        str(session.root),
        f"{exp} experience · {proj} projects · {skl} skills",
        f"{session.backend} / {session.model}",
        "/help for commands · /exit to leave",
    ], title="jobfeed")


# (name, shortcut letter, arg hint, one-line description). SHORTCUTS and the
# /help listing are both built from this, so a command is described in one place.
_HELP = [
    ("/jd",        "j", "<path|url> [flags]", "tailor a resume for one job description"),
    ("/batch",     "b", "<glob|dir|file>",    "score several, then generate the ones you pick"),
    ("/skills",    "s", "",                   "fill in your skills and derive more"),
    ("/new",       "n", "[section]",          "add one entry - model extracts, you confirm"),
    ("/edit",      "e", "",                   "browse content.json and change an entry or a bullet"),
    ("/add",       "a", "[path]",             "add another resume/CV to content.json"),
    ("/recompile", "r", "",                   "re-render a saved application (picker if no path)"),
    ("/status",    "i", "",                   "what is loaded"),
    ("/setup",     "",  "",                   "re-run setup"),
    ("/help",      "h", "",                   "this list"),
    ("/exit",      "q", "",                   "leave"),
]
SHORTCUTS = {f"/{s}": name for name, s, _, _ in _HELP if s}
SHORTCUTS["/rc"] = "/recompile"
# Every real command name, in menu order -- for resolving a partial like /exi.
_ALL_COMMANDS = [name for name, _s, _h, _d in _HELP]


def cmd_help(session: Session, args: list) -> None:
    name_w = max(len(f"{n} {h}".strip()) for n, _, h, _ in _HELP)
    lines = []
    for name, short, hint, desc in _HELP:
        left = f"{name} {hint}".strip().ljust(name_w)
        tag = f"·{short}" if short else "  "
        lines.append(f"  {left}   {tag}   {desc}")
    print("\n".join(lines))
    print("\n  ·x is the shortcut: /i == /status. A bare path or URL (no /jd) is /jd;")
    print("  a bare /jd opens a paste box. Partial names resolve: /exi -> /exit.")


# Command names, not function objects: dispatch resolves them through the module
# namespace at call time. Binding the functions here would freeze them at import,
# so patching shell.cmd_jd (this project's testing idiom everywhere else) would
# rebind the name while dispatch kept calling the original.
COMMANDS = {
    "/jd": "cmd_jd",
    "/skills": "cmd_skills",
    "/new": "cmd_new",
    "/edit": "cmd_edit",
    "/add": "cmd_add",
    "/setup": "cmd_setup",
    "/recompile": "cmd_recompile",
    "/status": "cmd_status",
    "/help": "cmd_help",
}


def dispatch(session: Session, line: str) -> bool:
    """Run one line. Returns False when the session should end.

    Every failure mode is contained here. SystemExit is caught alongside Exception
    because tailor.read_jd and tailor.run exit the process on a missing or empty JD --
    correct for a one-shot script, fatal for a REPL.
    """
    line = (line or "").strip()
    if not line:
        return True
    if line in ("exit", "quit"):
        return False

    # A slash-command's first token has no further slash (/jd, /help, /nope). An
    # absolute POSIX path starts with a slash too (/Users/me/jd.txt), so testing
    # startswith("/") alone would route every dragged-in path to "unknown command" --
    # do not "simplify" this back to that.
    parts = line.split()
    if line.startswith("/") and "/" not in parts[0][1:]:
        name, args = parts[0], parts[1:]
        name = SHORTCUTS.get(name, name)          # /i -> /status, /j -> /jd, ...
        if name not in COMMANDS and name != "/batch":
            # A partial command + Enter resolves to its first match, menu order:
            # /exi -> /exit, /rec -> /recompile, /s -> /skills.
            name = next((c for c in _ALL_COMMANDS if c.startswith(name)), name)
        if name in ("/exit", "/quit"):
            return False
    elif jdsource.is_url(line) or "/" in line or "." in parts[0]:
        # A pasted job link or a dragged-in file -> /jd, whole line unsplit (a
        # dragged file produces names with spaces). It runs the SAME interactive
        # flow as typing "/jd ..." -- score, then the questions, then generate.
        # For unattended, type "/jd <url> -y" or use /batch.
        name, args = "/jd", [line]
    else:
        # A bare word that is neither a command, a URL, nor a path.
        print(f"  Unknown command /{parts[0].lstrip('/')}. Type /help.")
        return True

    if name != "/batch" and name not in COMMANDS:
        print(f"  Unknown command {name}. /help lists them.")
        return True

    try:
        if name == "/batch":
            from batch import cmd_batch      # Task 9; import inside try so a
            fn = cmd_batch                   # missing module is contained, not fatal
        else:
            fn = globals()[COMMANDS[name]]
        fn(session, args)
    except JDCancelled:
        print("  Cancelled — no resume generated.")
    except KeyboardInterrupt:
        print("\n  Aborted. The session is still open.")
    except SystemExit as e:
        print(f"  Command stopped (exit {e.code}).")
    except Exception as e:
        print(f"  {type(e).__name__}: {str(e)[:400]}")
    return True


def _first_run(root: Path) -> bool:
    path = root / "content.json"
    if not path.exists():
        return True
    try:
        content = json.loads(path.read_text())
    except Exception:
        return True
    return not (content.get("experience") or content.get("projects"))


def main() -> None:
    if _first_run(ROOT):
        print("No content.json yet — running setup first.\n")
        install.main()

    # detect_backend raises LLMError when no CLI is on PATH -- exactly the first-run
    # user. Match tailor.run: print the message (it names both CLIs and how to
    # install them) and exit cleanly rather than dumping a traceback.
    try:
        backend, model = llm.resolve()
    except llm.LLMError as e:
        print(f"Error: {e}")
        sys.exit(1)
    session = Session(root=ROOT, backend=backend, model=model)

    # Captured rather than printed directly: on a real tty this becomes the
    # output pane's opening content instead of text on the terminal's primary
    # screen buffer, which the alt-screen the full-screen path switches to
    # would simply hide.
    banner_buf = io.StringIO()
    with contextlib.redirect_stdout(banner_buf):
        session.reload()
        startup_summary(session)
        print()
    banner = banner_buf.getvalue()

    status = f"{session.backend}/{session.model}"
    history = ROOT / ".resume_history"
    # (name, shortcut, description) for the completion menu — one row per real
    # command; /i, /rc etc. resolve but don't get their own row.
    cmd_meta = [(name, short, desc) for name, short, _hint, desc in _HELP]

    if console.available():
        console.run_fullscreen(lambda line: dispatch(session, line), status=status,
                               history_file=history, log_path=ROOT / ".resume_shell.log",
                               banner=banner, commands=cmd_meta)
    else:
        print(banner, end="")
        while True:
            try:
                line = console.prompt("> ", status=status, history_file=history,
                                      commands=cmd_meta)
            except KeyboardInterrupt:
                print()
                continue
            if not dispatch(session, line):
                break
    print("Bye.")


if __name__ == "__main__":
    main()
