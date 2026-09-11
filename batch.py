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
# Directory scans take .txt only: a folder of saved postings usually also holds a
# README.md, notes.md or scratch markdown, and guessing wrong here does not cost a
# skipped file -- it costs a tailored resume compiled against a non-posting and
# filed into the user's application records. An explicit glob (/batch dir/*.md) is
# the way to pull in other suffixes; that branch does not filter.
_JD_SUFFIXES = (".txt",)


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


def _key(r: dict):
    """A stable, unique handle for a row.

    `label` is a bare basename (or a truncated URL), so two sources named `role.txt`
    in different folders collapse onto one another. `collect_answers` and `generate`
    key off this instead, so one posting's gap answers can never be injected as
    `-x` context into another posting's resume. `score_all` stamps every row with an
    integer `id`; the `id(r)` fallback keeps directly-built rows distinct too.
    """
    return r.get("id", id(r))


def _score_one(source: str, session) -> dict:
    row = {"id": None, "source": source, "label": source, "path": None,
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

    No `rich` live display is held across the pool: the workers each open their own
    spinners and `print()` from inside their threads, so an outer status render just
    interleaves into noise. Progress is one plain line before the work; every
    per-source line is buffered until the whole pool has finished.
    """
    print(f"Scoring {len(sources)} job descriptions...", flush=True)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        rows = list(pool.map(lambda s: _score_one(s, session), sources))
    for i, r in enumerate(rows):
        r["id"] = i

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
    # Number every option: two rows can share a basename and a score, and an
    # un-numbered checkbox would collapse them to one entry that selects both.
    labels = [f"{i + 1}. {_row_label(r)}" for i, r in enumerate(rows)]
    picked = set(ui.checkbox("Pick the ones to generate:", labels))
    return [r for r, lbl in zip(rows, labels) if lbl in picked]


def collect_answers(rows: list) -> dict:
    """{row key: extra_context} -- asked up front so generation can run unattended."""
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
            answers[_key(r)] = "\n\n".join(parts)
    return answers


def generate(rows: list, *, session, answers: dict) -> None:
    """Sequential on purpose: LaTeX is disk-bound and interleaved output is unreadable."""
    answers = answers or {}
    made, failed = [], []
    for i, r in enumerate(rows, 1):
        print(f"\n[{i}/{len(rows)}] {r['label']}")
        argv = ["-y"]
        if session.output_dir:
            argv += ["-o", str(session.output_dir)]
        if answers.get(_key(r)):
            argv += ["-x", answers[_key(r)]]
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
    if session.profile is None:
        print("  No corpus loaded — run /setup first.")
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
