# Resume-Tex

Point it at a job description; it returns a typeset, one-page PDF resume tailored to that
job, plus a match report telling you where you actually stand.

The model never invents anything. It reorders, reframes, and rewords **your** source
content against the JD, and a set of Python checks re-reads what it produced and flags
anything you would not want to defend in an interview.

```
python3 tailor.py jd.txt
```

```
Mode      : RESUME (source: content.json)
Model     : gpt-5.6-terra via openclaw
Asking openai/gpt-5.6-terra to tailor your resume (thinking=medium)...

Company   : Meridian Care Systems
Role      : Senior Product Designer — Patient Experience
Portfolio : example.com
Location  : Seattle, WA (JD default)
Compiling PDF...
Pages     : 1 (single page OK)
Fill      : 93% of page (53pt slack)
Saved PDF : .../Meridian_Care_Systems/Senior_Product_Designer/Alex_Rivera_Resume.pdf

Match      : untailored 1-page 71/100 -> JD-tailored 96/100 (+25)
JD terms   : 19/22 carried · missing: supplier experience, SQL, Jira
```

On a terminal the run is interactive — it scores the JD first, offers a few
gap questions, then generates. Pipe a JD in or pass `-y` to skip straight through.
See [The interactive flow](#the-interactive-flow).

---

## What it actually does

1. **Sends your whole corpus, not a pre-trimmed resume.** Every role, project, skill, and
   the `notes` field behind each one.
2. **Asks the model to over-generate a ranked superset** — more content than fits, ordered
   by relevance to this JD.
3. **Compiles, measures, and trims from the end of each list** until the PDF is exactly one
   page with no orphan lines. Ranking is the model's job; fitting is Python's.
4. **Lints the result against your source.** Invented dollar figures, skills with no
   evidence, claimed seniority you do not have, claimed teamwork that never happened — each
   one printed as a warning before you send anything.
5. **Writes a match report** scoring you against the JD's real requirements, with the score
   recomputed in Python so an encouraging-but-wrong number cannot slip through.

Every run lands in `~/Documents/Applications/<Company>/<Role>/`: the PDF, the JD, the
tailored JSON, and the match report. Change the base with `-o`, `$RESUME_TEX_OUTPUT_DIR`,
or `output_dir` in `constant.json`.

---

## Setup

```bash
python3 install.py
```

`install.py` walks you through it:

1. **Dependencies** — checks Python, LaTeX (`pdflatex` + `crimson titlesec enumitem`), Ghostscript; offers to `pip install -r requirements.txt`.
2. **Model CLI** — finds Claude Code and/or OpenClaw on your PATH. With both installed you pick the default and can connect the other too. It then sends a one-line test prompt, because that, not the login command, is what proves the thing works: a CLI that is already connected can still fail its own `auth login` (needing a TTY, or a plugin it does not need for these calls) and generate perfectly well. Records the choice in `constant.json`.
3. **Bootstrap** — point it at your existing resume (`.pdf` / `.docx` / `.txt` / `.md`); it drafts `constant.json` + `content.json` for you to refine. Quotes around a dragged-in path are fine. Defaults to yes on a first run, and to no once you have a `content.json` worth keeping.
4. **Optional sections** — pick any of Certifications / Awards / Publications / Languages.
5. **Preferences** — one portfolio or a visual/research split; a fixed location or per-region rules; solo-worker mode; summary-anchor bans; headings; output directory.
6. **Notes** — for each role and project, add the unpolished context ("why", constraints, the real number) the model writes bullets from.

Re-run `install.py` any time — it reads what is already on disk and every step defaults
to skipping what is configured, so fixing one answer never means re-typing the rest. Slow
steps (bootstrap, note distillation, the model-CLI check) show progress while they run.
There is a ready-made `examples/sample-resume.txt` you can hand step 3 to see the whole
thing end to end.

If a login step fails, setup keeps going and tells you what the CLI said — the answers you
have already given are never thrown away.

### Requirements (what `install.py` checks for)

| | |
|---|---|
| **Python 3.9+** | `pip3 install jinja2 pydantic` (`pip3 install -r requirements.txt` for the tests too) |
| **A LaTeX install** | macOS: [MacTeX](https://tug.org/mactex/) or `brew install --cask basictex` · Linux: `sudo apt install texlive-latex-recommended texlive-fonts-extra` · Windows: [MiKTeX](https://miktex.org/). The template needs the `crimson` font package; BasicTeX users run `sudo tlmgr install crimson titlesec enumitem`. |
| **A model CLI** | one of the two below |
| **Ghostscript** (optional) | `brew install ghostscript` — only used to report how full the page is |

### A model CLI

Runs go through a CLI you already pay for, so nothing here needs an API key or bills per
token. Install **either**:

**Claude Code** — `npm install -g @anthropic-ai/claude-code`, then run `claude` once to log
in to your Claude subscription.

**OpenClaw** — if you already run it, `tailor.py` will use whatever models are bound in
your `~/.openclaw` config, including the `gpt-5.6-*` line.

The provider is detected automatically:

| Installed | Provider used | Default model |
|---|---|---|
| Claude Code only | `claude` | `claude-sonnet-5` |
| OpenClaw only | `openclaw` | `openai/gpt-5.6-terra` |
| Both | `openclaw` | `openai/gpt-5.6-terra` |

Override any of it with `--provider claude`, `--preset opus`, or `--model <id>`.
Why terra when both are available: it won a seven-preset sweep on writing quality, speed,
and how much surplus content it produced for the trimmer to choose from. Re-run `bench.py`
on your own JDs before trusting that for your writing.

### Your data files

`install.py` bootstraps `constant.json` and `content.json` for you. To set them up by
hand instead, copy the examples into the project root and edit them — nothing works until
the first two exist:

```bash
cp examples/constant.json examples/content.json .          # required
cp examples/impact-metrics.md .                            # optional
cp examples/recommendations.json examples/coursework.json .  # optional
```

**`constant.json`** — who you are, and how you want the tailoring to behave.

| Key | What it does |
|---|---|
| `name`, `email`, `phone`, `linkedin`, `location` | The header line of the PDF |
| `backend` | `"claude"` or `"openclaw"` to pin the model CLI; `""` (or absent) auto-detects. `install.py` writes this |
| `portfolios` | `{"visual": "...", "research": "..."}` to have the model pick between two sites by role emphasis, or a single string if you have one |
| `location_rules` | `[{"match": "<regex>", "location": "<city>"}]` — first match against the JD text wins, otherwise `location`. Useful if you list a different base for jobs in another country |
| `solo_worker` | `true` if you genuinely work end-to-end alone. Turns on a prompt rule and a lint that catch invented teammates — JDs are written in the plural and models drift into "partnered with" |
| `banned_summary_anchors` | Phrases or numbers that may appear in bullets but must never headline the summary |
| `skills_title` | The heading over the skills section (default `Skills`) |
| `doc_basename` | PDF filename stem (default: your name) |
| `output_dir` | Where runs are written. Blank/absent → `~/Documents/Applications` |

**`content.json`** — your full corpus. Not a resume; the raw material for one.

- `experience` and `projects` each take a **`notes`** field. This matters more than the
  bullets: notes are unpolished context — why you made a choice, what the constraint was,
  what the number actually measured — and the model writes new JD-relevant bullets out of
  them. Thin notes produce generic resumes.
- `skills` groups can carry any labels you like; whatever you use is what the model is
  told to keep. Put more in than fits — selection is the point.
- Everything here is source-of-truth. A claim not traceable to this file is a bug, and the
  lint is built to catch it.

**`impact-metrics.md`** (optional) — the only place a *projected* figure may come from.
The rule it enforces: a number is allowed only when it sizes the scale of a real problem
citable from external data, never when it prices a business that does not exist or assumes
an efficacy rate for something never built. Each eligible project gets a `###` section and
one `**Approved claim:**` line; anything else the model puts a dollar sign on gets flagged.
Delete the file and no projected figures are ever added.

**`recommendations.json`** (optional) — testimonials rendered verbatim by `-r`.
**`coursework.json`** (optional) — per-institution coursework listed by `-ee`. Keys must
match an `institution` in `content.json` exactly.

### Try it

```bash
python3 tailor.py examples/sample-jd.txt -y
```

Run that before editing anything — with the example data in place it exercises the whole
pipeline and tells you whether LaTeX, the model CLI, and the trimmer all work. `-y` runs
it straight through without the interactive prompts.

---

## The shell

```
python3 shell.py
```

One prompt loop instead of a process per application. `content.json` is loaded
once and stays warm, so the second job description of an evening costs a model
call, not a cold start. It is a full-screen prompt (prompt_toolkit): output
scrolls above, questions and pickers render inline above the input, and the
fetched JD folds into one line you can expand in place.

On a first run with no corpus it runs `install.py` inline first, then opens the
prompt. **[QUICKSTART.md](QUICKSTART.md)** is the short version of everything below.

| Command | Short | What it does |
|---|---|---|
| `/jd <path\|url> [flags]` | `/j` | Tailor a resume for one JD. Any `tailor.py` flag after the path passes through (`--cv`, `-c`, `-e`, …). A bare `/jd` opens a paste box. |
| `/batch <glob\|dir\|file>` | `/b` | Score several job descriptions, then generate the ones you pick |
| `/new [section]` | `/n` | Add one entry: paste a blob, the model extracts the fields, you confirm and sharpen |
| `/edit` | `/e` | Browse `content.json` and rewrite an entry, one bullet, every bullet, a field, or its notes |
| `/skills` | `/s` | Fill in your skill groups, then review derived suggestions |
| `/add [path]` | `/a` | Add another resume or CV into your existing `content.json` |
| `/recompile [dir] [-i] [--cv]` | `/rc` | Re-render a saved application from its edited JSON, no model call; a picker of recent applications when no dir is given |
| `/status` | `/i` | Corpus counts, backend/model, output directory |
| `/setup` | | Re-run `install.py` |
| `/help` · `/exit` | `/h` · `/q` | |

A line that is not a slash command but is a path or a URL is treated as `/jd`.
A partial command resolves to the first match (`/exi` → `/exit`); Tab completes.
Every command builds a real argv list and hands it to `tailor.parse_args`, so
flag defaults are inherited rather than re-derived.

Keys: `Space`/`a`/`Enter` toggle, select-all, submit in a multi-select ·
`Esc Enter` newline in a multi-line answer · click the summary, `F4`, or
`Ctrl-O` to expand/collapse the fetched JD · wheel, `PgUp`/`PgDn`,
`Ctrl-Home`/`Ctrl-End` scroll the output · `F2` releases the mouse so the
terminal can select and copy · `Esc Esc` cancels the running command.

### Job descriptions by URL

`/jd <url>` fetches the page and extracts the posting, then checks the result
actually reads like a job description (length, and words like "responsibilities"
/ "qualifications" / "requirements") before using it.

- **Static boards work** — Greenhouse, Lever and Ashby serve the posting as HTML.
- **JS-rendered or login-walled boards do not** — LinkedIn, Workday and similar
  return a shell page or a sign-in wall. That fails the check and drops you into
  a paste box instead.

This is deliberate. Passing a login wall's HTML through to the generator would
produce a confidently wrong resume, which is worse than one more prompt.

### `/batch`

Point it at a directory, a glob, or a text file of URLs (one per line):

```
/batch ~/jobs/                 # every .txt in the folder
/batch '~/jobs/*.md'           # explicit glob — suffix not filtered
/batch urls.txt                # a file whose every non-blank line is a URL
```

A directory scan takes `.txt` only — a saved-postings folder usually also holds
a `README.md` or scratch notes, and a wrong guess there costs a resume compiled
against a non-posting and filed into your records. An explicit glob is how you
pull in other suffixes; that path does not filter.

The order is fixed: score everything first (cheap model, three at a time), show
a ranked table, ask which to generate (`all` / `top 3` / `pick` / `none`),
collect gap answers for the ones you picked, then generate them unattended.
Every question a human answers happens before the slow part starts. Scoring
failures are reported and dropped, never fatal.

### `/skills`

Fill in your skill groups by hand, then review suggestions derived from your own
`content.json`. Each suggestion shows the verbatim span from one of your bullets
or notes that supports it; that span is re-checked as a substring in Python, and
anything that cannot be traced to your own words — or whose evidence is too thin
to support an inferred skill — is dropped before you see it. Suggestions you
turn down can optionally be suppressed for good (recorded as `rejected_skills`
in `constant.json`).

### `/add`

Merge another resume or CV into your existing `content.json`.

- Your **notes are never sent to the model.** It sees bullets only, and proposes
  which entries correspond and which incoming bullets are new.
- Matched entries **keep their notes verbatim.** Conflicting dates or titles are
  **reported, not resolved** — you fix them by hand.
- A **timestamped, non-clobbering backup** of `content.json` is written before it
  is rewritten (`.<timestamp>.bak`). `/setup`'s bootstrap rebuild snapshots
  `constant.json` the same way.
- Nothing is written until you have seen the plan and confirmed it.

### Notes and gap answers

A gap answer you type is distilled to a line or two and appended to a `notes`
field in `content.json`, where every later run reuses it — so where it lands
matters. If the model's guess at the target entry is exact it is used silently;
otherwise you are asked which experience, project, or education entry the note is
about (or "general", or skip). Nothing goes to a catch-all bucket by guess.

Where you already have material on that entry, the gap question is **pre-filled
with a draft** built only from that entry's own bullets and notes, shown with a
citation. Every span the draft claims to use is verified as a substring of your
text; if it cannot be traced, or there is no material, the field is left empty
and the prompt says `no material found for this`.

---

The one-shot CLI is unchanged. `python3 tailor.py jd.txt` and every flag below
work exactly as before — the shell is a loop around the same pipeline, not a
replacement for it.

---

## Using it

```bash
python3 tailor.py jd.txt                    # one-page resume
python3 tailor.py jd.txt --cv               # multi-page CV, everything included
cat jd.txt | python3 tailor.py              # or pipe the JD in (skips the prompts)
python3 tailor.py                           # or paste it, Ctrl-D to end

python3 tailor.py jd.txt -y                 # straight through: score, then generate, no prompts
python3 tailor.py jd.txt -e                 # force education in (the JD-based decision is automatic otherwise)
python3 tailor.py jd.txt -ee                # education + coursework, always
python3 tailor.py jd.txt -r                 # + recommendations
python3 tailor.py jd.txt -c                 # + a cover-letter prompt (always; no prompt)
python3 tailor.py jd.txt -x "referred by Dana Chen"   # extra context for this application
python3 tailor.py jd.txt -l "New York, NY"  # override the location shown
python3 tailor.py jd.txt -o ~/Applications  # override where output lands

python3 tailor.py jd.txt --provider claude --preset opus
python3 tailor.py jd.txt --score-preset haiku   # model for the baseline score step
python3 tailor.py jd.txt --thinking high    # OpenClaw only
```

| Flag | Effect |
|---|---|
| `--no-interactive` / `-y` | Compute the baseline score, then generate — skip the gap-question and cover-letter prompts. Implied when a JD is piped in |
| `--score-preset NAME` | Preset for the baseline score. Default: one tier below the generation model |
| `-c` / `--cover-letter` | Always write `cover-letter-prompt.md`; no prompt |
| `-o DIR` / `--output-dir` | Output base. Beats `$RESUME_TEX_OUTPUT_DIR`, which beats `output_dir` in `constant.json`, which falls back to `~/Documents/Applications` |

### Read the warnings

The last thing a run prints is the part worth your attention:

```
Review before sending:
  ! summary claims seniority absent from the candidate's source titles
  ! project/Pantry: INVENTED figure $4M — not in the source, and not an approved claim
  ! skills/Technical Skills: new entry 'Kubernetes' has no evidence-backed inference record
```

Each one is a claim you would have to defend. Fix the source, re-run, or cut the line —
but do not send a resume with warnings you have not read. Same for
`Questions that could improve this resume:` — those are usually pointing at real material
missing from your `notes`.

---

## The interactive flow

`python3 tailor.py jd.txt` on a terminal:

1. **Score first** — a cheaper model (one tier below your generation model) scores the JD
   against your current `content.json` and shows the requirement breakdown.
2. **Gap questions** *(optional, default No)* — 3–4 questions, each tagged with the match
   points it could recover. Your answers are distilled to one or two lines and appended to
   the relevant `notes` in `content.json`, so they help every future run too.
3. **Generate** — the tailored one-page resume + match report, as before.
4. **Cover letter** *(prompt, default No)* — writes a `cover-letter-prompt.md` to work
   from. `-c` skips the prompt and always writes it.

The education line is always printed — whether it was included, omitted, or a warning
that the JD wants a degree your `content.json` does not list. The final line shows
`baseline NN → tailored NN`. Pipe a JD in, or pass `-y`, to skip the questions and run
straight through.

## Editing and regenerating

Every run writes `content.resume.json` next to the PDF. Edit it and:

```bash
python3 recompile.py <Company>/<Role>/        # re-render, no model call
python3 recompile.py <Company>/<Role>/ -i     # menu: add education / certifications / awards / publications / languages / recommendations
```

`recompile.py` reads that folder's own `content.<mode>.json` verbatim — identity and the
LaTeX template come from the package, everything else is exactly what you edited. Add
`--cv` to work from `content.cv.json`.

---

## Benchmarking presets

```bash
python3 bench.py jd.txt                          # every preset, thinking=medium
python3 bench.py jd.txt -p sonnet,opus -k medium,high
python3 bench.py jd.txt --repeat 3               # run-to-run variance
python3 bench.py jd.txt --dry-run                # print the matrix and stop
```

Output goes to `./bench-runs/<timestamp>/` with a `summary.md` comparing wall time,
integrity warnings, mechanical rule violations, how much the trimmer had to cut, and page
fill. It refuses to write anywhere near your real output directory.

## Tests

```bash
python3 -m pytest -q      # no model calls, no network
```

They cover the two CLI argv shapes, both response parsers, schema validation and its single
retry, portfolio selection, the match-score arithmetic, and every branch of the integrity
lint. Run them after editing prompts or config — several pin exact prompt rules, so a test
failure often means you deleted a rule you meant to keep.

---

## Files

```
tailor.py          the pipeline: prompt, lint, trim, compile
shell.py           persistent session: one warm corpus, command dispatch over the pipeline
batch.py           /batch: score many JDs in parallel, then generate the picked ones
jdsource.py        resolve a JD from a local path or a URL; paste fallback on a login wall
console.py         bottom-anchored input region and full-screen pickers, with a non-TTY fallback
skills.py          /skills: fill skill groups, derive more with evidence checked in Python
merge.py           /add: fold another resume into content.json; notes preserved, backups first
notes.py           where a distilled note attaches; asks when the target is not certain
drafts.py          pre-fill a gap answer from the entry's own text, verified span by span
install.py         guided setup: deps, model CLI, bootstrap, preferences, notes
recompile.py       re-render a saved application from its edited JSON, no model call
profile.py         constant.json + content.json loader, prompt assembly, note writes
ui.py              interactive widgets and panels, with a non-TTY fallback
llm.py             both CLI backends, schema validation, retry
template.tex       the LaTeX resume template (Jinja, (( )) delimiters)
build.py           render the template from content.json with no model call
bench.py           preset sweep
examples/          data files to copy and edit, plus a sample JD and sample resume
```

## Notes and limits

- **The prompt is opinionated, and it is tuned to design and research work** — two
  portfolios split by emphasis, a specialist-framing rule, an anti-fabrication section that
  names the failure modes seen in that domain. `SYSTEM_PROMPT_BASE` in `tailor.py` is meant
  to be edited. If you are in another field, read it once and rewrite what does not fit.
- **The match score is a resume-evidence diagnostic, not a prediction.** Direct evidence
  scores full, adjacent half, missing zero. A low score usually means the resume does not
  yet show something you have actually done.
- **Neither CLI has a structured-output mode**, so the JSON schema travels in the prompt and
  is validated afterward, with one correction round. An occasional retry line in the output
  is normal.
- **The one-page trim is real trimming.** What ships is the trimmed JSON, saved next to the
  PDF, so you can always see exactly what the employer got.
