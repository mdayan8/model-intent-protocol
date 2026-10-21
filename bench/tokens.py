"""Token cost of real MCP tool catalogs vs the MIP prompt.
Input : bench/results/mcp_catalogs.json (from mcp_catalogs.py)
Output: bench/results/tokens.json
Tokenizer: tiktoken o200k_base as a proxy; Claude's tokenizer differs by a few percent.
"""
import json, os, re, statistics, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import fast  # noqa: E402

tok = fast.tokens

def as_api_tool(t):
    """The shape a client sends to the model API for each MCP tool."""
    return {"name": t["name"], "description": t.get("description", ""), "input_schema": t.get("inputSchema", {})}

def signature(t):
    """MIP's one-line form: name(required, optional?)."""
    s = t.get("inputSchema", {}) or {}
    props, req = s.get("properties", {}) or {}, set(s.get("required", []) or [])
    args = [p if p in req else p + "?" for p in props]
    return "%s(%s)" % (t["name"], ",".join(args))

def main():
    cats = json.load(open(os.path.join(HERE, "results", "mcp_catalogs.json")))
    rows, per_tool, sig_tokens = [], [], []
    for c in cats:
        defs = [as_api_tool(t) for t in c["tools"]]
        n = tok(json.dumps(defs, separators=(",", ":")))
        each = [tok(json.dumps(d, separators=(",", ":"))) for d in defs]
        per_tool += each
        sig_tokens += [tok(signature(t)) for t in c["tools"]]
        rows.append({"server": c["server"], "package": c["package"], "version": c["version"],
                     "tools": len(defs), "tokens": n, "max_tool": max(each), "median_tool": statistics.median(each)})
    rows.sort(key=lambda r: -r["tokens"])
    total = sum(r["tokens"] for r in rows)
    ntools = sum(r["tools"] for r in rows)

    # MIP: one tool plus the 3 signatures retrieved for the task (worst case: the 3 longest)
    longest3 = sorted(sig_tokens)[-3:]
    mip_fixed = tok(json.dumps(fast.TOOL, separators=(",", ":")))
    mip_worst = mip_fixed + sum(longest3) + 2
    out = {
        "tokenizer": fast.TOKENIZER,
        "servers": rows,
        "all_servers": {"tools": ntools, "tokens": total},
        "per_tool": {"mean": statistics.mean(per_tool), "median": statistics.median(per_tool),
                     "max": max(per_tool), "min": min(per_tool)},
        "signature_per_tool": {"mean": statistics.mean(sig_tokens), "median": statistics.median(sig_tokens),
                               "max": max(sig_tokens)},
        "mip": {"fixed_tool_tokens": mip_fixed, "worst_case_with_3_signatures": mip_worst},
        "reduction_all_servers": total / mip_worst,
    }
    json.dump(out, open(os.path.join(HERE, "results", "tokens.json"), "w"), indent=1)
    print("%-20s %6s %8s %9s" % ("server", "tools", "tokens", "max/tool"))
    for r in rows:
        print("%-20s %6d %8d %9d" % (r["server"], r["tools"], r["tokens"], r["max_tool"]))
    print("%-20s %6d %8d" % ("ALL 10 SERVERS", ntools, total))
    print("\nper MCP tool : mean %.0f, median %.0f, max %d tokens" % (out["per_tool"]["mean"], out["per_tool"]["median"], out["per_tool"]["max"]))
    print("per MIP sig  : mean %.1f, max %d tokens" % (out["signature_per_tool"]["mean"], out["signature_per_tool"]["max"]))
    print("MIP prompt   : %d fixed, %d worst case with 3 signatures" % (mip_fixed, mip_worst))
    print("reduction    : %.0fx against all 10 servers loaded" % out["reduction_all_servers"])

if __name__ == "__main__":
    main()
