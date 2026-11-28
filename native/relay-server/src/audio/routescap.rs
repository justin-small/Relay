//! `relay-core`'s [`Routes`] on `relay-audio`'s channels and routing: what a module (the NDI router, #658) sees of core routes. A module's
//! [`RouteProvider`] is registered with the routing under its provider id, so a route asked for on one of its destinations reaches it.

use std::sync::Arc;

use relay_audio::channels::{End, Fut};
use relay_audio::routing::{Provider, ReadItem, Wanted};
use relay_audio::{Channels, Error, Routing};
use relay_core::future::BoxFuture;
use relay_core::routes::{ReadSource, RouteError, RouteProvider, RouteResult, Routes, WantedSource};
use serde_json::{json, Value};

pub struct CoreRoutes {
    pub routing: Arc<Routing>,
    pub channels: Arc<Channels>,
}

fn err(e: Error) -> RouteError {
    match e {
        Error::Refused(key) => RouteError::Refused(relay_audio::error::sentence(key)),
        Error::Unavailable(why) => {
            tracing::info!("routes: the database is not available ({why})");
            RouteError::Unavailable
        }
    }
}

fn problems(v: &Value) -> Vec<String> {
    v["problems"].as_array().into_iter().flatten().filter_map(|p| p.as_str().map(str::to_owned)).collect()
}

/// A module's provider seen as the routing's own.
struct Adapter(Arc<dyn RouteProvider>);

impl Provider for Adapter {
    fn exclusive(&self) -> bool {
        self.0.exclusive()
    }
    fn gain(&self) -> bool {
        self.0.gain()
    }
    fn permission(&self) -> Option<String> {
        self.0.permission()
    }
    fn apply<'a>(&'a self, end: &'a End, wanted: &'a [Wanted]) -> Fut<'a, String> {
        Box::pin(async move {
            let wanted: Vec<WantedSource> = wanted.iter().map(|w| WantedSource { ends: w.ends.iter().map(End::json).collect(), gain_db: w.gain_db }).collect();
            self.0.apply(&end.json(), &wanted).await
        })
    }
    fn read<'a>(&'a self, end: &'a End) -> Fut<'a, Option<Vec<ReadItem>>> {
        Box::pin(async move {
            let got: Option<Vec<ReadSource>> = self.0.read(&end.json()).await;
            got.map(|v| v.into_iter().map(|r| ReadItem { provider: r.provider, key: r.key, gain_db: r.gain_db }).collect())
        })
    }
}

impl Routes for CoreRoutes {
    fn ensure<'a>(&'a self, provider: &'a str, key: &'a str, kind: &'a str, direction: &'a str, gear_name: &'a str) -> BoxFuture<'a, RouteResult<Option<String>>> {
        Box::pin(async move { self.channels.ensure(provider, key, kind, direction, gear_name).await.map_err(err) })
    }

    fn channels(&self) -> BoxFuture<'_, RouteResult<Vec<Value>>> {
        Box::pin(async move { Ok(self.channels.listing("").await.map_err(err)?.iter().map(relay_audio::channels::Channel::json).collect()) })
    }

    fn views<'a>(&'a self, provider: &'a str) -> BoxFuture<'a, RouteResult<Vec<Value>>> {
        Box::pin(async move { self.routing.listing(provider, None).await.map_err(err) })
    }

    fn report<'a>(&'a self, provider: &'a str, reported: Vec<Value>) -> BoxFuture<'a, ()> {
        Box::pin(async move {
            let _ = self.channels.report(provider, &reported).await;
        })
    }

    fn set<'a>(&'a self, destination: &'a str, source: &'a str) -> BoxFuture<'a, RouteResult<Vec<String>>> {
        Box::pin(async move { Ok(problems(&self.routing.set(&json!(destination), &json!(source), &Value::Null, true).await.map_err(err)?)) })
    }

    fn clear<'a>(&'a self, destination: &'a str) -> BoxFuture<'a, RouteResult<Vec<String>>> {
        Box::pin(async move { Ok(problems(&self.routing.clear(&json!(destination), &Value::Null, true).await.map_err(err)?)) })
    }

    fn reapply<'a>(&'a self, provider: &'a str) -> BoxFuture<'a, RouteResult<Vec<String>>> {
        Box::pin(async move { self.routing.reapply(provider).await.map_err(err) })
    }

    fn salvos<'a>(&'a self, scope: &'a str) -> BoxFuture<'a, RouteResult<Vec<Value>>> {
        Box::pin(async move { self.routing.salvos(Some(scope)).await.map_err(err) })
    }

    fn save_salvo<'a>(&'a self, name: &'a str, destinations: Vec<String>, scope: &'a str, sid: Option<i64>) -> BoxFuture<'a, RouteResult<Value>> {
        Box::pin(async move {
            let dests: Vec<Value> = destinations.into_iter().map(Value::from).collect();
            self.routing.save_salvo(name, &dests, scope, sid).await.map_err(err)
        })
    }

    fn delete_salvo(&self, sid: i64) -> BoxFuture<'_, RouteResult<()>> {
        Box::pin(async move { self.routing.delete_salvo(sid).await.map_err(err) })
    }

    fn take_salvo(&self, sid: i64) -> BoxFuture<'_, RouteResult<Vec<String>>> {
        Box::pin(async move { self.routing.take_salvo(sid).await.map_err(err) })
    }

    fn forget<'a>(&'a self, channel: &'a str) -> BoxFuture<'a, RouteResult<()>> {
        Box::pin(async move {
            let Some(id) = relay_audio::channels::channel_id(&json!(channel)) else { return Err(RouteError::Refused(relay_audio::error::sentence("no_channel"))) };
            self.channels.forget(id).await.map_err(err)
        })
    }

    fn register(&self, provider: &str, api: Arc<dyn RouteProvider>) {
        self.routing.provide_native(provider, Arc::new(Adapter(api)));
    }

    fn withdraw(&self, provider: &str) {
        self.routing.withdraw_native(provider);
    }
}
