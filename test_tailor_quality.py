"""Truthfulness and match-report regression tests for the resume tailor."""

import tailor
from profile import Profile


SOURCE = {
    "experience": [{"title": "Product Designer"}],
    "skills": [
        {"label": "Design Skills", "entries": ["Figma", "Design Systems"]},
        {"label": "Technical Skills", "entries": ["JavaScript"]},
    ],
}


def test_match_score_is_recomputed_from_statuses():
    analysis = {
        "overall_score": 99,
        "requirements": [
            {"weight": 50, "status": "direct"},
            {"weight": 30, "status": "adjacent"},
            {"weight": 20, "status": "missing"},
        ],
    }
    assert tailor.normalize_match_analysis(analysis)["overall_score"] == 65


def test_match_score_normalizes_weights_that_do_not_total_100():
    analysis = {
        "requirements": [
            {"weight": 4, "status": "direct"},
            {"weight": 1, "status": "missing"},
        ],
    }
    assert tailor.normalize_match_analysis(analysis)["overall_score"] == 80


def test_integrity_flags_unearned_seniority_and_renamed_skills():
    tailored = {
        "summary": "Sr Product Designer who built AI-assisted products.",
        "experience": [],
        "projects": [],
        "skills": [
            {"label": "Design Skills", "entries": ["Figma", "Product Thinking"]},
        ],
    }
    findings = tailor.check_integrity(tailored, SOURCE)
    assert any("seniority" in finding for finding in findings)
    assert any("no evidence-backed inference record" in finding for finding in findings)
    assert any("AI tool usage" in finding for finding in findings)


def test_integrity_accepts_exact_source_skills():
    tailored = {
        "summary": "Product Designer focused on accessible systems.",
        "experience": [],
        "projects": [],
        "skills": [
            {"label": "Design Skills", "entries": ["Figma", "Design Systems"]},
            {"label": "Technical Skills", "entries": ["JavaScript"]},
        ],
    }
    assert not [f for f in tailor.check_integrity(tailored, SOURCE) if f.startswith("skills/")]


def test_integrity_accepts_jd_skill_with_exact_source_quote():
    source = {
        **SOURCE,
        "skills": SOURCE["skills"] + [
            {"label": "Research Skills", "entries": ["User Flows"]},
        ],
        "projects": [{
            "title": "Workflow redesign",
            "notes": "Mapped publishing workflows and redesigned role-based task flows.",
        }],
    }
    tailored = {
        "summary": "Product Designer focused on accessible systems.",
        "experience": [],
        "projects": [],
        "skills": [{"label": "Research Skills", "entries": ["Task Flows"]}],
    }
    analysis = {
        "inferred_skills": [{
            "skill": "Task Flows",
            "group": "Research Skills",
            "kind": "method",
            "basis": "adjacent",
            "jd_term": "User Journey Mapping & Task Flows",
            "source_item": "Workflow redesign",
            "source_quote": "Mapped publishing workflows and redesigned role-based task flows.",
        }],
    }
    findings = tailor.check_integrity(tailored, source, analysis)
    assert not [f for f in findings if f.startswith("skills/")]


def test_integrity_rejects_inferred_skill_without_exact_quote():
    source = {
        **SOURCE,
        "skills": SOURCE["skills"] + [
            {"label": "Research Skills", "entries": ["User Flows"]},
        ],
    }
    tailored = {
        "summary": "Product Designer focused on accessible systems.",
        "experience": [],
        "projects": [],
        "skills": [{"label": "Research Skills", "entries": ["Task Flows"]}],
    }
    analysis = {
        "inferred_skills": [{
            "skill": "Task Flows",
            "group": "Research Skills",
            "kind": "method",
            "basis": "adjacent",
            "jd_term": "Task Flows",
            "source_item": "Unknown",
            "source_quote": "This sentence does not exist in the source.",
        }],
    }
    findings = tailor.check_integrity(tailored, source, analysis)
    assert any("lacks an exact source quote" in finding for finding in findings)
    assert any("no evidence-backed inference record" in finding for finding in findings)


def test_integrity_requires_direct_exact_evidence_for_new_technology():
    source = {
        **SOURCE,
        "projects": [{"title": "Prototype", "notes": "Built the prototype with JavaScript."}],
    }
    tailored = {
        "summary": "Product Designer focused on accessible systems.",
        "experience": [],
        "projects": [],
        "skills": [{"label": "Technical Skills", "entries": ["TypeScript"]}],
    }
    analysis = {
        "inferred_skills": [{
            "skill": "TypeScript",
            "group": "Technical Skills",
            "kind": "tool_or_technology",
            "basis": "adjacent",
            "jd_term": "TypeScript",
            "source_item": "Prototype",
            "source_quote": "Built the prototype with JavaScript.",
        }],
    }
    findings = tailor.check_integrity(tailored, source, analysis)
    assert any("cannot be inferred by adjacency" in finding for finding in findings)
    assert any("no evidence-backed inference record" in finding for finding in findings)


def test_integrity_accepts_new_technology_named_in_source_quote():
    source = {
        **SOURCE,
        "projects": [{"title": "Prototype", "notes": "Built the prototype with TypeScript."}],
    }
    tailored = {
        "summary": "Product Designer focused on accessible systems.",
        "experience": [],
        "projects": [],
        "skills": [{"label": "Technical Skills", "entries": ["TypeScript"]}],
    }
    analysis = {
        "inferred_skills": [{
            "skill": "TypeScript",
            "group": "Technical Skills",
            "kind": "tool_or_technology",
            "basis": "direct",
            "jd_term": "TypeScript",
            "source_item": "Prototype",
            "source_quote": "Built the prototype with TypeScript.",
        }],
    }
    findings = tailor.check_integrity(tailored, source, analysis)
    assert not [finding for finding in findings if finding.startswith("skills/")]


PROFILE = {
    "location": "Seattle, WA",
    "location_rules": [{"match": r"\bIndia\b|\bBangalore\b|\bINR\b", "location": "Mumbai, India"}],
}


def test_a_matching_location_rule_wins():
    assert tailor.location_for_jd("Product Designer, Bangalore onsite", PROFILE) == "Mumbai, India"
    assert tailor.location_for_jd("Role based in India", PROFILE) == "Mumbai, India"
    assert tailor.location_for_jd("Compensation: INR 30 lakh", PROFILE) == "Mumbai, India"


def test_no_matching_rule_falls_back_to_the_base_location():
    assert tailor.location_for_jd("Designer, London", PROFILE) == "Seattle, WA"
    assert tailor.location_for_jd("Remote", PROFILE) == "Seattle, WA"
    assert tailor.location_for_jd("Remote", {"location": "Berlin"}) == "Berlin"


def test_a_broken_rule_regex_does_not_crash_the_run():
    profile = {"location": "Seattle, WA", "location_rules": [{"match": "((", "location": "X"}]}
    assert tailor.location_for_jd("anything", profile) == "Seattle, WA"


def test_prompt_forbids_using_advertised_seniority_as_candidate_identity():
    assert "NEVER use that seniority as the candidate's title" in tailor.SYSTEM_PROMPT_BASE
    assert "DISTINGUISH AI PRODUCT DESIGN FROM AI-ASSISTED WORK" in tailor.SYSTEM_PROMPT_BASE
    assert "Prefer the JD's exact, standard skill term" in tailor.SYSTEM_PROMPT_BASE
    assert "copied VERBATIM from a source bullet or note" in tailor.SYSTEM_PROMPT_BASE
    assert "Adjacent inference is allowed only for capability, method, or domain" in tailor.SYSTEM_PROMPT_BASE
    assert "Never infer one technology from another" in tailor.SYSTEM_PROMPT_BASE
    assert 'workflow analysis may support "Task Flows"' not in tailor.SYSTEM_PROMPT_BASE
    assert 'does not support "Cross-functional Collaboration"' not in tailor.SYSTEM_PROMPT_BASE


# ── Config-driven prompt assembly ─────────────────────────────────────────────

BASE_CONTENT = {"skills": [{"label": "Design Skills", "entries": ["Figma"]}]}


def _system_prompt(constant=None, content=None):
    return Profile.from_dicts(constant or {}, content or BASE_CONTENT).system_prompt()


def test_two_portfolios_produce_the_choice_rule():
    prompt = _system_prompt(
        {"portfolios": {"visual": "v.example.com", "research": "r.example.com"}})
    assert "v.example.com" in prompt and "r.example.com" in prompt
    assert "differ by EMPHASIS" in prompt


def test_one_portfolio_produces_a_fixed_link_rule():
    prompt = _system_prompt({"portfolios": "only.example.com"})
    assert 'set it to exactly "only.example.com"' in prompt
    assert "differ by EMPHASIS" not in prompt


def test_the_solo_rule_is_opt_in():
    assert "NEVER INVENT COLLABORATION" not in _system_prompt()
    assert "NEVER INVENT COLLABORATION" in _system_prompt({"solo_worker": True})


def test_banned_summary_anchors_reach_the_prompt():
    assert "BANNED IN THE SUMMARY" not in _system_prompt()
    prompt = _system_prompt({"banned_summary_anchors": ["the 95 SUS score"]})
    assert "BANNED IN THE SUMMARY: the 95 SUS score" in prompt


def test_skill_group_labels_come_from_the_content_file():
    prompt = _system_prompt(
        content={"skills": [{"label": "Engineering"}, {"label": "Languages"}]})
    assert '"Engineering", "Languages"' in prompt
    assert "%%" not in prompt          # every token resolved


def test_no_token_survives_into_a_finished_prompt():
    for constant in ({}, {"solo_worker": True, "portfolios": "a.example.com"}):
        assert "%%" not in _system_prompt(constant)


# ── Integrity lint is config-driven too ───────────────────────────────────────

def test_collaboration_is_only_flagged_for_a_solo_candidate():
    tailored = {"summary": "Designer.", "skills": [],
                "experience": [{"company": "Acme", "bullets": ["Partnered with the data team."]}],
                "projects": []}
    source = {"experience": [{"title": "Designer"}], "skills": []}
    assert not any("collaboration" in f
                   for f in tailor.check_integrity(tailored, source, {},
                                                   profile=Profile.from_dicts({}, {})))
    assert any("collaboration" in f
               for f in tailor.check_integrity(tailored, source, {},
                                               profile=Profile.from_dicts({"solo_worker": True}, {})))


def test_a_banned_summary_anchor_is_flagged():
    tailored = {"summary": "Designer with a 95 SUS score.", "skills": [],
                "experience": [], "projects": []}
    source = {"experience": [{"title": "Designer"}], "skills": []}
    findings = tailor.check_integrity(tailored, source, {},
                                      profile=Profile.from_dicts({"banned_summary_anchors": ["95 SUS"]}, {}))
    assert any("banned as a headline" in f for f in findings)


# ── Summary + bullet-form richness (mechanism, before/after, no fabrication) ──

def test_summary_rule_asks_for_mechanism_alongside_a_metric():
    assert ("pair it with the mechanism that produced it — the stack, method, or system"
            in tailor.SYSTEM_PROMPT_BASE)


def test_summary_rule_states_the_no_defensible_metric_fallback():
    assert ("When the source has no defensible metric, use a named artifact or a "
            "concrete shipped outcome in its place instead — never manufacture a "
            "figure to fill the slot" in tailor.SYSTEM_PROMPT_BASE)


def test_bullet_form_includes_a_mechanism_slot():
    assert ("Bullet form: [strong verb] + [what, named when the source names it] + "
            "[how — the method, system, or stack] + [concrete or quantified outcome]"
            in tailor.SYSTEM_PROMPT_BASE)


def test_before_after_preference_carries_its_no_invented_endpoint_gate():
    assert ("Prefer the before→after form when the source contains both endpoints"
            in tailor.SYSTEM_PROMPT_BASE)
    assert ("Never derive an endpoint that is not in the source"
            in tailor.SYSTEM_PROMPT_BASE)


def test_round_number_ban_is_present():
    assert ("Carry the source's figure exactly as recorded; never round it to a "
            "cleaner-sounding number" in tailor.SYSTEM_PROMPT_BASE)


def test_prompt_tells_the_model_to_adopt_the_jd_action_verbs():
    assert "ACTIVELY ADOPT THE JD'S ACTION VERBS" in tailor.SYSTEM_PROMPT_BASE
    assert "main lever for raising keyword match" in tailor.SYSTEM_PROMPT_BASE
    assert "changes WORDS ONLY" in tailor.SYSTEM_PROMPT_BASE


def test_shrink_exhausts_every_fine_cut_before_dropping_a_whole_entry():
    data = {
        "experience": [{"company": f"C{i}", "bullets": ["a", "b", "c"]} for i in range(4)],
        "projects": [{"title": f"P{i}", "bullets": ["x", "y"]} for i in range(2)],
        "skills": [{"label": "S", "entries": ["1", "2", "3", "4", "5", "6"]}],
        "certifications": [{"name": "AWS"}],
    }
    exp0, proj0 = len(data["experience"]), len(data["projects"])
    first_entry_drop = None
    for step in range(60):
        if not tailor._shrink_one_step(data):
            break
        if first_entry_drop is None and (len(data["experience"]) < exp0
                                         or len(data["projects"]) < proj0):
            first_entry_drop = step
            # by the time the FIRST whole entry is dropped, every fine cut is spent
            assert all(len(e["bullets"]) <= 2 for e in data["experience"])
            assert all(len(p["bullets"]) <= 1 for p in data["projects"])
            assert "certifications" not in data
            assert len(data["skills"][0]["entries"]) <= 4
    assert first_entry_drop is not None and first_entry_drop >= 8   # many fine cuts first


def test_shrink_drops_a_surplus_project_only_after_all_fine_cuts():
    data = {
        "experience": [{"company": "C0", "bullets": ["a", "b"]}],   # already at floor
        "projects": [{"title": f"P{i}", "bullets": ["x"]} for i in range(3)],  # 3 = surplus
        "skills": [],
    }
    assert tailor._shrink_one_step(data) is True
    assert len(data["projects"]) == 2                    # the surplus (3rd) project popped


def test_bullet_form_requires_a_purpose_clause_in_jd_words():
    p = tailor.SYSTEM_PROMPT_BASE
    assert "PURPOSE CLAUSE" in p and "to improve usability and scalability" in p
    assert "REQUIRED on every bullet that has no metric" in p
    assert "never invent a mechanism, artifact name, or purpose" in p


def test_prompt_carries_the_jd_key_terms_checklist_rule():
    p = tailor.SYSTEM_PROMPT_BASE
    assert "JD KEY TERMS CHECKLIST" in p and "VERBATIM" in p
    assert "A term with no honest home stays out" in p


def test_summary_rule_asks_for_keyword_density_around_the_anchor():
    p = tailor.SYSTEM_PROMPT_BASE
    assert "KEYWORD DENSITY" in p and "at least 5 of the JD KEY TERMS" in p
    assert "up to 3 JD-listed tools" in p


def test_prompt_asks_for_per_entry_tailoring_notes():
    assert "tailoring_notes" in tailor.SYSTEM_PROMPT_BASE and "change log" in tailor.SYSTEM_PROMPT_BASE
