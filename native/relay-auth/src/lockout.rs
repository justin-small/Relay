//! Failed sign-ins per client address (app/main.py `_login_failed`, `_login_locked`): five failures lock an address for ten
//! minutes (#278). Shared by password sign-in, PIN sign-in, first-run setup and the password change. Kept in memory, like
//! Python's: a restart clears it.

use std::collections::HashMap;
use std::sync::Mutex;
use std::time::{Duration, Instant};

pub const MAX_FAILS: u32 = 5;
pub const LOCKOUT: Duration = Duration::from_secs(600);
/// Every failed attempt costs this long before it is answered, so a guesser on the venue LAN is capped long before the
/// lock-out bites and an operator who mistyped waits under a second.
pub const FAIL_DELAY: Duration = Duration::from_millis(750);
const KEEP_AT_MOST: usize = 4096;

#[derive(Default)]
pub struct Lockout {
    fails: Mutex<HashMap<String, (u32, Instant)>>,
}

impl Lockout {
    pub fn new() -> Self {
        Self::default()
    }

    /// Time of lock-out left for this address, or `None` when it may try.
    pub fn locked(&self, ip: &str) -> Option<Duration> {
        self.locked_at(ip, Instant::now())
    }

    pub fn locked_at(&self, ip: &str, at: Instant) -> Option<Duration> {
        let mut fails = self.fails.lock().unwrap_or_else(|e| e.into_inner());
        let &(count, last) = fails.get(ip)?;
        if count < MAX_FAILS {
            return None;
        }
        let used = at.saturating_duration_since(last);
        if used >= LOCKOUT {
            fails.remove(ip);
            return None;
        }
        Some(LOCKOUT - used)
    }

    /// Count one failed attempt. Returns true when this one started a lock-out (the caller writes it to the audit log; the
    /// attempts refused during the lock-out are not, they would only fill it).
    pub fn failed(&self, ip: &str) -> bool {
        self.failed_at(ip, Instant::now())
    }

    pub fn failed_at(&self, ip: &str, at: Instant) -> bool {
        let mut fails = self.fails.lock().unwrap_or_else(|e| e.into_inner());
        if fails.len() >= KEEP_AT_MOST {
            fails.retain(|_, (_, last)| at.saturating_duration_since(*last) < LOCKOUT);
        }
        let (mut count, last) = fails.get(ip).copied().unwrap_or((0, at));
        // A quiet lock-out window clears the slate rather than accumulating forever.
        if count > 0 && at.saturating_duration_since(last) > LOCKOUT {
            count = 0;
        }
        count += 1;
        fails.insert(ip.to_string(), (count, at));
        count == MAX_FAILS
    }

    /// A successful sign-in clears the address's count.
    pub fn clear(&self, ip: &str) {
        self.fails.lock().unwrap_or_else(|e| e.into_inner()).remove(ip);
    }
}

/// The sentence for a locked address (`_locked_sentence`).
pub fn sentence(left: Duration) -> String {
    format!("Too many attempts. Try again in {} min.", left.as_secs() / 60 + 1)
}
