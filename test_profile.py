# test_profile.py
"""Profile owns every applicant-specific decision. One file to read when personalizing."""
import json
import pytest
from pathlib import Path
from profile import Profile, portfolio_links

CONSTANT_TWO = {
    "name": "Alex Rivera", "email": "a@x.com", "phone": "+1 555", "linkedin": "in/alex",
    "location": "Seattle, WA",
    "portfolios": {"visual": "visual.example.com", "research": "example.com"},
    "location_rules": [{"match": r"\bIndia\b", "location": "Mumbai, India"}],
    "solo_worker": True,
    "banned_summary_anchors": ["95 SUS"],
    "skills_title": "Skills", "doc_basename": "Alex_Rivera", "output_dir": "",
    "backend": "openclaw",
}
CONTENT = {
    "portfolio_link": "example.com", "summary": "s",
    "experience": [{"company": "Northgate", "title": "PD", "dates": "2023--", "location": "R", "notes": "n", "bullets": ["b"]}],
    "projects": [{"title": "Transit Board", "notes": "n", "bullets": ["b"]}],
    "skills": [{"label": "Research Skills", "entries": ["Interviews"]}],
    "education": [{"institution": "UW", "degree": "B.Des", "gpa": "3.8", "start": "2015", "end": "2019", "location": "S"}],
    "certifications": [],
}


def prof(constant=None, content=None):
    return Profile.from_dicts(constant or CONSTANT_TWO, content or CONTENT)


def _write(root, content):
    (root / "constant.json").write_text(json.dumps(CONSTANT_TWO))
    (root / "content.json").write_text(json.dumps(content, indent=2))


def test_identity_is_only_the_header_fields():
    assert prof().identity == {"name": "Alex Rivera", "email": "a@x.com", "phone": "+1 555", "linkedin": "in/alex"}


def test_portfolio_links_two_site_shape():
    assert prof().portfolio_links() == {"visual": "visual.example.com", "research": "example.com"}


def test_portfolio_links_single_string():
    p = prof({**CONSTANT_TWO, "portfolios": "only.example.com"})
    assert p.portfolio_links() == {"default": "only.example.com"}


def test_portfolio_links_none_falls_back_to_content_link():
    # constant has no portfolios; content.json's own portfolio_link is the last resort.
    c = {k: v for k, v in CONSTANT_TWO.items() if k != "portfolios"}
    assert "portfolio_link" not in c
    assert CONTENT["portfolio_link"] == "example.com"
    assert prof(c).portfolio_links() == {"default": "example.com"}


def test_location_for_jd_rule_match_then_fallback():
    p = prof()
    assert p.location_for_jd("role based in India") == "Mumbai, India"
    assert p.location_for_jd("role in Berlin") == "Seattle, WA"


def test_location_for_jd_bad_regex_is_ignored(capsys):
    p = prof({**CONSTANT_TWO, "location_rules": [{"match": "(", "location": "X"}]})
    assert p.location_for_jd("anything") == "Seattle, WA"


def test_skill_groups_and_optional_sections():
    p = prof()
    assert p.skill_groups == ["Research Skills"]
    assert p.optional_sections == ["certifications"]  # present key, even though empty


def test_system_prompt_fills_every_token():
    sp = prof().system_prompt()
    assert "%%" not in sp
    assert "visual.example.com" in sp and "example.com" in sp
    assert "NEVER INVENT COLLABORATION" in sp            # solo rule on
    assert "95 SUS" in sp                                # banned anchor injected


def test_system_prompt_drops_solo_rule_when_not_solo():
    sp = prof({**CONSTANT_TWO, "solo_worker": False}).system_prompt()
    assert "%%" not in sp
    assert "NEVER INVENT COLLABORATION" not in sp


def test_append_note_to_existing_experience(tmp_path):
    (tmp_path / "constant.json").write_text(json.dumps(CONSTANT_TWO))
    (tmp_path / "content.json").write_text(json.dumps(CONTENT, indent=2))
    p = Profile.load(tmp_path)
    p.append_note("experience:Northgate", "Shipped v2 to 40k users.")
    reloaded = json.loads((tmp_path / "content.json").read_text())
    assert reloaded["experience"][0]["notes"].endswith("Shipped v2 to 40k users.")
    assert (tmp_path / "content.json").read_text().endswith("\n")


def test_append_note_general_bucket(tmp_path):
    (tmp_path / "constant.json").write_text(json.dumps(CONSTANT_TWO))
    (tmp_path / "content.json").write_text(json.dumps(CONTENT, indent=2))
    p = Profile.load(tmp_path)
    p.append_note("general", "Open to relocation.")
    assert json.loads((tmp_path / "content.json").read_text())["notes"] == "Open to relocation."


def test_append_note_unknown_target_raises(tmp_path):
    (tmp_path / "constant.json").write_text(json.dumps(CONSTANT_TWO))
    (tmp_path / "content.json").write_text(json.dumps(CONTENT, indent=2))
    p = Profile.load(tmp_path)
    with pytest.raises(ValueError):
        p.append_note("project:Does Not Exist", "x")


def test_append_note_joins_with_a_newline_not_a_space(tmp_path):
    # Space-joining welded successive notes into one unreadable paragraph, and
    # drafts.py reads notes as source text -- a blob lets a garbled claim pass
    # the verbatim-substring gate because the blob really does contain it.
    _write(tmp_path, {"experience": [{"company": "Acme", "bullets": [], "notes": "First note."}]})
    p = Profile.load(tmp_path)
    p.append_note("experience:Acme", "Second note.")
    assert p.content["experience"][0]["notes"] == "First note.\nSecond note."


def test_append_note_to_education(tmp_path):
    _write(tmp_path, {"education": [{"institution": "UW", "degree": "BFA"}]})
    p = Profile.load(tmp_path)
    p.append_note("education:UW", "Thesis on transit wayfinding.")
    assert p.content["education"][0]["notes"] == "Thesis on transit wayfinding."


def test_append_note_unknown_education_raises(tmp_path):
    _write(tmp_path, {"education": [{"institution": "UW", "degree": "BFA"}]})
    p = Profile.load(tmp_path)
    with pytest.raises(ValueError):
        p.append_note("education:Nowhere", "x")


def test_load_missing_file_exits(tmp_path):
    with pytest.raises(SystemExit):
        Profile.load(tmp_path)
