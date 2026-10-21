"""Print the tool calls and replies of one run: python3 trace.py runs/<key>"""
import json, sys
for l in open(sys.argv[1] + "/cli.jsonl"):
    m = json.loads(l)
    if m.get("type") in ("assistant", "user"):
        for c in m["message"]["content"]:
            if c.get("type") == "tool_use": print("CALL", json.dumps(c["input"])[:800])
            if c.get("type") == "tool_result": print("  ->", str(c["content"])[:500])
            if c.get("type") == "text": print("TEXT", c["text"][:300])
