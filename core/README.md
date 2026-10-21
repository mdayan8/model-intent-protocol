# mip-core

The MIP kernel in Rust: labeled values, `canon` (emails and generic destinations such as
IBANs, URLs and Slack ids), `lookalike`, `resolve_intent`, `named`, `decide`, and wire
`encode`/`decode`. It is a line-for-line port of `../mip.py`, which is the spec.
The library does no I/O. Its dependencies are `unicode-normalization` (pinned) and `serde_json`.

```
cargo test --release                              # unit tests, demo scenarios, mip.py's fuzz invariants A-D
cargo build --release && python3 diff.py 2000000  # differential test against mip.py
cargo bench                                       # decide() latency
```

## Differential test

`diff.py` imports `../mip.py` and generates inputs, sends each one to `target/release/mip-cli`
as a JSON line, and exits non-zero on any disagreement. For `decide`, it compares both the
verdict and the rule that fired (`Rule::code`, mapped from mip.py's reason text). It does
not compare the reason text itself.

| Phase | Inputs | Cases | Disagreements |
|---|---|---|---|
| decide | Half exactly `mip.fuzz()`'s distribution, including `None`/`42` destinations and the `""` contact. A quarter wider: Unicode tricks, whitespace Python strips, non-string values, empty and multiple labels, negative offsets, random sources, verified records and contacts. A quarter destination-focused: clean body, prompts with IBANs (spaced and not), URLs, Slack ids, short names, Cyrillic lookalikes, punycode, full-width text, and destinations cut from the prompt across spaces | 2,000,000 | **0** |
| canon | Every code point except surrogates, in 7 templates (5 email, 2 non-email) | 7,784,448 | **0** |
| intent | Random prompts: addresses, IBANs, URLs, `#channels`, role words, `ſ`/`İ`, combining marks, Unicode separators and the tokenizer's split characters. Compares `dests`, `roles`, `text` and the readback | 200,000 | **0** |
| canonv | `canon(v)` for random non-string JSON values (`None`, booleans, ints, floats, nested lists and dicts) and strings: anything that is not a string is never a destination | 200,000 | **0** |
| wire | `encode(decode(msg, agent))`, including a missing `v` | 20,000 | **0** |

All 14 rules fire in the decide phase. In 200k sampled cases, about 3,800 allow a
non-email destination and about 4,000 are named only through the `text` path (the 8+ character
substring rule).

No inputs are excluded. Per 2M decide cases, about 420 combine a `""` contact with a
destination of 2 characters or less, and about 110k have a non-string destination.

Run against mip.py md5 `f8df4cf8fb01f3bb331f795c8cfac5bc`: spec v0.4, with the string-only
`canon` and the `lookalike` fix. The md5 was the same before and after the run.

## Benchmark

Apple M2, `cargo bench`, best of 5 runs of 1M calls each. Inputs are mip.py's demo world.

| Case | Latency |
|---|---|
| allow, full path (quote check, echo scan of both sources, lookalike) | **782 ns** |
| allow, full-width Unicode destination (NFKC path) | 1,017 ns |
| allow, spaced IBAN named through the prompt `text` | 827 ns |
| deny, destination not named | 206 ns |

The target is under 10 µs (SPEC §7). The worst case measured is about 10x under it.

## Findings

1. **Unicode version is part of the spec.** The first run found 16 `canon`
   disagreements. `unicode-normalization` 0.1.26 uses Unicode 18. Characters added after
   Unicode 16, such as U+A7F1 and U+209D, NFKC-fold to ASCII letters there, so Rust
   accepted `a꟱b@corp.com` and Python 3.14 (Unicode 16.0) rejected it. The crate is
   now pinned to `=0.1.24` (Unicode 16.0), and a test asserts the version. **SPEC should name
   the Unicode version, and Python, Rust and Lean must move together.**
2. **Python's `strip()`, `\s` and `\w` are not Rust's.** Python strips `\x1c`–`\x1f` and
   Rust's `trim()` does not, so `"boss@corp.com\x1c"` is canonical in mip.py. Python's `\w`
   excludes combining marks and includes `²` and `Ⅻ`. Rust's regex does the opposite.
   With `re.I`, `ſ` matches `s` and `İ`/`ı` match `i`. `src/tables.rs` is generated from
   Python by `gen_tables.py`, and the parsing is hand-written to match it, so `regex` is
   not a dependency.
3. **Bugs found in mip.py, already fixed upstream while this crate was being written.**
   Empty labels were treated as trusted, which contradicted SPEC §3.2. A destination
   with 2+ `verified:` labels picked one through `next(iter(frozenset))`, so the verdict
   depended on `PYTHONHASHSEED`. The current mip.py has `trusted()` and denies multiple
   verified labels. This crate matches it (rules U1 and R0), and the diff covers both
   cases.
4. **Found and fixed: non-string destinations.** In v0.4, `canon(str(to.val))` made `None` the
   destination `"none"` and `42` the destination `"42"`. Matching that needed a port of
   Python's `str()`/`repr()`, including float repr. That port surfaced serde_json's inexact
   float parsing and the fact that Python breaks shortest-repr ties to even. mip.py
   `f8df4cf8` now returns None for any non-string, and Rust does the same
   (`to.val.as_str()`), so the port is gone.
5. **Found and fixed: an empty contact disabled the lookalike check (it failed open).**
   `dist("", d) <= 2` for any destination of 2 characters or less, so `lookalike()` returned
   `""`, and `if near:` read that as "no lookalike". `hash("")` is 0, so `""` came first in
   every set, and a real lookalike after it was never reached. With contacts `{"", "ac"}`,
   sending to `"ab"` was a silent ALLOW under every `PYTHONHASHSEED`. The first v0.4
   differential run found 45 such cases. mip.py `f8df4cf8` now skips empty contacts and
   checks `near is not None`, which is what Rust does (test
   `empty_contact_does_not_hide_a_lookalike`). The diff includes `""` contacts again.
6. **Known deviations outside the diff:**
   - `decode` returns `Err(BadLabels)` when `"l"` is not a list of strings. mip.py
     accepts any iterable, so `"l": "user"` becomes the labels {u, s, e, r}. Both still
     add `untrusted:agent:<x>`, so this is not a safety issue.
   - Lone surrogates can't exist in Rust strings or UTF-8 JSON.
   - Quote offsets are `i64` where Python allows any size of integer.
   - Reason text quotes values with Rust `{:?}`. A C3 reason names the
     first contact in sorted order, while mip.py takes set order.
   - `str.lower()` on source text for the echo check uses Rust std's Unicode tables. These
     may be newer than 16.0, which only matters for characters assigned after 16.0.

Skipped: `join` and the plan interpreter, which belong to the Monty fork (phase 2b).
