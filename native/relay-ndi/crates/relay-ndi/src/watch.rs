//! Deciding when a source the booth relies on has a real problem (issue 562): a fault must last the hold time before it
//! is raised, and be gone for a few seconds before it is cleared, so a blip raises nothing and a flap raises once.

use std::collections::HashMap;

/// How long a fault must be gone before it counts as cleared.
pub const CLEAR_AFTER_S: f64 = 5.0;

#[derive(Clone, Copy, PartialEq, Eq, Hash, Debug, PartialOrd, Ord)]
pub enum Kind {
    Offline,
    Frames,
    Silence,
    Clipping,
}

impl Kind {
    pub fn name(self) -> &'static str {
        match self {
            Kind::Offline => "offline",
            Kind::Frames => "frames",
            Kind::Silence => "silence",
            Kind::Clipping => "clipping",
        }
    }

    pub fn parse(s: &str) -> Option<Kind> {
        [Kind::Offline, Kind::Frames, Kind::Silence, Kind::Clipping].into_iter().find(|k| k.name() == s)
    }
}

#[derive(Clone, PartialEq, Debug)]
pub enum Event {
    Raised { source: String, kind: Kind, why: String },
    Cleared { source: String, kind: Kind, why: String },
}

struct Fault {
    /// When it started holding, until it has been raised.
    since: f64,
    /// Raised: the sentence it was raised with, and when it was last seen to be gone (None while it holds).
    raised: Option<(String, Option<f64>)>,
}

pub struct Watcher {
    hold: f64,
    faults: HashMap<(String, Kind), Fault>,
}

impl Watcher {
    pub fn new(hold_s: f64) -> Watcher {
        Watcher { hold: hold_s.max(0.0), faults: HashMap::new() }
    }

    pub fn set_hold(&mut self, hold_s: f64) {
        self.hold = hold_s.max(0.0);
    }

    /// What is wrong with `source` right now (`now` in monotonic seconds); every kind not listed is fine. Returns what
    /// changed.
    pub fn feed(&mut self, now: f64, source: &str, problems: &[(Kind, String)]) -> Vec<Event> {
        let mut out = Vec::new();
        for (kind, why) in problems {
            let f = self.faults.entry((source.to_string(), *kind)).or_insert(Fault { since: now, raised: None });
            match &mut f.raised {
                Some((text, gone)) => {
                    *gone = None;
                    *text = why.clone();
                }
                None if now - f.since >= self.hold => {
                    f.raised = Some((why.clone(), None));
                    out.push(Event::Raised { source: source.to_string(), kind: *kind, why: why.clone() });
                }
                None => {}
            }
        }
        let held: Vec<Kind> = problems.iter().map(|(k, _)| *k).collect();
        let mut done = Vec::new();
        for ((s, kind), f) in self.faults.iter_mut() {
            if s != source || held.contains(kind) {
                continue;
            }
            match &mut f.raised {
                None => done.push((*kind, None)),
                Some((why, gone)) => {
                    let since = *gone.get_or_insert(now);
                    if now - since >= CLEAR_AFTER_S {
                        done.push((*kind, Some(why.clone())));
                    }
                }
            }
        }
        done.sort_by_key(|(k, _)| *k);
        for (kind, why) in done {
            self.faults.remove(&(source.to_string(), kind));
            if let Some(why) = why {
                out.push(Event::Cleared { source: source.to_string(), kind, why });
            }
        }
        out
    }

    /// The source is no longer watched: everything raised for it is cleared.
    pub fn forget(&mut self, source: &str) -> Vec<Event> {
        let mut keys: Vec<(String, Kind)> = self.faults.keys().filter(|(s, _)| s == source).cloned().collect();
        keys.sort_by_key(|(_, k)| *k);
        let mut out = Vec::new();
        for key in keys {
            if let Some(Fault { raised: Some((why, _)), .. }) = self.faults.remove(&key) {
                out.push(Event::Cleared { source: key.0, kind: key.1, why });
            }
        }
        out
    }

    /// Everything raised and not cleared, sorted: `(source, kind, why)`.
    pub fn active(&self) -> Vec<(String, Kind, String)> {
        let mut v: Vec<(String, Kind, String)> = self
            .faults
            .iter()
            .filter_map(|((s, k), f)| f.raised.as_ref().map(|(why, _)| (s.clone(), *k, why.clone())))
            .collect();
        v.sort();
        v
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn off() -> Vec<(Kind, String)> {
        vec![(Kind::Offline, "gone".to_string())]
    }

    #[test]
    fn a_blip_shorter_than_the_hold_raises_nothing() {
        let mut w = Watcher::new(10.0);
        assert!(w.feed(0.0, "CAM", &off()).is_empty());
        assert!(w.feed(5.0, "CAM", &off()).is_empty());
        assert!(w.feed(6.0, "CAM", &[]).is_empty());
        assert!(w.feed(100.0, "CAM", &[]).is_empty());
        assert!(w.active().is_empty());
    }

    #[test]
    fn a_fault_that_holds_is_raised_once_and_cleared_after_it_stays_gone() {
        let mut w = Watcher::new(10.0);
        w.feed(0.0, "CAM", &off());
        let up = w.feed(10.0, "CAM", &off());
        assert_eq!(up, vec![Event::Raised { source: "CAM".into(), kind: Kind::Offline, why: "gone".into() }]);
        assert!(w.feed(11.0, "CAM", &off()).is_empty(), "raised only once");
        assert_eq!(w.active().len(), 1);
        assert!(w.feed(12.0, "CAM", &[]).is_empty());
        assert!(w.feed(16.0, "CAM", &[]).is_empty(), "gone for under five seconds");
        let down = w.feed(17.0, "CAM", &[]);
        assert_eq!(down, vec![Event::Cleared { source: "CAM".into(), kind: Kind::Offline, why: "gone".into() }]);
        assert!(w.active().is_empty());
    }

    #[test]
    fn a_flap_while_raised_does_not_clear_it() {
        let mut w = Watcher::new(0.0);
        assert_eq!(w.feed(0.0, "CAM", &off()).len(), 1);
        w.feed(1.0, "CAM", &[]);
        w.feed(3.0, "CAM", &off()); // back before it was cleared
        assert!(w.feed(9.0, "CAM", &off()).is_empty());
        assert!(w.feed(20.0, "CAM", &[]).is_empty());
        assert_eq!(w.feed(25.0, "CAM", &[]).len(), 1);
    }

    #[test]
    fn sources_and_kinds_are_tracked_separately() {
        let mut w = Watcher::new(0.0);
        let a = w.feed(0.0, "A", &[(Kind::Frames, "drops".into()), (Kind::Silence, "quiet".into())]);
        assert_eq!(a.len(), 2);
        assert_eq!(w.feed(0.0, "B", &off()).len(), 1);
        assert_eq!(w.active().iter().map(|a| (a.0.as_str(), a.1)).collect::<Vec<_>>(),
                   vec![("A", Kind::Frames), ("A", Kind::Silence), ("B", Kind::Offline)]);
        let c = w.forget("A");
        assert_eq!(c.len(), 2);
        assert_eq!(w.active().len(), 1);
        assert!(w.forget("A").is_empty());
    }

    #[test]
    fn kinds_have_names() {
        for k in [Kind::Offline, Kind::Frames, Kind::Silence, Kind::Clipping] {
            assert_eq!(Kind::parse(k.name()), Some(k));
        }
        assert_eq!(Kind::parse("other"), None);
    }
}
