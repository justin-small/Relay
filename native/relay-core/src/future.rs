//! The boxed future every trait object in core returns. It is its own module so that the modules that return one do not
//! have to use `module`, which uses them.

use std::future::Future;
use std::pin::Pin;

pub type BoxFuture<'a, T> = Pin<Box<dyn Future<Output = T> + Send + 'a>>;
