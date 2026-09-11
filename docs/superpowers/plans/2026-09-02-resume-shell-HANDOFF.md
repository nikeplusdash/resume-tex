# Handoff — Resume-Tex shell build

## What this project is

Resume-Tex is a Python CLI that generates JD-tailored LaTeX resumes. It reads a
corpus (`content.json` = experience/projects/skills/education + freeform
`notes`; `constant.json` = identity + preferences), scores it against a job
description, asks gap questions, generates a tailored resume, and compiles a
PDF into `~/Documents/Applications/<Company>/<Role>/`.

Both JSON files are **gitignored** — they hold the user's real data and are not
in the repo or in any worktree.

## What is being built

A persistent REPL front door (`shell.py`) plus five capabilities, replacing the
one-shot scripts without removing them. Full detail in:

- **Spec:** `docs/superpowers/specs/2026-09-02-resume-shell-design.md`
- **Plan:** `docs/superpowers/plans/2026-09-02-resume-shell.md` (13 tasks, each
  with complete test + implementation code)
- **Ledger:** `.superpowers/sdd/2026-09-02-resume-shell/progress.md` — every
  dispatch, review verdict, ruling, and deferred minor. **Read this first.**

## Where the work is happening

- Worktree: `.claude/worktrees/resume-shell`, branch `resume-shell`, based on
  `c272a3b`. Work only here.
- `main` is untouched apart from three pre-branch commits: `2366233` (the
  user's own uncommitted work, committed so the branch could inherit it),
  `c272a3b` (gitignore).
- The user is **concurrently editing `main`** in the parent directory, fixing
  resume spacing after a manual CV merge. If their fix touches `tailor.py` or
  `template.tex`, expect a merge — Tasks 8, 12 and 13 also edit `tailor.py`.

## Execution method

superpowers:subagent-driven-development. Per task: extract a brief with
`scripts/task-brief PLAN N`, dispatch a fresh implementer (never in parallel),
generate a review package with `scripts/review-package PLAN BASE HEAD`,
dispatch a task reviewer, then a fix loop (max 5 rounds) with a scoped
re-review each round. Scripts live at
`~/.claude/plugins/cache/claude-plugins-official/superpowers/6.3.0/skills/subagent-driven-development/scripts/`.

Models used: haiku for implementers (the briefs contain complete code, so the
work is transcription plus testing), sonnet for task reviews, haiku for scoped
re-reviews.

## Status

Suite was **188 passing** at branch point. Now **225 passing**.

| Task | State | Commits |
|---|---|---|
| 1 · `ui.py` widgets (`table`, `evidence_checkbox`, `merge_diff`) | ✅ complete | `b2b7e62..9443f5c` |
| 2 · `jdsource.py` — JD from path or URL | ✅ complete | `9443f5c..852e33f` |
| 3 · OpenClaw plugin gate | ✅ complete | `145faaf..985b5b1` |
| 4 · Skippable backend step + wording | fix round 1 re-review in flight | `985b5b1..75e8753` |
| 5 · Per-entry notes guidance | not started | — |
| 6 · `skills.py` — fill + evidence-linked derive | not started | — |
| 7 · `merge.py` — add a resume, preserve notes | not started | — |
| 8 · `shell.py` — the REPL | not started | — |
| 9 · `batch.py` — score many, pick, generate | not started | — |
| 13 · Note targeting + education targets + newline storage | not started | — |
| 11 · `console.py` — input region + full-screen picker | not started | — |
| 12 · `drafts.py` — evidence-only draft answers | not started | — |
| 10 · README + manual verification | last | — |

**Execution order: 1→9, then 13, 11, 12, then 10.** Task 13 must precede Task 12
because `drafts.py` reads notes as source text and needs the fixed shape.

## Load-bearing invariants — do not weaken these

Three separate features feed `content.json`, and a wrong claim there is
permanent: every future application reuses it. Each has a **mechanical Python
check**, not a model instruction:

1. **`skills.derive`** drops any candidate whose `evidence` string is not a
   verbatim substring of the user's own bullets/notes.
2. **`drafts.draft_for`** returns `("", "")` unless every span the model claims
   to have used is found verbatim in the target entry's source text.
3. **`merge.apply`** never sends existing `notes` to the model — they are
   copied through in Python. `content.json.bak` is written before any merge.

Also: `jdsource.looks_like_jd` gates fetched URLs (≥300 chars + JD keywords)
so a login-wall page can never be passed off as a posting.

## Gotchas discovered the hard way

- **`looks_like_jd` returns `""` on SUCCESS.** The plan originally had
  `if not looks_like_jd(text): raise`, inverted on both branches. Fixed.
- **`recompile_menu` has no `main()`**, and `recompile.main()` calls
  `ap.parse_args()` with no argv — from a REPL it would parse the shell's own
  arguments. Call `recompile.recompile_folder` / `recompile_menu.run_menu`
  directly.
- **`console.available()` reads `sys.stdin.isatty()`**, but `test_ui.py`'s
  autouse `_no_tty` fixture patches `ui.interactive`. Every `ui` delegation to
  console must be guarded on `interactive() and console.available()`, or the
  existing widget tests break under `pytest -s`.
- **`tailor.run()` reads `args.jd_file`**, and both it and `read_jd` call
  `sys.exit(1)`. The shell's dispatcher must catch `SystemExit` alongside
  `Exception`, or a missing JD kills the session.
- **Python floor is 3.9**, macOS system Python. `urllib3 2.6.3` emits
  `NotOpenSSLWarning` here (LibreSSL 2.8.3, not OpenSSL 1.1.1+). Warning only,
  suite green — but if real HTTPS JD fetching fails for the user, pin
  `urllib3<2`.

## Constraints binding every task

- No test may make a real model call, network request, or subprocess.
- Tests write only to `tmp_path`. **Never** write to `~/Documents/Applications`
  — that holds the user's real application records.
- JSON sent to a model is minified `separators=(",", ":")`; JSON written to
  disk keeps `indent=2` (the user hand-edits those files).
- Commit trailers:
  `Co-Authored-By: Claude <model> <noreply@anthropic.com>` and
  `Claude-Session: https://claude.ai/code/session_01Pd3ymHfez9zTb3kKjF8JZM`
