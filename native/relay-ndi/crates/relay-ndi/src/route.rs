//! A routed NDI output: a source on the network that shows whatever it is pointed at, switched inside the NDI
//! library with no decoding or re-encoding (issue 298).

use crate::ffi::{self, Api, Handle, RoutingCreate, Source};
use crate::find::cstr;
use std::ptr;
use std::sync::{Arc, Mutex};

pub struct Router {
    api: Arc<Api>,
    h: Mutex<Option<usize>>,
}

impl Router {
    pub fn open(name: &str) -> Result<Router, String> {
        let api = ffi::api()?;
        let n = cstr(name);
        let create = RoutingCreate { name: n.as_ptr(), groups: ptr::null() };
        // SAFETY: `create` and `n` outlive the call.
        let h = unsafe { (api.routing_create)(&create) };
        if h.is_null() {
            return Err("NDI would not create the output.".into());
        }
        Ok(Router { api, h: Mutex::new(Some(h as usize)) })
    }

    fn handle(&self) -> Option<Handle> {
        self.h.lock().unwrap_or_else(|p| p.into_inner()).map(|h| h as Handle)
    }

    pub fn change(&self, source: &str, address: &str) -> bool {
        let Some(h) = self.handle() else { return false };
        let (n, a) = (cstr(source), cstr(address));
        let src = Source { name: n.as_ptr(), url: if address.is_empty() { ptr::null() } else { a.as_ptr() } };
        // SAFETY: the strings outlive the call; the library copies them.
        unsafe { (self.api.routing_change)(h, &src) }
    }

    pub fn clear(&self) -> bool {
        // SAFETY: the handle is live while it is in `h`.
        self.handle().map(|h| unsafe { (self.api.routing_clear)(h) }).unwrap_or(false)
    }

    pub fn close(&self) {
        if let Some(h) = self.h.lock().unwrap_or_else(|p| p.into_inner()).take() {
            // SAFETY: taken out of `h`, so destroyed once.
            unsafe { (self.api.routing_destroy)(h as Handle) }
        }
    }
}

impl Drop for Router {
    fn drop(&mut self) {
        self.close();
    }
}
