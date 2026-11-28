//! What signage offers other modules while it is on (`signage.content`, #523): its media library and playlists, as far as a module that adds
//! files (Planning Center's plan files) needs them. Offered as [`relay_core::caps::ContentCap`], withdrawn when signage stops.

use std::sync::Arc;

use axum::body::Body;
use relay_core::caps::{ContentError, ContentResult, SignageContent};
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
}
