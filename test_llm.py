"""Tests for the model call layer (both backends).

AGENT_DOC and CLAUDE_DOC are trimmed from real `openclaw agent --json` and
`claude -p --output-format json` output, so each parser is pinned to a shape the CLI
actually emits rather than to one we assumed it emits.

Run: python3 -m pytest test_llm.py -q
"""
from typing import List, Optional

import pytest
from pydantic import BaseModel

import llm


class Inner(BaseModel):
    label: str
    entries: List[str]


class Sample(BaseModel):
    name: Optional[str] = None
    title: str
    groups: List[Inner]


AGENT_DOC = {
    "payloads": [{"text": '{"title":"t","groups":[]}', "mediaUrl": None}],
    "meta": {
        "durationMs": 3038,
        "finalAssistantVisibleText": '{"title":"t","groups":[]}',
        "finalAssistantRawText": '{"title":"t","groups":[]}',
        "completion": {"finishReason": "stop", "stopReason": "completed", "refusal": False},
        "agentMeta": {
            "sessionId": "f51b03a0",
            "provider": "claude-cli",
            "model": "claude-opus-5",
            "usage": {"input": 2, "output": 1, "cacheWrite": 46740},
        },
    },
}


# ── Presets ───────────────────────────────────────────────────────────────────

def test_openclaw_presets_resolve_to_prefixed_ids():
    for name, expected in [("opus", "anthropic/claude-opus-5"),
                           ("sonnet", "anthropic/claude-sonnet-5"),
                           ("sol", "openai/gpt-5.6-sol"),
                           ("terra", "openai/gpt-5.6-terra"),
                           ("luna", "openai/gpt-5.6-luna")]:
        assert llm.resolve(name, backend="openclaw") == ("openclaw", expected)


def test_claude_presets_resolve_to_plain_ids():
    assert llm.resolve("sonnet", backend="claude") == ("claude", "claude-sonnet-5")
    assert llm.resolve("opus", backend="claude") == ("claude", "claude-opus-5")


def test_each_backend_defaults_to_its_own_preset():
    # The documented contract: OpenClaw -> terra, Claude CLI -> sonnet.
    assert llm.DEFAULT_PRESET["openclaw"] == "terra"
    assert llm.DEFAULT_PRESET["claude"] == "sonnet"
    for backend, preset in llm.DEFAULT_PRESET.items():
        catalog = llm.PRESETS_BY_BACKEND[backend]
        assert preset in catalog
        assert llm.resolve(backend=backend) == (backend, catalog[preset])


def test_openclaw_wins_when_both_clis_are_installed():
    assert llm.detect_backend(which=lambda b: "/bin/" + b) == "openclaw"
    assert llm.detect_backend(which=lambda b: "/bin/claude" if b == "claude" else None) == "claude"


def test_no_cli_at_all_names_both_install_paths():
    with pytest.raises(llm.LLMError, match="claude-code"):
        llm.detect_backend(which=lambda b: None)


def test_every_openclaw_preset_carries_a_provider_prefix():
    # A bare id resolves to openai/<id> inside OpenClaw and errors at call time.
    for name, model in llm.OPENCLAW_PRESETS.items():
        assert model.split("/")[0] in ("anthropic", "openai"), name


def test_no_openclaw_preset_points_at_an_unbound_model():
    # Agent "main" rejects anything not in agents.defaults.models; claude-haiku-4-5 is
    # the one that bit us, so it must never reappear as an OpenClaw preset.
    assert "anthropic/claude-haiku-4-5" not in llm.OPENCLAW_PRESETS.values()


def test_bare_model_id_is_rejected_for_openclaw_only():
    with pytest.raises(llm.LLMError, match="provider prefix"):
        llm.resolve(model="claude-opus-5", backend="openclaw")
    # The Claude CLI wants exactly that bare form.
    assert llm.resolve(model="claude-opus-5", backend="claude") == ("claude", "claude-opus-5")


def test_model_override_wins_over_preset():
    assert llm.resolve("opus", model="openai/gpt-5.5", backend="openclaw") == (
        "openclaw", "openai/gpt-5.5")


def test_unknown_preset_is_named_with_its_backend():
    with pytest.raises(llm.LLMError, match="nope"):
        llm.resolve("nope", backend="openclaw")
    # A preset can be real on one backend and absent from the other.
    with pytest.raises(llm.LLMError, match="claude backend"):
        llm.resolve("terra", backend="claude")


# ── Command line ──────────────────────────────────────────────────────────────

def test_agent_argv_is_pinned():
    argv = llm.agent_argv(binary="openclaw", model="anthropic/claude-opus-5",
                          prompt="P", thinking="high", key="agent:main:k")
    assert argv == [
        "openclaw", "agent", "--local", "--model", "anthropic/claude-opus-5",
        "--json", "--session-key", "agent:main:k", "--message", "P",
        "--thinking", "high"]


def test_agent_argv_never_delivers():
    # The runtime carries a `message` tool; --deliver would let a resume generation
    # decide to send something to a chat channel.
    assert "--deliver" not in llm.agent_argv(
        binary="openclaw", model="m", prompt="P", thinking=None, key="k")


def test_thinking_is_omitted_when_unset():
    assert "--thinking" not in llm.agent_argv(
        binary="openclaw", model="m", prompt="P", thinking=None, key="k")


def test_session_keys_are_unique_per_run():
    # A reused key would carry the previous JD into the next generation.
    assert llm.session_key() != llm.session_key()
    assert llm.session_key().startswith("agent:main:resume-tex-")


# ── Environment ───────────────────────────────────────────────────────────────

PARENT_ENV = {"ANTHROPIC_API_KEY": "sk-x", "OPENAI_API_KEY": "sk-y",
              "ANTHROPIC_AUTH_TOKEN": "t", "PATH": "/usr/bin", "HOME": "/h"}


def test_api_keys_are_stripped_from_an_openclaw_child():
    # A stray key would silently route the run back to metered API billing.
    assert llm.child_env("openclaw", PARENT_ENV) == {"PATH": "/usr/bin", "HOME": "/h"}


def test_the_claude_cli_keeps_the_parent_env():
    # An API key may be the only credential that user has; stripping it breaks them.
    assert llm.child_env("claude", PARENT_ENV) == PARENT_ENV


# ── Result parsing ────────────────────────────────────────────────────────────

def test_agent_reply_from_real_shape():
    reply = llm.reply_from_agent(AGENT_DOC)
    assert reply.text == '{"title":"t","groups":[]}'
    assert reply.model == "claude-opus-5"
    assert reply.finish == "stop"


def test_agent_falls_back_to_meta_text_when_payloads_are_empty():
    doc = {"payloads": [], "meta": dict(AGENT_DOC["meta"])}
    assert llm.reply_from_agent(doc).text == '{"title":"t","groups":[]}'


def test_agent_refusal_is_an_error_not_an_empty_resume():
    doc = {"payloads": [{"text": "I can't help with that."}],
           "meta": {"completion": {"refusal": True}}}
    with pytest.raises(llm.LLMError, match="declined"):
        llm.reply_from_agent(doc)


def test_empty_reply_is_an_error():
    with pytest.raises(llm.LLMError):
        llm.reply_from_agent({"payloads": [{"text": ""}], "meta": {}})
    with pytest.raises(llm.LLMError):
        llm.reply_from_agent({"payloads": [], "meta": {}})


# ── JSON extraction ───────────────────────────────────────────────────────────

def test_plain_json():
    assert llm.json_from('{"a":1}') == {"a": 1}


def test_fenced_json():
    assert llm.json_from('```json\n{"a":1}\n```') == {"a": 1}


def test_json_wrapped_in_prose():
    assert llm.json_from('Here you go:\n{"a":1}\nHope that helps!') == {"a": 1}


def test_truncated_json_raises():
    # This is how a max_tokens cutoff surfaces when finishReason does not catch it.
    with pytest.raises(llm.LLMError, match="unparseable"):
        llm.json_from('{"a":1,"b":[1,2')


def test_non_json_raises():
    with pytest.raises(llm.LLMError, match="unparseable"):
        llm.json_from("I'm afraid I can't do that.")


# ── Schema flattening ─────────────────────────────────────────────────────────

def test_refs_are_inlined():
    js = llm.schema_json(Sample)
    assert "$defs" not in js
    groups = js["properties"]["groups"]["items"]
    assert groups["properties"]["label"]["type"] == "string"


def test_flattened_schema_is_json_serialisable():
    import json as _json
    _json.dumps(llm.schema_json(Sample))


# ── Prompt assembly ───────────────────────────────────────────────────────────

def test_prompt_has_all_three_sections_in_order():
    p = llm.build_prompt(system="S", user="U", schema={"type": "object"})
    assert p.index("SYSTEM INSTRUCTIONS") < p.index("USER REQUEST") < p.index("SCHEMA")


def test_prompt_without_schema_omits_the_format_section():
    p = llm.build_prompt(system="S", user="U")
    assert "SCHEMA" not in p and "USER REQUEST" in p


# ── Validation and retry ──────────────────────────────────────────────────────

def _stub(monkeypatch, replies):
    """Feed run_once a queue of canned texts; record the prompts it was given."""
    seen = []

    def fake(*, backend, model, system, user, schema=None, thinking=None,
             binary=None, timeout=...):
        seen.append(user)
        return llm.Reply(replies[len(seen) - 1])

    monkeypatch.setattr(llm, "run_once", fake)
    return seen


def test_valid_first_try_makes_one_call(monkeypatch):
    seen = _stub(monkeypatch, ['{"title":"t","groups":[]}'])
    data, _ = llm.complete_schema(backend="openclaw", model="m", system="S", user="U",
                                  schema=Sample, thinking="low")
    assert data == {"title": "t", "groups": []}
    assert len(seen) == 1


def test_none_fields_are_dropped(monkeypatch):
    _stub(monkeypatch, ['{"title":"t","name":null,"groups":[]}'])
    data, _ = llm.complete_schema(backend="openclaw", model="m", system="S", user="U",
                                  schema=Sample, thinking="low")
    assert "name" not in data


def test_invalid_output_retries_once_and_succeeds(monkeypatch):
    seen = _stub(monkeypatch, ['{"groups":[]}',                    # missing `title`
                               '{"title":"t","groups":[]}'])
    data, _ = llm.complete_schema(backend="openclaw", model="m", system="S", user="U",
                                  schema=Sample, thinking="low")
    assert data["title"] == "t"
    assert len(seen) == 2
    assert "VALIDATION ERROR" in seen[1] and "PREVIOUS OUTPUT" in seen[1]


def test_two_failures_raise_and_name_the_error(monkeypatch):
    seen = _stub(monkeypatch, ['not json', 'still not json'])
    with pytest.raises(llm.LLMError, match="failed validation twice"):
        llm.complete_schema(backend="openclaw", model="m", system="S", user="U",
                            schema=Sample, thinking="low")
    assert len(seen) == 2  # exactly one retry, not an unbounded loop


def test_retry_callback_fires_once(monkeypatch):
    _stub(monkeypatch, ['bad', '{"title":"t","groups":[]}'])
    calls = []
    llm.complete_schema(backend="openclaw", model="m", system="S", user="U",
                        schema=Sample, thinking="low", on_retry=calls.append)
    assert len(calls) == 1


# ── Claude CLI backend ────────────────────────────────────────────────────────

# Trimmed from a real `claude -p --output-format json` run.
CLAUDE_DOC = {
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "stop_reason": "end_turn",
    "num_turns": 1,
    "result": '{"title":"t","groups":[]}',
    "modelUsage": {"claude-sonnet-5": {"inputTokens": 2, "outputTokens": 9}},
}


def test_claude_argv_is_pinned():
    argv = llm.claude_argv(binary="claude", model="claude-sonnet-5", system="SYS")
    assert argv == [
        "claude", "-p",
        "--model", "claude-sonnet-5",
        "--output-format", "json",
        "--system-prompt", "SYS",
        "--allowed-tools", "",
        "--strict-mcp-config",
        "--mcp-config", '{"mcpServers":{}}']


def test_claude_argv_never_leaves_tools_enabled():
    # Tools, MCP servers, and Claude Code's own system prompt all have to be off: this
    # is a completion, not a coding agent turn.
    argv = llm.claude_argv(binary="claude", model="m", system="SYS")
    assert argv[argv.index("--allowed-tools") + 1] == ""
    assert "--strict-mcp-config" in argv
    assert "--system-prompt" in argv
    assert "--dangerously-skip-permissions" not in argv


def test_claude_reply_from_real_shape():
    reply = llm.reply_from_claude(CLAUDE_DOC)
    assert reply.text == '{"title":"t","groups":[]}'
    assert reply.model == "claude-sonnet-5"
    assert reply.finish == "end_turn"


def test_claude_error_result_raises():
    doc = dict(CLAUDE_DOC, is_error=True, subtype="error_during_execution",
               result="Credit balance is too low")
    with pytest.raises(llm.LLMError, match="Credit balance"):
        llm.reply_from_claude(doc)


def test_claude_empty_result_raises():
    with pytest.raises(llm.LLMError, match="empty"):
        llm.reply_from_claude(dict(CLAUDE_DOC, result="  "))


def test_the_schema_travels_in_the_user_turn_for_both_backends():
    # OpenClaw gets one flat string; the Claude CLI gets the system prompt as a flag and
    # this as stdin. Either way the schema has to be in what the model reads.
    turn = llm.user_message("U", {"type": "object"})
    assert "SCHEMA" in turn and turn.startswith("U")
    assert llm.user_message("U") == "U"
