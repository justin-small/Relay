//! `texttranslate.py`'s rules, with a chat model that answers from the test.

use std::sync::{Arc, Mutex};
use std::time::Duration;

use relay_captions::aimodels::{normalize, resolve, OPENAI_CHAT_URL, VERCEL_CHAT_URL};
use relay_captions::translate::{check, iso, prompt_name, Chat, Setup, Translator};
use relay_core::future::BoxFuture;
use serde_json::{json, Value};
use tokio::sync::{mpsc, Semaphore};

type Reply = Box<dyn Fn(&Value) -> Result<Value, String> + Send + Sync>;

struct Fake {
    calls: Mutex<Vec<(String, String, Value)>>,
    reply: Reply,
    gate: Option<Arc<Semaphore>>,
}

impl Chat for Fake {
    fn complete<'a>(&'a self, url: &'a str, key: &'a str, body: &'a Value) -> BoxFuture<'a, Result<Value, String>> {
        Box::pin(async move {
            self.calls.lock().unwrap().push((url.to_owned(), key.to_owned(), body.clone()));
            if let Some(g) = &self.gate {
                g.acquire().await.unwrap().forget();
            }
            (self.reply)(body)
        })
    }
}

/// A chat completion whose content is `{code: text}`.
fn answer(code: &str, text: &str) -> Result<Value, String> {
    Ok(json!({"choices": [{"message": {"content": json!({code: text}).to_string()}}]}))
}

/// The one language code a request asks for.
fn code_of(body: &Value) -> String {
    let user: Value = serde_json::from_str(body["messages"][1]["content"].as_str().unwrap()).unwrap();
    user["languages"].as_object().unwrap().keys().next().unwrap().clone()
}

fn text_of(body: &Value) -> String {
    let user: Value = serde_json::from_str(body["messages"][1]["content"].as_str().unwrap()).unwrap();
    user["text"].as_str().unwrap().to_owned()
}

struct Rig {
    tr: Translator,
    chat: Arc<Fake>,
    out: mpsc::UnboundedReceiver<(String, String)>,
}

fn rig(reply: Reply, gate: Option<Arc<Semaphore>>) -> Rig {
    let chat = Arc::new(Fake { calls: Mutex::new(Vec::new()), reply, gate });
    let (tx, out) = mpsc::unbounded_channel();
    let on_final: relay_captions::translate::Final = Arc::new(move |t, x| {
        let _ = tx.send((t.to_owned(), x.to_owned()));
    });
    let tr = Translator::new(chat.clone(), &Setup { url: "https://x/chat", key: "sk-1", model: "m-1", reasoning_effort: "", source_label: "English" }, on_final, Arc::new(|| {}));
    tr.start();
    Rig { tr, chat, out }
}

async fn next(r: &mut Rig) -> (String, String) {
    tokio::time::timeout(Duration::from_secs(5), r.out.recv()).await.expect("a translation").unwrap()
}

#[tokio::test]
async fn each_target_is_its_own_request_and_is_published_when_it_lands() {
    let mut r = rig(Box::new(|b| answer(&code_of(b), &format!("<{}>", code_of(b)))), None);
    r.tr.set_targets(["SPANISH".to_owned(), "FRENCH".to_owned()]);
    r.tr.submit("  Good morning.  ");
    let mut got = vec![next(&mut r).await, next(&mut r).await];
    got.sort();
    assert_eq!(got, [("FRENCH".to_owned(), "<fr>".to_owned()), ("SPANISH".to_owned(), "<es>".to_owned())]);
    let calls = r.chat.calls.lock().unwrap().clone();
    assert_eq!(calls.len(), 2);
    let (url, key, body) = &calls[0];
    assert_eq!((url.as_str(), key.as_str(), body["model"].as_str()), ("https://x/chat", "sk-1", Some("m-1")));
    assert!(body["messages"][0]["content"].as_str().unwrap().contains("translate it from English into each language"));
    assert_eq!(text_of(body), "Good morning.");
    assert_eq!(body["response_format"]["json_schema"]["strict"], true);
    assert_eq!(body["response_format"]["json_schema"]["schema"]["required"], json!([code_of(body)]));
    assert!(body.get("reasoning_effort").is_none());
}

#[tokio::test]
async fn lines_wait_for_the_last_and_carry_two_lines_of_context() {
    let mut r = rig(Box::new(|b| answer("es", &format!("T:{}", text_of(b)))), None);
    r.tr.set_targets(["SPANISH".to_owned()]);
    for line in ["one", "two", "three"] {
        r.tr.submit(line);
        assert_eq!(next(&mut r).await, ("SPANISH".to_owned(), format!("T:{line}")));
    }
    let calls = r.chat.calls.lock().unwrap().clone();
    let context = |i: usize| -> Value {
        let user: Value = serde_json::from_str(calls[i].2["messages"][1]["content"].as_str().unwrap()).unwrap();
        user["context"].clone()
    };
    assert_eq!((context(0), context(1), context(2)), (json!([]), json!(["one"]), json!(["one", "two"])));
    r.tr.submit("four");
    next(&mut r).await;
    let user: Value = serde_json::from_str(r.chat.calls.lock().unwrap()[3].2["messages"][1]["content"].as_str().unwrap()).unwrap();
    assert_eq!(user["context"], json!(["two", "three"]), "only the last two");
}

#[tokio::test]
async fn a_backlog_is_sent_as_one_line() {
    let gate = Arc::new(Semaphore::new(0));
    let mut r = rig(Box::new(|b| answer("es", &text_of(b))), Some(gate.clone()));
    r.tr.set_targets(["SPANISH".to_owned()]);
    r.tr.submit("first");
    tokio::time::timeout(Duration::from_secs(5), async {
        while r.chat.calls.lock().unwrap().is_empty() {
            tokio::task::yield_now().await;
        }
    })
    .await
    .unwrap();
    r.tr.submit("second");
    r.tr.submit("third");
    gate.add_permits(2);
    assert_eq!(next(&mut r).await.1, "first");
    assert_eq!(next(&mut r).await.1, "second third", "the lines that waited went as one");
    assert_eq!(r.chat.calls.lock().unwrap().len(), 2);
}

#[test]
fn an_unfit_translation_is_left_out() {
    let codes = ["es".to_owned()];
    assert_eq!(check("Hi", &json!({"es": "  Hola "}), &codes), [("es".to_owned(), "Hola".to_owned())]);
    assert!(check("Hi", &json!({}), &codes).is_empty(), "missing");
    assert!(check("Hi", &json!({"es": "   "}), &codes).is_empty(), "empty");
    assert!(check("Hi", &json!({"es": 5}), &codes).is_empty(), "not text");
    assert!(check("Hi", &json!({"es": "x".repeat(50)}), &codes).is_empty(), "more than 4 x 2 + 40 characters");
    assert_eq!(check("Hi", &json!({"es": "x".repeat(48)}), &codes).len(), 1);
}

#[tokio::test]
async fn a_dropped_reply_is_counted_and_nothing_is_published() {
    let mut r = rig(Box::new(|_| answer("es", "")), None);
    r.tr.set_targets(["SPANISH".to_owned()]);
    r.tr.submit("Hello.");
    tokio::time::timeout(Duration::from_secs(5), async {
        while r.tr.health()["lines"] != 1 {
            tokio::task::yield_now().await;
        }
    })
    .await
    .unwrap();
    assert_eq!(r.tr.health()["dropped_translations"], 1);
    assert_eq!(r.tr.state(), "connected");
    assert!(r.out.try_recv().is_err());
}

#[tokio::test]
async fn a_rejected_key_is_fatal_and_stops_the_lines_until_the_route_changes() {
    let r = rig(Box::new(|_| Err("HTTP 401: Incorrect API key provided".to_owned())), None);
    r.tr.set_targets(["SPANISH".to_owned()]);
    r.tr.submit("Hello.");
    tokio::time::timeout(Duration::from_secs(5), async {
        while r.tr.state() != "error" {
            tokio::task::yield_now().await;
        }
    })
    .await
    .unwrap();
    assert!(r.tr.fatal());
    assert!(r.tr.health()["error"].as_str().unwrap().contains("401"));
    r.tr.submit("ignored");
    tokio::task::yield_now().await;
    assert_eq!(r.chat.calls.lock().unwrap().len(), 1, "nothing more is sent to a provider that refused the key");
    r.tr.use_route("https://y/chat", "sk-2", "m-2");
    assert!(!r.tr.fatal());
    assert_eq!(r.tr.state(), "connected");
    assert_eq!(r.tr.health()["model"], "m-2");
    r.tr.stop();
    assert_eq!(r.tr.state(), "idle");
}

#[tokio::test]
async fn a_target_switched_off_in_flight_is_not_published() {
    let gate = Arc::new(Semaphore::new(0));
    let mut r = rig(Box::new(|_| answer("es", "Hola")), Some(gate.clone()));
    r.tr.set_targets(["SPANISH".to_owned()]);
    r.tr.submit("Hello.");
    tokio::time::timeout(Duration::from_secs(5), async {
        while r.chat.calls.lock().unwrap().is_empty() {
            tokio::task::yield_now().await;
        }
    })
    .await
    .unwrap();
    r.tr.remove_target("SPANISH");
    gate.add_permits(1);
    tokio::time::timeout(Duration::from_secs(5), async {
        while r.tr.health()["lines"] != 1 {
            tokio::task::yield_now().await;
        }
    })
    .await
    .unwrap();
    assert!(r.out.try_recv().is_err());
}

#[test]
fn language_names_and_codes() {
    assert_eq!((iso("SPANISH"), iso("HMONG"), iso("KLINGON")), ("es".to_owned(), "hmn".to_owned(), "kl".to_owned()));
    assert_eq!(prompt_name("SPANISH"), "Spanish");
    assert_eq!(prompt_name("ARABIC"), "Modern Standard Arabic");
    assert_eq!(prompt_name("HMONG"), "White Hmong (Hmoob Dawb), in the Romanized Popular Alphabet");
    assert_eq!(prompt_name("KLINGON"), "Klingon");
}

#[test]
fn the_translation_route_follows_the_keys() {
    let both = json!({"vercel_api_key": "v", "openai_api_key": "o"});
    let r = resolve(&both, "translation").unwrap();
    assert_eq!((r.provider, r.model.as_str(), r.key.as_str(), r.url, r.warning.is_none()), ("vercel", "openai/gpt-6-luna", "v", VERCEL_CHAT_URL, true));
    assert_eq!(r.label(), "Vercel AI Gateway");
    // Vercel chosen, only the OpenAI key saved: it runs on OpenAI and says why.
    let r = resolve(&json!({"openai_api_key": "o"}), "translation").unwrap();
    assert_eq!((r.provider, r.model.as_str(), r.url), ("openai", "gpt-6-luna", OPENAI_CHAT_URL));
    assert!(r.warning.unwrap().contains("Vercel AI Gateway, but its key is not saved"));
    // No key at all: the chosen provider comes back with an empty key.
    let r = resolve(&json!({}), "translation").unwrap();
    assert_eq!((r.provider, r.key.as_str(), r.warning), ("vercel", "", None));
    // A task switched to the OpenAI key stays on it.
    let cfg = json!({"vercel_api_key": "v", "ai": {"tasks": {"translation": {"provider": "openai", "openai_model": "gpt-x"}}}});
    let r = resolve(&cfg, "translation").unwrap();
    assert_eq!((r.provider, r.model.as_str(), r.key.as_str()), ("openai", "gpt-x", ""));
    assert!(resolve(&json!({}), "nothing").is_none());
}

#[test]
fn a_config_from_before_495_keeps_its_translation_model() {
    let n = normalize(None, "gpt-5-mini");
    assert_eq!(n["tasks"]["translation"]["openai_model"], "gpt-5-mini");
    assert_eq!(n["tasks"]["translation"]["model"], "openai/gpt-5-mini");
    // A model id with a space is not a model id.
    let n = normalize(Some(&json!({"tasks": {"translation": {"model": "bad id", "provider": "nope"}}})), "");
    assert_eq!((n["tasks"]["translation"]["model"].clone(), n["tasks"]["translation"]["provider"].clone()), (json!("openai/gpt-6-luna"), json!("vercel")));
    assert_eq!(n["tasks"].as_object().unwrap().len(), 4);
}
