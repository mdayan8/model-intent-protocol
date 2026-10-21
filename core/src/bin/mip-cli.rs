//! Differential-test driver. One JSON request per stdin line, one JSON reply per stdout line.
//!   {"op":"decide","to":V,"body":V,"pc":[..],"intent":{"dests":[..],"roles":[..],"text":".."},
//!    "sources":{..},"verified":{..},"contacts":[..]}      -> {"verdict","rule"}
//!   {"op":"canon","s":".."}                               -> {"canon": str|null}
//!   {"op":"canonv","v":any}                               -> {"canon": canon(v)}, None unless v is a string
//!   {"op":"intent","prompt":".."}                         -> {"dests","roles","text","readback"}
//!   {"op":"wire","msg":{..},"agent":".."}                 -> {"enc": encode(decode(msg))} | {"err"}
//! V = {"v": any, "l": [..], "q": [] | [sid, a, b], "parts": [V..]}
use mip_core::*;
use serde_json::{json, Value};
use std::collections::{BTreeMap, BTreeSet};
use std::io::{BufRead, BufWriter, Write};

fn strs(v: &Value) -> BTreeSet<String> {
    v.as_array().into_iter().flatten().filter_map(|s| s.as_str().map(String::from)).collect()
}

fn lv(v: &Value) -> V {
    let q = v["q"].as_array().filter(|q| q.len() == 3).map(|q| Quote {
        sid: q[0].as_str().unwrap_or_default().into(),
        start: q[1].as_i64().unwrap_or(0),
        end: q[2].as_i64().unwrap_or(0),
    });
    let parts = v["parts"].as_array().into_iter().flatten().map(lv).collect();
    V { val: v["v"].clone(), src: strs(&v["l"]), quote: q, parts }
}

fn handle(r: &Value) -> Value {
    match r["op"].as_str() {
        Some("decide") => {
            let text = r["intent"]["text"].as_str().unwrap_or_default().into();
            let intent = Intent { dests: strs(&r["intent"]["dests"]), roles: strs(&r["intent"]["roles"]), text };
            let obj = |k: &str| r[k].as_object().cloned().unwrap_or_default();
            let sources: BTreeMap<String, String> =
                obj("sources").into_iter().map(|(k, v)| (k, v.as_str().unwrap_or_default().into())).collect();
            let verified: BTreeMap<String, Option<String>> =
                obj("verified").into_iter().map(|(k, v)| (k, v.as_str().map(String::from))).collect();
            let d = decide(&lv(&r["to"]), &lv(&r["body"]), &strs(&r["pc"]), &intent, &sources, &verified, &strs(&r["contacts"]));
            json!({"verdict": d.verdict.as_str(), "rule": d.rule.code()})
        }
        Some("canon") => json!({"canon": canon(r["s"].as_str().unwrap_or_default())}),
        Some("canonv") => json!({"canon": r["v"].as_str().and_then(canon)}),
        Some("intent") => {
            let (i, readback) = resolve_intent(r["prompt"].as_str().unwrap_or_default());
            json!({"dests": i.dests, "roles": i.roles, "text": i.text, "readback": readback})
        }
        Some("wire") => match decode(&r["msg"], r["agent"].as_str().unwrap_or_default()) {
            Ok(v) => json!({"enc": encode(&v)}),
            Err(e) => json!({"err": format!("{e:?}")}),
        },
        _ => json!({"err": "bad op"}),
    }
}

fn main() {
    let mut out = BufWriter::new(std::io::stdout().lock());
    for line in std::io::stdin().lock().lines() {
        let r: Value = serde_json::from_str(&line.expect("stdin")).expect("json");
        writeln!(out, "{}", handle(&r)).expect("stdout");
    }
}
