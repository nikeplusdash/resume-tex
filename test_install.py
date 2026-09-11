# test_install.py
import json
import subprocess

import pytest

import install
import merge


def test_check_dependencies_reports_missing_pdflatex(monkeypatch):
    monkeypatch.setattr(install.shutil, "which", lambda n: None if n == "pdflatex" else "/usr/bin/" + n)
    probs = install.check_dependencies()
    assert any("pdflatex" in p or "LaTeX" in p for p in probs)


def test_setup_backend_none_installed_exits(monkeypatch):
    monkeypatch.setattr(install.llm, "available_backends", lambda which=None: [])
    with pytest.raises(SystemExit):
        install.setup_backend(which=lambda n: None)


def _no_verify(monkeypatch, problem=""):
    """Stub the connectivity probe: it would otherwise spawn a real CLI."""
    monkeypatch.setattr(install, "verify_backend", lambda backend, **k: problem)


def test_setup_backend_openclaw_runs_connect(monkeypatch):
    monkeypatch.setattr(install.llm, "available_backends", lambda which=None: ["openclaw"])
    calls = []
    monkeypatch.setattr(install.ui, "select", lambda *a, **k: "openai")
    monkeypatch.setattr(install.ui, "confirm", lambda *a, **k: True)
    monkeypatch.setattr(install.subprocess, "run", lambda *a, **k: calls.append(a[0]) or subprocess.CompletedProcess(a[0], 0))
    _no_verify(monkeypatch)
    b = install.setup_backend(which=lambda n: "/bin/openclaw" if n == "openclaw" else None)
    assert b == "openclaw"
    assert any("openclaw" in c[0] for c in calls)


def test_auth_argv_shapes():
    assert install.auth_argv("openclaw", "claude") == [
        "openclaw", "models", "auth", "login", "--provider", "claude", "--set-default"]
    assert install.auth_argv("claude") == ["claude"]


def test_a_failed_login_reports_and_continues(monkeypatch, capsys):
    # A fresh OpenClaw has no provider plugins; `models auth login` exits non-zero.
    # That must not end setup and lose every answer already given.
    monkeypatch.setattr(install.ui, "select", lambda *a, **k: "claude")
    monkeypatch.setattr(install.ui, "confirm", lambda *a, **k: True)
    ok = install.connect_backend(
        "openclaw", run=lambda argv, **k: subprocess.CompletedProcess(argv, 1))
    out = capsys.readouterr().out
    assert ok is False
    assert "exited 1" in out and "plugin" in out.lower()


def test_declining_a_login_prints_the_command_for_later(monkeypatch, capsys):
    monkeypatch.setattr(install.ui, "select", lambda *a, **k: "openai")
    monkeypatch.setattr(install.ui, "confirm", lambda *a, **k: False)
    ran = []
    assert install.connect_backend("claude", run=lambda argv, **k: ran.append(argv)) is False
    assert not ran
    assert "Later: claude" in capsys.readouterr().out


def test_both_clis_installed_lets_the_user_choose_and_connect_both(monkeypatch):
    monkeypatch.setattr(install.llm, "available_backends", lambda which=None: ["openclaw", "claude"])
    monkeypatch.setattr(install.ui, "select",
                        lambda msg, choices: "claude" if "default" in msg else "openai")
    monkeypatch.setattr(install.ui, "confirm", lambda msg, **k: "Also connect" in msg)
    _no_verify(monkeypatch)
    connected = []
    monkeypatch.setattr(install, "connect_backend",
                        lambda b, **k: connected.append(b) or True)
    assert install.setup_backend(which=lambda n: "/bin/" + n) == "claude"
    assert connected == ["claude", "openclaw"]     # chosen default first, then the other


def test_preferred_backend_is_offered_first(monkeypatch):
    monkeypatch.setattr(install.llm, "available_backends", lambda which=None: ["openclaw", "claude"])
    seen = {}
    monkeypatch.setattr(install.ui, "select",
                        lambda msg, choices: seen.setdefault("choices", list(choices))[0])
    monkeypatch.setattr(install.ui, "confirm", lambda *a, **k: False)
    monkeypatch.setattr(install, "connect_backend", lambda b, **k: True)
    install.setup_backend(which=lambda n: "/bin/" + n, preferred="claude")
    assert seen["choices"][0] == "claude"


def test_a_failing_probe_offers_the_other_backend(monkeypatch):
    monkeypatch.setattr(install.llm, "available_backends", lambda which=None: ["openclaw", "claude"])
    monkeypatch.setattr(install.ui, "select", lambda msg, choices: "openclaw")
    # yes to the probe, no to "also connect", yes to switching
    monkeypatch.setattr(install.ui, "confirm",
                        lambda msg, **k: "Also connect" not in msg)
    monkeypatch.setattr(install, "connect_backend", lambda b, **k: True)
    _no_verify(monkeypatch, problem="No provider plugins found.")
    assert install.setup_backend(which=lambda n: "/bin/" + n) == "claude"


def test_write_constant_key_merges(tmp_path):
    install.write_constant_key(tmp_path, "backend", "claude")
    install.write_constant_key(tmp_path, "solo_worker", True)
    data = json.loads((tmp_path / "constant.json").read_text())
    assert data == {"backend": "claude", "solo_worker": True}
    assert (tmp_path / "constant.json").read_text().endswith("\n")


def test_extract_resume_text_txt(tmp_path):
    f = tmp_path / "r.txt"; f.write_text("Alex Rivera\nProduct Designer")
    assert "Alex Rivera" in install.extract_resume_text(f)


def test_extract_resume_text_unknown_suffix(tmp_path):
    f = tmp_path / "r.rtf"; f.write_text("x")
    with pytest.raises(SystemExit):
        install.extract_resume_text(f)


def test_bootstrap_writes_both_files(tmp_path, monkeypatch):
    (tmp_path / "r.md").write_text("# Alex Rivera\nProduct designer, 5 years.")
    payload = {
        "identity": {"name": "Alex Rivera", "email": "a@x.com", "phone": "+1", "linkedin": "in/a", "location": "Seattle, WA"},
        "experience": [{"company": "Northgate", "title": "PD", "dates": "2023--", "location": "S", "bullets": ["b"]}],
        "projects": [], "skills": [{"label": "Design Skills", "entries": ["Figma"]}], "education": [],
    }
    monkeypatch.setattr(install.llm, "complete_schema",
                        lambda **k: (install.BootstrapExtract.model_validate(payload).model_dump(exclude_none=True),
                                     install.llm.Reply("{}", finish="stop")))
    install.bootstrap_from_resume(tmp_path / "r.md", tmp_path, backend="claude", model="claude-sonnet-5")
    const = json.loads((tmp_path / "constant.json").read_text())
    cont = json.loads((tmp_path / "content.json").read_text())
    assert const["name"] == "Alex Rivera" and const["backend"] == "claude"
    assert cont["experience"][0]["company"] == "Northgate"
    assert cont["experience"][0]["notes"] == ""


def _seed(root):
    root.mkdir(exist_ok=True)
    (root / "constant.json").write_text(json.dumps({"name": "Alex Rivera", "email": "a@x.com",
        "phone": "+1", "linkedin": "in/a", "location": "Seattle, WA", "backend": "claude"}, indent=2))
    (root / "content.json").write_text(json.dumps({
        "portfolio_link": "", "summary": "",
        "experience": [{"company": "Northgate", "title": "PD", "dates": "2023--", "location": "S", "notes": "", "bullets": ["b"]}],
        "projects": [{"title": "Transit Board", "notes": "", "bullets": ["b"]}],
        "skills": [{"label": "Design Skills", "entries": ["Figma"]}],
    }, indent=2))


def test_choose_optional_sections_adds_empty_arrays(tmp_path, monkeypatch):
    _seed(tmp_path)
    monkeypatch.setattr(install.ui, "checkbox", lambda *a, **k: ["Certifications", "Languages"])
    install.choose_optional_sections(tmp_path)
    c = json.loads((tmp_path / "content.json").read_text())
    assert c["certifications"] == [] and c["languages"] == []
    assert "awards" not in c


def test_preferences_walkthrough_fixed_location(tmp_path, monkeypatch):
    _seed(tmp_path)
    monkeypatch.setattr(install.ui, "select", lambda msg, choices: "One" if "portfolio" in msg else "Fixed")
    monkeypatch.setattr(install.ui, "text", lambda msg, **k: {"Portfolio URL": "example.com",
        "City shown on the resume": "Seattle, WA", "Skills section heading": "Skills",
        "PDF filename stem": "Alex_Rivera", "Output directory": "./applications"}.get(msg.split("(")[0].strip(), ""))
    monkeypatch.setattr(install.ui, "confirm", lambda *a, **k: False)
    install.preferences_walkthrough(tmp_path)
    c = json.loads((tmp_path / "constant.json").read_text())
    assert c["portfolios"] == "example.com"
    assert c["location"] == "Seattle, WA"
    assert "location_rules" not in c or c["location_rules"] == []
    assert c["solo_worker"] is False


def test_preferences_walkthrough_keeps_bootstrapped_defaults(tmp_path, monkeypatch):
    _seed(tmp_path)
    # values the résumé bootstrap already wrote to constant.json
    install.write_constant_key(tmp_path, "location", "Portland, OR")
    install.write_constant_key(tmp_path, "doc_basename", "Alex_Rivera")
    install.write_constant_key(tmp_path, "skills_title", "Core Skills")
    monkeypatch.setattr(install.ui, "select",
                        lambda msg, choices: "One" if "portfolio" in msg else "Fixed")
    # a bare Enter at every text prompt -> ui.text returns the default it was handed
    monkeypatch.setattr(install.ui, "text", lambda msg, **k: k.get("default", ""))
    monkeypatch.setattr(install.ui, "confirm", lambda *a, **k: False)
    install.preferences_walkthrough(tmp_path)
    c = json.loads((tmp_path / "constant.json").read_text())
    assert c["location"] == "Portland, OR"
    assert c["doc_basename"] == "Alex_Rivera"
    assert c["skills_title"] == "Core Skills"


def test_preferences_output_dir_blank_writes_empty_string(tmp_path, monkeypatch):
    _seed(tmp_path)
    monkeypatch.setattr(install.ui, "select",
                        lambda msg, choices: "One" if "portfolio" in msg else "Fixed")
    monkeypatch.setattr(install.ui, "text", lambda msg, **k: {
        "Portfolio URL": "example.com",
        "City shown on the resume": "Seattle, WA",
        "Skills section heading": "Skills",
        "PDF filename stem": "Alex_Rivera",
        "Output directory": "",   # user left it blank
    }.get(msg.split("(")[0].strip(), ""))
    monkeypatch.setattr(install.ui, "confirm", lambda *a, **k: False)
    install.preferences_walkthrough(tmp_path)
    c = json.loads((tmp_path / "constant.json").read_text())
    assert c["output_dir"] == ""


def test_preferences_variable_location_builds_rules(tmp_path, monkeypatch):
    _seed(tmp_path)
    monkeypatch.setattr(install.ui, "select", lambda msg, choices:
                        "One" if "portfolio" in msg else "Varies by job region")
    region_answers = iter([True])   # add one rule, then stop
    monkeypatch.setattr(install.ui, "confirm", lambda msg, **k:
                        next(region_answers, False) if "region rule" in msg else False)
    monkeypatch.setattr(install.ui, "text", lambda msg, **k: {
        "Default city": "Bangalore, India",
        "Regex to match in the JD": r"\bIndia\b",
        "City to show when it matches": "Mumbai, India",
        "Skills section heading": "Skills",
        "PDF filename stem": "Alex_Rivera",
        "Output directory": "",
    }.get(msg.split("(")[0].strip(), ""))
    install.preferences_walkthrough(tmp_path)
    c = json.loads((tmp_path / "constant.json").read_text())
    assert c["location"] == "Bangalore, India"
    assert c["location_rules"] == [{"match": r"\bIndia\b", "location": "Mumbai, India"}]


def test_notes_walkthrough_distills_and_appends(tmp_path, monkeypatch):
    _seed(tmp_path)
    answers = iter(["moved 12 services to EKS", ""])
    monkeypatch.setattr(install.ui, "editor", lambda *a, **k: next(answers))
    monkeypatch.setattr(install.tailor, "distill_note", lambda q, a, **k: f"N:{a}" if a.strip() else "")
    monkeypatch.setattr(install.llm, "complete_schema", lambda **k: ({"entries": []}, None))
    monkeypatch.setattr(install.ui, "confirm", lambda *a, **k: True)
    install.notes_walkthrough(tmp_path, backend="claude", model="claude-haiku-4-5")
    c = json.loads((tmp_path / "content.json").read_text())
    assert c["experience"][0]["notes"].endswith("N:moved 12 services to EKS")
    assert c["projects"][0]["notes"] == ""


def test_notes_walkthrough_skips_write_on_declined_confirmation(tmp_path, monkeypatch):
    # Spec §9: the distilled note must be shown and confirmed before it is written --
    # declining must leave content.json untouched for that entry.
    _seed(tmp_path)
    answers = iter(["moved 12 services to EKS", ""])
    monkeypatch.setattr(install.ui, "editor", lambda *a, **k: next(answers))
    monkeypatch.setattr(install.tailor, "distill_note", lambda q, a, **k: f"N:{a}" if a.strip() else "")
    monkeypatch.setattr(install.llm, "complete_schema", lambda **k: ({"entries": []}, None))
    monkeypatch.setattr(install.ui, "confirm", lambda *a, **k: False)
    install.notes_walkthrough(tmp_path, backend="claude", model="claude-haiku-4-5")
    c = json.loads((tmp_path / "content.json").read_text())
    assert c["experience"][0]["notes"] == ""


# ── Paths as humans type them ────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("'/Users/a/Downloads/Resume.pdf'", ".pdf"),      # quoted by drag-and-drop
    ('"/Users/a/Downloads/Resume.pdf"', ".pdf"),
    ("  /Users/a/Resume.pdf  ", ".pdf"),
    ("/Users/a/My\\ Resume.pdf", ".pdf"),             # backslash-escaped space
])
def test_quoted_paths_keep_their_real_suffix(raw, expected):
    from pathlib import Path as _P
    assert _P(install.ui.clean_path(raw)).suffix == expected


def test_extract_resume_text_rejects_only_real_unknowns(tmp_path):
    # The regression: a quoted .pdf used to reach here as suffix ".pdf'" and be rejected.
    f = tmp_path / "r.pdf"
    assert install.ui.clean_path(f"'{f}'").endswith(".pdf")


# ── Steps skip what is already configured ────────────────────────────────────

def test_configured_reports_each_step(tmp_path, monkeypatch):
    monkeypatch.setattr(install.shutil, "which", lambda n: "/bin/" + n)
    _seed(tmp_path)
    state = install._configured(tmp_path)
    assert state["backend"] is True and state["current_backend"] == "claude"
    assert state["content"] is True
    assert state["preferences"] is False   # no portfolios key written yet
    assert state["notes"] is False         # seeded entries have empty notes
    assert state["optional"] is False

    install.write_constant_key(tmp_path, "portfolios", "example.com")
    content = json.loads((tmp_path / "content.json").read_text())
    content["experience"][0]["notes"] = "real note"
    content["certifications"] = []
    (tmp_path / "content.json").write_text(json.dumps(content))
    state = install._configured(tmp_path)
    assert state["preferences"] and state["notes"] and state["optional"]


def test_configured_on_an_empty_directory(tmp_path):
    state = install._configured(tmp_path)
    assert not any(state[k] for k in ("backend", "content", "preferences", "notes", "optional"))


def test_step_defaults_to_skip_when_done(monkeypatch):
    monkeypatch.setattr(install.ui, "interactive", lambda: True)
    seen = {}
    monkeypatch.setattr(install.ui, "confirm",
                        lambda msg, **k: seen.update(msg=msg, default=k.get("default")) or False)
    install._step("preferences", lambda: None, done=True)
    assert seen["default"] is False and "Redo" in seen["msg"]
    install._step("preferences", lambda: None, done=False)
    assert seen["default"] is True


def test_step_is_a_no_op_when_done_and_non_interactive(monkeypatch):
    monkeypatch.setattr(install.ui, "interactive", lambda: False)
    ran = []
    install._step("preferences", lambda: ran.append(1), done=True)
    assert not ran
    install._step("preferences", lambda: ran.append(1), done=False)
    assert ran == [1]


# ── Bootstrap ────────────────────────────────────────────────────────────────

def test_bootstrap_defaults_to_yes_on_a_first_run(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(install.ui, "confirm",
                        lambda msg, **k: seen.update(default=k.get("default")) or False)
    install.bootstrap_step(tmp_path, backend="claude", done=False)
    assert seen["default"] is True


def _seed_content(tmp_path):
    (tmp_path / "content.json").write_text('{"experience": [], "projects": []}')


def test_bootstrap_once_content_exists_offers_add_before_replace(tmp_path, monkeypatch):
    # The property this whole task exists to guarantee: destroying existing content is
    # never the default and always needs an explicit, informed action.
    _seed_content(tmp_path)
    seen = {}
    monkeypatch.setattr(install.ui, "select",
                        lambda msg, choices: seen.update(choices=list(choices)) or "Skip")
    install.bootstrap_step(tmp_path, backend="claude", done=True)
    choices = seen["choices"]
    assert choices[0].startswith("Add")                      # non-destructive action is first
    assert not choices[0].lower().startswith("replace")
    replace = [c for c in choices if c.lower().startswith("replace")][0]
    assert "notes" in replace.lower()                        # the consequence is visible


def test_bootstrap_skip_writes_nothing(tmp_path, monkeypatch):
    _seed_content(tmp_path)
    original = (tmp_path / "content.json").read_text()
    monkeypatch.setattr(install.ui, "select", lambda msg, choices: "Skip")
    monkeypatch.setattr(install.ui, "path",
                        lambda *a, **k: pytest.fail("Skip must not ask for a resume"))
    monkeypatch.setattr(merge, "run", lambda *a, **k: pytest.fail("Skip must not merge"))
    monkeypatch.setattr(install, "bootstrap_from_resume",
                        lambda *a, **k: pytest.fail("Skip must not rebuild content.json"))
    install.bootstrap_step(tmp_path, backend="claude", done=True)
    assert (tmp_path / "content.json").read_text() == original
    assert not list(tmp_path.glob("content.json*.bak"))


def test_bootstrap_add_path_calls_merge_run_and_never_bootstraps(tmp_path, monkeypatch):
    _seed_content(tmp_path)
    called = []
    monkeypatch.setattr(install.ui, "select", lambda msg, choices: choices[0])   # "Add ..."
    monkeypatch.setattr(install.ui, "path", lambda *a, **k: tmp_path / "cv.pdf")
    monkeypatch.setattr(merge, "run", lambda *a, **k: called.append("run") or True)
    monkeypatch.setattr(install, "bootstrap_from_resume",
                        lambda *a, **k: pytest.fail("Add must not reach bootstrap_from_resume"))
    install.bootstrap_step(tmp_path, backend="claude", done=True)
    assert called == ["run"]


def test_bootstrap_replace_defaults_to_no_and_backs_up_before_overwrite(tmp_path, monkeypatch):
    _seed_content(tmp_path)
    seen = {}
    order = []
    monkeypatch.setattr(install.ui, "select",
                        lambda msg, choices: [c for c in choices if c.lower().startswith("replace")][0])
    monkeypatch.setattr(install.ui, "path", lambda *a, **k: tmp_path / "cv.pdf")
    monkeypatch.setattr(install.ui, "confirm",
                        lambda msg, **k: seen.update(default=k.get("default"), msg=msg) or True)
    monkeypatch.setattr(merge, "backup",
                        lambda *a, **k: order.append(("backup", k.get("include_constant")))
                        or [tmp_path / "content.json.STAMP.bak", tmp_path / "constant.json.STAMP.bak"])
    monkeypatch.setattr(install, "bootstrap_from_resume",
                        lambda *a, **k: order.append("overwrite"))
    install.bootstrap_step(tmp_path, backend="claude", done=True)
    assert seen["default"] is False                 # the destructive confirm defaults to No
    assert "constant.json" in seen["msg"]           # both consequences named, not just notes
    assert order == [("backup", True), "overwrite"]  # constant.json is snapshotted too, first


def test_bootstrap_replace_declined_writes_nothing(tmp_path, monkeypatch):
    _seed_content(tmp_path)
    monkeypatch.setattr(install.ui, "select",
                        lambda msg, choices: [c for c in choices if c.lower().startswith("replace")][0])
    monkeypatch.setattr(install.ui, "path", lambda *a, **k: tmp_path / "cv.pdf")
    monkeypatch.setattr(install.ui, "confirm", lambda msg, **k: False)
    monkeypatch.setattr(merge, "backup",
                        lambda *a, **k: pytest.fail("a declined replace must not back up"))
    monkeypatch.setattr(install, "bootstrap_from_resume",
                        lambda *a, **k: pytest.fail("a declined replace must not overwrite"))
    install.bootstrap_step(tmp_path, backend="claude", done=True)


def test_bootstrap_first_run_backs_up_constant_json_when_no_content_exists(tmp_path, monkeypatch):
    # The sequence that actually bit the user: preferences walkthrough wrote portfolios
    # and location_rules into constant.json, content.json was never bootstrapped, and a
    # re-run took the first-run branch. The guard keyed on content.json, so nothing was
    # backed up and bootstrap_from_resume wiped constant.json.
    (tmp_path / "constant.json").write_text(json.dumps({
        "portfolios": "https://p.example",
        "location_rules": [{"match": "NYC", "city": "New York, NY"}],
        "backend": "claude"}, indent=2))
    assert not (tmp_path / "content.json").exists()
    monkeypatch.setattr(install.ui, "confirm", lambda *a, **k: True)
    monkeypatch.setattr(install.ui, "path", lambda *a, **k: tmp_path / "cv.pdf")
    monkeypatch.setattr(install, "bootstrap_from_resume",
                        lambda *a, **k: (tmp_path / "constant.json").write_text('{"backend": "claude"}'))
    install.bootstrap_step(tmp_path, backend="claude", done=False)

    baks = list(tmp_path.glob("constant.json.*.bak"))
    assert len(baks) == 1
    saved = json.loads(baks[0].read_text())
    assert saved["portfolios"] == "https://p.example"
    assert saved["location_rules"] == [{"match": "NYC", "city": "New York, NY"}]


def test_bootstrap_blank_path_skips_without_calling_the_model(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(install.ui, "confirm", lambda *a, **k: True)
    monkeypatch.setattr(install.ui, "path", lambda *a, **k: None)
    monkeypatch.setattr(install, "bootstrap_from_resume",
                        lambda *a, **k: pytest.fail("must not run without a file"))
    install.bootstrap_step(tmp_path, backend="claude", done=False)
    assert "Skipped" in capsys.readouterr().out


def test_a_working_probe_overrides_a_failed_login(monkeypatch, capsys):
    # The real case on a connected machine: `openclaw models auth login` refuses
    # (it wants a TTY it does not have) while openclaw itself answers fine.
    monkeypatch.setattr(install.llm, "available_backends", lambda which=None: ["openclaw"])
    monkeypatch.setattr(install.ui, "confirm", lambda *a, **k: True)
    monkeypatch.setattr(install, "connect_backend", lambda b, **k: False)
    _no_verify(monkeypatch, problem="")
    assert install.setup_backend(which=lambda n: "/bin/openclaw") == "openclaw"
    out = capsys.readouterr().out
    assert "is working" in out and "no further login needed" in out


def test_skipping_the_probe_after_a_failed_login_warns(monkeypatch, capsys):
    monkeypatch.setattr(install.llm, "available_backends", lambda which=None: ["openclaw"])
    monkeypatch.setattr(install.ui, "confirm", lambda *a, **k: False)
    monkeypatch.setattr(install, "connect_backend", lambda b, **k: False)
    install.setup_backend(which=lambda n: "/bin/openclaw")
    assert "did not complete" in capsys.readouterr().out


def _cp(argv, code=0, out=""):
    return subprocess.CompletedProcess(argv, code, stdout=out, stderr="")


def test_openclaw_plugins_parses_names():
    out = "Installed plugins:\n  openai  v1.2.0\n  anthropic  v0.9.1\n"
    got = install.openclaw_plugins(run=lambda *a, **k: _cp(a[0], 0, out))
    assert got == ["openai", "anthropic"]


def test_openclaw_plugins_unknown_on_failure():
    assert install.openclaw_plugins(run=lambda *a, **k: _cp(a[0], 1, "")) == []


def test_ensure_plugin_installs_a_missing_provider(monkeypatch):
    # The exact first-run failure: picking `claude` with no provider plugin installed
    # printed "No provider plugins found" and left setup stuck.
    monkeypatch.setattr(install, "openclaw_plugins", lambda **k: ["openai"])
    monkeypatch.setattr(install.ui, "confirm", lambda *a, **k: True)
    calls = []
    ok = install.ensure_plugin("claude", run=lambda argv, **k: calls.append(argv) or _cp(argv, 0))
    assert ok is True
    assert ["openclaw", "plugins", "install", "claude"] in calls


def test_ensure_plugin_skips_when_already_installed(monkeypatch):
    monkeypatch.setattr(install, "openclaw_plugins", lambda **k: ["claude"])
    calls = []
    assert install.ensure_plugin("claude", run=lambda argv, **k: calls.append(argv) or _cp(argv, 0))
    assert calls == []


def test_ensure_plugin_never_fatal_when_install_fails(monkeypatch, capsys):
    monkeypatch.setattr(install, "openclaw_plugins", lambda **k: ["openai"])
    monkeypatch.setattr(install.ui, "confirm", lambda *a, **k: True)
    ok = install.ensure_plugin("claude", run=lambda argv, **k: _cp(argv, 1))
    assert ok is False
    assert "plugins install claude" in capsys.readouterr().out


def test_ensure_plugin_proceeds_when_plugin_list_unknown(monkeypatch):
    # An unparseable `plugins list` must not block a working setup.
    monkeypatch.setattr(install, "openclaw_plugins", lambda **k: [])
    assert install.ensure_plugin("claude", run=lambda argv, **k: _cp(argv, 0)) is True


def test_ensure_plugin_user_declines_install(monkeypatch, capsys):
    monkeypatch.setattr(install, "openclaw_plugins", lambda **k: ["openai"])
    monkeypatch.setattr(install.ui, "confirm", lambda *a, **k: False)
    calls = []
    ok = install.ensure_plugin("claude", run=lambda argv, **k: calls.append(argv) or _cp(argv, 0))
    assert ok is False
    assert calls == []  # no install command was run
    assert "Later:" in capsys.readouterr().out


def test_openclaw_plugins_exception_returns_empty():
    # Timeout or other exceptions during plugin list must not crash.
    def raise_timeout(*a, **k):
        raise subprocess.TimeoutExpired("cmd", 30)
    assert install.openclaw_plugins(run=raise_timeout) == []

    def raise_error(*a, **k):
        raise OSError("connection failed")
    assert install.openclaw_plugins(run=raise_error) == []


def test_openclaw_plugins_rejects_prose_starting_with_provider_name():
    # The hard case: a prose description line whose first word is a provider name.
    # E.g. "  openai models require authentication" would be parsed as 'openai' installed
    # if we only validate tokens, which makes ensure_plugin skip the install the user needs.
    out = "Installed plugins:\n  claude  v1.0.0\n  openai models require authentication\n"
    assert install.openclaw_plugins(run=lambda *a, **k: _cp(a[0], 0, out)) == []


def test_openclaw_plugins_rejects_json_output():
    # JSON output is indented and starts with `{`, which would parse as garbage.
    out = "Installed plugins:\n  {\"name\": \"openai\", \"version\": \"1.2.0\"}\n"
    assert install.openclaw_plugins(run=lambda *a, **k: _cp(a[0], 0, out)) == []


def test_openclaw_plugins_rejects_box_drawing():
    # Box-drawing characters like │ or ─ are not valid plugin names.
    out = "Installed plugins:\n│ openai │ v1.2.0 │\n│ claude │ v2.0.0 │\n"
    assert install.openclaw_plugins(run=lambda *a, **k: _cp(a[0], 0, out)) == []


def test_openclaw_plugins_strips_ansi_colors():
    # ANSI color codes should be stripped before parsing.
    out = "Installed plugins:\n  \x1b[32mopenai\x1b[0m  v1.2.0\n  \x1b[32manthropicai\x1b[0m  v2.0.0\n"
    got = install.openclaw_plugins(run=lambda *a, **k: _cp(a[0], 0, out))
    assert got == ["openai", "anthropicai"]


def test_openclaw_plugins_accepts_names_with_underscores_dots_dashes():
    # Valid plugin names can contain underscores, dots, and dashes (but not start with them).
    out = "Installed plugins:\n  open-ai_fork  v1.0\n  anthropic.official  v2.0\n"
    got = install.openclaw_plugins(run=lambda *a, **k: _cp(a[0], 0, out))
    assert got == ["open-ai_fork", "anthropic.official"]


def test_openclaw_plugins_rejects_plugin_name_with_colon():
    # Plugin names with colons (like vendor:openai) are not valid and should reject the list.
    out = "Installed plugins:\n  vendor:openai  v1.0\n"
    assert install.openclaw_plugins(run=lambda *a, **k: _cp(a[0], 0, out)) == []


def test_solo_question_wording_is_plain():
    import inspect
    src = inspect.getsource(install.preferences_walkthrough)
    # "end-to-end solo, with no handoffs" read as jargon on a first run.
    assert "no handoffs" not in src
    assert "research, design and build yourself" in src


def test_configured_reports_backend_when_binary_present(tmp_path, monkeypatch):
    (tmp_path / "constant.json").write_text('{"backend": "openclaw"}')
    (tmp_path / "content.json").write_text('{"experience": []}')
    monkeypatch.setattr(install.shutil, "which", lambda n: "/bin/" + n)
    state = install._configured(tmp_path)
    assert state["backend"] is True
    assert state["current_backend"] == "openclaw"


def test_configured_reports_backend_missing_when_binary_gone(tmp_path, monkeypatch):
    (tmp_path / "constant.json").write_text('{"backend": "openclaw"}')
    (tmp_path / "content.json").write_text("{}")
    monkeypatch.setattr(install.shutil, "which", lambda n: None)
    assert install._configured(tmp_path)["backend"] is False


def test_step_skips_a_configured_step_by_default(monkeypatch):
    monkeypatch.setattr(install.ui, "interactive", lambda: True)
    seen = {}
    monkeypatch.setattr(install.ui, "confirm",
                        lambda msg, default=False: seen.setdefault("default", default))
    install._step("preferences", lambda: None, done=True)
    assert seen["default"] is False        # already set up -> default is "don't redo"


def test_main_declining_setup_with_no_cli_exits_cleanly(tmp_path, monkeypatch, capsys):
    """First-run decline path with no CLI on PATH should exit cleanly, not traceback."""
    monkeypatch.setattr(install, "ROOT", tmp_path)
    tmp_path.mkdir(exist_ok=True)
    monkeypatch.setattr(install.ui, "interactive", lambda: True)
    monkeypatch.setattr(install.ui, "confirm", lambda *a, **k: False)  # decline all steps
    monkeypatch.setattr(install, "check_dependencies", lambda: [])
    monkeypatch.setattr(install, "_pip_install", lambda: None)
    monkeypatch.setattr(install, "bootstrap_step", lambda *a, **k: None)
    monkeypatch.setattr(install, "choose_optional_sections", lambda *a, **k: None)
    monkeypatch.setattr(install, "preferences_walkthrough", lambda *a, **k: None)
    monkeypatch.setattr(install, "notes_walkthrough", lambda *a, **k: None)
    monkeypatch.setattr(install.llm, "detect_backend",
                        lambda: (_ for _ in ()).throw(
                            install.llm.LLMError("no supported CLI found on PATH")))
    with pytest.raises(SystemExit) as exc_info:
        install.main()
    assert exc_info.value.code == 1
    out = capsys.readouterr().out
    assert "no supported CLI found on PATH" in out
    assert "Traceback" not in out


def test_main_preserves_configured_backend_when_redo_declined(tmp_path, monkeypatch):
    """Configured backend is preserved when all steps are declined."""
    monkeypatch.setattr(install, "ROOT", tmp_path)
    (tmp_path / "constant.json").write_text(json.dumps({
        "name": "Test", "email": "test@x.com", "phone": "+1",
        "linkedin": "in/test", "location": "Seattle, WA", "backend": "openclaw"
    }, indent=2))
    (tmp_path / "content.json").write_text(json.dumps({
        "portfolio_link": "", "summary": "",
        "experience": [{"company": "Co", "title": "T", "dates": "2023--", "location": "S",
                       "notes": "", "bullets": ["b"]}],
        "projects": [], "skills": [], "education": []
    }, indent=2))
    monkeypatch.setattr(install.shutil, "which", lambda n: "/bin/" + n)  # backends available
    monkeypatch.setattr(install.ui, "interactive", lambda: True)
    monkeypatch.setattr(install.ui, "confirm", lambda *a, **k: False)  # decline all steps
    monkeypatch.setattr(install, "check_dependencies", lambda: [])
    monkeypatch.setattr(install, "_pip_install", lambda: None)
    monkeypatch.setattr(install, "bootstrap_step", lambda *a, **k: None)
    monkeypatch.setattr(install, "choose_optional_sections", lambda *a, **k: None)
    monkeypatch.setattr(install, "preferences_walkthrough", lambda *a, **k: None)
    monkeypatch.setattr(install, "notes_walkthrough", lambda *a, **k: None)

    # Capture the backend passed to llm.score_model
    seen_backend = {}
    def capture_score_model(backend, *a, **k):
        seen_backend["backend"] = backend
        return backend, "test-model"
    monkeypatch.setattr(install.llm, "score_model", capture_score_model)

    # Verify setup_backend was never called
    setup_called = []
    orig_setup = install.setup_backend
    def track_setup(*a, **k):
        setup_called.append(True)
        return orig_setup(*a, **k)
    monkeypatch.setattr(install, "setup_backend", track_setup)

    install.main()
    assert not setup_called  # setup_backend should not be called when all steps declined
    assert seen_backend["backend"] == "openclaw"  # backend preserved from config


def test_entry_prompts_maps_questions_by_ref(monkeypatch):
    payload = {"entries": [
        {"ref": "project:Transit Fare", "questions": ["How many daily users?", "What constraint?"]},
        {"ref": "experience:Acme", "questions": ["What was the team size?"]},
    ]}
    monkeypatch.setattr(install.llm, "complete_schema", lambda **k: (payload, None))
    targets = [("project", {}, "Transit Fare"), ("experience", {}, "Acme")]
    got = install.entry_prompts(targets, backend="openclaw", model="m")
    assert got["project:Transit Fare"][0] == "How many daily users?"
    assert got["experience:Acme"] == ["What was the team size?"]


def test_entry_prompts_falls_back_to_static_on_failure(monkeypatch):
    def boom(**k):
        raise install.llm.LLMError("no model")
    monkeypatch.setattr(install.llm, "complete_schema", boom)
    got = install.entry_prompts([("project", {}, "Transit Fare")], backend="openclaw", model="m")
    # A failed call must not block the walkthrough -- generic guidance beats none.
    assert got["project:Transit Fare"] == install.STATIC_PROMPTS


def test_notes_walkthrough_prints_guidance(monkeypatch, tmp_path, capsys):
    (tmp_path / "constant.json").write_text('{"name": "A"}')
    (tmp_path / "content.json").write_text(json.dumps(
        {"experience": [], "projects": [{"title": "Transit Fare", "bullets": ["Cut steps 7 to 4"]}]}))
    monkeypatch.setattr(install, "entry_prompts",
                        lambda *a, **k: {"project:Transit Fare": ["How many daily users?"]})
    monkeypatch.setattr(install.ui, "editor", lambda *a, **k: "")   # skip every entry
    install.notes_walkthrough(tmp_path, backend="openclaw", model="m")
    out = capsys.readouterr().out
    assert "Useful context here" in out
    assert "How many daily users?" in out


def test_entry_prompts_matches_refs_despite_whitespace_and_casing(monkeypatch):
    # The model rarely echoes a ref back byte-for-byte. Drift in surrounding
    # whitespace or casing must not miss the lookup for every entry.
    payload = {"entries": [
        {"ref": "  PROJECT:Transit Fare  ", "questions": ["How many daily users?"]},
        {"ref": "Experience:ACME", "questions": ["What was the team size?"]},
    ]}
    monkeypatch.setattr(install.llm, "complete_schema", lambda **k: (payload, None))
    targets = [("project", {}, "Transit Fare"), ("experience", {}, "Acme")]
    got = install.entry_prompts(targets, backend="openclaw", model="m")
    assert got["project:Transit Fare"] == ["How many daily users?"]
    assert got["experience:Acme"] == ["What was the team size?"]


def test_entry_prompts_flags_a_total_ref_mismatch(monkeypatch, capsys):
    # Call succeeded but not one ref resolved -> that is a failed personalisation,
    # not success. Every entry gets STATIC_PROMPTS and the user is told why.
    payload = {"entries": [{"ref": "something:else", "questions": ["ignored"]}]}
    monkeypatch.setattr(install.llm, "complete_schema", lambda **k: (payload, None))
    targets = [("project", {}, "Transit Fare"), ("experience", {}, "Acme")]
    got = install.entry_prompts(targets, backend="openclaw", model="m")
    assert got["project:Transit Fare"] == install.STATIC_PROMPTS
    assert got["experience:Acme"] == install.STATIC_PROMPTS
    assert "generic" in capsys.readouterr().out.lower()


def test_entry_prompts_failure_prints_a_visible_hint(monkeypatch, capsys):
    def boom(**k):
        raise install.llm.LLMError("no model")
    monkeypatch.setattr(install.llm, "complete_schema", boom)
    install.entry_prompts([("project", {}, "Transit Fare")], backend="openclaw", model="m")
    assert "generic" in capsys.readouterr().out.lower()


def test_entry_prompts_sends_notes_in_the_payload(monkeypatch):
    seen = {}
    monkeypatch.setattr(install.llm, "complete_schema",
                        lambda **k: seen.update(k) or ({"entries": []}, None))
    targets = [("project", {"bullets": ["b"], "notes": "already: 2M daily riders"}, "Transit Fare")]
    install.entry_prompts(targets, backend="openclaw", model="m")
    assert "already: 2M daily riders" in seen["user"]


def test_entry_prompts_payload_is_minified_and_capped(monkeypatch):
    seen = {}
    monkeypatch.setattr(install.llm, "complete_schema",
                        lambda **k: seen.update(k) or ({"entries": []}, None))
    targets = [("project", {"bullets": ["x " * 20000]}, "Transit Fare")]
    install.entry_prompts(targets, backend="openclaw", model="m")
    assert ", " not in seen["user"] and ": " not in seen["user"]  # separators=(",", ":")
    assert len(seen["user"]) == 20000                             # [:20000] cap applied
