#!/usr/bin/env python3
"""
bench.py — Run one JD across preset x thinking combinations and compare the results.

Picking a default for tailor.py is a judgement call about writing quality, which no
script settles. What this does is put the candidates side by side and measure the parts
that ARE mechanical — rule violations, trim behaviour, wall time — so the reading you do
afterwards is over a short list rather than seven full resumes.

    python3 bench.py jd.txt                              # every preset, thinking=medium
    python3 bench.py jd.txt -p opus,sol,luna -k medium,high
    python3 bench.py jd.txt --repeat 3                   # variance across identical runs
    python3 bench.py jd.txt --dry-run                    # print the matrix and stop

Output lands in ./bench-runs/<timestamp>/ — one directory per run holding the tailored
JSON and the PDF, plus runs.json and summary.md across the whole matrix. It refuses to
write into your real output directory (constant.json's output_dir, or the one you pass
tailor.py): that holds application records, and a sweep would overwrite them.

WHAT THE COLUMNS MEAN
    s        wall seconds for the model call plus compile
    retry    the first response failed schema validation and a correction round ran
    warn     check_integrity findings: invented figures, claimed collaboration, banned
             summary anchor. These are the ones that would embarrass you in front of an
             employer, so a preset with any is disqualified regardless of how it reads.
    rule     mechanical prompt-rule violations (banned phrases, em/en dashes, summary
             sentence count, skill-group cap). Cheap to count, and a proxy for how
             closely a model follows a long instruction list.
    trim     bullets the one-page trimmer had to drop. High means the model over-
             generated, which is intended — near zero means it under-generated and the
             trimmer had no room to choose.
    fill     how full page 1 ended up. Under ~90% reads as a thin resume.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

import llm
import tailor
from profile import Profile

ROOT = Path(__file__).resolve().parent

# Phrases SYSTEM_PROMPT_BASE bans outright. Duplicated here on purpose: the prompt states
# them as prose for the model, this states them as a regex for the scorer, and the two
# serve different readers. Keep them in sync by hand when the prompt's list changes.
BANNED = [
    r"proven track record", r"passionate about", r"dramatically", r"seamless",
    r"cohesive", r"robust", r"leverag(?:e|ing)", r"faster than ever before",
    r"successfully", r"responsible for", r"helped to", r"strong narrative",
    r"\bmere\b", r"\bdirectly\b", r"operating at the intersection of",
    r"best-in-class", r"world-class", r"cutting-edge",
]
BANNED_RE = [(p, re.compile(p, re.I)) for p in BANNED]

# The prompt forbids em/en dashes in prose, but "--" inside dates fields is required.
DASH_RE = re.compile(r"[—–]")


def score_rules(data: dict) -> list[str]:
    """Mechanical violations of the prompt's own rules. Not a quality judgement."""
    problems = []
    summary = data.get("summary", "") or ""

    sentences = [s for s in re.split(r"(?<=[.!?])\s+", summary.strip()) if s]
    if len(sentences) != 2:
        problems.append(f"summary is {len(sentences)} sentences, not 2")

    prose = [("summary", summary)]
    for e in data.get("experience", []):
        for b in e.get("bullets", []):
            prose.append((f"exp/{e.get('company', '?')[:20]}", b))
    for p in data.get("projects", []):
        for b in p.get("bullets", []):
            prose.append((f"proj/{p.get('title', '?')[:20]}", b))
    for g in data.get("skills", []):
        for entry in g.get("entries", []):
            prose.append((f"skill/{g.get('label', '?')}", entry))

    for where, text in prose:
        if DASH_RE.search(text):
            problems.append(f"{where}: em/en dash")
        for pat, rx in BANNED_RE:
            if rx.search(text):
                problems.append(f"{where}: banned phrase /{pat}/")

    for g in data.get("skills", []):
        n = len(g.get("entries", []))
        if n > 5:
            problems.append(f"skills/{g.get('label', '?')}: {n} entries, cap is 5")

    return problems


def count_bullets(data: dict) -> int:
    return (sum(len(e.get("bullets", [])) for e in data.get("experience", []))
            + sum(len(p.get("bullets", [])) for p in data.get("projects", [])))


def shape(data: dict) -> dict:
    return {
        "experience": len(data.get("experience", [])),
        "projects": len(data.get("projects", [])),
        "bullets": count_bullets(data),
        "skill_groups": len(data.get("skills", [])),
        "skill_entries": sum(len(g.get("entries", [])) for g in data.get("skills", [])),
        "summary_chars": len(data.get("summary", "") or ""),
    }


def run_one(*, preset: str, model: str, thinking: str, jd: str, profile: Profile, mode: str,
            dest: Path, timeout: float, backend: str, binary: str = None) -> dict:
    """One generation, compiled to a PDF. Never raises: a failure is a row like any other."""
    rec: dict = {"preset": preset, "model": model, "thinking": thinking, "ok": False}
    retries: list[str] = []
    t0 = time.time()

    try:
        tailored = tailor.tailor_content(
            profile, jd, mode=mode, backend=backend, model=model, thinking=thinking,
            binary=binary, timeout=timeout, on_retry=retries.append,
        )
    except SystemExit as e:
        rec["retried"] = bool(retries)
        # tailor_content exits the process on an unrecoverable model failure. In a sweep
        # that would kill every remaining combination, so it becomes a failed row here.
        rec.update(seconds=round(time.time() - t0, 1),
                   error=f"tailor_content exited ({e.code})")
        return rec
    except Exception as e:
        rec.update(seconds=round(time.time() - t0, 1),
                   error=f"{type(e).__name__}: {e}",
                   traceback=traceback.format_exc()[-1500:])
        return rec

    tailored = tailor.enforce_portfolio(tailored, jd, profile)
    tailored = tailor.enforce_caps(tailored)

    rec["match_analysis"] = tailor.normalize_match_analysis(
        tailored.pop("match_analysis", {}))

    rec["warnings"] = tailor.check_integrity(
        tailored, profile.content, rec["match_analysis"], profile=profile)
    rec["rule_problems"] = score_rules(tailored)
    rec["pre_trim"] = shape(tailored)
    rec["portfolio_link"] = tailored.get("portfolio_link")
    rec["company_name"] = tailored.get("company_name")
    rec["job_title"] = tailored.get("job_title")
    rec["summary"] = tailored.get("summary")

    company = (tailored.pop("company_name", None) or "").strip() or "Unknown Company"
    job = (tailored.pop("job_title", None) or "").strip() or "Unknown Role"
    escaped = tailor.escape_latex_strings(tailored)

    render = dict(profile.identity)
    render.update(escaped)
    render["location"] = profile.location_for_jd(jd)
    render["projects_title"] = profile.projects_title
    render["skills_title"] = profile.skills_title
    if mode == "cv":
        render["education"] = profile.content.get("education", [])

    dest.mkdir(parents=True, exist_ok=True)
    (dest / "tailored.json").write_text(json.dumps(rec["pre_trim"] | {
        "summary": rec["summary"], "content": tailored}, indent=2))

    try:
        pdf, final = tailor.compile_pdf(render, dest, company, job, mode=mode,
                                        basename=profile.doc_basename)
    except Exception as e:
        rec.update(seconds=round(time.time() - t0, 1),
                   error=f"compile failed: {type(e).__name__}: {e}")
        return rec

    rec["post_trim"] = shape(final)
    rec["trimmed_bullets"] = rec["pre_trim"]["bullets"] - rec["post_trim"]["bullets"]
    rec["pdf"] = str(pdf)
    rec["pdf_bytes"] = pdf.stat().st_size if pdf.exists() else 0

    slack = tailor._page_fill(pdf)
    if slack is not None:
        span = tailor._TEXT_TOP - tailor._TEXT_BOTTOM
        rec["fill"] = round((span - slack) / span, 3)

    rec["seconds"] = round(time.time() - t0, 1)
    rec["retried"] = bool(retries)
    rec["retry_errors"] = retries
    rec["ok"] = True
    return rec


def table(records: list[dict]) -> str:
    head = f"{'preset':<11} {'think':<7} {'s':>6} {'match':>6} {'warn':>5} {'rule':>5} {'trim':>5} {'fill':>6}  result"
    lines = [head, "-" * len(head)]
    for r in records:
        if not r.get("ok"):
            lines.append(f"{r['preset']:<11} {r['thinking']:<7} {r.get('seconds', 0):>6} "
                         f"{'-':>6} {'-':>5} {'-':>5} {'-':>5} {'-':>6}  FAILED: {r.get('error', '?')[:60]}")
            continue
        fill = f"{r['fill']:.0%}" if r.get("fill") is not None else "?"
        lines.append(
            f"{r['preset']:<11} {r['thinking']:<7} {r['seconds']:>6} "
            f"{r.get('match_analysis', {}).get('overall_score', 0):>6} "
            f"{len(r['warnings']):>5} {len(r['rule_problems']):>5} "
            f"{r['trimmed_bullets']:>5} {fill:>6}  "
            f"{r['post_trim']['experience']}exp/{r['post_trim']['projects']}proj/"
            f"{r['post_trim']['bullets']}bul")
    return "\n".join(lines)


def detail(records: list[dict]) -> str:
    """The findings themselves. The table says how many; this says which."""
    out = []
    for r in records:
        if not r.get("ok"):
            continue
        if not (r["warnings"] or r["rule_problems"]):
            continue
        out.append(f"\n{r['preset']} / {r['thinking']}")
        for w in r["warnings"]:
            out.append(f"  [integrity] {w}")
        for p in r["rule_problems"]:
            out.append(f"  [rule]      {p}")
    return "\n".join(out) if out else "\nNo integrity or rule findings on any run."


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="Benchmark tailor.py presets against one JD.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("jd_file", help="Path to the job description to sweep.")
    ap.add_argument("--provider", choices=list(llm.BACKENDS),
                    help="Which CLI to sweep through. Default: auto (see llm.detect_backend).")
    ap.add_argument("--presets", "-p",
                    help="Comma-separated. Default: every preset of the chosen provider.")
    ap.add_argument("--thinking", "-k", default=llm.DEFAULT_THINKING,
                    help=f"Comma-separated from {', '.join(llm.THINKING_LEVELS)}. "
                         f"Default: {llm.DEFAULT_THINKING}.")
    ap.add_argument("--repeat", "-n", type=int, default=1,
                    help="Runs per combination. >1 shows run-to-run variance. Default: 1.")
    ap.add_argument("--mode", choices=("resume", "cv"), default="resume")
    ap.add_argument("--out", default=str(ROOT / "bench-runs"),
                    help="Parent output directory. Default: ./bench-runs")
    ap.add_argument("--timeout", type=float, default=llm.DEFAULT_TIMEOUT)
    ap.add_argument("--binary", "--openclaw-binary", dest="binary",
                    help="Path to the provider's executable if it is not on PATH.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print the matrix and the time estimate, then stop.")
    return ap.parse_args()


def main() -> None:
    args = parse_args()

    try:
        backend = args.provider or llm.detect_backend()
    except llm.LLMError as e:
        sys.exit(f"Error: {e}")
    catalog = llm.PRESETS_BY_BACKEND[backend]

    presets = ([p.strip() for p in args.presets.split(",") if p.strip()]
               if args.presets else list(catalog))
    levels = [t.strip() for t in args.thinking.split(",") if t.strip()]

    unknown = [p for p in presets if p not in catalog]
    if unknown:
        sys.exit(f"Error: unknown preset(s) {', '.join(unknown)} for provider {backend}. "
                 f"Choose from: {', '.join(catalog)}")
    bad = [t for t in levels if t not in llm.THINKING_LEVELS]
    if bad:
        sys.exit(f"Error: unknown thinking level(s) {', '.join(bad)}. "
                 f"Choose from: {', '.join(llm.THINKING_LEVELS)}")

    jd_path = Path(args.jd_file)
    if not jd_path.exists():
        sys.exit(f"Error: no such JD file: {jd_path}")
    jd = jd_path.read_text().strip()
    if not jd:
        sys.exit("Error: job description is empty.")

    profile = Profile.load(ROOT, mode=args.mode)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_root = Path(args.out).expanduser().resolve() / stamp

    # Your real output directory holds application records, one directory per company
    # and role. compile_pdf writes into <out>/<company>/<role>/, so pointing a sweep
    # there would overwrite a real record with benchmark output.
    configured = profile.output_dir
    guarded = [Path(p).expanduser().resolve()
               for p in (configured, os.environ.get("RESUME_TEX_OUTPUT_DIR")) if p]
    for guard in guarded:
        if guard == out_root or guard in out_root.parents:
            sys.exit(f"Error: refusing to write benchmark output under {guard} — that "
                     "directory holds real application records. Pick another --out.")

    combos = [(p, t, i) for p in presets for t in levels for i in range(1, args.repeat + 1)]
    print(f"JD        : {jd_path} ({len(jd)} chars)")
    print(f"Provider  : {backend}")
    print(f"Mode      : {args.mode.upper()}")
    print(f"Matrix    : {len(presets)} preset(s) x {len(levels)} level(s) "
          f"x {args.repeat} = {len(combos)} run(s)")
    print(f"Output    : {out_root}")
    # Observed range on this machine is roughly 60-290s per run, compile included.
    print(f"Estimate  : {len(combos) * 1:.0f}-{len(combos) * 5:.0f} minutes")
    print()

    if args.dry_run:
        for p, t, i in combos:
            print(f"  {p:<11} {t:<7} run {i}  ->  {catalog[p]}")
        return

    out_root.mkdir(parents=True, exist_ok=True)
    (out_root / "jd.txt").write_text(jd)

    records = []
    for n, (preset, level, i) in enumerate(combos, 1):
        model = catalog[preset]
        label = f"{preset}-{level}" + (f"-{i}" if args.repeat > 1 else "")
        print(f"[{n}/{len(combos)}] {label}  ({model})")
        rec = run_one(preset=preset, model=model, thinking=level, jd=jd, profile=profile,
                      mode=args.mode, dest=out_root / label, backend=backend,
                      timeout=args.timeout, binary=args.binary)
        rec["run"] = i
        rec["label"] = label
        records.append(rec)
        # Written after every run, so an interrupted sweep still leaves usable data.
        (out_root / "runs.json").write_text(json.dumps(records, indent=2))
        print()

    report = table(records) + "\n" + detail(records)
    print(report)

    (out_root / "summary.md").write_text(
        f"# Preset benchmark — {stamp}\n\n"
        f"JD: `{jd_path}` ({len(jd)} chars), mode: {args.mode}\n\n"
        f"```\n{table(records)}\n```\n\n## Findings\n```{detail(records)}\n```\n")
    print(f"\nSaved      : {out_root}/summary.md")
    print(f"             {out_root}/runs.json")

    ok = [r for r in records if r.get("ok")]
    clean = [r for r in ok if not r["warnings"] and not r["rule_problems"]]
    print(f"\n{len(ok)}/{len(records)} runs succeeded; {len(clean)} had no findings.")
    print("Read the PDFs for the ones that scored clean — the writing is the part "
          "this script cannot judge.")


if __name__ == "__main__":
    main()
