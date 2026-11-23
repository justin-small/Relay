//! Websocket plumbing (#656) for the routes that need a socket: the Relay Helper (`/ws/helper`), intercom and remote intercom. Those
//! routes arrive with their own issues (#497's hub, #662); what they share is here, so each is a handler and nothing more.
//!
//! [`serve`] upgrades the request and gives the handler a [`Peer`]. The pump around it does what no handler should have to: a
//! ping every [`Options::ping`], a close when nothing at all (not even a pong) has arrived for [`Options::idle`], a limit on message
//! size, a close with code 1001 when the server shuts down, and a clean stop of both ends when either side finishes. A handler that
//! returns closes the socket; a socket that closes ends the handler's `recv` with `None`.

use std::future::Future;
use std::time::Duration;

use axum::extract::ws::{CloseFrame, Message, WebSocket, WebSocketUpgrade};
use axum::response::Response;
use bytes::Bytes;
use relay_core::Shutdown;
use tokio::sync::mpsc;

/// Close codes (RFC 6455).
pub const GOING_AWAY: u16 = 1001;
pub const TOO_BIG: u16 = 1009;
pub const POLICY: u16 = 1008;

#[derive(Clone, Copy, Debug)]
pub struct Options {
    /// How often a ping goes out on a quiet socket.
    pub ping: Duration,
    /// Close after this long with nothing received from the client.
    pub idle: Duration,
    /// The largest message accepted, in bytes.
    pub max_message: usize,
}

impl Default for Options {
    fn default() -> Self {
        Self { ping: Duration::from_secs(20), idle: Duration::from_secs(60), max_message: 1 << 20 }
    }
}

#[derive(Debug, PartialEq, Eq)]
pub enum Inbound {
    Text(String),
    Binary(Bytes),
}

enum Out {
    Message(Message),
    Close(u16, &'static str),
}

/// The handler's end of a socket.
pub struct Peer {
    rx: mpsc::Receiver<Inbound>,
    tx: mpsc::Sender<Out>,
}

impl Peer {
    /// The next message from the client; `None` once the socket has closed.
    pub async fn recv(&mut self) -> Option<Inbound> {
        self.rx.recv().await
    }

    /// False when the socket has closed.
    pub async fn send_text(&self, text: impl Into<String>) -> bool {
        self.tx.send(Out::Message(Message::Text(text.into().into()))).await.is_ok()
    }

    pub async fn send_binary(&self, data: impl Into<Bytes>) -> bool {
        self.tx.send(Out::Message(Message::Binary(data.into()))).await.is_ok()
    }

    /// Ask the pump to close the socket with `code` and a fixed `reason`, then stop.
    pub async fn close(&self, code: u16, reason: &'static str) {
        let _ = self.tx.send(Out::Close(code, reason)).await;
    }
}

/// Upgrade the request and run `handler` on the socket.
pub fn serve<H, Fut>(upgrade: WebSocketUpgrade, shutdown: Shutdown, opts: Options, handler: H) -> Response
where
    H: FnOnce(Peer) -> Fut + Send + 'static,
    Fut: Future<Output = ()> + Send + 'static,
{
    upgrade.max_message_size(opts.max_message).max_frame_size(opts.max_message).on_upgrade(move |socket| async move {
        let (in_tx, in_rx) = mpsc::channel(32);
        let (out_tx, out_rx) = mpsc::channel(32);
        let task = tokio::spawn(handler(Peer { rx: in_rx, tx: out_tx.clone() }));
        pump(socket, in_tx, out_rx, shutdown, opts, task.abort_handle()).await;
        // The handler ends on its own when `recv` gives `None`; abort one that is stuck on something else.
        drop(out_tx);
        if tokio::time::timeout(Duration::from_secs(2), task).await.is_err() {
            tracing::debug!("a websocket handler did not stop with its socket");
        }
    })
}

async fn pump(mut socket: WebSocket, inbound: mpsc::Sender<Inbound>, mut outbound: mpsc::Receiver<Out>, shutdown: Shutdown, opts: Options, handler: tokio::task::AbortHandle) {
    let mut ping = tokio::time::interval(opts.ping);
    ping.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);
    ping.reset();
    let mut last_heard = tokio::time::Instant::now();
    // A close is a handshake: send ours, then read until the client answers or gives up, so it is not cut off with a reset
    // (which would throw away frames it has not read yet, our close among them).
    async fn finish(socket: &mut WebSocket, code: u16, reason: &'static str) {
        if socket.send(Message::Close(Some(CloseFrame { code, reason: reason.into() }))).await.is_ok() {
            let _ = tokio::time::timeout(Duration::from_secs(1), async { while let Some(Ok(_)) = socket.recv().await {} }).await;
        }
    }
    loop {
        let idle_at = last_heard + opts.idle;
        tokio::select! {
            got = socket.recv() => match got {
                Some(Ok(msg)) => {
                    last_heard = tokio::time::Instant::now();
                    let item = match msg {
                        Message::Text(t) => Inbound::Text(t.as_str().to_owned()),
                        Message::Binary(b) => Inbound::Binary(b),
                        Message::Close(_) => break,
                        // axum answers a ping with a pong itself; a pong only shows the client is there.
                        Message::Ping(_) | Message::Pong(_) => continue,
                    };
                    if inbound.send(item).await.is_err() {
                        finish(&mut socket, 1000, "").await;
                        break;
                    }
                }
                // An error is a message over the size limit, a broken frame or a reset.
                Some(Err(_)) | None => break,
            },
            out = outbound.recv() => match out {
                Some(Out::Message(m)) => if socket.send(m).await.is_err() { break },
                Some(Out::Close(code, reason)) => {
                    finish(&mut socket, code, reason).await;
                    break;
                }
                // The handler returned: closing is its answer.
                None => {
                    finish(&mut socket, 1000, "").await;
                    break;
                }
            },
            _ = ping.tick() => if socket.send(Message::Ping(Bytes::new())).await.is_err() { break },
            () = tokio::time::sleep_until(idle_at) => {
                finish(&mut socket, POLICY, "idle").await;
                break;
            }
            () = shutdown.wait() => {
                finish(&mut socket, GOING_AWAY, "server stopping").await;
                break;
            }
        }
    }
    let _ = handler;
}
