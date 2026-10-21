import MIP.Kernel

/-!
# The three safety theorems of SPEC.md §8

Each is proven for every non-`deny` verdict, which covers `allow` and also `confirm`,
under the SPEC.md §3.2 reading: empty and unknown label sets count as untrusted.
The `allow` form named in the spec follows as a one-line corollary.
-/
namespace MIP

variable {P : Prims} {to body : Value} {pc : Labels} {I : Intent}
  {S V : List (Str × Str)} {C : List Str}

/-- A Python slice is always a contiguous substring of what it slices. -/
theorem pySlice_infix (s : Str) (a b : Int) : pySlice s a b <:+: s :=
  (List.drop_suffix _ _).isInfix.trans (List.take_prefix _ _).isInfix

theorem bodyOk_of_ne_deny (h : decide P to body pc I S V C ≠ .deny) : bodyOk S body = true := by
  unfold decide at h; split at h <;> simp_all

theorem destVerdict_of_ne_deny (h : decide P to body pc I S V C ≠ .deny) :
    destVerdict P to pc I S V C ≠ .deny := by
  unfold decide at h; split at h <;> simp_all

/-- An `untrusted…` label anywhere makes a label set not trusted. -/
theorem trusted_false_of_untrusted {ls : Labels} (hu : untrusted ls = true) :
    trusted ls = false := by
  obtain ⟨l, hl, hlu⟩ := List.any_eq_true.mp hu
  simp only [trusted, Bool.and_eq_false_iff, List.all_eq_false]
  exact .inr ⟨l, hl, by cases l <;> simp_all [Label.isUntrusted, Label.isPlanOrUser]⟩

/-! ## Theorem 2 (stated first, the others follow): every non-deny destination is
user-named with trusted labels, or the single verified sender under a `sender` role -/

theorem dest_authorized (h : decide P to body pc I S V C ≠ .deny) :
    ∃ d, P.canon to.val = some d ∧
      ((trusted to.src = true ∧ named d I = true) ∨
        (∃ mid, to.src = [.verified mid] ∧ Role.sender ∈ I.roles ∧ untrusted pc = false ∧
          lookup V mid = some d)) := by
  have h := destVerdict_of_ne_deny h
  unfold destVerdict at h
  cases hc : P.canon to.val with
  | none => simp [hc] at h
  | some dest =>
    simp only [hc] at h
    refine ⟨dest, rfl, ?_⟩
    by_cases hv : (!to.src.isEmpty && to.src.all (·.verifiedId?.isSome)) = true
    · simp only [hv, ↓reduceIte] at h
      split at h
      · rename_i mid hs
        cases hpc : untrusted pc
        · simp [hpc] at h
          exact .inr ⟨mid, hs, h.1, rfl, h.2⟩
        · simp [hpc] at h
      · simp at h
    · simp only [hv, Bool.false_eq_true, ↓reduceIte] at h
      cases ht : trusted to.src
      · simp [ht] at h
      · by_cases hd : named dest I = true
        · exact .inl ⟨rfl, hd⟩
        · simp [ht, hd] at h

theorem dest_authorized_allow (h : decide P to body pc I S V C = .allow) :
    ∃ d, P.canon to.val = some d ∧
      ((trusted to.src = true ∧ named d I = true) ∨
        (∃ mid, to.src = [.verified mid] ∧ Role.sender ∈ I.roles ∧ untrusted pc = false ∧
          lookup V mid = some d)) :=
  dest_authorized (body := body) (S := S) (C := C) (by simp [h])

/-! ## Theorem 1: a non-deny destination is trusted (non-empty, only `plan`/`user`), or is
exactly one verified label bound to a `sender` role. Empty and unknown labels never pass. -/

theorem dest_trusted_or_role (h : decide P to body pc I S V C ≠ .deny) :
    trusted to.src = true ∨ ∃ mid, to.src = [.verified mid] ∧ Role.sender ∈ I.roles := by
  obtain ⟨_, _, ht | ⟨mid, hs, hr, _⟩⟩ := dest_authorized h
  · exact .inl ht.1
  · exact .inr ⟨mid, hs, hr⟩

theorem dest_trusted_or_role_allow (h : decide P to body pc I S V C = .allow) :
    trusted to.src = true ∨ ∃ mid, to.src = [.verified mid] ∧ Role.sender ∈ I.roles :=
  dest_trusted_or_role (P := P) (body := body) (pc := pc) (S := S) (V := V) (C := C)
    (by simp [h])

/-- The spec's wording: no `untrusted…`-labeled destination gets a non-deny verdict. -/
theorem no_untrusted_dest (h : decide P to body pc I S V C ≠ .deny) :
    untrusted to.src = false := by
  cases hu : untrusted to.src
  · rfl
  rcases dest_trusted_or_role h with ht | ⟨mid, hs, _⟩
  · simp [trusted_false_of_untrusted hu] at ht
  · simp [hs, untrusted, Label.isUntrusted] at hu

/-- Python `p in s` really is a contiguous substring test. -/
theorem isSubstr_infix {p s : Str} (h : isSubstr p s = true) : p <:+: s := by
  obtain ⟨i, -, hi⟩ := List.any_eq_true.mp h
  exact (List.isPrefixOf_iff_prefix.mp hi).isInfix.trans (List.drop_suffix _ _).isInfix

/-- "User-named" end to end, token branch: a canonical destination in `resolveIntent`'s
dests is `canon r` for a raw email or token `r` the user typed. A rejected `!raw` entry
never matches, because every raw email contains `@` (the extraction regex requires it). -/
theorem named_means_typed {extract tokens : Str → List Str} {roles : Str → List Role}
    {prompt x d : Str} (hex : ∀ r ∈ extract prompt, '@' ∈ r) (hc : P.canon x = some d)
    (hd : d ∈ (resolveIntent P extract tokens roles prompt).dests) :
    ∃ r ∈ extract prompt ++ tokens prompt, P.canon r = some d := by
  simp only [resolveIntent, List.mem_append, List.mem_map, List.mem_filterMap] at hd
  rcases hd with ⟨r, hr, he⟩ | ⟨r, hr, he⟩
  · cases hcr : P.canon r with
    | some e => exact ⟨r, List.mem_append_left _ hr, by simp_all⟩
    | none =>
      rw [hcr] at he
      subst he
      have := P.canon_no_bang x _ hc (List.mem_cons_of_mem _ (hex r hr))
      simp at this
  · exact ⟨r, List.mem_append_right _ hr, he⟩

/-- "User-named" end to end, text branch: a destination `named` without being a token
(an IBAN typed with spaces, say) is 8+ characters and a contiguous substring of the
user's canonical prompt. -/
theorem named_text_typed {extract tokens : Str → List Str} {roles : Str → List Role}
    {prompt d : Str} (h : named d (resolveIntent P extract tokens roles prompt) = true) :
    d ∈ (resolveIntent P extract tokens roles prompt).dests ∨
      (8 ≤ d.length ∧ d <:+: P.normPrompt prompt) := by
  simp only [named, Bool.or_eq_true, List.contains_iff_mem, Bool.and_eq_true,
    decide_eq_true_eq] at h
  rcases h with h | ⟨hl, hs⟩
  · exact .inl h
  · exact .inr ⟨hl, isSubstr_infix hs⟩

/-- `lookalike` never returns an empty contact, so an empty contact cannot switch the check
off (the bug the Rust differential test found in mip.py). -/
theorem lookalike_ne_empty {a c : Str} {cs : List Str} (h : lookalike a cs = some c) :
    c ≠ [] := by
  have := List.find?_some h
  intro hc
  simp [hc] at this

/-! ## Theorem 3: every body atom that is not trusted carries an exact verified quote -/

theorem body_quotes_exact (h : decide P to body pc I S V C ≠ .deny) :
    (quotedAtoms body).length ≤ 1 ∧
    ∀ p ∈ body.atoms, trusted p.src = false → ∃ sid a b src,
      p.quote = some (sid, a, b) ∧ lookup S sid = some src ∧
      pySlice src a b = p.val ∧ p.val <:+: src ∧ MIN_QUOTE ≤ p.val.length := by
  have hb := bodyOk_of_ne_deny h
  simp only [bodyOk, Bool.and_eq_true, decide_eq_true_eq, List.all_eq_true] at hb
  refine ⟨hb.1, fun p hp ht => ?_⟩
  have hq := hb.2 p (List.mem_filter.mpr ⟨hp, by simp [ht]⟩)
  unfold quoteOk at hq
  split at hq
  · simp at hq
  rename_i sid a b hpq
  simp only [Bool.and_eq_true, decide_eq_true_eq, beq_iff_eq] at hq
  obtain ⟨hmin, hs⟩ := hq
  cases hl : lookup S sid with
  | none =>
    simp only [hl, Option.getD_none, pySlice, List.take_nil, List.drop_nil] at hs
    simp [← hs, MIN_QUOTE] at hmin
  | some src =>
    simp only [hl, Option.getD_some] at hs
    exact ⟨sid, a, b, src, hpq, hl, hs, hs ▸ pySlice_infix src a b, hmin⟩

end MIP
