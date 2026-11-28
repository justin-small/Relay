//! The NDI router (#658) on core routes held in memory and a fake NDI runtime. A route asked for through core reaches the router's provider and is applied to the NDI
//! output; a preset saves and takes; what the output reports is what core shows.

use std::collections::HashMap;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};

use relay_core::future::BoxFuture;
use relay_core::routes::{RouteError, RouteProvider, RouteResult, Routes, WantedSource};
use relay_module_ndi::router::{Backend, Closer, Deps, Instance, Router};
use serde_json::{json, Value};

/// Core's routes in memory, with the semantics `relay-audio` gives them (the real ones are tested in `relay-server/tests/routescap.rs`):
/// a destination takes one source, a route is applied through the destination's provider, and what the provider reads back is "actual".
#[derive(Default)]
struct MemRoutes {
    chans: Mutex<Vec<Chan>>,
    routes: Mutex<Vec<(usize, usize)>>,
    salvos: Mutex<Vec<Salvo>>,
    providers: Mutex<HashMap<String, Arc<dyn RouteProvider>>>,
    next_salvo: AtomicUsize,
}

/// id, name, scope, and each destination with the source it took (or none).
type Salvo = (i64, String, String, Vec<(usize, Option<usize>)>);

struct Chan {
    provider: String,
    key: String,
    direction: String,
    name: String,
    missing: bool,
}

fn cref(i: usize) -> String {
    format!("channel:relay:{}", i + 1)
}

fn index(r: &str) -> Option<usize> {
    r.strip_prefix("channel:relay:")?.parse::<usize>().ok()?.checked_sub(1)
}

impl MemRoutes {
    fn end(&self, i: usize) -> Value {
        let c = self.chans.lock().expect("lock");
        json!({"provider": c[i].provider, "key": c[i].key, "direction": c[i].direction, "gear_name": c[i].name, "missing": c[i].missing})
    }

    fn provider(&self, i: usize) -> Option<Arc<dyn RouteProvider>> {
        let p = self.chans.lock().expect("lock")[i].provider.clone();
        self.providers.lock().expect("lock").get(&p).cloned()
    }

    async fn push(&self, dest: usize) -> Vec<String> {
        let Some(p) = self.provider(dest) else { return vec![] };
        let wanted: Vec<WantedSource> = self.routes.lock().expect("lock").iter().filter(|(d, _)| *d == dest).map(|(_, s)| WantedSource { ends: vec![self.end(*s)], gain_db: None }).collect();
        let said = p.apply(&self.end(dest), &wanted).await;
        if said.is_empty() { vec![] } else { vec![said] }
    }

    fn upsert(&self, provider: &str, key: &str, direction: &str, name: &str, missing: bool) -> usize {
        let mut c = self.chans.lock().expect("lock");
        if let Some(i) = c.iter().position(|x| x.provider == provider && x.key == key) {
            c[i].missing = missing && c[i].missing;
            return i;
        }
        c.push(Chan { provider: provider.to_owned(), key: key.to_owned(), direction: direction.to_owned(), name: name.to_owned(), missing });
        c.len() - 1
    }
}

impl Routes for MemRoutes {
    fn ensure<'a>(&'a self, provider: &'a str, key: &'a str, _kind: &'a str, direction: &'a str, gear_name: &'a str) -> BoxFuture<'a, RouteResult<Option<String>>> {
        Box::pin(async move { Ok(Some(cref(self.upsert(provider, key, direction, gear_name, true)))) })
    }
    fn channels(&self) -> BoxFuture<'_, RouteResult<Vec<Value>>> {
        Box::pin(async move {
            let n = self.chans.lock().expect("lock").len();
            Ok((0..n).map(|i| json!({"ref": cref(i), "shown": self.chans.lock().expect("lock")[i].name, "ends": [self.end(i)]})).collect())
        })
    }
    fn views<'a>(&'a self, provider: &'a str) -> BoxFuture<'a, RouteResult<Vec<Value>>> {
        Box::pin(async move {
            let routes = self.routes.lock().expect("lock").clone();
            let mut out = Vec::new();
            for (d, s) in routes {
                if self.chans.lock().expect("lock")[d].provider != provider {
                    continue;
                }
                let actual = match self.provider(d) {
                    Some(p) => p.read(&self.end(d)).await,
                    None => None,
                };
                let src_key = self.chans.lock().expect("lock")[s].key.clone();
                let state = match actual {
                    None => "wanted",
                    Some(a) if a.iter().any(|x| x.key == src_key) => "same",
                    Some(_) => "differs",
                };
                out.push(json!({"destination": cref(d), "state": state, "wanted": [{"ref": cref(s)}], "sources": [{"ref": cref(s)}]}));
            }
            Ok(out)
        })
    }
    fn report<'a>(&'a self, provider: &'a str, reported: Vec<Value>) -> BoxFuture<'a, ()> {
        Box::pin(async move {
            let keys: Vec<String> = reported.iter().map(|r| r["key"].as_str().unwrap_or("").to_owned()).collect();
            for r in &reported {
                let i = self.upsert(provider, r["key"].as_str().unwrap_or(""), r["direction"].as_str().unwrap_or("source"), r["gear_name"].as_str().unwrap_or(""), false);
                self.chans.lock().expect("lock")[i].missing = false;
            }
            for c in self.chans.lock().expect("lock").iter_mut().filter(|c| c.provider == provider) {
                if !keys.contains(&c.key) {
                    c.missing = true;
                }
            }
        })
    }
    fn set<'a>(&'a self, destination: &'a str, source: &'a str) -> BoxFuture<'a, RouteResult<Vec<String>>> {
        Box::pin(async move {
            let (Some(d), Some(s)) = (index(destination), index(source)) else { return Err(RouteError::Refused("That channel does not exist.")) };
            if d == s {
                return Err(RouteError::Refused("A channel cannot feed itself."));
            }
            {
                let mut r = self.routes.lock().expect("lock");
                r.retain(|(x, _)| *x != d);
                r.push((d, s));
            }
            Ok(self.push(d).await)
        })
    }
    fn clear<'a>(&'a self, destination: &'a str) -> BoxFuture<'a, RouteResult<Vec<String>>> {
        Box::pin(async move {
            let Some(d) = index(destination) else { return Err(RouteError::Refused("That channel does not exist.")) };
            self.routes.lock().expect("lock").retain(|(x, _)| *x != d);
            Ok(self.push(d).await)
        })
    }
    fn reapply<'a>(&'a self, provider: &'a str) -> BoxFuture<'a, RouteResult<Vec<String>>> {
        Box::pin(async move {
            let dests: Vec<usize> = self.routes.lock().expect("lock").iter().map(|(d, _)| *d).filter(|d| self.chans.lock().expect("lock")[*d].provider == provider).collect();
            let mut out = Vec::new();
            for d in dests {
                out.extend(self.push(d).await);
            }
            Ok(out)
        })
    }
    fn salvos<'a>(&'a self, scope: &'a str) -> BoxFuture<'a, RouteResult<Vec<Value>>> {
        Box::pin(async move {
            Ok(self.salvos.lock().expect("lock").iter().filter(|s| s.2 == scope).map(|(id, name, _, rows)| {
                json!({"id": id, "name": name, "routes": rows.iter().map(|(d, s)| json!({"destination": cref(*d), "source": s.map(cref).unwrap_or_default()})).collect::<Vec<_>>()})
            }).collect())
        })
    }
    fn save_salvo<'a>(&'a self, name: &'a str, destinations: Vec<String>, scope: &'a str, sid: Option<i64>) -> BoxFuture<'a, RouteResult<Value>> {
        Box::pin(async move {
            if name.trim().is_empty() {
                return Err(RouteError::Refused("Give the preset a name."));
            }
            let rows: Vec<(usize, Option<usize>)> = destinations.iter().filter_map(|d| index(d)).map(|d| (d, self.routes.lock().expect("lock").iter().find(|(x, _)| *x == d).map(|(_, s)| *s))).collect();
            let id = sid.unwrap_or_else(|| self.next_salvo.fetch_add(1, Ordering::SeqCst) as i64 + 1);
            let mut all = self.salvos.lock().expect("lock");
            all.retain(|s| s.0 != id);
            all.push((id, name.trim().to_owned(), scope.to_owned(), rows));
            Ok(json!({"id": id, "name": name.trim()}))
        })
    }
    fn delete_salvo(&self, sid: i64) -> BoxFuture<'_, RouteResult<()>> {
        Box::pin(async move {
            let mut all = self.salvos.lock().expect("lock");
            let before = all.len();
            all.retain(|s| s.0 != sid);
            if all.len() == before { Err(RouteError::Refused("That preset does not exist.")) } else { Ok(()) }
        })
    }
    fn take_salvo(&self, sid: i64) -> BoxFuture<'_, RouteResult<Vec<String>>> {
        Box::pin(async move {
            let rows = self.salvos.lock().expect("lock").iter().find(|s| s.0 == sid).map(|s| s.3.clone()).ok_or(RouteError::Refused("That preset does not exist."))?;
            let mut out = Vec::new();
            for (d, s) in rows {
                match s {
                    Some(s) => out.extend(self.set(&cref(d), &cref(s)).await?),
                    None => out.extend(self.clear(&cref(d)).await?),
                }
            }
            Ok(out)
        })
    }
    fn forget<'a>(&'a self, channel: &'a str) -> BoxFuture<'a, RouteResult<()>> {
        Box::pin(async move {
            let i = index(channel).ok_or(RouteError::Refused("That channel does not exist."))?;
            let mut c = self.chans.lock().expect("lock");
            if !c[i].missing {
                return Err(RouteError::Refused("That channel is still reported; only a missing one can be forgotten."));
            }
            c[i].provider = "forgotten".to_owned();
            self.routes.lock().expect("lock").retain(|(d, s)| *d != i && *s != i);
            Ok(())
        })
    }
    fn register(&self, provider: &str, api: Arc<dyn RouteProvider>) {
        self.providers.lock().expect("lock").insert(provider.to_owned(), api);
    }
    fn withdraw(&self, provider: &str) {
        self.providers.lock().expect("lock").remove(provider);
    }
}

/// What the fake NDI runtime was told, in order: `("Relay - Lobby", "Camera 1 (OBS)")`.
#[derive(Default)]
struct Told {
    changes: Mutex<Vec<(String, String)>>,
    opened: Mutex<Vec<String>>,
    combiners: Mutex<Vec<(String, String, String)>>,
    fail_open: AtomicBool,
}

struct FakeInstance {
    name: String,
    told: Arc<Told>,
}

impl Instance for FakeInstance {
    fn change(&self, source: &str) -> bool {
        self.told.changes.lock().expect("lock").push((self.name.clone(), source.to_owned()));
        true
    }
    fn clear(&self) -> bool {
        self.told.changes.lock().expect("lock").push((self.name.clone(), String::new()));
        true
    }
    fn close(&self) {}
}

struct FakeCloser;

impl Closer for FakeCloser {
    fn close(&self) {}
}

struct FakeNdi(Arc<Told>);

impl Backend for FakeNdi {
    fn open_router(&self, name: &str) -> Result<Box<dyn Instance>, String> {
        if self.0.fail_open.load(Ordering::SeqCst) {
            return Err("no".into());
        }
        self.0.opened.lock().expect("lock").push(name.to_owned());
        Ok(Box::new(FakeInstance { name: name.to_owned(), told: Arc::clone(&self.0) }))
    }
    fn open_combiner(&self, name: &str, video: &str, audio: &str) -> Result<Box<dyn Closer>, String> {
        self.0.combiners.lock().expect("lock").push((name.to_owned(), video.to_owned(), audio.to_owned()));
        Ok(Box::new(FakeCloser))
    }
}

struct World {
    routes: Arc<MemRoutes>,
    router: Router,
    told: Arc<Told>,
    settings: Arc<Mutex<Value>>,
    running: Arc<AtomicBool>,
}

fn sources(names: &[&str]) -> Vec<Value> {
    names.iter().map(|n| json!({"key": format!("src:{n}"), "kind": "video", "direction": "source", "gear_name": n})).collect()
}

async fn world() -> World {
    let routes = Arc::new(MemRoutes::default());
    let told = Arc::new(Told::default());
    let settings = Arc::new(Mutex::new(json!({"router_outputs": []})));
    let running = Arc::new(AtomicBool::new(true));
    let core: Arc<dyn Routes> = routes.clone();
    let router = Router::new(Deps {
        routes: Arc::clone(&core),
        backend: Arc::new(FakeNdi(Arc::clone(&told))),
        settings: Arc::new({
            let s = Arc::clone(&settings);
            move || s.lock().expect("lock").clone()
        }),
        save: Arc::new({
            let s = Arc::clone(&settings);
            move |c| {
                let mut cur = s.lock().expect("lock");
                for (k, v) in c.as_object().expect("object") {
                    cur[k] = v.clone();
                }
            }
        }),
        notify: Arc::new(|| {}),
        running: Arc::new({
            let r = Arc::clone(&running);
            move || r.load(Ordering::SeqCst)
        }),
        report: Arc::new({
            let (routes, settings) = (Arc::clone(&core), Arc::clone(&settings));
            move || {
                let (routes, settings) = (Arc::clone(&routes), Arc::clone(&settings));
                Box::pin(async move {
                    let mut found = sources(&["Camera 1 (OBS)", "Camera 2 (OBS)"]);
                    for r in settings.lock().expect("lock")["router_outputs"].as_array().into_iter().flatten() {
                        found.push(json!({"key": format!("route:{}", r["id"].as_str().unwrap_or("")), "kind": "video", "direction": "destination",
                                          "gear_name": relay_module_ndi::router::ndi_name(r["name"].as_str().unwrap_or(""))}));
                    }
                    routes.report("ndi", found).await;
                })
            }
        }),
    });
    core.register("ndi", router.provider());
    World { routes, router, told, settings, running }
}

fn changes(w: &World) -> Vec<(String, String)> {
    w.told.changes.lock().expect("lock").clone()
}

#[tokio::test(flavor = "multi_thread")]
async fn a_route_asked_for_through_core_points_the_ndi_output() {
    let w = world().await;
    let out = w.router.add_output("Lobby  TV").await.expect("added");
    let oid = out["id"].as_str().expect("id").to_owned();
    assert_eq!(out["name"], "Lobby TV");
    assert_eq!(w.told.opened.lock().expect("lock").as_slice(), ["Relay - Lobby TV"], "the output is an NDI source named for it");
    assert_eq!(w.router.route(&oid, "Camera 1 (OBS)").await, "");
    assert_eq!(changes(&w), vec![("Relay - Lobby TV".to_owned(), "Camera 1 (OBS)".to_owned())], "NDI was told, once");
    let snap = w.router.snapshot().await;
    let r = &snap["routes"][0];
    assert_eq!((r["source"].clone(), r["asked"].clone(), r["state"].clone(), r["live"].clone()), (json!("Camera 1 (OBS)"), json!("Camera 1 (OBS)"), json!("same"), json!(true)));
    assert_eq!(r["ndi"], "Relay - Lobby TV");
    assert_eq!(w.router.route(&oid, "").await, "");
    assert_eq!(changes(&w).last().map(|c| c.1.clone()), Some(String::new()), "an empty source clears the output");
}

#[tokio::test(flavor = "multi_thread")]
async fn an_output_cannot_show_itself_and_names_are_kept_apart() {
    let w = world().await;
    let a = w.router.add_output("Lobby").await.expect("added");
    assert_eq!(w.router.add_output("lobby").await.err().as_deref(), Some("There is already an output with that name."));
    assert_eq!(w.router.add_output("  ").await.err().as_deref(), Some("Give the output a name."));
    let id = a["id"].as_str().expect("id");
    assert_eq!(w.router.route(id, "Relay - Lobby").await, "An output cannot show itself.");
    assert_eq!(w.router.route(id, "PC (Relay - Lobby)").await, "An output cannot show itself.");
    assert_eq!(w.router.route("nope", "x").await, "That output does not exist.");
    assert!(changes(&w).is_empty());
}

#[tokio::test(flavor = "multi_thread")]
async fn at_most_sixteen_outputs() {
    let w = world().await;
    for i in 0..16 {
        w.router.add_output(&format!("Out {i}")).await.expect("added");
    }
    assert_eq!(w.router.add_output("One more").await.err().as_deref(), Some("At most 16 routed outputs."));
    assert_eq!(w.router.live_count(), 16);
}

#[tokio::test(flavor = "multi_thread")]
async fn a_preset_saves_what_every_output_shows_and_takes_it_back() {
    let w = world().await;
    let a = w.router.add_output("A").await.expect("a")["id"].as_str().expect("id").to_owned();
    let b = w.router.add_output("B").await.expect("b")["id"].as_str().expect("id").to_owned();
    assert_eq!(w.router.route(&a, "Camera 1 (OBS)").await, "");
    assert_eq!(w.router.route(&b, "Camera 2 (OBS)").await, "");
    let saved = w.router.save_salvo("Service", "").await.expect("saved");
    assert_eq!(saved["name"], "Service");
    assert_eq!(w.router.route(&a, "Camera 2 (OBS)").await, "");
    assert_eq!(w.router.route(&b, "").await, "");
    let before = changes(&w).len();
    assert_eq!(w.router.take(saved["id"].as_str().expect("id")).await, "");
    let snap = w.router.snapshot().await;
    let shown = |oid: &str| snap["routes"].as_array().expect("routes").iter().find(|r| r["id"] == oid).map(|r| r["source"].clone());
    assert_eq!((shown(&a), shown(&b)), (Some(json!("Camera 1 (OBS)")), Some(json!("Camera 2 (OBS)"))));
    assert!(changes(&w).len() > before, "taking a preset told NDI");
    assert_eq!(snap["salvos"][0]["routes"][&a], "Camera 1 (OBS)");
    assert!(w.router.delete_salvo(saved["id"].as_str().expect("id")).await);
    assert!(!w.router.delete_salvo("999").await);
    assert_eq!(w.router.take("999").await, "That preset does not exist.");
    assert!(w.router.take("abc").await.contains("does not exist"));
}

#[tokio::test(flavor = "multi_thread")]
async fn presets_are_limited_and_named() {
    let w = world().await;
    w.router.add_output("A").await.expect("a");
    assert_eq!(w.router.save_salvo("  ", "").await.err().as_deref(), Some("Give the preset a name."));
    for i in 0..24 {
        w.router.save_salvo(&format!("P{i}"), "").await.expect("saved");
    }
    assert_eq!(w.router.save_salvo("P24", "").await.err().as_deref(), Some("At most 24 presets."));
}

#[tokio::test(flavor = "multi_thread")]
async fn removing_an_output_closes_it_and_lets_core_forget_it() {
    let w = world().await;
    let a = w.router.add_output("A").await.expect("a")["id"].as_str().expect("id").to_owned();
    assert_eq!(w.router.route(&a, "Camera 1 (OBS)").await, "");
    assert!(w.router.remove_output(&a).await);
    assert!(!w.router.remove_output(&a).await);
    assert_eq!(w.router.live_count(), 0);
    assert!(w.settings.lock().expect("lock")["router_outputs"].as_array().expect("list").is_empty());
    let left: Vec<Value> = w.routes.channels().await.expect("channels").into_iter().filter(|c| c["ends"].as_array().is_some_and(|e| e.iter().any(|x| x["key"] == format!("route:{a}") && x["provider"] == "ndi"))).collect();
    assert!(left.is_empty(), "core let the channel go with its routes");
}

#[tokio::test(flavor = "multi_thread")]
async fn sound_from_another_source_is_a_combiner() {
    let w = world().await;
    let a = w.router.add_output("A").await.expect("a")["id"].as_str().expect("id").to_owned();
    assert_eq!(w.router.set_audio(&a, "Relay - A").await, "An output cannot take sound from itself.");
    assert_eq!(w.router.set_audio(&a, "Mic (Desk)").await, "");
    assert_eq!(w.told.combiners.lock().expect("lock").first().cloned(), Some(("Relay - A".to_owned(), String::new(), "Mic (Desk)".to_owned())), "sound alone until a picture is chosen");
    assert_eq!(w.router.route(&a, "Camera 1 (OBS)").await, "");
    assert_eq!(w.told.combiners.lock().expect("lock").last().cloned(), Some(("Relay - A".to_owned(), "Camera 1 (OBS)".to_owned(), "Mic (Desk)".to_owned())), "the picture and the other sound together");
    assert_eq!(w.settings.lock().expect("lock")["router_outputs"][0]["audio"], "Mic (Desk)");
    assert_eq!(w.router.set_audio("nope", "x").await, "That output does not exist.");
}

#[tokio::test(flavor = "multi_thread")]
async fn nothing_is_applied_while_ndi_is_off_and_it_is_when_it_comes_on() {
    let w = world().await;
    let a = w.router.add_output("A").await.expect("a")["id"].as_str().expect("id").to_owned();
    w.running.store(false, Ordering::SeqCst);
    assert_eq!(w.router.route(&a, "Camera 1 (OBS)").await, "", "asked for, so core keeps it");
    assert!(changes(&w).is_empty(), "NDI is off, so it was not told");
    w.running.store(true, Ordering::SeqCst);
    w.router.start().await;
    assert_eq!(changes(&w).last().map(|c| c.1.clone()), Some("Camera 1 (OBS)".to_owned()), "start applies what core holds");
}

#[tokio::test(flavor = "multi_thread")]
async fn an_output_ndi_will_not_open_says_so() {
    let w = world().await;
    w.told.fail_open.store(true, Ordering::SeqCst);
    let a = w.router.add_output("A").await.expect("a")["id"].as_str().expect("id").to_owned();
    let snap = w.router.snapshot().await;
    assert_eq!((snap["routes"][0]["live"].clone(), snap["routes"][0]["error"].clone()), (json!(false), json!("NDI would not create this output.")));
    assert_eq!(w.router.route(&a, "Camera 1 (OBS)").await, "NDI would not create this output.");
}
