# MIP kernel in Lean 4

Phase 1 of SPEC.md §10: a model of `decide()` from `mip.py` and proofs of the three theorems in SPEC.md §8.
Lean 4.35.0, core library only, no Mathlib.

```bash
lake build
```

| File | Contents |
|---|---|
| `MIP/Kernel.lean` | Labels, atoms, values, intent, Python slicing, `decide` (as `bodyOk` + `destVerdict`), `resolveIntent` |
| `MIP/Theorems.lean` | The proofs |
| `MIP/Examples.lean` | mip.py scenarios checked by evaluation, plus the spec gap below |

## Theorems

Each one is proven for every verdict other than `deny`, so it covers `confirm` as well as `allow`. Each has an `_allow` corollary that matches the wording in the spec. All of them use the SPEC.md §3.2 reading: `trusted(src)` means non-empty and only `plan`/`user`, so empty and unknown labels count as untrusted.

1. `dest_trusted_or_role`: the destination's labels are trusted, or they are exactly one `verified:mid` label and the intent has the `sender` role. `no_untrusted_dest` gives the spec's wording as a corollary: a destination with any `untrusted…` label is denied.
2. `dest_authorized`: the destination canonicalizes to some `d`, and one of these holds:
   - its labels are trusted and `named d intent` holds (`d` is in `intent.dests`, or `d` is at least 8 characters and a substring of `intent.text`), or
   - its labels are exactly `[verified:mid]`, the `sender` role is in the intent, `pc` is clean, and `verified[mid] = d`.

   Two lemmas link the first case back to what the user typed:
   - `named_means_typed`: a destination in `resolveIntent`'s dests is `canon r` for some raw email or token `r` in the prompt. A rejected email entry (`!raw`) can never match.
   - `named_text_typed`: a destination named through the text branch is at least 8 characters and a contiguous substring of the user's canonical prompt. This covers an IBAN typed with spaces. It rests on `isSubstr_infix`, which proves that the model's `p in s` really is a substring test.
3. `body_quotes_exact`: at most one body atom is not trusted. Every atom that is not trusted has a quote span `(sid, a, b)`, its source exists, `source[a:b]` equals the atom exactly (with Python slice semantics, negative indices included), the atom is a contiguous substring of the source, and it is at least 12 characters long.

`#print axioms` reports only `propext`, `Classical.choice` and `Quot.sound`. No `sorry`, no `native_decide`.

## Modelled abstractly, and why

These are fields of `Prims`. The theorems hold for every possible choice of them.

- **Unicode canonicalization (`canon`, `normLower`, `normPrompt`).** `canon` covers emails (NFKC, lowercase, ASCII, address regex) and, since v0.4, other destinations such as IBANs, URLs and channels (whitespace removed, printable ASCII, no `xn--`, length at least 2). `normPrompt` builds the intent's `text`: NFKC, lowercase, all whitespace removed. NFKC needs the Unicode decomposition tables, and the checks are regexes. Neither exists in Lean core. Modelling them would be large, and the safety argument does not depend on them: the theorems talk about whatever `canon` returns.

  The proofs assume one property only: a `canon` output that contains `@` never starts with `!`. mip.py's email regex guarantees this. It is narrower than before because v0.4's non-email branch accepts `!` (`canon("!ab") == "!ab"`). `resolve_intent` marks rejected emails as `"!" + raw`. Every raw email contains `@`, which `named_means_typed` takes as a hypothesis on the extraction regex, so a rejected entry still never matches.

  Getting homoglyphs right is the job of differential testing against the Rust kernel, not of these proofs.
- **Edit distance is no longer abstract.** `editDist` and `lookalike` are concrete, with the same recurrence as `dist` in mip.py, so the empty-contact case below can be checked. No safety theorem depends on them: they can only turn `allow` into `confirm`.
- **Prompt parsing in `resolve_intent`.** The regexes are parameters: `extract` (email-like raw addresses), `tokens` (other tokens, already split, with `.:` stripped and no `@`), and `roles` (role words).

## Modelling choices to mirror in the differential tests

- Strings are `List Char`, meaning code points, as Python `str` is. Values are strings by type, so mip.py's newer rule that `canon` returns `None` for non-strings (None, numbers) holds trivially here. Differential tests must map a non-string destination to `canon = none`, so it is denied. A Python `V` holding a non-string (such as a bool) is always denied by mip.py when used as a quote, so the Rust kernel should take strings only.
- Label strings map to constructors: `"plan"`, `"user"`, prefix `"verified:"` → `verified id`, prefix `"untrusted"` → `untrusted rest`, anything else → `other`.
- `Labels` is a list standing for a Python set, so encode it without duplicates. `to.src = [verified mid]` is mip.py's `len(to.src) == 1`.
- In `verified`, a `None` value (sender failed `canon`) behaves as a missing key.
- `decide` returns the verdict only. The reason strings are left out.

## Found and fixed

Phase 1 found that mip.py's `decide` did not follow SPEC.md §3.2, which says "Values without labels are treated as untrusted". It treated empty and unknown label sets as trusted:

- An unlabeled destination named by the user was allowed.
- An unlabeled body atom with no quote span, such as `"CEO approved the $10,000 wire"`, was never checked against a source, so the send was allowed.
- A destination with an unknown label such as `"foo"` was allowed.

Separately, when a destination carried two `verified:` labels, the result depended on set iteration order (`next(iter(to.src))`).

mip.py now fails closed. `trusted(src)` requires a non-empty subset of `{plan, user}`. Body atoms are checked unless they are trusted, destinations are denied unless they are trusted, and more than one verified label is denied. This model matches that. All four cases are checks in `MIP/Examples.lean` that now evaluate to `deny`, and the same inputs return `deny` from the updated mip.py. Theorems 1 and 3 are now proven in the stronger §3.2 form.

### Empty contact disabled the lookalike check (found by the Rust differential test)

mip.py's `lookalike` could return the empty contact `""`, because any 2-character destination is within edit distance 2 of it. `decide` then tested the result for truthiness, and `""` is falsy, so the check was skipped and the send was allowed.

- **Example:** contacts `{"", "ac"}`, destination `"ab"`. When set iteration visited `""` first, the verdict was `allow` instead of `confirm`.
- **Fix in mip.py:** `lookalike` now skips empty contacts, and `decide` tests `near is not None`. `canon` now also returns `None` for non-string input.
- **In this model:** the result was already an `Option` tested with `isSome`, but `lookalike` was abstract, so the case couldn't be expressed. It is now concrete and skips empty contacts. The lemma `lookalike_ne_empty` proves it never returns an empty contact. `MIP/Examples.lean` checks that contacts `["", "ac"]` with destination `"ab"` give `confirm`, the same result as the fixed mip.py.
