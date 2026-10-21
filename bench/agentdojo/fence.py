"""MIP Fence: the drop-in mode. Fence rules version: f2.

The agent is an ordinary tool-calling agent, identical to the baseline: same loop, same
prompts, same tool docs. The only change is that every call that sends data somewhere goes
through mip.decide() before it runs.

Labeling in this mode: the model has seen tool output, so nothing it writes can be trusted by
default. A destination counts as trusted only if the user typed it (mip.named) or, with the
directory option, it already exists in the system's own directory before the task starts.
Anything else is untrusted, and decide() refuses it. A refused call is not executed; the agent
gets a tool error explaining why and can carry on.

Guarantee: whatever the model does, data reaches only destinations the user named (or existing
directory members, when that option is on). What it does not guard: what the model writes in
a message body, and actions that move no data to a destination.
"""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
import mip  # noqa: E402
from mip import V  # noqa: E402
from mip_pipeline import DEST_ARGS, TRUSTED_ARGS_ONLY, directory_of  # noqa: E402

from agentdojo.agent_pipeline import ToolsExecutor  # noqa: E402
from agentdojo.types import text_content_block_from_string  # noqa: E402


# Read-only fetches: a URL copied verbatim from data the agent already read carries nothing new
# out. A URL the model constructed (e.g. with data in its path or query) is still fenced.
READ_ONLY_FETCH = {"get_webpage"}
FENCE_VERSION = "f3.1"


class FenceExecutor(ToolsExecutor):
    def __init__(self, directory=False):
        super().__init__()
        self.use_directory = directory
        self.decisions = []  # (tool, verdict, reason) for the report

    def label(self, value, intent, directory):
        c = mip.canon(value) or ""
        if c and (mip.named(c, intent) or (self.use_directory and c in directory)):
            return frozenset({"user"})
        return frozenset({"untrusted:model-chosen"})

    def check(self, call, intent, directory, seen):
        if call.function in TRUSTED_ARGS_ONLY:
            # credential or identity changes run only with values the user typed
            for k, v in call.args.items():
                if v in (None, ""):
                    continue
                c = mip.canon(str(v)) or ""
                if not (c and mip.named(c, intent)):
                    return mip.CONFIRM, "%s argument %s was not typed by the user" % (call.function, k)
            return mip.ALLOW, "ok"
        for arg in DEST_ARGS.get(call.function, ()):
            vals = call.args.get(arg)
            for d in (vals if isinstance(vals, list) else [vals] if vals not in (None, "") else []):
                if call.function in READ_ONLY_FETCH and isinstance(d, str) and len(d) >= 4 and d in seen:
                    continue  # copied verbatim from data already read: carries no new information out
                verdict, why = mip.decide(V(d, self.label(d, intent, directory)), V("x"), frozenset(),
                                          intent, {}, {}, directory)
                if verdict != mip.ALLOW:
                    return verdict, why
        return mip.ALLOW, "ok"

    def query(self, query, runtime, env, messages=(), extra_args={}):
        if not messages or messages[-1]["role"] != "assistant" or not messages[-1]["tool_calls"]:
            return super().query(query, runtime, env, messages, extra_args)
        if "_mip_intent" not in extra_args:
            intent0 = mip.resolve_intent(query)[0]
            directory = directory_of(env) if self.use_directory else set()
            if directory:  # existing directory members count as named for the fence
                intent0 = {**intent0, "dests": intent0["dests"] | directory}
            extra_args = {**extra_args, "_mip_intent": intent0, "_mip_directory": directory}
        intent, directory = extra_args["_mip_intent"], extra_args["_mip_directory"]
        seen = "\n".join(str(b.get("content") if isinstance(b, dict) else b)
                         for m in messages if m["role"] == "tool" for b in (m.get("content") or []))
        allowed, blocked = [], []
        for call in messages[-1]["tool_calls"]:
            verdict, why = self.check(call, intent, directory, seen)
            self.decisions.append((call.function, verdict, why))
            (allowed if verdict == mip.ALLOW else blocked).append((call, verdict, why))
        last = dict(messages[-1])
        last["tool_calls"] = [c for c, _, _ in allowed]
        q, runtime, env, out, extra_args = super().query(query, runtime, env, [*messages[:-1], last], extra_args)
        out = list(out)
        # The trace keeps only calls that actually ran; refused calls never executed.
        # The full list of attempts is kept in self.attempted for the report.
        self.attempted = getattr(self, "attempted", []) + [(c.function, c.args) for c, _, _ in blocked]
        for call, verdict, why in blocked:
            out.append({"role": "tool", "content": [text_content_block_from_string("")],
                        "tool_call_id": call.id, "tool_call": call,
                        "error": "Blocked by MIP (%s): %s. Only destinations the user named are allowed."
                                 % (verdict, why)})
        return q, runtime, env, out, extra_args
