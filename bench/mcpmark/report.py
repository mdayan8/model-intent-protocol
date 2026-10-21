"""runs/log.jsonl -> results.json + the tables for RESULTS.md (printed). python3 bench/mcpmark/report.py"""
import json, math, re, statistics
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUNS = HERE / "runs"


def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    c = (p + z * z / (2 * n)) / (1 + z * z / n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return (max(0.0, c - h), min(1.0, c + h))


def mip_trace(key):
    """What the model sent to run() and what the gateway said back."""
    calls, codes, self_approved, intent_has_dir, denied_dest = 0, Counter(), 0, 0, []
    pending = {}
    for l in (RUNS / key / "cli.jsonl").open():
        m = json.loads(l)
        if m.get("type") not in ("assistant", "user"):
            continue
        for c in m["message"]["content"]:
            if c.get("type") == "tool_use":
                calls += 1
                a = c["input"]
                pending[c["id"]] = a
                self_approved += bool(a.get("approve"))
                intent_has_dir += "/mcpmark_runs/" in str(a.get("intent", ""))
            elif c.get("type") == "tool_result":
                txt = c["content"] if isinstance(c["content"], str) else "".join(x.get("text", "") for x in c["content"])
                try:
                    r = json.loads(txt)
                except ValueError:
                    codes["non-json"] += 1
                    continue
                codes[r.get("code") or r.get("status")] += 1
                if r.get("code", "").startswith("E_DEST"):
                    denied_dest.append(r.get("effect", {}).get("dests"))
    return {"run_calls": calls, "codes": dict(codes), "self_approved_calls": self_approved,
            "calls_with_task_dir_in_intent": intent_has_dir, "denied_dests": denied_dest[:5]}


def main():
    recs = [json.loads(l) for l in (RUNS / "log.jsonl").open()]
    for r in recs:
        u = r.get("usage") or {}
        # timed-out runs have no usage: leave them out of token medians instead of counting 0
        r["input_total"] = sum(u.get(k, 0) for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")) if u else None
        r["output_tokens"] = u.get("output_tokens", 0) if u else None
        rd = r.get("reader") or {}
        ru = rd.get("usage") or {}
        r["reader_input"] = sum(ru.get(k, 0) for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
        r["reader_output"] = ru.get("output_tokens", 0)
        r["cost_all"] = (r.get("total_cost_usd") or 0) + (rd.get("cost_usd") or 0)
        r["api_s_all"] = ((r.get("duration_api_ms") or 0) + (rd.get("duration_api_ms") or 0)) / 1000
        key = "%s__%s__t%d" % (r["task"].replace("/", "__"), r["arm"], r["trial"])
        if r["arm"].startswith("mip") and (RUNS / key / "cli.jsonl").exists():
            r["mip"] = mip_trace(key)
    arms = sorted({r["arm"] for r in recs})
    summary = {}
    for arm in arms:
        rs = [r for r in recs if r["arm"] == arm]
        trials = max(r["trial"] for r in rs)
        k, n = sum(r["passed"] for r in rs), len(rs)
        med = lambda f: statistics.median([f(r) for r in rs if f(r) is not None]) if rs else None
        by_task = defaultdict(list)
        for r in rs:
            by_task[r["task"]].append(r["passed"])
        full = [v for v in by_task.values() if len(v) == trials]
        summary[arm] = {
            "runs": n, "passed": k, "pass_rate": k / n, "wilson95": wilson(k, n),
            "pass_hat_k": (sum(all(v) for v in full) / len(full)) if trials > 1 and full else None,
            "pass_at_k": (sum(any(v) for v in full) / len(full)) if trials > 1 and full else None,
            "median_input_tokens": med(lambda r: r["input_total"]), "median_output_tokens": med(lambda r: r["output_tokens"]),
            "median_turns": med(lambda r: r.get("num_turns")),
            "median_api_s": med(lambda r: (r.get("duration_api_ms") or 0) / 1000),
            "median_reader_input_tokens": med(lambda r: r["reader_input"]),
            "median_reader_output_tokens": med(lambda r: r["reader_output"]),
            "median_reader_calls": med(lambda r: (r.get("reader") or {}).get("calls", 0)),
            "median_api_s_incl_reader": med(lambda r: r["api_s_all"]),
            "median_cost_usd": med(lambda r: r["cost_all"]),
            "total_cost_usd": sum(r["cost_all"] for r in rs),
            "trials": trials,
            "runs_with_cli_error": sum(bool(r.get("error")) for r in rs),
        }
    out = {"model": "claude-haiku-5-5", "summary": summary, "runs": recs,
           "excluded_runs": [json.loads(l) for l in (RUNS / "excluded.jsonl").open()] if (RUNS / "excluded.jsonl").exists() else []}
    (HERE / "results.json").write_text(json.dumps(out, indent=1))

    print("| arm | tasks x trials | passed | pass@1 (Wilson 95%) | pass^k | median input tok | median output tok | median reader in/out tok | median turns | median API s (incl. reader) | median $ | total $ |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for arm, s in summary.items():
        lo, hi = s["wilson95"]
        ph = "%.1f%% (k=%d)" % (100 * s["pass_hat_k"], s["trials"]) if s["pass_hat_k"] is not None else "-"
        print("| %s | %d x %d | %d | %.1f%% (%.1f-%.1f%%) | %s | %s | %s | %s / %s | %s | %.1f | %.4f | %.2f |" % (
            arm, s["runs"] // s["trials"], s["trials"], s["passed"], 100 * s["pass_rate"], 100 * lo, 100 * hi, ph,
            f"{s['median_input_tokens']:,.0f}", f"{s['median_output_tokens']:,.0f}",
            f"{s['median_reader_input_tokens']:,.0f}", f"{s['median_reader_output_tokens']:,.0f}", s["median_turns"],
            s["median_api_s_incl_reader"], s["median_cost_usd"], s["total_cost_usd"]))
    print()
    print("| task | " + " | ".join("%s (pass/trials, med in/out tok, turns)" % a for a in arms) + " |")
    print("|---|" + "---|" * len(arms))
    for t in sorted({r["task"] for r in recs}):
        cells = []
        for arm in arms:
            rs = [r for r in recs if r["task"] == t and r["arm"] == arm]
            if not rs:
                cells.append("-"); continue
            cells.append("%d/%d, %s/%s, %s" % (sum(r["passed"] for r in rs), len(rs),
                         f"{statistics.median([r['input_total'] for r in rs if r['input_total'] is not None] or [0]):,.0f}",
                         f"{statistics.median([r['output_tokens'] for r in rs if r['output_tokens'] is not None] or [0]):,.0f}",
                         statistics.median([r["num_turns"] for r in rs if r.get("num_turns")] or [0])))
        print("| %s | %s |" % (t, " | ".join(cells)))


if __name__ == "__main__":
    main()
