# Third-party notices

## jpeg-encoder

`crates/relay-ndi/src/picture.rs` encodes the viewer's JPEG frames with the `jpeg-encoder` crate
(https://github.com/vstroebel/jpeg-encoder, `(MIT OR Apache-2.0) AND IJG`). Its IJG part asks that documentation of a
product that uses it says this:

    This software is based in part on the work of the Independent JPEG Group.

## The NDI runtime

Relay links nothing of NDI's. `crates/relay-ndi/src/ffi.rs` declares the few functions it calls from NDI's public C API and
opens the operator's own NDI runtime with dlopen. NDI is a trademark of Vizrt NDI AB.
