//! Shared fakes: a clock a test moves, panels that record what they were pushed, and a settings store held in memory.
#![allow(dead_code)]

use std::collections::HashMap;
use std::sync::{Arc, Mutex};

use relay_show::alerts::{Env, History, Row, Store};
use relay_show::Clock;
use serde_json::{json, Value};

pub struct Manual(Mutex<f64>);

impl Manual {
    pub fn new() -> Arc<Self> {
        Arc::new(Self(Mutex::new(1000.0)))
    }
    pub fn advance(&self, s: f64) {
        *self.0.lock().unwrap() += s;
    }
}

impl Clock for Manual {
    fn now(&self) -> f64 {
        *self.0.lock().unwrap()
    }
    fn stamp(&self) -> String {
        relay_show::stamp_of(1_700_000_000.0 + self.now())
    }
    fn epoch(&self) -> f64 {
        1_700_000_000.0 + self.now()
    }
}

#[derive(Default)]
pub struct FakeEnv {
    pub pushed: Mutex<Vec<(String, Value)>>,
    pub names: Mutex<HashMap<String, String>>,
    pub places: Mutex<HashMap<i64, Vec<(String, String)>>>,
    /// Panels with a stream open (a push to any other takes nothing).
    pub open: Mutex<Vec<String>>,
}

impl FakeEnv {
    pub fn take(&self, pid: &str) -> Vec<Value> {
        let mut p = self.pushed.lock().unwrap();
        let (mine, rest): (Vec<_>, Vec<_>) = p.drain(..).partition(|(id, _)| id == pid);
        *p = rest;
        mine.into_iter().map(|(_, v)| v).collect()
    }
}

impl Env for FakeEnv {
    fn push_panel(&self, pid: &str, message: &Value) -> usize {
        self.pushed.lock().unwrap().push((pid.to_owned(), message.clone()));
        usize::from(self.open.lock().unwrap().iter().any(|p| p == pid))
    }
    fn panel_name(&self, pid: &str) -> String {
        self.names.lock().unwrap().get(pid).cloned().unwrap_or_default()
    }
    fn in_place(&self, place: i64) -> Option<Vec<(String, String)>> {
        self.places.lock().unwrap().get(&place).cloned()
    }
}

#[derive(Default)]
pub struct MemStore(pub Mutex<Value>);

impl Store for MemStore {
    fn read(&self) -> Value {
        self.0.lock().unwrap().clone()
    }
    fn save(&self, clean: &Value) {
        *self.0.lock().unwrap() = clean.clone();
    }
}

#[derive(Default)]
pub struct MemHistory(pub Mutex<Vec<Row>>);

impl History for MemHistory {
    fn record(&self, row: Row) {
        self.0.lock().unwrap().push(row);
    }
}

pub fn pid(n: u32) -> String {
    format!("{n:032x}")
}

pub fn drain(rx: &mut tokio::sync::mpsc::Receiver<bytes::Bytes>) -> Vec<Value> {
    let mut out = Vec::new();
    while let Ok(frame) = rx.try_recv() {
        let text = String::from_utf8(frame.to_vec()).unwrap();
        out.push(serde_json::from_str(text.strip_prefix("data: ").unwrap().trim_end()).unwrap());
    }
    out
}

pub fn row<'a>(state: &'a Value, name: &str) -> &'a Value {
    state["buttons"].as_array().unwrap().iter().find(|b| b["name"] == name).unwrap()
}

pub fn empty() -> Value {
    json!({})
}
