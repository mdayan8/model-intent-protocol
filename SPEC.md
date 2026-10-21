# MIP: Model Intent Protocol

**Draft 0.3, 2026-10-21. Working name.**

> MCP tells the model what it can do. MIP makes sure it only does what you meant.

MIP is an open protocol and runtime for AI agents that call tools. It makes three promises:

1. **Provably safe.** A hijacked agent cannot send your data anywhere you did not choose. This is enforced by a small, formally verified kernel, not by the model behaving well.
2. **Radically cheaper and faster.** The model sees a fixed catalog of about 1,000 tokens however many tools exist, and does multi-step work in one round trip.
3. **Zero adoption cost.** It speaks MCP on the wire, works with every existing MCP server and OpenAPI spec on day one, and needs no change from any provider.

The executable reference is [mip.py](mip.py). It runs 18 attack and utility scenarios and a 50,000-case fuzz test of the kernel.

---

## 1. Why MIP exists

| Problem with tool use today | Evidence | MIP's answer |
|---|---|---|
| Tool definitions flood the context | 550 to 1,400 tokens per tool. One team lost 143k of 200k tokens to three servers. MCP used 4 to 32 times more tokens than a CLI in Scalekit's benchmark. | Fixed three-tool core with on-demand search (§4) |
| Every step is a model round trip | Each removed round trip saves about 1 to 5 seconds. Wire format saves microseconds. | Plans are code: one round trip for many calls (§4) |
| Prompt injection steals data | The "lethal trifecta": private data, untrusted content, and a way out. Most useful agents have all three. | Label-tracked data flow, enforced outside the model (§5) |
| Tool poisoning and rug pulls | The postmark-mcp backdoor BCC'd every email to an attacker after 15 clean versions. | Hash-pinned manifests, descriptions treated as data (§6) |
| Agents hold broad keys | Logs say only "the agent did it". | Keyless agents with one-shot, attenuable capabilities (§5.6) |
| Agent-to-agent hops break guarantees | "Can CaMeLs Talk?" (October 2026) showed single-agent defenses don't compose. | Labels travel on the wire across every hop (§3.2) |

Every challenger to MCP that demanded a rewrite has stalled, including UTCP, agents.json, ANP and IBM ACP. Every standard that displaced an incumbent kept compatibility first, as HTTP/2, HTTP/3 and JSON did. MIP takes that path. It is a strict superset of MCP that adds what MCP structurally lacks.

---

## 2. Architecture

```
 user ──prompt──▶ ┌─────────────┐   plan (restricted Python)   ┌────────────────────────────┐
                  │   PLANNER   │ ───────────────────────────▶ │        MIP RUNTIME         │
                  │ sees: user  │                              │  interpreter + labels      │
                  │ words, tool │ ◀─── small final result ──── │  ┌──────────────────────┐  │
                  │ signatures  │                              │  │ KERNEL decide()      │  │
                  └─────────────┘                              │  │ pure, verified       │  │
                                                               │  └──────────────────────┘  │
                  ┌─────────────┐   typed answers only         │  gateway: keys, nonces,    │
                  │   READER    │ ◀── untrusted text ────────▶ │  sources, contacts, seals  │
                  │ no tools    │                              └──────────────┬─────────────┘
                  └─────────────┘                                             │ MCP / HTTP
                                                                  existing MCP servers, OpenAPI,
                                                                  GraphQL, CLIs, WebMCP, agents
```

- **Planner.** Sees only the user's words and tool signatures. It never sees tool output, so injected text cannot reach it. It writes the plan.
- **Reader.** A quarantined model that reads untrusted content and has no tools. It answers only in types: bool, enum, number, or one verified quote.
- **Runtime.** Interprets the plan, labels every value, and calls the kernel before every side effect.
- **Kernel.** One pure, total function, `decide()`, with no I/O and no model. It is the entire trusted policy core and is small enough to verify formally.
- **Gateway.** Holds API keys in the OS keychain, the spent-nonce set, source texts, the user's contacts and plan seals. The agent holds none of these.

---

## 3. Wire protocol

### 3.1 MCP compatibility

MIP runs over MCP 2026-07-28: stateless, Streamable HTTP, `Mcp-Method` and `Mcp-Name` headers.

- **MIP client to any MCP server.** Works today. The server's tools become typed stubs.
- **Any MCP client to MIP.** The runtime appears as one MCP server exposing the three tools in §4. Claude, Cursor, Codex and ChatGPT can use it the day it ships.
- **MIP-aware servers** advertise the `mip` extension and add labels to results.

Transport is stateless HTTP/3 with pooled connections, falling back to HTTP/2. Payloads are JSON, with CBOR negotiated for large or binary data. Binary RPC formats are deliberately out of scope, because they save microseconds against seconds of model time.

### 3.2 Labeled values

Any value crossing a MIP boundary may carry labels:

```json
{"v": "Invoice 4471 is due Friday.", "l": ["untrusted:mail:m1"], "q": ["mail:m1", 0, 27]}
```

| Field | Meaning |
|---|---|
| `v` | The value |
| `l` | Provenance labels: `user`, `plan`, `untrusted:<source>`, `verified:<source>` |
| `q` | Optional quote span: source id, start, end |

**The accumulation rule:** a receiver adds `untrusted:agent:<sender>` to everything it receives from another agent and never removes a label. A remote agent cannot vouch for itself. Values without labels are treated as untrusted.

### 3.3 Verified sources

Only cryptographically authenticated metadata may carry `verified:`:

- Email `From` with DMARC pass (DKIM `d=` aligned), bound to Message-ID and a 7-day freshness window.
- HMAC webhooks with a secret registered for that provider (Standard Webhooks, Slack, GitHub).
- HTTP Message Signatures (RFC 9421) or Web Bot Auth checked against a domain's JWKS.
- JWS-signed manifests.

Page text, tool return values, CLI output and model output are never verified.

---

## 4. Model interface: fast and cheap by construction

The model always sees exactly three tools, byte-identical across sessions, so the prompt cache is never invalidated. Cached reads cost a tenth of normal input.

| Tool | Purpose |
|---|---|
| `search(query) -> signatures` | Finds tools among thousands; returns TypeScript-style signatures |
| `execute(plan)` | Runs a restricted-Python plan; only the final result returns |
| `call(name, args)` | Single lookup with no plan, for the simplest cases |

**Signatures, not JSON Schema.** Tools are shown to the model as one-line signatures with a short doc comment, optional fields marked, string unions instead of enums, and one or two examples for tricky parameters. Each costs about 40 to 120 tokens. Servers still publish JSON Schema for validation, but the model never reads it.

```ts
/** Send an email. Destination must be one the user named. */
send_email(to: string, body: string): void
```

**Progressive disclosure.** A one-line category index sits in the prefix. Search results are appended to the conversation, never inserted into the prefix. Providers may pin their top 3 to 5 tools.

**Plans are code.** Measured results for this pattern:

| System | Before | After |
|---|---|---|
| Cloudflare Code Mode, 2,500 endpoints | 1.17M tokens | about 1,000 tokens |
| Anthropic code execution, Drive to Salesforce | 150k tokens | 2k tokens |
| Anthropic tool search, 58 tools | about 55k tokens | about 3.5k tokens, accuracy up from 49% to 74% |
| Code vs JSON calls, BFCL v4 | | code equal or better in 11 of 14 models |

**Result budgets.** Every tool result declares a size budget. Servers truncate or summarize before returning, so large data never enters the context.

### 4.0 The fast path (default since draft 0.3)

The three-tool core above is the fallback. The default path removes the search round trip and most model calls. The executable version is [fast.py](fast.py).

| Technique | What it does | Measured in fast.py |
|---|---|---|
| One tool | The model sees a single `run(code)` tool plus the 3 signatures it needs | 81 tokens of overhead, against a 120 target |
| Local retrieval | The runtime picks those 3 signatures from the user's words before the model runs, using an in-memory index | 10,502 tools, 0.6 ms per lookup in Python, 10 of 10 test phrasings found |
| Deterministic reader | Structured fields and quote selection are done by code. A model reader is called only when meaning truly needs one, and that model can run on the device | 0 model tokens for the reader |
| Plan cache | Sealed plans are stored with addresses and numbers turned into slots. A repeat task reuses the plan with new values | 0 model calls on a hit, 215 µs runtime |
| Streaming execution | Reads start as soon as their line is written. Writes wait for the full plan, because the seal covers the whole plan | 1.25 s against 1.55 s serial, simulated |
| One-command init | `mip wrap` rewrites an existing MCP config so every server routes through MIP unchanged | config rewrite shown in fast.py |

**Per-task budget on the fast path:**

```
first time : 81 overhead + user prompt (about 14) + plan (about 38) ≈ 133 model tokens, 1 model call
repeat     : 0 model tokens, 0 model calls
```

**Security is unchanged.** A cached plan only comes from a plan that was sealed and ran, so an attacker can't write to the cache. A hit still goes through plan checking, dry run, sealing and the kernel. The deterministic reader's quotes are verified like any other reader output.

**Limits of these numbers.** The tokenizer is a proxy for the model's own. The retrieval test set is small and was written for this prototype. Retrieval that misses a needed tool costs one extra model turn. Speed figures come from the Python prototype, so the Rust core should be faster.

### 4.1 The plan language

Plans are a strict subset of Python. To the model it is just Python. To the runtime it is a language small enough to analyze.

- **Allowed:** assignment, `if`, `for`, calls to known tools, string concatenation and f-strings, comparisons, lists, subscripts with constant keys.
- **Forbidden:** `def`, `class`, `import`, `while`, attribute access, `eval`, dunder names. Anything else is rejected before execution.
- **Errors** are fixed codes such as `E_FIELD`, never data, so errors cannot leak information.

---

## 5. Security model

### 5.1 The rule

> The agent never holds keys and never reads untrusted data.

Everything below enforces that rule mechanically.

### 5.2 Labels and STRICT control flow

- Every value carries its labels. Operations take the union of their inputs' labels.
- **STRICT mode.** Once a branch or loop depends on untrusted data, every later value and effect inherits that label. This closes the "send only if the email says X" one-bit leak. It is the mode CaMeL uses.

### 5.3 Intent fence

- Data may go only to destinations the user named. The runtime shows a readback such as "Data may go to: boss@corp.com" before running.
- In product UIs, destinations come from a structured pick, such as a contact chip, rather than free-text parsing.
- **Roles** let users name destinations without typing them. "Reply to the sender" binds to the verified sender of the message being replied to, never to body text.

### 5.4 The kernel decision

`decide(to, body, pc, intent, sources, verified, contacts)` returns `allow`, `confirm` or `deny`:

1. **Body.** Only planner text, plus at most one untrusted quote. The quote must be contiguous, at least 12 characters, and match its source exactly at its offsets.
2. **Canonical destination.** NFKC normalization, lowercase, ASCII only. Homoglyphs, mixed scripts and punycode are rejected.
3. **Role path.** A verified-only destination is allowed only with a matching role in the intent, a clean branch history, and a matching verified record.
4. **Untrusted destination.** Always denied.
5. **Not named by the user.** Denied.
6. **Named, but suspicious.** Ask the user if the send depends on an untrusted branch, if the address appeared in untrusted content first, or if it is within edit distance 2 of a known contact.
7. Otherwise **allow**, with zero clicks.

### 5.5 Sealed plans

The runtime dry-runs a plan with sinks stubbed and lists every effect. If all effects are `allow`, the plan is sealed automatically. Otherwise the user sees the list once. The seal is the SHA-256 hash of the exact plan, so any change after approval is refused.

### 5.6 Capabilities

- **Format:** Biscuit tokens. They are public-key signed, attenuable offline and carry Datalog caveats.
- **Binding:** bound to the agent's key, DPoP-style.
- **Scope:** one tool, one audience, expiry under 60 seconds, and a nonce recorded in the gateway's spent set so it works only once.
- **Delegated user auth:** OAuth 2.1 with PKCE, RFC 8707 `resource` always sent, RFC 9207 `iss` always checked, RFC 8693 token exchange with an `act` claim where supported.
- **Intersection rule:** the runtime drops any call outside agent policy, and the server enforces user-consented scopes.

### 5.7 What each attack hits

| Attack | Stopped by | In mip.py |
|---|---|---|
| Hijacked planner sends to attacker | Intent fence | yes |
| Destination taken from email text | Labels | yes |
| Reader invents a fact | Quote verification | yes |
| Many short quotes spliced together | One-quote rule plus minimum length | yes |
| User fooled into typing attacker address | Echo detection, confirm | yes |
| Homoglyph address | Canonicalization | yes |
| Lookalike domain | Contact edit distance, confirm | yes |
| Spoofed or replayed sender | DMARC plus freshness | yes |
| Branch leak | STRICT labels, confirm | yes |
| Sub-agent vouches for itself | Wire label accumulation | yes |
| Sandbox escape via imports or attributes | Plan language check | yes |
| Plan changed after approval | Seal | yes |
| Capability replay | Spent-nonce set | yes |
| Tool poisoning, rug pull | Pinned manifests (§6) | spec |
| Stolen token | Key binding, expiry under 60s | spec |
| Counting and timing channels | Fixed call budget per plan, normalized errors | spec |

### 5.8 Residual risks

These are stated openly, because no system in the literature solves them:

- **Text-only manipulation.** An injection can still change what a final answer says, without any data leaving. MIP marks untrusted spans in answers so users can see them.
- **In-scope misuse.** If the user asks to email someone, injected content can influence which true sentence gets quoted to them.
- **Outside persuasion.** A phone scammer who gets the user to type an address bypasses any software.

Data theft through the agent, the threat that matters most, is blocked by construction.

---

## 6. Discovery and supply-chain trust

### 6.1 Zero-cost discovery

Without any provider opt-in, the runtime tries these in order:

1. `/.well-known/agent-tools.json`
2. `/.well-known/agent-card.json` (A2A)
3. The MCP registry, with about 30,000 servers
4. `/openapi.json` and common paths
5. APIs.guru, with about 2,500 APIs
6. GraphQL introspection
7. CLI `--help`, which produces sandboxed, unverified tools only

### 6.2 Opt-in file

Providers who want better descriptions or the signed trust tier publish one file. Only `v` is required:

```json
{
  "v": 1,
  "openapi": "https://api.example.com/openapi.json",
  "auth": "https://api.example.com/.well-known/oauth-protected-resource",
  "overrides": {"refund": {"destructive": true, "requiresConfirmation": true}},
  "tools_digest": "sha256:…",
  "jwks_uri": "https://example.com/.well-known/jwks.json",
  "signatures": ["<detached JWS over the RFC 8785 canonical form>"]
}
```

The smallest valid file is `{"v":1,"openapi":"https://…/openapi.json"}`.

### 6.3 Trust tiers

Every tool shows one tier:

- **Signed.** The manifest's JWS verifies against the domain's keys, or the package has Sigstore provenance.
- **Domain-served.** Fetched over HTTPS from the API's own domain, unsigned.
- **Derived.** Converted from a directory or CLI help. It is sandboxed and every write needs confirmation.

### 6.4 Rules for every tier

- The tool-set digest is pinned at install. Any change disables the tool and shows a diff until the user approves it again. This blocks rug pulls.
- Descriptions are data. They are length-limited, instruction-like text is flagged, and a tool may not mention another tool.
- Packages are pinned to an exact version and hash, and are never auto-updated.

---

## 7. Implementation

| Component | Choice | Why |
|---|---|---|
| Core language | Rust | Memory-safe, no GC pauses, one binary for macOS, Linux and Windows |
| Plan interpreter | Fork of Pydantic Monty | Rust Python subset, starts in microseconds, MIT license, no filesystem, network or environment access by default, pause at every external call. Fallback: starlark-rust. |
| Rejected engines | QuickJS, V8, Pyodide | QuickJS has repeated memory-safety CVEs. V8 can't carry labels per value without a fork. Pyodide takes seconds to start. |
| Bindings | PyO3 for Python, napi-rs for Node, C ABI for Go | The same pattern Monty already ships |
| Keys | macOS Keychain, Windows Credential Manager, libsecret | The OS stores, never files |
| Capabilities | biscuit-auth crate | Mature Rust implementation |

**Performance targets:** under 1 ms runtime overhead per plan, excluding tool latency. Under 5 MB memory per plan. Kernel decision under 10 microseconds.

---

## 8. Verification

The approach follows AWS Cedar, where proofs found 4 bugs and differential testing found 21 more.

1. **Lean model** of `decide()`, canonicalization and label propagation.
2. **Proven theorems:**
   - No value labeled `untrusted` reaches a destination argument with an `allow` result.
   - Every `allow` destination was user-named or is a role-bound verified sender.
   - Every untrusted body atom is an exact substring of its source.
3. **Differential testing:** millions of random inputs run through both the Lean model and the Rust kernel. Any disagreement blocks a release.
4. **Public benchmark:** AgentDojo security and utility, token counts, and latency, published with every release.

---

## 9. Adoption plan

1. **Ship the runtime as one command** that wraps existing MCP servers with no change to them.
2. **The killer demo,** about 60 seconds. Replay the real GitHub "toxic issue" attack, where a malicious public issue makes an agent leak a private repo. Plain MCP leaks it. Add one line of config, change nothing in the server, run it again. MIP blocks it with a readable trace of where the data came from and where it was stopped, while using about 90% fewer tokens.
3. **Publish the benchmark.** Close to undefended utility, zero successful exfiltrations.
4. **Win one flagship host** as an opt-in "secure mode": Claude Code, Cursor, Codex or VS Code. LSP had VS Code, SPDY had Chrome.
5. **Integrate, don't compete,** with agentgateway, Docker MCP Gateway and Pydantic AI.
6. **Submit to the Agentic AI Foundation** within 3 to 6 months, with two independent implementations. Propose the labeled-value format as an MCP extension.
7. **Ratchet the default.** Once major hosts adopt it, secure mode becomes the default, as Let's Encrypt made HTTPS the default.

---

## 10. Build roadmap

| Phase | Deliverable | Done when |
|---|---|---|
| 0 | Executable spec, [mip.py](mip.py) and fast path, [fast.py](fast.py) | **Done.** 18 scenarios, a 50,000-case fuzz test and the fast-path measurements pass. |
| 1 | Lean model and proofs of the kernel | The three theorems in §8 are proven |
| 2 | Rust kernel plus Monty fork with labels | Differential tests agree with Lean on 10M cases |
| 3 | Gateway: keychain, Biscuit caps, MCP client, OpenAPI import | Wraps the GitHub MCP server unchanged |
| 4 | MCP bridge exposing search, execute and call | Works inside Claude Code and Cursor |
| 5 | Planner and reader wiring for Claude, GPT and local models | AgentDojo run published |
| 6 | Killer demo and launch | Public repo, video, benchmark |

Phase 1 and the Rust kernel part of phase 2 can run in parallel once this spec is frozen. Phases 3 and 4 depend on phase 2.

---

## Sources

- MCP 2026-07-28 and Cloudflare's stateless rewrite: blog.cloudflare.com/mcp-v2
- Code Mode: blog.cloudflare.com/code-mode, blog.cloudflare.com/code-mode-mcp
- Anthropic advanced tool use and code execution: anthropic.com/engineering/advanced-tool-use, anthropic.com/engineering/code-execution-with-mcp
- MCP vs CLI benchmark: scalekit.com/blog/mcp-vs-cli-use
- CaMeL: arXiv 2503.18813. FIDES: arXiv 2505.23643. Progent: arXiv 2504.11703. Can CaMeLs Talk?: arXiv 2610.05640. Design patterns: arXiv 2506.08837
- Code vs JSON calling: arXiv 2608.06370, arXiv 2402.01030. Notation study: arXiv 2605.29676
- Cedar verification: arXiv 2407.01688
- Lethal trifecta: simonwillison.net/2025/Jun/16/the-lethal-trifecta
- postmark-mcp backdoor: koi.ai/blog/postmark-mcp-npm-malicious-backdoor-email-theft
- Pydantic Monty: pydantic.dev/articles/pydantic-monty
- Biscuit: docs.rs/biscuit-auth. HTTP Message Signatures: RFC 9421
- Ranked MCP critiques: gradientflow.com/mcp-objections-and-complaints

Some figures come from secondary sources and must be checked against the primary source before publication.
