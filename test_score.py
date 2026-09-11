import pytest
import llm

def test_default_run_scores_one_tier_down():
    assert llm.score_model("openclaw", "terra", None) == ("openclaw", "openai/gpt-5.6-luna")
    assert llm.score_model("claude", "sonnet", None) == ("claude", "claude-haiku-4-5")

def test_opus_scores_on_sonnet():
    assert llm.score_model("openclaw", "opus", None) == ("openclaw", "anthropic/claude-sonnet-5")
    assert llm.score_model("claude", "opus", None) == ("claude", "claude-sonnet-5")

def test_cheapest_preset_stays_itself():
    assert llm.score_model("openclaw", "luna", None)[1] == "openai/gpt-5.6-luna"
    assert llm.score_model("claude", "haiku", None)[1] == "claude-haiku-4-5"

def test_pinned_model_falls_back_to_backend_cheapest():
    # run pinned an explicit id (no preset) -> score on the backend's cheapest preset
    assert llm.score_model("openclaw", None, "anthropic/claude-opus-4-8")[1] == "openai/gpt-5.6-luna"
    assert llm.score_model("claude", None, "claude-opus-5")[1] == "claude-haiku-4-5"

def test_override_preset_wins():
    assert llm.score_model("openclaw", "terra", None, override_preset="sonnet") == ("openclaw", "anthropic/claude-sonnet-5")

def test_default_run_scores_tier_below_default_preset():
    # tailor.run() passes `args.preset or llm.DEFAULT_PRESET[backend]` as run_preset, so a
    # default run (no --preset) scores one tier below the DEFAULT preset via SCORE_TIER,
    # not through the pinned-model cheapest fallback.
    for backend in llm.DEFAULT_PRESET:
        run_preset = None or llm.DEFAULT_PRESET[backend]      # the tailor.run expression
        want = llm.resolve(llm.SCORE_TIER[backend][llm.DEFAULT_PRESET[backend]], backend=backend)
        assert llm.score_model(backend, run_preset, None) == want
