#!/usr/bin/env python3
"""One-time setup for Resume-Tex. Re-runnable -- each step offers skip/redo.

    python3 install.py
"""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

if __name__ == "__main__":
    # Printed before the imports below, which pull in pydantic, jinja2 and the whole of
    # tailor.py and take a beat on a cold start. Silence there reads as a hang.
    print("Resume-Tex setup — loading...", flush=True)

from pydantic import BaseModel

import llm
import notes
import tailor
import ui
from profile import Profile
from tailor import EducationEntry, ExperienceEntry, ProjectEntry, SkillGroup

MIN_PY = (3, 9)

_OPTIONAL = ["Certifications", "Awards", "Publications", "Languages"]
_OPTIONAL_KEY = {"Certifications": "certifications", "Awards": "awards",
                 "Publications": "publications", "Languages": "languages"}
_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_PLUGIN_LINE = re.compile(r"^\s+([A-Za-z0-9][A-Za-z0-9._-]*)(?:\s+v?\d[\w.+-]*)?\s*$")


class IdentityBlock(BaseModel):
    name: str = ""
    email: str = ""
    phone: str = ""
    linkedin: str = ""
    location: str = ""


class BootstrapExtract(BaseModel):
    identity: IdentityBlock
    experience: list[ExperienceEntry] = []
    projects: list[ProjectEntry] = []
    skills: list[SkillGroup] = []
    education: list[EducationEntry] = []


BOOTSTRAP_SYSTEM = (
    "Extract structured resume data from the text below. Copy facts verbatim. Do not "
    "invent, embellish, add metrics, or infer skills not written. Leave a field empty "
    "if the text does not state it. Leave notes empty. Return the schema only."
)


def check_dependencies() -> list:
    problems = []
    if sys.version_info < MIN_PY:
        problems.append(f"Python {MIN_PY[0]}.{MIN_PY[1]}+ required (have {sys.version.split()[0]}).")
    if shutil.which("pdflatex") is None:
        problems.append(
            "LaTeX not found (pdflatex). macOS: brew install --cask basictex ; "
            "Linux: sudo apt install texlive-latex-recommended texlive-fonts-extra ; "
            "then: sudo tlmgr install crimson titlesec enumitem")
    if shutil.which("gs") is None:
        problems.append("Ghostscript not found (optional -- only used to report page fill).")
    return problems


def _packages_present() -> bool:
    """Whether the imports this project needs already resolve in this interpreter."""
    try:
        import jinja2, pydantic, questionary, rich   # noqa: F401
    except ImportError:
        return False
    return True


def _pip_install():
    present = _packages_present()
    if present:
        print("Python packages: already installed.")
    if ui.confirm("Install Python packages from requirements.txt now?", default=not present):
        subprocess.run([sys.executable, "-m", "pip", "install", "-r", str(ROOT / "requirements.txt")])


def auth_argv(backend: str, provider: str = "") -> list:
    """The CLI's own login command. Claude Code logs in from its own TUI."""
    binary = llm.DEFAULT_BINARY[backend]
    if backend == "openclaw":
        return [binary, "models", "auth", "login", "--provider", provider, "--set-default"]
    return [binary]


def openclaw_plugins(*, run=None) -> list:
    """Provider plugin names OpenClaw reports as installed. [] means "could not tell".

    Returns an all-or-nothing list: either a clean set of plugin names, or [] if any
    line looks malformed. A wrong list is worse than no information — it makes ensure_plugin
    skip a needed install, reintroducing the exact bug this function was added to prevent.

    A line is a valid plugin entry only if the whole line matches: plugin name, optionally
    followed by a version, then end of line. A prose line beginning with a provider name
    (e.g. "openai models require authentication") would otherwise be read as that provider
    being installed, making ensure_plugin skip the install the user needs.
    """
    run = run or subprocess.run
    try:
        proc = run([llm.DEFAULT_BINARY["openclaw"], "plugins", "list"],
                   capture_output=True, text=True, timeout=30)
    except Exception:
        return []
    if getattr(proc, "returncode", 1):
        return []
    names = []
    for line in (getattr(proc, "stdout", "") or "").splitlines():
        # Strip ANSI escape sequences (e.g. colour codes)
        line = _ANSI.sub("", line)
        # Skip non-indented headings like "Installed plugins:" or "No plugins"
        if not line[:1].isspace() or line.startswith(("Installed", "No ")):
            continue
        # Match whole line: plugin name, optionally followed by version, then end of line
        match = _PLUGIN_LINE.match(line)
        if not match:
            # Unrecognised output format (e.g. JSON, box-drawing, prose description).
            # Return [] to signal we cannot trust this output.
            return []
        names.append(match.group(1))
    return names


def ensure_plugin(provider: str, *, run=None) -> bool:
    """Make sure OpenClaw has a plugin for `provider`. Never fatal.

    A fresh OpenClaw ships no provider plugins, so `models auth login --provider claude`
    fails with "No provider plugins found" -- an error the user cannot act on from
    inside this installer. Offering the install here is the difference between a
    dead end and a working setup.
    """
    run = run or subprocess.run
    installed = openclaw_plugins(run=run)
    if not installed:
        return True                    # cannot tell; let the login attempt speak
    if provider in installed:
        return True
    print(f"\n  OpenClaw has no plugin for '{provider}' "
          f"(installed: {', '.join(installed) or 'none'}).")
    argv = [llm.DEFAULT_BINARY["openclaw"], "plugins", "install", provider]
    if not ui.confirm(f"Run `{' '.join(argv)}` now?", default=True):
        print(f"  Later: {' '.join(argv)}")
        return False
    with ui.spinner(f"Installing the {provider} plugin"):
        code = getattr(run(argv), "returncode", 0)
    if code:
        print(f"  `{' '.join(argv)}` exited {code} — its own message is above.")
        return False
    print(f"  Installed the {provider} plugin.")
    return True


def connect_backend(backend: str, *, run=None) -> bool:
    """Walk one CLI's login. Returns whether it completed cleanly.

    Never fatal. A CLI can fail here for reasons this installer cannot fix -- a fresh
    OpenClaw has no provider plugins yet, and its own error names the command to run --
    so the failure is reported and setup continues rather than dropping every answer
    given so far.
    """
    run = run or subprocess.run
    provider = ""
    if backend == "openclaw":
        provider = ui.select("Which provider should OpenClaw connect to?", ["openai", "claude"])
        ensure_plugin(provider, run=run)
    argv = auth_argv(backend, provider)
    printable = " ".join(argv)

    if backend == "claude":
        ui.hint("Claude Code logs in through its own screen. If it opens, finish the "
                "login and quit it (/exit) to come back here.")
    if not ui.confirm(f"Run `{printable}` now?", default=True):
        print(f"  Later: {printable}")
        return False

    # Not captured: a login is interactive (browser handoff, pasted key, a full TUI),
    # so the CLI needs the terminal. Its own output is what the user just read.
    code = getattr(run(argv), "returncode", 0)
    if code:
        print(f"\n  `{printable}` exited {code} — its own message is above.")
        if backend == "openclaw":
            print("  A fresh OpenClaw ships no provider plugins; install the one it names,")
            print("  then re-run this step. Setup continues either way.")
        return False
    return True


def verify_backend(backend: str, *, timeout: float = 120.0) -> str:
    """Send one throwaway prompt. Returns "" on success, or the error to show.

    Worth the seconds it costs: a login that silently did not take shows up here, at
    setup, instead of eight steps later in the middle of a real application.
    """
    _, model = llm.resolve(llm.CHEAPEST_PRESET[backend], backend=backend)
    try:
        with ui.spinner(f"Checking {backend} can reach {model}"):
            llm.run_once(backend=backend, model=model,
                         system="Reply with the single word: ok.",
                         user="Reply with the single word: ok.", timeout=timeout)
    except llm.LLMError as e:
        return str(e)[:400]
    return ""


def setup_backend(*, which=None, run=None, preferred: str = "") -> str:
    """Pick the default CLI, connect it, and optionally connect the other one too."""
    which = which or shutil.which
    run = run or subprocess.run
    found = llm.available_backends(which=which)
    if not found:
        print("No model CLI found. Install one:")
        print("  Claude Code:  npm install -g @anthropic-ai/claude-code   (then run `claude` to log in)")
        print("  OpenClaw:     see https://docs.openclaw.ai (then run `openclaw onboard`)")
        sys.exit(1)

    if len(found) > 1:
        # Both installed: the user picks, rather than inheriting detect_backend's
        # preference for OpenClaw -- which is wrong for anyone whose OpenClaw is not
        # actually connected to a provider.
        ordered = ([preferred] + [b for b in found if b != preferred]
                   if preferred in found else found)
        backend = ui.select("Which CLI should Resume-Tex use by default?", ordered)
    else:
        backend = found[0]

    logged_in = connect_backend(backend, run=run)

    for other in [b for b in found if b != backend]:
        if ui.confirm(f"Also connect {other} now? (you can switch with --provider later)",
                      default=False):
            connect_backend(other, run=run)

    # The probe, not the login command, is the real answer to "does this work?".
    # An already-connected CLI can fail `auth login` for its own reasons -- needing a
    # TTY it does not have, or a plugin it does not need for the call this project
    # makes -- and that failure says nothing about whether a generation will run.
    if ui.confirm(f"Send a one-line test prompt to check {backend} works?", default=True):
        problem = verify_backend(backend)
        if not problem:
            # Reached whether the login step succeeded, was declined, or failed: the
            # call went through, so there is nothing left to fix either way.
            print(f"  {backend} is working"
                  + ("." if logged_in else " — no further login needed."))
            return backend
        print(f"  {backend} could not complete a call:\n    {problem}")
        others = [b for b in found if b != backend]
        if others and ui.confirm(f"Use {others[0]} as the default instead?", default=True):
            backend = others[0]
    elif not logged_in:
        print(f"  Heads up: the {backend} login did not complete, and nothing has been "
              "tested. Run install.py again once it is sorted.")
    return backend


def write_constant_key(root: Path, key: str, value) -> None:
    path = root / "constant.json"
    data = json.loads(path.read_text()) if path.exists() else {}
    data[key] = value
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def extract_resume_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in (".txt", ".md"):
        return path.read_text()
    if suffix == ".pdf":
        try:
            import pdfplumber
        except ImportError:
            print("PDF support needs pdfplumber:  pip install pdfplumber")
            sys.exit(1)
        with pdfplumber.open(str(path)) as pdf:
            return "\n".join(page.extract_text() or "" for page in pdf.pages)
    if suffix == ".docx":
        try:
            import docx
        except ImportError:
            print("DOCX support needs python-docx:  pip install python-docx")
            sys.exit(1)
        return "\n".join(p.text for p in docx.Document(str(path)).paragraphs)
    print(f"Unsupported resume file type: {suffix or '(none)'}. Use .pdf, .docx, .txt, or .md.")
    sys.exit(1)


def bootstrap_from_resume(path: Path, root: Path, *, backend: str, model: str) -> None:
    text = extract_resume_text(Path(path))
    data, _ = llm.complete_schema(backend=backend, model=model, system=BOOTSTRAP_SYSTEM,
                                  user=text[:20000], schema=BootstrapExtract, thinking="low")
    ident = data["identity"]
    constant = {
        **{k: ident.get(k, "") for k in ("name", "email", "phone", "linkedin", "location")},
        "backend": backend,
        "portfolios": "",
        "solo_worker": False,
        "banned_summary_anchors": [],
        "skills_title": "Skills",
        "doc_basename": (ident.get("name") or "Resume").replace(" ", "_"),
        "output_dir": "",
    }
    content = {"portfolio_link": "", "summary": ""}
    for key in ("experience", "projects", "skills", "education"):
        items = data.get(key, [])
        if key in ("experience", "projects"):
            for it in items:
                it.setdefault("notes", "")
        content[key] = items
    (root / "constant.json").write_text(json.dumps(constant, indent=2, ensure_ascii=False) + "\n")
    (root / "content.json").write_text(json.dumps(content, indent=2, ensure_ascii=False) + "\n")


def _load_content(root: Path) -> dict:
    return json.loads((root / "content.json").read_text())


def _save_content(root: Path, content: dict) -> None:
    (root / "content.json").write_text(json.dumps(content, indent=2, ensure_ascii=False) + "\n")


STATIC_PROMPTS = [
    "What was the scale — users, revenue, requests, team size?",
    "What constraint shaped the solution?",
    "What changed as a result, and how do you know?",
]

PROMPTS_SYSTEM = (
    "For each resume entry below, write 2-3 short questions whose answers would make "
    "its bullets more specific and more credible. Ask about scale, constraints, and "
    "measured outcomes the bullets do not already state. Do not ask about anything "
    "already written. The entry's `notes` field is context already captured — treat "
    "it as already written and do not re-ask it. Do not invent facts. Use the entry's "
    "exact ref string. Return the schema only."
)


class _EntryPrompt(BaseModel):
    ref: str
    questions: list[str] = []


class EntryPrompts(BaseModel):
    entries: list[_EntryPrompt] = []


def entry_prompts(targets: list, *, backend: str, model: str) -> dict:
    """{"kind:label": [question, ...]} for every target. One call, never raises.

    Batched deliberately: asking per entry would stall the walkthrough between each
    one, which is the pause that made the old flow feel hung.
    """
    refs = [f"{kind}:{label}" for kind, _entry, label in targets]
    payload = [{"ref": f"{kind}:{label}",
                "bullets": (entry.get("bullets") or []),
                "notes": (entry.get("notes") or "")}
               for kind, entry, label in targets]
    try:
        with ui.spinner(f"Working out what context would help for {len(refs)} entries"):
            data, _ = llm.complete_schema(
                backend=backend, model=model, system=PROMPTS_SYSTEM,
                user=json.dumps(payload, separators=(",", ":"), ensure_ascii=False)[:20000],
                schema=EntryPrompts, thinking="low")
    except Exception:
        ui.hint("Could not reach the model — the context prompts below are generic.")
        return {ref: STATIC_PROMPTS for ref in refs}
    # The model echoes each ref back; whitespace or casing drift would miss the lookup
    # for every entry and silently degrade all of them to STATIC_PROMPTS. Normalise
    # both sides, and if nothing matched at all, say so rather than fake success.
    got = {(e.get("ref") or "").strip().casefold(): [q for q in (e.get("questions") or []) if q]
           for e in data.get("entries", [])}
    matched = {ref: got[ref.strip().casefold()]
               for ref in refs if ref.strip().casefold() in got}
    if not matched:
        ui.hint("Could not match the model's answers to your entries — the context "
                "prompts below are generic.")
        return {ref: STATIC_PROMPTS for ref in refs}
    return {ref: matched.get(ref) or STATIC_PROMPTS for ref in refs}


def choose_optional_sections(root: Path) -> None:
    content = _load_content(root)
    for label in ui.checkbox("Include optional sections:", _OPTIONAL):
        content.setdefault(_OPTIONAL_KEY[label], [])
    _save_content(root, content)


def preferences_walkthrough(root: Path) -> None:
    # Bootstrap already derived location / doc_basename / skills_title from the résumé.
    # Seed every prompt whose value it set with the current file so a bare Enter keeps
    # what was extracted instead of a hardcoded placeholder.
    cpath = root / "constant.json"
    current = json.loads(cpath.read_text()) if cpath.exists() else {}
    kind = ui.select("How many portfolios? (one, or a visual/research split)", ["One", "Two"])
    if kind == "One":
        write_constant_key(root, "portfolios", ui.text("Portfolio URL"))
    else:
        write_constant_key(root, "portfolios", {
            "visual": ui.text("Visual portfolio URL"),
            "research": ui.text("Research portfolio URL")})

    loc = ui.select("Location shown on the resume: fixed, or varies by job region?",
                    ["Fixed", "Varies by job region"])
    if loc == "Fixed":
        write_constant_key(root, "location", ui.text(
            "City shown on the resume", default=current.get("location", "") or "Seattle, WA"))
    else:
        write_constant_key(root, "location", ui.text("Default city (when no rule matches)"))
        rules = []
        while ui.confirm("Add a region rule?", default=bool(not rules)):
            rules.append({"match": ui.text("Regex to match in the JD (e.g. \\bIndia\\b)"),
                          "location": ui.text("City to show when it matches")})
        if rules:
            write_constant_key(root, "location_rules", rules)

    ui.hint("Solo work: you span the whole job yourself — research, design, build —\n"
            "rather than handing a stage off to someone else. Saying yes adds a prompt\n"
            "rule and a lint that strip invented teammates (\"partnered with the data\n"
            "team\"), which models add on their own because job ads are written in the\n"
            "plural. Say no if you genuinely work with a team; the lint would then flag\n"
            "every true sentence you have about them.")
    write_constant_key(root, "solo_worker", ui.confirm(
        "Is your work solo — you do the research, design and build yourself?",
        default=False))
    anchors = []
    while ui.confirm("Add a phrase that may appear in bullets but never headline the summary?",
                     default=bool(not anchors)):
        anchors.append(ui.text("Phrase or number"))
    write_constant_key(root, "banned_summary_anchors", anchors)
    write_constant_key(root, "skills_title", ui.text(
        "Skills section heading", default=current.get("skills_title") or "Skills"))
    write_constant_key(root, "doc_basename", ui.text(
        "PDF filename stem", default=current.get("doc_basename") or current.get("name") or "Resume"))
    write_constant_key(root, "output_dir",
                       ui.text("Output directory (blank = ~/Documents/Applications)", default=""))


def notes_walkthrough(root: Path, *, backend: str, model: str) -> None:
    content = _load_content(root)
    profile = Profile.load(root)
    targets = [(kind, entry, entry.get(match) or "")
               for kind, key, match in (("experience", "experience", "company"),
                                        ("project", "projects", "title"))
               for entry in content.get(key, [])]
    targets = [t for t in targets if t[2]]   # an unnamed entry has no valid note target
    if targets:
        ui.hint(f"{len(targets)} entries. Each answer is distilled by the model into a "
                "short source note, so expect a pause after you finish one. Enter skips.")
    guidance = entry_prompts(targets, backend=backend, model=model) if targets else {}
    for i, (kind, entry, label) in enumerate(targets, 1):
        print(f"\n[{i}/{len(targets)}] {kind}: {label}")
        for b in entry.get("bullets", []):
            print(f"  - {b}")
        questions = guidance.get(f"{kind}:{label}") or STATIC_PROMPTS
        print("\n  Useful context here:")
        for q in questions:
            print(f"    · {q}")
        raw = ui.editor("Add context — constraints, real numbers, why (Enter to skip):")
        if not raw.strip():
            continue
        with ui.spinner(f"Distilling your note on {label}"):
            note = tailor.distill_note(label, raw, backend=backend, model=model)
        if not note:
            continue
        target = notes.resolve_target(profile, f"{kind}:{label}")
        if not target:
            continue
        print(f"\n  distilled note ({target}): {note}")
        if not ui.confirm("Save this note to content.json?", default=True):
            continue
        profile.append_note(target, note)


def _step(name, fn, *, done: bool = False) -> None:
    """Run one setup step, defaulting to whatever a re-run most likely wants.

    Re-running the installer to fix one answer should not mean re-typing every other
    answer, so a step that is already configured defaults to skip and says so.
    """
    if not ui.interactive():
        if not done:
            fn()
        return
    prompt = f"{name} — already set up. Redo it?" if done else f"Set up {name}?"
    if ui.confirm(prompt, default=not done):
        fn()


def _configured(root: Path) -> dict:
    """What previous runs have already left on disk, per step."""
    const_path, content_path = root / "constant.json", root / "content.json"
    const = json.loads(const_path.read_text()) if const_path.exists() else {}
    content = json.loads(content_path.read_text()) if content_path.exists() else {}
    entries = list(content.get("experience") or []) + list(content.get("projects") or [])
    return {
        "backend": bool(const.get("backend")) and shutil.which(
            llm.DEFAULT_BINARY.get(const.get("backend"), "")) is not None,
        "content": bool(content.get("experience") or content.get("projects")),
        "optional": any(k in content for k in _OPTIONAL_KEY.values()),
        # portfolios is written only by the preferences walkthrough
        "preferences": "portfolios" in const,
        "notes": any((e.get("notes") or "").strip() for e in entries),
        "current_backend": const.get("backend", ""),
    }


def bootstrap_step(root: Path, *, backend: str, done: bool) -> None:
    """Build content.json from a resume, or add another resume to what is there.

    Defaults to bootstrapping on a first run (there is nothing to lose). Once content
    exists, replacing it discards the notes that make tailoring good, so adding is
    offered first and replacing is spelled out as destructive.
    """
    import merge

    _, model = llm.resolve(backend=backend)
    if not done:
        if not ui.confirm("Bootstrap content.json and constant.json from an existing resume file?",
                          default=True):
            return
        resume = ui.path("Path to your resume (.pdf / .docx / .txt / .md)", must_exist=True)
        if not resume:
            print("  Skipped — no file given.")
            return
        # `done` is False whenever experience/projects are empty, but constant.json exists
        # independently (the preferences walkthrough writes portfolios / location_rules
        # before anything is bootstrapped) and gets clobbered here too. Snapshot whatever
        # exists — guard on EITHER file, not just content.json.
        if (root / "content.json").exists() or (root / "constant.json").exists():
            saved = merge.backup(root, include_constant=True)
            if saved:
                print(f"  Backed up {', '.join(p.name for p in saved)} before rebuilding.")
        with ui.spinner(f"Reading {resume.name} and extracting your experience"):
            bootstrap_from_resume(resume, root, backend=backend, model=model)
        print(f"  Wrote content.json and constant.json from {resume.name}.")
        return

    content = _load_content(root)
    counts = (len(content.get("experience") or []), len(content.get("projects") or []))
    print(f"\ncontent.json already has {counts[0]} experience and {counts[1]} project entries.")
    choice = ui.select("What would you like to do?", [
        "Add another resume/CV to it",
        "Replace it entirely (discards your notes and saved preferences)",
        "Skip",
    ])
    if choice.startswith("Skip"):
        return
    resume = ui.path("Path to the resume (.pdf / .docx / .txt / .md)", must_exist=True)
    if not resume:
        print("  Skipped — no file given.")
        return
    if choice.startswith("Add"):
        merge.run(root, resume, backend=backend, model=model)
        return
    if ui.confirm(f"Replace content.json and constant.json from {resume.name}? "
                  "This cannot be undone.", default=False):
        # bootstrap_from_resume overwrites constant.json too — portfolios, location_rules,
        # output_dir and the rest — so back up both, not just content.json.
        saved = merge.backup(root, include_constant=True)
        with ui.spinner(f"Reading {resume.name} and extracting your experience"):
            bootstrap_from_resume(resume, root, backend=backend, model=model)
        names = ", ".join(p.name for p in saved) if saved else "nothing (no existing files)"
        print(f"  Rebuilt content.json and constant.json from {resume.name} "
              f"(previous saved to {names}).")


def main():
    if sys.version_info < MIN_PY:
        print(f"Resume-Tex needs Python {MIN_PY[0]}.{MIN_PY[1]}+ (have {sys.version.split()[0]}). "
              "Install a newer Python and re-run.")
        sys.exit(1)
    print("Resume-Tex setup\n")
    state = _configured(ROOT)
    if any(state[k] for k in ("backend", "content", "preferences", "notes")):
        print("Found an existing setup — each step below defaults to skipping what is\n"
              "already configured. Press Enter through anything you do not want to redo.\n")
    for problem in check_dependencies():
        print(f"  - {problem}")
    _pip_install()

    backend = state["current_backend"]

    def _do_backend():
        nonlocal backend
        backend = setup_backend(preferred=backend)
        write_constant_key(ROOT, "backend", backend)

    _step(f"the model CLI (currently {backend})" if backend else "the model CLI",
          _do_backend, done=state["backend"])
    if not backend:
        # Nothing configured and the step was declined: later steps need a backend.
        try:
            backend = llm.detect_backend()
        except llm.LLMError as e:
            print(f"\n{e}")
            sys.exit(1)

    bootstrap_step(ROOT, backend=backend, done=state["content"])

    _step("optional sections", lambda: choose_optional_sections(ROOT), done=state["optional"])
    _step("preferences", lambda: preferences_walkthrough(ROOT), done=state["preferences"])
    _, score_id = llm.score_model(backend, llm.DEFAULT_PRESET[backend], None)
    _step("the notes walkthrough",
          lambda: notes_walkthrough(ROOT, backend=backend, model=score_id),
          done=state["notes"])

    print("\nSetup complete. Try:  python3 tailor.py path/to/jd.txt")


if __name__ == "__main__":
    main()
