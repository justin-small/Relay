//! What signage offers other modules while it is on (`signage.content`, #523): its media library and playlists, as far as a module that adds
//! files (Planning Center's plan files) needs them. Offered as [`relay_core::caps::ContentCap`], withdrawn when signage stops.

use std::sync::Arc;

use axum::body::Body;
use relay_core::caps::{ContentError, ContentResult, SignageContent, SignageControl};
use relay_core::future::BoxFuture;
use serde_json::{json, Value};

use crate::media::{IMAGE_LIMIT, VIDEO_LIMIT};
use crate::Service;

pub struct Content(pub Arc<Service>);

fn refused(e: impl std::fmt::Debug) -> ContentError {
    ContentError(format!("{e:?}"))
}

impl SignageContent for Content {
    fn limit(&self, image: bool) -> u64 {
        if image { IMAGE_LIMIT } else { VIDEO_LIMIT }
    }

    fn media_list(&self) -> BoxFuture<'_, ContentResult<Vec<Value>>> {
        Box::pin(async move { self.0.store.media_list().await.map_err(refused) })
    }

    fn receive(&self, body: Body) -> BoxFuture<'_, ContentResult<Option<(String, String, u64)>>> {
        Box::pin(async move {
            match self.0.store.receive_media(body).await {
                Ok(kept) => Ok(Some(kept)),
                // A file signage does not play, or one that is too large: the caller skips it.
                Err(r) if matches!(r.status, 413 | 415) => Ok(None),
                Err(r) => Err(refused(r)),
            }
        })
    }

    fn media_add<'a>(&'a self, name: &'a str, sha: &'a str, ext: &'a str, size: u64) -> BoxFuture<'a, ContentResult<Value>> {
        Box::pin(async move { self.0.store.media_add(name, sha, ext, i64::try_from(size).unwrap_or(0), 0, 0, 0.0).await.map_err(refused) })
    }

    fn media_delete<'a>(&'a self, id: &'a str) -> BoxFuture<'a, ContentResult<()>> {
        Box::pin(async move { self.0.store.media_delete(id).await.map_err(refused) })
    }

    fn playlists(&self) -> BoxFuture<'_, ContentResult<Vec<Value>>> {
        Box::pin(async move { self.0.store.playlists().await.map_err(refused) })
    }

    fn playlist_create<'a>(&'a self, name: &'a str) -> BoxFuture<'a, ContentResult<Value>> {
        Box::pin(async move { self.0.store.playlist_create(&json!(name)).await.map_err(refused) })
    }

    fn playlist_set_items<'a>(&'a self, id: i64, items: &'a Value) -> BoxFuture<'a, ContentResult<()>> {
        Box::pin(async move { self.0.store.playlist_set_items(id, items).await.map_err(refused) })
    }

    fn media_thumb<'a>(&'a self, id: &'a str, data: &'a [u8]) -> BoxFuture<'a, ContentResult<()>> {
        Box::pin(async move { self.0.store.media_thumb(id, data).await.map_err(refused) })
    }
}

/// What a module that drives screens (slide-note tags) may do: pick and push, never edit.
pub struct Control(pub Arc<Service>);

/// A sentence signage wrote for the operator, or a plain one for a failure the operator cannot act on.
fn sentence(e: crate::store::Error) -> ContentError {
    match e {
        crate::store::Error::Invalid(text, _) => ContentError(text),
        crate::store::Error::Unavailable => ContentError("Signage could not be reached right now.".into()),
    }
}

impl SignageControl for Control {
    fn screens(&self) -> BoxFuture<'_, ContentResult<Vec<Value>>> {
        Box::pin(async move { self.0.store.screens().await.map_err(sentence) })
    }

    fn groups(&self) -> BoxFuture<'_, ContentResult<Vec<Value>>> {
        Box::pin(async move { self.0.store.groups().await.map_err(sentence) })
    }

    fn playlists(&self) -> BoxFuture<'_, ContentResult<Vec<Value>>> {
        Box::pin(async move { self.0.store.playlists().await.map_err(sentence) })
    }

    fn media_list(&self) -> BoxFuture<'_, ContentResult<Vec<Value>>> {
        Box::pin(async move { self.0.store.media_list().await.map_err(sentence) })
    }

    fn push_set<'a>(&'a self, target: &'a str, target_id: i64, media_id: &'a str) -> BoxFuture<'a, ContentResult<()>> {
        Box::pin(async move { self.0.store.push_set(target, target_id, media_id, "once", 0).await.map(|_| ()).map_err(sentence) })
    }

    fn live_view_set<'a>(&'a self, target: &'a str, target_id: i64, view: &'a str) -> BoxFuture<'a, ContentResult<()>> {
        Box::pin(async move { self.0.store.live_view_set(target, target_id, view).await.map(|_| ()).map_err(sentence) })
    }

    fn takeover_clear<'a>(&'a self, target: &'a str, target_id: i64) -> BoxFuture<'a, ContentResult<bool>> {
        Box::pin(async move {
            match self.0.store.takeover_clear(target, target_id).await {
                Ok(()) => Ok(true),
                Err(crate::store::Error::Invalid(_, 404)) => Ok(false),
                Err(e) => Err(sentence(e)),
            }
        })
    }

    fn takeover_clear_all(&self) -> BoxFuture<'_, ContentResult<u64>> {
        Box::pin(async move { self.0.store.takeover_clear_all().await.map_err(sentence) })
    }

    fn bump(&self) {
        self.0.store.live.bump();
    }
}
