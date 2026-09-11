"""Model calls, routed through a CLI you already pay for -- never a metered API key.

Two backends, picked automatically:

    openclaw   `openclaw agent --local`, which reaches whatever models are bound in
               your ~/.openclaw config. That includes the gpt-5.6-* line (sol/terra/luna)
               and the Claude line, so one path covers everything worth running.
    claude     `claude -p`, the Claude Code CLI, billed to your Claude subscription.

Detection order (see `detect_backend`): OpenClaw wins when both are installed, because
its default preset (terra) is the one this project benchmarked best on. With only the
Claude CLI present the default preset is sonnet (claude-sonnet-5). Override any of it
with --provider / --preset / --model.

What each path costs you before your prompt is even read:

    openclaw   ~25-47k tokens of preamble -- the runtime's system prompt, the workspace
               files (~/.openclaw/workspace/{AGENTS,SOUL,TOOLS}.md), and the schemas for
               its nodes/cron/message tools. Those workspace files are a persona, and
               resume prose is exactly the kind of output a persona colors. If one
               preset's writing reads oddly against another's, suspect that before you
               suspect the rules in tailor.py.
    claude     a Claude Code session preamble (~13k tokens observed). We pass
               --system-prompt to replace the coding-agent system prompt, and disable
               tools and MCP servers, so the model does nothing but answer.

Neither backend has a structured-output flag, so the JSON schema is appended to the
prompt as text and the reply is validated here, with one retry. That is strictly weaker
than a `messages.parse` guarantee: a model that ignores the schema fails at validation
rather than being prevented from doing so.
"""
from __future__ import annotations

import copy
import json
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
from typing import Any, Optional

from pydantic import BaseModel, ValidationError


class LLMError(RuntimeError):
    """Any failure of a model call: spawn, timeout, refusal, or unusable output."""


# ── Backends and presets ──────────────────────────────────────────────────────
#
# OpenClaw: the provider prefix is mandatory. A bare `claude-opus-5` resolves to
# `openai/claude-opus-5` and errors. Every entry here must also be bound in your
# agents.defaults.models, or the call fails with "Model override ... is not allowed
# for agent" -- agent "main" refuses anything it does not know, which is why the cheap
# Claude slots below are opus-4-8 and sonnet-4-6 rather than a haiku.
#
# Claude CLI: plain model ids or aliases, exactly what `claude --model` accepts.

OPENCLAW_PRESETS: dict[str, str] = {
    "opus":       "anthropic/claude-opus-5",
    "sonnet":     "anthropic/claude-sonnet-5",
    "opus-4-8":   "anthropic/claude-opus-4-8",
    "sonnet-4-6": "anthropic/claude-sonnet-4-6",
    "sol":        "openai/gpt-5.6-sol",
    "terra":      "openai/gpt-5.6-terra",
    "luna":       "openai/gpt-5.6-luna",
}

CLAUDE_PRESETS: dict[str, str] = {
    "opus":   "claude-opus-5",
    "sonnet": "claude-sonnet-5",
    "haiku":  "claude-haiku-4-5",
    "fable":  "claude-fable-5",
}

PRESETS_BY_BACKEND: dict[str, dict[str, str]] = {
    "openclaw": OPENCLAW_PRESETS,
    "claude": CLAUDE_PRESETS,
}

BACKENDS = tuple(PRESETS_BY_BACKEND)

DEFAULT_BINARY = {"openclaw": "openclaw", "claude": "claude"}

# terra was the fastest clean run in a 7-preset sweep on a graphic-design/web hybrid JD
# (bench.py, 2026-08-31): 39.7s with zero integrity or rule findings, and it over-generated
# the most (19 bullets, 97% page fill), which is what the one-page trimmer wants. On the
# Claude CLI the equivalent default is sonnet. Re-run bench.py before changing either.
DEFAULT_PRESET = {"openclaw": "terra", "claude": "sonnet"}

# OpenClaw validates against a wider set (off/minimal/low/medium/high/adaptive/xhigh/max);
# these are the three worth sweeping for this task. The Claude CLI has no equivalent flag,
# so the level is recorded and ignored there.
THINKING_LEVELS = ("low", "medium", "high")

DEFAULT_THINKING = "medium"
DEFAULT_TIMEOUT = 900.0

# Stripped from an OpenClaw child so its own stored credentials decide which account is
# billed. A stray ANTHROPIC_API_KEY would silently route the run back to metered API
# billing -- the exact thing this module exists to avoid. The Claude CLI is left alone:
# an API key there may be the only credential a user has.
_STRIPPED_ENVS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
                  "OPENAI_API_KEY", "OPENAI_API_KEYS", "OPENCLAW_LIVE_OPENAI_KEY")


def available_backends(which=shutil.which) -> list[str]:
    """Backends whose CLI is actually on PATH, in preference order."""
    return [b for b in BACKENDS if which(DEFAULT_BINARY[b])]


def detect_backend(which=shutil.which) -> str:
    """OpenClaw if installed, else the Claude CLI. Both installed -> OpenClaw (terra)."""
    found = available_backends(which)
    if not found:
        raise LLMError(
            "no supported CLI found on PATH. Install one of:\n"
            "  - Claude Code:  npm install -g @anthropic-ai/claude-code   (then `claude` to log in)\n"
            "  - OpenClaw:     see its own install docs\n"
            "Then re-run, or pass --binary with an explicit path.")
    return found[0]


def resolve(preset: Optional[str] = None, model: Optional[str] = None,
            backend: Optional[str] = None) -> tuple[str, str]:
    """(backend, model id) from an optional preset name and/or explicit model override."""
    backend = backend or detect_backend()
    if backend not in PRESETS_BY_BACKEND:
        raise LLMError(f"unknown provider {backend!r}; expected one of {', '.join(BACKENDS)}")

    if model:
        if backend == "openclaw" and "/" not in model:
            raise LLMError(
                f"--model {model!r} needs a provider prefix (anthropic/... or openai/...) "
                "when running through OpenClaw, which resolves a bare id against the "
                "default provider and errors.")
        return backend, model

    presets = PRESETS_BY_BACKEND[backend]
    name = preset or DEFAULT_PRESET[backend]
    if name not in presets:
        raise LLMError(
            f"preset {name!r} is not available on the {backend} backend. "
            f"Valid presets there: {', '.join(presets)}.")
    return backend, presets[name]


# ── Score tier: the model step B runs on, one notch below the generation model ──
SCORE_TIER: dict[str, dict[str, str]] = {
    "openclaw": {"opus": "sonnet", "sonnet": "luna", "opus-4-8": "sonnet-4-6",
                 "sonnet-4-6": "luna", "sol": "luna", "terra": "luna", "luna": "luna"},
    "claude":   {"opus": "sonnet", "sonnet": "haiku", "haiku": "haiku", "fable": "haiku"},
}
# The cheapest usable preset per backend: the fallback score tier, and what a setup
# connectivity probe should spend.
CHEAPEST_PRESET = {"openclaw": "luna", "claude": "haiku"}
_CHEAPEST_PRESET = CHEAPEST_PRESET   # older name, kept for callers still using it


def score_model(backend, run_preset, run_model_id, *, override_preset=None):
    """(backend, resolvable model id) for the pre-score call.

    override_preset wins. Else one tier below run_preset per SCORE_TIER. If the run
    pinned an explicit --model (run_model_id set, run_preset None), score on the
    backend's cheapest preset rather than trying to demote an arbitrary id.
    """
    if override_preset:
        return resolve(override_preset, backend=backend)
    if run_preset:
        tier = SCORE_TIER.get(backend, {})
        return resolve(tier.get(run_preset, _CHEAPEST_PRESET[backend]), backend=backend)
    return resolve(_CHEAPEST_PRESET[backend], backend=backend)


# ── Schema flattening ─────────────────────────────────────────────────────────
#
# pydantic emits $ref/$defs. A model reading the schema out of a prompt does better
# with it inlined than with references it has to resolve itself.

def flatten_schema(schema: dict) -> dict:
    defs = schema.get("$defs", {})

    def walk(node: Any, seen: tuple = ()) -> Any:
        if isinstance(node, list):
            return [walk(n, seen) for n in node]
        if not isinstance(node, dict):
            return node
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/$defs/"):
            name = ref.split("/")[-1]
            if name in seen:            # self-referential schema: leave the ref alone
                return {"type": "object"}
            target = defs.get(name)
            if target is None:
                return node
            merged = walk(copy.deepcopy(target), seen + (name,))
            merged.update({k: v for k, v in node.items() if k != "$ref"})
            return merged
        return {k: walk(v, seen) for k, v in node.items() if k != "$defs"}

    return walk(copy.deepcopy(schema))


def schema_json(schema: type[BaseModel]) -> dict:
    return flatten_schema(schema.model_json_schema())


# ── Prompt assembly ───────────────────────────────────────────────────────────

SCHEMA_INSTRUCTION = (
    "OUTPUT FORMAT (this is enforced by validation, not by the model -- get it right):\n"
    "Return one JSON object matching the schema below, and nothing else. No Markdown "
    "fences, no preamble, no trailing commentary. Omit optional fields rather than "
    "emitting null for them, except where an instruction above explicitly calls for null.\n"
    "SCHEMA:\n")


def user_message(user: str, schema: Optional[dict] = None) -> str:
    """The turn the model answers. Carries the schema, which both backends send as text."""
    if schema is None:
        return user
    return user + "\n\n" + SCHEMA_INSTRUCTION + json.dumps(schema, separators=(",", ":"))


def build_prompt(*, system: str, user: str, schema: Optional[dict] = None) -> str:
    """One flat prompt, for OpenClaw: its agent takes a single --message string."""
    parts = []
    if system:
        parts.append("SYSTEM INSTRUCTIONS:\n" + system)
    parts.append("USER REQUEST:\n" + user_message(user, schema))
    return "\n\n".join(parts)


def session_key(prefix: str = "resume-tex") -> str:
    """A fresh key per run: reusing one would carry the previous JD into this generation.

    The random suffix matters -- a bare millisecond timestamp collides between the two
    calls of a retry, which would put the corrected attempt in the failed one's session.
    """
    return f"agent:main:{prefix}-{int(time.time() * 1000)}-{secrets.token_hex(3)}"


def child_env(backend: str, parent: Optional[dict] = None) -> dict:
    parent = os.environ if parent is None else parent
    if backend != "openclaw":
        return dict(parent)
    return {k: v for k, v in parent.items() if k not in _STRIPPED_ENVS}


# ── Command lines ─────────────────────────────────────────────────────────────
#
# Pure, so the golden tests can pin them without spawning anything.

def agent_argv(*, binary: str, model: str, prompt: str, thinking: Optional[str],
               key: str) -> list[str]:
    # No --deliver. The runtime carries a `message` tool, and a resume is not something
    # we want it deciding to send anywhere.
    argv = [binary, "agent", "--local", "--model", model, "--json",
            "--session-key", key, "--message", prompt]
    if thinking:
        argv += ["--thinking", thinking]
    return argv


def claude_argv(*, binary: str, model: str, system: str) -> list[str]:
    """`claude -p` as a plain completion: no tools, no MCP, no coding-agent preamble.

    The user turn goes in on stdin rather than argv -- a full CV plus JD plus schema is
    tens of kilobytes, and stdin has no length limit to worry about.
    --system-prompt REPLACES Claude Code's own system prompt, which is the point: none of
    its tool and repository instructions have anything to do with writing a resume.
    """
    return [binary, "-p",
            "--model", model,
            "--output-format", "json",
            "--system-prompt", system,
            "--allowed-tools", "",
            "--strict-mcp-config",
            "--mcp-config", '{"mcpServers":{}}']


# ── Result parsing ────────────────────────────────────────────────────────────

class Reply:
    def __init__(self, text: str, *, model: str = "", finish: Optional[str] = None):
        self.text = text
        self.model = model
        self.finish = finish


def reply_from_agent(doc: dict) -> Reply:
    """Parse `openclaw agent --json` output."""
    meta = doc.get("meta") if isinstance(doc.get("meta"), dict) else {}
    completion = meta.get("completion") if isinstance(meta.get("completion"), dict) else {}
    agent_meta = meta.get("agentMeta") if isinstance(meta.get("agentMeta"), dict) else {}

    if completion.get("refusal"):
        raise LLMError("the model declined to answer (refusal). Try another preset, or "
                       "check whether the JD text tripped a safety classifier.")

    payloads = doc.get("payloads")
    text = ""
    if isinstance(payloads, list) and payloads and isinstance(payloads[0], dict):
        text = payloads[0].get("text") or ""
    if not text.strip():
        text = meta.get("finalAssistantVisibleText") or meta.get("finalAssistantRawText") or ""
    if not isinstance(text, str) or not text.strip():
        raise LLMError("OpenClaw agent returned an empty reply")

    return Reply(text, model=str(agent_meta.get("model") or ""),
                 finish=completion.get("finishReason"))


def reply_from_claude(doc: dict) -> Reply:
    """Parse `claude -p --output-format json` output."""
    text = doc.get("result")
    if doc.get("is_error") or doc.get("subtype") not in (None, "success"):
        detail = text if isinstance(text, str) else json.dumps(doc)[:400]
        raise LLMError(f"claude reported an error: {str(detail)[:500]}")
    if not isinstance(text, str) or not text.strip():
        raise LLMError("claude returned an empty reply")

    usage = doc.get("modelUsage")
    model = next(iter(usage), "") if isinstance(usage, dict) else ""
    return Reply(text, model=str(model), finish=doc.get("stop_reason"))


# ── JSON extraction ───────────────────────────────────────────────────────────
#
# A model told to skip the fences still sometimes emits them.

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.M)


def json_from(text: str) -> Any:
    cleaned = _FENCE.sub("", text).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        i, j = cleaned.find("{"), cleaned.rfind("}")
        if i != -1 and j > i:
            try:
                return json.loads(cleaned[i:j + 1])
            except json.JSONDecodeError:
                pass
        raise LLMError(f"unparseable model output: {cleaned[:300]}")


# ── Execution ─────────────────────────────────────────────────────────────────

# Every model subprocess currently running, so the shell's double-Esc (via
# terminate_active) can kill a call in progress. subprocess.run gives no handle
# to do that, hence the Popen below.
_active_procs: "set[subprocess.Popen]" = set()
_active_lock = threading.Lock()


def terminate_active() -> int:
    """SIGTERM every model subprocess in flight. Returns how many were signalled.

    The killed call surfaces as an LLMError up its own stack; the shell pairs this
    with a KeyboardInterrupt into the worker so the whole command unwinds, not
    just the one call."""
    with _active_lock:
        procs = list(_active_procs)
    for p in procs:
        try:
            p.terminate()
        except Exception:                       # already gone
            pass
    return len(procs)


def _spawn(argv, *, backend: str, timeout: float, stdin: str = "") -> subprocess.CompletedProcess:
    binary = argv[0]
    proc = None
    try:
        # A temp cwd keeps either CLI from treating the project directory as a workspace
        # it should go reading (Claude Code would pick up a CLAUDE.md from it).
        with tempfile.TemporaryDirectory(prefix="resume-tex-") as cwd:
            proc = subprocess.Popen(
                argv, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, env=child_env(backend))
            with _active_lock:
                _active_procs.add(proc)
            try:
                out, err = proc.communicate(input=stdin, timeout=timeout)
            finally:
                with _active_lock:
                    _active_procs.discard(proc)
            return subprocess.CompletedProcess(argv, proc.returncode, out, err)
    except FileNotFoundError as e:
        raise LLMError(f"{backend} binary {binary!r} not found. Install it, or pass "
                       "--binary with its path.") from e
    except subprocess.TimeoutExpired as e:
        if proc is not None:
            proc.kill()
            proc.communicate()
        raise LLMError(f"{binary} timed out after {timeout:.0f}s. Raise --timeout, or drop "
                       "--thinking to a lower level.") from e


def run_once(*, backend: str, model: str, system: str, user: str,
             schema: Optional[dict] = None, thinking: Optional[str] = None,
             binary: Optional[str] = None, timeout: float = DEFAULT_TIMEOUT) -> Reply:
    binary = binary or DEFAULT_BINARY[backend]

    if backend == "openclaw":
        argv = agent_argv(binary=binary, model=model,
                          prompt=build_prompt(system=system, user=user, schema=schema),
                          thinking=thinking, key=session_key())
        stdin = ""
    elif backend == "claude":
        argv = claude_argv(binary=binary, model=model, system=system)
        stdin = user_message(user, schema)
    else:
        raise LLMError(f"unknown provider {backend!r}")

    proc = _spawn(argv, backend=backend, timeout=timeout, stdin=stdin)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[-500:]
        raise LLMError(f"{binary} exited {proc.returncode}: {detail}")

    try:
        doc = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise LLMError(f"{binary} returned no JSON document: "
                       f"{(proc.stdout or '').strip()[:300]}") from e

    return reply_from_agent(doc) if backend == "openclaw" else reply_from_claude(doc)


def complete_text(*, backend: str, model: str, system: str, user: str,
                  thinking: Optional[str] = None, binary: Optional[str] = None,
                  timeout: float = DEFAULT_TIMEOUT) -> Reply:
    """A plain prose completion -- no schema, no validation, no retry."""
    return run_once(backend=backend, model=model, system=system, user=user,
                    thinking=thinking, binary=binary, timeout=timeout)


def complete_schema(*, backend: str, model: str, system: str, user: str,
                    schema: type[BaseModel], thinking: Optional[str] = None,
                    binary: Optional[str] = None, timeout: float = DEFAULT_TIMEOUT,
                    on_retry=None) -> tuple[dict, Reply]:
    """Validated JSON, with one correction round.

    The retry is a fresh call carrying the bad output and the validator's complaint --
    not a continuation, so it starts a new session rather than inheriting the failed turn.
    """
    js = schema_json(schema)
    turn = user

    last_error = last_text = ""
    for attempt in (1, 2):
        reply = run_once(backend=backend, model=model, system=system, user=turn,
                         schema=js, thinking=thinking, binary=binary, timeout=timeout)
        try:
            data = json_from(reply.text)
            return schema.model_validate(data).model_dump(exclude_none=True), reply
        except (LLMError, ValidationError) as e:
            last_error, last_text = str(e), reply.text
            if attempt == 2:
                break
            if on_retry:
                on_retry(last_error)
            turn = (user + "\n\nA previous attempt produced output that failed validation. "
                    "Return corrected JSON only.\n"
                    f"PREVIOUS OUTPUT:\n{last_text[:4000]}\n"
                    f"VALIDATION ERROR:\n{last_error[:2000]}")

    raise LLMError(
        f"model output failed validation twice.\nLast error: {last_error[:1500]}\n"
        f"Last output: {last_text[:500]}")
