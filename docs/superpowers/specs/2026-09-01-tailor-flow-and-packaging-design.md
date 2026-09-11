# Tailor A→E Flow + Template Packaging — Design

**Status:** draft for review
**Date:** 2026-09-01
**Base:** `resume-tex.zip` (de-personalized packaging branch, ahead of the working dir). Unpack it over the project as step 0; reconcile any newer working-dir edits.

---

## Goal

1. Reorder `tailor.py` into an interactive A→E flow: score first, offer gap questions, generate, offer cover letter.
2. Ship the project as a personal-data-free template a stranger can install, bootstrap, run, edit, and regenerate.
3. Move all applicant-specific logic behind a `Profile` class so personalization is one file.

## Non-goals

- No pip/PyPI packaging. Distribution stays: repo/zip + `README.md` + `install.py`.
- No web UI. CLI only.
- No new model backend. Keep the zip's dual OpenClaw/Claude-CLI `llm.py`.
- No change to the LaTeX design language beyond four new optional sections.

---

## 1. Package layout

```
resume-tex/
  install.py         NEW  one-time setup wizard
  profile.py         NEW  Profile class — all applicant-specific logic
  ui.py              NEW  interactive-prompt layer (questionary + rich), TTY-degrading
  tailor.py          MOD  A→E flow; consumes Profile; optional-section schema fields
  recompile.py       NEW to package  edit/regenerate + `-i` menu (de-personalized)
  llm.py             MOD  score_model() tier map
  template.tex       MOD  4 guarded optional-section blocks
  build.py           unchanged
  bench.py           unchanged
  requirements.txt   MOD  + questionary, rich, pdfplumber, python-docx
  README.md          MOD  Setup rewritten around install.py; flow documented
  examples/          MOD  sample data gains the 4 optional sections
  <user data files>  created by install.py, git-ignored
```

Files that change together: prompt-piece construction moves out of `tailor.py` into `profile.py` with the rest of the personalization.

---

## 2. `profile.py` — `Profile` class

Owns everything that varies by applicant. One file to read when personalizing.

### Construction

```
Profile.load(root: Path, *, mode: str = "resume") -> Profile
```

- Reads `constant.json` (required) and `content.json` (required); missing file → the zip's existing "copy examples/…" error and exit.
- `mode` selects the content source; today both modes use `content.json`, so `mode` only affects `.projects_title`.
- Raw dicts kept as `._constant` / `._content`; everything else is a typed accessor.

### Properties / methods (each replaces named code in today's zip `tailor.py`)

| Member | Type | Replaces |
|---|---|---|
| `.identity` | `dict` — `name, email, phone, linkedin` only | `constant_fields` whitelist in `main()` |
| `.location` | `str` | `_constant["location"]` |
| `.location_rules` | `list[dict]` | `_constant.get("location_rules")` |
| `.location_for_jd(jd: str)` | `str` | `default_location_for_jd(jd, profile)` |
| `.portfolio_links()` | `dict` — `{"visual","research"}` \| `{"default"}` \| `{}` | `portfolio_links(profile)` |
| `.skill_groups` | `list[str]` (labels) | inline `labels` in `build_system_prompt` |
| `.solo_worker` | `bool` | `_constant.get("solo_worker")` |
| `.banned_summary_anchors` | `list[str]` | `_summary_bans(profile)` input |
| `.doc_basename` | `str` (safe) | `document_basename(profile)` |
| `.output_dir` | `str \| ""` | `_constant.get("output_dir")` |
| `.backend` | `str \| None` | (new — installer writes it; `main()` uses it as `--provider` default) |
| `.skills_title` | `str` (default `"Skills"`) | `cv.get("skills_title")` |
| `.projects_title` | `str` | `render_data["projects_title"]` literal |
| `.content` | `dict` — experience/projects/skills/education/optional sections | `content_only` filtering |
| `.optional_sections` | `list[str]` subset of `{"certifications","awards","publications","languages"}` present in `content.json` | (new) |
| `.system_prompt()` | `str` | `build_system_prompt(profile)` + all `%%TOKEN%%` fills |
| `.append_note(target: str, text: str) -> None` | mutates `content.json` on disk | (new — step C + installer notes) |

### `.append_note(target, text)`

- `target` is `"experience:<company>"`, `"project:<title>"`, or `"general"`.
- Appends `text` to that entry's `notes` (creating the key), space-joined; `"general"` → top-level `"notes"` string on `content.json`.
- Rewrites `content.json` with `json.dumps(obj, indent=2, ensure_ascii=False)` + trailing newline. Key order preserved (dict insertion order; new keys appended).
- Unknown `target` → raise `ValueError` (caller validates against known entries first).
- After a batch of `append_note` calls, callers rebuild their `Profile` via `Profile.load(...)`.

### Personalization contract

`install.py` writes `constant.json`. Humans personalize by editing the JSON, or by subclassing `Profile` and overriding an accessor. `tailor.py`, `recompile.py`, `install.py` accept a `Profile`, never a raw dict.

---

## 3. `llm.py` — score tier

```
SCORE_TIER: dict[str, dict[str, str]] = {
    "openclaw": {"opus": "sonnet", "sonnet": "luna",  "sonnet-4-6": "luna",
                 "opus-4-8": "sonnet-4-6", "sol": "luna", "terra": "luna", "luna": "luna"},
    "claude":   {"opus": "sonnet", "sonnet": "haiku", "haiku": "haiku", "fable": "haiku"},
}

def score_model(backend: str, run_preset: str | None, run_model_id: str | None) -> tuple[str, str]:
    """(backend, model id) one tier below the run model. If run_model_id was pinned
    (not a preset), fall back to the backend's cheapest preset. Returns a resolvable id."""
```

- Resolution reuses `resolve()`. `--score-preset NAME` on the CLI overrides the map.
- Default run (`terra` / `sonnet`) → score on `luna` / `haiku`.

---

## 3a. `ui.py` — interactive prompt layer

Claude-Code-style inline prompts. `questionary` (prompt_toolkit) for input, `rich` for compact output. Every function degrades to plain `input()` / `print()` when `not sys.stdin.isatty()`. Tests mock `ui.*` directly.

| Function | Widget | Non-TTY fallback |
|---|---|---|
| `ui.confirm(msg, *, default=False) -> bool` | yes/no | reads a line; empty → default |
| `ui.text(msg, *, default="", validate=None) -> str` | single-line, inline edit | `input()` |
| `ui.select(msg, choices) -> str` | arrow-key list | numbered `input()` |
| `ui.checkbox(msg, choices, *, default=()) -> list[str]` | space-toggle multiselect | comma-separated indices |
| `ui.editor(msg, *, default="") -> str` | multi-line prompt_toolkit buffer (Esc-Enter submits) | reads to EOF |
| `ui.score_panel(baseline, requirements)` | `rich` panel: score + one line per requirement (`direct/adjacent/missing`, weight) | 3 plain lines |
| `ui.gap_table(gap_questions)` | `rich` table: `#`, `+pts`, question | `Q{i} (+p): …` lines |
| `ui.report(baseline, tailored, folded, improvements, questions, warnings)` | `rich` panel: `b → t (+d)` then grouped lists | today's plain prints |

**Copy rule (global): prompt and panel text is terse — a label and the choices, no explanation paragraphs.**

Full-screen (Textual) is out of scope for v1; `ui.editor` + `ui.gap_table` cover the one multi-field step (C). A Textual gap-question form is a later enhancement behind the same `ui` interface — no caller changes.

---

## 4. `tailor.py` — A→E flow

### Trigger

Interactive path runs when **`sys.stdin.isatty()` and not `args.no_interactive`**. Otherwise straight-through: B computes and prints, C skipped, E only if `-c`.

### New flags

| Flag | Effect |
|---|---|
| `--no-interactive`, `-y` | Force straight-through. No gap-question prompt, no cover-letter prompt. |
| `--score-preset NAME` | Override step B's model (any preset of the active backend). |

`-c` / `--cover-letter` semantics change: in the interactive path it **pre-answers step E "yes"** and suppresses the prompt. Non-interactive: unchanged (writes the prompt file).

### A. JD in

Unchanged (`read_jd`).

### B. Score first — `score_jd(profile, jd, *, backend, score_model_id) -> dict`

- One call on the score-tier model, schema `PreScoreAnalysis`:

```python
class GapQuestion(BaseModel):
    question: str
    potential_points: int          # = remaining weight of target_requirement
    target: str                    # "experience:<company>" | "project:<title>" | "general"
    target_requirement: str        # the JD requirement this would strengthen

class PreScoreAnalysis(BaseModel):
    overall_score: int
    requirements: List[RequirementAssessment]   # reused from today's schema
    gap_questions: List[GapQuestion]            # 3–4, ordered by potential_points desc
```

- System prompt: a trimmed variant of the match-analysis half of `SYSTEM_PROMPT_BASE` — extract 6–10 weighted requirements (weights total 100), classify each `direct|adjacent|missing` against `content.json` only, then produce 3–4 `gap_questions` targeting the highest-weight `missing`/`adjacent` requirements. Each `potential_points` = that requirement's weight for `missing`, or half for `adjacent`. `target` must name a real entry from `content.json` or `"general"`.
- **Gap questions must be grounded in what `content.json` lacks.** The system prompt states explicitly: *never ask for information the CV JSON already contains.* In particular — if the `education` array is populated, do not ask about degree, institution, graduation date, or GPA; if a role or project already states a fact, do not ask for it.
- Python recomputes `overall_score` with `normalize_match_analysis` (unchanged).
- `gap_questions` validated: drop any whose `target` is neither `"general"` nor a real `experience:`/`project:` key; clamp `potential_points` to `0..100`; **and, when `profile.content.get("education")` is non-empty, drop any question whose text matches `/\b(degree|bachelor'?s?|master'?s?|graduat|institution|university|college|GPA|alma mater)\b/i`** — that data is present, the question is noise. (Scoped to education: it is the observed failure and a bounded, safe filter; a general "is this fact present" check over free-text questions is too fuzzy to apply here.)
- `ui.score_panel(score, requirements)`.
- On `LLMError`: `ui`-level warning, set `baseline = None`, `gap_questions = []`, continue to D. Scoring is not allowed to abort a generation.

### C. Gap questions — offered, default No

- `ui.confirm("Answer questions to raise your match score?", default=False)` — non-interactive → skip.
- Yes → `ui.gap_table(gap_questions)`, then for each `GapQuestion`: `ui.editor("A{i}", default="")`. Blank → skip that question.
- Each non-blank answer → one call on the **score-tier** model, `distill_note(question, answer) -> str`:
  - System: "Rewrite the applicant's answer as a resume source note: ≤2 sentences, factual, specific numbers kept, no marketing language, no first person. Return the note text only."
  - Result passed to `profile.append_note(gap_question.target, distilled)`.
- After the loop: `profile = Profile.load(ROOT, mode=mode)` to pick up the writes. Record `folded = [(q.question, q.potential_points) for answered]` for the final report.
- `Updated content.json: {n} note(s) added`.

### D0. Education decision (before D)

Education is the pipeline's sharpest edge: today it is filtered out of the model's input unless `-e` is passed, so a JD that *requires* a degree gets a resume with no education section — exactly what happened on the Intuitive UX-Engineer run. The flow now decides explicitly and **tells the user which way it went**.

- `jd_requires_degree(jd: str) -> bool` — true when the JD names a degree requirement. Match (case-insensitive) any of: `bachelor'?s`, `master'?s`, `\bB\.?S\.?\b`, `\bB\.?A\.?\b`, `\bBSc\b`, `\bM\.?S\.?\b`, `degree in `, `degree from`, `educational qualification`, `accredited institution`, `\bdegree required\b`, `undergraduate degree`.
- Decision table (resume mode; `include_education` feeds `tailor_content`):

  | Condition | `include_education` | Printed line |
  |---|---|---|
  | `-ee` / `--extended-education` | true (+ coursework, injected as today) | `Education  : included with coursework (-ee)` |
  | `-e` / `--education` | true | `Education  : included (-e)` |
  | JD requires a degree **and** `content.json` has `education` | true | `Education  : included — the JD requires a degree` |
  | JD requires a degree **and** `content.json` has no `education` | false | `Education  : WARNING — JD requires a degree but content.json has none. Add it.` |
  | JD does not require a degree, interactive | `ui.confirm("The JD doesn't mention a degree — include education anyway?", default=False)` | `Education  : included (your choice)` / `Education  : omitted (JD doesn't require it)` |
  | JD does not require a degree, `--no-interactive` / piped | false | `Education  : omitted (JD doesn't require it)` |

- CV mode (`--cv`) is unchanged: education is always shown.
- When `include_education` is true, the model is told firmly to include it: `tailor_content` uses a firm rule ("The `education` array below MUST appear in the output.") in place of the old conditional `EDUCATION_RULE`. The old conditional rule — "include only if the JD explicitly requires a degree" — is what let the model drop a required section, and the Python gate now owns that decision.

### D. Generate

`tailor_content()` — full-tier model, still returns its own `match_analysis`. It now takes `profile`, calls `profile.system_prompt()` instead of `build_system_prompt(cv)`, and receives `include_education` from the D0 decision (not from `-e` alone). When `include_education`, `education` is added to `content_keys` so the model actually sees the data, and the firm education rule is appended.

### E. Cover letter — prompt, default No

- Interactive and not `-c`: `ui.confirm("Generate a cover-letter prompt?", default=False)`.
- `-c` given: skip the prompt, always generate.
- Non-interactive without `-c`: skip.
- Generation path unchanged (`generate_cover_letter_prompt`).

### Final report

`ui.report(baseline=b, tailored=t, folded=folded, improvements=..., questions=..., warnings=...)`.

- `b` from B (or `None`/`n/a` if B failed), `t` from D's normalized `match_analysis`.
- `folded` renders as `Incorporated: <question> (+p)` per line.
- Improvements, clarifying questions, integrity warnings: same content as today, grouped in the panel.

### `main()` wiring

1. Parse args. `Profile.load(ROOT, mode=mode)`.
2. `backend, model = llm.resolve(args.preset, model=args.model, backend=args.provider or profile.backend)`.
3. `score_backend, score_id = llm.score_model(backend, args.score_preset or args.preset, args.model)`.
4. A → B (`score_jd`) → C (if interactive & confirmed) → reload Profile → D → E → report.
5. Output paths, JD save, JSON save, integrity lint: unchanged, but `check_integrity(..., profile=profile)` and `compile_pdf(..., basename=profile.doc_basename)`.

---

## 5. `template.tex` — optional sections

Four blocks, each `(% if <key> is defined and <key> %)`, placed:

- **Certifications** — after Education. `certifications: [{name, issuer, date}]` → `\textbf{name}, issuer \hfill date` per line.
- **Awards** — after Certifications. `awards: [{title, body, year}]` → `\textbf{title}, body \hfill year`.
- **Publications** — after Awards. `publications: [{title, venue, year}]` → `title. \textit{venue}, year`.
- **Languages** — after Skills. `languages: [{language, proficiency}]` → one line, `\textbf{Languages:} English (native), French (working)`.

`TailoredResume` gains:

```python
certifications: Optional[List[CertEntry]] = None
awards:         Optional[List[AwardEntry]] = None
publications:   Optional[List[PubEntry]]  = None
languages:      Optional[List[LangEntry]] = None
```

- D emits a section only when `content.json` carries that array (mirrors the existing `education` rule).
- `escape_latex_strings` already recurses; new string fields covered automatically. `_URL_KEYS` unchanged.
- `enforce_caps`: no cap on the new sections (they are short); leave as-is.
- One-page trimmer (`_shrink_one_step`): add the optional sections to Tier-2 shedding **after** projects and before the skill-group trim — drop `languages`, then `publications`, then `awards`, then `certifications`, each entirely, lowest-value first. (Rationale: on a one-pager these lose to experience and projects.)

---

## 6. `recompile.py` — edit / regenerate

De-personalized: no `<Name>_Resume.pdf` literal; `Profile.load` for `constant.json`; filename via `profile.doc_basename`. Operates on an application output folder's `content.<mode>.json` (default `content.resume.json`, `--cv` picks `content.cv.json`).

### `recompile <folder>`

- Load folder JSON + `constant.json` identity, render, compile.
- Resume mode: run the one-page trimmer (`compile_single_page`). CV mode: `compile_to_dir`.
- **No model call.**

### `recompile -i <folder>`

`ui.checkbox("Add sections to this application:", choices=[...])` — nothing selected → plain recompile. Choices:

- **Education** — then `ui.checkbox` institutions from master `content.json` (all preselected). Injects chosen `education` entries (+ coursework from `coursework.json` if present).
- **Certifications** — from master `content.json` if present, else `ui.editor` lines `name | issuer | date`.
- **Awards** — `title | body | year`.
- **Publications** — `title | venue | year`.
- **Languages** — `language | proficiency`.
- **Recommendations** — from `recommendations.json`.

- Chosen data merged into the folder JSON, file rewritten (`indent=2`), then recompiled.
- Still **no model call.** A future `--fresh` (out of scope here) would re-run `tailor_content`.

---

## 7. `install.py` — setup wizard

Run once: `python3 install.py`. All prompts use `ui.*`. Re-runnable — each step detects prior state and offers `ui.select` → `skip / redo`.

### Step 1 — Dependencies

- Python ≥ 3.9 check (exit with message if lower).
- `pip install -r requirements.txt` (prompt before running).
- `shutil.which("pdflatex")` — if missing, print the OS-specific install command from `README.md` and the `tlmgr install crimson titlesec enumitem` line. Non-fatal (warn, continue).
- `shutil.which("gs")` — optional; note if absent.

### Step 2 — Model CLI + auth

- `llm.available_backends()`.
- None → print both install commands (`npm i -g @anthropic-ai/claude-code`; OpenClaw docs), exit 1.
- OpenClaw present → shell `openclaw` through its provider connect/login for OpenAI **or** Claude (prompt which), wait for completion.
- Only Claude CLI → run its login (`claude`) or instruct the user, wait.
- Write the chosen backend to `constant.json` `"backend"`.

### Step 3 — Bootstrap `content.json` + identity from a résumé file

- Prompt for a path. Accept `.pdf` (`pdfplumber`), `.docx` (`python-docx`), `.txt`/`.md` (read). Guarded imports — if the parser lib is missing, print `pip install pdfplumber` / `python-docx` and let the user re-run.
- One model call (full-tier), schema `BootstrapExtract`:

```python
class BootstrapExtract(BaseModel):
    identity: IdentityBlock          # name, email, phone, linkedin, location
    experience: List[ExperienceEntry]
    projects: List[ProjectEntry]
    skills: List[SkillGroup]
    education: List[EducationEntry]
```

- System prompt: "Extract structured resume data verbatim from this text. Do not invent, embellish, or add metrics. Leave a field empty if the text does not state it. `notes` empty for now."
- Write `constant.json` (identity + `backend` + placeholder preference keys) and `content.json` (the four arrays, `notes: ""` on each entry).

### Step 4 — Optional sections

- `ui.checkbox("Include optional sections:", ["Certifications","Awards","Publications","Languages"])`.
- Each chosen → empty array on `content.json` (so the template + `-i` recognise it). No template edit needed — blocks are always present and guard on the key.

### Step 5 — Preferences walkthrough → `constant.json`

Each prompt via `ui.*`, written to the key the zip already reads:

| Prompt | Widget | Key |
|---|---|---|
| One portfolio or two (visual/research)? URLs. | `ui.select` + `ui.text` | `portfolios` |
| **Location: fixed, or varies by job region?** | `ui.select(["Fixed","Varies by job region"])` | — |
| ↳ Fixed → one city. | `ui.text` | `location` |
| ↳ Varies → repeat: `pattern → city` rows, then a fallback city. | `ui.text` loop + `ui.confirm("add another?")` | `location_rules`, `location` |
| Do you work end-to-end solo? | `ui.confirm` | `solo_worker` |
| Number/phrase allowed in bullets but never the summary headline? | `ui.text` loop | `banned_summary_anchors` |
| Skills section heading (default `Skills`). | `ui.text` | `skills_title` |
| PDF filename stem (default: your name). | `ui.text` | `doc_basename` |
| Where should generated applications be written (blank = default `~/Documents/Applications`)? | `ui.text` | `output_dir` |

### Step 6 — Notes walkthrough

- Iterate every `experience` then `project` entry. Show company/title (or project title) and the extracted bullets.
- `ui.editor("Add context — constraints, real numbers, why (Enter to skip):")`.
- Non-blank → `distill_note` (score-tier model) → `profile.append_note("experience:<company>" | "project:<title>", text)`.

### Step 7 — Done

- Print: `Setup complete. Try:  python3 tailor.py path/to/jd.txt`
- Do **not** auto-run a generation.

---

## 8. Config deltas

**`constant.json`** — `+ "backend": "openclaw"|"claude"`. All other keys already defined in the zip.

**`content.json`** — optional: `"certifications"`, `"awards"`, `"publications"`, `"languages"` (arrays), `"notes"` (top-level string, the `"general"` bucket).

**`requirements.txt`**:

```
jinja2>=3.0
pydantic>=2.0
questionary>=2.0    # interactive prompts (ui.py)
rich>=13.0         # score panel + report (ui.py)
pytest>=7.0        # tests only
pdfplumber>=0.11   # install.py only, PDF résumé bootstrap
python-docx>=1.1   # install.py only, DOCX résumé bootstrap
```

`ui.py` imports `questionary`/`rich` lazily; if absent and stdin is a TTY it prints `pip install questionary rich` and uses the plain-text fallbacks. Non-TTY runs never import them.

**`.gitignore`** — already ignores the user data files in the zip; add `applications/` and `content.json`'s siblings if not covered.

## 8a. Output location (amendment)

Generated applications **always** land under a single tree, one folder per application:

```
<output base>/<Company>/<Role>/
    <doc_basename>_Resume.pdf   (or _CV.pdf under --cv)
    jd-<Role>-<YYYY-MM-DD>.txt
    content.resume.json         (or content.cv.json)
    match-report.md  match-report.json
    cover-letter-prompt.md      (with -c)
```

**Output-base precedence:** `--output-dir` / `-o` → `$RESUME_TEX_OUTPUT_DIR` → `constant.json` `output_dir` → **`~/Documents/Applications`** (the default when none is set). This replaces the zip's project-local `./applications` fallback — the old comment "Never a hardcoded home directory" is removed; a stable home-directory tree is exactly what's wanted so records accrue in one place across machines and checkouts. `compile_pdf` / `run()` build `<base>/<safe_company>/<safe_role>/` and the filename from `profile.doc_basename` exactly as today.

---

## 9. Testing

All tests mock `llm.run_once` — no model calls, no network. Extends the zip's 88-test suite.

| Area | Tests |
|---|---|
| `llm.score_model` | preset→tier for every entry in `SCORE_TIER`; pinned-`--model` falls back to cheapest preset; `--score-preset` override wins |
| `PreScoreAnalysis` | valid parse; one-retry on malformed JSON; `overall_score` recomputed; `gap_questions` with bad `target` dropped; `potential_points` clamped |
| `Profile` | `.load` reads both files; missing file → exit with copy-example message; `.portfolio_links()` all three shapes; `.location_for_jd` rule match + fallback + bad regex ignored; `.system_prompt()` fills every `%%TOKEN%%` and drops solo rule when `solo_worker` false |
| `Profile.append_note` | appends to existing entry; creates `notes` key; `"general"` → top-level; unknown target → `ValueError`; file round-trips as valid JSON with 2-space indent |
| `ui.py` | each function returns the widget value on a fake TTY (mock questionary) and the fallback value on `isatty()==False`; missing `questionary` → fallback path, no crash |
| flow control | `--no-interactive` skips C prompt and E prompt; piped stdin skips C; `-c` pre-answers E; B failure → flow continues, report shows `n/a` baseline |
| `template.tex` | renders with each optional section present; renders with all absent (no stray headings); trimmer sheds `languages` before `publications` before `awards` before `certifications` |
| `recompile -i` | option 1 = plain recompile; option 3–6 merge from master content.json; option 3–6 merge from prompt when master lacks the key; no `llm` call in any path |
| `install.py` | résumé-file dispatch by extension; missing parser lib → instructive message; `BootstrapExtract` write produces loadable `constant.json` + `content.json`; step re-run detects existing files |

`python3 -m pytest -q` stays green, no network, no model calls.

---

## 10. Rollout order

1. Unpack the zip over the project; reconcile newer working-dir edits; `pytest` green.
2. `profile.py` + swap `tailor.py`/`recompile.py` internals to `Profile` (pure refactor, tests still green).
3. `ui.py` + its tests (fallbacks + mocked widgets).
4. `llm.score_model` + `SCORE_TIER`.
5. `template.tex` optional sections + `TailoredResume` fields + trimmer ordering.
6. `tailor.py` A→E flow + new flags (uses `ui`).
7. `recompile.py` into the package + `-i` menu.
8. `install.py`.
9. `README.md` rewrite; `examples/` gain optional sections.

Each step ends with `pytest` green and is independently reviewable.
