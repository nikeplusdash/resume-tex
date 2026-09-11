# Tailor A→E Flow + Template Packaging — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reorder `tailor.py` into an interactive score→ask→generate→cover-letter flow, and ship the project as a personal-data-free template with an installer, a `Profile` personalization class, and a Claude-Code-style prompt UI.

**Architecture:** Start from `resume-tex.zip` (the de-personalized branch). Add `profile.py` (all applicant-specific logic behind one class), `ui.py` (questionary + rich prompts that degrade to plain I/O on a non-TTY), a score-tier map in `llm.py`, four optional LaTeX sections, an A→E flow in `tailor.py:main()`, a `recompile.py` with an interactive add-sections menu, and `install.py` (deps → CLI auth → résumé bootstrap → walkthrough).

**Tech Stack:** Python 3.9+, pydantic v2, jinja2, questionary, rich, pdfplumber, python-docx, pdflatex, pytest. Model calls go through `llm.py`'s existing OpenClaw / Claude-CLI backends.

**Spec:** `docs/superpowers/specs/2026-09-01-tailor-flow-and-packaging-design.md`

## Global Constraints

- **Base is `resume-tex.zip`**, not the working-directory `tailor.py`. Unpack it over the project in Task 1 before any other work.
- **No project is a git repo yet.** Task 1 runs `git init`. Every task ends with a commit.
- **No model calls in tests.** Mock `llm.run_once` / `llm.complete_text` / `llm.complete_schema`, or `ui.*`. The suite must stay network-free and offline. Target: existing suite (≈88 tests) stays green plus the new tests.
- **No personal data in any committed file.** Sample/persona data only (the `examples/` "Alex Rivera" set). User data files (`constant.json`, `content.json`, `impact-metrics.md`, `recommendations.json`, `coursework.json`, `applications/`) are git-ignored.
- **Prompt/panel copy is terse** — a label and the choices, no explanatory paragraphs on screen.
- **Interactive code path runs only when `sys.stdin.isatty()` and not `args.no_interactive`.** Piped/scripted runs take the straight-through path.
- **Python floor is 3.9** — no `match` statements, no `X | Y` runtime type unions in signatures that execute on 3.9 (annotations are fine with `from __future__ import annotations`, already imported in `llm.py`).
- Preserve JSON files as `json.dumps(obj, indent=2, ensure_ascii=False)` + trailing newline when rewriting them.

---

## File Structure

| File | Responsibility |
|---|---|
| `profile.py` (new) | `Profile` — loads `constant.json` + `content.json`, exposes typed accessors, builds the personalized system prompt, writes notes back |
| `ui.py` (new) | Interactive prompt primitives (`confirm/text/select/checkbox/editor`) and rich output (`score_panel/gap_table/report`); plain-I/O fallback when not a TTY |
| `tailor.py` (modify) | The A→E pipeline; consumes a `Profile`; new schema fields for optional sections; new flags `--no-interactive/-y`, `--score-preset` |
| `recompile.py` (new to package) | Recompile a saved application folder from its edited JSON; `-i` menu to merge in optional sections; no model call |
| `llm.py` (modify) | `SCORE_TIER` map + `score_model()` |
| `template.tex` (modify) | Four guarded optional-section blocks: certifications, awards, publications, languages |
| `requirements.txt` (modify) | + questionary, rich, pdfplumber, python-docx |
| `install.py` (new) | One-time setup wizard |
| `README.md` (modify) | Setup rewritten around `install.py`; A→E flow documented |
| `examples/` (modify) | Sample `content.json` gains the four optional sections; add `sample-resume.txt` |
| `test_profile.py`, `test_ui.py`, `test_score.py`, `test_flow.py`, `test_template_sections.py`, `test_recompile.py`, `test_install.py` (new) | Unit tests per component |

---

## Task 1: Unpack the packaging branch and establish a green baseline

**Files:**
- Replace working tree with the contents of `resume-tex.zip`
- Create: `.git/` (via `git init`), `docs/superpowers/` already present
- Modify: `.gitignore`

- [ ] **Step 1: Snapshot the current working directory**

```bash
cd $REPO
mkdir -p /private/tmp/resume-tex-prezip && cp -R . /private/tmp/resume-tex-prezip/
```

- [ ] **Step 2: Unpack the zip into a staging dir and compare**

```bash
cd /private/tmp && rm -rf zstage && mkdir zstage && cd zstage
unzip -q $REPO/resume-tex.zip
diff -rq resume-tex $REPO \
  | grep -v -E '\.git|__pycache__|\.pytest_cache|\.DS_Store|docs/superpowers|applications' || true
```

Expected: the zip's `tailor.py`, `llm.py`, `template.tex`, `README.md`, `requirements.txt`, `.gitignore`, and `examples/` differ or are new; the working dir has extra personal files (`content.json`, `constant.json`, `impact-metrics.md`, `<Name>*.pdf`, `bench-jds/`, `extra/`, `recompile.py`, `cv_preview.py`, …).

- [ ] **Step 3: Copy the zip's tracked files over the project**

```bash
cd $REPO
cp /private/tmp/zstage/resume-tex/tailor.py .
cp /private/tmp/zstage/resume-tex/llm.py .
cp /private/tmp/zstage/resume-tex/bench.py .
cp /private/tmp/zstage/resume-tex/build.py .
cp /private/tmp/zstage/resume-tex/template.tex .
cp /private/tmp/zstage/resume-tex/README.md .
cp /private/tmp/zstage/resume-tex/requirements.txt .
cp /private/tmp/zstage/resume-tex/.gitignore .
cp /private/tmp/zstage/resume-tex/test_llm.py .
cp /private/tmp/zstage/resume-tex/test_portfolio.py .
cp /private/tmp/zstage/resume-tex/test_tailor_quality.py .
rm -rf examples && cp -R /private/tmp/zstage/resume-tex/examples .
```

- [ ] **Step 4: Keep the personal data files locally, git-ignore them**

Append to `.gitignore` (if not already covered):

```
# personal data — never committed
/constant.json
/content.json
/content.*.json
/content.resume.json
/impact-metrics.md
/recommendations.json
/coursework.json
/applications/
/bench-jds/
/bench-runs/
/extra/
/*.pdf
/*.jpg
/Official Transcript*
```

- [ ] **Step 5: Provide the data files the tests/tools expect at runtime**

The zip's `tailor.py` `load_json_required` needs `constant.json` + `content.json` to *run*, but the tests do not (they mock or use `examples/`). Confirm by copying the examples to root for local runs only:

```bash
cp examples/constant.json examples/content.json .
```

(These are git-ignored by Step 4. Real personal data can replace them later.)

- [ ] **Step 6: Init git and run the baseline suite**

```bash
git init
python3 -m pip install -r requirements.txt
python3 -m pytest -q
```

Expected: PASS (≈88 tests), no network.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "chore: adopt de-personalized packaging branch as baseline

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Pd3ymHfez9zTb3kKjF8JZM"
```

---

## Task 2: `Profile` class

**Files:**
- Create: `profile.py`
- Test: `test_profile.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `class Profile` with classmethods `Profile.load(root: Path, *, mode: str = "resume") -> "Profile"` and `Profile.from_dicts(constant: dict, content: dict, *, root: Path | None = None, mode: str = "resume") -> "Profile"`
  - properties: `identity: dict`, `location: str`, `location_rules: list`, `skill_groups: list[str]`, `solo_worker: bool`, `banned_summary_anchors: list[str]`, `doc_basename: str`, `output_dir: str`, `backend: str | None`, `skills_title: str`, `projects_title: str`, `content: dict`, `optional_sections: list[str]`
  - methods: `portfolio_links() -> dict`, `location_for_jd(jd: str) -> str`, `system_prompt() -> str`, `append_note(target: str, text: str) -> None`
  - module constants moved from `tailor.py`: `SYSTEM_PROMPT_BASE`, `PORTFOLIO_RULE_TWO`, `PORTFOLIO_RULE_ONE`, `SOLO_RULE`, `SUMMARY_SOLO_RULE`, `EDUCATION_RULE`, `RESUME_CAPS`, `CV_GUIDANCE`
  - free functions kept importable: `portfolio_links(constant: dict) -> dict`, `location_for_jd(jd: str, constant: dict) -> str`

- [ ] **Step 1: Write the failing test**

```python
# test_profile.py
"""Profile owns every applicant-specific decision. One file to read when personalizing."""
import json
import pytest
from pathlib import Path
from profile import Profile, portfolio_links

CONSTANT_TWO = {
    "name": "Alex Rivera", "email": "a@x.com", "phone": "+1 555", "linkedin": "in/alex",
    "location": "Seattle, WA",
    "portfolios": {"visual": "visual.example.com", "research": "example.com"},
    "location_rules": [{"match": r"\bIndia\b", "location": "Mumbai, India"}],
    "solo_worker": True,
    "banned_summary_anchors": ["95 SUS"],
    "skills_title": "Skills", "doc_basename": "Alex_Rivera", "output_dir": "",
    "backend": "openclaw",
}
CONTENT = {
    "portfolio_link": "example.com", "summary": "s",
    "experience": [{"company": "Northgate", "title": "PD", "dates": "2023--", "location": "R", "notes": "n", "bullets": ["b"]}],
    "projects": [{"title": "Transit Board", "notes": "n", "bullets": ["b"]}],
    "skills": [{"label": "Research Skills", "entries": ["Interviews"]}],
    "education": [{"institution": "UW", "degree": "B.Des", "gpa": "3.8", "start": "2015", "end": "2019", "location": "S"}],
    "certifications": [],
}

def prof(constant=None, content=None):
    return Profile.from_dicts(constant or CONSTANT_TWO, content or CONTENT)

def test_identity_is_only_the_header_fields():
    assert prof().identity == {"name": "Alex Rivera", "email": "a@x.com", "phone": "+1 555", "linkedin": "in/alex"}

def test_portfolio_links_two_site_shape():
    assert prof().portfolio_links() == {"visual": "visual.example.com", "research": "example.com"}

def test_portfolio_links_single_string():
    p = prof({**CONSTANT_TWO, "portfolios": "only.example.com"})
    assert p.portfolio_links() == {"default": "only.example.com"}

def test_portfolio_links_none_falls_back_to_content_link():
    c = {k: v for k, v in CONSTANT_TWO.items() if k != "portfolios"}
    assert prof(c).portfolio_links() == {"default": "example.com"}

def test_location_for_jd_rule_match_then_fallback():
    p = prof()
    assert p.location_for_jd("role based in India") == "Mumbai, India"
    assert p.location_for_jd("role in Berlin") == "Seattle, WA"

def test_location_for_jd_bad_regex_is_ignored(capsys):
    p = prof({**CONSTANT_TWO, "location_rules": [{"match": "(", "location": "X"}]})
    assert p.location_for_jd("anything") == "Seattle, WA"

def test_skill_groups_and_optional_sections():
    p = prof()
    assert p.skill_groups == ["Research Skills"]
    assert p.optional_sections == ["certifications"]  # present key, even though empty

def test_system_prompt_fills_every_token():
    sp = prof().system_prompt()
    assert "%%" not in sp
    assert "visual.example.com" in sp and "example.com" in sp
    assert "NEVER INVENT COLLABORATION" in sp            # solo rule on
    assert "95 SUS" in sp                                # banned anchor injected

def test_system_prompt_drops_solo_rule_when_not_solo():
    sp = prof({**CONSTANT_TWO, "solo_worker": False}).system_prompt()
    assert "%%" not in sp
    assert "NEVER INVENT COLLABORATION" not in sp

def test_append_note_to_existing_experience(tmp_path):
    (tmp_path / "constant.json").write_text(json.dumps(CONSTANT_TWO))
    (tmp_path / "content.json").write_text(json.dumps(CONTENT, indent=2))
    p = Profile.load(tmp_path)
    p.append_note("experience:Northgate", "Shipped v2 to 40k users.")
    reloaded = json.loads((tmp_path / "content.json").read_text())
    assert reloaded["experience"][0]["notes"].endswith("Shipped v2 to 40k users.")
    assert (tmp_path / "content.json").read_text().endswith("\n")

def test_append_note_general_bucket(tmp_path):
    (tmp_path / "constant.json").write_text(json.dumps(CONSTANT_TWO))
    (tmp_path / "content.json").write_text(json.dumps(CONTENT, indent=2))
    p = Profile.load(tmp_path)
    p.append_note("general", "Open to relocation.")
    assert json.loads((tmp_path / "content.json").read_text())["notes"] == "Open to relocation."

def test_append_note_unknown_target_raises(tmp_path):
    (tmp_path / "constant.json").write_text(json.dumps(CONSTANT_TWO))
    (tmp_path / "content.json").write_text(json.dumps(CONTENT, indent=2))
    p = Profile.load(tmp_path)
    with pytest.raises(ValueError):
        p.append_note("project:Does Not Exist", "x")

def test_load_missing_file_exits(tmp_path):
    with pytest.raises(SystemExit):
        Profile.load(tmp_path)
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest test_profile.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'profile'` (note: shadows stdlib `profile`; that is fine for a script project, but the test imports the local file first because CWD is on `sys.path`).

- [ ] **Step 3: Write `profile.py`**

Move the prompt constants and helpers out of `tailor.py` verbatim, then wrap them:

```python
"""Profile — everything about the applicant. Edit constant.json / content.json, or subclass."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Optional

# ── Prompt text (moved verbatim from tailor.py) ───────────────────────────────
SYSTEM_PROMPT_BASE = """<PASTE the exact string from zip tailor.py lines 407-462, unchanged>"""
EDUCATION_RULE = """<PASTE from tailor.py>"""
RESUME_CAPS = """<PASTE from tailor.py>"""
CV_GUIDANCE = """<PASTE from tailor.py>"""
PORTFOLIO_RULE_TWO = """<PASTE from tailor.py lines ~472-476>"""
PORTFOLIO_RULE_ONE = """<PASTE from tailor.py line ~479>"""
SOLO_RULE = """<PASTE from tailor.py>"""
SUMMARY_SOLO_RULE = """<PASTE from tailor.py>"""


def portfolio_links(constant: dict) -> dict:
    """<PASTE body of tailor.py portfolio_links(profile), s/profile/constant/>"""
    raw = constant.get("portfolios")
    if isinstance(raw, str):
        raw = {"default": raw}
    if not isinstance(raw, dict) or not any(raw.values()):
        link = (constant.get("portfolio_link") or "").strip()
        return {"default": link} if link else {}
    visual, research = raw.get("visual"), raw.get("research")
    if visual and research and visual != research:
        return {"visual": visual, "research": research}
    single = raw.get("default") or visual or research or next((v for v in raw.values() if v), "")
    return {"default": single} if single else {}


def location_for_jd(jd: str, constant: dict) -> str:
    """<PASTE body of tailor.py default_location_for_jd(jd, profile), s/profile/constant/>"""
    for rule in constant.get("location_rules") or []:
        pattern, location = rule.get("match"), rule.get("location")
        if not pattern or not location:
            continue
        try:
            if re.search(pattern, jd or "", re.I):
                return location
        except re.error as e:
            print(f"Warning   : ignoring bad location_rules regex {pattern!r}: {e}")
            continue
    return constant.get("location", "")


def _summary_bans(constant: dict) -> str:
    """<PASTE body of tailor.py _summary_bans(profile)>"""
    anchors = [a for a in (constant.get("banned_summary_anchors") or []) if a]
    if not anchors:
        return ""
    return ("- BANNED IN THE SUMMARY: " + "; ".join(anchors) +
            ". These stay available as bullets, but must never be the headline proof of "
            "the candidate's work. Pick a different concrete anchor (shipped artifact, "
            "user/vote counts, component counts, latency or time savings, tools).\n")


_IDENTITY_KEYS = ("name", "email", "phone", "linkedin")
_OPTIONAL_SECTION_KEYS = ("certifications", "awards", "publications", "languages")


class Profile:
    def __init__(self, constant: dict, content: dict, *, root: Optional[Path] = None, mode: str = "resume"):
        self._constant = constant
        self._content = content
        self._root = root
        self._mode = mode

    # ── constructors ──
    @classmethod
    def load(cls, root: Path, *, mode: str = "resume") -> "Profile":
        root = Path(root)
        constant = cls._read_required(root, "constant.json")
        content = cls._read_required(root, "content.json")
        return cls(constant, content, root=root, mode=mode)

    @classmethod
    def from_dicts(cls, constant: dict, content: dict, *, root: Optional[Path] = None, mode: str = "resume") -> "Profile":
        return cls(dict(constant), dict(content), root=root, mode=mode)

    @staticmethod
    def _read_required(root: Path, name: str) -> dict:
        path = root / name
        if not path.exists():
            print(f"Error: {name} not found in {root}.")
            print(f"  Copy examples/{name} to {path} and fill in your own details.")
            print("  See README.md -> 'Your data files'.")
            sys.exit(1)
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError as e:
            print(f"Error: {path} is not valid JSON: {e}")
            sys.exit(1)

    # ── identity / config ──
    @property
    def identity(self) -> dict:
        return {k: self._constant[k] for k in _IDENTITY_KEYS if k in self._constant}

    @property
    def location(self) -> str:
        return self._constant.get("location", "")

    @property
    def location_rules(self) -> list:
        return self._constant.get("location_rules") or []

    @property
    def solo_worker(self) -> bool:
        return bool(self._constant.get("solo_worker"))

    @property
    def banned_summary_anchors(self) -> list:
        return [a for a in (self._constant.get("banned_summary_anchors") or []) if a]

    @property
    def skills_title(self) -> str:
        return self._content.get("skills_title") or self._constant.get("skills_title") or "Skills"

    @property
    def projects_title(self) -> str:
        return "Relevant Projects" if self._mode == "resume" else "Personal Projects"

    @property
    def doc_basename(self) -> str:
        from tailor import safe_name  # safe_name stays in tailor.py
        raw = (self._constant.get("doc_basename") or self._constant.get("name") or "").strip()
        return safe_name(raw, "Resume")

    @property
    def output_dir(self) -> str:
        return self._constant.get("output_dir") or ""

    @property
    def backend(self) -> Optional[str]:
        return self._constant.get("backend")

    @property
    def content(self) -> dict:
        return self._content

    @property
    def skill_groups(self) -> list:
        return [g.get("label") for g in self._content.get("skills") or [] if g.get("label")]

    @property
    def optional_sections(self) -> list:
        return [k for k in _OPTIONAL_SECTION_KEYS if k in self._content]

    # ── behavior ──
    def portfolio_links(self) -> dict:
        return portfolio_links(self._constant)

    def location_for_jd(self, jd: str) -> str:
        return location_for_jd(jd, self._constant)

    def system_prompt(self) -> str:
        """<PASTE body of tailor.py build_system_prompt(profile), reading self>"""
        links = self.portfolio_links()
        if "visual" in links:
            portfolio_rule = (PORTFOLIO_RULE_TWO
                              .replace("%%VISUAL%%", links["visual"])
                              .replace("%%RESEARCH%%", links["research"]))
        elif links:
            portfolio_rule = PORTFOLIO_RULE_ONE.replace("%%LINK%%", links["default"])
        else:
            portfolio_rule = ("PORTFOLIO_LINK — the candidate has no portfolio URL on file. "
                              "Return an empty string; never invent one.")
        groups = ", ".join(f'"{label}"' for label in self.skill_groups) or "as given in the CV JSON below"
        solo = self.solo_worker
        return (SYSTEM_PROMPT_BASE
                .replace("%%SKILL_GROUPS%%", groups)
                .replace("%%PORTFOLIO_RULE%%", portfolio_rule)
                .replace("%%SOLO_RULE%%", SOLO_RULE if solo else "")
                .replace("%%SUMMARY_BANS%%", _summary_bans(self._constant))
                .replace("%%SUMMARY_SOLO%%", SUMMARY_SOLO_RULE if solo else ""))

    def append_note(self, target: str, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        if target == "general":
            existing = (self._content.get("notes") or "").strip()
            self._content["notes"] = (existing + " " + text).strip() if existing else text
        else:
            kind, _, name = target.partition(":")
            key = {"experience": "experience", "project": "projects"}.get(kind)
            if not key:
                raise ValueError(f"unknown note target {target!r}")
            match = "company" if key == "experience" else "title"
            for entry in self._content.get(key, []):
                if entry.get(match) == name:
                    existing = (entry.get("notes") or "").strip()
                    entry["notes"] = (existing + " " + text).strip() if existing else text
                    break
            else:
                raise ValueError(f"no {kind} entry named {name!r}")
        self._write()

    def _write(self) -> None:
        if self._root is None:
            return
        (self._root / "content.json").write_text(
            json.dumps(self._content, indent=2, ensure_ascii=False) + "\n")
```

Note the paste markers: copy the referenced literals from the zip's `tailor.py` **exactly**, including the em dashes and the `%%TOKEN%%` placeholders inside `SYSTEM_PROMPT_BASE`.

- [ ] **Step 4: Run the test**

Run: `python3 -m pytest test_profile.py -q`
Expected: PASS (13 tests).

- [ ] **Step 5: Commit**

```bash
git add profile.py test_profile.py
git commit -m "feat: add Profile class for applicant-specific config and prompt"
```

---

## Task 3: Route `tailor.py` through `Profile`

**Files:**
- Modify: `tailor.py` — delete the moved constants/helpers, import from `profile`, thread a `Profile` through `main()`, `tailor_content()`, `generate_cover_letter_prompt()`, `enforce_portfolio()`, `check_integrity()`, `compile_pdf()`
- Modify: `test_portfolio.py`, `test_tailor_quality.py` — pass a `Profile` (or `Profile.from_dicts`) where they currently pass a raw dict
- Test: existing suite

**Interfaces:**
- Consumes: `profile.Profile`, `profile.portfolio_links`, all moved constants.
- Produces:
  - `tailor.tailor_content(profile: Profile, jd: str, mode="resume", *, include_education=False, extra_context="", backend, model, thinking, binary=None, timeout=..., on_retry=None) -> dict`
  - `tailor.enforce_portfolio(tailored: dict, jd: str, profile: Profile) -> dict`
  - `tailor.check_integrity(tailored: dict, source: dict, match_analysis=None, profile: Profile | None = None) -> list`
  - `tailor.safe_name` unchanged and still importable (Profile.doc_basename depends on it)

- [ ] **Step 1: Update the tests first (they encode the new call shape)**

In `test_portfolio.py` replace the raw-dict `profile` args with a `Profile`:

```python
from profile import Profile

def _prof(constant):
    content = {"skills": [], "experience": [], "projects": []}
    return Profile.from_dicts(constant, content)

def enforce(title, link, jd=TILCO_JD, profile=None):
    p = profile if profile is not None else _prof(TWO)
    return tailor.enforce_portfolio(
        {"job_title": title, "portfolio_link": link}, jd, p)["portfolio_link"]
```

Update the `TWO` / `ONE` module constants to be the `constant.json` shape they already are (`{"portfolios": {...}}`), and any direct `tailor.portfolio_links(TWO)` call becomes `tailor.portfolio_links(TWO)` still works — keep the free function delegating. In `test_tailor_quality.py`, wherever `build_system_prompt(cv)` or `tailor_content(cv, ...)` is called, switch to `Profile.from_dicts(constant, content)` and `profile.system_prompt()` / `tailor_content(profile, ...)`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest test_portfolio.py test_tailor_quality.py -q`
Expected: FAIL — `AttributeError` / `TypeError` on the old signatures.

- [ ] **Step 3: Edit `tailor.py`**

1. Delete from `tailor.py`: `SYSTEM_PROMPT_BASE`, `EDUCATION_RULE`, `RESUME_CAPS`, `CV_GUIDANCE`, `PORTFOLIO_RULE_TWO`, `PORTFOLIO_RULE_ONE`, `SOLO_RULE`, `SUMMARY_SOLO_RULE`, `_summary_bans`, `build_system_prompt`, `portfolio_links`, `default_location_for_jd`, `document_basename`. Keep `safe_name`, `enforce_caps`, `normalize_match_analysis`, everything else.
2. Add near the top: `from profile import (Profile, portfolio_links, location_for_jd, SYSTEM_PROMPT_BASE, EDUCATION_RULE, RESUME_CAPS, CV_GUIDANCE)`.
3. `tailor_content`: change first param `cv: dict` → `profile: Profile`; inside, `content_only = {k: v for k, v in profile.content.items() if k in content_keys}` and `system_prompt = (profile.system_prompt() + (EDUCATION_RULE if include_education else "") + (RESUME_CAPS if mode == "resume" else CV_GUIDANCE) + ...)`.
4. `enforce_portfolio(tailored, jd, profile)`: replace `links = portfolio_links(profile)` call already there — now `links = profile.portfolio_links()`.
5. `check_integrity(..., profile=None)`: `solo = bool(profile.solo_worker) if profile else False`; `for anchor in (profile.banned_summary_anchors if profile else []):`.
6. `generate_cover_letter_prompt(profile, jd, ...)`: `content_only` from `profile.content`.
7. `main()`:
   - `profile = Profile.load(ROOT, mode=mode)`
   - `backend, model = llm.resolve(args.preset, model=args.model, backend=args.provider or profile.backend)`
   - `render_data = dict(profile.identity)`
   - `render_data["skills_title"] = profile.skills_title`
   - `render_data["projects_title"] = profile.projects_title`
   - `render_data["location"] = args.location or profile.location_for_jd(jd)`
   - `tailored = tailor_content(profile, jd, mode=mode, ...)`
   - `tailored = enforce_portfolio(tailored, jd, profile)`
   - `pdf_path, final_data = compile_pdf(render_data, output_base, company, job_title, mode=mode, basename=profile.doc_basename)`
   - `flags = check_integrity(shipped, profile.content, match_analysis, profile=profile)`
   - `output_base = Path(args.output_dir or profile.output_dir or (ROOT / "applications")).expanduser()`

- [ ] **Step 4: Run the full suite**

Run: `python3 -m pytest -q`
Expected: PASS (baseline count, adjusted for the edited tests — same total).

- [ ] **Step 5: Smoke-test a real run offline**

Run: `python3 tailor.py --help`
Expected: usage prints, no import error.

- [ ] **Step 6: Commit**

```bash
git add tailor.py test_portfolio.py test_tailor_quality.py
git commit -m "refactor: route tailor.py through Profile; move prompt text to profile.py"
```

---

## Task 4: `ui.py` — interactive prompt layer

**Files:**
- Create: `ui.py`
- Modify: `requirements.txt` — add `questionary>=2.0`, `rich>=13.0`
- Test: `test_ui.py`

**Interfaces:**
- Produces:
  - `ui.confirm(msg: str, *, default: bool = False) -> bool`
  - `ui.text(msg: str, *, default: str = "", validate=None) -> str`
  - `ui.select(msg: str, choices: list[str]) -> str`
  - `ui.checkbox(msg: str, choices: list[str], *, default: tuple = ()) -> list[str]`
  - `ui.editor(msg: str, *, default: str = "") -> str`
  - `ui.score_panel(baseline: int, requirements: list[dict]) -> None`
  - `ui.gap_table(gap_questions: list) -> None`
  - `ui.report(*, baseline, tailored: int, folded: list, improvements: list, questions: list, warnings: list) -> None`
  - `ui.interactive() -> bool` (== `sys.stdin.isatty()`; monkeypatched in tests)

- [ ] **Step 1: Write the failing test**

```python
# test_ui.py
"""ui.py: rich prompts on a TTY, plain stdin/stdout when piped. No prompt reaches the model."""
import builtins
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
    assert "72" in out and "88" in out and "K8s" in out


def test_widgets_used_when_interactive(monkeypatch):
    monkeypatch.setattr(ui, "interactive", lambda: True)
    calls = {}
    class FakeQ:
        def __init__(self, val): self._val = val
        def ask(self): return self._val
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest test_ui.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'ui'`.

- [ ] **Step 3: Write `ui.py`**

```python
"""Claude-Code-style prompts. questionary + rich on a TTY; plain I/O when piped.

Every function degrades so a non-interactive run (piped stdin, CI) still works and
never blocks. Tests monkeypatch `interactive` and, for the widget path, `_q`.
"""
from __future__ import annotations

import sys
from typing import Optional

try:                       # optional; absent -> fallback path only
    import questionary as _q
except Exception:          # pragma: no cover - import guard
    _q = None

try:
    from rich.console import Console as _Console
    from rich.panel import Panel as _Panel
    from rich.table import Table as _Table
    _console = _Console()
except Exception:          # pragma: no cover
    _console = None


def interactive() -> bool:
    return sys.stdin.isatty()


def _use_widgets() -> bool:
    return interactive() and _q is not None


# ── input ────────────────────────────────────────────────────────────────────
def confirm(msg: str, *, default: bool = False) -> bool:
    if _use_widgets():
        return bool(_q.confirm(msg, default=default).ask())
    ans = input(f"{msg} [{'Y/n' if default else 'y/N'}] ").strip().lower()
    if not ans:
        return default
    return ans in ("y", "yes")


def text(msg: str, *, default: str = "", validate=None) -> str:
    if _use_widgets():
        return _q.text(msg, default=default, validate=validate).ask() or default
    ans = input(f"{msg}{f' [{default}]' if default else ''} ").strip()
    return ans or default


def select(msg: str, choices: list) -> str:
    if _use_widgets():
        return _q.select(msg, choices=list(choices)).ask()
    print(msg)
    for i, c in enumerate(choices, 1):
        print(f"  {i}. {c}")
    while True:
        raw = input("> ").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(choices):
            return choices[int(raw) - 1]


def checkbox(msg: str, choices: list, *, default: tuple = ()) -> list:
    if _use_widgets():
        opts = [_q.Choice(c, checked=(c in default)) for c in choices]
        return list(_q.checkbox(msg, choices=opts).ask() or [])
    print(f"{msg}  (comma-separated numbers, Enter for none)")
    for i, c in enumerate(choices, 1):
        print(f"  {i}. {c}")
    raw = input("> ").strip()
    picks = []
    for tok in raw.split(","):
        tok = tok.strip()
        if tok.isdigit() and 1 <= int(tok) <= len(choices):
            picks.append(choices[int(tok) - 1])
    return picks


def editor(msg: str, *, default: str = "") -> str:
    if _use_widgets():
        return (_q.text(msg + " (Esc then Enter to finish)", default=default,
                        multiline=True).ask() or default).strip()
    print(f"{msg}  (end with Ctrl-D)")
    return sys.stdin.read().strip() or default


# ── output ───────────────────────────────────────────────────────────────────
def score_panel(baseline: int, requirements: list) -> None:
    if _console is not None and interactive():
        t = _Table(show_header=True, box=None)
        t.add_column("req"); t.add_column("fit"); t.add_column("wt", justify="right")
        for r in requirements:
            t.add_row(str(r.get("requirement", "")), str(r.get("status", "")), str(r.get("weight", 0)))
        _console.print(_Panel(t, title=f"Baseline match  {baseline}/100"))
        return
    print(f"Baseline match : {baseline}/100")
    for r in requirements:
        print(f"  {str(r.get('status','')).upper():8} {r.get('weight',0):>3}  {r.get('requirement','')}")


def gap_table(gap_questions: list) -> None:
    if _console is not None and interactive():
        t = _Table(show_header=True, box=None)
        t.add_column("#"); t.add_column("+pts", justify="right"); t.add_column("question")
        for i, g in enumerate(gap_questions, 1):
            t.add_row(str(i), str(getattr(g, "potential_points", g.get("potential_points", 0))),
                      str(getattr(g, "question", g.get("question", ""))))
        _console.print(t)
        return
    for i, g in enumerate(gap_questions, 1):
        pts = getattr(g, "potential_points", None) or g.get("potential_points", 0)
        q = getattr(g, "question", None) or g.get("question", "")
        print(f"  Q{i} (+{pts}): {q}")


def report(*, baseline, tailored: int, folded: list, improvements: list,
           questions: list, warnings: list) -> None:
    b = f"{baseline}" if baseline is not None else "n/a"
    delta = f" ({tailored - baseline:+d})" if baseline is not None else ""
    lines = [f"Match      : baseline {b}/100 -> tailored {tailored}/100{delta}"]
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
    if _console is not None and interactive():
        _console.print(_Panel(body, title="Result"))
    else:
        print(body)
```

- [ ] **Step 4: Run the test**

Run: `python3 -m pytest test_ui.py -q`
Expected: PASS (7 tests).

- [ ] **Step 5: Add deps and commit**

Edit `requirements.txt` to the block in the spec §8. Then:

```bash
python3 -m pip install -r requirements.txt
git add ui.py test_ui.py requirements.txt
git commit -m "feat: add ui.py — questionary/rich prompts with plain-IO fallback"
```

---

## Task 5: `llm.score_model` — one tier below the run model

**Files:**
- Modify: `llm.py` — add `SCORE_TIER` and `score_model()` after `resolve()` (zip `llm.py:127-154`)
- Test: `test_score.py` (new; the model-tier half)

**Interfaces:**
- Consumes: `llm.resolve`, `llm.PRESETS_BY_BACKEND`, `llm.DEFAULT_PRESET`.
- Produces: `llm.score_model(backend: str, run_preset: Optional[str], run_model_id: Optional[str], *, override_preset: Optional[str] = None) -> tuple[str, str]` returning `(backend, resolvable_model_id)`.

- [ ] **Step 1: Write the failing test**

```python
# test_score.py
import pytest
import llm

def test_default_run_scores_one_tier_down():
    assert llm.score_model("openclaw", "terra", None) == ("openclaw", "openai/gpt-5.6-luna")
    assert llm.score_model("claude", "sonnet", None) == ("claude", "claude-haiku-4-5")

def test_opus_scores_on_sonnet():
    assert llm.score_model("openclaw", "opus", None) == ("openclaw", "anthropic/claude-sonnet-5")
    assert llm.score_model("claude", "opus", None) == ("claude", "claude-sonnet-5")

def test_cheapest_preset_stays_itself():
    assert llm.score_model("openclaw", "luna", None)[1] == "openai/gpt-5.6-luna"
    assert llm.score_model("claude", "haiku", None)[1] == "claude-haiku-4-5"

def test_pinned_model_falls_back_to_backend_cheapest():
    # run pinned an explicit id (no preset) -> score on the backend's cheapest preset
    assert llm.score_model("openclaw", None, "anthropic/claude-opus-4-8")[1] == "openai/gpt-5.6-luna"
    assert llm.score_model("claude", None, "claude-opus-5")[1] == "claude-haiku-4-5"

def test_override_preset_wins():
    assert llm.score_model("openclaw", "terra", None, override_preset="sonnet") == ("openclaw", "anthropic/claude-sonnet-5")
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest test_score.py -q`
Expected: FAIL — `AttributeError: module 'llm' has no attribute 'score_model'`.

- [ ] **Step 3: Implement in `llm.py`**

```python
# ── Score tier: the model step B runs on, one notch below the generation model ──
SCORE_TIER: dict[str, dict[str, str]] = {
    "openclaw": {"opus": "sonnet", "sonnet": "luna", "opus-4-8": "sonnet-4-6",
                 "sonnet-4-6": "luna", "sol": "luna", "terra": "luna", "luna": "luna"},
    "claude":   {"opus": "sonnet", "sonnet": "haiku", "haiku": "haiku", "fable": "haiku"},
}
_CHEAPEST_PRESET = {"openclaw": "luna", "claude": "haiku"}


def score_model(backend, run_preset, run_model_id, *, override_preset=None):
    """(backend, resolvable model id) for the pre-score call.

    override_preset wins. Else one tier below run_preset per SCORE_TIER. If the run
    pinned an explicit --model (run_model_id set, run_preset None), score on the
    backend's cheapest preset rather than trying to demote an arbitrary id.
    """
    if override_preset:
        return resolve(override_preset, backend=backend)
    if run_preset:
        tier = SCORE_TIER.get(backend, {})
        return resolve(tier.get(run_preset, _CHEAPEST_PRESET[backend]), backend=backend)
    return resolve(_CHEAPEST_PRESET[backend], backend=backend)
```

- [ ] **Step 4: Run the test**

Run: `python3 -m pytest test_score.py -q`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add llm.py test_score.py
git commit -m "feat: llm.score_model — pick the pre-score model one tier down"
```

---

## Task 6: Optional LaTeX sections + schema + trimmer

**Files:**
- Modify: `template.tex` — four guarded blocks
- Modify: `tailor.py` — `CertEntry/AwardEntry/PubEntry/LangEntry` models; optional fields on `TailoredResume`; `_shrink_one_step` sheds them in Tier 2
- Modify: `examples/content.json` — add the four sections with sample data
- Test: `test_template_sections.py`

**Interfaces:**
- Consumes: `tailor.render_tex`, `tailor._shrink_one_step`.
- Produces: `TailoredResume` with `certifications/awards/publications/languages: Optional[List[...]]`; template renders each iff its key is truthy.

- [ ] **Step 1: Write the failing test**

```python
# test_template_sections.py
import copy
import tailor

BASE = {
    "name": "Alex Rivera", "email": "a@x.com", "phone": "+1", "linkedin": "in/a",
    "location": "Seattle, WA", "portfolio_link": "example.com", "summary": "s",
    "skills_title": "Skills", "projects_title": "Relevant Projects",
    "experience": [{"company": "N", "title": "PD", "dates": "2023--", "location": "S", "bullets": ["b1"]}],
    "projects": [{"title": "P", "bullets": ["b1"]}],
    "skills": [{"label": "Design Skills", "entries": ["Figma"]}],
}

def test_no_optional_sections_no_headings():
    tex = tailor.render_tex(BASE)
    for h in ("Certifications", "Awards", "Publications", "Languages"):
        assert h not in tex

def test_each_optional_section_renders_when_present():
    data = copy.deepcopy(BASE)
    data["certifications"] = [{"name": "CPACC", "issuer": "IAAP", "date": "2024"}]
    data["awards"] = [{"title": "Best Poster", "body": "CHI", "year": "2023"}]
    data["publications"] = [{"title": "On Boards", "venue": "CHI EA", "year": "2022"}]
    data["languages"] = [{"language": "English", "proficiency": "native"},
                         {"language": "French", "proficiency": "working"}]
    tex = tailor.render_tex(data)
    assert "Certifications" in tex and "CPACC" in tex and "IAAP" in tex
    assert "Awards" in tex and "Best Poster" in tex
    assert "Publications" in tex and "On Boards" in tex
    assert "Languages" in tex and "English (native)" in tex and "French (working)" in tex

def test_trimmer_sheds_languages_before_publications_before_awards_before_certs():
    data = copy.deepcopy(BASE)
    data["experience"] = [dict(BASE["experience"][0]) for _ in range(3)]
    data["projects"] = [dict(BASE["projects"][0]) for _ in range(2)]
    data["certifications"] = [{"name": "C", "issuer": "I", "date": "1"}]
    data["awards"] = [{"title": "A", "body": "B", "year": "1"}]
    data["publications"] = [{"title": "P", "venue": "V", "year": "1"}]
    data["languages"] = [{"language": "L", "proficiency": "p"}]
    order = []
    d = copy.deepcopy(data)
    # exhaust only the optional-section cuts by forcing tier-2 (min experience/projects)
    d["experience"] = d["experience"][:3]
    d["projects"] = d["projects"][:1]
    for _ in range(4):
        before = {k: (k in d) for k in ("languages", "publications", "awards", "certifications")}
        tailor._shrink_one_step(d)
        after = {k: (k in d) for k in ("languages", "publications", "awards", "certifications")}
        gone = [k for k in before if before[k] and not after[k]]
        order += gone
    assert order == ["languages", "publications", "awards", "certifications"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest test_template_sections.py -q`
Expected: FAIL — headings absent because template lacks the blocks; trimmer test fails because `_shrink_one_step` doesn't touch these keys.

- [ ] **Step 3: Edit `template.tex`**

After the Education block (`(% endif %)` that closes education), before Technical Skills, add:

```latex
(% if certifications is defined and certifications %)
\section*{Certifications}
(% for c in certifications %)
\noindent\textbf{(( c.name ))}, (( c.issuer )) \hfill (( c.date ))(% if not loop.last %) \\(% endif %)
(% endfor %)
(% endif %)
(% if awards is defined and awards %)
\section*{Awards}
(% for a in awards %)
\noindent\textbf{(( a.title ))}, (( a.body )) \hfill (( a.year ))(% if not loop.last %) \\(% endif %)
(% endfor %)
(% endif %)
(% if publications is defined and publications %)
\section*{Publications}
(% for p in publications %)
\noindent (( p.title )). \textit{(( p.venue ))}, (( p.year ))(% if not loop.last %) \\(% endif %)
(% endfor %)
(% endif %)
```

After the Technical Skills block, add:

```latex
(% if languages is defined and languages %)
\section*{Languages}
\noindent(% for l in languages %)\textbf{(( l.language ))} ((( l.proficiency )))(% if not loop.last %), (% endif %)(% endfor %)
(% endif %)
```

- [ ] **Step 4: Edit `tailor.py` schema + trimmer**

Add models near `ProjectEntry` (zip `tailor.py:86`):

```python
class CertEntry(BaseModel):
    name: str
    issuer: str
    date: str

class AwardEntry(BaseModel):
    title: str
    body: str
    year: str

class PubEntry(BaseModel):
    title: str
    venue: str
    year: str

class LangEntry(BaseModel):
    language: str
    proficiency: str
```

On `TailoredResume` (zip `tailor.py:123`) add:

```python
    certifications: Optional[List[CertEntry]] = None
    awards: Optional[List[AwardEntry]] = None
    publications: Optional[List[PubEntry]] = None
    languages: Optional[List[LangEntry]] = None
```

In `_shrink_one_step` (zip `tailor.py:948`), in Tier 2 **before** the "trim the largest skill group" step, add:

```python
    # Optional sections lose to experience and projects on a one-pager. Shed whole,
    # least-valuable first.
    for key in ("languages", "publications", "awards", "certifications"):
        if data.get(key):
            data.pop(key)
            return True
```

In `main()`, after `render_data.update(tailored)`, the optional keys ride along automatically because they're in `tailored`. They must survive the `not_content` filter for the saved JSON — they already will (only `constant_fields | {"projects_title","skills_title","recommendations"}` is stripped).

Also: the model emits these only when the source has them. Add to `tailor_content`'s `content_keys` set: `content_keys |= {k for k in ("certifications","awards","publications","languages") if k in profile.content}`.

- [ ] **Step 5: Add sample data to `examples/content.json`**

Append to the JSON object:

```json
  "certifications": [
    { "name": "CPACC", "issuer": "IAAP", "date": "2024" }
  ],
  "awards": [
    { "title": "Jury Award, Civic Tech Showcase", "body": "City of Seattle", "year": "2021" }
  ],
  "publications": [
    { "title": "Designing the 48-page service blueprint report", "venue": "Civic Signal", "year": "2021" }
  ],
  "languages": [
    { "language": "English", "proficiency": "native" },
    { "language": "Spanish", "proficiency": "conversational" }
  ]
```

- [ ] **Step 6: Run the tests + a real compile if pdflatex is present**

Run: `python3 -m pytest test_template_sections.py -q`
Expected: PASS (3 tests).
Run (best-effort): `python3 build.py` — expect a clean PDF if `examples/content.json` is the local `content.json`.

- [ ] **Step 7: Commit**

```bash
git add template.tex tailor.py examples/content.json test_template_sections.py
git commit -m "feat: certifications/awards/publications/languages sections + trim order"
```

---

## Task 7: `PreScoreAnalysis` schema + `score_jd()` (step B)

**Files:**
- Modify: `tailor.py` — `GapQuestion`, `PreScoreAnalysis` models; `score_jd()`; `SCORE_SYSTEM_PROMPT`; `_EDU_QUESTION_RE`
- Test: `test_flow.py` (new; step B portion)

**Interfaces:**
- Consumes: `llm.complete_schema`, `tailor.normalize_match_analysis`, `Profile`.
- Produces:
  - `tailor.score_jd(profile: Profile, jd: str, *, backend: str, model: str, binary=None, timeout=llm.DEFAULT_TIMEOUT) -> dict` — returns `{"overall_score": int, "requirements": [...], "gap_questions": [GapQuestion-as-dict...]}` with `overall_score` recomputed and gap questions validated. On `llm.LLMError`, returns `{"overall_score": None, "requirements": [], "gap_questions": []}` (never raises).
  - `GapQuestion` fields: `question: str`, `potential_points: int`, `target: str`, `target_requirement: str`.
  - **Gap-question grounding (spec §4 B):** `SCORE_SYSTEM_PROMPT` forbids asking for information already in the CV JSON. `score_jd` additionally drops any gap question matching `_EDU_QUESTION_RE` (degree/institution/graduation/GPA words) whenever `profile.content.get("education")` is non-empty.

- [ ] **Step 1: Write the failing test**

```python
# test_flow.py  (step B section; more added in Task 9)
import json
import pytest
import tailor
import llm
from profile import Profile

CONSTANT = {"name": "Alex Rivera", "email": "a@x.com", "phone": "+1", "linkedin": "in/a",
            "location": "Seattle, WA", "portfolios": "example.com", "solo_worker": False,
            "banned_summary_anchors": [], "skills_title": "Skills"}
CONTENT = {
    "portfolio_link": "example.com", "summary": "s",
    "experience": [{"company": "Northgate", "title": "PD", "dates": "2023--", "location": "S", "notes": "", "bullets": ["b"]}],
    "projects": [{"title": "Transit Board", "notes": "", "bullets": ["b"]}],
    "skills": [{"label": "Design Skills", "entries": ["Figma"]}],
}

def _profile():
    return Profile.from_dicts(CONSTANT, CONTENT)

def _fake_schema_reply(payload):
    def fake(*, model, system, user, schema, thinking, binary, timeout, on_retry=None):
        return schema.model_validate(payload).model_dump(exclude_none=True), llm.Reply(json.dumps(payload), finish="stop")
    return fake

GOOD = {
    "overall_score": 999,   # deliberately wrong -> must be recomputed
    "requirements": [
        {"requirement": "Kubernetes", "weight": 20, "status": "missing", "evidence": [], "gap_or_talking_point": "no evidence"},
        {"requirement": "Design systems", "weight": 80, "status": "direct", "evidence": ["Northgate"], "gap_or_talking_point": ""},
    ],
    "gap_questions": [
        {"question": "Have you run K8s in production?", "potential_points": 20, "target": "experience:Northgate", "target_requirement": "Kubernetes"},
        {"question": "Any infra work?", "potential_points": 500, "target": "nonsense:x", "target_requirement": "Kubernetes"},
    ],
}

def test_score_jd_recomputes_score_and_filters_gap_questions(monkeypatch):
    monkeypatch.setattr(llm, "complete_schema", _fake_schema_reply(GOOD))
    out = tailor.score_jd(_profile(), "jd text", backend="claude", model="claude-haiku-4-5")
    assert out["overall_score"] == 80          # 80 direct + 0 missing, /100
    assert len(out["gap_questions"]) == 1      # bad target dropped
    assert out["gap_questions"][0]["potential_points"] == 20

def test_score_jd_swallows_llm_error(monkeypatch):
    def boom(**k): raise llm.LLMError("down")
    monkeypatch.setattr(llm, "complete_schema", boom)
    out = tailor.score_jd(_profile(), "jd", backend="claude", model="claude-haiku-4-5")
    assert out == {"overall_score": None, "requirements": [], "gap_questions": []}

def test_score_jd_drops_education_questions_when_education_present(monkeypatch):
    payload = {
        "overall_score": 0,
        "requirements": [{"requirement": "Degree", "weight": 20, "status": "missing", "evidence": [], "gap_or_talking_point": ""}],
        "gap_questions": [
            {"question": "What is your bachelor's degree, institution, and graduation date?",
             "potential_points": 20, "target": "general", "target_requirement": "Degree"},
            {"question": "Which React projects can you document with dates?",
             "potential_points": 10, "target": "project:Transit Board", "target_requirement": "React"},
        ],
    }
    monkeypatch.setattr(llm, "complete_schema", _fake_schema_reply(payload))
    content = {**CONTENT, "education": [
        {"institution": "UW", "degree": "B.Des", "gpa": "3.8", "start": "2015", "end": "2019", "location": "S"}]}
    prof = Profile.from_dicts(CONSTANT, content)
    out = tailor.score_jd(prof, "Bachelor's degree required", backend="claude", model="claude-haiku-4-5")
    qs = [g["question"] for g in out["gap_questions"]]
    assert not any("bachelor" in q.lower() or "degree" in q.lower() for q in qs)
    assert any("React" in q for q in qs)

def test_score_jd_keeps_education_questions_when_no_education(monkeypatch):
    payload = {
        "overall_score": 0,
        "requirements": [{"requirement": "Degree", "weight": 20, "status": "missing", "evidence": [], "gap_or_talking_point": ""}],
        "gap_questions": [
            {"question": "What is your degree and institution?", "potential_points": 20,
             "target": "general", "target_requirement": "Degree"}],
    }
    monkeypatch.setattr(llm, "complete_schema", _fake_schema_reply(payload))
    out = tailor.score_jd(_profile(), "Bachelor's degree required", backend="claude", model="claude-haiku-4-5")
    assert len(out["gap_questions"]) == 1   # no education array -> the question stands
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest test_flow.py -q`
Expected: FAIL — `AttributeError: module 'tailor' has no attribute 'score_jd'`.

- [ ] **Step 3: Implement in `tailor.py`**

Add models next to `MatchAnalysis`:

```python
class GapQuestion(BaseModel):
    question: str
    potential_points: int
    target: str
    target_requirement: str

class PreScoreAnalysis(BaseModel):
    overall_score: int
    requirements: List[RequirementAssessment]
    gap_questions: List[GapQuestion]
```

Add the prompt and function (near `tailor_content`):

```python
SCORE_SYSTEM_PROMPT = """Score this candidate's existing CV JSON against the job description. Do not write a resume.

Extract 6-10 material requirements from the JD; assign each a weight so weights total 100. Weight hiring gates as gates (a required credential or minimum-years gate 15-25; the defining domain 10-20; a single tool 5-8). Mark each requirement:
- direct   — the CV JSON explicitly proves it
- adjacent — transferable evidence exists but the exact requirement is not proved
- missing  — no meaningful evidence
Do not award direct for keyword similarity.

Then produce 3-4 gap_questions targeting the highest-weight missing or adjacent requirements — short questions whose answer, if positive, would move that requirement up. For each: potential_points = the requirement's weight if it is missing, or half (rounded) if adjacent; target = the exact "company" of an experience entry as "experience:<company>", the exact "title" of a project as "project:<title>", or "general"; target_requirement = the requirement text.

NEVER ask for information the CV JSON already contains. If the education array is populated, do not ask about degree, institution, graduation date, or GPA. If a role or project already states a fact, do not ask for it.

Return only the schema."""

# Education facts live in the "education" array; when it is populated, a question
# asking for a degree/school/grad-date is asking for data we already have. Scoped
# to education on purpose — it is the observed failure and a bounded, safe filter.
_EDU_QUESTION_RE = re.compile(
    r"\b(degree|bachelor'?s?|master'?s?|graduat\w*|institution|universit\w*|college|GPA|alma mater)\b", re.I)

def _known_note_targets(profile: "Profile") -> set:
    t = {"general"}
    for e in profile.content.get("experience", []):
        if e.get("company"): t.add(f"experience:{e['company']}")
    for p in profile.content.get("projects", []):
        if p.get("title"): t.add(f"project:{p['title']}")
    return t

def score_jd(profile, jd, *, backend, model, binary=None, timeout=llm.DEFAULT_TIMEOUT):
    system = (SCORE_SYSTEM_PROMPT
              + f"\n\nHere is the candidate's CV (JSON):\n"
              + json.dumps({k: profile.content.get(k) for k in ('summary','experience','projects','skills','education')}, separators=(',', ':')))
    user = f"Here is the job description:\n---\n{jd}\n---\nReturn the score and gap questions."
    try:
        data, _ = llm.complete_schema(model=model, system=system, user=user,
                                      schema=PreScoreAnalysis, thinking="low",
                                      binary=binary, timeout=timeout)
    except llm.LLMError as e:
        print(f"Warning   : baseline scoring failed ({str(e)[:120]}); continuing without it.")
        return {"overall_score": None, "requirements": [], "gap_questions": []}

    data["requirements"] = data.get("requirements") or []
    normalized = normalize_match_analysis({"requirements": data["requirements"]})
    known = _known_note_targets(profile)
    has_education = bool(profile.content.get("education"))
    gaps = []
    for g in data.get("gap_questions") or []:
        if g.get("target") not in known:
            continue
        if has_education and _EDU_QUESTION_RE.search(g.get("question", "")):
            continue  # that data is already in content.json — asking for it is noise
        g["potential_points"] = max(0, min(100, int(g.get("potential_points", 0))))
        gaps.append(g)
    return {"overall_score": normalized["overall_score"],
            "requirements": data["requirements"], "gap_questions": gaps}
```

Note: `score_jd` passes `model=` (already resolved by `llm.score_model`) straight to `complete_schema`; `backend` is accepted for signature symmetry and future use and is currently unused inside — keep the parameter (the caller always has it) but it is fine that the body relies on `complete_schema`'s own routing via the resolved id. If `complete_schema` in the zip requires a `backend=` kwarg, pass it through.

*Implementer: check `llm.complete_schema`'s real signature in the zip (`llm.py:381`) and match it — the zip added `backend=` to these functions. Pass `backend=backend` if so.*

- [ ] **Step 4: Run the test**

Run: `python3 -m pytest test_flow.py -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add tailor.py test_flow.py
git commit -m "feat: score_jd — pre-generation baseline score + gap questions"
```

---

## Task 8: `distill_note()` + gap-answer write-back (step C helpers)

**Files:**
- Modify: `tailor.py` — `distill_note()`, `collect_gap_answers()`
- Test: `test_flow.py` (append)

**Interfaces:**
- Consumes: `llm.complete_text`, `ui.editor`, `ui.confirm`, `ui.gap_table`, `Profile.append_note`.
- Produces:
  - `tailor.distill_note(question: str, answer: str, *, backend: str, model: str, binary=None, timeout=...) -> str` — ≤2-sentence factual note; returns `""` for a blank answer.
  - `tailor.collect_gap_answers(profile: Profile, gap_questions: list, *, backend: str, model: str, binary=None) -> list[tuple[str, int]]` — prompts per question, distills, calls `profile.append_note`, returns `folded` list of `(question, points)` actually incorporated. Skips entirely (returns `[]`) when `not ui.interactive()` or the user declines the `ui.confirm`.

- [ ] **Step 1: Write the failing test (append to `test_flow.py`)**

```python
import ui

def test_distill_note_blank_answer_returns_empty(monkeypatch):
    monkeypatch.setattr(llm, "complete_text", lambda **k: (_ for _ in ()).throw(AssertionError("should not call")))
    assert tailor.distill_note("q", "   ", backend="claude", model="m") == ""

def test_distill_note_calls_model(monkeypatch):
    monkeypatch.setattr(llm, "complete_text", lambda **k: llm.Reply("Led K8s migration for 12 services.", finish="stop"))
    out = tailor.distill_note("Have you run K8s?", "yeah at Northgate we moved 12 svcs", backend="claude", model="m")
    assert out == "Led K8s migration for 12 services."

def test_collect_gap_answers_writes_notes_and_returns_folded(tmp_path, monkeypatch):
    (tmp_path / "constant.json").write_text(json.dumps(CONSTANT))
    (tmp_path / "content.json").write_text(json.dumps(CONTENT, indent=2))
    prof = Profile.load(tmp_path)
    gqs = [{"question": "K8s in prod?", "potential_points": 20, "target": "experience:Northgate", "target_requirement": "Kubernetes"},
           {"question": "Infra work?", "potential_points": 5, "target": "general", "target_requirement": "Infra"}]
    monkeypatch.setattr(ui, "interactive", lambda: True)
    monkeypatch.setattr(ui, "confirm", lambda *a, **k: True)
    monkeypatch.setattr(ui, "gap_table", lambda *a, **k: None)
    answers = iter(["ran 12 services on EKS", ""])   # second one skipped
    monkeypatch.setattr(ui, "editor", lambda *a, **k: next(answers))
    monkeypatch.setattr(tailor, "distill_note", lambda q, a, **k: f"NOTE:{a}" if a.strip() else "")
    folded = tailor.collect_gap_answers(prof, gqs, backend="claude", model="m")
    assert folded == [("K8s in prod?", 20)]
    reloaded = json.loads((tmp_path / "content.json").read_text())
    assert reloaded["experience"][0]["notes"].endswith("NOTE:ran 12 services on EKS")
    assert "notes" not in reloaded or reloaded.get("notes") in ("", None)

def test_collect_gap_answers_declined(monkeypatch):
    monkeypatch.setattr(ui, "interactive", lambda: True)
    monkeypatch.setattr(ui, "confirm", lambda *a, **k: False)
    assert tailor.collect_gap_answers(_profile(), [{"question": "x", "potential_points": 1, "target": "general", "target_requirement": "y"}], backend="claude", model="m") == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest test_flow.py -q`
Expected: FAIL — `distill_note` / `collect_gap_answers` not defined.

- [ ] **Step 3: Implement in `tailor.py`**

```python
import ui  # near the other imports

DISTILL_SYSTEM_PROMPT = (
    "Rewrite the applicant's answer as a resume source note. Constraints: at most 2 "
    "sentences; factual; keep concrete numbers; no first person; no marketing words "
    "(passionate, robust, seamless, spearheaded). Output the note text only, nothing else."
)

def distill_note(question, answer, *, backend, model, binary=None, timeout=llm.DEFAULT_TIMEOUT):
    answer = (answer or "").strip()
    if not answer:
        return ""
    reply = llm.complete_text(model=model, system=DISTILL_SYSTEM_PROMPT,
                              user=f"Question asked: {question}\nApplicant's answer: {answer}",
                              thinking="low", binary=binary, timeout=timeout)
    return reply.text.strip()

def collect_gap_answers(profile, gap_questions, *, backend, model, binary=None):
    if not gap_questions or not ui.interactive():
        return []
    if not ui.confirm("Answer questions to raise your match score?", default=False):
        return []
    ui.gap_table(gap_questions)
    folded = []
    for i, g in enumerate(gap_questions, 1):
        raw = ui.editor(f"A{i}", default="")
        note = distill_note(g["question"], raw, backend=backend, model=model, binary=binary)
        if not note:
            continue
        profile.append_note(g["target"], note)
        folded.append((g["question"], int(g["potential_points"])))
    if folded:
        print(f"Updated content.json: {len(folded)} note(s) added")
    return folded
```

*(Pass `backend=backend` into `llm.complete_text` too if the zip's signature requires it.)*

- [ ] **Step 4: Run the test**

Run: `python3 -m pytest test_flow.py -q`
Expected: PASS (6 tests total in file).

- [ ] **Step 5: Commit**

```bash
git add tailor.py test_flow.py
git commit -m "feat: gap-answer collection distilled into content.json notes"
```

---

## Task 9: Wire A→E into `main()` + new flags + report + education decision

**Files:**
- Modify: `tailor.py` — `parse_args` (add `--no-interactive/-y`, `--score-preset`); `jd_requires_degree()`, `decide_education()`, `FIRM_EDUCATION_RULE`; `tailor_content` uses `FIRM_EDUCATION_RULE` when `include_education`; `main()` orchestration incl. the D0 education decision; final report via `ui.report`
- Test: `test_flow.py` (append end-to-end-ish with everything mocked)

**Interfaces:**
- Consumes: `score_jd`, `collect_gap_answers`, `llm.score_model`, `ui.score_panel`, `ui.confirm`, `ui.interactive`, `ui.report`, `Profile.load`.
- Produces:
  - `tailor.jd_requires_degree(jd: str) -> bool`
  - `tailor.decide_education(profile: Profile, jd: str, args) -> tuple[bool, str]` — `(include_education, status_line)` per spec §4 D0; resume mode only.
  - CLI behavior — interactive path runs B (always), C (if TTY & confirmed), D0 (education decision, always printed in resume mode), D, E (prompt default-No, or forced by `-c`); straight-through path when `-y` or piped.

- [ ] **Step 1: Write the failing test (append)**

```python
def test_argparse_has_new_flags():
    import argparse, contextlib, io
    ns = tailor.parse_args.__wrapped__() if hasattr(tailor.parse_args, "__wrapped__") else None
    # simplest: parse a known argv
    import sys
    argv = ["tailor.py", "jd.txt", "-y", "--score-preset", "haiku"]
    monkey = sys
    old = sys.argv
    try:
        sys.argv = argv
        args = tailor.parse_args()
    finally:
        sys.argv = old
    assert args.no_interactive is True
    assert args.score_preset == "haiku"

def test_main_straight_through_when_no_interactive(tmp_path, monkeypatch, capsys):
    # full offline wiring: Profile from tmp, all model + compile steps stubbed
    (tmp_path / "constant.json").write_text(json.dumps({**CONSTANT, "backend": "claude"}))
    (tmp_path / "content.json").write_text(json.dumps(CONTENT, indent=2))
    monkeypatch.setattr(tailor, "ROOT", tmp_path)
    monkeypatch.setattr(tailor.Profile, "load", classmethod(lambda cls, root, **k: Profile.from_dicts(
        json.loads((tmp_path / "constant.json").read_text()),
        json.loads((tmp_path / "content.json").read_text()), root=tmp_path, **k)))
    monkeypatch.setattr(llm, "resolve", lambda *a, **k: ("claude", "claude-sonnet-5"))
    monkeypatch.setattr(llm, "score_model", lambda *a, **k: ("claude", "claude-haiku-4-5"))
    monkeypatch.setattr(tailor, "score_jd", lambda *a, **k: {"overall_score": 70, "requirements": [], "gap_questions": [{"question": "q", "potential_points": 9, "target": "general", "target_requirement": "r"}]})
    called = {}
    monkeypatch.setattr(tailor, "collect_gap_answers", lambda *a, **k: called.setdefault("C", True) or [])
    monkeypatch.setattr(tailor, "tailor_content", lambda *a, **k: {
        "company_name": "Meridian", "job_title": "PD", "portfolio_link": "example.com",
        "summary": "s", "experience": [{"company": "N", "title": "PD", "dates": "d", "location": "l", "bullets": ["b"]}],
        "projects": [{"title": "P", "bullets": ["b"]}], "skills": [{"label": "Design Skills", "entries": ["Figma"]}],
        "match_analysis": {"requirements": [{"requirement": "x", "weight": 100, "status": "direct"}],
                           "overall_score": 0, "strongest_matches": [], "improvements": [], "interview_talking_points": [],
                           "inferred_skills": [], "clarifying_questions": []}})
    monkeypatch.setattr(tailor, "compile_pdf", lambda data, *a, **k: (tmp_path / "out.pdf", data))
    monkeypatch.setattr(tailor, "check_integrity", lambda *a, **k: [])
    monkeypatch.setattr("sys.stdin", type("S", (), {"isatty": staticmethod(lambda: False), "read": staticmethod(lambda: "")})())
    import sys as _s
    old = _s.argv
    try:
        _s.argv = ["tailor.py", str(tmp_path / "jd.txt"), "-y"]
        (tmp_path / "jd.txt").write_text("Senior Product Designer, patient experience. Seattle.")
        tailor.main()
    finally:
        _s.argv = old
    assert "C" not in called                 # straight-through skipped gap questions
    out = capsys.readouterr().out
    assert "70" in out and "baseline" in out.lower()
```

*(This test is deliberately heavy; if wiring it proves brittle, split `main()` so the orchestration is a testable `run(args, profile)` function and test that instead. Recommended: extract `run(args) -> None` called by `main()`.)*

Also add tests for the education decision (spec §4 D0):

```python
def test_jd_requires_degree_detects_common_phrasings():
    for jd in ["Bachelor's degree from an accredited institution",
               "BS in Computer Science or related discipline",
               "requires a Master's degree", "undergraduate degree required",
               "Educational Qualifications: Bachelor's degree in Engineering"]:
        assert tailor.jd_requires_degree(jd) is True
    for jd in ["5+ years building web apps", "Strong React and TypeScript skills",
               "You will mentor other engineers"]:
        assert tailor.jd_requires_degree(jd) is False

def test_decide_education_matrix():
    edu = [{"institution": "UW", "degree": "B.Des", "gpa": "3.8", "start": "2015", "end": "2019", "location": "S"}]
    P = lambda has_edu: Profile.from_dicts(CONSTANT, {**CONTENT, **({"education": edu} if has_edu else {})})
    A = lambda **kw: type("A", (), {"education": False, "extended_education": False,
                                    "no_interactive": True, **kw})()
    # -ee / -e force include
    assert tailor.decide_education(P(True), "no degree talk", A(extended_education=True))[0] is True
    assert tailor.decide_education(P(True), "no degree talk", A(education=True))[0] is True
    # JD requires + data present -> include
    inc, line = tailor.decide_education(P(True), "Bachelor's degree required", A())
    assert inc is True and "JD requires a degree" in line
    # JD requires + no data -> exclude + warning
    inc, line = tailor.decide_education(P(False), "Bachelor's degree required", A())
    assert inc is False and "WARNING" in line
    # JD doesn't require, non-interactive -> exclude
    inc, line = tailor.decide_education(P(True), "React skills", A())
    assert inc is False and "omitted" in line

def test_decide_education_prompts_when_jd_silent_and_interactive(monkeypatch):
    edu = [{"institution": "UW", "degree": "B.Des", "gpa": "3.8", "start": "2015", "end": "2019", "location": "S"}]
    prof = Profile.from_dicts(CONSTANT, {**CONTENT, "education": edu})
    args = type("A", (), {"education": False, "extended_education": False, "no_interactive": False})()
    monkeypatch.setattr(ui, "interactive", lambda: True)
    monkeypatch.setattr(ui, "confirm", lambda *a, **k: True)
    inc, line = tailor.decide_education(prof, "React skills, no degree mentioned", args)
    assert inc is True and "your choice" in line
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest test_flow.py -q`
Expected: FAIL — `args.no_interactive`, `tailor.jd_requires_degree`, `tailor.decide_education` missing.

- [ ] **Step 3: Edit `parse_args`**

Add after the `--recommendations` argument:

```python
    parser.add_argument(
        "--no-interactive", "-y", action="store_true",
        help="Straight through: compute the baseline score but skip the gap-question and cover-letter prompts.",
    )
    parser.add_argument(
        "--score-preset", metavar="NAME",
        help="Model preset for the baseline score step. Default: one tier below the run model.",
    )
```

- [ ] **Step 4a: Add the education decision (spec §4 D0)**

Near the top helpers of `tailor.py` (by `default_location_for_jd` / the regexes):

```python
_DEGREE_RE = re.compile(
    r"\b(bachelor'?s?|master'?s?|B\.?S\.?|B\.?A\.?|BSc|M\.?S\.?|MSc|"
    r"undergraduate degree|degree required|degree in |degree from|"
    r"educational qualification|accredited institution)\b", re.I)

def jd_requires_degree(jd: str) -> bool:
    return bool(_DEGREE_RE.search(jd or ""))

# Firm replacement for the old conditional EDUCATION_RULE. The Python gate below
# now owns the include/exclude decision; when we say include, the model includes.
FIRM_EDUCATION_RULE = ("\nEDUCATION: an \"education\" array is provided in the CV JSON. "
                       "It MUST appear as an Education section in the output. Do not omit it, "
                       "and do not second-guess this instruction against the JD.")

def decide_education(profile, jd, args):
    """(include_education: bool, status_line: str) per spec §4 D0. Resume mode only;
    CV mode always shows education and never calls this."""
    has_edu = bool(profile.content.get("education"))
    if getattr(args, "extended_education", False):
        return True, "Education  : included with coursework (-ee)"
    if getattr(args, "education", False):
        return True, "Education  : included (-e)"
    if jd_requires_degree(jd):
        if has_edu:
            return True, "Education  : included — the JD requires a degree"
        return False, "Education  : WARNING — JD requires a degree but content.json has none. Add it."
    if not getattr(args, "no_interactive", False) and ui.interactive():
        if ui.confirm("The JD doesn't mention a degree — include education anyway?", default=False):
            return True, "Education  : included (your choice)"
    return False, "Education  : omitted (JD doesn't require it)"
```

In `tailor_content`, when `include_education` is true, append `FIRM_EDUCATION_RULE` (not the old conditional `EDUCATION_RULE`) to the system prompt, and keep the existing `content_keys.add("education")` so the model actually receives the array. `-ee` coursework injection in `main()` is unchanged.

- [ ] **Step 4: Edit `main()` — the A→E orchestration**

Delete the old `model_education = args.education and mode == "resume" and not args.extended_education` line. Replace the linear body around `tailor_content(...)` with:

```python
    profile = Profile.load(ROOT, mode=mode)
    try:
        backend, model = llm.resolve(args.preset, model=args.model, backend=args.provider or profile.backend)
    except llm.LLMError as e:
        print(f"Error: {e}"); sys.exit(1)
    score_backend, score_id = llm.score_model(
        backend, args.preset, args.model, override_preset=args.score_preset)

    print(f"Mode      : {mode.upper()} (source: {CONTENT_FILE[mode]})")
    print(f"Model     : {model} via {backend}" + (f" (thinking={args.thinking})" if backend == "openclaw" else ""))

    # ── B. baseline score ──
    pre = score_jd(profile, jd, backend=score_backend, model=score_id,
                   binary=args.binary, timeout=args.timeout)
    baseline = pre["overall_score"]
    if baseline is not None:
        ui.score_panel(baseline, pre["requirements"])

    # ── C. gap questions (interactive, default No) ──
    folded = []
    if not args.no_interactive:
        folded = collect_gap_answers(profile, pre["gap_questions"],
                                     backend=score_backend, model=score_id, binary=args.binary)
        if folded:
            profile = Profile.load(ROOT, mode=mode)   # pick up the note writes

    # ── D0. education decision (resume mode; CV mode always shows it) ──
    if mode == "resume":
        include_education, edu_line = decide_education(profile, jd, args)
        print(edu_line)
    else:
        include_education = False   # CV mode injects education directly, as today

    # ── D. generate ──
    tailored = tailor_content(profile, jd, mode=mode, include_education=include_education,
                              extra_context=extra_context, backend=backend, model=model,
                              thinking=args.thinking, binary=args.binary, timeout=args.timeout)
    tailored = enforce_portfolio(tailored, jd, profile)
    tailored = enforce_caps(tailored)
    match_analysis = normalize_match_analysis(tailored.pop("match_analysis", {}))
```

Keep the existing company/job_title/escape/render_data/output block. Then:

```python
    # ── E. cover letter (prompt default No; -c forces it) ──
    want_cl = args.cover_letter or (not args.no_interactive and ui.interactive()
                                    and ui.confirm("Generate a cover-letter prompt?", default=False))
    if want_cl:
        ... existing cover-letter generation ...
```

Replace the final `improvements` / `clarifying_questions` / `check_integrity` prints with:

```python
    ui.report(baseline=baseline,
              tailored=match_analysis.get("overall_score", 0),
              folded=folded,
              improvements=match_analysis.get("improvements", []),
              questions=match_analysis.get("clarifying_questions", []),
              warnings=check_integrity(shipped, profile.content, match_analysis, profile=profile))
    print("\nDone.")
```

Recommended: wrap all of the above (from `profile = Profile.load` to `print("\nDone.")`) in `def run(args): ...` and have `main()` be `args = parse_args(); jd = ...; run(args)` so `test_flow.py` can call `run` with a prepared `args` namespace.

- [ ] **Step 5: Run the suite**

Run: `python3 -m pytest -q`
Expected: PASS (all prior + `test_flow.py`).

- [ ] **Step 6: Commit**

```bash
git add tailor.py test_flow.py
git commit -m "feat: A->E flow in tailor.main — score, ask gaps, generate, offer cover letter"
```

---

## Task 10: Bring `recompile.py` into the package (de-personalized, plain recompile)

**Files:**
- Create: `recompile.py` (adapted from the working-dir version in `/private/tmp/resume-tex-prezip/recompile.py`)
- Test: `test_recompile.py`

**Interfaces:**
- Consumes: `tailor.compile_to_dir`, `tailor.compile_single_page`, `tailor.render_tex`, `Profile.load`, `profile.doc_basename`.
- Produces:
  - `recompile.recompile_folder(app_dir: Path, *, root: Path, mode: str = "resume") -> Path` — renders the folder's `content.<mode>.json` merged with root `constant.json` identity, returns the PDF path. No model call.
  - CLI: `python3 recompile.py [folder] [--cv] [-i]`.

- [ ] **Step 1: Write the failing test**

```python
# test_recompile.py
import json
import pytest
import recompile
from profile import Profile

CONSTANT = {"name": "Alex Rivera", "email": "a@x.com", "phone": "+1", "linkedin": "in/a",
            "location": "Seattle, WA", "portfolios": "example.com", "doc_basename": "Alex_Rivera"}
FOLDER_JSON = {
    "portfolio_link": "example.com", "summary": "s",
    "experience": [{"company": "N", "title": "PD", "dates": "2023--", "location": "S", "bullets": ["b1", "b2"]}],
    "projects": [{"title": "P", "bullets": ["b1"]}],
    "skills": [{"label": "Design Skills", "entries": ["Figma"]}],
}

def test_recompile_folder_no_model_call(tmp_path, monkeypatch):
    root = tmp_path / "pkg"; root.mkdir()
    (root / "constant.json").write_text(json.dumps(CONSTANT))
    (root / "template.tex").write_text((tmp_path / "..").resolve().joinpath().as_posix() and open("template.tex").read())
    app = tmp_path / "app"; app.mkdir()
    (app / "content.resume.json").write_text(json.dumps(FOLDER_JSON, indent=2))
    captured = {}
    monkeypatch.setattr(recompile, "compile_to_dir", lambda data, dest, filename="resume.pdf": captured.setdefault("data", data) or 1)
    import llm
    monkeypatch.setattr(llm, "run_once", lambda **k: (_ for _ in ()).throw(AssertionError("model called")))
    recompile.recompile_folder(app, root=root)
    assert captured["data"]["name"] == "Alex Rivera"
    assert captured["data"]["experience"][0]["bullets"] == ["b1", "b2"]

def test_missing_folder_json_exits(tmp_path):
    root = tmp_path / "pkg"; root.mkdir()
    (root / "constant.json").write_text(json.dumps(CONSTANT))
    with pytest.raises(SystemExit):
        recompile.recompile_folder(tmp_path / "empty", root=root)
```

*(The `template.tex` line above is awkward; simplify in implementation by having `recompile.recompile_folder` accept `root` and read `root/"template.tex"` — in the test just `shutil.copy` the real `template.tex` into `root`.)*

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest test_recompile.py -q`
Expected: FAIL — `No module named 'recompile'`.

- [ ] **Step 3: Write `recompile.py`**

```python
#!/usr/bin/env python3
"""Recompile a saved application from its edited JSON. No model call.

    python3 recompile.py <Company>/<Role>/          # re-render from content.resume.json
    python3 recompile.py <folder> --cv              # from content.cv.json
    python3 recompile.py <folder> -i                # menu to merge in optional sections
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from profile import Profile
import tailor


def _folder_json_name(mode: str) -> str:
    return "content.cv.json" if mode == "cv" else "content.resume.json"


def recompile_folder(app_dir: Path, *, root: Path = ROOT, mode: str = "resume") -> Path:
    app_dir = Path(app_dir).expanduser().resolve()
    src = app_dir / _folder_json_name(mode)
    if not src.exists():
        print(f"Error: {src.name} not found in {app_dir}")
        sys.exit(1)
    profile = Profile.load(root, mode=mode)
    content = json.loads(src.read_text())
    content.pop("company_name", None)
    content.pop("job_title", None)

    data = dict(profile.identity)
    data["location"] = content.get("location") or profile.location
    data["skills_title"] = profile.skills_title
    data["projects_title"] = profile.projects_title
    data.update(content)

    filename = f"{profile.doc_basename}_{'CV' if mode == 'cv' else 'Resume'}.pdf"
    if mode == "resume":
        pages, _ = tailor.compile_single_page(data, app_dir, filename)
    else:
        pages = tailor.compile_to_dir(data, app_dir, filename)
    pdf = app_dir / filename
    print(f"Saved PDF : {pdf}")
    print("Pages     : 1 (single page OK)" if pages == 1 else f"Warning   : {pages} pages")
    return pdf


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", nargs="?", default=".", help="application folder (default: CWD)")
    ap.add_argument("--cv", action="store_true", help="use content.cv.json")
    ap.add_argument("-i", "--interactive", action="store_true", help="menu to add optional sections")
    args = ap.parse_args()
    mode = "cv" if args.cv else "resume"
    app_dir = Path(args.folder).expanduser().resolve()
    if args.interactive:
        from recompile_menu import run_menu   # Task 11
        run_menu(app_dir, root=ROOT, mode=mode)
    recompile_folder(app_dir, root=ROOT, mode=mode)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the test**

Run: `python3 -m pytest test_recompile.py -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add recompile.py test_recompile.py
git commit -m "feat: recompile.py — re-render a saved application from its edited JSON"
```

---

## Task 11: `recompile -i` — merge optional sections (no model call)

**Files:**
- Create: `recompile_menu.py`
- Modify: `recompile.py` — already imports `recompile_menu.run_menu`
- Test: `test_recompile.py` (append)

**Interfaces:**
- Consumes: `ui.checkbox`, `ui.editor`, `Profile.load`, `coursework.json` / `recommendations.json` loaders from `tailor`.
- Produces: `recompile_menu.run_menu(app_dir: Path, *, root: Path, mode: str) -> None` — mutates `app_dir/content.<mode>.json` in place with any selected sections, no model call, no compile (the caller compiles after).

- [ ] **Step 1: Write the failing test (append to `test_recompile.py`)**

```python
import ui

def test_menu_merges_from_master_content(tmp_path, monkeypatch):
    root = tmp_path / "pkg"; root.mkdir()
    master = {
        **CONSTANT,
    }
    (root / "constant.json").write_text(json.dumps(CONSTANT))
    (root / "content.json").write_text(json.dumps({
        **FOLDER_JSON,
        "certifications": [{"name": "CPACC", "issuer": "IAAP", "date": "2024"}],
        "languages": [{"language": "English", "proficiency": "native"}],
    }, indent=2))
    app = tmp_path / "app"; app.mkdir()
    (app / "content.resume.json").write_text(json.dumps(FOLDER_JSON, indent=2))
    import recompile_menu
    monkeypatch.setattr(ui, "checkbox", lambda *a, **k: ["Certifications", "Languages"])
    recompile_menu.run_menu(app, root=root, mode="resume")
    merged = json.loads((app / "content.resume.json").read_text())
    assert merged["certifications"][0]["name"] == "CPACC"
    assert merged["languages"][0]["language"] == "English"

def test_menu_prompts_when_master_lacks_section(tmp_path, monkeypatch):
    root = tmp_path / "pkg"; root.mkdir()
    (root / "constant.json").write_text(json.dumps(CONSTANT))
    (root / "content.json").write_text(json.dumps(FOLDER_JSON, indent=2))
    app = tmp_path / "app"; app.mkdir()
    (app / "content.resume.json").write_text(json.dumps(FOLDER_JSON, indent=2))
    import recompile_menu
    monkeypatch.setattr(ui, "checkbox", lambda *a, **k: ["Awards"])
    monkeypatch.setattr(ui, "editor", lambda *a, **k: "Jury Award | CHI | 2023")
    recompile_menu.run_menu(app, root=root, mode="resume")
    merged = json.loads((app / "content.resume.json").read_text())
    assert merged["awards"] == [{"title": "Jury Award", "body": "CHI", "year": "2023"}]

def test_menu_no_selection_is_noop(tmp_path, monkeypatch):
    root = tmp_path / "pkg"; root.mkdir()
    (root / "constant.json").write_text(json.dumps(CONSTANT))
    (root / "content.json").write_text(json.dumps(FOLDER_JSON, indent=2))
    app = tmp_path / "app"; app.mkdir()
    (app / "content.resume.json").write_text(json.dumps(FOLDER_JSON, indent=2))
    import recompile_menu
    monkeypatch.setattr(ui, "checkbox", lambda *a, **k: [])
    recompile_menu.run_menu(app, root=root, mode="resume")
    assert json.loads((app / "content.resume.json").read_text()) == FOLDER_JSON
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest test_recompile.py -q`
Expected: FAIL — `No module named 'recompile_menu'`.

- [ ] **Step 3: Write `recompile_menu.py`**

```python
"""Interactive `recompile -i`: merge optional sections into a saved application's JSON.

No model call. Pulls a section from the package's master content.json when present,
otherwise prompts for `a | b | c` lines. The caller recompiles afterward.
"""
import json
from pathlib import Path

import ui

_SECTIONS = {
    "Education": ("education", None),
    "Certifications": ("certifications", ("name", "issuer", "date")),
    "Awards": ("awards", ("title", "body", "year")),
    "Publications": ("publications", ("title", "venue", "year")),
    "Languages": ("languages", ("language", "proficiency")),
    "Recommendations": ("recommendations", None),
}


def _parse_lines(text, fields):
    out = []
    for line in (text or "").splitlines():
        parts = [p.strip() for p in line.split("|")]
        if len(parts) == len(fields) and any(parts):
            out.append(dict(zip(fields, parts)))
    return out


def run_menu(app_dir: Path, *, root: Path, mode: str) -> None:
    app_dir = Path(app_dir)
    src = app_dir / ("content.cv.json" if mode == "cv" else "content.resume.json")
    content = json.loads(src.read_text())
    master = {}
    mp = root / "content.json"
    if mp.exists():
        master = json.loads(mp.read_text())
    recs_path = root / "recommendations.json"

    picks = ui.checkbox("Add sections to this application:", list(_SECTIONS))
    if not picks:
        return

    for label in picks:
        key, fields = _SECTIONS[label]
        if label == "Recommendations":
            if recs_path.exists():
                content[key] = json.loads(recs_path.read_text())
            continue
        if master.get(key):
            content[key] = master[key]
        elif fields:
            hint = " | ".join(fields)
            content[key] = _parse_lines(ui.editor(f"{label} — one per line: {hint}"), fields)

    src.write_text(json.dumps(content, indent=2, ensure_ascii=False) + "\n")
```

- [ ] **Step 4: Run the tests**

Run: `python3 -m pytest test_recompile.py -q`
Expected: PASS (5 tests in file).

- [ ] **Step 5: Commit**

```bash
git add recompile_menu.py test_recompile.py
git commit -m "feat: recompile -i menu to merge optional sections without a model call"
```

---

## Task 12: `install.py` — deps + model-CLI auth (steps 1–2)

**Files:**
- Create: `install.py`
- Test: `test_install.py`

**Interfaces:**
- Consumes: `llm.available_backends`, `llm.DEFAULT_BINARY`, `ui.select`, `ui.confirm`.
- Produces:
  - `install.check_dependencies() -> list[str]` — returns a list of human-readable problems (empty = all good); never exits.
  - `install.setup_backend(*, which=shutil.which, run=subprocess.run) -> str` — returns `"openclaw"` or `"claude"`; exits(1) if neither CLI is present; drives the chosen CLI's connect/login.
  - `install.write_constant_key(root: Path, key: str, value) -> None` — merge one key into `constant.json` (create if absent), 2-space indent.

- [ ] **Step 1: Write the failing test**

```python
# test_install.py
import json
import subprocess
import pytest
import install


def test_check_dependencies_reports_missing_pdflatex(monkeypatch):
    monkeypatch.setattr(install.shutil, "which", lambda n: None if n == "pdflatex" else "/usr/bin/" + n)
    probs = install.check_dependencies()
    assert any("pdflatex" in p or "LaTeX" in p for p in probs)


def test_setup_backend_none_installed_exits(monkeypatch):
    monkeypatch.setattr(install.llm, "available_backends", lambda which=None: [])
    with pytest.raises(SystemExit):
        install.setup_backend(which=lambda n: None)


def test_setup_backend_openclaw_runs_connect(monkeypatch):
    monkeypatch.setattr(install.llm, "available_backends", lambda which=None: ["openclaw"])
    calls = []
    monkeypatch.setattr(install.ui, "select", lambda *a, **k: "openai")
    monkeypatch.setattr(install.ui, "confirm", lambda *a, **k: True)
    monkeypatch.setattr(install.subprocess, "run", lambda *a, **k: calls.append(a[0]) or subprocess.CompletedProcess(a[0], 0))
    b = install.setup_backend(which=lambda n: "/bin/openclaw" if n == "openclaw" else None)
    assert b == "openclaw"
    assert any("openclaw" in c[0] for c in calls)


def test_write_constant_key_merges(tmp_path):
    install.write_constant_key(tmp_path, "backend", "claude")
    install.write_constant_key(tmp_path, "solo_worker", True)
    data = json.loads((tmp_path / "constant.json").read_text())
    assert data == {"backend": "claude", "solo_worker": True}
    assert (tmp_path / "constant.json").read_text().endswith("\n")
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest test_install.py -q`
Expected: FAIL — `No module named 'install'`.

- [ ] **Step 3: Write `install.py` (steps 1–2 only for this task)**

```python
#!/usr/bin/env python3
"""One-time setup for Resume-Tex. Re-runnable — each step offers skip/redo.

    python3 install.py
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import llm
import ui

MIN_PY = (3, 9)


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
        problems.append("Ghostscript not found (optional — only used to report page fill).")
    return problems


def _pip_install():
    if ui.confirm("Install Python packages from requirements.txt now?", default=True):
        subprocess.run([sys.executable, "-m", "pip", "install", "-r", str(ROOT / "requirements.txt")])


def setup_backend(*, which=shutil.which, run=subprocess.run) -> str:
    found = llm.available_backends(which=which)
    if not found:
        print("No model CLI found. Install one:")
        print("  Claude Code:  npm install -g @anthropic-ai/claude-code   (then run `claude` to log in)")
        print("  OpenClaw:     see its install docs")
        sys.exit(1)
    backend = found[0]
    binary = llm.DEFAULT_BINARY[backend]
    if backend == "openclaw":
        provider = ui.select("Which provider should OpenClaw connect to?", ["openai", "claude"])
        if ui.confirm(f"Run `{binary}` to connect {provider} now?", default=True):
            run([binary, "connect", provider])
    else:
        if ui.confirm("Run `claude` to log in now?", default=True):
            run([binary])
    return backend


def write_constant_key(root: Path, key: str, value) -> None:
    path = root / "constant.json"
    data = json.loads(path.read_text()) if path.exists() else {}
    data[key] = value
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def main():
    print("Resume-Tex setup\n")
    for p in check_dependencies():
        print(f"  - {p}")
    _pip_install()
    backend = setup_backend()
    write_constant_key(ROOT, "backend", backend)
    # steps 3-7 appended in Tasks 13-14
    print("\nBackend recorded. Re-run install.py to continue setup.")


if __name__ == "__main__":
    main()
```

*(Implementer: verify OpenClaw's actual connect subcommand. If it differs from `openclaw connect <provider>`, use the correct one and update the test's assertion to match. The contract that matters: the chosen backend's CLI is invoked to authenticate, and `backend` lands in `constant.json`.)*

- [ ] **Step 4: Run the tests**

Run: `python3 -m pytest test_install.py -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add install.py test_install.py
git commit -m "feat: install.py — dependency check and model-CLI auth"
```

---

## Task 13: `install.py` — résumé bootstrap (step 3)

**Files:**
- Modify: `install.py` — `extract_resume_text()`, `BootstrapExtract` schema, `bootstrap_from_resume()`
- Modify: `requirements.txt` — `pdfplumber>=0.11`, `python-docx>=1.1` (append; already added rich/questionary in Task 4)
- Test: `test_install.py` (append)

**Interfaces:**
- Consumes: `llm.complete_schema`, `ui.text`.
- Produces:
  - `install.extract_resume_text(path: Path) -> str` — dispatches by suffix (`.pdf`→pdfplumber, `.docx`→python-docx, `.txt`/`.md`→read). Missing parser lib → prints `pip install …` and `sys.exit(1)`.
  - `install.BootstrapExtract` pydantic model: `identity` (name/email/phone/linkedin/location), `experience`, `projects`, `skills`, `education` (reuse `tailor`'s entry models where shapes match; `notes=""`).
  - `install.bootstrap_from_resume(path: Path, root: Path, *, backend: str, model: str) -> None` — writes `constant.json` (identity + `backend`) and `content.json`.

- [ ] **Step 1: Write the failing test (append)**

```python
def test_extract_resume_text_txt(tmp_path):
    f = tmp_path / "r.txt"; f.write_text("Alex Rivera\nProduct Designer")
    assert "Alex Rivera" in install.extract_resume_text(f)

def test_extract_resume_text_unknown_suffix(tmp_path):
    f = tmp_path / "r.rtf"; f.write_text("x")
    with pytest.raises(SystemExit):
        install.extract_resume_text(f)

def test_bootstrap_writes_both_files(tmp_path, monkeypatch):
    (tmp_path / "r.md").write_text("# Alex Rivera\nProduct designer, 5 years.")
    payload = {
        "identity": {"name": "Alex Rivera", "email": "a@x.com", "phone": "+1", "linkedin": "in/a", "location": "Seattle, WA"},
        "experience": [{"company": "Northgate", "title": "PD", "dates": "2023--", "location": "S", "bullets": ["b"]}],
        "projects": [], "skills": [{"label": "Design Skills", "entries": ["Figma"]}], "education": [],
    }
    monkeypatch.setattr(install.llm, "complete_schema",
                        lambda **k: (install.BootstrapExtract.model_validate(payload).model_dump(exclude_none=True),
                                     install.llm.Reply("{}", finish="stop")))
    install.bootstrap_from_resume(tmp_path / "r.md", tmp_path, backend="claude", model="claude-sonnet-5")
    const = json.loads((tmp_path / "constant.json").read_text())
    cont = json.loads((tmp_path / "content.json").read_text())
    assert const["name"] == "Alex Rivera" and const["backend"] == "claude"
    assert cont["experience"][0]["company"] == "Northgate"
    assert cont["experience"][0]["notes"] == ""
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest test_install.py -q`
Expected: FAIL — `extract_resume_text` / `bootstrap_from_resume` / `BootstrapExtract` not defined.

- [ ] **Step 3: Implement in `install.py`**

```python
from tailor import ExperienceEntry, ProjectEntry, SkillGroup, EducationEntry
from pydantic import BaseModel


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
    "if the text does not state it. Return the schema only."
)


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
    print(f"Unsupported résumé file type: {suffix}. Use .pdf, .docx, .txt, or .md.")
    sys.exit(1)


def bootstrap_from_resume(path: Path, root: Path, *, backend: str, model: str) -> None:
    text = extract_resume_text(Path(path))
    data, _ = llm.complete_schema(model=model, system=BOOTSTRAP_SYSTEM,
                                  user=text[:20000], schema=BootstrapExtract, thinking="low")
    ident = data["identity"]
    constant = {**{k: ident.get(k, "") for k in ("name", "email", "phone", "linkedin", "location")},
                "backend": backend, "portfolios": "", "solo_worker": False,
                "banned_summary_anchors": [], "skills_title": "Skills",
                "doc_basename": ident.get("name", "Resume").replace(" ", "_"), "output_dir": ""}
    content = {"portfolio_link": "", "summary": ""}
    for key in ("experience", "projects", "skills", "education"):
        items = data.get(key, [])
        if key in ("experience", "projects"):
            for it in items:
                it.setdefault("notes", "")
        content[key] = items
    (root / "constant.json").write_text(json.dumps(constant, indent=2, ensure_ascii=False) + "\n")
    (root / "content.json").write_text(json.dumps(content, indent=2, ensure_ascii=False) + "\n")
```

Append `pdfplumber>=0.11` and `python-docx>=1.1` to `requirements.txt`.

- [ ] **Step 4: Run the tests**

Run: `python3 -m pytest test_install.py -q`
Expected: PASS (7 tests in file).

- [ ] **Step 5: Commit**

```bash
git add install.py requirements.txt test_install.py
git commit -m "feat: install.py — bootstrap content.json from a resume file"
```

---

## Task 14: `install.py` — optional sections, preferences, notes walkthrough (steps 4–7)

**Files:**
- Modify: `install.py` — `choose_optional_sections()`, `preferences_walkthrough()`, `notes_walkthrough()`, full `main()` wiring with per-step skip/redo
- Test: `test_install.py` (append)

**Interfaces:**
- Consumes: `ui.checkbox`, `ui.select`, `ui.text`, `ui.confirm`, `ui.editor`, `Profile.load`, `tailor.distill_note`, `llm.score_model`.
- Produces:
  - `install.choose_optional_sections(root: Path) -> None` — adds empty arrays for chosen sections to `content.json`.
  - `install.preferences_walkthrough(root: Path) -> None` — writes `portfolios`, `location`/`location_rules`, `solo_worker`, `banned_summary_anchors`, `skills_title`, `doc_basename`, `output_dir`.
  - `install.notes_walkthrough(root: Path, *, backend: str, model: str) -> None` — iterates experience+projects, distills each answer, `Profile.append_note`.

- [ ] **Step 1: Write the failing test (append)**

```python
def _seed(root):
    root.mkdir(exist_ok=True)
    (root / "constant.json").write_text(json.dumps({"name": "Alex Rivera", "email": "a@x.com",
        "phone": "+1", "linkedin": "in/a", "location": "Seattle, WA", "backend": "claude"}, indent=2))
    (root / "content.json").write_text(json.dumps({
        "portfolio_link": "", "summary": "",
        "experience": [{"company": "Northgate", "title": "PD", "dates": "2023--", "location": "S", "notes": "", "bullets": ["b"]}],
        "projects": [{"title": "Transit Board", "notes": "", "bullets": ["b"]}],
        "skills": [{"label": "Design Skills", "entries": ["Figma"]}],
    }, indent=2))


def test_choose_optional_sections_adds_empty_arrays(tmp_path, monkeypatch):
    _seed(tmp_path)
    monkeypatch.setattr(install.ui, "checkbox", lambda *a, **k: ["Certifications", "Languages"])
    install.choose_optional_sections(tmp_path)
    c = json.loads((tmp_path / "content.json").read_text())
    assert c["certifications"] == [] and c["languages"] == []
    assert "awards" not in c


def test_preferences_walkthrough_fixed_location(tmp_path, monkeypatch):
    _seed(tmp_path)
    monkeypatch.setattr(install.ui, "select", lambda msg, choices: "One" if "portfolio" in msg else "Fixed")
    monkeypatch.setattr(install.ui, "text", lambda msg, **k: {"Portfolio URL": "example.com",
        "City shown on the resume": "Seattle, WA", "Skills section heading": "Skills",
        "PDF filename stem": "Alex_Rivera", "Output directory": "./applications"}.get(msg.split("(")[0].strip(), ""))
    monkeypatch.setattr(install.ui, "confirm", lambda *a, **k: False)
    install.preferences_walkthrough(tmp_path)
    c = json.loads((tmp_path / "constant.json").read_text())
    assert c["portfolios"] == "example.com"
    assert c["location"] == "Seattle, WA"
    assert "location_rules" not in c or c["location_rules"] == []
    assert c["solo_worker"] is False


def test_notes_walkthrough_distills_and_appends(tmp_path, monkeypatch):
    _seed(tmp_path)
    answers = iter(["moved 12 services to EKS", ""])
    monkeypatch.setattr(install.ui, "editor", lambda *a, **k: next(answers))
    monkeypatch.setattr(install.tailor, "distill_note", lambda q, a, **k: f"N:{a}" if a.strip() else "")
    install.notes_walkthrough(tmp_path, backend="claude", model="claude-haiku-4-5")
    c = json.loads((tmp_path / "content.json").read_text())
    assert c["experience"][0]["notes"].endswith("N:moved 12 services to EKS")
    assert c["projects"][0]["notes"] == ""
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest test_install.py -q`
Expected: FAIL — the three functions are undefined.

- [ ] **Step 3: Implement in `install.py`**

```python
from profile import Profile
import tailor

_OPTIONAL = ["Certifications", "Awards", "Publications", "Languages"]
_OPTIONAL_KEY = {"Certifications": "certifications", "Awards": "awards",
                 "Publications": "publications", "Languages": "languages"}


def _load_content(root):
    return json.loads((root / "content.json").read_text())


def _save_content(root, content):
    (root / "content.json").write_text(json.dumps(content, indent=2, ensure_ascii=False) + "\n")


def choose_optional_sections(root: Path) -> None:
    content = _load_content(root)
    for label in ui.checkbox("Include optional sections:", _OPTIONAL):
        content.setdefault(_OPTIONAL_KEY[label], [])
    _save_content(root, content)


def preferences_walkthrough(root: Path) -> None:
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
        write_constant_key(root, "location", ui.text("City shown on the resume", default="Seattle, WA"))
    else:
        write_constant_key(root, "location", ui.text("Default city (when no rule matches)"))
        rules = []
        while ui.confirm("Add a region rule?", default=bool(not rules)):
            rules.append({"match": ui.text("Regex to match in the JD (e.g. \\bIndia\\b)"),
                          "location": ui.text("City to show when it matches")})
        if rules:
            write_constant_key(root, "location_rules", rules)

    write_constant_key(root, "solo_worker",
                       ui.confirm("Do you work end-to-end solo (no handoffs)?", default=False))
    anchors = []
    while ui.confirm("Add a phrase that may appear in bullets but never headline the summary?",
                     default=bool(not anchors)):
        anchors.append(ui.text("Phrase or number"))
    write_constant_key(root, "banned_summary_anchors", anchors)
    write_constant_key(root, "skills_title", ui.text("Skills section heading", default="Skills"))
    write_constant_key(root, "doc_basename", ui.text("PDF filename stem", default="Resume"))
    write_constant_key(root, "output_dir", ui.text("Output directory", default="./applications"))


def notes_walkthrough(root: Path, *, backend: str, model: str) -> None:
    content = _load_content(root)
    profile = Profile.load(root)
    for kind, key, match in (("experience", "experience", "company"), ("project", "projects", "title")):
        for entry in content.get(key, []):
            label = entry.get(match, "?")
            print(f"\n{kind}: {label}")
            for b in entry.get("bullets", []):
                print(f"  - {b}")
            raw = ui.editor("Add context — constraints, real numbers, why (Enter to skip):")
            note = tailor.distill_note(raw and label or "", raw, backend=backend, model=model) if raw.strip() else ""
            if note:
                profile.append_note(f"{kind}:{label}", note)
```

Then finish `main()`:

```python
def _step(name, fn):
    marker = ui.select(f"Step: {name}", ["run", "skip"]) if ui.interactive() else "run"
    if marker == "run":
        fn()


def main():
    print("Resume-Tex setup\n")
    for p in check_dependencies():
        print(f"  - {p}")
    _pip_install()
    backend = setup_backend()
    write_constant_key(ROOT, "backend", backend)

    if not (ROOT / "content.json").exists() or ui.confirm("Re-bootstrap content.json from a resume file?", default=False):
        path = ui.text("Path to your existing resume (.pdf / .docx / .txt / .md)")
        if path:
            from llm import score_model
            _, model = llm.resolve(backend=backend)
            bootstrap_from_resume(Path(path).expanduser(), ROOT, backend=backend, model=model)

    _step("optional sections", lambda: choose_optional_sections(ROOT))
    _step("preferences", lambda: preferences_walkthrough(ROOT))
    _, score_id = llm.score_model(backend, llm.DEFAULT_PRESET[backend], None)
    _step("notes walkthrough", lambda: notes_walkthrough(ROOT, backend=backend, model=score_id))

    print("\nSetup complete. Try:  python3 tailor.py path/to/jd.txt")
```

- [ ] **Step 4: Run the tests**

Run: `python3 -m pytest test_install.py -q`
Expected: PASS (10 tests in file).

- [ ] **Step 5: Full suite**

Run: `python3 -m pytest -q`
Expected: PASS (everything).

- [ ] **Step 6: Commit**

```bash
git add install.py test_install.py
git commit -m "feat: install.py — optional sections, preferences, notes walkthrough"
```

---

## Task 15: README + examples + final polish

**Files:**
- Modify: `README.md` — Setup section rewritten around `install.py`; new "The interactive flow" section; flags table refresh; `recompile.py` documented; output-location note (§8a)
- Create: `examples/sample-resume.txt` — a plain-text résumé for `install.py` demos (Alex Rivera persona)
- Modify: `examples/constant.json` — add `"backend": ""` key with a comment in README
- Modify: `tailor.py` — **output-base default + double-"Match"-line polish (see Step 0)**
- Test: `test_flow.py` (append 2 output-base tests); run the whole suite once more

- [ ] **Step 0: `tailor.py` polish (spec §8a + a Task 9 leftover)**

Two small fixes:

1. **Output-base default → `~/Documents/Applications`.** In `run()` (the block near `output_base = Path(...)`), change the final fallback from `(ROOT / "applications")` to `(Path.home() / "Documents" / "Applications")`. Rewrite the preceding comment — drop "Never a hardcoded home directory"; the precedence is now `--output-dir` / `-o` → `$RESUME_TEX_OUTPUT_DIR` (already the `-o` default) → `profile.output_dir` → `~/Documents/Applications`. Update the `--output-dir` `help=` text: `… else ~/Documents/Applications`. `bench.py` is unaffected (its own `--out` default is `./bench-runs`, plus its real-dir write guard).

   Tests (append to `test_flow.py`, mocking as `test_run_straight_through` does):
   ```python
   def test_output_base_defaults_to_home_documents_applications(tmp_path, monkeypatch):
       # profile.output_dir empty, no -o, no env -> ~/Documents/Applications
       monkeypatch.delenv("RESUME_TEX_OUTPUT_DIR", raising=False)
       captured = {}
       # stub compile_pdf to capture the output_base it was handed
       monkeypatch.setattr(tailor, "compile_pdf",
           lambda data, output_dir, company, job, mode="resume", basename="Resume": (captured.setdefault("base", output_dir), data)[::-1][0] if False else (__import__("pathlib").Path(output_dir), data))
       # ... run() with everything else mocked as in test_run_straight_through ...
       assert captured["base"] == pathlib.Path.home() / "Documents" / "Applications"

   def test_output_base_respects_explicit_override(tmp_path, monkeypatch):
       # args.output_dir set -> that wins over the default
       # assert captured["base"] == tmp_path / "custom"
   ```
   Keep the two tests small and focused; the exact mocking shape follows whatever `test_run_straight_through` (Task 9) established — reuse its fixture pattern. The assertion that matters: default → `Path.home()/"Documents"/"Applications"`; `-o DIR` → `DIR`.

2. **Double "Match" line.** Task 9 left `print(f"Match     : {match_analysis.get('overall_score', 0)}/100")` in `run()` *and* `ui.report(...)` also renders the tailored score. Delete the standalone `print(f"Match     : …")` line — `ui.report` is the single source for the score now. (The earlier `print(f"Match     : {baseline}...")`-style lines from step B go through `ui.score_panel`, not this print; only the post-generation standalone `Match` print is removed.)

Commit Step 0 separately: `fix: default output to ~/Documents/Applications; drop duplicate Match line` + the trailer lines. Then continue with the docs steps below.

- [ ] **Step 1: Rewrite `README.md` Setup**

Replace the "Setup" numbered list with:

```markdown
## Setup

```bash
python3 install.py
```

`install.py` walks you through it:

1. **Dependencies** — checks Python, LaTeX (`pdflatex` + `crimson titlesec enumitem`), Ghostscript; offers to `pip install -r requirements.txt`.
2. **Model CLI** — finds Claude Code or OpenClaw on your PATH and runs its login/connect. Records the choice in `constant.json`.
3. **Bootstrap** — point it at your existing resume (`.pdf` / `.docx` / `.txt` / `.md`); it drafts `constant.json` + `content.json` for you to refine.
4. **Optional sections** — pick any of Certifications / Awards / Publications / Languages.
5. **Preferences** — one portfolio or a visual/research split; a fixed location or per-region rules; solo-worker mode; summary-anchor bans; headings; output directory.
6. **Notes** — for each role and project, add the unpolished context ("why", constraints, the real number) the model writes bullets from.

Re-run `install.py` any time; each step offers run/skip.
```

Add after "Using it":

```markdown
## The interactive flow

`python3 tailor.py jd.txt` on a terminal:

1. **Score first** — a cheaper model (one tier below your generation model) scores the JD against your current `content.json` and shows the requirement breakdown.
2. **Gap questions** *(optional, default No)* — 3–4 questions, each tagged with the match points it could recover. Your answers are distilled to one or two lines and appended to the relevant `notes` in `content.json`, so they help every future run too.
3. **Generate** — the tailored one-page resume + match report, as before.
4. **Cover letter** *(prompt, default No)* — writes a `cover-letter-prompt.md` to work from. `-c` skips the prompt and always writes it.

The final line shows `baseline NN → tailored NN`. Pipe a JD in, or pass `-y`, to skip the questions and run straight through.

## Editing and regenerating

Every run writes `content.resume.json` next to the PDF. Edit it and:

```bash
python3 recompile.py <Company>/<Role>/        # re-render, no model call
python3 recompile.py <Company>/<Role>/ -i     # menu: add education / certifications / awards / publications / languages / recommendations
```
```

Refresh the flags table to include `--no-interactive/-y` and `--score-preset`.

- [ ] **Step 2: Add `examples/sample-resume.txt`**

A ~30-line plain-text résumé for the Alex Rivera persona (name, contact, 3 roles with bullets, 1 project, education, skills) so `python3 install.py` can be demoed end to end.

- [ ] **Step 3: Run the whole suite + a dry help check on every entrypoint**

```bash
python3 -m pytest -q
python3 tailor.py --help
python3 recompile.py --help
python3 install.py --help 2>/dev/null || python3 -c "import install"
python3 bench.py --help
```

Expected: suite PASS; every entrypoint imports and prints usage.

- [ ] **Step 4: Commit**

```bash
git add README.md examples/sample-resume.txt examples/constant.json
git commit -m "docs: rewrite setup around install.py; document the interactive flow and recompile"
```

---

## Self-Review

**1. Spec coverage**

| Spec section | Task |
|---|---|
| §0 build on the zip | Task 1 |
| §1 package layout | Tasks 1, 4, 10, 11, 12 |
| §2 `Profile` (all members + `append_note`) | Task 2; wired in Task 3 |
| §3 `llm.score_model` + `SCORE_TIER` | Task 5 |
| §3a `ui.py` (all widgets + panels + non-TTY fallback) | Task 4 |
| §4 A trigger + `--no-interactive`/`--score-preset` + `-c` change | Task 9 |
| §4 B `score_jd` + `PreScoreAnalysis` + recompute + validate + LLMError→continue | Task 7 |
| §4 B gap-question grounding (never ask for present info; education-array filter) | Task 7 |
| §4 D0 education decision (`jd_requires_degree`, `decide_education`, printed line, `FIRM_EDUCATION_RULE`) | Task 9 |
| §4 C confirm-default-No + gap table + distill + append + reload + folded | Task 8; wired Task 9 |
| §4 D `tailor_content(profile, …)` still emits `match_analysis` | Task 3 (signature), Task 9 (wiring) |
| §4 E cover-letter prompt default No, `-c` forces | Task 9 |
| §4 final report (`baseline → tailored`, folded, improvements, questions, warnings) | Task 4 (`ui.report`), Task 9 (call) |
| §5 template optional sections + schema + trimmer order | Task 6 |
| §6 `recompile.py` plain + `-i` menu, no model call | Tasks 10, 11 |
| §7 `install.py` steps 1–7 incl. fixed/variable location | Tasks 12, 13, 14 |
| §8 config deltas (`backend`, optional arrays, `notes`, requirements) | Tasks 2, 4, 6, 13 |
| §9 testing (every row) | test files across Tasks 2, 4–14 |
| §10 rollout order | Task order matches (ui before score is a harmless swap: Task 4 before Task 5) |

No gaps.

**2. Placeholder scan**

- `profile.py` Step 3 uses `<PASTE …>` markers for the large prompt literals — this is deliberate ("copy verbatim from zip `tailor.py:407-524`"), not a TODO; the exact source lines are cited. Acceptable: reproducing ~120 lines of prompt text inline would be error-prone; the instruction is precise.
- No "add error handling" / "handle edge cases" / bare "write tests" anywhere — every test step carries real code.

**3. Type consistency**

- `Profile.from_dicts` / `Profile.load` used consistently in Tasks 2, 3, 7, 8, 9, 10, 11, 14.
- `score_jd(...) -> dict` with keys `overall_score` / `requirements` / `gap_questions` — consumed with those exact keys in Task 9.
- `collect_gap_answers(...) -> list[tuple[str,int]]` (`folded`) — consumed by `ui.report(folded=…)` which iterates `for q, p in folded` (Task 4). Consistent.
- `llm.score_model(backend, run_preset, run_model_id, *, override_preset=None) -> (backend, model_id)` — call site in Task 9 passes `(backend, args.preset, args.model, override_preset=args.score_preset)`. Consistent.
- `recompile_menu.run_menu(app_dir, *, root, mode)` — imported and called with those kwargs in `recompile.py` (Task 10) and tests (Task 11). Consistent.
- `ui.editor` / `ui.confirm` / `ui.checkbox` signatures identical across `install.py`, `tailor.py`, `recompile_menu.py`.

One caveat flagged inline for the implementer: the zip's `llm.complete_schema` / `complete_text` signatures (`llm.py:373-398`) — confirm whether they require `backend=`; Tasks 7 and 8 note to pass it through if so. This is a "match the real signature" note, not an unresolved type.

---

## Execution Handoff

Plan saved to `docs/superpowers/plans/2026-09-01-tailor-flow-and-packaging.md`. Two execution options:

1. **Subagent-Driven (recommended)** — a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** — tasks run in this session via `executing-plans`, batched with checkpoints.

Which approach?
