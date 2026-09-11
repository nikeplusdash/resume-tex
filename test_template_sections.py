import copy

import tailor

BASE = {
    "name": "Alex Rivera", "email": "a@x.com", "phone": "+1", "linkedin": "in/a",
    "location": "Seattle, WA", "portfolio_link": "example.com", "summary": "s",
    "skills_title": "Skills", "projects_title": "Relevant Projects",
    "experience": [{"company": "N", "title": "PD", "dates": "2023--", "location": "S", "bullets": ["b1"]}],
    "projects": [{"title": "P", "bullets": ["b1"]}],
    "skills": [{"label": "Design Skills", "entries": ["Figma"]}],
}


def test_no_optional_sections_no_headings():
    tex = tailor.render_tex(BASE)
    for h in ("Certifications", "Awards", "Publications", "Languages"):
        assert h not in tex


def test_each_optional_section_renders_when_present():
    data = copy.deepcopy(BASE)
    data["certifications"] = [{"name": "CPACC", "issuer": "IAAP", "date": "2024"}]
    data["awards"] = [{"title": "Best Poster", "body": "CHI", "year": "2023"}]
    data["publications"] = [{"title": "On Boards", "venue": "CHI EA", "year": "2022"}]
    data["languages"] = [{"language": "English", "proficiency": "native"},
                         {"language": "French", "proficiency": "working"}]
    tex = tailor.render_tex(data)
    assert "Certifications" in tex and "CPACC" in tex and "IAAP" in tex
    assert "Awards" in tex and "Best Poster" in tex
    assert "Publications" in tex and "On Boards" in tex
    assert "Languages" in tex and "English (native)" in tex and "French (working)" in tex


def test_trimmer_sheds_languages_before_publications_before_awards_before_certs():
    """Tier-2 trimming must shed whole optional sections, least valuable first:
    languages, then publications, then awards, then certifications."""
    data = copy.deepcopy(BASE)
    data["certifications"] = [{"name": "C", "issuer": "I", "date": "1"}]
    data["awards"] = [{"title": "A", "body": "B", "year": "1"}]
    data["publications"] = [{"title": "P", "venue": "V", "year": "1"}]
    data["languages"] = [{"language": "L", "proficiency": "p"}]

    optional = ("languages", "publications", "awards", "certifications")
    shed_order = []
    for _ in range(len(optional)):
        present_before = {k for k in optional if k in data}
        changed = tailor._shrink_one_step(data)
        assert changed, "expected a trim step to fire"
        gone = present_before - {k for k in optional if k in data}
        assert len(gone) == 1, f"expected exactly one section shed per call, got {gone}"
        shed_order.append(next(iter(gone)))

    assert shed_order == ["languages", "publications", "awards", "certifications"]
    assert not any(k in data for k in optional)
