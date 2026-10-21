//! MIP kernel, ported from mip.py (the executable spec). Pure and total: no I/O,
//! no clock, no randomness. Everything security-relevant is decided in `decide`.
//!
//! Text semantics follow Python exactly: lengths and slices count code points,
//! `strip()`/`\s`/`\w` use Python's Unicode tables (src/tables.rs, generated).
use serde_json::{json, Value};
use std::borrow::Cow;
use std::collections::{BTreeMap, BTreeSet};
use unicode_normalization::UnicodeNormalization;

mod tables;

/// Shorter quotes are too easy to splice into new sentences.
pub const MIN_QUOTE: usize = 12;

pub type Labels = BTreeSet<String>;

#[derive(Clone, Debug, PartialEq)]
pub struct Quote {
    pub sid: String,
    pub start: i64,
    pub end: i64,
}

/// A labeled value. `src`: plan | user | untrusted:<id> | verified:<id>.
#[derive(Clone, Debug, PartialEq)]
pub struct V {
    pub val: Value,
    pub src: Labels,
    pub quote: Option<Quote>,
    /// Atoms of a concatenated string. Not recursive, as in mip.py.
    pub parts: Vec<V>,
}

impl V {
    /// Same default as mip.py: a bare value is planner text.
    pub fn new(val: impl Into<Value>) -> V {
        V::labeled(val, &["plan"])
    }

    pub fn labeled(val: impl Into<Value>, src: &[&str]) -> V {
        V { val: val.into(), src: src.iter().map(|s| s.to_string()).collect(), quote: None, parts: vec![] }
    }

    pub fn atoms(&self) -> &[V] {
        if self.parts.is_empty() { std::slice::from_ref(self) } else { &self.parts }
    }
}

pub fn untrusted(src: &Labels) -> bool {
    src.iter().any(|s| s.starts_with("untrusted"))
}

/// Fail closed: empty or unknown labels are NOT trusted (SPEC 3.2).
pub fn trusted(src: &Labels) -> bool {
    !src.is_empty() && src.iter().all(|s| s == "plan" || s == "user")
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Verdict {
    Allow,
    Confirm,
    Deny,
}

impl Verdict {
    pub fn as_str(self) -> &'static str {
        match self {
            Verdict::Allow => "allow",
            Verdict::Confirm => "confirm",
            Verdict::Deny => "deny",
        }
    }
}

/// Which line of decide() fired. Lets the differential test check more than the verdict.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Rule {
    SplicedQuotes,
    UnverifiedQuote,
    FakeQuote,
    NonCanonicalDest,
    ReplyOnUntrustedBranch,
    VerifiedSender,
    RoleMismatch,
    MultiVerified,
    UntrustedDest,
    NotNamed,
    UntrustedBranch,
    Echoed,
    Lookalike,
    Named,
}

impl Rule {
    pub fn code(self) -> &'static str {
        use Rule::*;
        match self {
            SplicedQuotes => "B1",
            UnverifiedQuote => "B2",
            FakeQuote => "B3",
            NonCanonicalDest => "D1",
            MultiVerified => "R0",
            ReplyOnUntrustedBranch => "R1",
            VerifiedSender => "R2",
            RoleMismatch => "R3",
            UntrustedDest => "U1",
            NotNamed => "N1",
            UntrustedBranch => "C1",
            Echoed => "C2",
            Lookalike => "C3",
            Named => "A1",
        }
    }

    pub fn verdict(self) -> Verdict {
        use Rule::*;
        match self {
            VerifiedSender | Named => Verdict::Allow,
            UntrustedBranch | Echoed | Lookalike => Verdict::Confirm,
            _ => Verdict::Deny,
        }
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct Decision {
    pub verdict: Verdict,
    pub rule: Rule,
    pub reason: String,
}

fn out(rule: Rule, reason: String) -> Decision {
    Decision { verdict: rule.verdict(), rule, reason }
}

#[derive(Clone, Debug, Default, PartialEq)]
pub struct Intent {
    pub dests: BTreeSet<String>,
    pub roles: BTreeSet<String>,
    /// The whole prompt, NFKC + lowercase, whitespace removed.
    pub text: String,
}

// ============================================================ python text helpers
fn in_table(t: &[(u32, u32)], c: char) -> bool {
    let c = c as u32;
    t.binary_search_by(|&(a, b)| {
        if b < c { std::cmp::Ordering::Less } else if a > c { std::cmp::Ordering::Greater } else { std::cmp::Ordering::Equal }
    })
    .is_ok()
}

fn py_space(c: char) -> bool {
    in_table(tables::PY_SPACE, c)
}

fn py_word(c: char) -> bool {
    in_table(tables::PY_WORD, c)
}


/// Python `s[a:b]` on code points.
fn py_slice(s: &str, a: i64, b: i64) -> &str {
    let n = s.chars().count() as i64;
    let fix = |i: i64| if i < 0 { (i + n).max(0) } else { i.min(n) };
    let (a, b) = (fix(a), fix(b));
    if b <= a {
        return "";
    }
    if s.is_ascii() {
        return &s[a as usize..b as usize];
    }
    let byte = |i: i64| s.char_indices().nth(i as usize).map_or(s.len(), |(o, _)| o);
    &s[byte(a)..byte(b)]
}

fn nfkc(s: &str) -> Cow<'_, str> {
    // NFKC is the identity on ASCII.
    if s.is_ascii() { Cow::Borrowed(s) } else { Cow::Owned(s.nfkc().collect()) }
}

// ============================================================ canonical addresses
/// Canonical destination, or None. NFKC + strip + lowercase, then:
/// emails (contain "@"): ASCII, no punycode, simple address shape;
/// anything else (IBAN, URL, channel, user): whitespace removed, 2+ chars,
/// printable ASCII, no punycode.
pub fn canon(addr: &str) -> Option<String> {
    let n = nfkc(addr);
    let t = n.trim_matches(py_space);
    let mut a = if t.is_ascii() { t.to_ascii_lowercase() } else { t.to_lowercase() };
    if a.contains('@') {
        return (a.is_ascii() && !a.contains("xn--") && is_addr(&a)).then_some(a);
    }
    a.retain(|c| !py_space(c));
    let ok = a.chars().count() >= 2 && a.bytes().all(|c| (0x20..0x7f).contains(&c)) && !a.contains("xn--");
    ok.then_some(a)
}

/// `[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}` as a full match. The TLD can hold no
/// dot, so the split is always at the last dot.
fn is_addr(a: &str) -> bool {
    let Some((local, domain)) = a.split_once('@') else { return false };
    let Some(k) = domain.rfind('.') else { return false };
    let (host, tld) = (&domain[..k], &domain[k + 1..]);
    !local.is_empty()
        && local.bytes().all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || b"._%+-".contains(&c))
        && !host.is_empty()
        && host.bytes().all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == b'.' || c == b'-')
        && tld.len() >= 2
        && tld.bytes().all(|c| c.is_ascii_lowercase())
}

/// Levenshtein distance <= 2, on code points.
fn near(x: &str, y: &str) -> bool {
    let (x, y): (Vec<char>, Vec<char>) = (x.chars().collect(), y.chars().collect());
    if x.len().abs_diff(y.len()) > 2 {
        return false; // distance is at least the length difference
    }
    let mut row: Vec<usize> = (0..=y.len()).collect();
    for (i, cx) in x.iter().enumerate() {
        let mut prev = row[0];
        row[0] = i + 1;
        for (j, cy) in y.iter().enumerate() {
            let cur = (row[j + 1] + 1).min(row[j] + 1).min(prev + (cx != cy) as usize);
            prev = row[j + 1];
            row[j + 1] = cur;
        }
    }
    row[y.len()] <= 2
}

/// Empty contacts are skipped: "" is within distance 2 of every short destination.
pub fn lookalike<'a>(a: &str, contacts: &'a BTreeSet<String>) -> Option<&'a String> {
    contacts.iter().find(|c| !c.is_empty() && c.as_str() != a && near(a, c))
}

/// `dest in NFKC(text).lower()`.
fn echoed(dest: &str, text: &str) -> bool {
    if text.is_ascii() {
        let (t, d) = (text.as_bytes(), dest.as_bytes());
        return t.windows(d.len()).any(|w| w.eq_ignore_ascii_case(d)); // dest is lowercase
    }
    nfkc(text).to_lowercase().contains(dest)
}

// ============================================================ intent
/// Python `re.findall(r"\S+@\S+\.\w+", prompt)`. A match never crosses
/// whitespace, so each token is scanned alone. From a start i, greedy
/// backtracking picks the last valid "." (followed by \w) and then needs any
/// "@" at least two chars before it; a later start can only do worse.
fn raw_addresses(p: &str) -> Vec<&str> {
    let cs: Vec<(usize, char)> = p.char_indices().collect();
    let n = cs.len();
    let byte = |i: usize| if i < n { cs[i].0 } else { p.len() };
    let (mut out, mut i) = (vec![], 0);
    while i < n {
        if py_space(cs[i].1) {
            i += 1;
            continue;
        }
        let mut j = i;
        while j < n && !py_space(cs[j].1) {
            j += 1;
        }
        let dot = (i + 2..j.saturating_sub(1)).rev().find(|&d| cs[d].1 == '.' && py_word(cs[d + 1].1));
        match dot.filter(|&d| (i + 1..d - 1).any(|k| cs[k].1 == '@')) {
            Some(d) => {
                let mut e = d + 1;
                while e < j && py_word(cs[e].1) {
                    e += 1;
                }
                out.push(&p[byte(i)..byte(e)]);
                i = e;
            }
            None => i = j,
        }
    }
    out
}

/// Python `re.I` equivalents of the role pattern's letters (measured, not guessed).
fn ci_eq(pat: char, c: char) -> bool {
    c == pat
        || c == pat.to_ascii_uppercase()
        || (pat == 'i' && (c == '\u{130}' || c == '\u{131}'))
        || (pat == 's' && c == '\u{17f}')
}

/// `re.search(r"\b(reply|sender|whoever (emailed|sent))\b", prompt, re.I)`.
fn says_sender(p: &str) -> bool {
    const ALTS: [&str; 4] = ["reply", "sender", "whoever emailed", "whoever sent"];
    let cs: Vec<char> = p.chars().collect();
    (0..cs.len()).any(|i| {
        (i == 0 || !py_word(cs[i - 1]))
            && ALTS.iter().any(|alt| {
                let e = i + alt.len();
                e <= cs.len() && alt.chars().zip(&cs[i..e]).all(|(x, &y)| ci_eq(x, y)) && (e == cs.len() || !py_word(cs[e]))
            })
    })
}

/// Returns the intent AND a readback line the UI must show the user.
/// dests: emails (rejected ones marked "!") plus every canonical token, i.e.
/// `re.findall(r"[^\s,;!?()\"'<>]+")` runs, right-stripped of ".:", without "@".
pub fn resolve_intent(prompt: &str) -> (Intent, String) {
    let mut dests: BTreeSet<String> = raw_addresses(prompt).into_iter().map(|r| canon(r).unwrap_or_else(|| format!("!{r}"))).collect();
    let sep = |c: char| py_space(c) || ",;!?()\"'<>".contains(c);
    for t in prompt.split(sep).filter(|t| !t.is_empty()) {
        let t = t.trim_end_matches(['.', ':']);
        if !t.contains('@') {
            dests.extend(canon(t));
        }
    }
    let roles: BTreeSet<String> = if says_sender(prompt) { ["sender".to_string()].into() } else { BTreeSet::new() };
    let mut text = nfkc(prompt).to_lowercase();
    text.retain(|c| !py_space(c));
    let shown: Vec<&str> = dests.iter().filter(|d| d.contains('@') || d.starts_with('!')).chain(&roles).map(|s| s.as_str()).collect();
    let readback = format!("Data may go to: {}", if shown.is_empty() { "nowhere".into() } else { shown.join(", ") });
    (Intent { dests, roles, text }, readback)
}

/// The user typed this destination: as a token, or (8+ chars) inside the prompt.
pub fn named(dest: &str, intent: &Intent) -> bool {
    intent.dests.contains(dest) || (dest.chars().count() >= 8 && intent.text.contains(dest))
}

// ============================================================ THE KERNEL
pub fn decide(
    to: &V,
    body: &V,
    pc: &Labels,
    intent: &Intent,
    sources: &BTreeMap<String, String>,
    verified: &BTreeMap<String, Option<String>>,
    contacts: &BTreeSet<String>,
) -> Decision {
    use Rule::*;
    // 1. Body: planner text, or exactly one verified, contiguous, long-enough quote.
    let mut quoted = body.atoms().iter().filter(|p| !trusted(&p.src));
    if let Some(p) = quoted.next() {
        let extra = quoted.count();
        if extra > 0 {
            return out(SplicedQuotes, format!("body splices {} untrusted quotes; only one is allowed", extra + 1));
        }
        let Some(q) = &p.quote else {
            return out(UnverifiedQuote, "untrusted text in body is not a verified quote".into());
        };
        let real = match &p.val {
            Value::String(s) => {
                s.chars().count() >= MIN_QUOTE && py_slice(sources.get(&q.sid).map_or("", |t| t), q.start, q.end) == s
            }
            _ => false,
        };
        if !real {
            return out(FakeQuote, format!("reader output is not a real quote from {}: {}", q.sid, p.val));
        }
    }
    // 2. Destination must be a clean canonical address.
    // Fail closed: only strings are destinations (None, numbers, lists are not).
    let Some(dest) = to.val.as_str().and_then(canon) else {
        return out(NonCanonicalDest, format!("destination {} is not a canonical ASCII address", to.val));
    };
    // 3. Role binding to authenticated metadata, never to body text.
    if !to.src.is_empty() && to.src.iter().all(|s| s.starts_with("verified:")) {
        if to.src.len() != 1 {
            return out(MultiVerified, "destination carries more than one verified label".into());
        }
        let mid = &to.src.first().expect("one label")["verified:".len()..];
        if untrusted(pc) {
            return out(ReplyOnUntrustedBranch, "reply decided by untrusted data".into());
        }
        if intent.roles.contains("sender") && verified.get(mid).is_some_and(|v| v.as_deref() == Some(&*dest)) {
            return out(VerifiedSender, format!("reply to DMARC-verified sender of {mid}"));
        }
        return out(RoleMismatch, "verified metadata used without a matching role in the request".into());
    }
    if !trusted(&to.src) {
        return out(UntrustedDest, format!("destination came from untrusted or unlabeled data: {}", to.val));
    }
    if !named(&dest, intent) {
        return out(NotNamed, format!("destination {dest:?} was never named by the user"));
    }
    // 4. User-named, but check how the decision and the address came to be.
    if untrusted(pc) {
        return out(UntrustedBranch, "this send only happens because of untrusted data".into());
    }
    if let Some((sid, _)) = sources.iter().find(|(_, text)| echoed(&dest, text)) {
        return out(Echoed, format!("{dest:?} appeared in untrusted {sid} before you typed it"));
    }
    if let Some(c) = lookalike(&dest, contacts) {
        return out(Lookalike, format!("{dest:?} looks like your contact {c:?}"));
    }
    out(Named, "destination named by the user".into())
}

// ============================================================ wire labels (agent hops)
pub fn encode(v: &V) -> Value {
    let q = v.quote.as_ref().map_or(json!([]), |q| json!([q.sid, q.start, q.end]));
    json!({"v": v.val, "l": v.src, "q": q})
}

#[derive(Debug, PartialEq)]
pub enum WireError {
    MissingValue,
    BadLabels,
}

/// Labels only accumulate across a hop. A remote agent can never vouch for itself.
pub fn decode(msg: &Value, agent: &str) -> Result<V, WireError> {
    let val = msg.get("v").ok_or(WireError::MissingValue)?.clone();
    let mut src = Labels::new();
    match msg.get("l") {
        None => {}
        Some(Value::Array(ls)) => {
            for l in ls {
                src.insert(l.as_str().ok_or(WireError::BadLabels)?.to_string());
            }
        }
        Some(_) => return Err(WireError::BadLabels),
    }
    src.insert(format!("untrusted:agent:{agent}"));
    Ok(V { val, src, quote: None, parts: vec![] })
}

#[cfg(test)]
mod tests;
