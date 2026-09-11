# Quickstart

Tailor a one-page PDF resume to a job description from your own source
content, with a before/after match score and a report of what changed.
Nothing is invented: the model reorders, reframes, and rewords what is in
`content.json`, and Python checks flag anything it cannot trace to the source.

## 1. Install (5 minutes)

```bash
git clone <this repo> resume-tex && cd resume-tex
pip3 install -r requirements.txt
python3 install.py                      # checks pdflatex + a model CLI, builds your data files
```

You need:

- **pdflatex** — MacTeX / TeX Live / MiKTeX.
- **One model CLI on PATH** — `claude` (Claude Code) or `openclaw`.
  `install.py` detects what you have; `--provider` picks between them.

`install.py` walks you through `constant.json` (name, contact, portfolio links)
and builds `content.json` from a pasted resume or CV. Both files are gitignored —
they never leave your machine except inside model calls.

## 2. Run the shell

```bash
python3 shell.py
```

A full-screen prompt. Type a command, or just paste a path / URL.

| Command | Short | What it does |
|---|---|---|
| `/jd <path\|url> [flags]` | `/j` | Tailor a resume for one JD. Bare `/jd` opens a paste box. |
| `/batch <glob\|dir>` | `/b` | Score several JDs, generate the ones you pick |
| `/new [section]` | `/n` | Add a role / project / cert — paste a blob, the model extracts, you confirm |
| `/edit` | `/e` | Browse `content.json`; rewrite an entry, a bullet, a field, or its notes |
| `/skills` | `/s` | Fill in skill groups, review derived suggestions |
| `/add [path]` | `/a` | Merge another resume/CV into `content.json` |
| `/recompile` | `/rc` | Re-render a saved application from its edited JSON (no model call) |
| `/status` | `/i` | What's loaded, which model, where output goes |
| `/help` · `/exit` | `/h` · `/q` | |

Partial names resolve (`/exi` → `/exit`). Tab completes.

### What a `/jd` run looks like

1. **Baseline score** — an untailored one-page cut of your corpus vs the JD, on a
   6–10 requirement rubric. The same call extracts the JD's key terms.
2. **One question block** — cover letter? extra context? then up to three
   *gap questions* (yes/no, then an optional detail). A "yes + detail" is
   distilled into a source note and routed into every entry it informs.
3. **Generate → compile → re-score** on the *same* rubric. If the tailored page
   scores below the baseline, the model gets the slipped requirements and
   missing terms back as revision notes and tries again (`--max-revisions`,
   default 2). The best attempt ships.
4. **Report** — score delta, `JD terms: 17/22 carried · missing: …`, warnings to
   read before sending, and `match-report.md` next to the PDF.

Output lands in `~/Documents/Applications/<Company>/<Role>/` (or `-o DIR`, or
`output_dir` in `constant.json`): the PDF, the shipped `content.resume.json`,
the JD, and the match report.

### Keys inside the shell

| Key | |
|---|---|
| `Enter` / `Esc Enter` | submit / newline in a multi-line answer |
| `Space` · `a` · `Enter` | toggle · all · submit in a multi-select |
| Click summary · `F4` · `Ctrl-O` | expand / collapse the fetched JD in place |
| Wheel · `PgUp` `PgDn` · `Ctrl-Home` `Ctrl-End` | scroll the output pane |
| `F2` | drop mouse capture so the terminal can select and copy text |
| `Esc Esc` | cancel the running command or question |

## 3. Without the shell

```bash
python3 tailor.py jd.txt                 # interactive: score, questions, generate
python3 tailor.py jd.txt -y              # straight through, no questions
python3 tailor.py jd.txt --cv            # multi-page CV instead of one page
python3 tailor.py jd.txt -c              # also write a cover-letter prompt
python3 tailor.py jd.txt --no-score      # skip both score calls
python3 tailor.py jd.txt --max-revisions 0
python3 recompile.py <application dir>   # re-render after editing the JSON
```

`--preset` picks a model tier; `--score-preset` sets the (cheaper) scorer.
See `python3 tailor.py -h`.

## 4. Keep it honest

- Real numbers only. A metric-less bullet gets a JD-worded purpose clause, not a
  made-up figure. `examples/impact-metrics.md` shows how to whitelist a
  projected figure for one project.
- Read the **Review before sending** lines. They fire on invented collaboration
  (if `solo_worker` is set), unearned seniority, skills with no source quote,
  and dollar figures that aren't yours.
- `match-report.md` lists every JD term that did and didn't land, and one line
  per entry on what was emphasized or condensed.

## Tests

```bash
python3 -m pytest -q
```

Every model call is mocked; the suite never spends tokens.
