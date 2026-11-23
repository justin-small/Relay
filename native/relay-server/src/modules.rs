//! The modules compiled into the server (#652). This file, and `Cargo.toml`'s dependencies, are the only places a module
//! crate is named: core never names one. Add a module by adding its crate to `Cargo.toml` and one line here.
//! `tests/test_rust_modules.py` checks the two lists agree and that no other crate depends on a module.

use std::sync::Arc;

use relay_core::{AppError, Module};

/// Every module, in the order they start (they stop in reverse).
pub fn all() -> Vec<Arc<dyn Module>> {
    Vec::new()
}

/// Registered into `registry` by `main`; returns the first problem a module's manifest has.
pub fn register_all(registry: &mut relay_core::Registry) -> Result<(), AppError> {
    for module in all() {
        registry.register(module)?;
    }
    Ok(())
}
