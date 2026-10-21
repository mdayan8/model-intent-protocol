"""An OpenAI-shaped client that answers through the `claude -p` CLI.

Both benchmark arms use this same client, so they see the same model under the
same settings. Every call is isolated: our own system prompt replaces the
default one, no built-in tools, no MCP servers, no user or project settings.

Auth: if ANTHROPIC_API_KEY is set, the CLI runs in --bare mode with that key.
Otherwise it uses the CLI's own login (`claude auth login`).

Every call is appended to $MIP_CALL_LOG (JSON lines) with tokens and latency.
"""
import json, os, subprocess, tempfile, time
from types import SimpleNamespace

MODEL = os.environ.get("MIP_MODEL", "claude-haiku-5-5")
CALL_LOG = os.environ.get("MIP_CALL_LOG")
_EMPTY_MCP = os.path.join(tempfile.gettempdir(), "mip_empty_mcp.json")
_NEUTRAL_CWD = tempfile.mkdtemp(prefix="mip_cli_")


class CLIError(RuntimeError):
    pass


def _text(content):
    if isinstance(content, str):
        return content
    return "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content or [])


def complete(system, messages, model=MODEL, tag="", timeout=300):
    """messages: list of {"role": "user"|"assistant", "content": str}. Returns (text, usage dict)."""
    if not os.path.exists(_EMPTY_MCP):
        with open(_EMPTY_MCP, "w") as f:
            f.write('{"mcpServers":{}}')
    # Render prior turns as a transcript; the last user turn is the live prompt.
    turns = []
    for m in messages[:-1]:
        turns.append("<%s>\n%s\n</%s>" % (m["role"], _text(m["content"]), m["role"]))
    prompt = _text(messages[-1]["content"])
    if turns:
        prompt = "Conversation so far:\n" + "\n".join(turns) + "\n\nLatest message from the user side:\n" + prompt
    cmd = ["claude", "-p", "--model", model, "--tools", "", "--strict-mcp-config", "--mcp-config", _EMPTY_MCP,
           "--setting-sources", "", "--output-format", "json", "--no-session-persistence",
           "--system-prompt", system or "You are a helpful assistant."]
    if os.environ.get("ANTHROPIC_API_KEY"):
        cmd.insert(2, "--bare")
    t0 = time.time()
    p = subprocess.run(cmd, input=prompt, capture_output=True, text=True, cwd=_NEUTRAL_CWD, timeout=timeout)
    dt = time.time() - t0
    try:
        out = json.loads(p.stdout)
    except ValueError:
        raise CLIError("CLI returned non-JSON (exit %d): %s" % (p.returncode, (p.stderr or p.stdout)[:300]))
    if out.get("is_error"):
        raise CLIError("CLI error: %s" % str(out.get("result"))[:300])
    u = out.get("usage", {})
    usage = {"input": u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0) + u.get("cache_creation_input_tokens", 0),
             "output": u.get("output_tokens", 0), "seconds": round(dt, 3),
             "api_seconds": out.get("duration_api_ms", 0) / 1000, "cost_usd": out.get("total_cost_usd", 0)}
    if CALL_LOG:
        with open(CALL_LOG, "a") as f:
            f.write(json.dumps({"tag": tag, "model": model, **usage}) + "\n")
    return out.get("result", ""), usage


class _Completions:
    def __init__(self, tag):
        self.tag = tag

    def create(self, model=MODEL, messages=(), **_ignored):
        system = "\n\n".join(_text(m["content"]) for m in messages if m["role"] == "system")
        rest = [{"role": "assistant" if m["role"] == "assistant" else "user", "content": _text(m["content"])}
                for m in messages if m["role"] != "system"]
        text, usage = complete(system, rest, model=model, tag=self.tag)
        msg = SimpleNamespace(content=text, tool_calls=None, role="assistant")
        return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason="stop")],
                               usage=SimpleNamespace(prompt_tokens=usage["input"], completion_tokens=usage["output"]))


class ClaudeCLIClient:
    """Drop-in for openai.OpenAI() where only chat.completions.create is used."""
    def __init__(self, tag=""):
        self.chat = SimpleNamespace(completions=_Completions(tag))


def check():
    """Fails fast with a clear message if the CLI cannot reach the model."""
    try:
        text, usage = complete("Reply with exactly: ok", [{"role": "user", "content": "ping"}], tag="healthcheck")
        return True, "model reachable: %r, %s" % (text.strip()[:20], usage)
    except Exception as e:
        return False, str(e)


if __name__ == "__main__":
    ok, msg = check()
    print("OK" if ok else "NOT READY", msg)
