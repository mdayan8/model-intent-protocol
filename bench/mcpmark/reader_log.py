"""MIP_READER_CMD wrapper: runs the snapshot gateway's own reader command unchanged, passes its stdout
through, and appends the reader's usage/cost to $MCPMARK_READER_LOG so ask() calls are counted.
  reader_log.py <snapshot gateway dir>"""
import json, os, subprocess, sys
os.environ.pop("MIP_READER_CMD", None)
sys.path.insert(0, sys.argv[1])
import mip_gateway  # noqa: E402
p = subprocess.run(mip_gateway.reader_cmd(), input=sys.stdin.read(), capture_output=True, text=True, cwd=os.getcwd())
sys.stdout.write(p.stdout)
try:
    out = json.loads(p.stdout)
    rec = {k: out.get(k) for k in ("usage", "total_cost_usd", "duration_api_ms", "num_turns", "is_error")}
except ValueError:
    rec = {"error": "non-JSON reader output", "rc": p.returncode}
with open(os.environ["MCPMARK_READER_LOG"], "a") as f:
    f.write(json.dumps(rec) + "\n")
sys.exit(p.returncode)
