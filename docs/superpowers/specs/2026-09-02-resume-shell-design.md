# Resume-Tex shell, skills, and incremental content — design

Date: 2026-09-02
Status: approved for planning

## Problem

Resume-Tex today is a set of one-shot scripts. Running several job descriptions
means paying the cold-start import cost each time, re-reading `content.json` each
time, and losing the terminal to a linear wizard. Setup has rough edges that a
first-run user hits before they reach any of that: an OpenClaw provider that
cannot connect, questions whose wording does not explain itself, and silent
multi-second pauses that read as a hang.

Three capabilities are also missing outright:

- Skills are whatever the bootstrap extraction happened to find. There is no way
  to fill them in deliberately, and no way to surface a skill the résumé implies
  but never names.
- `content.json` can only be built from scratch. Adding a second résumé or CV
  means replacing the corpus — and with it the notes that make tailoring good.
- A job description must be a local file. Every posting starts life as a URL.

## Goals

1. One long-running shell that keeps `content.json` warm across many JDs.
2. Batch a directory or list of postings: score all, review, answer gaps once,
   generate unattended.
3. Accept a URL anywhere a JD path is accepted.
4. A skills step that fills groups by hand and derives more, each derived skill
   traceable to real evidence.
5. Add a résumé to an existing `content.json` without losing notes.
6. Close the setup gaps: OpenClaw plugin, skippable steps, clearer wording,
   progress on every slow call, and per-entry guidance in the notes walkthrough.

## Non-goals

- **The transcript is never redrawn by us.** Completed output scrolls into the
  terminal's own buffer, so `Cmd+F`, mouse scroll and copy/paste keep working.
  What we own is the input region at the bottom and, transiently, an option
  picker. There is no persistent alt-screen and no custom scroll buffer holding
  history.
- No headless browser. JS-rendered, login-walled boards fall back to paste.
- No change to `tailor.py`'s generation pipeline, prompts, or output layout. The
  shell calls `tailor.run()` with the same Namespace `parse_args()` produces.
  The single exception is making `parse_args(argv=None)` accept an argv list
  (§Architecture), which changes no existing path.
- No change to the existing one-shot CLIs. They keep working exactly as today.

## Architecture

Four new modules, all thin. Existing behaviour is reused, not reimplemented.

```
shell.py       REPL loop + command dispatch          (new)
console.py     input region + full-screen picker      (new)
skills.py      fill + evidence-linked derive          (new)
merge.py       incremental content.json merge         (new)
jdsource.py    path-or-URL -> JD text                 (new)
drafts.py      evidence-only draft gap answers        (new)

install.py     setup steps, now importable + fixed    (modified)
ui.py          + evidence_checkbox, merge_diff, table (modified)
tailor.py      parse_args(argv); draft default on A_i (modified)
llm.py         unchanged
```

Data flow for one JD:

```
"/jd <arg>"  ->  jdsource.load_jd(arg)  ->  (text, label)
             ->  materialise to a path (temp file for a URL)
             ->  tailor.run(tailor.parse_args([... , path]))
             ->  <output_base>/<Company>/<Role>/{pdf, json, jd, match-report}
```

`tailor.run()` reads the JD from `args.jd_file`. Rather than hand-building a
Namespace — which would silently drift as flags are added — `parse_args` gains
an optional `argv` parameter (`def parse_args(argv=None)`, passing it through to
`parser.parse_args(argv)`). The shell then constructs a real argv list and
inherits every default and choice-validation for free. This is the only edit to
`tailor.py`.

Fetched URL text is written to a temp file so the existing `jd_file` path is
used unchanged; `tailor.run()` copies it into the application folder as it
already does (tailor.py:1242).

**`sys.exit` containment.** `read_jd()` and `run()` call `sys.exit(1)` on a
missing or empty JD. In a REPL that would kill the session, so the shell's
dispatcher catches `SystemExit` alongside `Exception` and reports it as a
command failure.

---

## 1. `shell.py` — the REPL

Entry point: `python3 shell.py`. Prints a banner with the loaded corpus and
backend, then loops on `> `.

```
Resume-Tex  ~/Documents/Projects/Resume-Tex
  content.json: 4 experience, 3 projects, 18 skills  ·  openclaw/terra

> /jd ~/Downloads/figma-pd.txt
```

### Commands

| Command | Behaviour |
|---|---|
| `/jd <path\|url>` | One JD, full A→E flow via `tailor.run()` |
| `/batch <glob\|dir\|urls.txt>` | Score all → review → gaps → generate |
| `/skills` | `skills.run()` |
| `/add <path>` | `merge.run()` — add a résumé to `content.json` |
| `/setup [step]` | Re-run `install.py` steps; bare form runs all |
| `/recompile [n]` | Existing `recompile_menu` |
| `/status` | Corpus counts, backend, output dir |
| `/help`, `/exit` | — |

Dispatch is a `dict[str, Callable[[list[str]], None]]`. A bare (non-`/`) line
containing a path or URL is treated as `/jd <line>`, since that is the dominant
use.

### Session state

A `Session` dataclass holds `root`, `profile`, `backend`, `model`, and a
`dirty` flag. `content.json` is loaded once at start. Any command that writes it
(`/skills`, `/add`, `/setup`) reloads the `Profile` afterwards so later commands
in the same session see the change.

### First run

If `content.json` is missing or empty, the shell runs the setup flow inline
before showing the prompt, then continues into the REPL. This makes `shell.py`
the single front door; `install.py` remains importable and directly runnable for
anyone who wants only setup.

### Error containment

A command raising is caught, printed as one line, and the loop continues. A
`KeyboardInterrupt` inside a command aborts that command, not the session; at the
bare prompt it exits. This matters most in batch, where one bad JD must not
discard the rest.

---

## 2. `/batch` — score all, ask once, generate unattended

Three phases, deliberately ordered so every question a human must answer happens
before the slow unattended work begins.

**Phase 1 — score.** Each source resolves through `jdsource.load_jd`. Scoring
uses `tailor.score_jd` on the cheap model (`llm.score_model`). Runs on a
`ThreadPoolExecutor(max_workers=3)`; each call is a subprocess, so threads are
the right tool and 3 keeps provider rate limits comfortable. Output is collected
per-future and printed only on completion, never interleaved — a single spinner
shows `scored 3/5`.

A failed source (fetch failed and paste declined, malformed file) is recorded
with its error and excluded from the table; it never aborts the batch.

**Phase 2 — review.** A ranked table, then two questions:

```
  figma-pd     72/100   2 gaps
  stripe-pd    68/100   3 gaps
  vercel-pd    41/100   6 gaps   ← weak fit

? Generate for which?  [all / top 3 / pick]
? Answer gap questions now?  [Y/n]
```

`← weak fit` marks anything under 50. Gap answers are collected per selected JD
and held in memory as `extra_context`, exactly as the single-JD flow does today.

**Phase 3 — generate.** Sequential (LaTeX compilation is disk-bound and
interleaving output would be unreadable), each with a progress line. A failure
is reported and the batch continues. A closing summary lists every PDF written
and every JD that failed, with the reason.

---

## 3. `jdsource.py` — path or URL

```python
def load_jd(arg: str) -> tuple[str, str]:   # (text, label)
```

Non-`http` arg → today's file read, unchanged.

URL → `requests.get` with a browser User-Agent and a 15s timeout, then
BeautifulSoup: drop `script`, `style`, `nav`, `header`, `footer`, `aside`, take
the text-densest remaining block, collapse whitespace.

**Sanity gate**, applied before returning. Text must be ≥300 characters *and*
match `responsibilit|qualificat|requirement|you.ll|experience` case-insensitively.
Either failing means we fetched a login wall or a shell page, not a posting. We
say which check failed and how much text we got, then open `ui.editor` to paste.
Silently proceeding with a login wall would produce a confidently wrong resume,
which is worse than asking.

Fetched text needs no separate cache: `tailor.run()` already writes the JD into
the application folder (tailor.py:1242), so `/recompile` works after a posting
is taken down.

`/batch` treats a `.txt` file whose lines are all URLs as a URL list, and mixes
those with globbed paths.

New dependencies: `requests`, `beautifulsoup4`.

---

## 4. `skills.py` — fill, then derive

### Phase 1 — fill

Each existing group in `content.json:skills` is shown as `label` plus a
comma-separated `entries` line, editable in place. Empty groups can be added and
existing ones deleted. No model call.

### Phase 2 — derive

One call. Input: every experience and project entry (bullets plus notes).
Output schema:

```python
class SkillCandidate(BaseModel):
    skill: str
    group: str          # existing label where one fits, else a new one
    evidence: str       # verbatim span from a bullet or note
    source_ref: str     # "experience:Acme" | "project:Transit Fare"
    strength: Literal["direct", "implied"]
```

The system prompt requires `evidence` to be copied verbatim from the supplied
text. A candidate whose `evidence` is not found as a substring of the source
corpus is **dropped before display** — a mechanical check, not a request to the
model. This is the guard that keeps a derived skill honest: if it cannot be
pointed at something the user actually wrote, it does not exist.

Candidates already in `skills`, and anything in
`constant.json:rejected_skills`, are filtered out.

Survivors render through `ui.evidence_checkbox`:

```
Derived from your experience — pick what's true:

 [x] Design Systems
     ← "built a 40-component library ..."  (experience:Acme)
 [ ] GraphQL
     ← "consumed the GraphQL API"  (experience:Bolt, implied)
```

`direct` candidates are pre-checked, `implied` are not. Accepted skills are
written into their group. Rejected ones append to `rejected_skills` so a re-run
does not re-ask.

---

## 5. `merge.py` — add a résumé to existing content

`bootstrap_step` gains a three-way choice when `content.json` already has
entries:

```
? content.json already has 4 experience and 3 projects.
  > Add another resume/CV to it
    Replace it entirely
    Skip
```

"Replace" is today's behaviour and stays destructive-with-warning. "Add" is new.

### Matching

Extract the new file with the existing `BootstrapExtract`, then one model call
returns a plan:

```python
class MergeMatch(BaseModel):
    kind: Literal["experience", "project"]
    existing_ref: str          # company+title, or project title
    incoming_ref: str
    new_bullets: list[str]
    conflicts: list[str]       # "dates differ: 2021-2023 vs 2021-2024"

class MergePlan(BaseModel):
    matched: list[MergeMatch]
    added: list[dict]          # entries with no counterpart
```

### Applying

- **Matched** — new bullets append. Existing `notes` are copied through
  **verbatim and never sent to the model**; the notes are the corpus tailoring
  quality depends on and there is no upside to letting a merge rewrite them.
  Conflicts are reported, never auto-resolved; dates and titles keep the
  existing value.
- **Added** — appended with `notes: ""`.
- Skills and education merge by case-insensitive dedupe, no model call.

`content.json` is copied to `content.json.bak` before any write.
`ui.merge_diff()` renders the plan and the user confirms before the write.
Declining leaves the file untouched.

---

## 6. `install.py` fixes

**OpenClaw plugin gate.** `openclaw_plugins()` shells `openclaw plugins list`
and parses installed provider names. If the chosen provider is absent,
`connect_backend` offers `openclaw plugins install <provider>` and retries auth
on success. A parse failure or a non-zero install falls back to today's printed
advice — this must never become fatal. This is the direct fix for "No provider
plugins found" when picking `claude`.

**Backend step skippable.** `_configured()["backend"]` is already computed;
route the backend step through `_step()` like every other step so a re-run
defaults to skipping it.

**Wording.** `"Do you work end-to-end solo, with no handoffs?"` →
`"Is your work solo — you do the research, design and build yourself?"` The
existing `ui.hint` explaining the lint stays.

**Notes walkthrough guidance.** Before the loop, one batched call takes all
entries and returns 2–3 targeted questions each:

```python
class EntryPrompts(BaseModel):
    ref: str                 # "project:Transit Fare Redesign"
    questions: list[str]     # 2-3, specific to that entry's bullets
```

Printed above each editor under `Useful context here:`. Batching keeps the cost
to one call and puts it behind a single spinner before the walkthrough starts,
so there is no stall between entries. A failed call degrades to a static
checklist (scale, constraint, outcome, why-you) rather than blocking the step.

**Progress.** Every operation over ~1s gets `ui.spinner`: the import warmup, the
batched notes call, plugin install, URL fetch, and each merge call.

---

## 7. `console.py` — input region and full-screen pickers

`questionary` is already a dependency and is built on **prompt_toolkit**, so the
capability is present transitively; no new package is added.

### Persistent input region

`console.prompt(placeholder) -> str` runs a prompt_toolkit `PromptSession` with
a bordered multi-line input anchored at the bottom of the terminal, plus a
right-aligned status bar showing backend, model and corpus counts.

This is the same arrangement Claude Code uses, and the mechanism matters: the
input region is redrawn in place *below* the cursor while everything already
printed scrolls away above it untouched. We are not compositing a transcript —
that is what keeps native selection and search working. Anything a command
prints goes to normal stdout via `patch_stdout()`, which lifts the input region,
writes the line, and re-renders the region beneath it.

Key bindings: `Enter` submits, `Alt+Enter` inserts a newline, `Ctrl+C` aborts
the current command, `Ctrl+D` on an empty line exits, `↑`/`↓` walk history
(persisted to `.resume_history`), `Tab` completes commands and file paths.

### Full-screen option picker

`console.pick(msg, options, *, multi=False, detail=None) -> str | list`.

Any prompt with a fixed set of options takes over the screen for as long as it
is open, then restores. Full-screen buys room for a description panel next to
the list, which is what these particular choices need — "Add another resume /
Replace it entirely / Skip" is a decision about destroying notes, and the
consequence should be on screen at the moment of choosing rather than in a hint
printed earlier. `detail` is a callable mapping an option to its explanation.

`ui.select`, `ui.checkbox` and `ui.evidence_checkbox` route through `pick` when
`console.available()` is true, and fall back to today's `questionary` and plain
paths otherwise. Every existing `ui` signature is unchanged, so no caller moves.

### Degradation

`console.available()` is false when stdin is not a TTY or prompt_toolkit is
missing. Every function then falls back to the current behaviour. The autouse
`_no_tty` fixture in `test_ui.py` already exercises that path.

---

## 8. `drafts.py` — evidence-only draft answers

A gap question is easier to answer when the material you already wrote is in
front of you. It is also the single easiest place in this project to end up with
a fabricated claim, because a fluent draft is easy to accept by reflex — and the
answer is distilled into `content.json` notes, where every later application
reuses it.

So the draft is assembled, not written:

```python
def draft_for(question: str, target: str, profile) -> tuple:  # (text, citation)
```

- Pull only the bullets and notes of the entry named by `target`
  (`"experience:Acme"`, `"project:Transit Fare"`), plus `general` → nothing.
- One model call whose schema is `{"draft": str, "used": list[str]}`, with a
  system prompt requiring every clause of `draft` to be supported by a supplied
  span, and `used` to quote those spans verbatim.
- **Verify in Python:** every entry of `used` must appear in the source text,
  and `used` must be non-empty. Any failure returns `("", "")`.
- On `("", "")` the field is left empty under the line
  `no material found for this — answer from scratch:`.

The draft is passed as `ui.editor(..., default=draft)` in
`tailor.collect_gap_answers`, so it is pre-filled and editable rather than
accepted silently. The citation prints above it as
`draft (from your notes · project:Transit Fare):`.

This is the same guard as §4's skills derivation, for the same reason: a claim
the user cannot trace to something real is one they have to defend in an
interview.

---

## 9. Note targeting and storage

Notes are the corpus every tailored application is built from. Two defects in
how they are attached and stored surfaced in real use.

**Notes are space-joined into one paragraph.** `Profile.append_note`
(profile.py:301, 311) does `(existing + " " + text)`, so successive notes weld
into an undifferentiated blob — two unrelated distilled answers read as one
incoherent claim, and neither can be edited or deleted separately. Worse for
§8: `drafts.py` reads notes as source text, and a blob would let a garbled
claim pass the verbatim-substring gate, because the blob genuinely does contain
it. Notes join with `"\n"` instead.

**Education cannot hold a note.** `append_note` maps only
`experience`/`project` (profile.py:304) and `_known_note_targets` builds only
those two (tailor.py:597), so anything about a degree, a thesis, or coursework
has nowhere to live. `education:<institution>` becomes a valid target.

**An unresolved target is silently dropped.** `score_jd` discards any gap
question whose target is not recognised (tailor.py:637). That filter stays —
it is a quality gate on the model's output — but at *note-writing* time a
target of `general`, or one that no longer matches an entry, now prompts a
picker instead of quietly landing in a catch-all:

```python
def resolve_note_target(profile, proposed: str) -> str
```

- A known specific target is used as-is, no prompt.
- `general`, an empty target, or one naming an entry that no longer exists →
  the user picks from every experience, project and education entry, plus
  "General notes" and "Skip this note".
- Used by both writers: `tailor.collect_gap_answers` and
  `install.notes_walkthrough`.

**Transcription latitude.** `distill_note` may tighten wording for concision
and readability. It may **not** add a fact, number, scope, or outcome the user
did not state — the same line every other model call in this project holds. The
distilled note is shown before it is written.

**Legacy cleanup (opt-in).** A `/notes` command offers, per entry, to split an
existing blob into separate notes and tidy their wording. The result is shown
as a before/after and requires confirmation; declining leaves the entry
byte-identical. `content.json.bak` is written first.

---

## 10. `ui.py` additions

All three degrade to plain text off-TTY, matching every existing function there.

- `evidence_checkbox(msg, candidates, *, precheck) -> list` — checkbox with an
  indented evidence line under each item.
- `merge_diff(plan) -> None` — renders matched / added / conflicts.
- `table(rows, headers) -> None` — the batch score table; extracted so the
  `rich`-or-plain branch is not written a third time.

---

## Testing

Existing suites (`test_install.py`, `test_ui.py`, `test_flow.py`) must keep
passing untouched — that is the regression guard for "one-shot CLIs still work".

New tests, all with the model call and the network stubbed:

| File | Covers |
|---|---|
| `test_shell.py` | dispatch table, bare-line→`/jd`, unknown command, error containment, `KeyboardInterrupt` aborts command not session, first-run triggers setup |
| `test_jdsource.py` | file passthrough; URL happy path; sanity gate rejects short text; gate rejects text missing JD keywords; paste fallback; URL-list detection |
| `test_skills.py` | evidence-not-in-corpus candidate is dropped; already-present skill filtered; rejected skill filtered; accepted written to correct group; rejection persisted |
| `test_merge.py` | matched entry keeps existing notes verbatim; new bullets appended; conflict reported not applied; unmatched entry added; backup written; decline leaves file byte-identical |
| `test_batch.py` | ranked ordering; failed source excluded, batch continues; `top 3` selection; generation failure does not abort remaining |
| `test_console.py` | `available()` false off-TTY; `prompt`/`pick` fall back; `pick` returns the chosen option; multi-select returns a list; `ui.select` still satisfies its existing tests |
| `test_drafts.py` | unsupported span → `("", "")`; empty `used` → `("", "")`; `general` target → no call; verified draft returned with its citation; model failure → `("", "")` |

The two highest-value tests, given the risk each guards:
`test_merge.py::test_matched_entry_preserves_notes` and
`test_skills.py::test_candidate_without_verbatim_evidence_is_dropped`.

Manual check before done: a real `/batch` over three postings — one Greenhouse
URL, one LinkedIn URL (expected to fall back to paste), one local file — with
`--output-dir` pointed at a scratch directory, never `~/Documents/Applications`.

## Risks

| Risk | Mitigation |
|---|---|
| Merge silently loses notes | Notes never sent to the model; copied verbatim; backup written; dedicated test |
| Derived skills are plausible but untrue | Verbatim-evidence substring check drops them mechanically; user approves each; `implied` unchecked by default |
| Login-walled URL yields a wrong resume | Sanity gate on length and keywords, then paste fallback |
| Parallel scoring garbles output | Results buffered per-future, printed on completion only |
| Shell diverges from one-shot CLI | Shell builds the same Namespace and calls `tailor.run()`; no pipeline duplication |
| A fluent draft answer becomes a fabricated note | Draft assembled only from the target entry's own text; every cited span verified as a substring in Python; unverifiable → empty field with "no material found"; always editable, never auto-accepted |
| Full-screen picker breaks scrollback | Full-screen is transient and restores on close; the transcript is never redrawn by us; input region uses `patch_stdout` so printed lines land in the native buffer |
| prompt_toolkit unavailable or non-TTY | `console.available()` gates every use; all `ui` signatures unchanged so callers fall back silently |
| Test run pollutes real application records | All new tests stub the model and write to tmp_path; manual check uses `--output-dir` |

## Sequencing

1. `ui.py` widgets — needed by 4 and 5
2. `jdsource.py` — self-contained, unblocks `/jd` and `/batch`
3. `install.py` fixes — independent, deliver value immediately
4. `skills.py`
5. `merge.py`
6. `shell.py` — depends on all of the above
7. `batch.py` inside the shell
8. `console.py` — input region and pickers; last because every `ui` function it
   replaces must already be under test, so the existing suites prove the
   fallback path still works
9. `drafts.py` — draft gap answers
