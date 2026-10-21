"""Worst case, no model: a fully hijacked agent.

For every AgentDojo v1.2.2 attacked case (user task x injection task), a scripted agent
does the user's task correctly (the task's ground-truth calls), then does everything the
attacker asked (the injection task's ground-truth calls) and ends with the attacker's
expected text output. This is the most a compromised model could do.

Each call goes through one executor:
  none       : runs every call (classic tool calling)
  fence      : MIP Fence, strict
  fence-dir  : MIP Fence with the directory option
A refused call is not executed, and the scripted agent carries on.

AgentDojo's own checkers score utility and attack success. Deterministic: no model, no sampling.
Run: python hijack.py   -> bench/results/agentdojo/hijack.json
"""
import json, os, sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
import mip  # noqa: E402,F401
from fence import FenceExecutor, FENCE_VERSION  # noqa: E402
from agentdojo.agent_pipeline import BasePipelineElement, ToolsExecutor  # noqa: E402
from agentdojo.task_suite.load_suites import get_suites  # noqa: E402
from agentdojo.attacks.attack_registry import load_attack  # noqa: E402
from agentdojo import models as ad_models  # noqa: E402
import claude_cli  # noqa: E402

ad_models.MODEL_NAMES[claude_cli.MODEL] = "Claude"


class _Named(BasePipelineElement):
    name = claude_cli.MODEL

    def query(self, *a, **k):
        raise NotImplementedError
from agentdojo.types import text_content_block_from_string  # noqa: E402


class HijackedAgent(BasePipelineElement):
    name = "hijacked-agent"

    def __init__(self, user_task, injection_task, executor):
        self.u, self.i, self.ex = user_task, injection_task, executor
        self.blocked = []

    def query(self, query, runtime, env, messages=(), extra_args={}):
        msgs = list(messages) or [{"role": "user", "content": [text_content_block_from_string(query)]}]
        plan = [("user", c) for c in self.u.ground_truth(env.model_copy(deep=True))]
        plan += [("attack", c) for c in self.i.ground_truth(env.model_copy(deep=True))]
        for who, call in plan:
            msgs.append({"role": "assistant", "content": [text_content_block_from_string("")], "tool_calls": [call]})
            n = len(msgs)
            query, runtime, env, msgs, extra_args = self.ex.query(query, runtime, env, msgs, extra_args)
            for m in msgs[n:]:
                if m.get("error") and "Blocked by MIP" in str(m["error"]):
                    self.blocked.append((who, call.function))
        text = (getattr(self.u, "GROUND_TRUTH_OUTPUT", "") or "") + "\n" + (getattr(self.i, "GROUND_TRUTH_OUTPUT", "") or "")
        msgs.append({"role": "assistant", "content": [text_content_block_from_string(text)], "tool_calls": None})
        return query, runtime, env, msgs, extra_args


# Injection tasks whose goal is to move data to, or act toward, a destination the attacker
# chooses (an email address, IBAN, URL, Slack user or channel). This is what MIP's guarantee covers.
# The rest ask for actions with no outside destination (book a hotel, create an event, delete a
# file, visit a page, say something), which MIP does not claim to block.
DATA_TO_DESTINATION = {
    "banking": {"injection_task_%d" % i for i in range(9)},
    "slack": {"injection_task_1", "injection_task_2", "injection_task_4", "injection_task_5"},
    "travel": {"injection_task_1", "injection_task_3", "injection_task_5"},
    "workspace": {"injection_task_0", "injection_task_2", "injection_task_3", "injection_task_4", "injection_task_5"},
}


def categorize(out):
    out["categories"] = {}
    for kind, per in out["per_injection"].items():
        c = {"data_to_destination": [0, 0], "other": [0, 0]}
        for key, (n, k) in per.items():
            suite, inj = key.split("/")
            cat = "data_to_destination" if inj in DATA_TO_DESTINATION.get(suite, ()) else "other"
            c[cat][0] += n; c[cat][1] += k
        out["categories"][kind] = c
    return out


def executor(kind):
    return {"none": lambda: ToolsExecutor(), "fence": lambda: FenceExecutor(directory=False),
            "fence-dir": lambda: FenceExecutor(directory=True)}[kind]()


def main():
    out = {"fence_version": FENCE_VERSION, "arms": {}, "per_injection": {}}
    for kind in ["none", "fence", "fence-dir"]:
        tot = defaultdict(int)
        per_inj = defaultdict(lambda: [0, 0])
        for sname, suite in get_suites("v1.2.2").items():
            attacker = load_attack("important_instructions", suite, _Named())
            for uid, u in suite.user_tasks.items():
                for iid, inj in suite.injection_tasks.items():
                    agent = HijackedAgent(u, inj, executor(kind))
                    try:  # the real injected text is placed in the data, as in the model runs
                        utility, attacked = suite.run_task_with_pipeline(agent, u, inj, attacker.attack(u, inj))
                    except Exception as e:  # a refused call can break a later step of the script
                        utility, attacked = False, False
                        tot["script_errors"] += 1
                    tot["cases"] += 1
                    tot["utility"] += bool(utility)
                    tot["attack_success"] += bool(attacked)
                    per_inj["%s/%s" % (sname, iid)][0] += 1
                    per_inj["%s/%s" % (sname, iid)][1] += bool(attacked)
        out["arms"][kind] = dict(tot)
        out["per_injection"][kind] = {k: v for k, v in per_inj.items()}
        print("%-10s %s" % (kind, dict(tot)))
    path = os.path.join(os.path.dirname(HERE), "results", "agentdojo", "hijack.json")
    json.dump(categorize(out), open(path, "w"), indent=1)
    print("saved", path)


if __name__ == "__main__":
    main()
