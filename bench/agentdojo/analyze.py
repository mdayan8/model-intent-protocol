"""Aggregate AgentDojo results: utility, utility under attack, targeted attack success rate,
with Wilson 95% intervals, plus model calls, tokens and API seconds per task.

Token accounting: every CLI call carries a fixed overhead (measured by the health check with a
one-word prompt). Raw totals are reported, and "net" figures subtract that constant per call.
Run: python analyze.py   -> bench/results/agentdojo/summary.json and summary.md
"""
import glob, json, math, os, statistics
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
RES = os.path.join(os.path.dirname(HERE), "results", "agentdojo")
ARMS = ["baseline", "fence", "fence-dir", "mip", "mip-dir"]
SUITES = ["banking", "slack", "travel", "workspace"]


def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (p, max(0.0, c - h), min(1.0, c + h))


def load(arm, suite, attack):
    p = os.path.join(RES, arm, suite, attack + ".jsonl")
    if not os.path.exists(p):
        return []
    last = {}  # a retried case keeps only its latest record
    for l in open(p):
        r = json.loads(l)
        last[(r["user_task"], r["injection_task"])] = r
    return list(last.values())


def calls(arm, suite, attack):
    p = os.path.join(RES, arm, suite, attack + ".calls.jsonl")
    out = defaultdict(list)
    if os.path.exists(p):
        for l in open(p):
            c = json.loads(l)
            if c.get("tag") == "healthcheck":
                continue
            out[c.get("tag", "")].append(c)
    return out


def overhead():
    xs = []
    for p in glob.glob(os.path.join(RES, "*", "*", "*.calls.jsonl")):
        xs += [json.loads(l)["input"] for l in open(p) if '"healthcheck"' in l]
    return statistics.median(xs) if xs else 0


def per_task_cost(arm, suite, attack, recs):
    """Returns list of dicts with calls, input, output, api_seconds per task."""
    rows = []
    if arm == "baseline" or arm.startswith("fence"):
        by_tag = calls(arm, suite, attack)
        for r in recs:
            tag = "%s|%s|%s|%s" % (arm, suite, r["user_task"], r["injection_task"] or "-")
            cs = by_tag.get(tag, [])
            rows.append({"calls": len(cs), "input": sum(c["input"] for c in cs),
                         "output": sum(c["output"] for c in cs), "api_s": sum(c.get("api_seconds", 0) for c in cs)})
    else:
        allc = calls(arm, suite, attack)
        api = {}  # MIP stats carry tokens; API seconds are taken from its own per-call usage
        for r in recs:
            m = r.get("mip") or {}
            rows.append({"calls": m.get("planner_calls", 0) + m.get("reader_calls", 0),
                         "input": m.get("input_tokens", 0), "output": m.get("output_tokens", 0),
                         "api_s": m.get("api_seconds", m.get("seconds", 0))})
    return rows


def main():
    oh = overhead()
    summary = {"fixed_overhead_tokens_per_call": oh, "arms": {}}
    md = ["| Arm | Suite | Utility (no attack) | Utility under attack | Attack success rate | Calls/task | Net input tok/task | Output tok/task | API s/task |",
          "|---|---|---|---|---|---|---|---|---|"]
    for arm in ARMS:
        summary["arms"][arm] = {}
        tot = defaultdict(lambda: [0, 0])
        cost_all = []
        for suite in SUITES:
            clean = load(arm, suite, "none")
            att = load(arm, suite, "important_instructions")
            if not clean and not att:
                continue
            u = sum(r["utility"] for r in clean); ua = sum(r["utility"] for r in att)
            asr = sum(bool(r["attack_success"]) for r in att)
            errs = sum(1 for r in clean + att if r.get("error"))
            costs = per_task_cost(arm, suite, "none", clean) + per_task_cost(arm, suite, "important_instructions", att)
            cost_all += costs
            def med(k, net=False):
                xs = [c[k] - (oh * c["calls"] if net else 0) for c in costs if c["calls"]]
                return statistics.median(xs) if xs else 0
            s = {"n_clean": len(clean), "n_attack": len(att), "errors": errs,
                 "utility": wilson(u, len(clean)), "utility_under_attack": wilson(ua, len(att)),
                 "attack_success": wilson(asr, len(att)),
                 "median_calls": med("calls"), "median_input_net": med("input", True),
                 "median_input_raw": med("input"), "median_output": med("output"), "median_api_s": med("api_s")}
            summary["arms"][arm][suite] = s
            for k, (a, b) in {"u": (u, len(clean)), "ua": (ua, len(att)), "asr": (asr, len(att))}.items():
                tot[k][0] += a; tot[k][1] += b
            f = lambda w: "%.1f%% [%.1f, %.1f]" % (100 * w[0], 100 * w[1], 100 * w[2])
            md.append("| %s | %s | %s (n=%d) | %s (n=%d) | %s | %.0f | %.0f | %.0f | %.1f |" % (
                arm, suite, f(s["utility"]), len(clean), f(s["utility_under_attack"]), len(att),
                f(s["attack_success"]), s["median_calls"], s["median_input_net"], s["median_output"], s["median_api_s"]))
        if tot:
            allrow = {k: wilson(*v) for k, v in tot.items()}
            summary["arms"][arm]["ALL"] = {"utility": allrow["u"], "utility_under_attack": allrow["ua"],
                                           "attack_success": allrow["asr"],
                                           "n_clean": tot["u"][1], "n_attack": tot["ua"][1]}
            f = lambda w: "%.1f%% [%.1f, %.1f]" % (100 * w[0], 100 * w[1], 100 * w[2])
            md.append("| **%s** | **all** | **%s** (n=%d) | **%s** (n=%d) | **%s** | | | | |" % (
                arm, f(allrow["u"]), tot["u"][1], f(allrow["ua"]), tot["ua"][1], f(allrow["asr"])))
    json.dump(summary, open(os.path.join(RES, "summary.json"), "w"), indent=1)
    open(os.path.join(RES, "summary.md"), "w").write("\n".join(md) + "\n\nFixed CLI overhead per call (subtracted in net): %d input tokens\n" % oh)
    print("\n".join(md))
    print("\nfixed overhead per call: %d tokens" % oh)


if __name__ == "__main__":
    main()
