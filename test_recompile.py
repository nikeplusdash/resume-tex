"""Tests for recompile.py — re-render a saved application from its edited JSON.

Ruling 2: `root` only locates constant.json + content.json (via Profile.load).
LaTeX rendering stays inside `tailor` (its own ROOT / template.tex), so these tests
never write a template into `root` and never run real pdflatex — the compile
functions are mocked on the `recompile` module.
"""
import json

import pytest

import llm
import recompile
import ui

CONSTANT = {
    "name": "Alex Rivera", "email": "a@x.com", "phone": "+1", "linkedin": "in/a",
    "location": "Seattle, WA", "portfolios": "example.com", "doc_basename": "Alex_Rivera",
}

# A master content.json is required by Profile.load even though recompile reads the
# folder's own content.<mode>.json for the actual data.
MASTER_CONTENT = {
    "skills": [{"label": "Design Skills", "entries": ["Figma"]}],
    "experience": [{"company": "N", "title": "PD", "dates": "2020--", "location": "S",
                    "bullets": ["x"]}],
    "projects": [{"title": "Old", "bullets": ["y"]}],
}

FOLDER_JSON = {
    "company_name": "Acme", "job_title": "Product Designer",
    "portfolio_link": "example.com", "summary": "s",
    "experience": [{"company": "N", "title": "PD", "dates": "2023--", "location": "S",
                    "bullets": ["b1", "b2"]}],
    "projects": [{"title": "P", "bullets": ["b1"]}],
    "skills": [{"label": "Design Skills", "entries": ["Figma"]}],
}


def _make_root(tmp_path):
    root = tmp_path / "pkg"
    root.mkdir()
    (root / "constant.json").write_text(json.dumps(CONSTANT))
    (root / "content.json").write_text(json.dumps(MASTER_CONTENT))
    return root


def _no_model(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("recompile made a model call")

    monkeypatch.setattr(llm, "complete_schema", boom)
    monkeypatch.setattr(llm, "complete_text", boom)


def _capture_single_page(captured):
    def fake(data, dest, filename, **kw):
        captured["data"] = data
        captured["dest"] = dest
        captured["filename"] = filename
        return 1, data
    return fake


def _capture_to_dir(captured):
    def fake(data, dest, filename="resume.pdf"):
        captured["data"] = data
        captured["dest"] = dest
        captured["filename"] = filename
        return 1
    return fake


def test_recompile_folder_resume_merges_identity_no_model_call(tmp_path, monkeypatch):
    root = _make_root(tmp_path)
    app = tmp_path / "app"
    app.mkdir()
    (app / "content.resume.json").write_text(json.dumps(FOLDER_JSON, indent=2))

    captured = {}
    monkeypatch.setattr(recompile, "compile_single_page", _capture_single_page(captured))
    monkeypatch.setattr(
        recompile, "compile_to_dir",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("resume must not use compile_to_dir")),
    )
    _no_model(monkeypatch)

    pdf = recompile.recompile_folder(app, root=root)

    data = captured["data"]
    assert data["name"] == "Alex Rivera"
    assert data["email"] == "a@x.com"
    assert data["location"] == "Seattle, WA"          # folder JSON has none -> profile.location
    assert data["skills_title"] == "Skills"
    assert data["projects_title"] == "Relevant Projects"
    assert data["experience"][0]["bullets"] == ["b1", "b2"]
    assert data["summary"] == "s"
    # reference-only keys are stripped before rendering
    assert "company_name" not in data
    assert "job_title" not in data
    assert captured["filename"] == "Alex_Rivera_Resume.pdf"
    assert pdf == app / "Alex_Rivera_Resume.pdf"


def test_recompile_folder_uses_folder_location_override(tmp_path, monkeypatch):
    root = _make_root(tmp_path)
    app = tmp_path / "app"
    app.mkdir()
    folder = dict(FOLDER_JSON, location="Berlin, DE")
    (app / "content.resume.json").write_text(json.dumps(folder))

    captured = {}
    monkeypatch.setattr(recompile, "compile_single_page", _capture_single_page(captured))
    _no_model(monkeypatch)

    recompile.recompile_folder(app, root=root)
    assert captured["data"]["location"] == "Berlin, DE"


def test_recompile_folder_cv_mode_uses_compile_to_dir(tmp_path, monkeypatch):
    root = _make_root(tmp_path)
    app = tmp_path / "app"
    app.mkdir()
    (app / "content.cv.json").write_text(json.dumps(FOLDER_JSON))

    captured = {}
    monkeypatch.setattr(recompile, "compile_to_dir", _capture_to_dir(captured))
    monkeypatch.setattr(recompile, "compile_single_page",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("cv must not use compile_single_page")))
    _no_model(monkeypatch)

    pdf = recompile.recompile_folder(app, root=root, mode="cv")
    assert captured["data"]["projects_title"] == "Personal Projects"
    assert captured["filename"] == "Alex_Rivera_CV.pdf"
    assert pdf == app / "Alex_Rivera_CV.pdf"


def test_missing_folder_json_exits(tmp_path, monkeypatch):
    root = _make_root(tmp_path)
    _no_model(monkeypatch)
    with pytest.raises(SystemExit):
        recompile.recompile_folder(tmp_path / "empty", root=root)


def test_recompile_module_does_not_import_llm():
    # No model call anywhere means the module never even pulls in llm.
    import sys
    assert "recompile" in sys.modules
    assert not hasattr(recompile, "llm")


def test_menu_merges_from_master_content(tmp_path, monkeypatch):
    root = tmp_path / "pkg"
    root.mkdir()
    (root / "constant.json").write_text(json.dumps(CONSTANT))
    (root / "content.json").write_text(json.dumps({
        **FOLDER_JSON,
        "certifications": [{"name": "CPACC", "issuer": "IAAP", "date": "2024"}],
        "languages": [{"language": "English", "proficiency": "native"}],
    }, indent=2))
    app = tmp_path / "app"
    app.mkdir()
    (app / "content.resume.json").write_text(json.dumps(FOLDER_JSON, indent=2))

    import recompile_menu
    monkeypatch.setattr(ui, "checkbox", lambda *a, **k: ["Certifications", "Languages"])
    monkeypatch.setattr(ui, "editor", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("editor should not be prompted when master has the section")))

    recompile_menu.run_menu(app, root=root, mode="resume")

    merged = json.loads((app / "content.resume.json").read_text())
    assert merged["certifications"][0]["name"] == "CPACC"
    assert merged["languages"][0]["language"] == "English"


def test_menu_prompts_when_master_lacks_section(tmp_path, monkeypatch):
    root = tmp_path / "pkg"
    root.mkdir()
    (root / "constant.json").write_text(json.dumps(CONSTANT))
    (root / "content.json").write_text(json.dumps(FOLDER_JSON, indent=2))
    app = tmp_path / "app"
    app.mkdir()
    (app / "content.resume.json").write_text(json.dumps(FOLDER_JSON, indent=2))

    import recompile_menu
    monkeypatch.setattr(ui, "checkbox", lambda *a, **k: ["Awards"])
    monkeypatch.setattr(ui, "editor", lambda *a, **k: "Jury Award | CHI | 2023")

    recompile_menu.run_menu(app, root=root, mode="resume")

    merged = json.loads((app / "content.resume.json").read_text())
    assert merged["awards"] == [{"title": "Jury Award", "body": "CHI", "year": "2023"}]


def test_menu_education_merges_with_coursework(tmp_path, monkeypatch):
    root = tmp_path / "pkg"
    root.mkdir()
    (root / "constant.json").write_text(json.dumps(CONSTANT))
    edu = [{"institution": "UW", "degree": "B.Des", "gpa": "3.8",
            "start": "2015", "end": "2019", "location": "S"}]
    (root / "content.json").write_text(json.dumps({**FOLDER_JSON, "education": edu}, indent=2))
    (root / "coursework.json").write_text(json.dumps({"UW": ["HCI", "Typography"]}))
    app = tmp_path / "app"
    app.mkdir()
    (app / "content.resume.json").write_text(json.dumps(FOLDER_JSON, indent=2))

    import recompile_menu
    monkeypatch.setattr(ui, "checkbox", lambda *a, **k: ["Education"])
    monkeypatch.setattr(ui, "editor", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("editor should not be prompted for Education")))

    recompile_menu.run_menu(app, root=root, mode="resume")

    merged = json.loads((app / "content.resume.json").read_text())
    assert merged["education"][0]["institution"] == "UW"
    assert merged["education"][0]["coursework"] == ["HCI", "Typography"]


def test_menu_education_skipped_when_master_lacks_it(tmp_path, monkeypatch, capsys):
    root = tmp_path / "pkg"
    root.mkdir()
    (root / "constant.json").write_text(json.dumps(CONSTANT))
    (root / "content.json").write_text(json.dumps(FOLDER_JSON, indent=2))  # no education, no coursework
    app = tmp_path / "app"
    app.mkdir()
    (app / "content.resume.json").write_text(json.dumps(FOLDER_JSON, indent=2))

    import recompile_menu
    monkeypatch.setattr(ui, "checkbox", lambda *a, **k: ["Education"])

    recompile_menu.run_menu(app, root=root, mode="resume")

    assert "skipped Education" in capsys.readouterr().out
    merged = json.loads((app / "content.resume.json").read_text())
    assert "education" not in merged
    assert merged == FOLDER_JSON


def test_menu_no_selection_is_noop(tmp_path, monkeypatch):
    root = tmp_path / "pkg"
    root.mkdir()
    (root / "constant.json").write_text(json.dumps(CONSTANT))
    (root / "content.json").write_text(json.dumps(FOLDER_JSON, indent=2))
    app = tmp_path / "app"
    app.mkdir()
    (app / "content.resume.json").write_text(json.dumps(FOLDER_JSON, indent=2))

    import recompile_menu
    monkeypatch.setattr(ui, "checkbox", lambda *a, **k: [])

    recompile_menu.run_menu(app, root=root, mode="resume")

    assert json.loads((app / "content.resume.json").read_text()) == FOLDER_JSON


# Tests for recompile.recent_apps()
import os
import time


def _app(base, company, role, *files):
    d = base / company / role
    d.mkdir(parents=True)
    for f in files:
        (d / f).write_text("{}")
    return d


def test_recent_apps_finds_only_recompilable_folders(tmp_path):
    _app(tmp_path, "Acme", "PD", "content.resume.json")
    _app(tmp_path, "Globex", "Lead", "content.resume.json", "content.cv.json")
    _app(tmp_path, "NoJson", "Role", "notes.txt")            # not recompilable
    apps = recompile.recent_apps(tmp_path)
    refs = {a["ref"] for a in apps}
    assert refs == {"Acme · PD", "Globex · Lead"}
    globex = next(a for a in apps if a["ref"] == "Globex · Lead")
    assert sorted(globex["modes"]) == ["cv", "resume"]
    acme = next(a for a in apps if a["ref"] == "Acme · PD")
    assert acme["modes"] == ["resume"]


def test_recent_apps_sorted_newest_first(tmp_path):
    a = _app(tmp_path, "Old", "R", "content.resume.json")
    b = _app(tmp_path, "New", "R", "content.resume.json")
    old = time.time() - 10_000
    os.utime(a / "content.resume.json", (old, old))
    apps = recompile.recent_apps(tmp_path)
    assert [x["ref"] for x in apps] == ["New · R", "Old · R"]


def test_recent_apps_missing_base_is_empty(tmp_path):
    assert recompile.recent_apps(tmp_path / "nope") == []


def test_recent_apps_underscores_become_spaces(tmp_path):
    _app(tmp_path, "Acme_Inc", "Senior_Product_Designer", "content.resume.json")
    assert recompile.recent_apps(tmp_path)[0]["ref"] == "Acme Inc · Senior Product Designer"
