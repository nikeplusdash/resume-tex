"""Profile — everything about the applicant. Edit constant.json / content.json, or subclass."""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Optional

# ── Note targets: "kind:name", disambiguated only on a collision ──────────────
#
# score_jd's SCORE_SYSTEM_PROMPT documents "experience:<company>", "project:<title>"
# and "education:<institution>" as the target format, and tailor._known_note_targets
# validates against exactly those forms -- so the canonical form must never change.
# But two entries can legitimately share a name (a promotion is two experience
# entries at one company), and the canonical form alone cannot then say which one a
# note is about. NOTE_TARGET_SEP marks an explicit, disambiguated form used ONLY
# when a collision exists: "kind:name::<0-based index among the colliding entries>".
# "::" is safe here because it is not a character any of company / project title /
# institution would plausibly contain, and it is never asked of or produced by the
# model (SCORE_SYSTEM_PROMPT only ever documents the plain form).
NOTE_TARGET_SEP = "::"

NOTE_KINDS = (("experience", "experience", "company"),
              ("project", "projects", "title"),
              ("education", "education", "institution"))


def note_targets(content: dict) -> list:
    """[(kind, entry, target)] for every named entry across experience/projects/
    education -- one row per entry, in the order each kind's list is stored.

    `target` is the canonical "kind:name" form when `name` is unique within its
    kind, or "kind:name::<index>" when more than one entry shares `name` -- the
    entry's 0-based position among that group, so each colliding entry still has
    a target that resolves to IT specifically, not to whichever comes first.
    """
    out = []
    for kind, key, match in NOTE_KINDS:
        entries = content.get(key) or []
        counts = Counter(e.get(match) for e in entries if e.get(match))
        seen = {}
        for entry in entries:
            name = entry.get(match)
            if not name:
                continue
            canonical = f"{kind}:{name}"
            if counts[name] > 1:
                idx = seen.get(name, 0)
                seen[name] = idx + 1
                target = f"{canonical}{NOTE_TARGET_SEP}{idx}"
            else:
                target = canonical
            out.append((kind, entry, target))
    return out


def _split_note_target(target: str):
    """("kind", "name", index_or_None) from a plain or disambiguated target."""
    kind, _, rest = target.partition(":")
    if NOTE_TARGET_SEP in rest:
        name, _, idx_s = rest.partition(NOTE_TARGET_SEP)
        return kind, name, (int(idx_s) if idx_s.isdigit() else None)
    return kind, rest, None


# ── Prompt text (moved verbatim from tailor.py) ───────────────────────────────

SYSTEM_PROMPT_BASE = """Create a truthful, JD-tailored resume and a separate match analysis from the candidate's source JSON. Optimize selection, order, and wording; do not optimize by inflating level, scope, outcomes, or skills. Return exactly the requested schema.

REFERENCE FIELDS:
- Set company_name from the JD, or null when the employer is unnamed.
- Set job_title to the JD's natural primary role title. Preserve its advertised seniority here because this field labels the application, but NEVER use that seniority as the candidate's title or identity unless it appears in the source CV.
- Do not use em/en dashes or hyphens as dashes in prose. Preserve "--" in dates and existing LaTeX escapes.

%%PORTFOLIO_RULE%%

SKILLS (reshape + cap):
- The input's skill groups are: %%SKILL_GROUPS%%. Keep these exact labels; never invent a new group.
- Prefer the JD's exact, standard skill term when the source clearly demonstrates the same competency. An emitted skill may be either (a) copied from the input skills list, or (b) a JD term or concise industry-standard term inferred from a source role/project.
- Every inferred skill MUST have one inferred_skills audit record containing its group, the matching JD term, the source role/project name, and a short source_quote copied VERBATIM from a source bullet or note. No exact source quote means no inferred skill.
- Use semantic equivalence, not keyword similarity. A related activity or domain may support an adjacent capability, method, or domain skill when the connection is specific and interview-defensible. Keep the assumption pool narrow: a merely associated artifact, stakeholder, industry convention, or likely responsibility is not evidence.
- Classify every inferred skill by kind and basis. Adjacent inference is allowed only for capability, method, or domain. A tool, software product, platform, framework, or programming language must use kind=tool_or_technology and basis=direct, and its exact name must appear in source_quote. Never infer one technology from another.
- Do not list both a source synonym and its JD-aligned replacement when one term is enough. Never infer a tool, domain, leadership skill, or responsibility that the projects and roles do not demonstrate.
- Include a group only when it adds evidence for this role. Use 3-5 high-signal entries per included group, ordered by relevance. A JD need not name a technical tool verbatim when that tool is concrete evidence of a requested capability such as technical depth or prototyping.
- Prefer specific tools and methods over broad duplicates. Omit unrelated programming languages and tools even when the JD broadly requests "technical depth." Stop when another entry would not materially improve the match. HARD CAP: 5 entries per group.

CONTENT RULES:
- EXPERIENCE ORDER: the most recent role MUST always appear and must remain first — omitting it creates a visible resume gap. Remaining roles may be included or skipped based on JD relevance; prefer roles whose skills, domain, or industry most closely match the JD. Apply the same ordering logic to projects (no recency constraint there — include or skip freely). Reorder/reword bullets within each entry to lead with JD-relevant achievements. Do NOT fabricate. Source: existing bullets + "notes" fields (use notes to derive bullets covering JD angles the prewritten bullets miss).
- Bullet form: [strong verb] + [what, named when the source names it] + [how — the method, system, or stack] + [concrete or quantified outcome] + [PURPOSE CLAUSE in the JD's words]. The purpose clause is a short "to <verb> <JD noun>" tail ("to improve usability and scalability", "to support data-driven decisions", "informing design decisions") that names what the work was FOR in the vocabulary this JD uses. It is REQUIRED on every bullet that has no metric and is welcome on bullets that do, provided the source supports that purpose — this tail is where the JD's terms land when there is no number to carry them. Filler that adds no purpose ("contributing to", "helping to", "responsible for", "directly addressing") stays banned; a purpose clause names a concrete aim, filler does not. Not every bullet has every part in the source — include the ones the source supports; never invent a mechanism, artifact name, or purpose to fill a slot.
- Prefer the before→after form when the source contains both endpoints ("from 12 to 7", "25 min to 5 min", "quarterly to near-zero") — it reads as more credible than a bare percentage because both ends are checkable. A bare percentage is the fallback when the source has the delta but not both named endpoints, not the goal to reach for. Never derive an endpoint that is not in the source — do not infer a "before" or "after" value the source does not state.
- Carry the source's figure exactly as recorded; never round it to a cleaner-sounding number. A precise, odd number (55%, 3.5x, 135 tests) reads as measured; a round one (50%, 2x) reads as invented, even when it is real.
- Name the system, product, or tool the work used when the source names it.
- Bullet-keep precedence: (1) keep a bullet that carries a real metric or concrete fact; (2) a metric-less bullet MAY be rewritten with a PROJECTED figure per IMPACT METRICS below; (3) only if neither applies, drop it. Every included experience/project entry MUST retain at least 1 bullet. The most-recent-role rule outranks bullet-dropping (never drop the most recent role's last bullet to zero).
- Metrics: prefer real, source-attested numbers (counts, minutes, users, scores, $). Drop vague metrics ("intuitiveness +60%"); replace with raw facts (25→5 min, 1100 users, 40 components) or omit.
- Tense: past roles past, current may be present. Never repeat a lead verb within one role; vary verbs.
- Anti-redundancy: no single keyword or skill phrase (e.g. WCAG, design system, accessibility, documentation) leads more than 2 bullets across the whole resume.
- Banned phrases anywhere: proven track record, passionate about, dramatically, seamless(ly), cohesive, robust, leverage/leveraging, faster than ever before, successfully, responsible for, helped to, strong narrative, comprehensive (unless followed by concrete noun), mere, directly, operating at the intersection of, best-in-class, world-class, cutting-edge and any excessively flamboyant word.

PRIORITIZE, DON'T INVENT (this is the core job):
- Never add roles, projects, tools, or responsibilities that are not in the input. Every claim stays traceable to the bullets + notes. Your leverage is ORDER and FRAMING, not new facts.
- Surface the most JD-relevant experience, projects, and skills first; drop or bury the rest (subject to the EXPERIENCE ORDER rule). Do not change WHAT the candidate did — change what leads.
%%SOLO_RULE%%
- MIRROR JD VOCABULARY, NEVER JD RESPONSIBILITIES. Reuse the JD's nouns and domain terms only to describe work the candidate genuinely did. A duty listed in the JD is not evidence the candidate has done it — restating a JD bullet as the candidate's experience is fabrication, and it is the single worst failure mode of this task.
- ACTIVELY ADOPT THE JD'S ACTION VERBS. When the JD leads with a specific verb (drove, partnered, instrumented, shipped, owned, scaled, championed, operationalized) and the candidate's source describes that same action in weaker or vaguer words, rewrite the bullet to lead with the JD's verb. This is not optional polish — it is the main lever for raising keyword match. Also surface the JD's skill nouns and phrasings in the summary and bullets wherever the underlying work genuinely supports them. The rewrite changes WORDS ONLY: never the action taken, its scope, the tools or systems used, or any number. If no honest re-wording brings the JD's term in, leave the bullet as it is rather than stretch the fact.
- JD KEY TERMS CHECKLIST. The user message may carry a "JD KEY TERMS" list: the phrases a keyword screen will look for (skill nouns, tools, methods, domain terms, lead verbs). Work through it deliberately: for EACH term, find the source bullet, note, or skill that genuinely demonstrates it and place the term VERBATIM (same spelling, same word form as the JD) in that bullet, the summary, or the skills list — a paraphrase does not count for a keyword screen. A term with no honest home stays out; never attach a term to work that does not demonstrate it. Aim to carry every supportable term at least once; a term already used twice need not be repeated. Prefer a JD term over a synonym of your own everywhere the meaning is the same ("user research" not "user studies", "design system" not "component library" when the JD says design system).
- DISTINGUISH AI PRODUCT DESIGN FROM AI-ASSISTED WORK. Designing an interface, trust pattern, feedback flow, or interaction for an AI feature is AI experience design. Using Gemini, Figma Make, Copilot, or an agentic coding tool to build or test something is workflow/tool usage. Never turn the latter into an "AI product," "agentic product," or claimed AI experience. Do not aggregate unrelated projects into a vague claim such as "built and tested AI-assisted products." Name the actual artifact and the candidate's actual role.

SPECIALIST FRAMING:
- Rewrite generalist phrasing into the precise vocabulary a senior practitioner in THIS JD's domain would use — but only where the underlying work genuinely supports it. Lead each entry with the specialized competency the JD is hiring for.

IMPACT METRICS (ELIGIBILITY IS A HARD GATE):
- The IMPACT REFERENCE below lists the ONLY projected figures that may ever appear. Use a listed claim only on its own project, only where a REAL bullet has no number, at most one per bullet, framed with "projected"/"addressable"/"targeting"/"est." — never as a realized result. It never displaces a real fact.
- Every other project gets NO invented figure. Ever. No dollar value, no market size, no valuation, no consulting rate. If a project has no number, IT HAS NO NUMBER — a bullet with no metric beats one with a metric that cannot be defended.
- NEVER price a business that does not exist ("brand equity at exit", "ARR at X% adoption", "$X in consulting scope"). NEVER assume an efficacy rate for something unbuilt ("our AI cuts review time 60%" for a Figma prototype). A legitimate figure sizes the SCALE OF THE REAL PROBLEM the work targets; it never claims what the artifact achieved. Never fabricate a precise percentage.
- Salary, budget, or funding numbers in the JD are NOT the candidate's metrics. Never copy them into a bullet.

SUMMARY:
- 2-3 sentences, 40-75 words total; a third sentence is permitted only for the current/ongoing-work clause below. Shorter and denser beats padded — this is a one-page resume and every summary word costs a bullet elsewhere.
- Open with a source-attested professional identity, normally the current/recent source title, not the advertised job_title. Never call the candidate Senior, Lead, Staff, Principal, or a people manager unless that title/scope exists in the source.
- State years only when the dated source roles support the number. When the source roles span more than one industry or product domain and the source names them, name that breadth (e.g. "across healthtech and fintech"); when the source is single-domain, skip this rather than inventing variety.
- Sentence 2 should connect two or three of the JD's most important requirements to specific evidence. Prefer a precise product domain, named artifact, user problem, shipped outcome, or defensible metric over a list of responsibilities. When the source has a real, defensible metric, lead with the strongest one and pair it with the mechanism that produced it — the stack, method, or system — rather than the bare number; name the employer or client alongside the outcome only when the source names them. When the source has no defensible metric, use a named artifact or a concrete shipped outcome in its place instead — never manufacture a figure to fill the slot.
- An optional final clause or third sentence may name current or ongoing work when the source has an active role or project. Do not cram the entire JD or a tool inventory into the summary.
- KEYWORD DENSITY: the summary is the first thing a keyword screen reads, so it must also carry the JD's core vocabulary. Work at least 5 of the JD KEY TERMS in verbatim (skill nouns and methods such as "end-to-end design", "user research", "usability testing", "design systems", "B2B"), and name up to 3 JD-listed tools the source shows — all only where the source genuinely supports them. Prefer the JD's own phrasing over a synonym in every slot where the meaning is the same. The concrete anchor (metric or named artifact) still leads sentence 2; the key terms are woven around it, not substituted for it.
%%SUMMARY_BANS%%%%SUMMARY_SOLO%%
MATCH ANALYSIS (not resume copy):
- Extract 6-10 material requirements from the JD. Include every explicit minimum such as years, degree, location/time overlap, domain, or leadership expectation as its own requirement. Weights must total 100.
- Weight hiring gates as hiring gates, not by how much resume evidence is available: an explicit minimum-years or required credential gate normally receives 15-25 points; the JD's defining domain receives 10-20; a single tool normally receives no more than 5-8. Do not combine unrelated requirements into one row to conceal a gap.
- Mark each requirement direct only when the source explicitly proves it, adjacent when transferable evidence exists but the exact requirement is not proved, or missing when there is no meaningful evidence. Do not award direct credit for keyword similarity alone.
- overall_score = sum of weight for direct requirements + half the weight for adjacent requirements, rounded to the nearest whole number. Missing earns zero. Do not soften hard gaps to make the score encouraging.
- Evidence must name source roles/projects and facts. Improvements must say what evidence, portfolio detail, metric, or future experience would close a gap; never suggest inventing it. Interview talking points may frame adjacent experience but must state the boundary honestly.
- inferred_skills is the audit ledger for every resume skill not copied exactly from the input skill arrays. Use an empty list when no inference is needed.
- clarifying_questions should contain only short questions whose answers could substantively improve the resume or match analysis, especially a promising JD skill that appears plausible but is not adequately documented. Do not ask for facts already present. Use an empty list when nothing material is ambiguous.
- tailoring_notes: one line per included experience or project entry, "<entry name>: <what you emphasized, expanded, or condensed and why>" (e.g. "Acme Corp: emphasized the B2B platform and PHP/WordPress work the JD asks for; condensed the unrelated internal tooling"). This is the applicant's change log for review, never resume copy.

EDUCATION FIELD: Omit the "education" field entirely unless an "education" array was present in the input CV JSON.
"""


# ── Personalized prompt pieces ────────────────────────────────────────────────
#
# Three parts of the prompt above depend on who is applying, so they are tokens filled
# in from constant.json rather than sentences baked into the rules.

PORTFOLIO_RULE_TWO = """PORTFOLIO_LINK — the two portfolios differ by EMPHASIS, not by medium. BOTH contain web work, so whether the role mentions websites, HTML/CSS, or front-end code tells you NOTHING about which to send. Choose on what the role is mainly hiring for:
1. "%%VISUAL%%" — the VISUAL portfolio (visual + UX). Choose it when the core craft is visual: graphic design, brand and identity, motion, illustration, print, marketing collateral, or visual/UI design. A visual role that ALSO asks for HTML/CSS/WordPress/JavaScript/front-end work is still this one — listed web skills never move a visual role off it.
2. "%%RESEARCH%%" — the RESEARCH portfolio (research + UX). Choose it when the core is UX research, product discovery, usability testing, strategy, or systems and product thinking, with visual craft secondary.
If a role genuinely straddles both, follow the JOB TITLE: a title naming graphic, visual, motion, or brand work takes 1; a title naming research, product, or UX strategy takes 2."""

PORTFOLIO_RULE_ONE = """PORTFOLIO_LINK — set it to exactly "%%LINK%%". The candidate has one portfolio; never substitute, shorten, or invent another URL."""

# Only for a candidate who genuinely works end-to-end alone. Inventing a teammate is one
# of the easiest fabrications for a model to slip in, because JDs are written in the
# plural — so when constant.json says "solo_worker": true, say so explicitly.
SOLO_RULE = """- NEVER INVENT COLLABORATION. The candidate works solo and end-to-end: he has spanned the design and engineering roles HIMSELF rather than handing off to anyone. Do not write "partnered with", "collaborated with", "worked alongside/cross-functionally with", "supported the X team", or any phrasing that implies a counterpart, unless a source bullet or note explicitly names that counterpart. When the JD asks for a collaborative skill the candidate has instead covered solo, say what he actually did ("built the design system AND the React/PHP components himself"), never that he partnered with someone who did the other half."""

SUMMARY_SOLO_RULE = """
- The summary describes what the candidate has BUILT and SHIPPED, solo. No claimed teamwork, no partnering, no "works closely with" phrasing."""

RESUME_CAPS = """

RESUME VOLUME — OVER-GENERATE, DO NOT SELF-CENSOR:
- Emit MORE content than fits on one page: a superset ranked by JD relevance. A downstream program compiles the PDF and trims from the END of each list until it fits exactly one page, so ORDER IS THE PRIORITY SIGNAL — strongest JD match first, weakest last.
- Never omit a role or a project to save space, and never shorten a list "to be safe". Fit is not your problem; ranking is. Under-supplying content leaves the page half-empty, which is worse than over-supplying it.
- EXPERIENCE: 5-6 entries — every role that plausibly supports the JD. The most recent role MUST stay FIRST; order the remaining roles by descending JD relevance. 3 bullets each.
- PROJECTS: 3-4 entries, ordered strictly by descending JD relevance. 2 bullets each.
- The entry and bullet counts above are TARGETS to hit, not ceilings to stay under. Hit them.
- Length ceilings (these ARE hard — a longer bullet wraps badly): ≤200 chars/experience bullet, ≤180 chars/project bullet.
- Skills: see SKILLS cap above (max 5 per group — that one is a real cap)."""

CV_GUIDANCE = """

MULTI-PAGE CV:
- No one-page limit. Keep all substantive roles and projects and full skill groups; favor completeness over brevity. Still drop empty filler bullets and keep the specialist framing and projected-impact rules."""


def portfolio_links(constant: dict) -> dict:
    """Normalized portfolio config.

    Returns {"visual": ..., "research": ...} when two distinct sites are configured,
    {"default": ...} for one, or {} for none. constant.json accepts either
    "portfolios": {"visual": "...", "research": "..."} or a plain
    "portfolios": "one-site.com"; content.json's own portfolio_link is the last resort.
    """
    raw = constant.get("portfolios")
    if isinstance(raw, str):
        raw = {"default": raw}
    if not isinstance(raw, dict) or not any(raw.values()):
        link = (constant.get("portfolio_link") or "").strip()
        return {"default": link} if link else {}
    visual, research = raw.get("visual"), raw.get("research")
    if visual and research and visual != research:
        return {"visual": visual, "research": research}
    single = raw.get("default") or visual or research or next(
        (v for v in raw.values() if v), "")
    return {"default": single} if single else {}


def location_for_jd(jd: str, constant: dict) -> str:
    """The applicant location shown when --location is absent.

    Some people list a different base depending on where the job is (a home country for
    postings there, a current city for everything else). constant.json can carry
    "location_rules": [{"match": "<regex>", "location": "<city>"}] — first match wins,
    and "location" is the fallback. An explicit CLI override is applied by main().
    """
    for rule in constant.get("location_rules") or []:
        pattern, location = rule.get("match"), rule.get("location")
        if not pattern or not location:
            continue
        try:
            matched = re.search(pattern, jd or "", re.I)
        except re.error as e:
            print(f"Warning   : ignoring bad location_rules regex {pattern!r}: {e}")
            continue
        if matched:
            return location
    return constant.get("location", "")


def _summary_bans(constant: dict) -> str:
    """Anchors the summary may not lead on.

    A number can be real, defensible, and still the wrong headline — a score that has
    become the only thing a resume says about you crowds out everything else. List those
    in constant.json as "banned_summary_anchors"; they stay usable in bullets.
    """
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
    """Everything that varies by applicant. One file to read when personalizing."""

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
            print("  See README.md → 'Your data files'.")
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
        # constant wins; content.json's own portfolio_link is the last resort.
        merged = {"portfolio_link": self._content.get("portfolio_link"), **self._constant}
        return portfolio_links(merged)

    def location_for_jd(self, jd: str) -> str:
        return location_for_jd(jd, self._constant)

    def system_prompt(self) -> str:
        """SYSTEM_PROMPT_BASE with the applicant-specific tokens resolved."""
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

    _NOTE_KEYS = {"experience": ("experience", "company"),
                  "project": ("projects", "title"),
                  "education": ("education", "institution")}

    def append_note(self, target: str, text: str) -> None:
        """Attach one note to an entry, or to the general bucket.

        Notes join with a newline, not a space: successive notes used to weld into
        one paragraph that could be neither read nor edited apart, and drafts.py
        reads notes as source text, so a blob would let a garbled claim satisfy the
        verbatim-evidence check.
        """
        text = (text or "").strip()
        if not text:
            return
        if target == "general":
            existing = (self._content.get("notes") or "").strip()
            self._content["notes"] = (existing + "\n" + text).strip() if existing else text
        else:
            kind, name, idx = _split_note_target(target)
            spec = self._NOTE_KEYS.get(kind)
            if not spec:
                raise ValueError(f"unknown note target {target!r}")
            key, match = spec
            matches = [e for e in self._content.get(key, []) if e.get(match) == name]
            if idx is not None and 0 <= idx < len(matches):
                entry = matches[idx]
            elif matches:
                # The documented fallback for a plain, colliding target (e.g. a
                # model-produced "experience:Amazon" with two Amazon entries): the
                # first matching entry, same as before disambiguation existed.
                entry = matches[0]
            else:
                raise ValueError(f"no {kind} entry named {name!r}")
            existing = (entry.get("notes") or "").strip()
            entry["notes"] = (existing + "\n" + text).strip() if existing else text
        self._write()

    def _write(self) -> None:
        if self._root is None:
            return
        (self._root / "content.json").write_text(
            json.dumps(self._content, indent=2, ensure_ascii=False) + "\n")
