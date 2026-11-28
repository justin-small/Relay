//! What the server offers modules by name besides the panels (#666): the directory of teams and positions (`app/directory.py`) and the
//! audio channels (`app/channels.py`) as `relay_core::caps` shapes, so Planning Center's rundown can ask for them with no import.
//!
//! Both read a copy that [`Directory::refresh`] / [`ChannelList::refresh`] bring up to date, because the caps' methods are plain
//! functions and the tables are read with `await`. A caller refreshes once, then reads.

use std::collections::{BTreeMap, HashMap, HashSet};
use std::sync::{Arc, Mutex, PoisonError};

use relay_audio::channels::Channels;
use relay_core::caps::{DIRECTORY_CHANGED, PLAN_CHANGED, team_ref, position_ref, Channel, ChannelCatalog, Described, DirItem, Directory, PlanCap, PCO, PLAN};
use relay_core::future::BoxFuture;
use relay_core::Host;
use serde_json::Value;
use sqlx::{Row, SqlitePool};


// ------------------------------------------------------------------------------------------------------------- directory
#[derive(Clone)]
struct Row_ {
    reference: String,
    kind: String,
    parent: String,
    name: String,
    missing: bool,
}

/// Teams and positions Planning Center lists, cached in `directory_items` so a saved name shows with the module off.
pub struct DirectoryStore {
    pool: SqlitePool,
    host: Host,
    items: Mutex<Vec<Row_>>,
}

impl DirectoryStore {
    pub fn new(pool: SqlitePool, host: Host) -> Arc<Self> {
        Arc::new(Self { pool, host, items: Mutex::new(Vec::new()) })
    }

    /// Hear the followed plans change and read Planning Center's teams and positions into the table (off the announcer's thread).
    pub fn listen(self: &Arc<Self>) {
        let me = Arc::downgrade(self);
        self.host.listen(
            "core",
            PLAN_CHANGED,
            Arc::new(move |_| {
                let Some(me) = me.upgrade() else { return };
                if tokio::runtime::Handle::try_current().is_ok() {
                    tokio::spawn(async move {
                        if let Err(e) = me.sync(PCO).await {
                            tracing::warn!("directory: could not update from Planning Center: {e}");
                        }
                    });
                }
            }),
        );
    }

    async fn reload(&self) -> Result<(), sqlx::Error> {
        let rows = sqlx::query("select ref, kind, parent, name, missing_at from directory_items where provider = ? order by lower(name), ref")
            .bind(PCO)
            .fetch_all(&self.pool)
            .await?;
        let items = rows
            .iter()
            .map(|r| Row_ {
                reference: r.get("ref"),
                kind: r.get("kind"),
                parent: r.get::<Option<String>, _>("parent").unwrap_or_default(),
                name: r.get("name"),
                missing: r.get::<Option<String>, _>("missing_at").is_some(),
            })
            .collect();
        *self.items.lock().unwrap_or_else(PoisonError::into_inner) = items;
        Ok(())
    }

    /// Read the provider's teams and positions into `directory_items`; `true` when something was added, renamed or went missing.
    /// Nothing happens while the provider is off or has listed nothing yet, so what was cached stays (`directory.sync`).
    pub async fn sync(&self, provider: &str) -> Result<bool, sqlx::Error> {
        let Some(plan) = self.host.using::<PlanCap>(PLAN) else { return Ok(false) };
        let catalog = (plan.teams)();
        if catalog.is_empty() || provider != PCO {
            return Ok(false);
        }
        // ref -> (kind, parent, name)
        let mut seen: BTreeMap<String, (&str, String, String)> = BTreeMap::new();
        for t in &catalog {
            let (id, name) = (text(t, "id"), text(t, "name"));
            if id.is_empty() || name.is_empty() {
                continue;
            }
            let tref = team_ref(&id);
            seen.insert(tref.clone(), ("team", String::new(), name));
            for p in t["positions"].as_array().into_iter().flatten() {
                let (pid, pname) = (text(p, "id"), text(p, "name"));
                if !pid.is_empty() && !pname.is_empty() {
                    seen.insert(position_ref(&id, &pid), ("position", tref.clone(), pname));
                }
            }
        }
        let old: BTreeMap<String, (String, bool)> = sqlx::query("select ref, name, missing_at from directory_items where provider = ?")
            .bind(provider)
            .fetch_all(&self.pool)
            .await?
            .iter()
            .map(|r| (r.get::<String, _>("ref"), (r.get::<String, _>("name"), r.get::<Option<String>, _>("missing_at").is_some())))
            .collect();
        let now = relay_auth::now();
        let mut changed = false;
        let mut tx = self.pool.begin().await?;
        for (reference, (kind, parent, name)) in &seen {
            let parent_arg = (!parent.is_empty()).then_some(parent.as_str());
            match old.get(reference) {
                None => {
                    sqlx::query("insert into directory_items (ref, kind, provider, parent, name, seen_at) values (?, ?, ?, ?, ?, ?)")
                        .bind(reference).bind(*kind).bind(provider).bind(parent_arg).bind(name).bind(&now)
                        .execute(&mut *tx)
                        .await?;
                    changed = true;
                }
                Some((had, missing)) if had != name || *missing => {
                    sqlx::query("update directory_items set name = ?, parent = ?, seen_at = ?, missing_at = null where ref = ?")
                        .bind(name).bind(parent_arg).bind(&now).bind(reference)
                        .execute(&mut *tx)
                        .await?;
                    changed = true;
                }
                Some(_) => {}
            }
        }
        for (reference, (_, missing)) in &old {
            if !seen.contains_key(reference) && !missing {
                sqlx::query("update directory_items set missing_at = ? where ref = ?").bind(&now).bind(reference).execute(&mut *tx).await?;
                changed = true;
            }
        }
        tx.commit().await?;
        if changed {
            self.reload().await?;
            self.host.announce(DIRECTORY_CHANGED, provider);
        }
        Ok(changed)
    }

    fn rows(&self, kind: &str) -> Vec<Row_> {
        self.items.lock().unwrap_or_else(PoisonError::into_inner).iter().filter(|r| r.kind == kind).cloned().collect()
    }
}

fn text(v: &Value, key: &str) -> String {
    match &v[key] {
        Value::String(s) => s.clone(),
        Value::Number(n) => n.to_string(),
        _ => String::new(),
    }
}

impl Directory for DirectoryStore {
    fn positions(&self) -> Vec<DirItem> {
        let teams: BTreeMap<String, String> = self.rows("team").into_iter().map(|t| (t.reference, t.name)).collect();
        self.rows("position")
            .into_iter()
            .map(|p| DirItem { team: teams.get(&p.parent).cloned().unwrap_or_default(), reference: p.reference, name: p.name, parent: p.parent, missing: p.missing })
            .collect()
    }

    fn describe(&self, reference: &str, saved: &str) -> Described {
        if reference.is_empty() {
            return Described { name: saved.to_owned(), missing: false, unmatched: !saved.is_empty() };
        }
        let got = self.items.lock().unwrap_or_else(PoisonError::into_inner).iter().find(|r| r.reference == reference).cloned();
        Described { name: got.as_ref().map(|g| g.name.clone()).filter(|n| !n.is_empty()).unwrap_or_else(|| saved.to_owned()), missing: got.is_some_and(|g| g.missing), unmatched: false }
    }

    fn knows(&self, reference: &str) -> bool {
        self.items.lock().unwrap_or_else(PoisonError::into_inner).iter().any(|r| r.reference == reference)
    }

    fn refresh(&self) -> BoxFuture<'_, ()> {
        Box::pin(async move {
            // What Planning Center lists now, else what was cached (`directory.items`).
            if let Err(e) = self.sync(PCO).await {
                tracing::warn!("directory: sync failed: {e}");
            }
            if let Err(e) = self.reload().await {
                tracing::warn!("directory: could not read the cache: {e}");
            }
        })
    }
}

// -------------------------------------------------------------------------------------------------------------- channels
#[derive(Default)]
struct Held {
    generation: Option<u64>,
    /// Every channel (one per merged group) with its kind.
    all: Vec<(String, Channel)>,
    merges: HashMap<i64, Option<i64>>,
}

/// The audio channels core holds, read for a rundown. One listing is kept and read again when any channel has been written.
pub struct ChannelList {
    channels: Arc<Channels>,
    held: Mutex<Held>,
}

impl ChannelList {
    pub fn new(channels: Arc<Channels>) -> Arc<Self> {
        Arc::new(Self { channels, held: Mutex::new(Held::default()) })
    }
}

fn shape(c: &relay_audio::channels::Channel) -> Channel {
    Channel { reference: c.reference(), shown: c.shown.clone(), number: c.number.map(|n| n.to_string()), missing: c.missing, direction: c.direction.clone() }
}

impl ChannelCatalog for ChannelList {
    fn by_ref(&self, reference: &str) -> Option<Channel> {
        let mut id = relay_core::caps::channel_id(reference)?;
        let held = self.held.lock().unwrap_or_else(PoisonError::into_inner);
        // A merged-away ref still works: follow it to the channel it went into.
        let mut seen = HashSet::new();
        while let Some(Some(next)) = held.merges.get(&id) {
            if !seen.insert(id) {
                return None;
            }
            id = *next;
        }
        let want = relay_audio::channels::reference(id);
        held.all.iter().find(|(_, c)| c.reference == want).map(|(_, c)| c.clone())
    }

    fn audio(&self) -> Vec<Channel> {
        self.held.lock().unwrap_or_else(PoisonError::into_inner).all.iter().filter(|(kind, _)| kind == "audio").map(|(_, c)| c.clone()).collect()
    }

    fn refresh(&self) -> BoxFuture<'_, ()> {
        Box::pin(async move {
            let generation = self.channels.generation();
            if self.held.lock().unwrap_or_else(PoisonError::into_inner).generation == Some(generation) {
                return;
            }
            match (self.channels.listing("").await, self.channels.merges().await) {
                (Ok(all), Ok(merges)) => {
                    *self.held.lock().unwrap_or_else(PoisonError::into_inner) = Held { generation: Some(generation), all: all.iter().map(|c| (c.kind.clone(), shape(c))).collect(), merges };
                }
                (Err(e), _) | (_, Err(e)) => tracing::warn!("channels: could not list for the rundown: {e}"),
            }
        })
    }
}

// ------------------------------------------------------------------------------------------------ stream status and alert buttons
/// The shared stream status, as a module publishes into it (`relay_core::caps::StreamBoard`).
pub struct StreamBoardForModules(pub Arc<crate::status::StreamStatus>);

impl relay_core::caps::StreamBoard for StreamBoardForModules {
    fn publish(&self, source: &str, e: relay_core::caps::StreamEntry) -> Result<(), &'static str> {
        self.0.publish(source, crate::status::Publish { label: &e.label, on_air: e.on_air, health: &e.health, live_since: e.live_since, viewers: None, detail: &e.detail })
    }

    fn clear(&self, source: &str) {
        self.0.clear(source);
    }
}

/// The operator's alert buttons, as a module that raises one needs them.
pub struct AlertButtonsForModules(pub Arc<relay_show::alerts::Alerts>);

impl relay_core::caps::AlertButtons for AlertButtonsForModules {
    fn buttons(&self) -> Vec<(String, String)> {
        self.0.settings()["buttons"].as_array().into_iter().flatten().filter_map(|b| Some((b["id"].as_str()?.to_owned(), b["name"].as_str().unwrap_or("").to_owned()))).collect()
    }

    fn exists(&self, id: &str) -> bool {
        self.0.button(id).is_some()
    }

    fn is_on(&self, id: &str) -> bool {
        self.0.is_on(id)
    }

    fn press(&self, id: &str, sender: &str) -> bool {
        self.0.press(id, sender, None).is_ok()
    }

    fn cancel(&self, id: &str) -> bool {
        self.0.cancel(id).is_ok()
    }
}

/// This computer's network ports, for a module that must tell "this computer" from another one.
pub fn interfaces_for_modules() -> relay_core::caps::InterfacesCap {
    relay_core::caps::InterfacesCap(Box::new(|| {
        crate::net::netinfo::interfaces()
            .into_iter()
            .map(|p| relay_core::caps::LocalPort { addresses: p.addresses(), mac: p.mac.clone().unwrap_or_default(), virtual_: p.virtual_ })
            .collect()
    }))
}
