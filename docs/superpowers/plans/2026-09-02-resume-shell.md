# Resume-Tex Shell Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the one-shot scripts with a persistent REPL that keeps `content.json` warm across many job descriptions, and add four capabilities it enables — batch scoring, URL job descriptions, evidence-linked skills derivation, and adding a résumé to existing content without losing notes.

**Architecture:** Four new thin modules (`jdsource.py`, `skills.py`, `merge.py`, `shell.py`) plus three `ui.py` widgets and targeted `install.py` fixes. Nothing in `tailor.py`'s generation pipeline changes: the shell builds a real argv list and calls `tailor.parse_args(argv)` then `tailor.run(args)`, inheriting every flag default rather than hand-building a Namespace that would drift.

**Tech Stack:** Python 3.9+, pydantic, questionary, rich, jinja2, pytest. New: `requests`, `beautifulsoup4`.

**Spec:** `docs/superpowers/specs/2026-09-02-resume-shell-design.md`

## Global Constraints

- Python floor is **3.9** (`install.MIN_PY = (3, 9)`). No `match`, no PEP 604 `X | Y` in annotations evaluated at runtime. Existing modules use `from __future__ import annotations` where they need modern syntax — follow that.
- **Never write test output into `~/Documents/Applications`.** That directory holds real application records. Every test writes to `tmp_path`; every manual check passes `--output-dir` explicitly.
- **No test may make a real model call or a real network request.** Stub `llm.complete_schema`, `llm.complete_text`, and `requests.get`.
- Existing suites must keep passing untouched: `test_install.py`, `test_ui.py`, `test_flow.py`, `test_llm.py`, `test_recompile.py`, `test_profile.py`, `test_portfolio.py`, `test_score.py`, `test_tailor_quality.py`, `test_template_sections.py`. They are the regression guard that the one-shot CLIs still work.
- Every `ui.py` function degrades to plain `print`/`input` when `ui.interactive()` is False. New widgets must do the same — the autouse `_no_tty` fixture in `test_ui.py` exercises exactly that path.
- Model calls go through `llm.complete_schema(backend=, model=, system=, user=, schema=, thinking=, binary=, timeout=)` returning `(dict, Reply)`. Never call a CLI directly.
- **JSON sent to a model is minified**: `json.dumps(payload, separators=(",", ":"), ensure_ascii=False)`. Indentation and spaces after separators are pure token cost in a prompt. This is the existing convention — see llm.py:225 and tailor.py:487, 620, 1103 — so match it, do not invent a variant.
- **JSON written to disk keeps `indent=2`.** `content.json` and `constant.json` are hand-edited by the user; minifying them saves nothing (they are parsed and re-serialized before reaching any prompt) and costs readability. Do not "optimise" the file writes.
- Any operation over ~1s is wrapped in `ui.spinner(msg)`.
- Commit after each task. Commit messages end with:
  ```
  Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01Pd3ymHfez9zTb3kKjF8JZM
  ```

## File Structure

| File | Responsibility |
|---|---|
| `ui.py` *(modify)* | + `table`, `evidence_checkbox`, `merge_diff` |
| `jdsource.py` *(create)* | Resolve a path or URL to JD text; sanity-gate fetches; paste fallback |
| `install.py` *(modify)* | OpenClaw plugin gate, skippable backend step, wording, notes guidance, merge branch |
| `skills.py` *(create)* | Fill skill groups by hand; derive more with verbatim evidence |
| `merge.py` *(create)* | Match an incoming résumé against `content.json`, preserve notes |
| `tailor.py` *(modify)* | `parse_args(argv=None)` — one line, additive |
| `shell.py` *(create)* | REPL loop, command dispatch, session state |
| `batch.py` *(create)* | Score many JDs, pick, generate |
| `console.py` *(create)* | Bottom input region, full-screen option picker |
| `drafts.py` *(create)* | Evidence-only draft answers for gap questions |

Tasks 1–5 are independent of each other. Tasks 6 and 7 need Task 1. Task 9 needs Tasks 2 and 8. Tasks 11 and 12 come last: 11 replaces the input layer under every `ui` function, so the suites from 1–10 are what prove its fallback path did not regress.

---

## Task 1: `ui.py` widgets

**Files:**
- Modify: `ui.py` (append to the `-- output --` section, after `gap_table`)
- Test: `test_ui.py`

**Interfaces:**
- Consumes: existing `ui.interactive`, `ui._console`, `ui._Table`, `ui._q`, `ui.checkbox`
- Produces:
  - `table(headers: list, rows: list) -> None`
  - `evidence_checkbox(msg: str, candidates: list, *, precheck=None) -> list` — `candidates` are dicts with keys `skill`, `evidence`, `source_ref`, `strength`; returns the accepted subset. `precheck` defaults to `lambda c: c.get("strength") == "direct"`, per spec §4: `direct` candidates arrive pre-checked, `implied` do not.
  - `merge_diff(plan: dict) -> None` — `plan` has keys `matched` (list of dicts with `existing_ref`, `new_bullets`, `conflicts`) and `added` (list of dicts with `_ref`)

- [ ] **Step 1: Write the failing tests**

Append to `test_ui.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest test_ui.py -k "table or evidence or merge_diff" -v`
Expected: FAIL — `AttributeError: module 'ui' has no attribute 'table'`

- [ ] **Step 3: Implement the widgets**

Append to `ui.py`:

```python
def table(headers: list, rows: list) -> None:
    """A plain grid. Extracted so the rich-or-plain branch is not written a third time."""
    if _console is not None and interactive():
        t = _Table(show_header=True, box=None)
        for h in headers:
            t.add_column(str(h))
        for r in rows:
            t.add_row(*[str(c) for c in r])
        _console.print(t)
        return
    widths = [max(len(str(h)), *(len(str(r[i])) for r in rows)) if rows else len(str(h))
              for i, h in enumerate(headers)]
    print("  ".join(str(h).ljust(w) for h, w in zip(headers, widths)))
    for r in rows:
        print("  ".join(str(c).ljust(w) for c, w in zip(r, widths)))


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
    by_label = dict(zip(labels, candidates))
    if _use_widgets():
        Choice = getattr(_q, "Choice", None)
        if Choice is not None:
            opts = [Choice(lbl, checked=precheck(c)) for lbl, c in by_label.items()]
            picked = list(_q.checkbox(msg, choices=opts).ask() or [])
            return [by_label[p] for p in picked if p in by_label]
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
    if _console is not None and interactive():
        _console.print(_Panel(body, title="Merge plan"))
    else:
        print(body)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest test_ui.py -v`
Expected: PASS, all tests including the pre-existing ones.

- [ ] **Step 5: Commit**

```bash
git add ui.py test_ui.py
git commit -m "feat: ui — table, evidence_checkbox, merge_diff widgets"
```

---

## Task 2: `jdsource.py` — path or URL

**Files:**
- Create: `jdsource.py`
- Modify: `requirements.txt`
- Test: `test_jdsource.py`

**Interfaces:**
- Consumes: `ui.editor`, `ui.spinner`
- Produces:
  - `load_jd(arg: str) -> tuple` — returns `(text, label)`; raises `JDError` when no text could be obtained
  - `is_url(arg: str) -> bool`
  - `looks_like_jd(text: str) -> str` — returns `""` if the text passes, else the reason it failed
  - `read_url_list(path) -> list` — lines of a file that are all URLs, else `[]`
  - `class JDError(Exception)`

- [ ] **Step 1: Write the failing tests**

Create `test_jdsource.py`:

```python
"""jdsource.py: a JD arrives as a local file or a URL. No test touches the network."""
import pytest

import jdsource

GOOD_JD = (
    "Senior Product Designer at Acme. Responsibilities: lead design for the core "
    "product. Qualifications: 5+ years of experience shipping design systems. "
    "You'll work with engineers to define requirements and ship." + " padding." * 40
)


def test_local_file_is_read_unchanged(tmp_path):
    p = tmp_path / "jd.txt"
    p.write_text(GOOD_JD)
    text, label = jdsource.load_jd(str(p))
    assert text == GOOD_JD
    assert label == "jd.txt"


def test_missing_local_file_raises(tmp_path):
    with pytest.raises(jdsource.JDError):
        jdsource.load_jd(str(tmp_path / "nope.txt"))


def test_is_url():
    assert jdsource.is_url("https://boards.greenhouse.io/acme/jobs/1")
    assert jdsource.is_url("http://example.com")
    assert not jdsource.is_url("/Users/me/jd.txt")
    assert not jdsource.is_url("jd.txt")


def test_looks_like_jd_accepts_a_real_posting():
    assert jdsource.looks_like_jd(GOOD_JD) == ""


def test_looks_like_jd_rejects_short_text():
    reason = jdsource.looks_like_jd("Sign in to view this job")
    assert "short" in reason.lower()


def test_looks_like_jd_rejects_text_without_jd_keywords():
    reason = jdsource.looks_like_jd("lorem ipsum dolor sit amet " * 40)
    assert "keyword" in reason.lower()


class _Resp:
    def __init__(self, text, status=200):
        self.text, self.status_code = text, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def test_url_happy_path(monkeypatch):
    html = f"<html><body><nav>menu</nav><main><p>{GOOD_JD}</p></main></body></html>"
    monkeypatch.setattr(jdsource.requests, "get", lambda *a, **k: _Resp(html))
    text, label = jdsource.load_jd("https://boards.greenhouse.io/acme/jobs/1")
    assert "Senior Product Designer" in text
    assert "menu" not in text          # nav is stripped
    assert "greenhouse.io" in label


def test_login_wall_falls_back_to_paste(monkeypatch, capsys):
    monkeypatch.setattr(jdsource.requests, "get",
                        lambda *a, **k: _Resp("<html><body>Sign in</body></html>"))
    monkeypatch.setattr(jdsource.ui, "editor", lambda *a, **k: GOOD_JD)
    text, _ = jdsource.load_jd("https://linkedin.com/jobs/view/1")
    assert text == GOOD_JD
    assert "short" in capsys.readouterr().out.lower()


def test_fetch_error_falls_back_to_paste(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("connection refused")
    monkeypatch.setattr(jdsource.requests, "get", boom)
    monkeypatch.setattr(jdsource.ui, "editor", lambda *a, **k: GOOD_JD)
    text, _ = jdsource.load_jd("https://example.com/job")
    assert text == GOOD_JD


def test_declined_paste_raises(monkeypatch):
    monkeypatch.setattr(jdsource.requests, "get", lambda *a, **k: _Resp("nope"))
    monkeypatch.setattr(jdsource.ui, "editor", lambda *a, **k: "")
    with pytest.raises(jdsource.JDError):
        jdsource.load_jd("https://example.com/job")


def test_read_url_list(tmp_path):
    p = tmp_path / "urls.txt"
    p.write_text("https://a.com/1\n\nhttps://b.com/2\n")
    assert jdsource.read_url_list(p) == ["https://a.com/1", "https://b.com/2"]

    q = tmp_path / "jd.txt"
    q.write_text(GOOD_JD)
    assert jdsource.read_url_list(q) == []     # a real JD is not a URL list
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest test_jdsource.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'jdsource'`

- [ ] **Step 3: Add the dependencies**

Append to `requirements.txt`:

```
requests>=2.31
beautifulsoup4>=4.12
```

Run: `python3 -m pip install requests beautifulsoup4`

- [ ] **Step 4: Implement `jdsource.py`**

Create `jdsource.py`:

```python
"""Resolve a job description from a local path or a URL.

A posting starts life as a URL, but the boards worth applying to differ wildly:
Greenhouse, Lever and Ashby serve static HTML, while LinkedIn and Workday render
in JS behind a login. Rather than guess, this module fetches what it can, checks
the result actually looks like a posting, and asks for a paste when it does not.
Handing a login wall to the generator would produce a confidently wrong resume,
which is worse than one more prompt.
"""
from __future__ import annotations

import re
from pathlib import Path

import requests
from bs4 import BeautifulSoup

import ui

MIN_CHARS = 300
_JD_KEYWORDS = re.compile(r"responsibilit|qualificat|requirement|you.ll|experience", re.I)
_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/122.0 Safari/537.36")
_STRIP_TAGS = ("script", "style", "nav", "header", "footer", "aside", "form")
TIMEOUT = 15.0


class JDError(Exception):
    """No usable job description could be obtained."""


def is_url(arg: str) -> bool:
    return str(arg).strip().lower().startswith(("http://", "https://"))


def looks_like_jd(text: str) -> str:
    """"" if the text passes as a posting, else the reason it failed."""
    text = (text or "").strip()
    if len(text) < MIN_CHARS:
        return f"too short ({len(text)} chars, need {MIN_CHARS})"
    if not _JD_KEYWORDS.search(text):
        return "no job-description keywords (responsibilities, qualifications, requirements)"
    return ""


def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(list(_STRIP_TAGS)):
        tag.decompose()
    body = soup.find("main") or soup.find("article") or soup.body or soup
    text = body.get_text("\n")
    lines = [ln.strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def fetch(url: str) -> str:
    with ui.spinner(f"Fetching {url}"):
        resp = requests.get(url, headers={"User-Agent": _UA}, timeout=TIMEOUT)
        resp.raise_for_status()
    return html_to_text(resp.text)


def _paste(reason: str) -> str:
    print(f"  ! {reason}")
    return (ui.editor("Paste the job description text instead (Enter to skip):") or "").strip()


def load_jd(arg: str) -> tuple:
    """(text, label) for a local path or a URL. Raises JDError when nothing usable."""
    arg = str(arg).strip()
    if not is_url(arg):
        path = Path(arg).expanduser()
        if not path.exists():
            raise JDError(f"file not found: {path}")
        return path.read_text(), path.name

    try:
        text = fetch(arg)
    except Exception as e:                       # any transport or parse failure
        text = ""
        reason = f"could not fetch ({str(e)[:120]})"
    else:
        reason = looks_like_jd(text)

    if reason:
        text = _paste(reason)
        # looks_like_jd returns "" on PASS, so a truthy result is the failure.
        if looks_like_jd(text):
            raise JDError(f"no usable job description for {arg}")
    label = re.sub(r"^https?://(www\.)?", "", arg)[:60]
    return text, label


def read_url_list(path) -> list:
    """The lines of a file, if every non-blank one is a URL. Else []."""
    try:
        lines = [ln.strip() for ln in Path(path).read_text().splitlines()]
    except OSError:
        return []
    lines = [ln for ln in lines if ln]
    if lines and all(is_url(ln) for ln in lines):
        return lines
    return []
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m pytest test_jdsource.py -v`
Expected: PASS (11 tests)

- [ ] **Step 6: Commit**

```bash
git add jdsource.py test_jdsource.py requirements.txt
git commit -m "feat: jdsource — accept a JD as a URL, with a sanity gate and paste fallback"
```

---

## Task 3: OpenClaw plugin gate

**Files:**
- Modify: `install.py` — add `openclaw_plugins`, `ensure_plugin`; call from `connect_backend` (install.py:97-128)
- Test: `test_install.py`

**Interfaces:**
- Consumes: `ui.confirm`, `ui.spinner`, `llm.DEFAULT_BINARY`
- Produces:
  - `openclaw_plugins(*, run=None) -> list` — installed provider names, `[]` when unknown
  - `ensure_plugin(provider: str, *, run=None) -> bool` — True when the provider is usable

- [ ] **Step 1: Write the failing tests**

Append to `test_install.py`:

```python
def _cp(argv, code=0, out=""):
    return subprocess.CompletedProcess(argv, code, stdout=out, stderr="")


def test_openclaw_plugins_parses_names():
    out = "Installed plugins:\n  openai  v1.2.0\n  anthropic  v0.9.1\n"
    got = install.openclaw_plugins(run=lambda *a, **k: _cp(a[0], 0, out))
    assert "openai" in got and "anthropic" in got


def test_openclaw_plugins_unknown_on_failure():
    assert install.openclaw_plugins(run=lambda *a, **k: _cp(a[0], 1, "")) == []


def test_ensure_plugin_installs_a_missing_provider(monkeypatch):
    # The exact first-run failure: picking `claude` with no provider plugin installed
    # printed "No provider plugins found" and left setup stuck.
    monkeypatch.setattr(install, "openclaw_plugins", lambda **k: ["openai"])
    monkeypatch.setattr(install.ui, "confirm", lambda *a, **k: True)
    calls = []
    ok = install.ensure_plugin("claude", run=lambda argv, **k: calls.append(argv) or _cp(argv, 0))
    assert ok is True
    assert ["openclaw", "plugins", "install", "claude"] in calls


def test_ensure_plugin_skips_when_already_installed(monkeypatch):
    monkeypatch.setattr(install, "openclaw_plugins", lambda **k: ["claude"])
    calls = []
    assert install.ensure_plugin("claude", run=lambda argv, **k: calls.append(argv) or _cp(argv, 0))
    assert calls == []


def test_ensure_plugin_never_fatal_when_install_fails(monkeypatch, capsys):
    monkeypatch.setattr(install, "openclaw_plugins", lambda **k: ["openai"])
    monkeypatch.setattr(install.ui, "confirm", lambda *a, **k: True)
    ok = install.ensure_plugin("claude", run=lambda argv, **k: _cp(argv, 1))
    assert ok is False
    assert "plugins install claude" in capsys.readouterr().out


def test_ensure_plugin_proceeds_when_plugin_list_unknown(monkeypatch):
    # An unparseable `plugins list` must not block a working setup.
    monkeypatch.setattr(install, "openclaw_plugins", lambda **k: [])
    assert install.ensure_plugin("claude", run=lambda argv, **k: _cp(argv, 0)) is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest test_install.py -k "plugin" -v`
Expected: FAIL — `AttributeError: module 'install' has no attribute 'openclaw_plugins'`

- [ ] **Step 3: Implement**

Add to `install.py`, above `connect_backend`:

```python
def openclaw_plugins(*, run=None) -> list:
    """Provider plugin names OpenClaw reports as installed. [] means "could not tell"."""
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
        tok = line.strip().split()
        # "  openai  v1.2.0" -> openai ; skip headings like "Installed plugins:"
        if tok and not line.startswith(("Installed", "No ")) and line[:1].isspace():
            names.append(tok[0].strip(":"))
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
```

Then wire it into `connect_backend`. Replace install.py:107-109:

```python
    provider = ""
    if backend == "openclaw":
        provider = ui.select("Which provider should OpenClaw connect to?", ["openai", "claude"])
        ensure_plugin(provider, run=run)
    argv = auth_argv(backend, provider)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest test_install.py -v`
Expected: PASS, including the pre-existing backend tests.

- [ ] **Step 5: Commit**

```bash
git add install.py test_install.py
git commit -m "fix: install — detect and offer to install a missing OpenClaw provider plugin"
```

---

## Task 4: Skippable backend step and clearer wording

**Files:**
- Modify: `install.py:410-416` (backend block in `main`), `install.py:297-304` (solo hint)
- Test: `test_install.py`

**Interfaces:**
- Consumes: existing `_step`, `_configured`
- Produces: no new public functions

- [ ] **Step 1: Write the failing tests**

Append to `test_install.py`:

```python
def test_solo_question_wording_is_plain():
    import inspect
    src = inspect.getsource(install.preferences_walkthrough)
    # "end-to-end solo, with no handoffs" read as jargon on a first run.
    assert "no handoffs" not in src
    assert "research, design and build yourself" in src


def test_configured_reports_backend_when_binary_present(tmp_path, monkeypatch):
    (tmp_path / "constant.json").write_text('{"backend": "openclaw"}')
    (tmp_path / "content.json").write_text('{"experience": []}')
    monkeypatch.setattr(install.shutil, "which", lambda n: "/bin/" + n)
    state = install._configured(tmp_path)
    assert state["backend"] is True
    assert state["current_backend"] == "openclaw"


def test_configured_reports_backend_missing_when_binary_gone(tmp_path, monkeypatch):
    (tmp_path / "constant.json").write_text('{"backend": "openclaw"}')
    (tmp_path / "content.json").write_text("{}")
    monkeypatch.setattr(install.shutil, "which", lambda n: None)
    assert install._configured(tmp_path)["backend"] is False


def test_step_skips_a_configured_step_by_default(monkeypatch):
    monkeypatch.setattr(install.ui, "interactive", lambda: True)
    seen = {}
    monkeypatch.setattr(install.ui, "confirm",
                        lambda msg, default=False: seen.setdefault("default", default))
    install._step("preferences", lambda: None, done=True)
    assert seen["default"] is False        # already set up -> default is "don't redo"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest test_install.py -k "solo_question or configured or step_skips" -v`
Expected: FAIL on `test_solo_question_wording_is_plain` — `"no handoffs" not in src` is False.

- [ ] **Step 3: Reword the solo question**

In `install.py`, replace the `ui.hint` and `write_constant_key` at install.py:297-304:

```python
    ui.hint("Solo work: you span the whole job yourself — research, design, build —\n"
            "rather than handing a stage off to someone else. Saying yes adds a prompt\n"
            "rule and a lint that strip invented teammates (\"partnered with the data\n"
            "team\"), which models add on their own because job ads are written in the\n"
            "plural. Say no if you genuinely work with a team; the lint would then flag\n"
            "every true sentence you have about them.")
    write_constant_key(root, "solo_worker", ui.confirm(
        "Is your work solo — you do the research, design and build yourself?",
        default=False))
```

- [ ] **Step 4: Route the backend step through `_step`**

In `install.py`, replace the backend block at install.py:410-416:

```python
    backend = state["current_backend"]

    def _do_backend():
        nonlocal backend
        backend = setup_backend(preferred=backend)
        write_constant_key(ROOT, "backend", backend)

    if state["backend"]:
        print(f"\nModel CLI : {backend} (configured)")
    _step(f"the model CLI (currently {backend})" if backend else "the model CLI",
          _do_backend, done=state["backend"])
    if not backend:
        # Nothing configured and the step was declined: later steps need a backend.
        backend = llm.detect_backend()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m pytest test_install.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add install.py test_install.py
git commit -m "fix: install — skippable backend step, plainer solo-work question"
```

---

## Task 5: Per-entry guidance in the notes walkthrough

**Files:**
- Modify: `install.py` — add `EntryPrompts`, `entry_prompts`, `STATIC_PROMPTS`; use in `notes_walkthrough` (install.py:318-339)
- Test: `test_install.py`

**Interfaces:**
- Consumes: `llm.complete_schema`, `ui.spinner`
- Produces:
  - `STATIC_PROMPTS: list` — the fallback checklist
  - `entry_prompts(targets: list, *, backend, model) -> dict` — maps `"kind:label"` to a list of questions; never raises

- [ ] **Step 1: Write the failing tests**

Append to `test_install.py`:

```python
def test_entry_prompts_maps_questions_by_ref(monkeypatch):
    payload = {"entries": [
        {"ref": "project:Transit Fare", "questions": ["How many daily users?", "What constraint?"]},
        {"ref": "experience:Acme", "questions": ["What was the team size?"]},
    ]}
    monkeypatch.setattr(install.llm, "complete_schema", lambda **k: (payload, None))
    targets = [("project", {}, "Transit Fare"), ("experience", {}, "Acme")]
    got = install.entry_prompts(targets, backend="openclaw", model="m")
    assert got["project:Transit Fare"][0] == "How many daily users?"
    assert got["experience:Acme"] == ["What was the team size?"]


def test_entry_prompts_falls_back_to_static_on_failure(monkeypatch):
    def boom(**k):
        raise install.llm.LLMError("no model")
    monkeypatch.setattr(install.llm, "complete_schema", boom)
    got = install.entry_prompts([("project", {}, "Transit Fare")], backend="openclaw", model="m")
    # A failed call must not block the walkthrough -- generic guidance beats none.
    assert got["project:Transit Fare"] == install.STATIC_PROMPTS


def test_notes_walkthrough_prints_guidance(monkeypatch, tmp_path, capsys):
    (tmp_path / "constant.json").write_text('{"name": "A"}')
    (tmp_path / "content.json").write_text(json.dumps(
        {"experience": [], "projects": [{"title": "Transit Fare", "bullets": ["Cut steps 7 to 4"]}]}))
    monkeypatch.setattr(install, "entry_prompts",
                        lambda *a, **k: {"project:Transit Fare": ["How many daily users?"]})
    monkeypatch.setattr(install.ui, "editor", lambda *a, **k: "")   # skip every entry
    install.notes_walkthrough(tmp_path, backend="openclaw", model="m")
    out = capsys.readouterr().out
    assert "Useful context here" in out
    assert "How many daily users?" in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest test_install.py -k "entry_prompts or guidance" -v`
Expected: FAIL — `AttributeError: module 'install' has no attribute 'entry_prompts'`

- [ ] **Step 3: Implement**

Add to `install.py`, above `notes_walkthrough`:

```python
STATIC_PROMPTS = [
    "What was the scale — users, revenue, requests, team size?",
    "What constraint shaped the solution?",
    "What changed as a result, and how do you know?",
]

PROMPTS_SYSTEM = (
    "For each resume entry below, write 2-3 short questions whose answers would make "
    "its bullets more specific and more credible. Ask about scale, constraints, and "
    "measured outcomes the bullets do not already state. Do not ask about anything "
    "already written. Do not invent facts. Use the entry's exact ref string. Return "
    "the schema only."
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
    payload = [{"ref": f"{kind}:{label}", "bullets": (entry.get("bullets") or [])}
               for kind, entry, label in targets]
    try:
        with ui.spinner(f"Working out what context would help for {len(refs)} entries"):
            data, _ = llm.complete_schema(
                backend=backend, model=model, system=PROMPTS_SYSTEM,
                user=json.dumps(payload, separators=(",", ":"), ensure_ascii=False)[:20000],
                schema=EntryPrompts, thinking="low")
    except Exception:
        return {ref: STATIC_PROMPTS for ref in refs}
    got = {e.get("ref"): [q for q in (e.get("questions") or []) if q]
           for e in data.get("entries", [])}
    return {ref: got.get(ref) or STATIC_PROMPTS for ref in refs}
```

Then in `notes_walkthrough`, after the `targets` filter and `ui.hint`, add the batched call and print the guidance. Replace the loop body header:

```python
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
```

The rest of the loop (the `if not raw.strip(): continue`, the distill call, `append_note`) is unchanged.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest test_install.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add install.py test_install.py
git commit -m "feat: install — suggest what context to add for each entry in the notes walkthrough"
```

---

## Task 6: `skills.py` — fill and derive

**Files:**
- Create: `skills.py`
- Test: `test_skills.py`

**Interfaces:**
- Consumes: `ui.evidence_checkbox` (Task 1), `ui.text`, `ui.confirm`, `ui.spinner`, `llm.complete_schema`, `install.write_constant_key`
- Produces:
  - `derive(content: dict, *, backend, model, rejected=()) -> list` — candidate dicts, evidence-verified
  - `corpus_text(content: dict) -> str` — every bullet and note, newline-joined
  - `apply(content: dict, accepted: list) -> dict` — returns a new content dict
  - `run(root, *, backend, model) -> None` — the interactive step

- [ ] **Step 1: Write the failing tests**

Create `test_skills.py`:

```python
"""skills.py: a derived skill must trace back to something the user actually wrote."""
import json

import skills

CONTENT = {
    "experience": [{"company": "Acme", "title": "Designer",
                    "bullets": ["built a 40-component library"], "notes": "tokenised the theme layer"}],
    "projects": [{"title": "Transit Fare", "bullets": ["cut checkout steps from 7 to 4"], "notes": ""}],
    "skills": [{"label": "Design", "entries": ["Figma"]}],
}


def _cand(skill, evidence, group="Design", strength="direct"):
    return {"skill": skill, "group": group, "evidence": evidence,
            "source_ref": "experience:Acme", "strength": strength}


def test_corpus_includes_bullets_and_notes():
    text = skills.corpus_text(CONTENT)
    assert "40-component library" in text
    assert "tokenised the theme layer" in text
    assert "cut checkout steps" in text


def test_candidate_without_verbatim_evidence_is_dropped(monkeypatch):
    # The load-bearing guard. A model can produce a plausible skill with invented
    # evidence; if the span is not in the corpus, the skill is not real.
    payload = {"candidates": [
        _cand("Design Systems", "built a 40-component library"),
        _cand("Machine Learning", "led the ML platform team"),   # never written
    ]}
    monkeypatch.setattr(skills.llm, "complete_schema", lambda **k: (payload, None))
    got = skills.derive(CONTENT, backend="openclaw", model="m")
    assert [c["skill"] for c in got] == ["Design Systems"]


def test_evidence_from_a_note_counts(monkeypatch):
    payload = {"candidates": [_cand("Design Tokens", "tokenised the theme layer")]}
    monkeypatch.setattr(skills.llm, "complete_schema", lambda **k: (payload, None))
    assert [c["skill"] for c in skills.derive(CONTENT, backend="openclaw", model="m")] == ["Design Tokens"]


def test_already_present_skill_is_filtered(monkeypatch):
    payload = {"candidates": [_cand("figma", "built a 40-component library")]}
    monkeypatch.setattr(skills.llm, "complete_schema", lambda **k: (payload, None))
    assert skills.derive(CONTENT, backend="openclaw", model="m") == []


def test_rejected_skill_is_not_offered_again(monkeypatch):
    payload = {"candidates": [_cand("Design Systems", "built a 40-component library")]}
    monkeypatch.setattr(skills.llm, "complete_schema", lambda **k: (payload, None))
    got = skills.derive(CONTENT, backend="openclaw", model="m", rejected=["design systems"])
    assert got == []


def test_derive_returns_empty_on_model_failure(monkeypatch):
    def boom(**k):
        raise skills.llm.LLMError("no model")
    monkeypatch.setattr(skills.llm, "complete_schema", boom)
    assert skills.derive(CONTENT, backend="openclaw", model="m") == []


def test_apply_adds_to_existing_group():
    out = skills.apply(CONTENT, [_cand("Design Systems", "x", group="Design")])
    design = [g for g in out["skills"] if g["label"] == "Design"][0]
    assert design["entries"] == ["Figma", "Design Systems"]


def test_apply_creates_a_new_group():
    out = skills.apply(CONTENT, [_cand("Python", "x", group="Engineering")])
    labels = [g["label"] for g in out["skills"]]
    assert "Engineering" in labels
    eng = [g for g in out["skills"] if g["label"] == "Engineering"][0]
    assert eng["entries"] == ["Python"]


def test_apply_does_not_mutate_the_input():
    before = json.dumps(CONTENT, sort_keys=True)
    skills.apply(CONTENT, [_cand("Python", "x", group="Engineering")])
    assert json.dumps(CONTENT, sort_keys=True) == before
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest test_skills.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'skills'`

- [ ] **Step 3: Implement `skills.py`**

Create `skills.py`:

```python
"""Fill in skill groups by hand, then derive more — each one backed by evidence.

The derivation is the risky half. A model asked for "skills implied by this resume"
will happily return ones the user cannot defend in an interview, so every candidate
must carry a verbatim span from the user's own bullets or notes, and that span is
checked mechanically here rather than trusted.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List

from pydantic import BaseModel

import llm
import ui

DERIVE_SYSTEM = (
    "List skills the candidate demonstrably has, based only on the entries below. "
    "For each one, copy into `evidence` the exact span of text from a bullet or note "
    "that demonstrates it — verbatim, character for character, not a paraphrase. "
    "Use `direct` when the text names the skill, `implied` when the work clearly "
    "required it. Do not list a skill you cannot quote evidence for. Return the "
    "schema only."
)


class SkillCandidate(BaseModel):
    skill: str
    group: str = ""
    evidence: str = ""
    source_ref: str = ""
    strength: str = "implied"


class DerivedSkills(BaseModel):
    candidates: List[SkillCandidate] = []


def corpus_text(content: dict) -> str:
    """Every bullet and note in the corpus, as one blob to check evidence against."""
    parts = []
    for key in ("experience", "projects"):
        for entry in content.get(key) or []:
            parts.extend(entry.get("bullets") or [])
            if entry.get("notes"):
                parts.append(entry["notes"])
    return "\n".join(parts)


def _existing(content: dict) -> set:
    return {e.strip().lower()
            for g in content.get("skills") or []
            for e in (g.get("entries") or [])}


def _payload(content: dict) -> str:
    entries = []
    for kind, key, match in (("experience", "experience", "company"),
                             ("project", "projects", "title")):
        for entry in content.get(key) or []:
            entries.append({"ref": f"{kind}:{entry.get(match, '')}",
                            "bullets": entry.get("bullets") or [],
                            "notes": entry.get("notes") or ""})
    groups = [g.get("label", "") for g in content.get("skills") or []]
    return json.dumps({"entries": entries, "existing_groups": groups},
                      separators=(",", ":"), ensure_ascii=False)


def derive(content: dict, *, backend: str, model: str, rejected=()) -> list:
    """Evidence-verified skill candidates not already present. Never raises."""
    try:
        with ui.spinner("Deriving skills from your experience"):
            data, _ = llm.complete_schema(
                backend=backend, model=model, system=DERIVE_SYSTEM,
                user=_payload(content)[:20000], schema=DerivedSkills, thinking="low")
    except Exception:
        return []

    corpus = corpus_text(content)
    have = _existing(content) | {r.strip().lower() for r in rejected}
    out, seen = [], set()
    for c in data.get("candidates", []):
        name = (c.get("skill") or "").strip()
        evidence = (c.get("evidence") or "").strip()
        key = name.lower()
        if not name or not evidence or key in have or key in seen:
            continue
        # The guard: an evidence span the user never wrote means the skill is invented.
        if evidence not in corpus:
            continue
        seen.add(key)
        out.append({"skill": name, "group": (c.get("group") or "").strip(),
                    "evidence": evidence, "source_ref": c.get("source_ref") or "",
                    "strength": "direct" if c.get("strength") == "direct" else "implied"})
    return out


def apply(content: dict, accepted: list) -> dict:
    """A copy of `content` with the accepted skills added to their groups."""
    out = json.loads(json.dumps(content))
    groups = out.setdefault("skills", [])
    by_label = {g.get("label", ""): g for g in groups}
    for c in accepted:
        label = c.get("group") or "Skills"
        group = by_label.get(label)
        if group is None:
            group = {"label": label, "entries": []}
            groups.append(group)
            by_label[label] = group
        entries = group.setdefault("entries", [])
        if c["skill"].lower() not in {e.lower() for e in entries}:
            entries.append(c["skill"])
    return out


def _fill(content: dict) -> dict:
    """Edit the groups by hand. No model call."""
    out = json.loads(json.dumps(content))
    groups = out.setdefault("skills", [])
    for g in groups:
        current = ", ".join(g.get("entries") or [])
        raw = ui.text(f"{g.get('label', 'Skills')} (comma-separated)", default=current)
        g["entries"] = [e.strip() for e in raw.split(",") if e.strip()]
    while ui.confirm("Add another skill group?", default=not groups):
        label = ui.text("Group name (e.g. Research, Engineering)").strip()
        if not label:
            break
        raw = ui.text(f"{label} (comma-separated)")
        groups.append({"label": label,
                       "entries": [e.strip() for e in raw.split(",") if e.strip()]})
    out["skills"] = [g for g in groups if g.get("entries")]
    return out


def run(root: Path, *, backend: str, model: str) -> None:
    """The interactive step: fill, then derive, then write."""
    import install                      # write_constant_key; imported here to avoid a cycle

    cpath, path = root / "constant.json", root / "content.json"
    content = json.loads(path.read_text())
    constant = json.loads(cpath.read_text()) if cpath.exists() else {}

    if ui.confirm("Edit your skill groups by hand first?", default=True):
        content = _fill(content)

    rejected = constant.get("rejected_skills") or []
    candidates = derive(content, backend=backend, model=model, rejected=rejected)
    if not candidates:
        print("  No new skills to suggest.")
    else:
        accepted = ui.evidence_checkbox(
            "Derived from your experience — pick what's true:", candidates)
        content = apply(content, accepted)
        picked = {c["skill"].lower() for c in accepted}
        turned_down = [c["skill"] for c in candidates if c["skill"].lower() not in picked]
        if turned_down:
            install.write_constant_key(root, "rejected_skills", rejected + turned_down)
        print(f"  Added {len(accepted)} skill(s).")

    path.write_text(json.dumps(content, indent=2, ensure_ascii=False) + "\n")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest test_skills.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add skills.py test_skills.py
git commit -m "feat: skills — fill groups by hand and derive more with verbatim evidence"
```

---

## Task 7: `merge.py` — add a résumé to existing content

**Files:**
- Create: `merge.py`
- Modify: `install.py:375-393` (`bootstrap_step` gains a three-way choice)
- Test: `test_merge.py`

**Interfaces:**
- Consumes: `ui.merge_diff` (Task 1), `ui.select`, `ui.confirm`, `ui.spinner`, `llm.complete_schema`, `install.extract_resume_text`, `install.BootstrapExtract`
- Produces:
  - `plan(existing: dict, incoming: dict, *, backend, model) -> dict` — `{"matched": [...], "added": [...]}`
  - `apply(existing: dict, plan: dict) -> dict`
  - `backup(root) -> Path`
  - `run(root, resume_path, *, backend, model) -> bool` — True when applied

- [ ] **Step 1: Write the failing tests**

Create `test_merge.py`:

```python
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
    dest = merge.backup(tmp_path)
    assert dest.exists()
    assert json.loads(dest.read_text()) == EXISTING


def test_plan_never_sends_notes_to_the_model(monkeypatch):
    # Notes are copied through in Python; there is no upside to a model seeing them
    # and a real risk of it rewriting them.
    sent = {}

    def capture(**k):
        sent["user"] = k["user"]
        return ({"matched": [], "added": []}, None)

    monkeypatch.setattr(merge.llm, "complete_schema", capture)
    merge.plan(EXISTING, {"experience": []}, backend="openclaw", model="m")
    assert "40 components" not in sent["user"]
    assert "12M riders" not in sent["user"]


def test_plan_returns_empty_on_model_failure(monkeypatch):
    def boom(**k):
        raise merge.llm.LLMError("no model")
    monkeypatch.setattr(merge.llm, "complete_schema", boom)
    assert merge.plan(EXISTING, {"experience": []}, backend="openclaw", model="m") == {
        "matched": [], "added": []}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest test_merge.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'merge'`

- [ ] **Step 3: Implement `merge.py`**

Create `merge.py`:

```python
"""Add another resume or CV to an existing content.json without losing what is there.

content.json is the corpus every tailored application is built from, and its `notes`
fields are the part a model cannot regenerate — they came from the user answering
questions about their own work. So the merge is deliberately lopsided: the model may
propose which entries correspond and which bullets are new, but notes never leave
Python, conflicts are reported rather than resolved, and nothing is written until the
user has seen the plan.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import List

from pydantic import BaseModel

import llm
import ui

MERGE_SYSTEM = (
    "Match incoming resume entries against existing ones. Two entries match when they "
    "describe the same role at the same organisation, or the same project, even if the "
    "wording differs. For a match, list only the incoming bullets that state something "
    "the existing bullets do not, and note any conflicting dates or titles. Entries "
    "with no counterpart go in `added`. Do not invent entries or bullets. Return the "
    "schema only."
)


class MergeMatch(BaseModel):
    kind: str = "experience"
    existing_ref: str = ""
    incoming_ref: str = ""
    new_bullets: List[str] = []
    conflicts: List[str] = []


class MergePlan(BaseModel):
    matched: List[MergeMatch] = []
    added: List[int] = []          # indices into the incoming entry list


def entry_ref(kind: str, entry: dict) -> str:
    if kind == "experience":
        return f"{entry.get('company', '')} · {entry.get('title', '')}".strip(" ·")
    return str(entry.get("title", ""))


def _strip_notes(content: dict) -> dict:
    """The corpus as the model sees it: bullets only, never notes."""
    out = {}
    for kind, key in (("experience", "experience"), ("project", "projects")):
        out[key] = [{"ref": entry_ref(kind, e), "bullets": e.get("bullets") or []}
                    for e in content.get(key) or []]
    return out


def plan(existing: dict, incoming: dict, *, backend: str, model: str) -> dict:
    """{"matched": [...], "added": [...]} — added entries carry a `_ref` for display."""
    flat_incoming = []
    for kind, key in (("experience", "experience"), ("project", "projects")):
        for e in incoming.get(key) or []:
            flat_incoming.append({"kind": kind, "key": key, "entry": e,
                                  "ref": entry_ref(kind, e)})
    if not flat_incoming:
        return {"matched": [], "added": []}

    user = json.dumps({
        "existing": _strip_notes(existing),
        "incoming": [{"index": i, "kind": f["kind"], "ref": f["ref"],
                      "bullets": f["entry"].get("bullets") or []}
                     for i, f in enumerate(flat_incoming)],
    }, separators=(",", ":"), ensure_ascii=False)
    try:
        with ui.spinner("Matching the new resume against your existing entries"):
            data, _ = llm.complete_schema(backend=backend, model=model, system=MERGE_SYSTEM,
                                          user=user[:20000], schema=MergePlan, thinking="low")
    except Exception:
        return {"matched": [], "added": []}

    added = []
    for i in data.get("added", []):
        if isinstance(i, int) and 0 <= i < len(flat_incoming):
            f = flat_incoming[i]
            added.append({**json.loads(json.dumps(f["entry"])),
                          "kind": f["kind"], "_ref": f["ref"]})
    return {"matched": data.get("matched", []), "added": added}


def merge_skills(existing: dict, incoming: dict) -> list:
    """Skill groups combined, deduped case-insensitively, existing spelling wins."""
    out = json.loads(json.dumps(existing.get("skills") or []))
    by_label = {g.get("label", ""): g for g in out}
    for g in incoming.get("skills") or []:
        target = by_label.get(g.get("label", ""))
        if target is None:
            target = {"label": g.get("label", ""), "entries": []}
            out.append(target)
            by_label[target["label"]] = target
        have = {e.lower() for e in target.get("entries") or []}
        for e in g.get("entries") or []:
            if e.lower() not in have:
                target.setdefault("entries", []).append(e)
                have.add(e.lower())
    return out


def apply(existing: dict, plan_dict: dict) -> dict:
    """A copy of `existing` with the plan applied. Notes are copied through untouched."""
    out = json.loads(json.dumps(existing))
    index = {}
    for kind, key in (("experience", "experience"), ("project", "projects")):
        for e in out.get(key) or []:
            index[entry_ref(kind, e)] = e

    for m in plan_dict.get("matched", []):
        target = index.get(m.get("existing_ref", ""))
        if target is None:
            continue
        bullets = target.setdefault("bullets", [])
        have = {b.strip().lower() for b in bullets}
        for b in m.get("new_bullets") or []:
            if b.strip().lower() not in have:
                bullets.append(b)
                have.add(b.strip().lower())
        # notes and dates are left exactly as they were: conflicts are reported, not resolved

    for a in plan_dict.get("added", []):
        entry = {k: v for k, v in a.items() if k not in ("kind", "_ref")}
        entry.setdefault("notes", "")
        key = "experience" if a.get("kind") == "experience" else "projects"
        out.setdefault(key, []).append(entry)
    return out


def backup(root: Path) -> Path:
    dest = Path(root) / "content.json.bak"
    shutil.copyfile(Path(root) / "content.json", dest)
    return dest


def run(root: Path, resume_path, *, backend: str, model: str) -> bool:
    """Extract, plan, show, confirm, write. Returns whether anything was applied."""
    import install                      # extract_resume_text / BootstrapExtract

    root = Path(root)
    existing = json.loads((root / "content.json").read_text())
    text = install.extract_resume_text(Path(resume_path))
    with ui.spinner(f"Reading {Path(resume_path).name}"):
        incoming, _ = llm.complete_schema(
            backend=backend, model=model, system=install.BOOTSTRAP_SYSTEM,
            user=text[:20000], schema=install.BootstrapExtract, thinking="low")

    p = plan(existing, incoming, backend=backend, model=model)
    ui.merge_diff(p)
    if not p["matched"] and not p["added"]:
        return False
    if not ui.confirm("Apply this merge?", default=True):
        print("  Nothing written.")
        return False

    dest = backup(root)
    merged = apply(existing, p)
    merged["skills"] = merge_skills(existing, incoming)
    have_edu = {json.dumps(e, sort_keys=True) for e in merged.get("education") or []}
    for e in incoming.get("education") or []:
        if json.dumps(e, sort_keys=True) not in have_edu:
            merged.setdefault("education", []).append(e)
    (root / "content.json").write_text(json.dumps(merged, indent=2, ensure_ascii=False) + "\n")
    print(f"  Merged. Previous content.json saved to {dest.name}.")
    return True
```

- [ ] **Step 4: Wire the three-way choice into `bootstrap_step`**

In `install.py`, replace `bootstrap_step` (install.py:375-393):

```python
def bootstrap_step(root: Path, *, backend: str, done: bool) -> None:
    """Build content.json from a resume, or add another resume to what is there.

    Defaults to bootstrapping on a first run (there is nothing to lose). Once content
    exists, replacing it discards the notes that make tailoring good, so adding is
    offered first and replacing is spelled out as destructive.
    """
    import merge

    _, model = llm.resolve(backend=backend)
    if not done:
        if not ui.confirm("Bootstrap content.json from an existing resume file?", default=True):
            return
        resume = ui.path("Path to your resume (.pdf / .docx / .txt / .md)", must_exist=True)
        if not resume:
            print("  Skipped — no file given.")
            return
        with ui.spinner(f"Reading {resume.name} and extracting your experience"):
            bootstrap_from_resume(resume, root, backend=backend, model=model)
        print(f"  Wrote content.json and constant.json from {resume.name}.")
        return

    content = _load_content(root)
    counts = (len(content.get("experience") or []), len(content.get("projects") or []))
    print(f"\ncontent.json already has {counts[0]} experience and {counts[1]} project entries.")
    choice = ui.select("What would you like to do?", [
        "Add another resume/CV to it",
        "Replace it entirely (loses your notes)",
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
    if ui.confirm(f"Replace content.json from {resume.name}? This cannot be undone.",
                  default=False):
        merge.backup(root)
        with ui.spinner(f"Reading {resume.name} and extracting your experience"):
            bootstrap_from_resume(resume, root, backend=backend, model=model)
        print(f"  Rebuilt content.json from {resume.name} (previous saved to content.json.bak).")
```

- [ ] **Step 5: Run the full suite**

Run: `python3 -m pytest test_merge.py test_install.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add merge.py test_merge.py install.py
git commit -m "feat: merge — add a resume to existing content.json, preserving notes"
```

---

## Task 8: `shell.py` — the REPL

**Files:**
- Create: `shell.py`
- Modify: `tailor.py:302` (`def parse_args(argv=None)`) and `tailor.py:411` (`parser.parse_args(argv)`)
- Test: `test_shell.py`

**Interfaces:**
- Consumes: `jdsource.load_jd` (Task 2), `skills.run` (Task 6), `tailor.parse_args`, `tailor.run`, `install.main`, `recompile_menu`, `Profile.load`
- Produces:
  - `class Session` — `root`, `backend`, `model`, `profile`, `output_dir`
  - `dispatch(session, line: str) -> bool` — False when the session should end
  - `cmd_jd(session, args: list) -> None`
  - `jd_to_path(arg: str) -> Path` — materialises a URL to a temp file
  - `main() -> None`

- [ ] **Step 1: Make `parse_args` accept an argv list**

In `tailor.py`, change line 302 and the final line of the function:

```python
def parse_args(argv=None) -> argparse.Namespace:
```

```python
    return parser.parse_args(argv)
```

- [ ] **Step 2: Write the failing tests**

Create `test_shell.py`:

```python
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


def test_bare_url_is_treated_as_jd(session, monkeypatch):
    seen = []
    monkeypatch.setattr(shell, "cmd_jd", lambda s, a: seen.append(a))
    shell.dispatch(session, "https://boards.greenhouse.io/acme/jobs/1")
    assert seen == [["https://boards.greenhouse.io/acme/jobs/1"]]


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


def test_cmd_jd_with_no_argument_reports(session, capsys):
    shell.cmd_jd(session, [])
    assert "usage" in capsys.readouterr().out.lower()
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python3 -m pytest test_shell.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'shell'`

- [ ] **Step 4: Implement `shell.py`**

Create `shell.py`:

```python
#!/usr/bin/env python3
"""A persistent Resume-Tex session.

    python3 shell.py

One prompt loop rather than a script per application: content.json is loaded once
and stays warm, so the second job description of an evening costs a model call
rather than a cold start. Scrollback is the terminal's own -- no alt-screen, no
custom scroll buffer -- so search, selection and copy all keep working.

Nothing here reimplements the pipeline. Every command builds a real argv list and
hands it to tailor.parse_args, which means the shell inherits each flag default
instead of a hand-built Namespace that drifts as flags are added.
"""
from __future__ import annotations

import glob
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

if __name__ == "__main__":
    print("Resume-Tex — loading...", flush=True)

import install
import jdsource
import llm
import skills as skills_mod
import tailor
import ui
from profile import Profile

BANNER = "Resume-Tex"


@dataclass
class Session:
    root: Path
    backend: str
    model: str
    profile: Optional[Profile] = None
    output_dir: Optional[Path] = None

    def reload(self) -> None:
        """Pick up a content.json that a command has just rewritten."""
        try:
            self.profile = Profile.load(self.root)
        except Exception as e:
            print(f"  Could not reload content.json: {str(e)[:200]}")


def jd_to_path(arg: str) -> Path:
    """A local path for the JD, fetching and materialising a URL when needed."""
    text, label = jdsource.load_jd(arg)
    if not jdsource.is_url(arg):
        return Path(arg).expanduser()
    tmp = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8")
    tmp.write(text)
    tmp.close()
    print(f"  Fetched {label} ({len(text.split())} words)")
    return Path(tmp.name)


def cmd_jd(session: Session, args: list) -> None:
    if not args:
        print("  Usage: /jd <path|url> [tailor flags, e.g. --cv -c]")
        return
    path = jd_to_path(args[0])
    argv = list(args[1:])
    if session.output_dir:
        argv += ["-o", str(session.output_dir)]
    argv.append(str(path))
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


def cmd_setup(session: Session, args: list) -> None:
    install.main()
    session.reload()


def cmd_recompile(session: Session, args: list) -> None:
    """Re-render a saved application from its edited JSON.

    Calls the library functions directly. recompile.main() parses sys.argv, which in
    a REPL is the shell's own argv, not the user's command.
    """
    import recompile
    import recompile_menu

    flags = {a for a in args if a.startswith("-")}
    positional = [a for a in args if not a.startswith("-")]
    app_dir = Path(positional[0]).expanduser().resolve() if positional else Path.cwd()
    mode = "cv" if "--cv" in flags else "resume"
    if flags & {"-i", "--interactive"}:
        recompile_menu.run_menu(app_dir, root=session.root, mode=mode)
    print(f"  Recompiled: {recompile.recompile_folder(app_dir, root=session.root, mode=mode)}")


def cmd_status(session: Session, args: list) -> None:
    content = session.profile.content if session.profile else {}
    ui.table(["what", "count"], [
        ["experience", len(content.get("experience") or [])],
        ["projects", len(content.get("projects") or [])],
        ["skills", sum(len(g.get("entries") or []) for g in content.get("skills") or [])],
    ])
    print(f"  backend    : {session.backend} / {session.model}")
    print(f"  output dir : {session.output_dir or '(default)'}")


def cmd_help(session: Session, args: list) -> None:
    print("""
  /jd <path|url> [flags]   tailor a resume for one job description
  /batch <glob|dir|file>   score several, then generate the ones you pick
  /skills                  fill in your skills and derive more
  /add [path]              add another resume/CV to content.json
  /setup                   re-run setup
  /recompile               re-render a saved application
  /status                  what is loaded
  /help  /exit

  A bare path or URL is treated as /jd.
""")


def cmd_exit(session: Session, args: list) -> None:
    pass


COMMANDS = {
    "/jd": cmd_jd, "/skills": cmd_skills, "/add": cmd_add, "/setup": cmd_setup,
    "/recompile": cmd_recompile, "/status": cmd_status, "/help": cmd_help,
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
    if line in ("/exit", "/quit", "exit", "quit"):
        return False

    parts = line.split()
    if line.startswith("/"):
        name, args = parts[0], parts[1:]
    else:
        name, args = "/jd", parts        # a bare path or URL is the dominant case

    if name == "/batch":
        from batch import cmd_batch
        fn = cmd_batch
    else:
        fn = COMMANDS.get(name)
    if fn is None:
        print(f"  Unknown command {name}. /help lists them.")
        return True

    try:
        fn(session, args)
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
        import json
        content = json.loads(path.read_text())
    except Exception:
        return True
    return not (content.get("experience") or content.get("projects"))


def main() -> None:
    if _first_run(ROOT):
        print("No content.json yet — running setup first.\n")
        install.main()

    backend, model = llm.resolve()
    session = Session(root=ROOT, backend=backend, model=model)
    session.reload()

    print(f"\n{BANNER}  {ROOT}")
    cmd_status(session, [])
    print("\n/help for commands, /exit to leave.\n")

    while True:
        try:
            line = input("> ")
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not dispatch(session, line):
            break
    print("Bye.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m pytest test_shell.py -v`
Expected: PASS (14 tests). `test_shell.py` does not import `batch`, so the deferred import in `dispatch` is fine until Task 9.

- [ ] **Step 6: Verify the one-shot CLI still works**

Run: `python3 -m pytest -q`
Expected: PASS, the whole suite. This is the check that `parse_args(argv=None)` broke nothing.

- [ ] **Step 7: Commit**

```bash
git add shell.py test_shell.py tailor.py
git commit -m "feat: shell — a persistent session with command dispatch over the existing pipeline"
```

---

## Task 9: `/batch` — score all, ask once, generate

**Files:**
- Create: `batch.py`
- Test: `test_batch.py`

**Interfaces:**
- Consumes: `jdsource.load_jd`, `shell.jd_to_path`, `shell.Session`, `tailor.score_jd`, `tailor.parse_args`, `tailor.run`, `ui.table`, `ui.select`, `ui.confirm`, `llm.score_model`
- Produces:
  - `expand(args: list) -> list` — sources from globs, directories and URL-list files
  - `score_all(sources: list, *, session, workers=3) -> list` — dicts with `source`, `label`, `path`, `score`, `gaps`, `error`
  - `select_targets(scored: list) -> list`
  - `cmd_batch(session, args: list) -> None`

- [ ] **Step 1: Write the failing tests**

Create `test_batch.py`:

```python
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
    monkeypatch.setattr(batch.ui, "checkbox", lambda *a, **k: ["b  60/100"])
    assert [r["label"] for r in batch.select_targets(rows)] == ["b"]


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest test_batch.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'batch'`

- [ ] **Step 3: Implement `batch.py`**

Create `batch.py`:

```python
"""Score a pile of job descriptions, then generate the ones worth generating.

Ordered so every question a human has to answer happens before the slow unattended
work starts: score everything first (cheap model, in parallel), show a ranked table,
collect gap answers for the postings you pick, then generate without you. The
alternative -- a full flow per JD -- ties you to the terminal for the whole run.
"""
from __future__ import annotations

import glob
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import jdsource
import llm
import shell
import tailor
import ui

WORKERS = 3          # each call is a subprocess; 3 keeps provider rate limits comfortable
WEAK_FIT = 50
_JD_SUFFIXES = (".txt", ".md")


def expand(args: list) -> list:
    """Sources from globs, directories and URL-list files, in a stable order."""
    out = []
    for arg in args:
        if jdsource.is_url(arg):
            out.append(arg)
            continue
        p = Path(arg).expanduser()
        if p.is_dir():
            out.extend(sorted(str(f) for f in p.iterdir()
                              if f.suffix.lower() in _JD_SUFFIXES))
            continue
        if p.is_file():
            urls = jdsource.read_url_list(p)
            out.extend(urls if urls else [str(p)])
            continue
        out.extend(sorted(glob.glob(os.path.expanduser(arg))))
    return out


def _score_one(source: str, session) -> dict:
    row = {"source": source, "label": source, "path": None,
           "score": None, "gaps": [], "error": ""}
    try:
        path = shell.jd_to_path(source)
        row["path"] = path
        row["label"] = path.name if not jdsource.is_url(source) else source[:50]
        _, score_model = llm.score_model(session.backend, llm.DEFAULT_PRESET[session.backend], None)
        result = tailor.score_jd(session.profile, path.read_text(),
                                 backend=session.backend, model=score_model)
        row["score"] = result.get("overall_score")
        row["gaps"] = result.get("gap_questions") or []
    except Exception as e:
        row["error"] = f"{type(e).__name__}: {str(e)[:200]}"
    return row


def score_all(sources: list, *, session, workers: int = WORKERS) -> list:
    """Scored rows, best first. Failures are reported and dropped, never fatal.

    Results are collected per-future and printed only once every call is done, so
    parallel output never interleaves into nonsense.
    """
    rows = []
    with ui.spinner(f"Scoring {len(sources)} job descriptions"):
        with ThreadPoolExecutor(max_workers=workers) as pool:
            rows = list(pool.map(lambda s: _score_one(s, session), sources))

    good, bad = [], []
    for r in rows:
        (bad if r["error"] or r["score"] is None else good).append(r)
    for r in bad:
        print(f"  ! {r['label']}: {r['error'] or 'no score'}")
    good.sort(key=lambda r: r["score"], reverse=True)
    return good


def _row_label(r: dict) -> str:
    return f"{r['label']}  {r['score']}/100"


def select_targets(rows: list) -> list:
    if not rows:
        return []
    choice = ui.select("Generate for which?", ["all", "top 3", "pick", "none"])
    if choice == "all":
        return rows
    if choice == "top 3":
        return rows[:3]
    if choice == "none":
        return []
    labels = [_row_label(r) for r in rows]
    picked = set(ui.checkbox("Pick the ones to generate:", labels))
    return [r for r, lbl in zip(rows, labels) if lbl in picked]


def collect_answers(rows: list) -> dict:
    """{label: extra_context} — asked up front so generation can run unattended."""
    answers = {}
    if not any(r["gaps"] for r in rows):
        return answers
    if not ui.confirm("Answer gap questions now? (they lift the score most)", default=True):
        return answers
    for r in rows:
        if not r["gaps"]:
            continue
        print(f"\n{r['label']}  ({r['score']}/100)")
        ui.gap_table(r["gaps"])
        parts = []
        for i, g in enumerate(r["gaps"], 1):
            q = g.get("question", "") if isinstance(g, dict) else getattr(g, "question", "")
            ans = ui.text(f"  Q{i}: {q}")
            if ans.strip():
                parts.append(f"{q}\n{ans.strip()}")
        if parts:
            answers[r["label"]] = "\n\n".join(parts)
    return answers


def generate(rows: list, *, session, answers: dict) -> None:
    """Sequential on purpose: LaTeX is disk-bound and interleaved output is unreadable."""
    made, failed = [], []
    for i, r in enumerate(rows, 1):
        print(f"\n[{i}/{len(rows)}] {r['label']}")
        argv = ["-y"]
        if session.output_dir:
            argv += ["-o", str(session.output_dir)]
        if answers.get(r["label"]):
            argv += ["-x", answers[r["label"]]]
        argv.append(str(r["path"]))
        try:
            tailor.run(tailor.parse_args(argv))
            made.append(r["label"])
        except (Exception, SystemExit) as e:
            print(f"  ! {r['label']}: {type(e).__name__}: {str(e)[:200]}")
            failed.append(r["label"])
    print(f"\nBatch done — {len(made)} generated, {len(failed)} failed.")
    for lbl in failed:
        print(f"  ! {lbl}")


def cmd_batch(session, args: list) -> None:
    if not args:
        print("  Usage: /batch <glob|directory|urls.txt>")
        return
    sources = expand(args)
    if not sources:
        print("  Nothing matched.")
        return

    rows = score_all(sources, session=session)
    if not rows:
        print("  Nothing could be scored.")
        return

    ui.table(["job description", "score", "gaps"],
             [[r["label"], f"{r['score']}/100",
               f"{len(r['gaps'])}" + ("   ← weak fit" if r["score"] < WEAK_FIT else "")]
              for r in rows])

    targets = select_targets(rows)
    if not targets:
        print("  Nothing selected.")
        return
    generate(targets, session=session, answers=collect_answers(targets))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest test_batch.py -v`
Expected: PASS (10 tests)

- [ ] **Step 5: Run the whole suite**

Run: `python3 -m pytest -q`
Expected: PASS, every test in the project.

- [ ] **Step 6: Commit**

```bash
git add batch.py test_batch.py
git commit -m "feat: batch — score many JDs in parallel, then generate the ones you pick"
```

---

## Task 10: README and manual verification

**Run this task LAST, after Tasks 11 and 12.** It is numbered here because it
closes out the feature, but its manual check exercises the input region and
draft answers those tasks build.

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Document the shell**

Add a section after the setup instructions covering: `python3 shell.py` as the front door, the command table from `cmd_help`, that a bare path or URL is treated as `/jd`, that URLs fall back to paste on login-walled boards, and that the one-shot `python3 tailor.py jd.txt` still works unchanged.

- [ ] **Step 2: Manual check — do NOT skip**

Run against a scratch output directory, never `~/Documents/Applications`:

```bash
mkdir -p /tmp/rt-check
python3 shell.py
```

Then in the shell:

1. `/status` — shows the corpus counts and backend.
2. `/jd <a Greenhouse or Lever URL>` — fetches, prints a word count, generates.
3. `/jd <a LinkedIn URL>` — expected to report a login wall and open the paste editor.
4. `/skills` — derived skills each show evidence; reject one, re-run, confirm it is not offered again.
5. `/batch <a directory of 3 JDs>` — ranked table, pick `top 3`, answer gaps, generation runs unattended.
6. Ctrl-C during a generation — the command aborts, the prompt returns.
7. During a `/jd` gap question, confirm a draft appears with its citation for a
   question about an entry you have notes on, and that a question with no
   material shows `no material found for this` and an empty field.
8. Confirm the input region stays anchored at the bottom while a generation
   prints above it, and that mouse selection of earlier output still works.
9. `/exit`.

Confirm every PDF landed under `/tmp/rt-check` and nothing was written to `~/Documents/Applications`.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: document the Resume-Tex shell, batch runs and URL job descriptions"
```

---

## Task 13: Note targeting, education targets, and newline storage

**Run this task AFTER Task 9 and BEFORE Task 12** — Task 12's `drafts.py` reads
notes as source text, so it must read the fixed shape.

**Files:**
- Modify: `profile.py:295-315` (`append_note`), `tailor.py:597-605` (`_known_note_targets`), `tailor.py:677-686` (`collect_gap_answers`), `install.py` (`notes_walkthrough`)
- Create: `notes.py`
- Test: `test_notes.py`, `test_profile.py`

**Interfaces:**
- Consumes: `ui.select`, `ui.confirm`, `Profile.content`
- Produces:
  - `notes.entry_choices(profile) -> list` — `[(target, label)]` for every experience, project and education entry
  - `notes.resolve_target(profile, proposed: str) -> str` — a valid target, or `""` to skip
  - `Profile.append_note` gains `education:<institution>` and joins with `"\n"`

- [ ] **Step 1: Write the failing tests**

Create `test_notes.py`:

```python
"""notes.py: every note lands on an entry the user chose, or on the general bucket."""
import types

import notes

CONTENT = {
    "experience": [{"company": "Acme", "title": "Designer", "bullets": [], "notes": ""}],
    "projects": [{"title": "Transit Fare", "bullets": [], "notes": ""}],
    "education": [{"institution": "UW", "degree": "BFA"}],
}


def _profile():
    return types.SimpleNamespace(content=CONTENT)


def test_entry_choices_covers_all_three_kinds():
    targets = [t for t, _ in notes.entry_choices(_profile())]
    assert "experience:Acme" in targets
    assert "project:Transit Fare" in targets
    assert "education:UW" in targets          # education had no note target before


def test_known_specific_target_is_used_without_prompting(monkeypatch):
    monkeypatch.setattr(notes.ui, "select",
                        lambda *a, **k: pytest_fail_no_prompt())
    assert notes.resolve_target(_profile(), "project:Transit Fare") == "project:Transit Fare"


def pytest_fail_no_prompt():
    raise AssertionError("a known target must not prompt")


def test_general_target_prompts_for_a_choice(monkeypatch):
    monkeypatch.setattr(notes.ui, "select", lambda msg, choices: "project — Transit Fare")
    assert notes.resolve_target(_profile(), "general") == "project:Transit Fare"


def test_stale_target_prompts_for_a_choice(monkeypatch):
    # An entry renamed since the question was generated must not silently vanish.
    monkeypatch.setattr(notes.ui, "select", lambda msg, choices: "experience — Acme")
    assert notes.resolve_target(_profile(), "project:Deleted Thing") == "experience:Acme"


def test_choosing_general_returns_general(monkeypatch):
    monkeypatch.setattr(notes.ui, "select", lambda msg, choices: notes.GENERAL_LABEL)
    assert notes.resolve_target(_profile(), "") == "general"


def test_choosing_skip_returns_empty(monkeypatch):
    monkeypatch.setattr(notes.ui, "select", lambda msg, choices: notes.SKIP_LABEL)
    assert notes.resolve_target(_profile(), "") == ""


def test_non_interactive_falls_back_to_general_without_prompting(monkeypatch):
    monkeypatch.setattr(notes.ui, "interactive", lambda: False)
    monkeypatch.setattr(notes.ui, "select",
                        lambda *a, **k: pytest_fail_no_prompt())
    assert notes.resolve_target(_profile(), "general") == "general"
```

Append to `test_profile.py`:

```python
def test_append_note_joins_with_a_newline_not_a_space(tmp_path):
    # Space-joining welded successive notes into one unreadable paragraph, and
    # drafts.py reads notes as source text -- a blob lets a garbled claim pass
    # the verbatim-substring gate because the blob really does contain it.
    _write(tmp_path, {"experience": [{"company": "Acme", "bullets": [], "notes": "First note."}]})
    p = Profile.load(tmp_path)
    p.append_note("experience:Acme", "Second note.")
    assert p.content["experience"][0]["notes"] == "First note.\nSecond note."


def test_append_note_to_education(tmp_path):
    _write(tmp_path, {"education": [{"institution": "UW", "degree": "BFA"}]})
    p = Profile.load(tmp_path)
    p.append_note("education:UW", "Thesis on transit wayfinding.")
    assert p.content["education"][0]["notes"] == "Thesis on transit wayfinding."


def test_append_note_unknown_education_raises(tmp_path):
    _write(tmp_path, {"education": [{"institution": "UW", "degree": "BFA"}]})
    p = Profile.load(tmp_path)
    with pytest.raises(ValueError):
        p.append_note("education:Nowhere", "x")
```

Note: `test_profile.py` already has a `_write`-style helper and a `Profile`
import — reuse whatever it defines rather than adding a second one. If its
existing helper has a different name or signature, adapt these three tests to
it; do not rewrite the existing tests.

Also append to `test_flow.py`:

```python
def test_known_note_targets_includes_education(tmp_path):
    profile = tailor.Profile.from_dicts(
        {"name": "A"},
        {"experience": [], "projects": [], "education": [{"institution": "UW"}]})
    assert "education:UW" in tailor._known_note_targets(profile)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest test_notes.py test_profile.py test_flow.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'notes'`, and the three
`test_profile.py` additions fail on the space join / missing education support.

- [ ] **Step 3: Fix `Profile.append_note`**

In `profile.py`, replace `append_note` (profile.py:295-315):

```python
    _NOTE_KEYS = {"experience": ("experience", "company"),
                  "project": ("projects", "title"),
                  "education": ("education", "institution")}

    def append_note(self, target: str, text: str) -> None:
        """Attach one note to an entry, or to the general bucket.

        Notes join with a newline, not a space: successive notes used to weld into
        one paragraph that could be neither read nor edited apart, and drafts.py
        reads notes as source text, so a blob would let a garbled claim satisfy the
        verbatim-evidence check.
        """
        text = (text or "").strip()
        if not text:
            return
        if target == "general":
            existing = (self._content.get("notes") or "").strip()
            self._content["notes"] = (existing + "\n" + text).strip() if existing else text
        else:
            kind, _, name = target.partition(":")
            spec = self._NOTE_KEYS.get(kind)
            if not spec:
                raise ValueError(f"unknown note target {target!r}")
            key, match = spec
            for entry in self._content.get(key, []):
                if entry.get(match) == name:
                    existing = (entry.get("notes") or "").strip()
                    entry["notes"] = (existing + "\n" + text).strip() if existing else text
                    break
            else:
                raise ValueError(f"no {kind} entry named {name!r}")
        self._write()
```

- [ ] **Step 4: Add education to `_known_note_targets`**

In `tailor.py`, replace `_known_note_targets` (tailor.py:597-605):

```python
def _known_note_targets(profile: "Profile") -> set:
    t = {"general"}
    for e in profile.content.get("experience", []):
        if e.get("company"):
            t.add(f"experience:{e['company']}")
    for p in profile.content.get("projects", []):
        if p.get("title"):
            t.add(f"project:{p['title']}")
    for d in profile.content.get("education", []):
        if d.get("institution"):
            t.add(f"education:{d['institution']}")
    return t
```

- [ ] **Step 5: Create `notes.py`**

```python
"""Decide which entry a note belongs to.

A note is only worth collecting if it lands somewhere it will be found again.
Before this, an unrecognised target meant the note went to a catch-all bucket or
was dropped -- so the answer the user typed disappeared from the entry it was
actually about. When the target is not certain, the user picks.
"""
from __future__ import annotations

import ui

GENERAL_LABEL = "General notes (not about one entry)"
SKIP_LABEL = "Skip this note"

_KINDS = (("experience", "experience", "company"),
          ("project", "projects", "title"),
          ("education", "education", "institution"))


def entry_choices(profile) -> list:
    """[(target, label)] for every entry a note can attach to."""
    out = []
    for kind, key, match in _KINDS:
        for entry in profile.content.get(key) or []:
            name = entry.get(match)
            if name:
                out.append((f"{kind}:{name}", f"{kind} — {name}"))
    return out


def resolve_target(profile, proposed: str) -> str:
    """A valid note target. "" means skip.

    A target the model already got right is used without a prompt; anything else
    asks, rather than guessing on the user's behalf.
    """
    proposed = (proposed or "").strip()
    choices = entry_choices(profile)
    valid = {t for t, _ in choices}
    if proposed in valid:
        return proposed
    if not ui.interactive():
        return "general"
    labels = [lbl for _, lbl in choices] + [GENERAL_LABEL, SKIP_LABEL]
    picked = ui.select("Which entry is this note about?", labels)
    if picked == SKIP_LABEL:
        return ""
    if picked == GENERAL_LABEL:
        return "general"
    return next((t for t, lbl in choices if lbl == picked), "general")
```

- [ ] **Step 6: Use it at both writers**

In `tailor.py`, in `collect_gap_answers`, replace the `profile.append_note` line
(tailor.py:685):

```python
        target = notes.resolve_target(profile, g.get("target", ""))
        if not target:
            continue
        profile.append_note(target, note)
```

Add `import notes` to `tailor.py`.

In `install.py`, in `notes_walkthrough`, replace the `profile.append_note` line
(install.py:339):

```python
        if note:
            target = notes.resolve_target(profile, f"{kind}:{label}")
            if target:
                profile.append_note(target, note)
```

Add `import notes` to `install.py`.

- [ ] **Step 7: Widen the distill prompt's transcription latitude**

In `tailor.py`, replace `DISTILL_SYSTEM_PROMPT`:

```python
DISTILL_SYSTEM_PROMPT = (
    "Rewrite the applicant's answer as a resume source note. Tighten the wording: "
    "make it concise, clear and specific, and fix grammar. Constraints: at most 2 "
    "sentences; factual; keep concrete numbers exactly as given; no first person; no "
    "marketing words (passionate, robust, seamless, spearheaded). Never add a fact, "
    "number, scope, outcome or claim the applicant did not state -- rephrasing is "
    "allowed, inventing is not. Output the note text only, nothing else."
)
```

- [ ] **Step 8: Run the whole suite**

Run: `python3 -m pytest -q`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add notes.py test_notes.py profile.py tailor.py install.py test_profile.py test_flow.py
git commit -m "feat: notes — attach to experience, project or education; ask when unsure"
```

---

## Task 11: `console.py` — input region and full-screen picker

**Files:**
- Create: `console.py`
- Modify: `ui.py` (`select`, `checkbox`, `evidence_checkbox` route through `console.pick`), `shell.py` (`main` uses `console.prompt`)
- Test: `test_console.py`

**Interfaces:**
- Consumes: `prompt_toolkit` (already present transitively via `questionary`)
- Produces:
  - `available() -> bool`
  - `prompt(message: str, *, status: str = "", history_file=None) -> str`
  - `pick(msg: str, options: list, *, multi=False, detail=None, precheck=None) -> object`

- [ ] **Step 1: Write the failing tests**

Create `test_console.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest test_console.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'console'`

- [ ] **Step 3: Implement `console.py`**

Create `console.py`:

```python
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
    from prompt_toolkit.completion import WordCompleter
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.patch_stdout import patch_stdout
    from prompt_toolkit.shortcuts import radiolist_dialog, checkboxlist_dialog
except Exception:                      # pragma: no cover - import guard
    _pt = None

COMMANDS = ["/jd", "/batch", "/skills", "/add", "/setup", "/recompile",
            "/status", "/help", "/exit"]


def available() -> bool:
    return _pt is not None and sys.stdin.isatty()


def _fallback_line(message: str) -> str:
    try:
        return input(message)
    except EOFError:
        print()
        return "/exit"


def prompt(message: str = "> ", *, status: str = "", history_file=None) -> str:
    """One line from the persistent input region. "/exit" on end-of-input."""
    if not available():
        return _fallback_line(message)

    bindings = KeyBindings()

    @bindings.add("escape", "enter")
    def _(event):                      # Alt+Enter inserts a newline; Enter submits
        event.current_buffer.insert_text("\n")

    session = _pt.PromptSession(
        history=FileHistory(str(history_file)) if history_file else None,
        completer=WordCompleter(COMMANDS, ignore_case=True),
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
    if not available():
        return _fallback_pick(msg, options, multi=multi, detail=detail)

    def label(o):
        text = detail(o) if detail is not None else ""
        return f"{o}\n    {text}" if text else str(o)

    values = [(o, label(o)) for o in options]
    if multi:
        chosen = checkboxlist_dialog(
            title="Resume-Tex", text=msg, values=values,
            default_values=[o for o in options if precheck and precheck(o)] or None).run()
        return list(chosen or [])
    chosen = radiolist_dialog(title="Resume-Tex", text=msg, values=values).run()
    return chosen if chosen is not None else options[0]
```

- [ ] **Step 4: Route `ui` through `console`**

In `ui.py`, add `import console` at the top, then change the three widgets to
delegate when the console is available.

**Every delegation is guarded by `interactive() and console.available()`, not
`console.available()` alone.** `console.available()` reads `sys.stdin.isatty()`
directly, while test_ui.py's autouse `_no_tty` fixture patches `ui.interactive`.
Without the `interactive()` half these three widgets take the console path under
`pytest -s` and every existing test for them breaks.

Replace the first line of `ui.select`:

```python
def select(msg: str, choices: list) -> str:
    if interactive() and console.available():
        return console.pick(msg, list(choices))
    if _use_widgets():
```

Replace the first line of `ui.checkbox`:

```python
def checkbox(msg: str, choices: list, *, default: tuple = ()) -> list:
    if interactive() and console.available():
        return console.pick(msg, list(choices), multi=True,
                            precheck=lambda c: c in default)
    if _use_widgets():
```

And in `ui.evidence_checkbox` (Task 1), insert before the `_use_widgets()` branch
(after `precheck` has been given its default, since this uses it):

```python
    if interactive() and console.available():
        picked = console.pick(msg, [c["skill"] for c in candidates], multi=True,
                              detail=lambda s: _evidence_line(
                                  next(c for c in candidates if c["skill"] == s)).strip(),
                              precheck=lambda s: precheck(
                                  next(c for c in candidates if c["skill"] == s)))
        return [c for c in candidates if c["skill"] in set(picked)]
```

- [ ] **Step 5: Use the input region in the shell**

In `shell.py`, replace the `input("> ")` call in `main`:

```python
    history = ROOT / ".resume_history"
    while True:
        status = f"{session.backend}/{session.model}"
        try:
            line = console.prompt("> ", status=status, history_file=history)
        except KeyboardInterrupt:
            print()
            continue
        if not dispatch(session, line):
            break
```

Add `import console` to `shell.py` and `.resume_history` to `.gitignore`.

- [ ] **Step 6: Run the whole suite**

Run: `python3 -m pytest -q`
Expected: PASS. `test_ui.py`'s autouse `_no_tty` fixture makes `console.available()` false, so every existing test still exercises the plain path.

- [ ] **Step 7: Commit**

```bash
git add console.py test_console.py ui.py shell.py .gitignore
git commit -m "feat: console — persistent input region and full-screen option pickers"
```

---

## Task 12: `drafts.py` — evidence-only draft gap answers

**Files:**
- Create: `drafts.py`
- Modify: `tailor.py:665-689` (`collect_gap_answers` pre-fills the editor)
- Test: `test_drafts.py`

**Interfaces:**
- Consumes: `llm.complete_schema`, `Profile.content`
- Produces:
  - `source_text(profile, target: str) -> str`
  - `draft_for(question: str, target: str, profile, *, backend, model) -> tuple` — `(draft, citation)`, both `""` when unverifiable

- [ ] **Step 1: Write the failing tests**

Create `test_drafts.py`:

```python
"""drafts.py: a draft answer may only restate what the user already wrote."""
import types

import drafts

CONTENT = {
    "experience": [{"company": "Acme", "bullets": ["shipped the design system"],
                    "notes": "40 components, 3 product teams"}],
    "projects": [{"title": "Transit Fare", "bullets": ["cut checkout steps from 7 to 4"],
                  "notes": "12M riders"}],
}


def _profile():
    return types.SimpleNamespace(content=CONTENT)


def test_source_text_is_scoped_to_the_target():
    text = drafts.source_text(_profile(), "project:Transit Fare")
    assert "12M riders" in text
    assert "40 components" not in text        # a different entry must not leak in


def test_general_target_has_no_source():
    assert drafts.source_text(_profile(), "general") == ""


def test_general_target_makes_no_model_call(monkeypatch):
    def boom(**k):
        raise AssertionError("should not call the model")
    monkeypatch.setattr(drafts.llm, "complete_schema", boom)
    assert drafts.draft_for("q", "general", _profile(), backend="openclaw", model="m") == ("", "")


def test_verified_draft_is_returned_with_a_citation(monkeypatch):
    monkeypatch.setattr(drafts.llm, "complete_schema", lambda **k: (
        {"draft": "12M riders; checkout cut from 7 steps to 4.",
         "used": ["12M riders", "cut checkout steps from 7 to 4"]}, None))
    text, cite = drafts.draft_for("scale?", "project:Transit Fare", _profile(),
                                  backend="openclaw", model="m")
    assert text.startswith("12M riders")
    assert "project:Transit Fare" in cite


def test_unsupported_span_yields_no_draft(monkeypatch):
    # The load-bearing guard: a fluent answer citing text the user never wrote is
    # exactly the fabrication this feature could otherwise introduce, and it would
    # be distilled into content.json where later applications reuse it.
    monkeypatch.setattr(drafts.llm, "complete_schema", lambda **k: (
        {"draft": "Served 50M riders across four countries.",
         "used": ["50M riders across four countries"]}, None))
    assert drafts.draft_for("scale?", "project:Transit Fare", _profile(),
                            backend="openclaw", model="m") == ("", "")


def test_empty_used_list_yields_no_draft(monkeypatch):
    monkeypatch.setattr(drafts.llm, "complete_schema", lambda **k: (
        {"draft": "Something plausible.", "used": []}, None))
    assert drafts.draft_for("scale?", "project:Transit Fare", _profile(),
                            backend="openclaw", model="m") == ("", "")


def test_model_failure_yields_no_draft(monkeypatch):
    def boom(**k):
        raise drafts.llm.LLMError("no model")
    monkeypatch.setattr(drafts.llm, "complete_schema", boom)
    assert drafts.draft_for("scale?", "project:Transit Fare", _profile(),
                            backend="openclaw", model="m") == ("", "")


def test_unknown_target_yields_no_draft(monkeypatch):
    monkeypatch.setattr(drafts.llm, "complete_schema",
                        lambda **k: ({"draft": "x", "used": ["x"]}, None))
    assert drafts.draft_for("q", "project:Nonexistent", _profile(),
                            backend="openclaw", model="m") == ("", "")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest test_drafts.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'drafts'`

- [ ] **Step 3: Implement `drafts.py`**

Create `drafts.py`:

```python
"""Pre-fill a gap answer with what the user has already written — and nothing else.

A gap answer is distilled into content.json notes, where every later application
reuses it. That makes this the easiest place in the project to acquire a claim
nobody can defend: a fluent draft is accepted by reflex in a way a blank field
never is. So the draft is assembled from one entry's own text, every span it
claims to use is checked as a substring in Python, and anything unverifiable
returns nothing at all rather than something plausible.
"""
from __future__ import annotations

from typing import List

from pydantic import BaseModel

import llm
import ui

DRAFT_SYSTEM = (
    "Answer the question using ONLY the source text supplied. Every clause of `draft` "
    "must be supported by the source. Copy into `used` the exact spans of source text "
    "you relied on, verbatim, character for character. If the source does not answer "
    "the question, return an empty draft and an empty `used`. Never add facts, "
    "numbers, or context that is not in the source. Return the schema only."
)


class DraftAnswer(BaseModel):
    draft: str = ""
    used: List[str] = []


def source_text(profile, target: str) -> str:
    """Bullets and notes of the one entry `target` names. "" for general/unknown."""
    target = (target or "").strip()
    if ":" not in target:
        return ""
    kind, _, label = target.partition(":")
    key, match = ("experience", "company") if kind == "experience" else ("projects", "title")
    for entry in (profile.content.get(key) or []):
        if (entry.get(match) or "") == label:
            parts = list(entry.get("bullets") or [])
            if entry.get("notes"):
                parts.append(entry["notes"])
            return "\n".join(parts)
    return ""


def draft_for(question: str, target: str, profile, *, backend: str, model: str) -> tuple:
    """(draft, citation). ("", "") whenever the draft cannot be traced to the source."""
    source = source_text(profile, target)
    if not source:
        return "", ""
    user = f"Source text:\n---\n{source}\n---\nQuestion: {question}"
    try:
        with ui.spinner("Checking what you have already written about this"):
            data, _ = llm.complete_schema(backend=backend, model=model,
                                          system=DRAFT_SYSTEM, user=user,
                                          schema=DraftAnswer, thinking="low")
    except Exception:
        return "", ""

    draft = (data.get("draft") or "").strip()
    used = [u for u in (data.get("used") or []) if (u or "").strip()]
    if not draft or not used:
        return "", ""
    # Every cited span must actually be in the user's own text.
    if any(u.strip() not in source for u in used):
        return "", ""
    return draft, f"draft (from your notes · {target}):"
```

- [ ] **Step 4: Pre-fill the editor in `collect_gap_answers`**

In `tailor.py`, replace the loop body of `collect_gap_answers` (tailor.py:677-679):

```python
    for i, g in enumerate(gap_questions, 1):
        draft, citation = drafts.draft_for(g["question"], g.get("target", ""), profile,
                                           backend=backend, model=model)
        if draft:
            print(f"\n  {citation}")
        else:
            print("\n  no material found for this — answer from scratch:")
        raw = ui.editor(f"A{i}", default=draft)
```

Add `import drafts` to `tailor.py`'s import block.

- [ ] **Step 5: Run the whole suite**

Run: `python3 -m pytest -q`
Expected: PASS. `collect_gap_answers` returns early when `not ui.interactive()`, so `test_flow.py` never reaches the new call.

- [ ] **Step 6: Commit**

```bash
git add drafts.py test_drafts.py tailor.py
git commit -m "feat: drafts — pre-fill gap answers from your own notes, verified span by span"
```

---

## Self-Review Notes

**Execution order:** 1 → 9, then 13, 11, 12, then 10. Task 13 precedes Task 12 because `drafts.py` reads notes as source text and must read the fixed shape.

**Spec coverage:** every spec section maps to a task — §1 shell→T8, §2 batch→T9, §3 jdsource→T2, §4 skills→T6, §5 merge→T7, §6 install fixes→T3/T4/T5, §7 console→T11, §8 drafts→T12, §9 ui→T1, §Testing→tests in each task plus T10's manual check.

**Naming consistency checked:** `entry_ref` (merge), `corpus_text`/`derive`/`apply` (skills — `apply` is defined in both `skills` and `merge`, but they are module-scoped and never imported bare), `jd_to_path`/`dispatch`/`Session` (shell), `_score_one`/`score_all`/`select_targets`/`generate`/`cmd_batch` (batch), `available`/`prompt`/`pick` (console), `source_text`/`draft_for` (drafts). `install.write_constant_key` is reused, not redefined.

**Two guards do the same job in two places, deliberately:** `skills.derive` drops a candidate whose `evidence` is not a substring of the corpus, and `drafts.draft_for` returns nothing when a cited span is not a substring of the source entry. Both are mechanical substring checks in Python, not instructions to the model, because both feed `content.json` — where a wrong claim persists into every future application.

**Known deferrals:** `cmd_setup` re-runs all of `install.main()` rather than a single named step; `/setup <step>` is accepted by the arg parse but ignored. `console.prompt` uses single-line mode with `Alt+Enter` for newlines rather than a true multi-line box; upgrade only if pasting long text proves awkward. Both are worth doing only if they annoy in real use.
