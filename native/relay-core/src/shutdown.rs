//! Shutdown (#652): one signal every task listens to. The server sets it on Ctrl-C or SIGTERM; modules and listeners finish
//! what they are doing and return. A clone is cheap and sees the same signal.

use tokio::sync::watch;

#[derive(Clone, Debug)]
pub struct Shutdown {
    tx: watch::Sender<bool>,
    rx: watch::Receiver<bool>,
}

impl Default for Shutdown {
    fn default() -> Self {
        Self::new()
    }
}

impl Shutdown {
    pub fn new() -> Self {
        let (tx, rx) = watch::channel(false);
        Self { tx, rx }
    }

    /// Tell everything to stop. Safe to call twice.
    pub fn trigger(&self) {
        self.tx.send_replace(true);
    }

    pub fn is_triggered(&self) -> bool {
        *self.rx.borrow()
    }

    /// Returns when [`Shutdown::trigger`] has been called (at once if it already was).
    pub async fn wait(&self) {
        let mut rx = self.rx.clone();
        // wait_for returns Err only when the sender is gone, and `self` keeps one.
        let _ = rx.wait_for(|stopped| *stopped).await;
    }
}
