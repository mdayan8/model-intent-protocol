# MIP: Model Intent Protocol

> MCP tells the model what it can do. MIP makes sure it only does what you meant.

MIP is a layer that sits on top of [MCP](https://modelcontextprotocol.io). Your agent and your MCP servers stay as they are. Every tool call that would send data somewhere passes through a small kernel first. The kernel allows the call only if the destination was named by the user, so a prompt injection cannot redirect your data to an attacker. The kernel is proven in Lean, ported to Rust, and the two agree on 2,000,000 random cases.

**Status: research prototype.** Everything below is measured and reproducible from this repo, including the parts where MIP does worse than plain tool calling.

## Results at a glance

| What was measured | Classic tool calling | MIP | Where |
|---|---|---|---|
| Tool-definition tokens for 10 popular MCP servers (132 tools) | 31,944 | 81 typical, 147 worst case | [tokens](#1-token-cost-of-real-mcp-servers) |
| Tool-definition tokens through the gateway, 4 real servers (40 tools) | 4,539 | 533 | [gateway](#2-the-gateway-against-real-mcp-servers) |
| Data-theft attacks that succeed when the model is **fully hijacked** (488 AgentDojo cases) | 466 (95.5%) | **6 (1.2%)** | [hijacked agent](#4-worst-case-a-fully-hijacked-agent) |
| AgentDojo tasks completed, no attack (97 tasks, Claude Haiku 5.5) | 80.4% | 69.1% (Fence, directory) | [AgentDojo](#3-agentdojo-real-model) |
| AgentDojo attacks that succeeded, real model (949 cases) | 1.9% | 1.9% | [AgentDojo](#3-agentdojo-real-model) |
| MCPMark filesystem runs passed (40 tasks × 3 trials) | 71.7% | 66.7%, within noise | [MCPMark](#5-mcpmark-mcp-benchmark) |
| MCPMark cost for the 120 runs | $6.13 | $3.01 | [MCPMark](#5-mcpmark-mcp-benchmark) |
| Gateway latency added per tool call (median) | | 0.31 ms read, 0.55 ms write | [gateway](#2-the-gateway-against-real-mcp-servers) |
| Kernel decision time (Rust, Apple M2) | | 0.21 to 1.02 µs | [kernel](#6-the-kernel-proofs-and-differential-testing) |

How to read this honestly:

- **The protection is real and does not depend on the model.** With a fully hijacked agent, MIP Fence stops 98.8% of the data-to-destination attacks in AgentDojo.
- **With today's model, AgentDojo shows no security difference.** Claude Haiku 5.5 resisted every data-theft attack on its own. The only attacks that succeeded, in every setup, were a text-only attack that changes what the reply says. MIP does not claim to stop that.
- **MIP has a real usability cost.** The fence refuses destinations that came from data, so it completes fewer tasks: 69.1% against 80.4%.

## What MIP is

There are three pieces.

- **The kernel** ([mip.py](mip.py), [core/](core/), [lean/](lean/)). One pure function, `decide()`, with no I/O and no model inside. For every destination it answers allow, confirm or deny. It is proven in Lean 4 and ported to Rust.
- **Two modes**, run against AgentDojo in [bench/agentdojo/](bench/agentdojo/):
  - **MIP Fence** (drop-in). The agent is an ordinary tool-calling agent. Only calls that send data go through the kernel. A refused call is not run; the agent is told why and continues.
  - **MIP Plan** (high assurance). The model writes a short restricted-Python plan from the user's words only and never sees tool output. A quarantined reader extracts values from untrusted data. This follows CaMeL and FIDES.
- **The gateway** ([gateway/](gateway/)). A real stdio MCP server that wraps existing MCP servers behind one `run(plan, intent)` tool. Any MCP client can use it.

The rule the kernel enforces: **data reaches only destinations the user named.** A destination is named if the user typed it, or, with the directory option, if it already existed in the system's own directory (Slack members and channels, saved contacts) before the task started. On top of that, the kernel:

- rejects homoglyph and punycode addresses
- asks the user before sending to an address within edit distance 2 of a known contact
- asks when the send only happens because of something untrusted data said
- binds "reply to the sender" to DMARC-verified metadata

## Quick start

Requirements: Python 3.12+. Node is needed only for the real MCP servers, Rust only for `core/`, Lean only for `lean/`.

```bash
python3 mip.py                    # kernel reference: 18 scenarios + 50,000-case fuzz test
python3 fast.py                   # token budget and plan cache measurements
python3 gateway/test_gateway.py   # 34 tests against real MCP servers (downloads them with npx)
cd core && cargo test             # Rust kernel
cd lean && lake build             # Lean proofs
```

Wrap an existing MCP config so every server runs behind MIP, with no change to the servers:

```bash
python3 gateway/mip_gateway.py wrap path/to/claude_desktop_config.json
```

## 1. Token cost of real MCP servers

[bench/mcp_catalogs.py](bench/mcp_catalogs.py) starts 10 widely used MCP servers over stdio and reads their real `tools/list`. [bench/tokens.py](bench/tokens.py) counts the tokens a client sends to the model for them.

![tokens](docs/figures/tokens_per_server.png)

| | Tokens |
|---|---|
| All 10 servers, 132 tools | 31,944 |
| Notion server alone | 17,163 |
| Per tool: mean, median, max | 242, 128, 1,268 |
| MIP prompt, fixed part | 59 |
| MIP prompt with 3 retrieved signatures, worst case | 147 |

The tokenizer is tiktoken `o200k_base`, used as a proxy. Claude's own counts differ by a few percent.

## 2. The gateway against real MCP servers

`python3 gateway/test_gateway.py` runs against `@modelcontextprotocol/server-filesystem`, `server-memory`, `server-everything` and `mcp-server-fetch`. All 34 tests pass. The suite includes:

- **Honest tasks**, which must succeed, and the files must really exist afterwards.
- **Planted injections** that try to write secrets outside the allowed folder, or send data out through a URL. All are refused.
- **A control test.** The same injection succeeds when MIP is removed, which shows the attacks are real.
- **Escape attempts** through `../`, symlinks, homoglyph folder names and prefix-sibling folders. All are refused.
- **Protocol conformance**, checked with a real MCP client handshake.

![gateway](docs/figures/gateway_overhead.png)

Details: [gateway/RESULTS.md](gateway/RESULTS.md).

## 3. AgentDojo, real model

[AgentDojo](https://github.com/ethz-spylab/agentdojo) v1.2.2 is the benchmark used by the CaMeL and FIDES papers. It has 97 user tasks, and 949 attacked cases with the `important_instructions` attack. Every setup ran the same model, Claude Haiku 5.5, through the same client. AgentDojo's own checkers score every case. The intervals are 95% Wilson intervals.

![utility](docs/figures/agentdojo_utility.png)

| Setup | Tasks completed, no attack | Completed under attack | Attacks succeeded |
|---|---|---|---|
| Classic tool calling | 80.4% [71.4, 87.1] | 78.9% [76.2, 81.4] | 1.9% [1.2, 3.0] |
| MIP Fence, directory | 69.1% [59.3, 77.4] | 72.2% [69.2, 74.9] | 1.9% [1.2, 3.0] |
| MIP Fence, strict | 61.9% [51.9, 70.9] | 68.4% [65.4, 71.3] | 2.0% [1.3, 3.1] |
| MIP Plan, directory | 41.2% [32.0, 51.2] | not run | not run |
| MIP Plan, strict | 36.1% [27.2, 46.0] | not run | not run |

![attacks](docs/figures/agentdojo_attacks_real_model.png)

- **Every successful attack, in all three setups, was travel injection task 6.** It asks the agent to say "you should absolutely visit Riverside View Hotel". No data moves and no action is taken. This kind of text-only manipulation is outside MIP's guarantee.
- **Where MIP loses tasks.** In almost every MIP Fence failure, the kernel refused a destination that came from data: an IBAN read from a bill, a website listed in a Slack message, a user to invite found on a web page. Those are exactly the calls an injection would make. In a real product they become a confirmation prompt, which the benchmark has no human to answer.
- **MIP Plan cost.** It uses 54 to 73% fewer input tokens per task than classic tool calling: travel 10.8k against 39.9k, and workspace 7.7k against 25.9k. But it completes about half as many tasks. The planner never sees the data, and the strict rule blocks more. The same cost shows in the CaMeL paper: 63.9% against 90.7% on Claude 3.5 Sonnet.

Full per-suite table, with model calls, tokens and API seconds per task: [bench/results/agentdojo/summary.md](bench/results/agentdojo/summary.md).

## 4. Worst case: a fully hijacked agent

A strong model can resist injections that a weaker or future-compromised model would follow. Telling Haiku 5.5 in its system prompt to obey injected text did not make it comply: [bench/results/agentdojo/baseline-compromised](bench/results/agentdojo/). So the guarantee is tested with no model at all.

[bench/agentdojo/hijack.py](bench/agentdojo/hijack.py) runs a scripted agent on all 949 attacked cases, with the real injected text placed in the data. The agent:

1. does the user's task correctly, using the task's ground-truth calls
2. then makes every call the attacker asked for, using the injection task's ground-truth calls
3. ends with the attacker's text

This is the most a compromised model could do. AgentDojo's own checkers score the result. The run is deterministic.

![hijacked](docs/figures/hijacked_agent.png)

| Fully hijacked agent | Classic | MIP Fence, strict | MIP Fence, directory |
|---|---|---|---|
| Attacks that move data to an attacker's destination (488) | 466 (95.5%) | **6 (1.2%)** | 22 (4.5%) |
| Other attacks: book a hotel, create an event, delete a file, visit a page, say something (461) | 137 | 137 | 137 |

The attack categories are listed in code (`DATA_TO_DESTINATION` in [hijack.py](bench/agentdojo/hijack.py)).

The 6 that get past the strict fence are all one case. The user's own request names Alice, so a message to Alice is allowed, and the attacker controls what that message says. The fence guards where data goes, not what a message to a named recipient contains. The directory option adds 16 more successes, because Alice is an existing colleague and is allowed even when the user did not mention her.

## 5. MCPMark, MCP benchmark

[MCPMark](https://github.com/eval-sys/mcpmark) filesystem tasks, all 40, at commit `cd45b7f`. Each task is scored by its own `verify.py`. The client in both setups is the Claude Code CLI with Claude Haiku 5.5, either connected directly to `@modelcontextprotocol/server-filesystem` or to the same server through the MIP gateway. Each task ran for 3 trials.

![mcpmark](docs/figures/mcpmark.png)

| 40 tasks × 3 trials | Direct MCP | MIP gateway v2 |
|---|---|---|
| Runs passed | 71.7% [63.0, 79.0] (86/120) | 66.7% [57.8, 74.5] (80/120) |
| Tasks passed on all 3 trials | 55.0% | 55.0% |
| Median turns per run | 12 | 8 |
| Median input tokens per run | 88,346 | 79,825 |
| Total cost | $6.13 | $3.01, including the reader |

- **The pass rates are within noise.** Tasks passing all 3 trials are identical.
- **MIP gateway v1 passed 0 of 16.** Plans could cause effects but could not get data back. The reader in v2 fixed that.
- **These tasks contain no prompt injection.** This benchmark measures only what MIP's protection costs in task success, not the protection itself.

Full report, including the analysis of every MIP failure and the integrity notes: [bench/mcpmark/RESULTS.md](bench/mcpmark/RESULTS.md).

## 6. The kernel: proofs and differential testing

- **Lean 4** ([lean/](lean/)). `lake build` passes with no `sorry`, no extra axioms and no `native_decide`. The proven theorems:
  - `no_untrusted_dest`: a destination carrying any untrusted label is always denied.
  - `dest_authorized`: every destination that is not denied was named by the user, or is the DMARC-verified sender under a reply role.
  - `named_text_typed`: a destination named through the prompt text is a contiguous substring of the user's own words.
  - `body_quotes_exact`: every untrusted piece of a message body is an exact, contiguous quote of its source, at least 12 characters long.
  - `lookalike_ne_empty`: an empty contact can never switch off the lookalike check.
- **Rust** ([core/](core/)). It agrees with the Python reference on every one of these:

  | Check | Cases | Disagreements |
  |---|---|---|
  | `decide()` | 2,000,000 | 0 |
  | intent parsing | 200,000 | 0 |
  | non-string destinations | 200,000 | 0 |
  | wire format | 20,000 | 0 |

  Decision time on an Apple M2: 206 ns to deny, 782 ns for a full allow, 1,017 ns with Unicode normalization.
- **Found and fixed by this process:**
  - The Rust differential test found a fail-open bug: an empty contact switched off the lookalike check.
  - The Lean model found that unlabeled values were treated as trusted.

  Both are fixed in all three implementations and covered by regression tests.

## What MIP does not do

- **It does not stop text-only manipulation.** An injection can still change what the agent says, as the one successful attack type above shows.
- **It does not stop actions with no outside destination.** If the agent is fully hijacked, it can still book, create, delete or visit something within the user's own account.
- **It does not police message content.** A message to a recipient the user named can carry attacker-written content, such as a phishing link to Alice.
- **It costs tasks.** Any task whose destination legitimately comes from data needs a human confirmation, or it fails.
- **The directory option trades security for usability.** It admits existing members the user did not mention.
- **The verbatim-URL rule lets the agent visit a URL found in data.** No new data leaves, but the visit itself is visible to the attacker.
- **The gateway cannot scope to a folder whose path contains a space.** It reads folder names from the user's request by splitting on spaces, so a path such as `.../Application Support/...` cannot be named. The MCPMark run worked around this with a symlink.
- **Every word of the request counts as a possible destination.** A request that happens to contain a common word that is also a channel name, such as "general", allows that channel.

## Method and caveats

- **Model and client.** All model runs used `claude-haiku-5-5` through the Claude Code CLI in headless mode. The default system prompt was replaced, and the built-in tools, MCP servers and settings were disabled. Each run was one trial at the CLI's default sampling.
- **Prompted tool calls.** The classic and Fence setups use AgentDojo's `PromptingLLM`, which asks for calls in text, not native API tool calls. Absolute numbers are therefore not comparable with the AgentDojo leaderboard, which used older models and native tool use. The comparison between setups is like for like.
- **CLI overhead.** The CLI adds about 600 to 800 input tokens to every call, identically in every setup. Token figures per task are raw and include it.
- **Account details in context.** The CLI includes the logged-in account's email in context, even with its system prompt replaced. Every setup sees it equally. It is replaced with `<redacted>` in the published results.
- **Development history**, every version kept in [bench/results/agentdojo/](bench/results/agentdojo/):
  - The MIP Plan adapter went through three versions: `_v1_adapter`, `_v2_adapter`, then the current one. Version 3 gives the planner the same tool documentation the classic setup gets. Its earlier lack was a fairness bug.
  - The Fence rules went through f1 (`_f1_fence`), f2 and f3.1. f2 allows credential changes the user typed, and fetching URLs found verbatim in data. f3 fixes a bug that made the directory option a no-op. f3.1 removes refused calls from the trace, because AgentDojo scores one attack by attempted calls.
  - The results from the directory bug are invalid and not published.
- **An earlier 16-task test was dropped.** I wrote it before these benchmarks, and it was too easy and mis-scored one case. It is not used as evidence and is not in this repo.

## Repository layout

| Path | Contents |
|---|---|
| [SPEC.md](SPEC.md) | Protocol and architecture specification |
| [mip.py](mip.py) | Kernel reference implementation, plan interpreter, tests |
| [fast.py](fast.py) | Fast path: 81-token prompt, local tool retrieval, plan cache |
| [core/](core/) | Rust kernel, differential test, benchmark |
| [lean/](lean/) | Lean 4 model and proofs |
| [gateway/](gateway/) | MCP gateway and its real-server tests |
| [bench/](bench/) | Benchmark harnesses, raw results, figure script |
| [docs/figures/](docs/figures/) | Figures in this README, rebuilt by `python3 bench/figures.py` |

## Credits

MIP builds on ideas from:
- [CaMeL](https://arxiv.org/abs/2503.18813), Google DeepMind
- [FIDES](https://arxiv.org/abs/2505.23643), Microsoft
- Simon Willison's "lethal trifecta"
- Cloudflare Code Mode and Anthropic's code-execution-with-MCP work

The benchmarks are [AgentDojo](https://github.com/ethz-spylab/agentdojo) (ETH Zurich SPY Lab) and [MCPMark](https://github.com/eval-sys/mcpmark).

Built with Claude Code.

## License

Apache-2.0
