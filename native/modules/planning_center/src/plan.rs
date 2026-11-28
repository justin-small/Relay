//! Reading Planning Center's JSON:API answers and working out which plan is today's (`planningcenter.py`'s second half). Pure functions on
//! JSON values, shaped as Python's dicts are, so what the admin page and the panels read is byte for byte what they read before.

use jiff::civil::Date;
use jiff::tz::TimeZone;
use jiff::Timestamp;
use serde_json::{json, Map, Value};

pub const SERVICE_TAIL_S: f64 = 15.0 * 60.0;
pub const SERVICE_DEFAULT_S: f64 = 90.0 * 60.0;
pub const RUNNING_STALE_S: f64 = 4.0 * 3600.0;

/// A string field, `""` when it is missing or not text.
pub fn s(v: &Value, key: &str) -> String {
    v.get(key).and_then(Value::as_str).unwrap_or("").to_owned()
}

/// A number field as seconds.
pub fn f(v: &Value, key: &str) -> Option<f64> {
    v.get(key).and_then(Value::as_f64)
}

/// A whole number field, `0` when missing.
pub fn i(v: &Value, key: &str) -> i64 {
    v.get(key).and_then(|x| x.as_i64().or_else(|| x.as_f64().map(|n| n as i64))).unwrap_or(0)
}

/// Python's `str(x or "")` for an attribute that may be text, a number or missing.
pub fn text(v: &Value) -> String {
    match v {
        Value::String(t) => t.clone(),
        Value::Number(n) => n.to_string(),
        Value::Bool(true) => "True".to_owned(),
        _ => String::new(),
    }
}

/// An ISO time from Planning Center as epoch seconds, or `None`. A time with no offset is UTC.
pub fn parse_time(raw: &Value) -> Option<f64> {
    let t = raw.as_str().filter(|t| !t.is_empty())?;
    if let Ok(ts) = t.parse::<Timestamp>() {
        return Some(ts.as_nanosecond() as f64 / 1e9);
    }
    let civil: jiff::civil::DateTime = t.parse().ok()?;
    let ts = civil.to_zoned(TimeZone::UTC).ok()?.timestamp();
    Some(ts.as_nanosecond() as f64 / 1e9)
}

/// The zone a plan day is read in.
#[derive(Clone, Debug)]
pub struct Zone {
    pub tz: TimeZone,
    pub name: String,
}

const RAILS_ZONES: &[(&str, &str)] = &[
    ("Eastern Time (US & Canada)", "America/New_York"),
    ("Central Time (US & Canada)", "America/Chicago"),
    ("Mountain Time (US & Canada)", "America/Denver"),
    ("Pacific Time (US & Canada)", "America/Los_Angeles"),
    ("Arizona", "America/Phoenix"),
    ("Alaska", "America/Juneau"),
    ("Hawaii", "Pacific/Honolulu"),
    ("Indiana (East)", "America/Indiana/Indianapolis"),
    ("Atlantic Time (Canada)", "America/Halifax"),
    ("Puerto Rico", "America/Puerto_Rico"),
    ("London", "Europe/London"),
    ("UTC", "UTC"),
];

impl Zone {
    /// An IANA name Relay knows, or `None`.
    pub fn named(name: &str) -> Option<Self> {
        TimeZone::get(name).ok().map(|tz| Self { tz, name: name.to_owned() })
    }

    /// The zone Planning Center named (by the Rails spelling or an IANA id), or `None` if Relay cannot place it.
    pub fn for_name(name: &str) -> Option<Self> {
        let rails = RAILS_ZONES.iter().find(|(k, _)| *k == name).map(|(_, v)| *v);
        rails.into_iter().chain(Some(name)).filter(|c| !c.is_empty()).find_map(Self::named)
    }

    pub fn utc() -> Self {
        Self { tz: TimeZone::UTC, name: "UTC".to_owned() }
    }

    /// The civil date at epoch second `ts` in this zone.
    pub fn day_of(&self, ts: f64) -> Date {
        Timestamp::from_nanosecond((ts * 1e9) as i128).unwrap_or(Timestamp::UNIX_EPOCH).to_zoned(self.tz.clone()).date()
    }

    /// `HHMM` of epoch second `ts` in this zone.
    pub fn hhmm(&self, ts: f64) -> String {
        let z = Timestamp::from_nanosecond((ts * 1e9) as i128).unwrap_or(Timestamp::UNIX_EPOCH).to_zoned(self.tz.clone());
        format!("{:02}{:02}", z.hour(), z.minute())
    }

    /// `h:mm AM` of epoch second `ts` in this zone.
    pub fn clock(&self, ts: f64) -> String {
        let z = Timestamp::from_nanosecond((ts * 1e9) as i128).unwrap_or(Timestamp::UNIX_EPOCH).to_zoned(self.tz.clone());
        format!("{}:{:02} {}", if z.hour() % 12 == 0 { 12 } else { z.hour() % 12 }, z.minute(), if z.hour() < 12 { "AM" } else { "PM" })
    }

    /// Epoch second of local midnight at the start of `day`.
    pub fn midnight(&self, day: Date) -> f64 {
        day.to_zoned(self.tz.clone()).map_or(0.0, |z| z.timestamp().as_nanosecond() as f64 / 1e9)
    }
}

/// `YYYY-MM-DD`.
pub fn iso_day(d: Date) -> String {
    format!("{:04}-{:02}-{:02}", d.year(), d.month(), d.day())
}

fn rows<'a>(doc: &'a Value, key: &str) -> &'a [Value] {
    doc.get(key).and_then(Value::as_array).map_or(&[][..], Vec::as_slice)
}

/// The `data` rows of a document.
pub fn data(doc: &Value) -> &[Value] {
    rows(doc, "data")
}

/// A document's `included` records by `(type, id)`.
pub fn included(doc: &Value) -> Vec<((String, String), &Value)> {
    rows(doc, "included").iter().map(|r| ((s(r, "type"), id_of(r)), r)).collect()
}

/// `str(row["id"])`.
pub fn id_of(row: &Value) -> String {
    row.get("id").map(text).unwrap_or_default()
}

pub fn find<'a>(inc: &[((String, String), &'a Value)], kind: &str, id: Option<&str>) -> Option<&'a Value> {
    let id = id?;
    inc.iter().find(|((k, i), _)| k == kind && i == id).map(|(_, v)| *v)
}

/// `row.attributes`, or an empty object.
pub fn attrs(row: Option<&Value>) -> Value {
    row.and_then(|r| r.get("attributes")).filter(|a| a.is_object()).cloned().unwrap_or_else(|| json!({}))
}

/// The id of the one record a relationship names.
pub fn rel_id(row: &Value, name: &str) -> Option<String> {
    let data = row.get("relationships")?.get(name)?.get("data")?;
    data.as_object()?.get("id").filter(|v| !v.is_null()).map(text)
}

/// The ids of the records a relationship names.
pub fn rel_ids(row: &Value, name: &str) -> Vec<String> {
    row.get("relationships")
        .and_then(|r| r.get(name))
        .and_then(|r| r.get("data"))
        .and_then(Value::as_array)
        .map(|a| a.iter().filter_map(|d| d.get("id").filter(|v| !v.is_null()).map(text)).collect())
        .unwrap_or_default()
}

/// Whether a relationship names its records at all (`"data"` is present), even an empty list.
pub fn has_rel_data(row: &Value, name: &str) -> bool {
    row.get("relationships").and_then(|r| r.get(name)).is_some_and(|r| r.is_object() && r.get("data").is_some())
}

pub fn item_time(row: Option<&Value>) -> Value {
    let Some(row) = row.filter(|r| !r.is_null()) else { return Value::Null };
    let a = attrs(Some(row));
    json!({"id": id_of(row), "item": rel_id(row, "item"), "plan_time": rel_id(row, "plan_time"),
           "live_start_at": parse_time(&a["live_start_at"]), "live_end_at": parse_time(&a["live_end_at"]),
           "exclude": a["exclude"].as_bool().unwrap_or(false) || (a["exclude"].is_number() && a["exclude"].as_i64() != Some(0))})
}

/// A Live document (from `GET .../live` or a LIVE action) as Relay keeps it.
pub fn live(doc: &Value) -> Value {
    let row = doc.get("data").filter(|d| d.is_object()).cloned().unwrap_or_else(|| json!({}));
    let inc = included(doc);
    let controller = find(&inc, "Person", rel_id(&row, "controller").as_deref()).map(|p| {
        let a = attrs(Some(p));
        let name = ["full_name", "name"].iter().map(|k| text(&a[*k])).find(|t| !t.is_empty()).unwrap_or_else(|| {
            ["first_name", "last_name"].iter().map(|k| text(&a[*k])).filter(|t| !t.is_empty()).collect::<Vec<_>>().join(" ")
        });
        let name = name.trim();
        json!({"id": id_of(p), "name": if name.is_empty() { "Someone" } else { name }})
    });
    json!({
        "id": row.get("id").filter(|v| !v.is_null()).map(text),
        "controller": controller,
        "current": item_time(find(&inc, "ItemTime", rel_id(&row, "current_item_time").as_deref())),
        "next": item_time(find(&inc, "ItemTime", rel_id(&row, "next_item_time").as_deref())),
    })
}

/// `PlanTime` spells its fields `live_starts_at` and `live_ends_at` (with the "s").
pub fn plan_of(row: &Value, times: &[&Value]) -> Value {
    let a = attrs(Some(row));
    let mut out: Vec<Value> = times
        .iter()
        .map(|t| {
            let ta = attrs(Some(t));
            json!({"id": id_of(t), "type": text(&ta["time_type"]), "name": text(&ta["name"]),
                   "starts_at": parse_time(&ta["starts_at"]), "ends_at": parse_time(&ta["ends_at"]),
                   "live_starts_at": parse_time(&ta["live_starts_at"]), "live_ends_at": parse_time(&ta["live_ends_at"])})
        })
        .collect();
    out.sort_by(|x, y| f(x, "starts_at").unwrap_or(0.0).total_cmp(&f(y, "starts_at").unwrap_or(0.0)));
    json!({"id": id_of(row), "title": text(&a["title"]), "series_title": text(&a["series_title"]), "dates": text(&a["dates"]),
           "sort_date": parse_time(&a["sort_date"]), "updated_at": text(&a["updated_at"]), "times": out})
}

// -------------------------------------------------------------------------------------------------- which plan is today's
/// The plan's service times that have a start.
pub fn service_times(plan: &Value) -> Vec<Value> {
    plan["times"].as_array().into_iter().flatten().filter(|t| t["type"] == "service" && f(t, "starts_at").is_some()).cloned().collect()
}

/// A service time's `(start, end)`: a time with no planned end is taken to last [`SERVICE_DEFAULT_S`].
pub fn window(t: &Value) -> (f64, f64) {
    let start = f(t, "starts_at").unwrap_or(0.0);
    let end = f(t, "ends_at").filter(|e| *e > start).unwrap_or(start + SERVICE_DEFAULT_S);
    (start, end)
}

/// Today's plan: one with a `service` time today in the organisation's zone. With several today (a morning and an evening plan), the
/// one being run wins, then the first not yet over, then the last.
pub fn pick_plan(plans: &[Value], now: f64, zone: &Zone) -> Option<Value> {
    let today = zone.day_of(now);
    let mut todays: Vec<(f64, &Value, Vec<Value>)> = Vec::new();
    for p in plans {
        let mine: Vec<Value> = service_times(p).into_iter().filter(|t| zone.day_of(f(t, "starts_at").unwrap_or(0.0)) == today).collect();
        if let Some(first) = mine.first() {
            todays.push((f(first, "starts_at").unwrap_or(0.0), p, mine));
        }
    }
    if todays.is_empty() {
        return None;
    }
    todays.sort_by(|a, b| a.0.total_cmp(&b.0));
    for (_, p, mine) in &todays {
        if mine.iter().any(|t| f(t, "live_starts_at").is_some_and(|st| st != 0.0) && f(t, "live_ends_at").is_none() && now - f(t, "live_starts_at").unwrap_or(0.0) < RUNNING_STALE_S) {
            return Some((*p).clone());
        }
    }
    for (_, p, mine) in &todays {
        if mine.iter().any(|t| window(t).1 > now) {
            return Some((*p).clone());
        }
    }
    todays.last().map(|(_, p, _)| (*p).clone())
}

/// True from an hour before a service time until a little after its planned end.
pub fn near_service(plan: Option<&Value>, now: f64) -> bool {
    let lead = 3600.0;
    plan.is_some_and(|p| service_times(p).iter().map(window).any(|(start, end)| start - lead <= now && now <= end + SERVICE_TAIL_S))
}

/// True while LIVE has an item open (started, not ended, not left open for hours).
pub fn running(live: Option<&Value>, now: f64) -> bool {
    let Some(cur) = live.map(|l| &l["current"]).filter(|c| c.is_object()) else { return false };
    f(cur, "live_start_at").is_some_and(|st| st != 0.0) && f(cur, "live_end_at").is_none() && now - f(cur, "live_start_at").unwrap_or(0.0) < RUNNING_STALE_S
}

fn round1(x: f64) -> f64 {
    (x * 10.0).round() / 10.0
}

/// Planning Center gives no time remaining, so it is worked out here.
///
/// `item_left_s` is the current item's start + its length - now (negative = over). `service_over_s` is how far behind plan the service is
/// (negative = ahead): time since the service's `live_starts_at`, less the lengths of the items done, less what the current item has used
/// of its own length. Both are `null` when nothing is running. Lengths are seconds.
pub fn timers(plan: Option<&Value>, items: &[Value], live: Option<&Value>, now: f64, service_start: Option<f64>) -> Value {
    let mut out = Map::new();
    for k in ["current_item", "next_item", "item_started_at", "item_length_s", "item_left_s", "service_started_at", "service_over_s"] {
        out.insert(k.to_owned(), Value::Null);
    }
    let cur = live.map(|l| &l["current"]).filter(|c| c.is_object());
    let nxt = live.map(|l| &l["next"]).filter(|c| c.is_object());
    out["next_item"] = nxt.map_or(Value::Null, |n| n["item"].clone());
    let Some(cur) = cur else { return Value::Object(out) };
    out["current_item"] = cur["item"].clone();
    if !running(live, now) {
        return Value::Object(out);
    }
    let item = items.iter().find(|i| i["id"] == cur["item"]);
    let started = f(cur, "live_start_at").unwrap_or(0.0);
    out["item_started_at"] = json!(started);
    let Some(item) = item else { return Value::Object(out) };
    let length = f(item, "length").unwrap_or(0.0);
    out["item_length_s"] = item["length"].clone();
    out["item_left_s"] = json!(round1(started + length - now));
    let pt = plan.and_then(|p| p["times"].as_array()).and_then(|ts| ts.iter().find(|t| t["id"] == cur["plan_time"]));
    let start = pt.and_then(|t| f(t, "live_starts_at")).filter(|v| *v != 0.0).or(service_start);
    out["service_started_at"] = start.map_or(Value::Null, |v| json!(v));
    let Some(start) = start else { return Value::Object(out) };
    if item["position"] != "during" {
        return Value::Object(out);
    }
    let seq = i(item, "sequence");
    let before: f64 = items.iter().filter(|x| x["position"] == "during" && i(x, "sequence") < seq).map(|x| f(x, "length").unwrap_or(0.0)).sum();
    let used = (now - started).min(length);
    out["service_over_s"] = json!(round1((now - start) - before - used));
    Value::Object(out)
}

/// Whether `text` is 1 to 20 digits (a Planning Center id).
pub fn is_id(text: &str) -> bool {
    (1..=20).contains(&text.len()) && text.bytes().all(|b| b.is_ascii_digit())
}
