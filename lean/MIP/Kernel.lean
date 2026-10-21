/-!
# MIP kernel model

A line-for-line model of `atoms`, `canon`, `lookalike`, `resolve_intent` and `decide`
in `mip.py`. Strings are `List Char` (Python `str` indexes by code point, so does this).
Unicode canonicalization, regex extraction and edit distance are parameters; see README.
-/
namespace MIP

abbrev Str := List Char

/-- Python labels are strings. The kernel only ever asks the questions below, so a label
string maps to one of these: `"plan"`, `"user"`, prefix `"verified:"`, prefix `"untrusted"`,
anything else. -/
inductive Label where
  | plan
  | user
  | untrusted (rest : Str)
  | verified (id : Str)
  | other (s : Str)
  deriving DecidableEq, Repr

def Label.isUntrusted : Label → Bool
  | .untrusted _ => true
  | _ => false

def Label.verifiedId? : Label → Option Str
  | .verified i => some i
  | _ => none

def Label.isPlanOrUser : Label → Bool
  | .plan | .user => true
  | _ => false

abbrev Labels := List Label

/-- `untrusted(src)` in mip.py. -/
def untrusted (ls : Labels) : Bool := ls.any Label.isUntrusted

/-- `trusted(src)` in mip.py: non-empty and a subset of `{plan, user}`. Fails closed, so
empty and unknown (`other`) label sets are not trusted (SPEC.md §3.2). -/
def trusted (ls : Labels) : Bool := !ls.isEmpty && ls.all Label.isPlanOrUser

/-- One atom of a body: a string, its labels, and an optional quote span `(sid, start, end)`. -/
structure Atom where
  val : Str
  src : Labels
  quote : Option (Str × Int × Int)

/-- A labeled value `V`. `parts` holds the atoms of a concatenated string. -/
structure Value where
  val : Str
  src : Labels := [.plan]
  quote : Option (Str × Int × Int) := none
  parts : List Atom := []

/-- `atoms(v)`: `v.parts or (v,)`. -/
def Value.atoms (v : Value) : List Atom :=
  if v.parts.isEmpty then [⟨v.val, v.src, v.quote⟩] else v.parts

inductive Verdict where
  | allow
  | confirm
  | deny
  deriving DecidableEq, Repr

inductive Role where
  | sender
  deriving DecidableEq, Repr

/-- `text` is the whole prompt, canonical (NFKC, lowercase, whitespace removed). -/
structure Intent where
  dests : List Str
  roles : List Role
  text : Str := []

/-- Python dict `get`. Dicts have unique keys, so the first match is the match. -/
def lookup (m : List (Str × Str)) (k : Str) : Option Str :=
  (m.find? (·.1 == k)).map (·.2)

/-- Python slice index normalization for step 1. -/
def pyIdx (n : Nat) (i : Int) : Nat :=
  if i < 0 then (i + n).toNat else min i.toNat n

/-- `s[a:b]` with Python semantics, negative and out-of-range indices included. -/
def pySlice (s : Str) (a b : Int) : Str :=
  (s.take (pyIdx s.length b)).drop (pyIdx s.length a)

/-- Python `p in s` for strings. -/
def isSubstr (p s : Str) : Bool :=
  (List.range (s.length + 1)).any fun i => p.isPrefixOf (s.drop i)

/-- The parts of the kernel modelled abstractly. -/
structure Prims where
  /-- `canon`: NFKC, strip, lowercase. Emails: ASCII only, address regex. Other destinations
  (IBAN, URL, channel): whitespace removed, printable ASCII, no `xn--`, length ≥ 2.
  `none` = rejected. -/
  canon : Str → Option Str
  /-- The only property of `canon` the proofs use: an output containing `@` never starts
  with `!` (it passed the email regex `[a-z0-9._%+-]+@...`). Non-email outputs can start with
  `!` since v0.4, so this is the most that holds. `resolve_intent` relies on it. -/
  canon_no_bang : ∀ s d, canon s = some d → '@' ∈ d → d.head? ≠ some '!'
  /-- `unicodedata.normalize("NFKC", t).lower()`, used only by the echo check. -/
  normLower : Str → Str
  /-- `resolve_intent`'s `text`: NFKC, lowercase, all whitespace removed. -/
  normPrompt : Str → Str

/-- One row of the Levenshtein table: `prev` is the row for the previous character of `x`,
`cx` is the next one. Same recurrence as `dist` in mip.py. -/
def levStep (y : Str) (prev : List Nat) (cx : Char) : List Nat :=
  let rec go : Str → List Nat → Nat → List Nat
    | cy :: ys, d :: u :: ps, left =>
      let v := min (min (u + 1) (left + 1)) (d + if cx = cy then 0 else 1)
      v :: go ys (u :: ps) v
    | _, _, _ => []
  match prev with
  | [] => []
  | p0 :: _ => (p0 + 1) :: go y prev (p0 + 1)

/-- Edit distance (insert, delete, substitute), as `dist` in mip.py. -/
def editDist (x y : Str) : Nat :=
  (x.foldl (levStep y) (List.range (y.length + 1))).getLastD 0

/-- `lookalike(a, contacts)`: a non-empty contact `c ≠ a` within edit distance 2.
Empty contacts are skipped: in Python `""` is falsy, so returning it disabled the check. -/
def lookalike (a : Str) (contacts : List Str) : Option Str :=
  contacts.find? fun c => !c.isEmpty && c != a && editDist a c ≤ 2

def MIN_QUOTE : Nat := 12

/-- Step 1 for one untrusted atom: a quote span, long enough, exactly matching its source. -/
def quoteOk (sources : List (Str × Str)) (p : Atom) : Bool :=
  match p.quote with
  | none => false
  | some (sid, a, b) =>
    MIN_QUOTE ≤ p.val.length && pySlice ((lookup sources sid).getD []) a b == p.val

/-- Atoms the kernel treats as untrusted quotes: every atom that is not `trusted`. -/
def quotedAtoms (body : Value) : List Atom :=
  body.atoms.filter fun p => !trusted p.src

/-- `named(dest, intent)`: the user typed it, as a token or (8+ chars) inside the prompt. -/
def named (d : Str) (intent : Intent) : Bool :=
  intent.dests.contains d || (8 ≤ d.length && isSubstr d intent.text)

/-- Step 1 of `decide`: at most one untrusted atom, and it is a verified quote. -/
def bodyOk (sources : List (Str × Str)) (body : Value) : Bool :=
  (quotedAtoms body).length ≤ 1 && (quotedAtoms body).all (quoteOk sources)

/-- Steps 2 to 7 of `decide`: everything about the destination.
`verified` maps message id to canonical sender; a `None` entry in Python behaves as absent.
`Labels` encodes a Python set, so lists here are duplicate-free; `[.verified mid]` is
`len(to.src) == 1`. -/
def destVerdict (P : Prims) (to : Value) (pc : Labels) (intent : Intent)
    (sources verified : List (Str × Str)) (contacts : List Str) : Verdict :=
  match P.canon to.val with
  | none => .deny
  | some dest =>
    if !to.src.isEmpty && to.src.all (·.verifiedId?.isSome) then
      match to.src with
      | [.verified mid] =>
        if untrusted pc then .deny
        else if intent.roles.contains .sender && lookup verified mid == some dest then .allow
        else .deny
      | _ => .deny
    else if !trusted to.src then .deny
    else if !named dest intent then .deny
    else if untrusted pc then .confirm
    else if sources.any (fun st => isSubstr dest (P.normLower st.2)) then .confirm
    else if (lookalike dest contacts).isSome then .confirm
    else .allow

/-- `decide(to, body, pc, intent, sources, verified, contacts)` from mip.py. -/
def decide (P : Prims) (to body : Value) (pc : Labels) (intent : Intent)
    (sources verified : List (Str × Str)) (contacts : List Str) : Verdict :=
  if bodyOk sources body then destVerdict P to pc intent sources verified contacts else .deny

/-- `resolve_intent`. The two regexes are parameters: `extract` finds email-like raw
addresses, `tokens` finds the other tokens (already split, `.:` stripped, `@`-free).
A raw email `canon` rejects becomes `"!" ++ raw`, which no canonical address equals;
a rejected token is dropped. -/
def resolveIntent (P : Prims) (extract tokens : Str → List Str) (roles : Str → List Role)
    (prompt : Str) : Intent :=
  { dests := (extract prompt).map (fun r => (P.canon r).getD ('!' :: r)) ++
      (tokens prompt).filterMap P.canon
    roles := roles prompt
    text := P.normPrompt prompt }

end MIP
