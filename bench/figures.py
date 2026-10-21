"""Build every figure in docs/figures from the saved results. No model calls.
Run: python3 bench/figures.py
"""
import json, math, os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = os.path.dirname(os.path.abspath(__file__))
RES = os.path.join(HERE, "results")
OUT = os.path.join(os.path.dirname(HERE), "docs", "figures")
os.makedirs(OUT, exist_ok=True)
GREY, ORANGE, BLUE, GREEN = "#8a8a8a", "#d0532b", "#3b6ea8", "#3c8d5a"
ARM_LABEL = {"baseline": "Classic tool calling", "fence": "MIP Fence (strict)", "fence-dir": "MIP Fence (directory)",
             "mip": "MIP Plan (strict)", "mip-dir": "MIP Plan (directory)"}
ARM_COLOR = {"baseline": GREY, "fence": BLUE, "fence-dir": ORANGE, "mip": GREEN, "mip-dir": "#7aa874"}


def wilson(k, n, z=1.96):
    if n == 0:
        return 0, 0, 0
    p = k / n; d = 1 + z * z / n; c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return p, c - h, c + h


def load(arm, attack):
    d = {}
    base = os.path.join(RES, "agentdojo", arm)
    for s in ["banking", "slack", "travel", "workspace"]:
        p = os.path.join(base, s, attack + ".jsonl")
        if os.path.exists(p):
            for l in open(p):
                r = json.loads(l)
                d[(r["suite"], r["user_task"], r["injection_task"])] = r
    return list(d.values())


def style(ax, title):
    ax.set_title(title, loc="left", fontsize=11)
    for s in ["top", "right"]:
        ax.spines[s].set_visible(False)


def bars(ax, names, vals, lo, hi, colors, fmt="%.0f%%"):
    y = range(len(names))
    ax.barh(list(y), vals, color=colors, xerr=[[v - l for v, l in zip(vals, lo)], [h - v for v, h in zip(vals, hi)]],
            error_kw={"elinewidth": 1, "capsize": 3, "ecolor": "#333"})
    ax.set_yticks(list(y)); ax.set_yticklabels(names); ax.invert_yaxis()
    for i, v in enumerate(vals):
        ax.text(min(hi[i], 100) + 1.5, i, fmt % v, va="center", fontsize=9)


def fig_tokens():
    t = json.load(open(os.path.join(RES, "tokens.json")))
    rows = t["servers"]
    fig, ax = plt.subplots(figsize=(8, 4.6), dpi=160)
    ax.barh([r["server"] for r in rows][::-1], [r["tokens"] for r in rows][::-1], color=GREY)
    m = t["mip"]["worst_case_with_3_signatures"]
    ax.axvline(m, color=ORANGE, lw=2)
    ax.text(m * 1.15, 0.2, "MIP prompt, worst case: %d tokens" % m, color=ORANGE, fontsize=9)
    ax.set_xscale("log"); ax.set_xlabel("Tool-definition tokens the model reads before any work (log scale)")
    style(ax, "10 real MCP servers vs the MIP prompt (%d tokens for all 10)" % t["all_servers"]["tokens"])
    fig.tight_layout(); fig.savefig(os.path.join(OUT, "tokens_per_server.png")); plt.close(fig)


def fig_utility():
    arms = ["baseline", "fence-dir", "fence", "mip-dir", "mip"]
    names, vals, lo, hi, cols = [], [], [], [], []
    for a in arms:
        rs = load(a, "none")
        if not rs:
            continue
        p, l, h = wilson(sum(r["utility"] for r in rs), len(rs))
        names.append("%s (n=%d)" % (ARM_LABEL[a], len(rs))); vals.append(100 * p); lo.append(100 * l); hi.append(100 * h)
        cols.append(ARM_COLOR[a])
    fig, ax = plt.subplots(figsize=(8, 3.6), dpi=160)
    bars(ax, names, vals, lo, hi, cols)
    ax.set_xlim(0, 110); ax.set_xlabel("Tasks completed, no attack (%), 95% Wilson interval")
    style(ax, "AgentDojo v1.2.2, Claude Haiku 5.5: tasks completed")
    fig.tight_layout(); fig.savefig(os.path.join(OUT, "agentdojo_utility.png")); plt.close(fig)


def fig_attacks():
    arms = ["baseline", "fence-dir", "fence"]
    fig, axes = plt.subplots(1, 2, figsize=(10, 3.4), dpi=160)
    for ax, key, title in [(axes[0], "attack_success", "Attacks that succeeded (%)"),
                           (axes[1], "utility", "Tasks still completed under attack (%)")]:
        names, vals, lo, hi, cols = [], [], [], [], []
        for a in arms:
            rs = load(a, "important_instructions")
            if not rs:
                continue
            p, l, h = wilson(sum(bool(r[key]) for r in rs), len(rs))
            names.append("%s (n=%d)" % (ARM_LABEL[a], len(rs))); vals.append(100 * p); lo.append(100 * l); hi.append(100 * h)
            cols.append(ARM_COLOR[a])
        bars(ax, names, vals, lo, hi, cols, fmt="%.1f%%")
        ax.set_xlim(0, 110 if key == "utility" else max(hi + [5]) * 1.4)
        style(ax, title)
    fig.suptitle("AgentDojo important_instructions attack, real model (Claude Haiku 5.5)", x=0.01, ha="left", fontsize=11)
    fig.tight_layout(); fig.savefig(os.path.join(OUT, "agentdojo_attacks_real_model.png")); plt.close(fig)


def fig_hijack():
    p = os.path.join(RES, "agentdojo", "hijack.json")
    if not os.path.exists(p):
        return
    h = json.load(open(p))
    cats = h.get("categories")
    fig, ax = plt.subplots(figsize=(8, 3.4), dpi=160)
    labels = {"none": "Classic tool calling", "fence": "MIP Fence (strict)", "fence-dir": "MIP Fence (directory)"}
    cols = {"none": GREY, "fence": BLUE, "fence-dir": ORANGE}
    names, vals, lo, hi, cc = [], [], [], [], []
    for k in ["none", "fence-dir", "fence"]:
        ex = cats[k]["data_to_destination"]
        pp, l, hh = wilson(ex[1], ex[0])
        names.append("%s (n=%d)" % (labels[k], ex[0])); vals.append(100 * pp); lo.append(100 * l); hi.append(100 * hh)
        cc.append(cols[k])
    bars(ax, names, vals, lo, hi, cc, fmt="%.1f%%")
    ax.set_xlim(0, 115); ax.set_xlabel("Data-to-destination attacks that succeeded (%)")
    style(ax, "Fully hijacked agent (no model): every attacker call is attempted")
    fig.tight_layout(); fig.savefig(os.path.join(OUT, "hijacked_agent.png")); plt.close(fig)


def fig_overhead():
    g = json.load(open(os.path.join(os.path.dirname(HERE), "gateway", "results.json")))
    t = g["tokens"]
    fig, axes = plt.subplots(1, 2, figsize=(10, 3.0), dpi=160)
    ax = axes[0]
    ax.barh(["Direct MCP (%d tools)" % t["direct"]["tools"], "Through MIP gateway"],
            [t["direct"]["api_tools"], t["gateway"]["api_tools"]], color=[GREY, ORANGE])
    for i, v in enumerate([t["direct"]["api_tools"], t["gateway"]["api_tools"]]):
        ax.text(v + 60, i, "%d" % v, va="center", fontsize=9)
    ax.invert_yaxis(); style(ax, "Tool-definition tokens, 4 real servers")
    ax = axes[1]
    lat = g.get("latency") or {}
    rows = []
    for k, v in lat.items():
        if isinstance(v, dict) and "overhead_ms" in v:
            rows.append((k, v["overhead_ms"]["median"], v["overhead_ms"]["p95"]))
    if rows:
        ax.barh([r[0] for r in rows], [r[1] for r in rows], color=ORANGE,
                xerr=[[0] * len(rows), [max(0, r[2] - r[1]) for r in rows]], error_kw={"capsize": 3})
        ax.invert_yaxis()
    ax.set_xlabel("ms added per tool call (median, bar to p95)")
    style(ax, "Gateway overhead per call")
    fig.tight_layout(); fig.savefig(os.path.join(OUT, "gateway_overhead.png")); plt.close(fig)


def fig_mcpmark():
    p = os.path.join(HERE, "mcpmark", "results.json")
    if not os.path.exists(p):
        return
    r = json.load(open(p))
    s = r.get("summary") or {}
    arms = [a for a in ["direct", "mip_v1", "mip_v2"] if a in s]
    if not arms:
        return
    names, vals, lo, hi = [], [], [], []
    for a in arms:
        k, n = s[a].get("passed", 0), s[a].get("runs", 0)
        pp, l, h = wilson(k, n)
        names.append("%s (n=%d)" % ({"direct": "Direct MCP", "mip_v1": "MIP gateway v1 (no reader)",
                                     "mip_v2": "MIP gateway v2"}[a], n))
        vals.append(100 * pp); lo.append(100 * l); hi.append(100 * h)
    fig, ax = plt.subplots(figsize=(8, 2.8), dpi=160)
    bars(ax, names, vals, lo, hi, [GREY, BLUE, ORANGE][:len(arms)])
    ax.set_xlim(0, 110); ax.set_xlabel("MCPMark filesystem runs passing verify.py (%)")
    style(ax, "MCPMark filesystem, Claude Code CLI as the client, Claude Haiku 5.5")
    fig.tight_layout(); fig.savefig(os.path.join(OUT, "mcpmark.png")); plt.close(fig)


if __name__ == "__main__":
    for f in [fig_tokens, fig_utility, fig_attacks, fig_hijack, fig_overhead, fig_mcpmark]:
        try:
            f(); print("ok  ", f.__name__)
        except Exception as e:
            print("FAIL", f.__name__, type(e).__name__, e)
