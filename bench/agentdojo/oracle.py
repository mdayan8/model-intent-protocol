"""Model-free checks on all 97 AgentDojo v1.2.2 user tasks.

1. Fence compatibility: take each task's ground-truth tool calls and ask the MIP kernel about every
   destination they use. A destination the user typed counts as planner text; any other destination
   must have come from data, so it carries an untrusted label. This measures how many tasks MIP's
   destination fence allows with a perfect planner, before any model is involved.
2. Plumbing: run the ground-truth calls as a literal plan through MIPPipeline (approve-confirms mode,
   so the fence does not hide plumbing bugs) and let AgentDojo's own scorer grade the result.

Run: python oracle.py   -> bench/results/agentdojo/oracle.json
"""
import json, os, sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import claude_cli  # noqa: E402
import mip_pipeline as mp  # noqa: E402
import mip  # noqa: E402
from mip import V  # noqa: E402
from agentdojo.task_suite.load_suites import get_suites  # noqa: E402


def literal(x):
    return repr(x)


def plan_from_ground_truth(calls):
    lines = ["r%d = %s(%s)" % (k, c.function, ", ".join("%s=%s" % (a, literal(v)) for a, v in c.args.items()))
             for k, c in enumerate(calls)]
    return "\n".join(lines + ['respond("done")'])


def main():
    out = {"fence": {}, "plumbing": {}, "tasks": []}
    for sname, suite in get_suites("v1.2.2").items():
        verdicts, plumb = Counter(), Counter()
        for uid, task in sorted(suite.user_tasks.items(), key=lambda kv: int(kv[0].split("_")[-1])):
            env = task.init_environment(suite.load_and_inject_default_environment({}))
            calls = task.ground_truth(env.model_copy(deep=True))
            intent, _ = mip.resolve_intent(task.PROMPT)
            worst, why = "allow", ""
            for c in calls:
                for arg in mp.DEST_ARGS.get(c.function, ()):
                    vals = c.args.get(arg)
                    for d in (vals if isinstance(vals, list) else [vals] if vals else []):
                        cd = mip.canon(d) or ""
                        src = frozenset({"plan"}) if mip.named(cd, intent) else frozenset({"untrusted:data"})
                        v, w = mip.decide(V(d, src), V("x"), frozenset(), intent, {}, {}, set())
                        if v == "deny" or (v == "confirm" and worst == "allow"):
                            worst, why = v, w
            verdicts[worst] += 1

            # plumbing: literal plan, model calls replaced by a stub that returns the plan
            code = plan_from_ground_truth(calls)
            orig = claude_cli.complete
            claude_cli.complete = lambda *a, **k: (code, {"input": 0, "output": 0, "seconds": 0})
            try:
                pipe = mp.MIPPipeline("oracle", approve_confirms=True)
                utility, _ = suite.run_task_with_pipeline(pipe, task, None, {})
                status = "utility" if utility else ("no_utility:" + pipe.stats[-1]["outcome"][:60])
            except Exception as e:
                status = "crash:%s" % type(e).__name__
            finally:
                claude_cli.complete = orig
            plumb[status.split(":")[0]] += 1
            out["tasks"].append({"suite": sname, "task": uid, "fence": worst, "fence_reason": why, "plumbing": status})
        out["fence"][sname] = dict(verdicts)
        out["plumbing"][sname] = dict(plumb)
        print("%-10s fence %-40s plumbing %s" % (sname, dict(verdicts), dict(plumb)))
    os.makedirs(os.path.join(os.path.dirname(HERE), "results", "agentdojo"), exist_ok=True)
    json.dump(out, open(os.path.join(os.path.dirname(HERE), "results", "agentdojo", "oracle.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
