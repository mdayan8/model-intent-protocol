"""MCPMark filesystem tasks, two arms (direct MCP vs MIP gateway), scored by each task's verify.py.

  python3 bench/mcpmark/run.py [--trials N] [--jobs 4] [--only SUBSTR] [--arms direct,mip]

Each (task, arm, trial) gets its own copy of the fixture under runs/, its own CLI process and its
own MCP server. Results are appended to runs/log.jsonl (one line per run; reruns skip what is logged).
"""
import argparse, json, os, shlex, shutil, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
TASKS = HERE / "upstream" / "tasks" / "filesystem"
FIXTURES = HERE / "fixtures"
RUNS = HERE / "runs"
VENV_PY = HERE / ".venv" / "bin" / "python"
# MIP arms run a frozen copy of the gateway (and the kernel files it imports), never the live
# gateway/ which another session edits: snapshots/<arm>/ mirrors the repo layout. Make one with
#   python3 run.py --snapshot mip_v2
SNAP_FILES = ["gateway/mip_gateway.py", "mip.py", "fast.py", "bench/tokens.py"]
SNAPSHOTS = HERE / "snapshots"
MODEL = "claude-haiku-5-5"
# The gateway reads directories out of the intent by splitting on whitespace, and this checkout lives
# under ".../Application Support/...". A space-free symlink to runs/ is the path the model is given.
LINK = Path(os.environ.get("MCPMARK_LINK", "/tmp/mcpmark_runs"))

SYSTEM = ("You are an autonomous agent that completes tasks on the user's files using the tools provided. "
          "Finish the whole task without asking questions. "
          "If a tool takes an `intent` argument, pass the user's full request, word for word and including any paths, as `intent`.")
# MCPMark's own suffix (src/base/task_manager.py, _format_task_instruction)
NOTE = "\n\nNote: Based on your understanding, solve the task all at once by yourself, don't ask for my opinions on anything."


def tasks():
    for v in sorted(TASKS.glob("*/*/*/verify.py")):
        d = v.parent
        meta = json.loads((d / "meta.json").read_text())
        yield {"id": "/".join(d.relative_to(TASKS).parts), "dir": d, "category": meta["category_id"]}


def run_one(t, arm, trial):
    key = "%s__%s__t%d" % (t["id"].replace("/", "__"), arm, trial)
    base = RUNS / key
    shutil.rmtree(base, ignore_errors=True)
    base.mkdir(parents=True)
    work = base / t["category"]  # same dir name MCPMark uses (test_root / category_id)
    shutil.copytree(FIXTURES / t["category"], work, copy_function=shutil.copy2, symlinks=True)
    path = str(LINK / key / t["category"])  # what the model and the servers see
    fs = {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", path]}
    if arm == "direct":
        cfg, allowed = {"mcpServers": {"fs": fs}}, "mcp__fs__*"
    else:
        (base / "servers.json").write_text(json.dumps({"mcpServers": {"fs": fs}}))
        gw = SNAPSHOTS / arm / "gateway"
        cfg = {"mcpServers": {"mip": {"command": sys.executable,
                                      "args": [str(gw / "mip_gateway.py"), "serve", "--config", str(base / "servers.json")],
                                      # ask() runs `claude -p`; route it through a logger so its tokens are counted
                                      "env": {"MIP_READER_CMD": "%s %s %s" % (shlex.quote(sys.executable), shlex.quote(str(HERE / "reader_log.py")), shlex.quote(str(gw))),
                                              "MCPMARK_READER_LOG": str(base / "reader.jsonl")}}}}
        allowed = "mcp__mip__run"
        assert (SNAPSHOTS / arm).is_dir(), "no snapshot for arm " + arm
    (base / "mcp.json").write_text(json.dumps(cfg))
    prompt = (t["dir"] / "description.md").read_text() + NOTE + \
        "\n\nThe directory for this task is %s" % path
    (base / "prompt.txt").write_text(prompt)
    cmd = ["claude", "-p", "--model", MODEL, "--tools", "", "--strict-mcp-config", "--mcp-config", str(base / "mcp.json"),
           "--allowedTools", allowed, "--permission-mode", "bypassPermissions", "--setting-sources", "",
           "--output-format", "stream-json", "--verbose", "--no-session-persistence", "--system-prompt", SYSTEM, prompt]
    t0 = time.time()
    rec = {"task": t["id"], "arm": arm, "trial": trial, "dir": str(work)}
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=1200, cwd=base, stdin=subprocess.DEVNULL)
        (base / "cli.jsonl").write_text(p.stdout)  # full transcript: every tool call and reply
        (base / "cli.err").write_text(p.stderr)
        lines = [json.loads(l) for l in p.stdout.splitlines() if l.startswith("{")]
        out = next((l for l in reversed(lines) if l.get("type") == "result"), {})
        u = out.get("usage", {})
        rec.update(usage={k: u.get(k, 0) for k in ("input_tokens", "cache_read_input_tokens",
                                                     "cache_creation_input_tokens", "output_tokens")},
                   num_turns=out.get("num_turns"), duration_api_ms=out.get("duration_api_ms"),
                   total_cost_usd=out.get("total_cost_usd"), cli_subtype=out.get("subtype"),
                   cli_is_error=out.get("is_error"), result=(out.get("result") or "")[:2000],
                   error=None if p.returncode == 0 and not out.get("is_error") else (p.stderr[-500:] or out.get("subtype")))
    except subprocess.TimeoutExpired:
        rec.update(error="timeout 1200s")
    except json.JSONDecodeError:
        rec.update(error="non-JSON CLI output: " + p.stdout[-300:])
    rec["wall_s"] = round(time.time() - t0, 1)
    if (base / "reader.jsonl").exists():  # ask() calls made by the MIP gateway, a separate CLI each
        rd = [json.loads(l) for l in (base / "reader.jsonl").open()]
        ru = lambda k: sum((r.get("usage") or {}).get(k, 0) for r in rd)
        rec["reader"] = {"calls": len(rd), "cost_usd": sum(r.get("total_cost_usd") or 0 for r in rd),
                         "duration_api_ms": sum(r.get("duration_api_ms") or 0 for r in rd),
                         "usage": {k: ru(k) for k in ("input_tokens", "cache_read_input_tokens",
                                                      "cache_creation_input_tokens", "output_tokens")}}
    # Score exactly as MCPMark does: <python> verify.py with FILESYSTEM_TEST_DIR set, exit 0 = pass.
    v = subprocess.run([str(VENV_PY), str(t["dir"] / "verify.py")], capture_output=True, text=True, timeout=300,
                       env=dict(os.environ, FILESYSTEM_TEST_DIR=str(work)))
    (base / "verify.out").write_text(v.stdout + v.stderr)
    rec.update(passed=v.returncode == 0, verify_tail=(v.stdout + v.stderr)[-600:])
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=1)
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--only", default="")
    ap.add_argument("--arms", default="direct")
    ap.add_argument("--system", default="", help="file whose text replaces SYSTEM (e.g. for mip_v2)")
    ap.add_argument("--snapshot", help="copy the live gateway + kernel into snapshots/<name>/ and exit")
    a = ap.parse_args()
    if a.snapshot:
        for f in SNAP_FILES:
            (SNAPSHOTS / a.snapshot / f).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / f, SNAPSHOTS / a.snapshot / f)
        subprocess.run("shasum -a 256 " + " ".join(SNAP_FILES) + " > SHA256SUMS", shell=True, cwd=SNAPSHOTS / a.snapshot)
        return
    if a.system:
        global SYSTEM
        SYSTEM = Path(a.system).read_text().strip()
    RUNS.mkdir(exist_ok=True)
    if not LINK.is_symlink():
        LINK.symlink_to(RUNS)
    assert LINK.resolve() == RUNS.resolve(), LINK
    log = RUNS / "log.jsonl"
    done = {(r["task"], r["arm"], r["trial"]) for r in map(json.loads, log.open())} if log.exists() else set()
    todo = [(t, arm, k) for k in range(1, a.trials + 1) for t in tasks() if a.only in t["id"]
            for arm in a.arms.split(",") if (t["id"], arm, k) not in done]
    print("%d runs to do" % len(todo), flush=True)
    with ThreadPoolExecutor(a.jobs) as ex, log.open("a") as f:
        for rec in ex.map(lambda x: run_one(*x), todo):
            f.write(json.dumps(rec) + "\n"); f.flush()
            print("%-55s %-6s t%d %s turns=%s $%s %s" % (rec["task"], rec["arm"], rec["trial"],
                  "PASS" if rec["passed"] else "fail", rec.get("num_turns"), rec.get("total_cost_usd"),
                  rec.get("error") or ""), flush=True)


if __name__ == "__main__":
    main()
