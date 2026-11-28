//! The real clients against local servers: the chat request over HTTP, and the transcription socket through a WebSocket
//! handshake with the headers and sub-protocol a gateway reads. (The TLS is rustls with bundled roots, as signage_web's.)

use std::sync::{Arc, Mutex};
use std::time::Duration;

use futures_util::{SinkExt, StreamExt};
use relay_captions::net::{Https, WebSockets};
use relay_captions::session::{AudioFeed, Session};
use relay_captions::transcribe::Backends;
use relay_captions::translate::Chat;
use relay_core::Net;
use serde_json::{json, Value};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpListener;
use tokio_tungstenite::tungstenite::handshake::server::{Request, Response};
use tokio_tungstenite::tungstenite::Message;

/// A one-shot HTTP server: records the request, answers `status` with `body`.
async fn http_server(status: u16, body: &'static str) -> (u16, Arc<Mutex<String>>) {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let port = listener.local_addr().unwrap().port();
    let seen = Arc::new(Mutex::new(String::new()));
    let log = Arc::clone(&seen);
    tokio::spawn(async move {
        loop {
            let Ok((mut sock, _)) = listener.accept().await else { return };
            let log = Arc::clone(&log);
            tokio::spawn(async move {
                let mut buf = Vec::new();
                let mut chunk = [0u8; 4096];
                loop {
                    let n = sock.read(&mut chunk).await.unwrap_or(0);
                    if n == 0 {
                        break;
                    }
                    buf.extend_from_slice(&chunk[..n]);
                    let text = String::from_utf8_lossy(&buf).into_owned();
                    if let Some(at) = text.find("\r\n\r\n") {
                        let len = text[..at].to_lowercase().split("content-length: ").nth(1).and_then(|r| r.split("\r\n").next()).and_then(|n| n.trim().parse::<usize>().ok()).unwrap_or(0);
                        if buf.len() >= at + 4 + len {
                            *log.lock().unwrap() = text;
                            break;
                        }
                    }
                }
                let reply = format!("HTTP/1.1 {status} X\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}", body.len());
                let _ = sock.write_all(reply.as_bytes()).await;
                let _ = sock.shutdown().await;
            });
        }
    });
    (port, seen)
}

#[tokio::test]
async fn a_chat_completion_is_posted_with_the_key_and_the_reply_parsed() {
    let (port, seen) = http_server(200, r#"{"choices":[{"message":{"content":"{\"es\":\"Hola\"}"}}]}"#).await;
    let https = Https::new(Net::unrouted());
    let got = https.complete(&format!("http://127.0.0.1:{port}/v1/chat/completions"), "sk-abc", &json!({"model": "m"})).await.unwrap();
    assert_eq!(got["choices"][0]["message"]["content"], "{\"es\":\"Hola\"}");
    let req = seen.lock().unwrap().clone();
    assert!(req.starts_with("POST /v1/chat/completions HTTP/1.1\r\n"), "{req}");
    assert!(req.to_lowercase().contains("authorization: bearer sk-abc\r\n"), "{req}");
    assert!(req.to_lowercase().contains("content-type: application/json\r\n"), "{req}");
    assert!(req.ends_with(r#"{"model":"m"}"#), "{req}");
}

#[tokio::test]
async fn a_refusal_comes_back_as_its_status_and_detail() {
    let (port, _) = http_server(401, r#"{"error":{"message":"Incorrect API key provided"}}"#).await;
    let err = Https::new(Net::unrouted()).complete(&format!("http://127.0.0.1:{port}/x"), "bad", &json!({})).await.unwrap_err();
    assert!(err.starts_with("HTTP 401: "), "{err}");
    assert!(err.contains("Incorrect API key"), "{err}");
    assert!(relay_captions::transcript::is_fatal(&err), "a rejected key stops the translator");
}

#[tokio::test]
async fn an_unreachable_server_is_a_sentence_not_a_panic() {
    let free = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let port = free.local_addr().unwrap().port();
    drop(free);
    let err = Https::new(Net::unrouted()).complete(&format!("http://127.0.0.1:{port}/x"), "k", &json!({})).await.unwrap_err();
    assert_eq!(err, "The connection could not be made.");
}

/// A WebSocket server that records the handshake, answers with a transcript delta and a final for the first frame it gets, and echoes
/// the sub-protocol the client offered first.
// The handshake callback's error type is the library's, and it is large.
#[allow(clippy::result_large_err)]
async fn ws_server() -> (u16, Arc<Mutex<Vec<(String, String)>>>, Arc<Mutex<Vec<Message>>>) {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let port = listener.local_addr().unwrap().port();
    let headers = Arc::new(Mutex::new(Vec::new()));
    let frames = Arc::new(Mutex::new(Vec::new()));
    let (h, f) = (Arc::clone(&headers), Arc::clone(&frames));
    tokio::spawn(async move {
        let Ok((sock, _)) = listener.accept().await else { return };
        let callback = |req: &Request, mut resp: Response| {
            *h.lock().unwrap() = req.headers().iter().map(|(k, v)| (k.as_str().to_owned(), v.to_str().unwrap_or("").to_owned())).collect();
            if let Some(first) = req.headers().get("sec-websocket-protocol").and_then(|v| v.to_str().ok()).and_then(|v| v.split(',').next()) {
                resp.headers_mut().insert("sec-websocket-protocol", first.trim().parse().unwrap());
            }
            Ok(resp)
        };
        let Ok(mut ws) = tokio_tungstenite::accept_hdr_async(sock, callback).await else { return };
        let mut said = false;
        while let Some(Ok(msg)) = ws.next().await {
            f.lock().unwrap().push(msg);
            if !said && f.lock().unwrap().len() >= 2 {
                said = true;
                let _ = ws.send(Message::text(json!({"type": "transcript-delta", "delta": "Hello"}).to_string())).await;
                let _ = ws.send(Message::text(json!({"type": "transcript-final", "text": "Hello."}).to_string())).await;
            }
        }
    });
    (port, headers, frames)
}

#[tokio::test]
async fn a_scribe_session_talks_to_a_real_websocket() {
    let (port, headers, frames) = ws_server().await;
    let backends = Backends::builtin();
    let mut spec = (backends.get("scribe").unwrap().session)(&json!({}), "sk-gw", "ENGLISH");
    // Only the address differs from the gateway's.
    spec.url = format!("ws://127.0.0.1:{port}/v4/ai/transcription-model?ai-model-id=elevenlabs%2Fscribe-v2-realtime");
    let finals = Arc::new(Mutex::new(Vec::<String>::new()));
    let f = Arc::clone(&finals);
    let session = Session::new("transcribe", spec, Arc::new(WebSockets::new(Net::unrouted())), Arc::new(|_| {}), Arc::new(move |t| f.lock().unwrap().push(t.to_owned())), Arc::new(|| {}));
    let (tx, feed) = AudioFeed::channel(8);
    session.start(feed);
    tx.push(vec![1, 2, 3]);
    for _ in 0..200 {
        if !finals.lock().unwrap().is_empty() {
            break;
        }
        tokio::time::sleep(Duration::from_millis(25)).await;
    }
    assert_eq!(*finals.lock().unwrap(), ["Hello."]);
    assert_eq!(session.state(), "connected");
    let seen = headers.lock().unwrap().clone();
    let protocol = seen.iter().find(|(k, _)| k == "sec-websocket-protocol").map(|(_, v)| v.clone()).unwrap_or_default();
    assert_eq!(protocol, "ai-gateway-transcription.v1, ai-gateway-auth.sk-gw");
    let got = frames.lock().unwrap().clone();
    let start: Value = serde_json::from_str(got[0].to_text().unwrap()).unwrap();
    assert_eq!(start["type"], "transcription-stream.start");
    assert_eq!(got[1], Message::binary(vec![1u8, 2, 3]));
    session.stop().await;
    assert_eq!(session.state(), "idle");
}

#[tokio::test]
async fn a_handshake_the_server_refuses_reports_its_status() {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let port = listener.local_addr().unwrap().port();
    tokio::spawn(async move {
        let Ok((mut sock, _)) = listener.accept().await else { return };
        let mut buf = [0u8; 2048];
        let _ = sock.read(&mut buf).await;
        let _ = sock.write_all(b"HTTP/1.1 401 Unauthorized\r\nContent-Length: 0\r\nConnection: close\r\n\r\n").await;
    });
    let backends = Backends::builtin();
    let mut spec = (backends.get("openai").unwrap().session)(&json!({}), "bad", "ENGLISH");
    spec.url = format!("ws://127.0.0.1:{port}/v1/realtime");
    let session = Session::new("transcribe", spec, Arc::new(WebSockets::new(Net::unrouted())), Arc::new(|_| {}), Arc::new(|_| {}), Arc::new(|| {}));
    let (_tx, feed) = AudioFeed::channel(1);
    session.start(feed);
    for _ in 0..200 {
        if session.fatal() {
            break;
        }
        tokio::time::sleep(Duration::from_millis(25)).await;
    }
    assert!(session.fatal(), "a 401 on connect stops the retries");
    assert_eq!(session.error().as_deref(), Some("HTTP 401"));
    session.stop().await;
}
