//! What changes reach the players, and what each screen reports it is playing (`live.py`, #205).
//!
//! `version` goes up on every signage change. Each player's event stream checks it once a second and, when it has moved, works out that
//! screen's state again and sends it only if it differs. Players report the item they are showing, which the admin page lists with when
//! each screen was last heard from. Both live in memory: a restart loses nothing that matters, since players reconnect and report again.

use std::collections::HashMap;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Mutex;
use std::time::{SystemTime, UNIX_EPOCH};

pub const NOW_MAX: usize = 500;

#[derive(Clone, Debug, PartialEq)]
pub struct Now {
    pub item_id: Option<i64>,
    pub label: String,
    pub since: f64,
    pub seen: f64,
}

#[derive(Default)]
pub struct Live {
    version: AtomicU64,
    now: Mutex<HashMap<String, Now>>,
    /// Screen slug -> the "once" push it has finished playing (#207).
    done: Mutex<HashMap<String, i64>>,
}

fn epoch() -> f64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0)
}

impl Live {
    pub fn version(&self) -> u64 {
        self.version.load(Ordering::SeqCst)
    }

    pub fn bump(&self) {
        self.version.fetch_add(1, Ordering::SeqCst);
    }

    pub fn report(&self, slug: &str, item_id: Option<i64>, label: &str) {
        let mut map = self.now.lock().unwrap_or_else(|e| e.into_inner());
        if !map.contains_key(slug) && map.len() >= NOW_MAX {
            return;
        }
        let at = epoch();
        let since = map.get(slug).filter(|p| p.item_id == item_id).map_or(at, |p| p.since);
        map.insert(slug.to_owned(), Now { item_id, label: label.chars().take(120).collect(), since, seen: at });
    }

    pub fn playing(&self, slug: &str) -> Option<Now> {
        self.now.lock().unwrap_or_else(|e| e.into_inner()).get(slug).cloned()
    }

    pub fn forget(&self, slug: Option<&str>) {
        let mut map = self.now.lock().unwrap_or_else(|e| e.into_inner());
        match slug {
            Some(s) => {
                map.remove(s);
            }
            None => map.clear(),
        }
    }

    /// A player says it has played a "once" push to its end.
    pub fn done(&self, slug: &str, push_id: i64) {
        {
            let mut map = self.done.lock().unwrap_or_else(|e| e.into_inner());
            if map.contains_key(slug) || map.len() < NOW_MAX {
                map.insert(slug.to_owned(), push_id);
            }
        }
        self.bump();
    }

    pub fn done_id(&self, slug: &str) -> Option<i64> {
        self.done.lock().unwrap_or_else(|e| e.into_inner()).get(slug).copied()
    }

    pub fn done_clear(&self) {
        self.done.lock().unwrap_or_else(|e| e.into_inner()).clear();
    }
}
