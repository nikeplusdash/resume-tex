# AI-assisted entry management (`/new`, `/edit`) and a `/recompile` picker — design

Date: 2026-09-05
Status: approved for planning

## Problem

`content.json` is the corpus every tailored application is built from. Two ways
exist to grow it, and both lose detail:

- `/project` (`shell.cmd_project`) takes a title and bullets typed by hand. No
  dates, no location, no link prompt for experience, and no `notes` — so a
  project added this way is thinner than one from the bootstrap, and thinner
  still than one that went through the setup notes walkthrough.
- `/add` merges a whole résumé file. It is all-or-nothing: you cannot add one
  role you just finished without exporting a document first.

There is no way to **edit** an entry at all. A weak bullet, a wrong date, a
missing metric — the only fix is opening `content.json` in a text editor, which
is exactly the failure mode the rest of the shell exists to avoid.

Separately, `/recompile` with no argument defaults to `cwd`, which is almost
never an application folder. To re-render a saved application you must type or
paste its full path, and applications live three directories deep under
`~/Documents/Applications/<Company>/<Role>/`.

## Goals

1. `/new <section>` — build one complete entry from a single pasted blob: the
   model extracts the fields, you correct them, it drafts sharper bullets, and a
   short clarifying loop captures `notes`. Covers every section, not just
   projects.
2. `/edit` — a drill-down browser over `content.json`: pick a section, then an
   entry, then the scope to change (the whole entry, every bullet, one bullet,
   the header fields, or the notes). The scope is wherever you press Enter.
3. AI assists but never fabricates: extraction is verbatim, bullet rewrites use
   only facts already present, and a model never overwrites `notes`.
4. `/recompile` with no path lists saved applications newest-first and lets you
   pick one. `/rc` is a shortcut for `/recompile`.

## Non-goals

- **No change to `tailor.py`'s generation pipeline.** `/new` and `/edit` write
  `content.json`; the next `tailor.run()` picks up the richer corpus with no
  code change there. The only `tailor.py` touch is importing its entry schemas.
- **No live tree widget.** The `/edit` drill-down is a sequence of inline
  pickers (`console.ask_choice`) with a `← back` row at each level, not one
  expand/collapse view. A real tree is a later enhancement.
- **No new model calls in `/recompile`.** It stays a pure render path; the
  picker only lists directories and reads mtimes.
- **`/add` is unchanged.** Whole-file merge stays its own command. `/new` is the
  single-entry, paste-a-blob path.
- No undo beyond the existing backup. Every write is preceded by
  `merge.backup(root)`; recovery is restoring a `.bak`, same as `/add` today.

## Architecture

### New module: `entries.py`

Owns `/new` and `/edit`. `shell.cmd_new` / `shell.cmd_edit` are thin wrappers
that pass `session.root`, `session.backend`, `session.model` and call
`session.reload()` afterward — the same shape as `cmd_add` / `cmd_skills`.

Depends on: `llm`, `ui`, `console` (for `ask_choice`), `merge` (for `backup`),
`install.entry_prompts` (clarifying questions), `tailor.distill_note` (answer →
note), `tailor.{ExperienceEntry,ProjectEntry,EducationEntry,CertEntry,
AwardEntry,PubEntry,LangEntry,SkillGroup}` (extraction schemas),
`profile.Profile` (to resolve note targets and reuse `append_note` semantics).

No import of `entries` from `tailor.py` or `profile.py` — the dependency points
one way, shell → entries → {llm, tailor schemas, merge, install helpers}.

### Section registry

A module-level table is the single source of truth for what is editable:

```python
@dataclass(frozen=True)
class Section:
    name: str            # user-facing + the /new argument: "experience", "project", ...
    key: str             # content.json key: "experience", "projects", ...
    schema: type         # pydantic model for extraction
    fields: tuple        # ordered (field_name, prompt_label, required: bool)
    ref: Callable        # entry dict -> one-line label for pickers
    rich: bool           # True => has bullets + notes

SECTIONS = {
  "experience":   Section("experience", "experience", ExperienceEntry,
                    (("company","Company",True), ("title","Title",True),
                     ("dates","Dates (e.g. Nov 2025 – Present)",True),
                     ("location","Location",False)),
                    lambda e: f"{e.get('company','?')} · {e.get('title','?')}", True),
  "project":      Section("project", "projects", ProjectEntry,
                    (("title","Title",True), ("short_link","Short link",False)),
                    lambda e: e.get("title","?"), True),
  "education":    Section("education", "education", EducationEntry,
                    (("institution","Institution",True), ("degree","Degree",True),
                     ("gpa","GPA",False), ("start","Start",False),
                     ("end","End",False), ("location","Location",False)),
                    lambda e: f"{e.get('institution','?')} — {e.get('degree','')}".strip(" —"),
                    False),
  "certification":Section("certification", "certifications", CertEntry,
                    (("name","Name",True), ("issuer","Issuer",False), ("date","Date",False)),
                    lambda e: f"{e.get('name','?')} ({e.get('issuer','')})".strip(" ()"), False),
  "award":        Section("award", "awards", AwardEntry,
                    (("title","Title",True), ("body","Awarding body",False), ("year","Year",False)),
                    lambda e: f"{e.get('title','?')} — {e.get('year','')}".strip(" —"), False),
  "publication":  Section("publication", "publications", PubEntry,
                    (("title","Title",True), ("venue","Venue",False), ("year","Year",False)),
                    lambda e: f"{e.get('title','?')} — {e.get('venue','')}".strip(" —"), False),
  "language":     Section("language", "languages", LangEntry,
                    (("language","Language",True), ("proficiency","Proficiency",False)),
                    lambda e: f"{e.get('language','?')} — {e.get('proficiency','')}".strip(" —"), False),
}
```

`skill` is **special-cased**, not a `Section`: its shape is `{label, entries[]}`
and it already has `/skills`. `/new` → "skill" and `/edit` → "skills" route to a
small dedicated path (add entries to a group, or start a new group). Kept in
`entries.py` for one entry point but with its own two functions.

Only `experience` and `project` are `rich`. Section membership of the registry
is asserted by a test against `tailor.py`'s schema list so a newly added schema
is a deliberate choice, not an omission.

### `/new` flow

`entries.cmd_new(root, *, backend, model) -> bool` (True on write):

1. **Pick section** — `console.ask_choice("Add what?", [names…])`. `/new project`
   skips this (argument names the section).
2. **Paste** — `ui.editor("Paste everything you have about this — a blurb, rough
   bullets, the role, dates, links. More is better (Enter to skip):")`. Empty →
   fall through to step 4 with an empty entry (pure manual path still works).
3. **Extract** — `entries.extract(section, blob, backend=…, model=…) -> dict`.
   One `llm.complete_schema` call, `EXTRACT_ENTRY_SYSTEM` + `section.schema`,
   `thinking="low"`, wrapped in `ui.spinner`. On `LLMError`: warn, return `{}`,
   continue to step 4 (manual).
   `entries.extract` validates against a **lenient copy** of `section.schema` —
   every field optional, `str` default `""`, `bullets` default `[]` — built once
   by `_lenient(schema)` (pydantic `create_model` off the original's fields).
   `tailor.py`'s schemas require most fields; a pasted blob rarely has all of
   them, and a missing GPA or end-date must not fail the call.
4. **Confirm fields** — for each `(field, label, required)` in `section.fields`:
   `ui.text(label, default=extracted.get(field, ""))`. A blank `required` field
   re-prompts once, then aborts with a message.
5. **Bullets** *(rich only)*:
   - Show extracted bullets. `console.ask_choice(..., multi=True)` to keep/drop.
   - `ui.editor("Add more bullets, one per line (Enter to skip):")` — split on
     newlines, append.
   - If any bullets: `ui.confirm("Let the model sharpen these bullets?",
     default=True)` → `entries.reform_bullets(bullets, entry, backend, model)`,
     print `old → new` per line, `ask_choice(multi=True, precheck=all)` which
     rewrites to accept.
6. **Clarify → notes** *(rich only)*: `install.entry_prompts([(kind, entry,
   label)], backend, model)` → print the 2-3 questions; `ui.editor("Answer any
   of these (Enter when done):")`; if non-blank, `tailor.distill_note(label,
   raw, …)` → append to `entry["notes"]` (newline-joined, mirroring
   `Profile.append_note`). Loop until blank.
7. **Preview + write** — `ui.box([...], title=f"new {section.name}")` showing
   every field, bullet and the notes; `ui.confirm("Add this to content.json?",
   default=True)`; `merge.backup(root)`; `content[section.key].append(entry)`
   (creating the list if absent); write with
   `json.dumps(..., indent=2, ensure_ascii=False) + "\n"`; print
   `f"  Added {section.name}: {section.ref(entry)}"`.

### `/edit` flow

`entries.cmd_edit(root, *, backend, model) -> bool`. A loop of inline pickers,
each level offering `← back` (returns to the previous level) and the top level
offering `← cancel` (returns False, no write). Breadcrumb printed above each
picker via `ask_choice`'s label.

1. **Section** — `ask_choice("Edit what?", ["experience (6)", "projects (12)",
   "education (2)", "skills (56 across 7 groups)", …, "← cancel"])`. Sections
   with zero entries are omitted. Counts come from `content`.
2. **Entry** — `ask_choice(f"{section} — pick one", [section.ref(e) for e in …]
   + ["← back"])`.
3. **Scope** *(rich entry)* —
   ```
   ask_choice(f"{ref} — edit what?", [
     "▸ Whole entry",
     "▸ Reform every bullet",
     *[f"• {b}" for b in entry['bullets']],
     "▸ Header fields",
     "▸ Notes / add context",
     "← back",
   ])
   ```
   *(flat entry)* — skip step 3, go straight to per-field `ui.text(label,
   default=current)` for each `section.fields`, then write.

   - **Whole entry** → the `/new` steps 2-7, pre-seeded: step 2's editor
     `default` is a readable dump of the current entry; step 4 defaults are the
     current field values; step 5 starts from the current bullets; step 6 shows
     the current `notes` first and appends. Full box preview shows a `- old` /
     `+ new` diff. One confirm, one write.
   - **Reform every bullet** → `reform_bullets(entry['bullets'], entry, …)`,
     `old → new` per line, `ask_choice(multi=True, precheck=all)` which to keep,
     write the accepted set.
   - **• one bullet** → `ui.editor("Rewrite it yourself, or leave blank and let
     the model sharpen it:", default=bullet)`. Blank → `reform_bullets([bullet],
     entry, …)[0]`. Print `old → new`. Then `ask_choice(["Save this rewrite",
     "Delete this bullet", "← back"])`. Save → replace in place; Delete → remove
     from list; write on Save/Delete.
   - **Header fields** → per-field `ui.text(label, default=current)`; write.
   - **Notes / add context** → `ask_choice(["Edit the note text directly",
     "Answer questions → distilled note", "← back"])`.
     - *Edit directly*: `ui.editor("Notes:", default=entry.get('notes',''))` →
       **replaces** `entry['notes']`. This is the one place a human, not a
       model, may overwrite notes.
     - *Answer questions*: `install.entry_prompts` + `ui.editor` +
       `tailor.distill_note` → **appends**.
4. **Write** — every write path: `merge.backup(root)` once, mutate the loaded
   `content` dict in place, `content_path.write_text(...)`, print a one-line
   summary (`  Acme Corp · Product Designer: bullet 2 rewritten`),
   `session.reload()` (in the shell wrapper). After a write, `/edit` returns to
   the **Scope** picker for the same entry (so several edits in a row cost one
   navigation), until `← back` / `← cancel`.

### New prompts (in `entries.py`)

```
EXTRACT_ENTRY_SYSTEM = (
  "Extract the fields for ONE {section} entry from the text below. Copy facts "
  "verbatim — company, title, dates, names, numbers, links. Never invent or "
  "infer a date, metric, title, or tool that the text does not state; leave a "
  "field empty instead. For bullets: one achievement per line, past tense, lead "
  "with the action, keep every number exactly as written, no first person, no "
  "marketing words (spearheaded, robust, seamless, passionate). Return the "
  "schema only."
)

REFORM_BULLET_SYSTEM = (
  "Rewrite this résumé bullet so it is tighter and more concrete. Use ONLY facts "
  "already present in: the bullet itself, the entry's other bullets, its notes, "
  "and the applicant's hint. Keep every number exactly. Past tense, no first "
  "person, no marketing words (spearheaded, robust, seamless, leveraged, "
  "passionate). If nothing can be improved without inventing, return the bullet "
  "unchanged. Output the single bullet line only, nothing else."
)
```

`reform_bullets(bullets, entry, *, backend, model) -> list[str]` makes one
`llm.complete_text` call per bullet (bullets are short; batching a schema adds
failure surface for little gain), each with the entry's other bullets + notes as
context. On `LLMError` for a given bullet, keep the original and print a note.

### `/recompile` picker + `/rc`

`recompile.recent_apps(base: Path) -> list[dict]`:

```python
# one dict per <Company>/<Role> folder that holds a recompilable JSON:
# {"dir": Path, "ref": "Company · Role", "mtime": float,
#  "modes": ["resume", "cv"]}   # whichever of content.{resume,cv}.json exist
```

Scans `base.glob("*/*/")`; a folder qualifies if it contains
`content.resume.json` or `content.cv.json`. `mtime` is the newest of those
files. `ref` is `f"{dir.parent.name} · {dir.name}"` with underscores → spaces.
Sorted by `mtime` descending. Missing `base` → `[]`.

`shell.cmd_recompile` change: when `positional` is empty **and**
`ui.interactive()` (the picker needs a person; a piped/CI `/recompile` with no
path keeps today's `cwd` fallback):

1. `base = Path(session.output_dir or profile.output_dir or
   Path.home()/"Documents"/"Applications")`.
2. `apps = recompile.recent_apps(base)`. Empty → today's behaviour (fall back to
   `cwd`, which errors helpfully).
3. `console.ask_choice("Recompile which application?", [
      (a["dir"], f"{a['ref']}   {_ago(a['mtime'])}   {'+'.join(a['modes'])}")
      for a in apps ] + [(None, "← cancel")])`.
4. `None` → return. Otherwise recompile the chosen dir. Mode: `--cv` flag wins;
   else if the folder has only `cv`, use `cv`; else `resume`. `-i` /
   `--interactive` still routes through `recompile_menu.run_menu` first.
5. A positional path still bypasses the picker entirely.

`_ago(mtime)` — a tiny relative formatter (`"2d ago"`, `"3w ago"`, `"just now"`)
local to `shell.py` or `ui.py`.

### Command wiring (`shell.py`)

- `COMMANDS` gains `"/new": "cmd_new"`, `"/edit": "cmd_edit"`. Drops
  `"/project": "cmd_project"`.
- `cmd_project` and its tests deleted.
- `_HELP` rows:
  ```
  ("/new",  "n", "[section]", "add one entry — model extracts, you confirm"),
  ("/edit", "e", "",          "browse content.json and change an entry or a bullet"),
  ```
  `/project` row removed. `/recompile` row unchanged (already `·r`).
- `SHORTCUTS` is rebuilt from `_HELP`, so `·n`/`·e` come for free. Add `/rc`
  explicitly: `SHORTCUTS["/rc"] = "/recompile"` after the comprehension (it is
  two letters, not one, so the `_HELP` letter column stays `r`).
- `cmd_new(session, args)` → `entries.cmd_new(session.root, backend=…, model=…,
  section=args[0] if args else None)`; then `session.reload()` if it returned
  True.
- `cmd_edit(session, args)` → `entries.cmd_edit(session.root, backend=…,
  model=…)`; `session.reload()` if True.

## Data flow

```
/new experience
  └─ ui.editor(blob)
       └─ entries.extract(section, blob) ── llm.complete_schema(EXTRACT_ENTRY_SYSTEM, ExperienceEntry)
            └─ ui.text × fields (defaults from extract)
                 └─ ask_choice(bullets, multi) + ui.editor(more)
                      └─ entries.reform_bullets ── llm.complete_text(REFORM_BULLET_SYSTEM) × N
                           └─ install.entry_prompts ── llm.complete_schema(EntryPrompts)
                                └─ ui.editor(answer) ── tailor.distill_note ── llm.complete_text
                                     └─ ui.box(preview) ─ ui.confirm ─ merge.backup ─ write ─ session.reload

/edit
  └─ ask_choice(section) ─ ask_choice(entry) ─ ask_choice(scope)
       ├─ whole entry     → /new steps, pre-seeded
       ├─ every bullet    → reform_bullets → ask_choice(multi, which to keep)
       ├─ one bullet      → ui.editor(default=bullet) | reform_bullets([bullet]) → Save/Delete
       ├─ header fields   → ui.text × fields
       └─ notes           → ui.editor(replace) | entry_prompts → distill_note (append)
     every write: merge.backup → mutate content dict → write → session.reload → back to scope picker

/recompile   (no path, interactive)
  └─ recompile.recent_apps(base) ─ ask_choice(newest first) ─ recompile.recompile_folder(dir, mode)
```

## Error handling

- **Extraction fails** (`LLMError`): print `f"  couldn't read that automatically
  — fill it in below"`, proceed manual with empty defaults. Never abort `/new`
  on a model failure.
- **`reform_bullets` fails for a bullet**: keep the original, print
  `f"  (kept bullet {i} as-is — model unavailable)"`. Never lose a bullet.
- **Required field left blank twice**: `print("  Need a {label}. Nothing
  saved.")`, return False. Backup not taken (no write happened).
- **`/edit` on an empty `content.json`** (no sections): `print("  Nothing to
  edit yet — /new or /add first.")`, return False.
- **`recent_apps` on a missing / empty applications dir**: picker is skipped,
  `cmd_recompile` falls back to `cwd`, whose existing "not found" message names
  the folder it looked in.
- **`KeyboardInterrupt` / double-Esc mid-flow**: `console.ask_choice` /
  `ui.editor` already return `""` / the default on cancel; `cmd_new` treats a
  cancelled section pick or a cancelled final confirm as "no write, return
  False". No partial write is ever possible — the single `merge.backup` +
  `write` is the last step.
- **Concurrent edit**: `content.json` is re-read from disk at the start of every
  write path (not held from command start), so a `/skills` run between two
  `/edit` writes is not clobbered. The loaded dict for display may be stale;
  acceptable — the next picker refetches.

## Testing model

`test_entries.py` (all model calls mocked via `llm.complete_schema` /
`llm.complete_text`, `console.ask_choice` / `ui.*` patched with queues):

- `extract` maps a blob to a section dict; `LLMError` → `{}` and a printed note.
- Section registry covers every schema `tailor.py` exposes (guard test).
- `/new` rich path: extract → field confirm (blank required re-prompts) →
  bullet keep/drop → reform accept-subset → clarify appends a distilled note →
  preview → backup taken exactly once → entry appended → list created if absent.
- `/new` flat path (education): no bullets/notes steps, straight to field form.
- `/new` manual path: empty blob, no model call, fields typed, saves.
- `reform_bullets`: one `complete_text` per bullet; a failing bullet is returned
  unchanged with a note; numbers in the prompt context.
- `/edit` navigator: a queued sequence of `ask_choice` returns drives
  section → entry → one-bullet → "Save"; asserts the bullet replaced in place,
  backup once, `content.json` on disk updated, other entries untouched.
- `/edit` "delete this bullet" removes it; "every bullet" writes the accepted
  subset; "header fields" changes a field; "notes → edit directly" replaces,
  "notes → questions" appends.
- `/edit` `← back` at each level returns without a write; `← cancel` returns
  False; empty `content.json` → the "nothing to edit" message.
- Notes are never overwritten by a model on any `/edit` path except the explicit
  "Edit the note text directly" choice.

`test_recompile.py` additions:

- `recent_apps(tmp_tree)` finds only folders with a `content.{resume,cv}.json`,
  sets `modes` correctly for resume-only / cv-only / both, sorts newest first,
  returns `[]` for a missing base.
- `_ago` boundaries (`< 1m` → "just now", days, weeks).

`test_shell.py` additions:

- `/new` and `/edit` dispatch to `entries.*` with root/backend/model and call
  `session.reload()` on a True return.
- `/rc` resolves to `/recompile`; `·n` → `/new`, `·e` → `/edit`.
- `cmd_recompile` with no path calls `recompile.recent_apps` and recompiles the
  picked dir; a positional path skips the picker; `--cv` forces cv mode.
- `/help` lists `/new` and `/edit`, no longer lists `/project`.

`test_console.py` addition (headless full-screen):

- `/new` end to end with a mocked model — section pick, paste, field defaults
  shown, bullet picker, confirm, and `content.json` gains the entry.
- `/edit` → drill to one bullet → blank editor → reform → "Save" → file updated.

## Migration / compatibility

- `content.json` schema is unchanged. `/new` and `/edit` only ever add the keys
  the section already uses (`notes`/`bullets` for rich types).
- Existing `content.json` files with no `certifications` / `awards` / etc. key:
  `/new certification` creates the list on first use.
- `/project` disappearing is a visible change; `/help` and the removed row make
  it discoverable that `/new` replaces it. `cmd_project`'s tests are deleted,
  not skipped.
- `.resume_shell.log` and the backup rotation are reused as-is.
