//! A capability is one thing a helper can do. Adding functionality later means adding one of these and registering it.

use std::collections::BTreeMap;
use std::future::Future;
use std::pin::Pin;
use std::sync::Arc;

use serde_json::Value;

use crate::protocol::CapInfo;

pub type CallFuture<'a> = Pin<Box<dyn Future<Output = Result<Value, String>> + Send + 'a>>;

pub trait Capability: Send + Sync {
    /// Short lowercase id Relay calls it by, e.g. `propresenter`.
    fn id(&self) -> &'static str;
    fn version(&self) -> u32;
    /// Every operation name `call` understands.
    fn ops(&self) -> &'static [&'static str];
    /// Run one operation. The error is a sentence Relay may show the operator; it must not carry secrets.
    fn call<'a>(&'a self, op: &'a str, args: Value) -> CallFuture<'a>;
}

#[derive(Default, Clone)]
pub struct Registry {
    caps: BTreeMap<&'static str, Arc<dyn Capability>>,
}

impl Registry {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn register(&mut self, cap: Arc<dyn Capability>) {
        self.caps.insert(cap.id(), cap);
    }

    pub fn infos(&self) -> Vec<CapInfo> {
        self.caps
            .values()
            .map(|c| CapInfo { id: c.id().to_string(), version: c.version(), ops: c.ops().iter().map(std::string::ToString::to_string).collect() })
            .collect()
    }

    pub async fn call(&self, cap: &str, op: &str, args: Value) -> Result<Value, String> {
        let Some(c) = self.caps.get(cap) else {
            return Err(format!("This helper has no '{cap}' capability."));
        };
        if !c.ops().contains(&op) {
            return Err(format!("The '{cap}' capability has no '{op}' operation."));
        }
        c.call(op, args).await
    }
}

impl std::fmt::Debug for Registry {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Registry").field("capabilities", &self.caps.keys().collect::<Vec<_>>()).finish()
    }
}
