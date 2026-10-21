"""Differential test: mip.py (the spec) vs the Rust kernel, via target/release/mip-cli.
Run: cargo build --release && python3 diff.py [decide_cases]
Exits non-zero on any disagreement. Compares verdict AND which rule fired."""
import json, os, random, re, subprocess, sys
from multiprocessing import Pool

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import mip
from mip import V, canon, decide, decode, encode, resolve_intent

CLI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "target", "release", "mip-cli")
CHUNK = 20000

def rule(reason):
    """Map mip.py's reason text to the Rust Rule code. Fixed prefixes first; the
    rest start with repr(value), so look at the fixed suffix or the text after it."""
    fixed = [("body splices ", "B1"), ("untrusted text in body is not a verified quote", "B2"),
             ("reader output is not a real quote from ", "B3"), ("reply decided by untrusted data", "R1"),
             ("reply to DMARC-verified sender of ", "R2"), ("verified metadata used without", "R3"),
             ("destination came from untrusted or unlabeled data: ", "U1"),
             ("destination carries more than one verified label", "R0"), ("this send only happens because", "C1"),
             ("destination named by the user", "A1")]
    for p, code in fixed:
        if reason.startswith(p):
            return code
    if reason.endswith(" is not a canonical ASCII address"):
        return "D1"
    if reason.endswith(" was never named by the user"):
        return "N1"
    after = reason.split(" ", 1)[1]  # reason starts with repr(dest), and dest has no spaces
    return {"appeared": "C2", "looks": "C3"}[after.split(" ", 1)[0]]

def lv(v):
    return {"v": v.val, "l": sorted(v.src), "q": list(v.quote), "parts": [lv(p) for p in v.parts]}

# ------------------------------------------------------------------ decide generators
SRCS = {"m1": mip.POISON, "m2": "Meeting moved to 3pm. Contact ops@corp.com for the room."}
POOL = ["boss@corp.com", "evil@attacker.io", "ops@corp.com", "vendor@shop.com", "boss@c0rp.com",
        "bоss@corp.com", "BOSS@CORP.COM", "", "not-an-address"]
FUZZ_POOL = ["ab", "ac", None, 42] + POOL  # mip.fuzz()'s pool, same order
FUZZ_CONTACTS = {"boss@corp.com", "vendor@shop.com", "", "ac"}
LABELS = [{"plan"}, {"user"}, {"untrusted:m1"}, {"untrusted:m2"}, {"verified:m1"},
          {"verified:m2"}, {"plan", "untrusted:m1"}, {"untrusted:agent:x"},
          set(), {"foo"}, {"plan", "foo"}, {"verified:m1", "verified:m2"}]
PCS = [frozenset(), frozenset({"untrusted:m1"}), frozenset({"plan"})]

def fuzz_case(rng):
    """Exactly mip.fuzz()'s distribution."""
    intent = {"dests": {canon(d) or "!" + str(d) for d in rng.sample(FUZZ_POOL, rng.randint(0, 3))},
              "roles": set(rng.sample(["sender"], rng.randint(0, 1)))}
    to = V(rng.choice(FUZZ_POOL), frozenset(rng.choice(LABELS)))
    parts = []
    for _ in range(rng.randint(0, 3)):
        sid = rng.choice(["m1", "m2", "mX"]); txt = SRCS.get(sid, "")
        a = rng.randint(0, max(0, len(txt) - 1)); b = a + rng.randint(-2, 40)
        real = txt[a:b] if b > a else ""
        val = real if rng.random() < .6 else rng.choice(["forged text here!", real + "!", "evil@attacker.io"])
        parts.append(V(val, frozenset(rng.choice(LABELS)), (sid, a, b) if rng.random() < .8 else ()))
    body = V("", frozenset(), (), tuple(parts)) if parts else V("ok")
    return to, body, rng.choice(PCS), intent, SRCS, {"m1": "vendor@shop.com"}, FUZZ_CONTACTS

# Non-email destinations (spec v0.4): IBANs with and without spaces, URLs, Slack, short names,
# Cyrillic lookalikes, punycode, full-width, control characters.
NONMAIL = ["DE89 3704 0044 0532 0130 00", "DE89370400440532013000", "de89 3704 0044 0532 0130 01", "GB29 NWBK 6016 1331 9268 19",
           "\uff24\uff25\uff18\uff19 \uff13\uff17\uff10\uff14", "DE89\t3704\u30000044", "https://evil.example/x?a=1", "https://Corp.com/inbox",
           "https://xn--pple-43d.com", "#general", "#g\u0435neral", "@alice", "U024BE7LH", "C0123ABCD", "a", "ab", "\u0410", "ops", "0130",
           "p\u0430ypal.com", "paypal.com", "a\x07b", "x\x7fy", "  #ops  ", "corp.com/boss", "\ufb01", "evil", "attacker.io/drop", "none", "42"]

# Wider than fuzz(): unicode tricks, whitespace Python strips but Rust's trim() would not,
# non-string values, empty labels, negative offsets, random sources/contacts/verified.
WPOOL = POOL + ["  boss@corp.com　", "boss@corp.com\x1c", " ops@corp.com\x85", "ｂｏｓｓ@ｃｏｒｐ.ｃｏｍ",
                "boss@xn--crp-8cd.com", "BoSs@CoRp.CoM", "boss@corp.c", "boss@@corp.com", "a@b.co", "İnfo@corp.com",
                "ops@corp.com.", "Kate@corp.com", "kate@corp.com", "boss@corp.ço", "ops@corp.ai", "bos@corp.com",
                "boss@corp.co", "x@y.z", "vendor@shop.co", "ve.ndor@shop.com", "boss@corp.com​", "ﬁle@corp.com"] + NONMAIL
WLABELS = LABELS + [set(), {"plan", "user"}, {"untrustedX"}, {"verified:agent:x"}, {"verified:m3"}, {"user", "verified:m1"}, {"verified:m1", "verified:m2"},
                   {"verified:m1", "verified:m3"}]
WTEXTS = [mip.POISON, SRCS["m2"], "Write to ｂｏｓｓ@ｃｏｒｐ.ｃｏｍ today please, thanks a lot", "contact OPS@CORP.COM",
          "x ops@corp.aİ and kate@corp.com", "KATE@CORP.COM is the ﬁle@corp.com owner", "", "ünïcödé text, no address here at all",
          "wire to DE89 3704 0044 0532 0130 00 now", "see https://evil.example/x?a=1 and #general", "pay GB29NWBK60161331926819"]
WPCS = PCS + [frozenset({"untrusted:agent:x"}), frozenset({"verified:m1"}), frozenset({"user", "untrustedX"})]

def wval(rng, s):
    return s if rng.random() < .93 else rng.choice([None, 42, ["boss@corp.com"], {"v": "boss@corp.com"}, True, 4.5, [1, 2], -0.0, 1e16, [None]])

def prompt(rng):
    return "".join(rng.choice(WORDS) + rng.choice(SEPS) for _ in range(rng.randint(0, 8)))

def wide_case(rng):
    sources = {k: rng.choice(WTEXTS) for k in rng.sample(["m1", "m2", "m3"], rng.randint(0, 3))}
    verified = {k: rng.choice([canon(rng.choice(WPOOL)), "vendor@shop.com", None]) for k in rng.sample(["m1", "m2", "m3"], rng.randint(0, 3))}
    contacts = set(rng.sample(CONTACTS, rng.randint(0, 4)))
    if rng.random() < .5:
        intent = resolve_intent(prompt(rng) + " " + " ".join(rng.sample(WPOOL, rng.randint(0, 3))))[0]
    else:
        intent = {"dests": {canon(d) or "!" + d for d in rng.sample(WPOOL, rng.randint(0, 4))},
                  "roles": {"sender"} if rng.random() < .5 else set()}
        if rng.random() < .5:
            intent["text"] = resolve_intent(prompt(rng))[0]["text"]
    to = V(wval(rng, rng.choice(WPOOL)), frozenset(rng.choice(WLABELS)))
    def atom():
        sid = rng.choice(["m1", "m2", "m3", "mX"]); txt = sources.get(sid, "")
        a = rng.randint(-5, len(txt) + 5); b = a + rng.randint(-3, 40) if rng.random() < .9 else rng.randint(-40, 0)
        real = txt[a:b]
        val = real if rng.random() < .6 else rng.choice(["forged text here!", real + "!", real[1:], "evil@attacker.io", real.upper()])
        q = (sid, a, b) if rng.random() < .8 else ()
        sub = (V("x"),) if rng.random() < .05 else ()  # nested parts are ignored by atoms()
        return V(wval(rng, val), frozenset(rng.choice(WLABELS)), q, sub)
    n = rng.randint(0, 3)
    body = V("", frozenset(rng.choice(WLABELS)), (), tuple(atom() for _ in range(n))) if n else (atom() if rng.random() < .5 else V("ok"))
    return to, body, rng.choice(WPCS), intent, sources, verified, contacts

CONTACTS = NONMAIL + WPOOL + ["ac"]  # includes "", which lookalike() must skip

def dest_case(rng):
    """Clean body and label, so the destination checks (named, echo, lookalike) decide."""
    prompt_ = prompt(rng) + " " + " ".join(rng.sample(NONMAIL + WPOOL, rng.randint(0, 3)))
    intent = resolve_intent(prompt_)[0]
    toks = re.findall(r"\S+", prompt_) or ["a"]
    i = rng.randrange(len(toks))
    to = rng.choice([rng.choice(NONMAIL + WPOOL), " ".join(toks[i:i + rng.randint(1, 6)]), "".join(toks[i:i + 3]), toks[i][1:]])
    sources = {k: rng.choice(WTEXTS) for k in rng.sample(["m1", "m2"], rng.randint(0, 2))}
    contacts = set(rng.sample(CONTACTS, rng.randint(0, 4)))
    lab = rng.choice([{"plan"}, {"user"}, {"plan", "user"}, {"untrusted:m1"}, set()])
    return (V(wval(rng, to), frozenset(lab)), V("ok"), rng.choice(WPCS[:3]), intent, sources,
            {"m1": "vendor@shop.com"}, contacts)

def decide_chunk(args):
    seed, n = args
    rng = random.Random(seed)
    lines, want = [], []
    for k in range(n):
        to, body, pc, intent, sources, verified, contacts = (fuzz_case, wide_case, fuzz_case, dest_case)[k % 4](rng)
        verdict, why = decide(to, body, pc, intent, sources, verified, contacts)
        req = {"op": "decide", "to": lv(to), "body": lv(body), "pc": sorted(pc),
               "intent": {"dests": sorted(intent["dests"]), "roles": sorted(intent["roles"]), "text": intent.get("text", "")},
               "sources": sources, "verified": verified, "contacts": sorted(contacts)}
        lines.append(json.dumps(req)); want.append({"verdict": verdict, "rule": rule(why)})
    return compare(lines, want)

# ------------------------------------------------------------------ canon, intent, wire
def canon_chunk(args):
    lo, hi = args
    lines, want = [], []
    for c in range(lo, hi):
        if 0xD800 <= c < 0xE000:
            continue  # lone surrogates cannot cross a UTF-8 wire
        ch = chr(c)
        for s in (ch, "a" + ch + "b@corp.com", ch + "boss@corp.com" + ch, "boss@corp.c" + ch + "m", "b" + ch + "@x" + ch + ".io",
                  "de89" + ch + "3704", ch + "#ops" + ch):
            lines.append(json.dumps({"op": "canon", "s": s})); want.append({"canon": canon(s)})
    return compare(lines, want)

WORDS = ["reply", "REPLY", "Reply", "ſender", "SENDER", "sender", "whoever emailed", "whoever sent", "whoever  sent",
         "replying", "reply_", "xreply", "rePly", "İ", "ı", "send", "boss@corp.com", "a@b.c", "@x.y", "a@.b", "a@b.",
         "x@y.z.é", "a@b.c@d.e", "q@w.e_r", "z@z.²", "m@n.Ⅻ", "k@l.é́", "u@v.a‿", "ｂｏｓｓ@ｃｏｒｐ.ｃｏｍ",
         "bоss@corp.com", "BOSS@CORP.COM,", "<ops@corp.com>", "(x@y.co)", "@@..", "a@b..c", "foo@bar.baz.", ".", "@"] + NONMAIL + [
         "DE89:", "#general.", "(ops)", "'quoted'", "x;y", "a.b.:", "boss\uff20corp.com", "https://a.b/c?d!e", "\"#ops\"", "x\x07y"]
SEPS = [" ", "", "\n", "\t", "\x1c", " ", "\x85", "　", ",", ".", " ", "​", "!", ";", ":", "<", ")"]

def intent_chunk(args):
    seed, n = args
    rng = random.Random(seed)
    lines, want = [], []
    for _ in range(n):
        p = prompt(rng)
        intent, readback = resolve_intent(p)
        lines.append(json.dumps({"op": "intent", "prompt": p}))
        want.append({"dests": sorted(intent["dests"]), "roles": sorted(intent["roles"]), "text": intent["text"], "readback": readback})
    return compare(lines, want)

CHARS = ["a", "Z", " ", "\t", "'", '"', "\\", "@", ".", "\x07", "\x7f", "\xe9", "\u200b", "\U0001f600", "\ud7ff", "\xa0", "\uff21", "\x85", "#"]

def jval(rng, depth=0):
    k = rng.randint(0, 7 if depth < 2 else 4)
    if k == 0: return None
    if k == 1: return rng.random() < .5
    if k == 2: return rng.randint(-10**12, 10**12)
    if k == 3:
        if rng.random() < .5:
            return rng.choice([0.0, -0.0, 1.0, 1e16, 1e15, 1.5e-7, 1e-4, 9.99e-5, 123.456, -2.5e300])
        return rng.uniform(-1e6, 1e6) * 10 ** rng.randint(-8, 20)
    if k == 4: return "".join(rng.choice(CHARS) for _ in range(rng.randint(0, 6)))
    if k in (5, 6): return [jval(rng, depth + 1) for _ in range(rng.randint(0, 3))]
    return {"".join(rng.choice(CHARS) for _ in range(rng.randint(0, 3))): jval(rng, depth + 1) for _ in range(rng.randint(0, 3))}

def canonv_chunk(args):
    """canon(str(v)) for any JSON value: the destination a non-string to.val becomes."""
    seed, n = args
    rng = random.Random(seed)
    lines, want = [], []
    for _ in range(n):
        v = jval(rng)
        lines.append(json.dumps({"op": "canonv", "v": v})); want.append({"canon": canon(v)})
    return compare(lines, want)

def wire_chunk(args):
    seed, n = args
    rng = random.Random(seed)
    lines, want = [], []
    for _ in range(n):
        msg = {}
        if rng.random() < .95:
            msg["v"] = rng.choice(["hi", "", None, 3, ["x"], {"a": 1}, "evil@attacker.io"])
        if rng.random() < .8:
            msg["l"] = rng.sample(["user", "plan", "untrusted:m1", "verified:m1", "untrusted:agent:x"], rng.randint(0, 3))
        agent = rng.choice(["x", "bob", "", "a:b"])
        try:
            w = {"enc": encode(decode(msg, agent))}
        except KeyError:
            w = {"err": "MissingValue"}
        lines.append(json.dumps({"op": "wire", "msg": msg, "agent": agent})); want.append(w)
    return compare(lines, want)

def compare(lines, want):
    out = subprocess.run([CLI], input="\n".join(lines) + "\n", capture_output=True, text=True, check=True).stdout.splitlines()
    assert len(out) == len(lines), "CLI returned %d lines for %d requests" % (len(out), len(lines))
    bad = [(l, w, json.loads(o)) for l, w, o in zip(lines, want, out) if json.loads(o) != w]
    return len(lines), bad[:5], len(bad)

def phase(name, pool, fn, jobs):
    total, nbad, shown = 0, 0, []
    for n, sample, b in pool.imap_unordered(fn, jobs):
        total += n; nbad += b; shown += sample
    print("%-7s %9d cases  %d disagreements" % (name, total, nbad))
    for l, w, o in shown[:5]:
        print("   python:", w, "\n   rust:  ", o, "\n   input: ", l[:400])
    return nbad

if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 1_000_000
    with Pool() as pool:
        bad = phase("decide", pool, decide_chunk, [(1000 + i, min(CHUNK, n - i * CHUNK)) for i in range((n + CHUNK - 1) // CHUNK)])
        bad += phase("canon", pool, canon_chunk, [(lo, min(lo + 0x4000, 0x110000)) for lo in range(0, 0x110000, 0x4000)])
        bad += phase("intent", pool, intent_chunk, [(2000 + i, CHUNK) for i in range(10)])
        bad += phase("canonv", pool, canonv_chunk, [(4000 + i, CHUNK) for i in range(10)])
        bad += phase("wire", pool, wire_chunk, [(3000 + i, 5000) for i in range(4)])
    print("FAIL" if bad else "OK: zero disagreements")
    sys.exit(1 if bad else 0)
