"""Measure the real token cost of popular MCP servers' tool catalogs.
Starts each server over stdio, sends initialize + tools/list, saves the tools.
Run: python3 bench/mcp_catalogs.py   (needs node/npx; downloads packages from npm)
"""
import json, os, subprocess, sys, tempfile, time

SERVERS = {
    "github":              ["npx", "-y", "@modelcontextprotocol/server-github"],
    "filesystem":          ["npx", "-y", "@modelcontextprotocol/server-filesystem", tempfile.gettempdir()],
    "memory":              ["npx", "-y", "@modelcontextprotocol/server-memory"],
    "everything":          ["npx", "-y", "@modelcontextprotocol/server-everything"],
    "sequential-thinking": ["npx", "-y", "@modelcontextprotocol/server-sequential-thinking"],
    "puppeteer":           ["npx", "-y", "@modelcontextprotocol/server-puppeteer"],
    "playwright":          ["npx", "-y", "@playwright/mcp@latest"],
    "slack":               ["npx", "-y", "@modelcontextprotocol/server-slack"],
    "notion":              ["npx", "-y", "@notionhq/notion-mcp-server"],
    "context7":            ["npx", "-y", "@upstash/context7-mcp"],
}
ENV = dict(os.environ, GITHUB_PERSONAL_ACCESS_TOKEN="dummy", SLACK_BOT_TOKEN="xoxb-dummy",
           SLACK_TEAM_ID="T0", NOTION_TOKEN="dummy", OPENAPI_MCP_HEADERS="{}")

def rpc(p, msg):
    p.stdin.write((json.dumps(msg) + "\n").encode()); p.stdin.flush()

def read(p, want_id, deadline):
    while time.time() < deadline:
        line = p.stdout.readline()
        if not line:
            time.sleep(.05); continue
        try:
            m = json.loads(line)
        except ValueError:
            continue
        if m.get("id") == want_id:
            return m
    raise TimeoutError

def fetch(name, cmd, timeout=180):
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=ENV)
    try:
        dl = time.time() + timeout
        rpc(p, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "mip-bench", "version": "0"}}})
        init = read(p, 1, dl)
        rpc(p, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        tools, cursor = [], None
        while True:
            rpc(p, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {"cursor": cursor} if cursor else {}})
            r = read(p, 2, dl)["result"]
            tools += r["tools"]; cursor = r.get("nextCursor")
            if not cursor: break
        info = init["result"].get("serverInfo", {})
        return {"server": name, "package": cmd[2], "version": info.get("version"), "tools": tools}
    finally:
        p.kill()

if __name__ == "__main__":
    out = []
    for name, cmd in SERVERS.items():
        try:
            r = fetch(name, cmd); out.append(r)
            print("%-20s %3d tools  v%s" % (name, len(r["tools"]), r["version"]))
        except Exception as e:
            print("%-20s FAILED %s" % (name, type(e).__name__))
    json.dump(out, open(os.path.join(os.path.dirname(__file__), "results", "mcp_catalogs.json"), "w"), indent=1)
