#!/usr/bin/env python3
"""
tailor.py — Generate a JD-tailored document (Resume or CV) as a PDF.

Two modes, one JD-driven engine. A model reprioritizes and reframes YOUR source
content against the job WITHOUT inventing anything, sharpens wording to a specialist
register, and adds PROJECTED impact metrics (grounded in impact-metrics.md, if you
keep one) where a real bullet has no number.

    Resume (default): 1-page. The model over-generates a JD-ranked superset and this
                      script trims from the end of each list until the PDF is exactly
                      one page, with no orphan lines.
    CV (--cv):        multi-page, everything, "Personal Projects", education included.

Your data lives in JSON beside this script — see README.md:
    constant.json           name, contact, portfolio(s), tailoring preferences  (required)
    content.json            experience, projects, skills, education             (required)
    impact-metrics.md       the only projected figures allowed on the resume     (optional)
    recommendations.json    testimonials for -r                                 (optional)
    coursework.json         per-institution coursework for -ee                  (optional)

Usage:
    python3 tailor.py jd.txt                 # 1-page resume tailored to jd.txt
    python3 tailor.py jd.txt --cv            # multi-page CV tailored to jd.txt
    python3 tailor.py jd.txt -ee             # + relevant coursework under each degree
    python3 tailor.py jd.txt -r              # + recommendations section
    python3 tailor.py jd.txt -x "referral from Jane"   # inline extra context
    python3 tailor.py jd.txt -l "New York, NY"         # override location
    python3 tailor.py jd.txt -o ~/Applications          # where the output lands
    cat jd.txt | python3 tailor.py           # pipe the JD in

Model selection — runs go through a CLI you already pay for, never a metered API key:
    python3 tailor.py jd.txt --provider claude       # force the Claude Code CLI
    python3 tailor.py jd.txt --preset sonnet         # a preset of the chosen backend
    python3 tailor.py jd.txt --thinking high         # low | medium | high (OpenClaw only)
    python3 tailor.py jd.txt --model openai/gpt-5.5  # pin a model outside the presets
With both CLIs installed OpenClaw wins and the default preset is terra; with only the
Claude CLI the default is sonnet (claude-sonnet-5). See llm.py.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path
from typing import List, Literal, Optional

try:
    from jinja2 import Environment, FileSystemLoader
except ImportError:
    print("Error: jinja2 not installed. Run: pip3 install jinja2")
    sys.exit(1)

from pydantic import BaseModel

import llm
import notes
import ui
from profile import (Profile, portfolio_links, location_for_jd,
                     SYSTEM_PROMPT_BASE, RESUME_CAPS, CV_GUIDANCE,
                     NOTE_TARGET_SEP, note_targets)

ROOT = Path(__file__).resolve().parent

# TeX Live and MiKTeX put pdflatex on PATH; MacTeX installs it here but does not always
# export it to a GUI-launched shell, so fall back to the known location.
PDFLATEX = shutil.which("pdflatex") or "/Library/TeX/texbin/pdflatex"

# ── Structured-output schema (field names must match template.tex keys) ────────

class EducationEntry(BaseModel):
    institution: str
    degree: str
    gpa: str
    start: str
    end: str
    location: str

class ExperienceEntry(BaseModel):
    company: str
    title: str
    dates: str
    location: str
    bullets: List[str]
    link: Optional[str] = None
    short_link: Optional[str] = None

class ProjectEntry(BaseModel):
    title: str
    bullets: List[str]
    link: Optional[str] = None
    short_link: Optional[str] = None

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

class SkillGroup(BaseModel):
    label: str
    entries: List[str]

class RequirementAssessment(BaseModel):
    requirement: str
    weight: int
    status: Literal["direct", "adjacent", "missing"]
    evidence: List[str]
    gap_or_talking_point: str

class InferredSkillEvidence(BaseModel):
    # `group` is a plain string because the group labels come from YOUR content.json,
    # not from this file. check_integrity rejects any label that is not in the source.
    skill: str
    group: str
    kind: Literal["capability", "method", "domain", "tool_or_technology"]
    basis: Literal["direct", "adjacent"]
    jd_term: str
    source_item: str
    source_quote: str

class MatchAnalysis(BaseModel):
    overall_score: int
    requirements: List[RequirementAssessment]
    strongest_matches: List[str]
    improvements: List[str]
    interview_talking_points: List[str]
    inferred_skills: List[InferredSkillEvidence]
    clarifying_questions: List[str]
    # Per-entry "what I emphasized / condensed" lines -- the applicant's change
    # log for review, never resume copy (see MATCH ANALYSIS in the prompt).
    tailoring_notes: List[str] = []

class GapQuestion(BaseModel):
    question: str
    potential_points: int
    target: str
    target_requirement: str

class PreScoreAnalysis(BaseModel):
    overall_score: int
    requirements: List[RequirementAssessment]
    gap_questions: List[GapQuestion]
    # The JD's keyword-screen vocabulary, extracted once here and handed to the
    # generation call as a checklist; term_coverage() audits the result.
    key_terms: List[str] = []

class RescoreAnalysis(BaseModel):
    """The after-score: the SAME requirements and weights as the baseline, only
    the statuses re-judged -- so before/after moves on content, not on which
    requirements the scorer happened to extract this time."""
    requirements: List[RequirementAssessment]

class NoteRouting(BaseModel):
    targets: List[str] = []

class TailoredResume(BaseModel):
    company_name: Optional[str] = None  # anonymous postings name no employer — null, not a guess
    job_title: str
    portfolio_link: str
    summary: str
    experience: List[ExperienceEntry]
    projects: List[ProjectEntry]
    skills: List[SkillGroup]
    match_analysis: MatchAnalysis
    education: Optional[List[EducationEntry]] = None
    certifications: Optional[List[CertEntry]] = None
    awards: Optional[List[AwardEntry]] = None
    publications: Optional[List[PubEntry]] = None
    languages: Optional[List[LangEntry]] = None

# ── Optional data files ───────────────────────────────────────────────────────
# Both are optional: an absent file simply means the flag has nothing to render. Copy
# the examples/ version next to this script and edit it to switch either one on.

def load_json_file(name: str, default):
    """Parse ROOT/<name>, or return `default` when the file is not there."""
    path = ROOT / name
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as e:
        print(f"Error: {path} is not valid JSON: {e}")
        sys.exit(1)

# Verbatim testimonials, rendered as-is when --recommendations/-r is passed. Quote
# strings carry no outer quotation marks — the template wraps each in ``...''. Inner
# quotes must use ``...'' and em dashes ---, because these strings go straight into the
# LaTeX template rather than through the model. May push the resume to a second page.
RECOMMENDATIONS = load_json_file("recommendations.json", [])

# Relevant coursework per institution, listed under each Education entry when
# --extended-education/-ee is passed. Each key must match an "institution" value in
# content.json EXACTLY. Escape ampersands as \\& for the same reason as above.
COURSEWORK_BY_INSTITUTION = load_json_file("coursework.json", {})

# ── Load source CV data ────────────────────────────────────────────────────────

# Both modes read the FULL corpus. The model ranks it against the JD and over-generates a
# superset; for the resume, compile_single_page() trims that down to one page. Keeping a
# separate hand-curated 1-page source starves the superset and hides whole roles from every
# resume — the trimmer does that curation per-JD now, which is strictly better. So put
# everything in content.json; the pipeline decides what fits.
CONTENT_FILE = {"resume": "content.json", "cv": "content.json"}

def load_json_required(name: str) -> dict:
    """A data file the run cannot proceed without, with a pointer to its example."""
    path = ROOT / name
    if not path.exists():
        print(f"Error: {name} not found in {ROOT}.")
        print(f"  Copy examples/{name} to {ROOT / name} and fill in your own details.")
        print("  See README.md → 'Your data files'.")
        sys.exit(1)
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as e:
        print(f"Error: {path} is not valid JSON: {e}")
        sys.exit(1)

def load_cv(mode: str = "resume") -> dict:
    """constant.json (identity + preferences) merged with the content source for the mode.

    Preference keys ride along in the same dict; render_data pulls only the identity
    whitelist out of it, so anything extra here never reaches the template."""
    data = load_json_required("constant.json")
    data.update(load_json_required(CONTENT_FILE[mode]))
    return data

def load_impact_reference() -> str:
    """The full methodology file — the human's interview-defence doc, and the lint's
    source of truth for which projects may carry a figure."""
    p = ROOT / "impact-metrics.md"
    return p.read_text() if p.exists() else ""

def impact_reference_for_prompt() -> str:
    """The same file distilled to what the model actually needs: the approved claims and the
    ban list. The source tables and model arithmetic exist to defend the number in an
    interview — sending them costs ~2K tokens per request and adds nothing but noise."""
    text = load_impact_reference()

    def section(name):
        # [^\n]* — never let the heading match run past its own line (re.S makes . greedy)
        m = re.search(r"^##\s+" + name + r"[^\n]*\n(.*?)(?=^##\s|\Z)", text, re.M | re.S)
        return m.group(1) if m else ""

    lines = []
    for block in re.split(r"^###\s+", section("ELIGIBLE"), flags=re.M)[1:]:
        title = block.splitlines()[0].strip()
        claim = re.search(r"^\*\*Approved claim:\*\*\s*(.+)$", block, re.M)
        if claim:
            lines.append(f"- {title}: {claim.group(1).strip()}")

    banned = [b.splitlines()[0].split("—")[0].strip()
              for b in re.split(r"^###\s+", section("NOT ELIGIBLE"), flags=re.M)[1:]]

    out = ""
    if lines:
        out += "APPROVED PROJECTED CLAIMS (the ONLY figures you may add to a bullet):\n" + "\n".join(lines)
    if banned:
        out += ("\n\nNEVER INVENT A FIGURE FOR THESE — any dollar amount you introduce for them is fabrication: "
                + "; ".join(banned)
                + ".\nThey have no market and no measurable impact. Numbers ALREADY present in their source bullets"
                " are real and may be kept verbatim; you simply may not add new ones. Describe the work instead.")
    return out

# ── Education decision (spec §4 D0) ───────────────────────────────────────────

_DEGREE_RE = re.compile(
    r"\b(bachelor'?s?|master'?s?|B\.?S\.?|B\.?A\.?|BSc|M\.?S\.?|MSc|"
    r"undergraduate degree|degree required|degree in |degree from|"
    r"educational qualification|accredited institution)\b", re.I)

def jd_requires_degree(jd: str) -> bool:
    return bool(_DEGREE_RE.search(jd or ""))

# Firm replacement for the old conditional EDUCATION_RULE. The Python gate in
# decide_education now owns the include/exclude decision; when we say include,
# the model includes — it does not get to second-guess against the JD.
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

# ── CLI args ───────────────────────────────────────────────────────────────────

def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a JD-tailored Resume (1-page) or CV (multi-page).")
    parser.add_argument(
        "--cv",
        action="store_true",
        help="CV mode: multi-page, full content (content.json), 'Personal Projects', education included. Default is 1-page resume.",
    )
    parser.add_argument(
        "--education", "-e",
        action="store_true",
        help="Force the education section in. Without this flag the JD-based decision is automatic.",
    )
    parser.add_argument(
        "--extended-education", "-ee",
        action="store_true",
        help="Extended education: always show the degree(s), each with its relevant coursework listed under its own entry (from transcripts). Superset of -e.",
    )
    parser.add_argument(
        "--cover-letter", "-c",
        action="store_true",
        help="Generate a cover-letter-prompt.md in the output directory.",
    )
    parser.add_argument(
        "--context", "-x",
        metavar="TEXT_OR_FILE",
        help="Extra context to pass alongside the JD (inline text or path to a file).",
    )
    parser.add_argument(
        "--location", "-l",
        metavar="LOCATION",
        help=("Override the location shown on the resume (e.g. \"New York, NY\"). "
              "Without an override: the first matching location_rules entry in "
              "constant.json, else its \"location\"."),
    )
    parser.add_argument(
        "--recommendations", "-r",
        action="store_true",
        help="Append a Recommendations section with verbatim testimonials (may push the resume to a second page).",
    )
    parser.add_argument(
        "--no-interactive", "-y",
        action="store_true",
        help="Straight through: compute the baseline score but skip the gap-question and cover-letter prompts.",
    )
    parser.add_argument(
        "--score-preset",
        metavar="NAME",
        help="Model preset for the baseline score step. Default: one tier below the run model.",
    )
    parser.add_argument(
        "--no-score",
        action="store_true",
        help="Skip both the baseline and the after score (two fewer model calls).",
    )
    parser.add_argument(
        "--provider",
        choices=list(llm.BACKENDS),
        help=("Which CLI to run the model through. Default: auto — OpenClaw if it is "
              "installed, otherwise the Claude Code CLI."),
    )
    parser.add_argument(
        "--preset", "-p",
        metavar="NAME",
        help=("Model preset for the chosen provider. "
              + " | ".join(f"{b}: {', '.join(p)}" for b, p in llm.PRESETS_BY_BACKEND.items())
              + ". Defaults: "
              + ", ".join(f"{b}={m}" for b, m in llm.DEFAULT_PRESET.items()) + "."),
    )
    parser.add_argument(
        "--thinking",
        choices=list(llm.THINKING_LEVELS),
        default=llm.DEFAULT_THINKING,
        help=(f"Reasoning depth. Default: {llm.DEFAULT_THINKING}. OpenClaw only — the "
              "Claude CLI has no equivalent flag, so the level is ignored there."),
    )
    parser.add_argument(
        "--model",
        metavar="ID",
        help=("Pin a model outside the presets. Overrides --preset. Through OpenClaw it "
              "needs a provider prefix (anthropic/claude-opus-4-8) and must be bound in "
              "agents.defaults.models; through the Claude CLI use a plain id "
              "(claude-opus-5)."),
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=llm.DEFAULT_TIMEOUT,
        metavar="SECONDS",
        help=f"Per-call ceiling. Default: {llm.DEFAULT_TIMEOUT:.0f}.",
    )
    parser.add_argument(
        "--binary", "--openclaw-binary",
        dest="binary",
        metavar="PATH",
        help="Path to the provider's executable if it is not on PATH.",
    )
    parser.add_argument(
        "--max-revisions",
        type=int, default=2, metavar="N",
        help=("If the tailored resume re-scores below the untailored baseline, regenerate "
              "with feedback up to N times and ship the best attempt (default 2; 0 disables)."),
    )
    parser.add_argument(
        "--output-dir", "-o",
        metavar="DIR",
        default=os.environ.get("RESUME_TEX_OUTPUT_DIR"),
        help=("Where <Company>/<Role>/ output folders are written. Default: "
              "$RESUME_TEX_OUTPUT_DIR, else the output_dir in constant.json, "
              "else ~/Documents/Applications."),
    )
    parser.add_argument(
        "jd_file",
        nargs="?",
        help="Path to a file containing the job description (optional; defaults to stdin/interactive).",
    )
    parser.add_argument(
        "extra_context_file",
        nargs="?",
        help="Path to a file containing extra context (optional second positional argument).",
    )
    return parser.parse_args(argv)

# ── Read job description ───────────────────────────────────────────────────────

def read_jd(jd_file) -> str:
    if jd_file:
        path = Path(jd_file)
        if not path.exists():
            print(f"Error: file not found: {path}")
            sys.exit(1)
        return path.read_text()

    if not sys.stdin.isatty():
        return sys.stdin.read()

    # A pasted JD is one long unbroken paragraph. Reading it line-by-line with input()
    # runs it through the terminal's canonical line discipline, which caps a single
    # unterminated line at MAX_INPUT (1024 bytes on macOS): the tty rejects the
    # overflow, redraws the wrapped line on every pasted character (the visible
    # "flicker"), and the paste never completes. ui.editor() takes the input in a
    # raw-mode prompt with bracketed paste, so an arbitrarily long paste lands intact.
    return ui.editor("Paste the job description below")


def read_extra_context(args, *, interactive: bool = True) -> str:
    """Resolve extra context from --context flag, second positional arg, or an
    interactive prompt.

    `interactive=False` resolves only the flag/file sources and never blocks --
    run() uses it to pull those in early, then asks for typed context later,
    inside the one grouped question block (via prompt_extra_context)."""
    # --context flag takes a file path or inline text
    if args.context:
        p = Path(args.context)
        return p.read_text() if p.exists() else args.context

    # second positional argument
    if args.extra_context_file:
        p = Path(args.extra_context_file)
        if not p.exists():
            print(f"Error: extra context file not found: {p}")
            sys.exit(1)
        return p.read_text()

    if interactive:
        return prompt_extra_context(args)
    return ""


def prompt_extra_context(args) -> str:
    """The typed-context prompt on its own -- routed through ui.editor so it works
    inside the full-screen shell (a raw input() there has no stdin to read and
    hangs). Returns "" straight through (-y) or when stdin is not a terminal."""
    if not (sys.stdin.isatty() and not args.no_interactive):
        return ""
    return ui.editor("Extra context (optional — Enter to skip)").strip()

# ── Call the model ─────────────────────────────────────────────────────────────

def tailor_content(profile: Profile, jd: str, mode: str = "resume",
                   include_education: bool = False, extra_context: str = "",
                   *, backend: str, model: str, thinking: str,
                   binary: Optional[str] = None, timeout: float = llm.DEFAULT_TIMEOUT,
                   on_retry=None, key_terms: Optional[list] = None,
                   revision_notes: str = "") -> dict:
    """One generation call. `key_terms` is the JD's keyword checklist from the
    score pass (surfaced verbatim where honest); `revision_notes` is the
    feedback from a previous attempt that scored below the untailored baseline."""
    content_keys = {"portfolio_link", "summary", "experience", "projects", "skills"}
    if include_education:
        content_keys.add("education")
    # Optional sections: hand the model each one only when the source actually has it
    # (mirrors how `education` is gated above).
    content_keys |= {k for k in ("certifications", "awards", "publications", "languages")
                     if k in profile.content}

    content_only = {k: v for k, v in profile.content.items() if k in content_keys}

    impact_ref = impact_reference_for_prompt()
    system_prompt = (
        profile.system_prompt()
        + (FIRM_EDUCATION_RULE if include_education else "")
        + (RESUME_CAPS if mode == "resume" else CV_GUIDANCE)
        + (f"\n\nIMPACT REFERENCE:\n{impact_ref}" if impact_ref else "")
        + f"\n\nHere is the candidate's CV (JSON):\n{json.dumps(content_only, separators=(',', ':'))}"
    )

    terms = [t for t in (key_terms or []) if str(t).strip()]
    user = (
        f"Here is the job description:\n---\n{jd}\n---\n"
        + (f"\nAdditional context from the applicant:\n---\n{extra_context}\n---\n" if extra_context else "")
        + (("\nJD KEY TERMS (place each verbatim where the source honestly supports it; "
            "see JD KEY TERMS CHECKLIST):\n" + "\n".join(f"- {t}" for t in terms) + "\n")
           if terms else "")
        + (f"\nREVISION NOTES (a previous attempt scored BELOW the untailored resume; "
           f"fix these without inventing anything):\n{revision_notes}\n" if revision_notes else "")
        + "\nReturn the tailored content."
    )

    try:
        with ui.spinner(f"Asking {model} to tailor your {mode} (thinking={thinking})"):
            tailored, reply = llm.complete_schema(
                backend=backend, model=model, system=system_prompt, user=user,
                schema=TailoredResume, thinking=thinking, binary=binary, timeout=timeout,
                on_retry=lambda err: (
                    print(f"  output failed validation, retrying once: {err[:160]}"),
                    on_retry and on_retry(err)),
            )
    except llm.LLMError as e:
        # Everything the model actually said is in the message; keep it on disk because
        # the terminal truncates and a failed run is exactly when you want the raw text.
        detail_path = Path(tempfile.gettempdir()) / "resume-tex-last-error.txt"
        detail_path.write_text(str(e))
        print(f"Error: {e}")
        print(f"Full detail saved to {detail_path} for inspection.")
        sys.exit(1)

    # The runtime reports this; a truncated response that slips past it surfaces as a
    # JSON parse failure in llm.complete_schema instead.
    if reply.finish and reply.finish not in ("stop", "end_turn", "completed"):
        print(f"Warning   : finish reason {reply.finish!r} — output may be truncated.")
    # No token line: the runtime reports its wrapper's counters (observed: 2 input /
    # 1 output for a full resume), not the generation's. A number that wrong is worse
    # than no number.

    return tailored

# ── Python-side guards (belt-and-braces over the schema) ───────────────────────

# The two portfolios split on EMPHASIS (visual vs research), not on whether the role
# involves the web — both sites carry web work. An earlier version keyed off web
# signals in the JD and forced the research portfolio onto every role mentioning
# HTML or WordPress, which sent the wrong link on exactly the visual roles that most
# needed the visual one.
#
# The model decides; this only settles the case it flip-flops on, a role that names
# both crafts. The TITLE is the tiebreak, because a JD lists many duties but states
# one role.
_VISUAL_TITLE = re.compile(
    r"\b(graphic|visual|brand(?:ing)?|motion|illustrat\w*|print|art director|"
    r"creative director|designer[/ ]marketing)\b", re.I)
_RESEARCH_TITLE = re.compile(
    r"\b(research\w*|user research|ux strateg\w*|product manager|product owner|"
    r"strategist|information architect)\b", re.I)

def enforce_portfolio(tailored: dict, jd: str, profile: Profile) -> dict:
    """Settle a straddling role by its title; otherwise leave the model's choice alone."""
    links = profile.portfolio_links()
    if not links:
        return tailored
    if "visual" not in links:
        # One portfolio: there is nothing to choose, so never let a hallucinated URL ship.
        tailored["portfolio_link"] = links["default"]
        return tailored

    title = tailored.get("job_title") or ""
    visual, research = _VISUAL_TITLE.search(title), _RESEARCH_TITLE.search(title)
    # Only decisive when the title points one way. A title naming both keeps the
    # model's judgement, which read the whole JD rather than nine words of it.
    if visual and not research:
        tailored["portfolio_link"] = links["visual"]
    elif research and not visual:
        tailored["portfolio_link"] = links["research"]
    return tailored

def enforce_caps(tailored: dict) -> dict:
    """Cap each skill group at 5 entries; drop experience/project entries whose
    bullets list is empty (an empty LaTeX itemize crashes pdflatex)."""
    for group in tailored.get("skills", []):
        entries = group.get("entries")
        if isinstance(entries, list):
            group["entries"] = entries[:5]
    for key in ("experience", "projects"):
        items = tailored.get(key)
        if isinstance(items, list):
            tailored[key] = [e for e in items if e.get("bullets")]
    return tailored

_MATCH_CREDIT = {"direct": 1.0, "adjacent": 0.5, "missing": 0.0}

SCORE_SYSTEM_PROMPT = """Score this candidate's existing CV JSON against the job description. Do not write a resume.

Extract 6-10 material requirements from the JD; assign each a weight so weights total 100. Weight hiring gates as gates (a required credential or minimum-years gate 15-25; the defining domain 10-20; a single tool 5-8). Mark each requirement:
- direct   — the CV JSON explicitly proves it
- adjacent — transferable evidence exists but the exact requirement is not proved
- missing  — no meaningful evidence
Do not award direct for keyword similarity.

Then produce at most 3 gap_questions targeting the highest-weight missing or adjacent requirements — short questions whose answer, if positive, would move that requirement up. Ask fewer: only one or two when only one or two requirements are genuinely worth asking about, and none when the CV already covers the material requirements. For each: potential_points = the requirement's weight if it is missing, or half (rounded) if adjacent; target = the exact "company" of an experience entry as "experience:<company>", the exact "title" of a project as "project:<title>", or "general"; target_requirement = the requirement text.

NEVER ask for information the CV JSON already contains. If the education array is populated, do not ask about degree, institution, graduation date, or GPA. If a role or project already states a fact, do not ask for it. Never ask whether the candidate has a portfolio, personal website, case-study pages, or links — assume they do. Never ask a yes/no "have you ever..." question whose only useful answer is a concrete example; ask directly for the example instead, scoped to a named requirement.

Also return key_terms: 15-25 phrases a keyword screen for THIS JD would look for, copied in the JD's exact wording — skill nouns and methods ("end-to-end design", "user research", "usability testing", "design systems"), named tools and platforms, domain terms ("B2B marketplace", "supplier experience"), and the JD's lead verbs ("own", "partner", "ship"). Short noun/verb phrases only (1-4 words); no sentences, no duplicates, no generic words ("experience", "team", "role").

Return only the schema."""

RESCORE_SYSTEM_PROMPT = """Re-score this candidate's resume JSON against the job description using the FIXED RUBRIC below. Do not write a resume.

The rubric lists the requirements and weights already chosen for this JD. Return the SAME requirements in the SAME order with the SAME weights; change only status and evidence. Mark each requirement:
- direct   — the resume JSON explicitly proves it
- adjacent — transferable evidence exists but the exact requirement is not proved
- missing  — no meaningful evidence
Do not award direct for keyword similarity. Judge the resume text in front of you, nothing else.

Return only the schema."""

# The keyword screen a JD term has to pass in the shipped text. A term counts as
# carried when it appears verbatim (case-insensitive), or when every word of it
# appears as a word stem -- "design systems" is carried by "design system",
# "usability testing" by "usability tests". Deterministic on purpose: the point
# is a number the applicant can check by reading the page.
_WORD_RE = re.compile(r"[a-z0-9][a-z0-9+#./-]*")

def _stem(word: str) -> str:
    w = word.lower()
    for suf in ("ing", "ies", "es", "ed", "s"):
        if len(w) > len(suf) + 2 and w.endswith(suf):
            return w[:-len(suf)] + ("y" if suf == "ies" else "")
    return w

def _shipped_text(content: dict) -> str:
    parts = [str(content.get("summary") or "")]
    for e in content.get("experience") or []:
        parts += [str(e.get("title") or ""), *(e.get("bullets") or [])]
    for p in content.get("projects") or []:
        parts += [str(p.get("title") or ""), *(p.get("bullets") or [])]
    for g in content.get("skills") or []:
        parts += [str(g.get("label") or ""), *(g.get("entries") or [])]
    return "\n".join(parts)

def term_coverage(content: dict, terms: list) -> dict:
    """Which JD key terms the shipped content carries. Returns
    {"covered": [...], "missing": [...]} preserving the input order."""
    text = _shipped_text(content).lower()
    stems = {_stem(w) for w in _WORD_RE.findall(text)}
    covered, missing = [], []
    for term in terms:
        t = str(term).strip()
        if not t:
            continue
        if t.lower() in text:
            covered.append(t); continue
        words = [_stem(w) for w in _WORD_RE.findall(t.lower())]
        (covered if words and all(w in stems for w in words) else missing).append(t)
    return {"covered": covered, "missing": missing}

# Education facts live in the "education" array; when it is populated, a question
# asking for a degree/school/grad-date is asking for data we already have. Scoped
# to education on purpose — it is the observed failure and a bounded, safe filter.
_EDU_QUESTION_RE = re.compile(
    r"\b(degree|bachelor'?s?|master'?s?|graduat\w*|institution|universit\w*|college|GPA|alma mater)\b", re.I)

# "Do you have a portfolio URL / case-study pages / a website?" -- if constant.json
# lists a portfolio or any project carries a link, that's already answered.
_PORTFOLIO_QUESTION_RE = re.compile(
    r"portfolio\s*(url|link|site|website|page)|case[\s-]?stud|do you have (a )?(portfolio|website|url|link)",
    re.I)


def _portfolio_known(profile: "Profile") -> bool:
    if str(profile._constant.get("portfolios", "")).strip():
        return True
    for kind in ("projects", "experience"):
        if any((e.get("link") or e.get("short_link")) for e in profile.content.get(kind) or []):
            return True
    return False

def _known_note_targets(profile: "Profile") -> set:
    """Every target score_jd's model is allowed to name, plus the always-valid
    plain "kind:name" form even when a collision means it is not the only valid
    target for that name -- the model only ever produces the plain form
    (SCORE_SYSTEM_PROMPT documents nothing else), and it must keep resolving
    (to the first matching entry -- Profile.append_note's documented fallback)
    rather than being dropped as an unknown target.
    """
    t = {"general"}
    for _, _, target in note_targets(profile.content):
        t.add(target)
        t.add(target.split(NOTE_TARGET_SEP, 1)[0])
    return t

GAP_POINT_FLOOR = 8      # a gap question below this many potential_points isn't worth a prompt


def _generic_one_page(profile: "Profile") -> "Profile":
    """The corpus cut to a plausible ONE-PAGE shape in source (recency) order --
    no JD reranking, no reword. This is the honest baseline: a one-page resume
    physically can't carry a six-role corpus, so scoring the full corpus against
    the JD sets a bar the tailored page can never reach. Comparing an untailored
    one-pager to the tailored one-pager is the real before/after."""
    c = dict(profile.content)
    c["experience"] = (c.get("experience") or [])[:4]
    c["projects"] = (c.get("projects") or [])[:3]
    c["skills"] = [{**g, "entries": (g.get("entries") or [])[:5]}
                   for g in (c.get("skills") or [])]
    return Profile.from_dicts(profile._constant, c, mode=profile._mode)


def score_jd(profile: "Profile", jd: str, *, backend: str, model: str,
             binary: Optional[str] = None, timeout: float = llm.DEFAULT_TIMEOUT,
             label: str = "your CV", rubric: Optional[list] = None) -> dict:
    """Step B: baseline-score the candidate's CV against a JD before any generation.

    Never raises: on an LLM failure it returns a null score so the interactive flow
    can continue. `overall_score` is recomputed by Python from the requirement
    statuses; gap questions with a bogus target — or, when education data already
    exists, ones fishing for a degree/school/grad-date — are dropped.

    With `rubric` (the baseline's requirement list) this is the AFTER score: the
    model re-judges status only, against the same requirements and weights, so the
    before/after delta measures the resume and not scorer drift. No gap questions
    or key terms come back in that mode.
    """
    cv_json = json.dumps({k: profile.content.get(k)
                          for k in ('summary', 'experience', 'projects', 'skills', 'education')},
                         separators=(',', ':'))
    empty = {"overall_score": None, "requirements": [], "gap_questions": [], "key_terms": []}
    if rubric:
        fixed = [{"requirement": r.get("requirement", ""), "weight": int(r.get("weight", 0))}
                 for r in rubric]
        system = (RESCORE_SYSTEM_PROMPT + "\n\nFIXED RUBRIC:\n"
                  + json.dumps(fixed, separators=(',', ':'))
                  + "\n\nHere is the candidate's resume (JSON):\n" + cv_json)
        user = f"Here is the job description:\n---\n{jd}\n---\nReturn the re-scored rubric."
        try:
            with ui.spinner(f"Scoring {label} against the JD with {model}"):
                data, _ = llm.complete_schema(backend=backend, model=model, system=system,
                                              user=user, schema=RescoreAnalysis, thinking="low",
                                              binary=binary, timeout=timeout)
        except llm.LLMError as e:
            print(f"Warning   : re-scoring failed ({str(e)[:120]}); continuing without it.")
            return empty
        reqs = data.get("requirements") or []
        # The weights are the rubric's, whatever the model echoed back. Pair by
        # position when the model kept the shape; fall back to its own list.
        if len(reqs) == len(fixed):
            for r, f in zip(reqs, fixed):
                r["requirement"], r["weight"] = f["requirement"], f["weight"]
        normalized = normalize_match_analysis({"requirements": reqs})
        return {"overall_score": normalized["overall_score"], "requirements": reqs,
                "gap_questions": [], "key_terms": []}

    system = SCORE_SYSTEM_PROMPT + "\n\nHere is the candidate's CV (JSON):\n" + cv_json
    user = f"Here is the job description:\n---\n{jd}\n---\nReturn the score and gap questions."
    try:
        with ui.spinner(f"Scoring {label} against the JD with {model}"):
            data, _ = llm.complete_schema(backend=backend, model=model, system=system, user=user,
                                          schema=PreScoreAnalysis, thinking="low",
                                          binary=binary, timeout=timeout)
    except llm.LLMError as e:
        print(f"Warning   : baseline scoring failed ({str(e)[:120]}); continuing without it.")
        return empty

    data["requirements"] = data.get("requirements") or []
    normalized = normalize_match_analysis({"requirements": data["requirements"]})
    known = _known_note_targets(profile)
    has_education = bool(profile.content.get("education"))
    has_portfolio = _portfolio_known(profile)
    gaps = []
    for g in data.get("gap_questions") or []:
        q = g.get("question", "")
        if g.get("target") not in known:
            continue
        if has_education and _EDU_QUESTION_RE.search(q):
            continue  # already in content.json — asking for it is noise
        if has_portfolio and _PORTFOLIO_QUESTION_RE.search(q):
            continue  # a portfolio / links are already on file
        g["potential_points"] = max(0, min(100, int(g.get("potential_points", 0))))
        gaps.append(g)
    # Only ask about gaps that actually move the needle. A model always returns
    # some questions; a +3-point gap on an already-strong match is not worth a
    # prompt. Below the floor -> no gap step at all.
    gaps = [g for g in gaps if g["potential_points"] >= GAP_POINT_FLOOR]
    gaps.sort(key=lambda g: g["potential_points"], reverse=True)
    # De-dupe the key terms case-insensitively, keep the model's order.
    seen, key_terms = set(), []
    for t in data.get("key_terms") or []:
        t = str(t).strip()
        if t and t.lower() not in seen:
            seen.add(t.lower()); key_terms.append(t)
    return {"overall_score": normalized["overall_score"],
            "requirements": data["requirements"], "gap_questions": gaps[:3],
            "key_terms": key_terms}

DISTILL_SYSTEM_PROMPT = (
    "Rewrite the applicant's answer as a resume source note. Tighten the wording: "
    "make it concise, clear and specific, and fix grammar. Constraints: at most 2 "
    "sentences; factual; keep concrete numbers exactly as given; no first person; no "
    "marketing words (passionate, robust, seamless, spearheaded). Never add a fact, "
    "number, scope, outcome or claim the applicant did not state -- rephrasing is "
    "allowed, inventing is not. Output the note text only, nothing else."
)

def distill_note(question, answer, *, backend, model, binary=None, timeout=llm.DEFAULT_TIMEOUT) -> str:
    """Step C: distil one raw gap answer into a factual <=2-sentence source note.

    Returns "" immediately for a blank/whitespace answer (no model call).
    """
    answer = (answer or "").strip()
    if not answer:
        return ""
    reply = llm.complete_text(backend=backend, model=model, system=DISTILL_SYSTEM_PROMPT,
                              user=f"Question asked: {question}\nApplicant's answer: {answer}",
                              thinking="low", binary=binary, timeout=timeout)
    return reply.text.strip()


ROUTE_NOTE_SYSTEM = (
    "A new source note from the applicant is below, and their resume entries "
    "(each: `ref`, its bullets, its existing notes). Return `targets`: the `ref` "
    "of every entry to which this note adds a fact that is NOT already stated in "
    "that entry's bullets or notes. Include an entry only when the note genuinely "
    "adds information there -- if the fact is already covered, leave that entry "
    'out. If the note is general career context tied to no single entry, return '
    '["general"]. Never return a ref that is not in the list. Return the schema only.'
)


def route_note(profile: "Profile", note: str, fallback: str, *, backend: str,
               model: str, binary: Optional[str] = None) -> list:
    """Which entries a distilled note should be appended to -- possibly several,
    only where it adds information the entry doesn't already carry. Falls back to
    `fallback` (the gap question's own target) on any model failure or empty
    result. Returns resolved, de-duplicated target strings."""
    rows = [{"ref": target, "bullets": entry.get("bullets") or [],
             "notes": entry.get("notes") or ""}
            for _kind, entry, target in note_targets(profile.content)]
    known = ({r["ref"] for r in rows}
             | {r["ref"].split(NOTE_TARGET_SEP, 1)[0] for r in rows} | {"general"})
    picks = []
    if rows:
        try:
            with ui.spinner("Working out which entries this belongs to"):
                data, _ = llm.complete_schema(
                    backend=backend, model=model,
                    system=ROUTE_NOTE_SYSTEM + "\n\nEntries:\n"
                    + json.dumps(rows, separators=(",", ":"), ensure_ascii=False)[:18000],
                    user=f"New note:\n{note}\n\nReturn targets.",
                    schema=NoteRouting, thinking="low", binary=binary)
            picks = [t for t in (data.get("targets") or []) if t in known]
        except llm.LLMError:
            picks = []
    if not picks:
        picks = [fallback] if fallback else ["general"]
    out, seen = [], set()
    for t in picks:
        r = notes.resolve_target(profile, t)
        if r and r not in seen:
            seen.add(r)
            out.append(r)
    return out

def collect_gap_answers(profile, gap_questions, *, backend, model, binary=None) -> list:
    """Step C: prompt for each gap answer, distil it, and fold it into content.json.

    Returns the list of (question, potential_points) tuples actually incorporated.
    Skips entirely (returns []) with no questions to ask. The "do you want to answer
    these at all" decision is made by the caller, upfront, alongside every other
    yes/no question this run has -- so once we get here the run just proceeds
    straight through the questions instead of stopping to ask a second time.
    """
    if not gap_questions or not ui.interactive():
        return []
    n = len(gap_questions)
    print(f"\n{n} question{'s' if n != 1 else ''} that could raise your match — "
          "yes/no, then add a detail only if it's something new.")
    folded = []
    for i, g in enumerate(gap_questions, 1):
        print(f"\nQ{i}/{n}  (+{g['potential_points']} pts)\n  {g['question']}")
        if not ui.confirm("Does this apply to you?", default=False):
            continue                                  # "no" -> nothing to add

        # No pre-drafted answer: a "yes" with nothing to add should skip
        # instantly, not after a model call for a draft the applicant won't use.
        raw = ui.editor("A specific detail — a number, a system, an outcome "
                        "(Enter to skip)")
        if not raw.strip():
            continue                                  # "yes" but no new detail -> don't invent one
        with ui.spinner("Distilling your answer into a source note"):
            note = distill_note(g["question"], raw, backend=backend, model=model, binary=binary)
        if not note:
            continue
        targets = route_note(profile, note,
                             notes.resolve_target(profile, g.get("target", "")),
                             backend=backend, model=model, binary=binary)
        if not targets:
            continue
        print(f"  distilled note: {note}")
        print(f"  → {', '.join(targets)}")
        if not ui.confirm("Save it to those entries?", default=True):
            continue
        for t in targets:
            profile.append_note(t, note)
        folded.append((g["question"], int(g["potential_points"])))
    if folded:
        print(f"Updated content.json: {len(folded)} note(s) added")
    return folded

_RANK = {"direct": 2, "adjacent": 1, "missing": 0}

def revision_feedback(baseline_reqs: list, after_reqs: list, coverage: dict) -> str:
    """The note handed back to the generation model when the tailored page
    scored below the untailored cut: which rubric rows lost status (with the
    evidence the baseline had for them) and which JD key terms never landed."""
    lines = []
    before = {r.get("requirement", ""): r for r in baseline_reqs}
    for a in after_reqs:
        b = before.get(a.get("requirement", ""))
        if not b:
            continue
        if _RANK.get(a.get("status"), 0) < _RANK.get(b.get("status"), 0):
            ev = "; ".join(b.get("evidence") or []) or "see the source CV"
            lines.append(f"- \"{a.get('requirement')}\" fell from {b.get('status')} to "
                         f"{a.get('status')}. The untailored resume carried this evidence: {ev}. "
                         f"Keep that content on the page, in the JD's words.")
    missing = (coverage or {}).get("missing") or []
    if missing:
        lines.append("- These JD key terms are absent from the page; place each verbatim "
                     "where the source honestly supports it: " + ", ".join(missing))
    if not lines:
        lines.append("- The page scored lower overall; keep every requirement's evidence "
                     "visible and lead each bullet with the JD's own terms.")
    return "\n".join(lines)


def normalize_match_analysis(analysis: dict) -> dict:
    """Recompute the score from the model's evidence classifications.

    The model chooses the material requirements and weights; Python owns the arithmetic
    so an encouraging-but-wrong total cannot slip into the applicant report.
    """
    analysis = dict(analysis or {})
    requirements = analysis.get("requirements") or []
    total_weight = sum(max(0, r.get("weight", 0)) for r in requirements)
    earned = sum(
        max(0, r.get("weight", 0)) * _MATCH_CREDIT.get(r.get("status"), 0.0)
        for r in requirements
    )
    analysis["overall_score"] = round(100 * earned / total_weight) if total_weight else 0
    return analysis

def match_report_markdown(analysis: dict, *, company: str, job_title: str,
                          coverage: Optional[dict] = None) -> str:
    lines = [
        f"# Match report: {job_title}",
        "",
        f"Company: {company}",
        f"Evidence-based match score: **{analysis.get('overall_score', 0)}/100**",
        "",
        "The score is a resume-evidence diagnostic, not a prediction of an interview or offer. "
        "Direct evidence earns full credit, adjacent evidence half credit, and missing evidence none.",
        "",
        "## Requirement breakdown",
        "",
    ]
    for req in analysis.get("requirements", []):
        lines.append(
            f"- **{req.get('status', 'missing').upper()} ({req.get('weight', 0)}%): "
            f"{req.get('requirement', '')}**"
        )
        for evidence in req.get("evidence", []):
            lines.append(f"  - Evidence: {evidence}")
        if req.get("gap_or_talking_point"):
            lines.append(f"  - Gap/talking point: {req['gap_or_talking_point']}")

    for heading, key in (
        ("Strongest matches", "strongest_matches"),
        ("Places to improve", "improvements"),
        ("Interview talking points", "interview_talking_points"),
    ):
        lines += ["", f"## {heading}", ""]
        lines += [f"- {item}" for item in analysis.get(key, [])]

    inferred = analysis.get("inferred_skills", [])
    if inferred:
        lines += ["", "## JD-aligned inferred skills", ""]
        for item in inferred:
            lines.append(
                f"- **{item.get('skill', '')}** ({item.get('group', '')}; "
                f"{item.get('kind', '')}, {item.get('basis', '')}) from "
                f"{item.get('source_item', '')}: “{item.get('source_quote', '')}”"
            )

    questions = analysis.get("clarifying_questions", [])
    if questions:
        lines += ["", "## Questions for the applicant", ""]
        lines += [f"- {question}" for question in questions]

    if coverage and (coverage.get("covered") or coverage.get("missing")):
        cov, miss = coverage.get("covered") or [], coverage.get("missing") or []
        lines += ["", f"## JD key terms carried: {len(cov)}/{len(cov) + len(miss)}", ""]
        lines += [f"- ✓ {t}" for t in cov]
        lines += [f"- ✗ {t}" for t in miss]

    notes_ = analysis.get("tailoring_notes") or []
    if notes_:
        lines += ["", "## What was emphasized or condensed", ""]
        lines += [f"- {n}" for n in notes_]
    return "\n".join(lines).rstrip() + "\n"

# ── Integrity lint ────────────────────────────────────────────────────────────
# The prompt forbids inventing collaboration (when constant.json marks the candidate
# as solo) and forbids any banned_summary_anchors as a summary headline. A prompt rule
# is not a guarantee, and this is a document someone signs their name to — so re-check
# the output and surface anything suspect for a human read.

_COLLAB_PATTERNS = [
    r"\bpartner(?:ed|ing|s)?\s+(?:closely\s+)?with\b",
    r"\bcollaborat(?:ed|ing|es|ion)\b",
    r"\b(?:work(?:ed|ing|s)?|liais(?:ed|ing))\s+(?:closely\s+)?(?:with|alongside)\b",
    r"\bcross[- ]functional(?:ly)?\b",
    r"\bsupport(?:ed|ing)\s+the\s+\w+\s+team\b",
    r"\bhand(?:ed|ing)?\s+off\s+to\b",
]

# A dollar figure is defensible only if it is (a) traceable to the source CV, or (b) the
# approved projected claim for a project listed under "## ELIGIBLE" in impact-metrics.md.
# Anything else is invented. Eligibility is parsed from that file rather than hardcoded, so
# deleting a section there immediately turns its figures into lint errors here.
_MONEY_RE = re.compile(r"\\?\$\s?\d[\d.,]*\s*(?:[KMB]|million|billion)?", re.I)

def _money(text: str) -> set:
    """Normalized money tokens in a string: '\\$1M+' and '$ 1m' both -> '$1M'."""
    return {re.sub(r"[\\\s,]", "", m).upper() for m in _MONEY_RE.findall(text)}

def _impact_eligible_tokens():
    """Distinctive words from the '###' project headings under '## ELIGIBLE'.
    Returns None if that section can't be found — the caller then trusts nothing."""
    body = re.search(r"^##\s+ELIGIBLE\s*$(.*?)(?=^##\s|\Z)", load_impact_reference(), re.M | re.S)
    if not body:
        return None
    tokens = set()
    for head in re.findall(r"^###\s+(.+)$", body.group(1), re.M):
        tokens |= set(re.findall(r"[a-z]{4,}", head.lower()))
    return tokens

def check_integrity(tailored: dict, source: dict, match_analysis: Optional[dict] = None,
                    profile: Optional[Profile] = None) -> list:
    """Warnings about claims a human must look at before this goes to an employer."""
    solo = bool(profile.solo_worker) if profile else False
    warnings = []
    summary = tailored.get("summary", "")
    eligible = _impact_eligible_tokens()
    if eligible is None:
        if (ROOT / "impact-metrics.md").exists():
            warnings.append("could not parse the '## ELIGIBLE' section of impact-metrics.md — all $ figures treated as unverified")
        eligible = set()
    source_money = _money(json.dumps(source))  # every real figure, bullets + notes

    for anchor in (profile.banned_summary_anchors if profile else []):
        if anchor and re.search(re.escape(anchor), summary, re.I):
            warnings.append(f"summary leads on '{anchor}' (banned as a headline)")
    source_titles = " ".join(e.get("title", "") for e in source.get("experience", []))
    if (re.search(r"\b(?:Senior|Sr\.?|Lead|Staff|Principal)\b", summary, re.I)
            and not re.search(r"\b(?:Senior|Sr\.?|Lead|Staff|Principal)\b", source_titles, re.I)):
        warnings.append("summary claims seniority absent from the candidate's source titles")
    if re.search(r"\b(?:AI[- ]assisted|agentic)\s+(?:products?|experiences?|concepts?)\b", summary, re.I):
        warnings.append("summary blurs AI tool usage with AI product-design experience")

    source_skills = {
        group.get("label"): set(group.get("entries", []))
        for group in source.get("skills", [])
    }
    source_text = json.dumps(source, ensure_ascii=False).casefold()
    inferred = {}
    for item in (match_analysis or {}).get("inferred_skills", []):
        skill = item.get("skill")
        group = item.get("group")
        kind = item.get("kind")
        basis = item.get("basis")
        quote = (item.get("source_quote") or "").strip()
        if not skill or not group or not quote or quote.casefold() not in source_text:
            warnings.append(
                f"skills/{group or '?'}: inferred skill '{skill or '?'}' lacks an exact source quote")
            continue
        if kind == "tool_or_technology" and basis != "direct":
            warnings.append(
                f"skills/{group}: tool or technology '{skill}' cannot be inferred by adjacency")
            continue
        if kind == "tool_or_technology" and skill.casefold() not in quote.casefold():
            warnings.append(
                f"skills/{group}: tool or technology '{skill}' is not named in its source quote")
            continue
        if kind not in {"capability", "method", "domain", "tool_or_technology"}:
            warnings.append(f"skills/{group}: inferred skill '{skill}' has an invalid kind")
            continue
        if basis not in {"direct", "adjacent"}:
            warnings.append(f"skills/{group}: inferred skill '{skill}' has an invalid basis")
            continue
        inferred[(group, skill)] = item

    for group in tailored.get("skills", []):
        label = group.get("label")
        if label not in source_skills:
            warnings.append(f"skills/{label}: group is absent from source")
            continue
        for entry in group.get("entries", []):
            if entry not in source_skills[label] and (label, entry) not in inferred:
                warnings.append(
                    f"skills/{label}: new entry '{entry}' has no evidence-backed inference record")

    def scan(text, where, listed=False):
        # Only a solo candidate is wronged by a claimed teammate; for everyone else this
        # would flag every accurate bullet they have.
        for pat in (_COLLAB_PATTERNS if solo else ()):
            m = re.search(pat, text, re.I)
            if m:
                warnings.append(f"{where}: claims collaboration ('{m.group(0)}') — verify it is in the source")
        for fig in _money(text) - source_money:
            if not listed:
                warnings.append(f"{where}: INVENTED figure {fig} — not in the source, and not an approved impact-metrics.md claim")

    scan(summary, "summary")
    for e in tailored.get("experience", []):
        for b in e.get("bullets", []):
            scan(b, f"experience/{e.get('company', '?')[:28]}")
    for p in tailored.get("projects", []):
        title = p.get("title", "?")
        listed = bool(eligible & set(re.findall(r"[a-z]{4,}", title.lower())))
        for b in p.get("bullets", []):
            scan(b, f"project/{title[:28]}", listed=listed)
    return warnings

_LATEX_ESCAPE_RE = re.compile(r"(?<!\\)([&%$#_])")
_URL_KEYS = {"link", "short_link", "portfolio_link"}

def escape_latex_strings(value):
    """Recursively escape naked & % $ # _ (not already backslash-escaped) in every
    string produced by the model. Applied only to the tailored dict. URL-bearing keys
    pass through untouched — escaping them would break \\href URLs."""
    if isinstance(value, str):
        return _LATEX_ESCAPE_RE.sub(r"\\\1", value)
    if isinstance(value, list):
        return [escape_latex_strings(v) for v in value]
    if isinstance(value, dict):
        return {k: (v if k in _URL_KEYS else escape_latex_strings(v)) for k, v in value.items()}
    return value

# ── Compile PDF ────────────────────────────────────────────────────────────────

def _page_count(log: str):
    # pdflatex wraps the "Output written on <path> (N pages" line at ~79 cols, so
    # strip all whitespace first and match the compacted form.
    import re
    compact = re.sub(r"\s+", "", log)
    m = re.search(r"Outputwrittenon.*?\((\d+)pages?", compact)
    return int(m.group(1)) if m else None

# A4 (842pt tall) with 0.5in margins: the text block runs from y=36pt to y=806pt.
_TEXT_BOTTOM = 36.0
_TEXT_TOP = 806.0

def _page_fill(pdf_path: Path) -> Optional[float]:
    """Vertical slack in points between the last ink on page 1 and the bottom of the
    text area. Ghostscript writes its bbox to stderr. Returns None if gs is missing or
    its output can't be parsed — measurement is a nice-to-have, never fatal."""
    gs = shutil.which("gs")
    if not gs:
        return None
    try:
        result = subprocess.run(
            [gs, "-q", "-dNOPAUSE", "-dBATCH", "-dFirstPage=1", "-dLastPage=1",
             "-sDEVICE=bbox", str(pdf_path)],
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    m = re.search(r"%%HiResBoundingBox:\s*\S+\s+(\S+)", result.stderr)
    if not m:
        return None
    try:
        lly = float(m.group(1))
    except ValueError:
        return None
    return lly - _TEXT_BOTTOM

def render_tex(data: dict) -> str:
    env = Environment(
        loader=FileSystemLoader(ROOT),
        variable_start_string="((",
        variable_end_string="))",
        block_start_string="(%",
        block_end_string="%)",
        comment_start_string="(#",
        comment_end_string="#)",
        trim_blocks=True,
        lstrip_blocks=True,
    )
    return env.get_template("template.tex").render(**data)

def compile_to_dir(data: dict, dest: Path, filename: str = "resume.pdf") -> int:
    """Compile rendered data to <filename> in dest. Returns page count (or 0)."""
    tex_source = render_tex(data)
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        tex_file = tmp_path / "resume.tex"
        tex_file.write_text(tex_source)
        result = subprocess.run(
            [PDFLATEX, "-interaction=nonstopmode", "-output-directory", str(tmp_path), str(tex_file)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print("pdflatex error. Log tail:")
            print(result.stdout[-3000:])
            sys.exit(1)
        pages = _page_count(result.stdout) or 0
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copy2(tmp_path / "resume.pdf", dest / filename)
        return pages

def _shrink_one_step(data: dict) -> bool:
    """Remove the single smallest low-value unit. Returns True if something changed.

    The model over-generates a JD-ranked superset, so the tail of every list is by
    construction the least relevant thing left — every cut comes off the end.

    Order matters: every fine cut (a trailing bullet, a skill, a whole optional
    section) runs BEFORE any whole experience or project entry is dropped, so the
    page fills tight with small bites instead of overshooting by a role. A whole
    entry carries JD-match evidence the re-score will miss; a trailing bullet
    usually doesn't."""
    experience = data.get("experience", [])
    projects = data.get("projects", [])
    skills = data.get("skills", [])

    # ── fine cuts first: trailing bullets, down to a 2/1 floor ──
    for j in reversed(experience):
        if len(j.get("bullets", [])) > 3:
            j["bullets"] = j["bullets"][:-1]
            return True
    for p in reversed(projects):
        if len(p.get("bullets", [])) > 2:
            p["bullets"] = p["bullets"][:-1]
            return True
    for j in reversed(experience):
        if len(j.get("bullets", [])) > 2:
            j["bullets"] = j["bullets"][:-1]
            return True
    for p in reversed(projects):
        if len(p.get("bullets", [])) > 1:
            p["bullets"] = p["bullets"][:-1]
            return True
    # one entry off the largest skill group (keep at least 4)
    biggest = max(skills, key=lambda s: len(s.get("entries", [])), default=None)
    if biggest and len(biggest.get("entries", [])) > 4:
        biggest["entries"] = biggest["entries"][:-1]
        return True
    # whole optional sections lose to experience/projects on a one-pager;
    # least-valuable first, one per call
    for key in ("languages", "publications", "awards", "certifications"):
        if data.get(key):
            data.pop(key)
            return True

    # ── only now: whole entries, surplus before target shape, most recent last ──
    if len(projects) > 2:
        projects.pop()
        return True
    if len(experience) > 4:
        experience.pop()
        return True
    if len(projects) > 1:
        projects.pop()
        return True
    if len(experience) > 3:          # never below 3, never the most recent (index 0)
        experience.pop()
        return True
    return False

def compile_single_page(data: dict, dest: Path, filename: str, max_steps: int = 20):
    """Compile, and if it overflows one page, iteratively trim the lowest-priority
    content and recompile until it fits (or nothing more can be cut). Capped at
    max_steps so an inherently multi-page add-on (e.g. full testimonials) can't gut
    the whole resume — it stops and warns instead.

    Returns (pages, data) — data is the TRIMMED copy actually in the PDF, so the
    caller can save a JSON that matches the document."""
    import copy
    data = copy.deepcopy(data)
    pages = compile_to_dir(data, dest, filename)
    steps = 0
    while pages > 1 and steps < max_steps and _shrink_one_step(data):
        steps += 1
        pages = compile_to_dir(data, dest, filename)
    if steps:
        print(f"Trimmed    : {steps} item(s) to fit one page")
    return pages, data

def safe_name(raw: str, fallback: str) -> str:
    """Filesystem-safe folder name. Anonymous postings return an empty company, which
    would silently collapse <company>/<job> into just <job> and let two different
    employers' identical roles overwrite each other."""
    cleaned = "".join(c if c.isalnum() or c in " -_" else "" for c in raw).strip().replace(" ", "_")
    return cleaned or fallback

def compile_pdf(data: dict, output_dir: Path, company: str, job: str, mode: str = "resume",
                basename: str = "Resume") -> Path:
    safe_company = safe_name(company, "Unknown_Company")
    safe_job = safe_name(job, "Unknown_Role")
    dest = output_dir / safe_company / safe_job
    filename = f"{basename}_Resume.pdf" if mode == "resume" else f"{basename}_CV.pdf"

    if mode == "resume":
        pages, data = compile_single_page(data, dest, filename)
        if pages > 1:
            print(f"Warning    : still {pages} pages after trimming — drop -r/-ee, or shorten the longest bullets in content.json.")
        else:
            print("Pages      : 1 (single page OK)")
            slack = _page_fill(dest / filename)
            if slack is not None:
                fill = (_TEXT_TOP - _TEXT_BOTTOM - slack) / (_TEXT_TOP - _TEXT_BOTTOM)
                print(f"Fill       : {fill:.0%} of page ({slack:.0f}pt slack)")
                if slack > 90:
                    print("Hint       : page is underfilled — the model returned less content than the trimmer needed.")
    else:
        pages = compile_to_dir(data, dest, filename)
        print(f"Pages      : {pages} (CV, multi-page)")
    return dest / filename, data

# ── Cover letter prompt ────────────────────────────────────────────────────────

COVER_LETTER_SYSTEM = """You are helping a job applicant prepare to write a tailored cover letter. Given a CV and job description, produce a concise analysis in exactly the markdown format below. No extra commentary.

<Name of company and Role>

# TOP-NEEDS
List the 3 most essential things the employer actually needs from this hire — framed as concrete tasks or outcomes, not just rephrased requirements. Be specific.

# LANGUAGE
List the key terms, phrases, and vocabulary the JD uses to describe the role and its context. These are the words the cover letter should mirror.

# AREA OF GROWTH
Identify one skill or experience area that appears in the JD but is absent or thin in the CV. Briefly explain how the candidate's existing experience provides a foundation to grow into it.
"""

def generate_cover_letter_prompt(profile: Profile, jd: str, extra_context: str = "",
                                 *, backend: str, model: str,
                                 binary: Optional[str] = None,
                                 timeout: float = llm.DEFAULT_TIMEOUT) -> str:
    content_keys = {"summary", "experience", "projects", "skills"}
    content_only = {k: v for k, v in profile.content.items() if k in content_keys}

    system = (
        COVER_LETTER_SYSTEM
        + f"\n\nHere is the candidate's CV (JSON):\n{json.dumps(content_only, separators=(',', ':'))}"
    )
    user = (
        f"Here is the job description:\n---\n{jd}\n---"
        + (f"\n\nAdditional context from the applicant:\n---\n{extra_context}\n---" if extra_context else "")
    )

    # Pinned low: this writes a prompt for a human to work from, not the resume itself.
    reply = llm.complete_text(backend=backend, model=model, system=system, user=user,
                              thinking="low", binary=binary, timeout=timeout)
    return reply.text.strip()

# ── Main ───────────────────────────────────────────────────────────────────────

def run(args) -> None:
    jd = read_jd(args.jd_file).strip()
    if not jd:
        print("Error: job description is empty.")
        sys.exit(1)

    # Only the flag/file sources here -- typed context is asked for later, in the
    # one grouped question block, so nothing blocks before the score.
    extra_context = read_extra_context(args, interactive=False)

    mode = "cv" if args.cv else "resume"

    profile = Profile.load(ROOT, mode=mode)

    # A missing CLI, a bad preset, or an unprefixed --model should fail here, before any
    # LaTeX work and before the JD is written anywhere.
    try:
        backend, model = llm.resolve(args.preset, model=args.model,
                                     backend=args.provider or profile.backend)
    except llm.LLMError as e:
        print(f"Error: {e}")
        sys.exit(1)

    # The baseline score runs a tier below the generation model (or --score-preset).
    # Pass the effective preset (the default when none was given) so a default run
    # scores one tier below DEFAULT_PRESET, not the backend's cheapest — matching install.py.
    score_backend, score_id = llm.score_model(
        backend, args.preset or llm.DEFAULT_PRESET[backend], args.model,
        override_preset=args.score_preset)

    print(f"Mode      : {mode.upper()} (source: {CONTENT_FILE[mode]})")
    print(f"Model     : {model} via {backend}"
          + (f" (thinking={args.thinking})" if backend == "openclaw" else ""))

    # ── B. baseline score ──────────────────────────────────────────────────
    # The score is the only thing left that needs a model call to decide. It runs
    # first, on its own, so the applicant sees a number before answering anything.
    no_score = getattr(args, "no_score", False)
    # Baseline scores an UNTAILORED one-page cut of the corpus, not the whole
    # corpus -- so "baseline -> tailored" compares like with like (see
    # _generic_one_page). Gap questions come from the same call.
    pre = ({"overall_score": None, "requirements": [], "gap_questions": []} if no_score
           else score_jd(_generic_one_page(profile), jd, backend=score_backend,
                         model=score_id, binary=args.binary, timeout=args.timeout,
                         label="an untailored one-page cut of your CV"))
    baseline = pre["overall_score"]
    if baseline is not None:
        ui.score_panel(baseline, pre["requirements"])

    # ── One interactive stop: every question this run has, together ─────────
    # After the score, ask everything at once -- cover letter, education (when the
    # JD is silent), then the gap answers -- and then let D-E run to the end
    # without stopping again. Each of these used to fire at its own point in the
    # pipeline, so a run kept pausing after work the applicant was waiting on.
    interactive_ok = not args.no_interactive and ui.interactive()
    want_cl = args.cover_letter or (interactive_ok
                                    and ui.confirm("Generate a cover-letter prompt?", default=False))
    if interactive_ok and not extra_context:
        extra_context = prompt_extra_context(args)
    if mode == "resume":
        include_education, edu_line = decide_education(profile, jd, args)
    else:
        include_education, edu_line = False, None   # CV mode injects education directly, below
    if edu_line:
        print(edu_line)

    folded = []
    if interactive_ok and pre["gap_questions"]:
        folded = collect_gap_answers(profile, pre["gap_questions"],
                                     backend=score_backend, model=score_id,
                                     binary=args.binary)
        if folded:
            profile = Profile.load(ROOT, mode=mode)   # pick up the note writes

    # ── D. generate → compile → re-score, revising when the score fell ─────
    # The tailored page is re-scored on the baseline's OWN rubric (same
    # requirements, same weights). If it still lands below the untailored cut,
    # the model gets told exactly which requirements slipped and which JD terms
    # went missing, and tries again -- up to --max-revisions times. The best
    # attempt ships. A tailoring pass that lowers the match is a bug, not an
    # outcome to report.
    key_terms = pre.get("key_terms") or []
    rubric = pre.get("requirements") or []
    can_rescore = baseline is not None and not no_score
    max_rev = max(0, int(getattr(args, "max_revisions", 2) or 0)) if can_rescore else 0

    # Precedence: --output-dir / -o (its argparse default is $RESUME_TEX_OUTPUT_DIR),
    # then profile.output_dir from constant.json, then ~/Documents/Applications. One
    # run per application, each in its own <Company>/<Role>/ folder.
    output_base = Path(
        args.output_dir or profile.output_dir or (Path.home() / "Documents" / "Applications")
    ).expanduser()
    # Identity fields only (name, email, etc.) — never pull content fields from the profile here.
    constant_fields = {"name", "email", "phone", "linkedin", "location"}
    not_content = set(constant_fields) | {"projects_title", "skills_title", "recommendations"}

    def _attempt(revision_notes: str) -> dict:
        tailored = tailor_content(
            profile, jd, mode=mode,
            include_education=include_education, extra_context=extra_context,
            backend=backend, model=model, thinking=args.thinking,
            binary=args.binary, timeout=args.timeout,
            key_terms=key_terms, revision_notes=revision_notes,
        )
        tailored = enforce_portfolio(tailored, jd, profile)
        tailored = enforce_caps(tailored)

        # Applicant-facing diagnosis, not resume content. Keep it out of LaTeX and
        # the shipped content JSON, and recompute its arithmetic before displaying it.
        match_analysis = normalize_match_analysis(tailored.pop("match_analysis", {}))

        # Pop reference-only fields before LaTeX escaping — they are printed and used
        # in paths, never rendered, so "AT&T" must stay "AT&T". Anonymous postings
        # return null (or ""), so coalesce rather than rely on a dict default.
        company = (tailored.pop("company_name", None) or "").strip() or "Unknown Company"
        job_title = (tailored.pop("job_title", None) or "").strip() or "Unknown Role"
        tailored = escape_latex_strings(tailored)

        render_data = dict(profile.identity)
        render_data.update(tailored)
        render_data["projects_title"] = profile.projects_title
        render_data["skills_title"] = profile.skills_title
        render_data["location"] = args.location or profile.location_for_jd(jd)
        if args.extended_education:
            # Inject the degree(s) verbatim and attach each institution's coursework.
            edu_list = [dict(e) for e in profile.content.get("education", [])]
            for e in edu_list:
                courses = COURSEWORK_BY_INSTITUTION.get(e.get("institution"))
                if courses:
                    e["coursework"] = courses
            render_data["education"] = edu_list
        elif mode == "cv":
            # A CV always shows education (no coursework unless -ee).
            render_data["education"] = profile.content.get("education", [])
        if args.recommendations:
            render_data["recommendations"] = RECOMMENDATIONS

        # compile_pdf returns the post-trim data, so the saved JSON matches the PDF
        # (the model over-generates a superset; the trimmer decides what ships).
        pdf_path, final_data = compile_pdf(render_data, output_base, company, job_title,
                                           mode=mode, basename=profile.doc_basename)
        shipped = {k: v for k, v in final_data.items() if k not in not_content}
        coverage = term_coverage(shipped, key_terms)

        # Re-score what actually shipped, through the SAME scorer and rubric as the
        # baseline -- one honest before/after on one axis, not the generation
        # model's self-assessment of an over-generated superset.
        after = None
        if can_rescore:
            after = score_jd(Profile.from_dicts(profile._constant, shipped, mode=mode), jd,
                             backend=score_backend, model=score_id, binary=args.binary,
                             timeout=args.timeout, label="the tailored resume",
                             rubric=rubric)
        score = after["overall_score"] if after and after["overall_score"] is not None else None
        return dict(tailored=tailored, match_analysis=match_analysis, company=company,
                    job_title=job_title, render_data=render_data, pdf_path=pdf_path,
                    shipped=shipped, coverage=coverage, after=after, score=score)

    best, attempt, revision_notes = None, 0, ""
    while True:
        cur = _attempt(revision_notes)
        if best is None or (cur["score"] or -1) > (best["score"] or -1):
            best = cur
        if cur["score"] is None or cur["score"] >= baseline or attempt >= max_rev:
            break
        attempt += 1
        revision_notes = revision_feedback(rubric, cur["after"]["requirements"], cur["coverage"])
        print(f"Tailored   : {cur['score']}/100 is below the untailored {baseline}/100 "
              f"-- revising ({attempt}/{max_rev})")

    if best is not cur:
        # The last attempt overwrote the PDF; put the best one back on disk.
        best["pdf_path"], final_data = compile_pdf(
            best["render_data"], output_base, best["company"], best["job_title"],
            mode=mode, basename=profile.doc_basename)
        best["shipped"] = {k: v for k, v in final_data.items() if k not in not_content}
    tailored, match_analysis = best["tailored"], best["match_analysis"]
    company, job_title, shipped = best["company"], best["job_title"], best["shipped"]
    render_data, pdf_path, coverage = best["render_data"], best["pdf_path"], best["coverage"]
    tailored_score = best["score"] if best["score"] is not None else match_analysis.get("overall_score", 0)
    if can_rescore and best["score"] is not None and best["score"] < baseline:
        print(f"Warning   : best of {attempt + 1} attempts still scores {best['score']} "
              f"< baseline {baseline}; see the match report for what is missing.")

    print(f"\nCompany   : {company}")
    print(f"Role      : {job_title}")
    print(f"Portfolio : {tailored.get('portfolio_link', '?')}")
    print(f"Location  : {render_data['location']}"
          + (" (overridden)" if args.location else " (JD default)"))
    if args.extended_education:
        edu_list = render_data.get("education") or []
        total = sum(len(e.get("coursework") or []) for e in edu_list)
        print(f"Ext. edu  : {len(edu_list)} degree(s) + {total} relevant courses (per institution)")
    if args.recommendations:
        print(f"Recs     : {len(RECOMMENDATIONS)} recommendation(s) appended")

    # Save the tailored content alongside the PDF — mode-scoped so a --cv run and a
    # resume run can coexist in the same <company>/<job> folder.
    safe_company = safe_name(company, "Unknown_Company")
    safe_job = safe_name(job_title, "Unknown_Role")
    json_dir = output_base / safe_company / safe_job
    json_dir.mkdir(parents=True, exist_ok=True)

    # Save the raw job description alongside the output for future reference
    today = date.today().isoformat()
    jd_filename = f"jd-{safe_job or 'role'}-{today}.txt"
    jd_path = json_dir / jd_filename
    jd_path.write_text(jd)
    print(f"Saved JD   : {jd_path}")

    # ── E. cover letter (decided upfront; want_cl already set) ─────────────
    if want_cl:
        print("Generating cover letter prompt...")
        cl_prompt = generate_cover_letter_prompt(
            profile, jd, extra_context=extra_context, backend=backend, model=model,
            binary=args.binary, timeout=args.timeout)
        cl_path = json_dir / "cover-letter-prompt.md"
        cl_path.write_text(cl_prompt)
        print(f"Saved CL   : {cl_path}")

    print(f"Saved PDF  : {pdf_path}")
    json_path = json_dir / f"content.{mode}.json"
    json_path.write_text(json.dumps(shipped, indent=2, ensure_ascii=False) + "\n")
    print(f"Saved JSON : {json_path}")

    match_analysis["term_coverage"] = coverage
    match_json_path = json_dir / "match-report.json"
    match_json_path.write_text(json.dumps(match_analysis, indent=2, ensure_ascii=False) + "\n")
    match_md_path = json_dir / "match-report.md"
    match_md_path.write_text(match_report_markdown(
        match_analysis, company=company, job_title=job_title, coverage=coverage))
    print(f"Saved Match: {match_md_path}")

    ui.report(baseline=baseline,
              tailored=tailored_score,
              folded=folded,
              improvements=match_analysis.get("improvements", []),
              questions=match_analysis.get("clarifying_questions", []),
              warnings=check_integrity(shipped, profile.content, match_analysis, profile=profile),
              coverage=coverage if key_terms else None)
    print("\nDone.")


def main():
    run(parse_args())

if __name__ == "__main__":
    main()
