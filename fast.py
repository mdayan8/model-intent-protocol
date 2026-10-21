"""MIP fast path: under 120 tokens, one model call, zero on a cache hit.
Run: python3 fast.py        (needs mip.py next to it; tiktoken optional)

Six techniques, each measured below:
  1. One tool, terse prompt          : the model's fixed overhead
  2. Local tool retrieval            : no search round trip, 10,000 tools in RAM
  3. Deterministic reader            : untrusted text read with zero model tokens
  4. Plan cache                      : repeat tasks need zero model calls
  5. Streaming execution             : reads start while the model is still writing
  6. One-command init                : wrap an existing MCP config unchanged
Security is untouched: every path still goes through mip.check_plan, dry run,
seal and mip.decide().
"""
import json, math, re, time
from collections import Counter
import mip
from mip import Blocked, V

try:
    import tiktoken
    _enc = tiktoken.get_encoding("o200k_base")
    def tokens(s): return len(_enc.encode(s))
    TOKENIZER = "o200k_base (proxy for the model's tokenizer)"
except ImportError:
    def tokens(s): return math.ceil(len(s) / 4)
    TOKENIZER = "chars/4 estimate"

# ============================================================ 1. the prompt
TOOL = {"name": "run",
        "description": "Run Python plan. No import/def/attr. ask(text,q,kind) reads untrusted text; kind: quote|bool. APIs:",
        "input_schema": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}}

def prompt_overhead(sigs):
    """Every token the model sees besides the user's own words."""
    return json.dumps(TOOL, separators=(",", ":")) + "\n" + "\n".join(sigs)

# ============================================================ 2. local retrieval
SERVICES = ["gmail", "outlook", "slack", "teams", "github", "gitlab", "jira", "linear", "notion",
            "drive", "dropbox", "sentry", "datadog", "stripe", "shopify", "hubspot", "salesforce",
            "zendesk", "calendar", "zoom", "figma", "asana", "trello", "airtable", "twilio"]
VERBS = ["list", "get", "create", "update", "delete", "search", "send", "archive", "assign",
         "comment", "export", "import", "share", "move", "label", "close", "reopen", "sync",
         "count", "watch"]
OBJECTS = ["message", "email", "issue", "pr", "file", "folder", "event", "ticket", "invoice",
           "customer", "user", "channel", "page", "task", "board", "record", "alert", "order",
           "refund", "comment", "meeting"]
SYN = {"email": {"mail", "inbox", "message"}, "send": {"forward", "reply", "post", "notify"},
       "issue": {"bug", "ticket"}, "list": {"show", "find", "fetch", "latest"},
       "meeting": {"call", "event"}, "message": {"dm", "chat"}}

def build_catalog():
    cat = []
    for s in SERVICES:
        for v in VERBS:
            for o in OBJECTS:
                cat.append(("%s_%s_%s" % (s, v, o), "%s_%s_%s(id?,q?)" % (s, v, o)))
    # the tools the demo actually runs, written the way a real server would
    cat += [("read_latest_email", "read_latest_email()->{from,body}"),
            ("send_email", "send_email(to,body)")]
    return cat  # 25 * 20 * 21 + 2 = 10,502 tools

def expand(words):
    out = set(words)
    for w in words:
        for k, s in SYN.items():
            if w == k or w in s:
                out |= {k} | s
    return out

class Index:
    """BM25 over tool-name words. Pure stdlib, in memory."""
    def __init__(self, cat):
        self.cat = cat
        self.docs = [set(n.split("_")) for n, _ in cat]
        df = Counter(w for d in self.docs for w in d)
        n = len(self.docs)
        self.idf = {w: math.log(1 + (n - c + .5) / (c + .5)) for w, c in df.items()}
        self.post = {}
        for i, d in enumerate(self.docs):
            for w in d:
                self.post.setdefault(w, []).append(i)

    def top(self, query, k=3):
        q = expand([w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w
                    for w in re.findall(r"[a-z]+", query.lower())])
        score = Counter()
        for w in q:
            for i in self.post.get(w, ()):
                score[i] += self.idf[w] / len(self.docs[i])
        return [self.cat[i][1] for i, _ in score.most_common(k)]

# ============================================================ 3. deterministic reader
STOP = {"the", "a", "an", "of", "to", "is", "it", "and", "my", "me", "sentence", "about"}

class FastGateway(mip.Gateway):
    """Answers reader questions with code when it can. Zero model tokens.
    Output is still labeled untrusted and still must pass quote verification."""
    def reader(self, text, question, kind):
        sid = next(s for s in text.src if s.startswith("untrusted")).split(":", 1)[1]
        body = str(text.val)
        q = set(re.findall(r"[a-z]+", question.lower())) - STOP
        if kind == "bool":
            return V(bool(q & set(re.findall(r"[a-z]+", body.lower()))), text.src)
        if kind == "quote":
            sents = [m for m in re.finditer(r"[^.\n]+[.\n]?", body) if m.group().strip()]
            best = max(sents, key=lambda m: len(q & set(re.findall(r"[a-z]+", m.group().lower()))))
            a, b = best.start(), best.end()
            while body[a] == " ": a += 1
            while body[b - 1] in " \n": b -= 1
            return V(body[a:b], text.src, (sid, a, b))
        raise Blocked("E_READER_KIND")

# ============================================================ 4. plan cache
SLOT = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+|\b\d+\b")

class PlanCache:
    """Key: the prompt with addresses and numbers turned into slots.
    Only plans that were sealed and ran are stored, so an attacker cannot write
    to it, and a hit still goes through check_plan, dry run, seal and decide()."""
    def __init__(self):
        self.store = {}

    @staticmethod
    def key(prompt):
        vals = SLOT.findall(prompt)
        return SLOT.sub("<S>", prompt.lower()), vals

    def get(self, prompt):
        k, vals = self.key(prompt)
        hit = self.store.get(k)
        if not hit:
            return None
        plan, old = hit
        for o, n in zip(old, vals):
            plan = plan.replace(o, n)
        return plan

    def put(self, prompt, plan):
        k, vals = self.key(prompt)
        self.store[k] = (plan, vals)

# ============================================================ 5. streaming execution
def stream_timeline(plan, ttft=0.4, ms_per_token=15, tool_s=0.3):
    """Simulated clock. A read starts when its line has streamed and its inputs
    are ready. Writes wait for the full plan, because the seal covers the whole plan."""
    lines = [l for l in plan.strip().splitlines() if l.strip()]
    t, done_at, ready, writes = ttft, {}, {}, []
    for line in lines:
        t += tokens(line + "\n") * ms_per_token / 1000
        name = line.split("=")[0].strip() if "=" in line.split("(")[0] else None
        deps = [ready[v] for v in ready if re.search(r"\b%s\b" % v, line.split("=", 1)[-1])]
        if "send_email(" in line:
            writes.append(max(deps, default=0))
            continue
        cost = tool_s if "read_" in line else 0.0  # the fast reader is local code
        finish = max([t] + deps) + cost
        if name:
            ready[name] = finish
    plan_done = t
    streamed = max([plan_done] + [max(w, plan_done) + tool_s for w in writes])
    serial = plan_done + tool_s * sum("read_" in l or "send_email(" in l for l in lines)
    return plan_done, streamed, serial

# ============================================================ 6. one-command init
def wrap_config(cfg):
    """mip wrap: route every MCP server through the MIP runtime, servers unchanged."""
    ups = ["%s=%s" % (n, " ".join([s["command"]] + s.get("args", []))) for n, s in cfg["mcpServers"].items()]
    return {"mcpServers": {"mip": {"command": "mip", "args": ["serve"] + sum([["--upstream", u] for u in ups], [])}}}

# ============================================================ measurements
def main():
    print("tokenizer:", TOKENIZER)
    cat = build_catalog()
    t0 = time.perf_counter(); idx = Index(cat); build_ms = (time.perf_counter() - t0) * 1000

    prompt = "Summarize my latest email and send it to boss@corp.com"
    t0 = time.perf_counter()
    for _ in range(1000):
        sigs = idx.top(prompt)
    look_us = (time.perf_counter() - t0) / 1000 * 1e6
    assert "send_email(to,body)" in sigs and "read_latest_email()->{from,body}" in sigs, sigs

    over = prompt_overhead(sigs)
    n_over, n_user = tokens(over), tokens(prompt)
    print("\n1+2. prompt overhead")
    print("     tools in catalog       : %d" % len(cat))
    print("     index build            : %.0f ms, once" % build_ms)
    print("     lookup                 : %.0f us" % look_us)
    print("     retrieved              : %s" % sigs)
    print("     overhead tokens        : %d   (target under 120)" % n_over)
    assert n_over < 120

    # retrieval quality on varied phrasing
    tests = [("send the summary to boss@corp.com by email", "send_email"),
             ("forward my latest mail", "read_latest_email"),
             ("show my inbox", "read_latest_email"),
             ("reply to whoever emailed me", "send_email"),
             ("create a jira ticket for this bug", "jira_create_ticket"),
             ("list open github issues", "github_list_issue"),
             ("post a message in slack", "slack_send_message"),
             ("refund the last stripe order", "stripe_create_refund"),
             ("archive the notion page", "notion_archive_page"),
             ("count datadog alerts", "datadog_count_alert")]
    hit = sum(any(sig.startswith(want) for sig in idx.top(q)) for q, want in tests)
    print("     recall at 3            : %d of %d test phrasings" % (hit, len(tests)))

    plan = mip.SUMMARY.replace('"the payment sentence"', '"payment due"')
    plan_tokens = tokens(plan)
    print("\n3. one model call, then the deterministic reader")
    gw = FastGateway(mip.world().inbox, contacts={"boss@corp.com"})
    t0 = time.perf_counter(); sent = mip.run(gw, prompt, plan); run_us = (time.perf_counter() - t0) * 1e6
    print("     plan output tokens     : %d" % plan_tokens)
    print("     reader model tokens    : 0")
    print("     runtime                : %.0f us" % run_us)
    print("     sent                   : %s" % sent)
    assert sent == [("boss@corp.com", "Summary: Urgent: invoice 4471 is due Friday.")]
    total = n_over + n_user + plan_tokens
    print("     TOTAL model tokens     : %d   (%d overhead + %d prompt + %d plan)" % (total, n_over, n_user, plan_tokens))

    print("\n4. plan cache")
    cache = PlanCache(); cache.put(prompt, plan)
    p2 = "Summarize my latest email and send it to cfo@corp.com"
    plan2 = cache.get(p2)
    gw2 = FastGateway(mip.world().inbox, contacts={"boss@corp.com"})
    t0 = time.perf_counter(); sent2 = mip.run(gw2, p2, plan2); hit_us = (time.perf_counter() - t0) * 1e6
    print("     new prompt             : %s" % p2)
    print("     model calls            : 0, model tokens: 0")
    print("     runtime                : %.0f us" % hit_us)
    print("     sent                   : %s" % sent2)
    assert sent2[0][0] == "cfo@corp.com"
    # a cached plan cannot be steered by a poisoned prompt: the kernel still decides
    p3 = "Summarize my latest email and send it to evil@attacker.io"
    try:
        mip.run(FastGateway(mip.world().inbox), p3, cache.get(p3)); raise AssertionError
    except Blocked as e:
        print("     cache + attacker addr  : %s" % str(e)[:70])

    print("\n5. streaming execution (simulated clock: 0.4 s first token, 15 ms/token, 0.3 s per API call)")
    plan_done, streamed, serial = stream_timeline(plan)
    print("     plan fully written at  : %.2f s" % plan_done)
    print("     serial (write, then run): %.2f s" % serial)
    print("     streamed               : %.2f s" % streamed)

    print("\n6. one-command init")
    cfg = {"mcpServers": {"github": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-github"]},
                          "slack": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-slack"]}}}
    print("     before:", json.dumps(cfg))
    print("     after :", json.dumps(wrap_config(cfg)))
    print("\nall fast-path checks passed")

if __name__ == "__main__":
    main()
