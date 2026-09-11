"""Repo-wide test safety net: no test may shell out to a real model CLI.

Task 5 retro: a real model subprocess hid inside a passing test because a caller
(`install.entry_prompts`) swallows failures and falls back silently. Tasks 6, 7, 9,
12 and 13 all add model calls beneath code that existing tests already exercise, so
without a guard this recurs and stays green.

Every model call in the project funnels through `llm._spawn` -- the `subprocess.run`
beneath `llm.run_once`. This autouse fixture replaces it with a hard failure that
names the offending test, so reaching the model layer without stubbing
`llm.complete_schema` / `llm.complete_text` (or `llm.run_once`) fails loudly instead
of quietly spawning a CLI.

A test that legitimately needs the real spawn seam marks itself
`@pytest.mark.real_model`.

Two more real-world seams get the same treatment here:

- `tailor.run(output_dir=None)` falls back to `~/Documents/Applications` -- the
  user's real, un-versioned application records -- and writes the JD into it
  *before* `compile_pdf` runs, so a test that forgets to override the output dir
  (or mock `Path.home`) writes there for real. `RESUME_TEX_OUTPUT_DIR` is read by
  `tailor.py`'s own argparse default, so setting it here redirects every test that
  doesn't explicitly ask for the real default.
- `install._pip_install` is the one subprocess caller with no injectable `run=`
  seam (every other caller in install.py takes `run=None` and defaults to
  `subprocess.run`), so a test that forgets to stub it would run a real
  `pip install`. Guarding `subprocess.run` itself catches that un-injected path
  without touching the `run=`-injected callers, which never reach it.
"""
import subprocess

import pytest

import llm


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "real_model: test legitimately exercises llm._spawn / a real model subprocess",
    )
    config.addinivalue_line(
        "markers",
        "real_subprocess: test legitimately exercises a real subprocess.run call",
    )


@pytest.fixture(autouse=True)
def _no_real_model_calls(request, monkeypatch):
    if request.node.get_closest_marker("real_model"):
        return

    def _blocked(argv, **kwargs):
        raise RuntimeError(
            f"{request.node.nodeid} reached the real model layer (llm._spawn). "
            "No test may shell out to a model CLI. Stub llm.complete_schema / "
            "llm.complete_text (or llm.run_once) in this test -- or mark it "
            "@pytest.mark.real_model if it truly needs the subprocess seam."
        )

    monkeypatch.setattr(llm, "_spawn", _blocked)


@pytest.fixture(autouse=True)
def _no_real_output_dir(tmp_path, monkeypatch):
    """Redirect the default output base into tmp_path for every test.

    A test that calls tailor.run(output_dir=None) without also mocking Path.home
    would otherwise write straight into ~/Documents/Applications. A test that wants
    to exercise that real default explicitly deletes the env var itself (see
    test_flow.py's Task 15 tests), which still works fine on top of this default.
    """
    monkeypatch.setenv("RESUME_TEX_OUTPUT_DIR", str(tmp_path / "Applications"))


@pytest.fixture(autouse=True)
def _no_real_subprocess_calls(request, monkeypatch):
    if request.node.get_closest_marker("real_subprocess"):
        return

    def _blocked(argv, **kwargs):
        raise RuntimeError(
            f"{request.node.nodeid} reached a real subprocess.run({argv!r}). "
            "No test may shell out to a real subprocess. Pass an injectable "
            "run=... to the function under test, or stub subprocess.run directly "
            "-- or mark the test @pytest.mark.real_subprocess if it truly needs it."
        )

    monkeypatch.setattr(subprocess, "run", _blocked)
