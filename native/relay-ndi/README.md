# relay-ndi: Relay's NDI engine in Rust (issue 606)

Everything of the NDI module (`app/modules/ndi/`) that talks to NDI or crunches what it carries. Python keeps the web
routes, settings, templates, discovery lookups (mDNS and BirdDog), the router and output bookkeeping, recording CSVs,
Pillow text and PNG reading, and the Playwright page renderer, which are not hot and are not NDI.

```bash
cd native/relay-ndi
cargo test --workspace --exclude relay-ndi-py
cargo clippy --locked --all-targets -- -D warnings
python ../../tools/build_bus_wheel.py     # builds and installs relay_bus and relay_ndi
```

| Module | What it does | Replaces |
| --- | --- | --- |
| `ffi.rs` | The slice of the NDI 6 C API Relay uses, declared by hand and resolved with dlopen at run time | cyndilib |
| `find.rs` | The finder: sources through discovery servers, extra IPs, groups and mDNS | cyndilib's `Finder` |
| `recv.rs` | One receiver: a thread calls capture in a loop and queues pictures, sound and sender metadata | cyndilib's `Receiver`; fixes #566 |
| `send.rs`, `route.rs` | Senders (BGRA or UYVY, with float audio) and routed outputs | cyndilib's `Sender`, the ctypes router |
| `scopes.rs` | Waveform, RGB parade, vectorscope, histogram, readouts, alarms | `analysis.scopes` (numpy) |
| `audio.rs` | Levels, true peak, EBU R128 loudness, correlation, spectrum | `analysis.AudioMeter` (pure Python) |
| `picture.rs` | Pixel format to RGB, scaling, JPEG | `viewer.encode` (numpy and Pillow) |
| `patterns.rs` | Test patterns, motion, blending, tone | `testpatterns.py` (numpy) |

## The NDI runtime is never linked or shipped

NDI's licence lets an app ship its libraries only under an EULA carrying NDI's terms, so Relay does not. `ffi.rs` opens
the operator's own install at run time: `NDI_RUNTIME_DIR_V6`, then the folders Python names (`runtime.py` offers the copy
`cyndilib` carries, if that package happens to be installed), then `/usr/local/lib` and the NDI SDK folder on macOS, then the
system search path. That is why a build needs neither the SDK nor its headers. The structs in `ffi.rs` follow the SDK 6
headers; a change to NDI's ABI would need them checked.

## Proved against real NDI (2026-11-21, macOS, NDI 6 runtime)

A separate process sends 1080p30 UYVY with a -23 dBFS stereo tone through this crate's sender; another receives it.

| | Rust engine | cyndilib |
| --- | --- | --- |
| Frames a second received (30 sent) | 30.1 | 13.1 |
| Late frames, gaps over 150 ms (10 s) | 0, 0 | not measured |
| Picture scopes, per 1080p UYVY frame | 4.9 ms | 11.4 to 12.9 ms (numpy) |
| Audio meter, per 33 ms chunk | 0.07 to 0.1 ms | 0.8 to 1.0 ms |
| Viewer JPEG at 960 wide, per frame | 7.7 ms | 19.8 ms (numpy and Pillow) |
| Moving test pattern, per 1080p frame | 0.9 ms | 8.5 ms |

The scopes and the audio meter match the Python versions: every scope pixel, histogram bin, readout and alarm is identical
on UYVY, BGRA and RGBA frames at 640x360 to 1920x1080, and the meters agree to the 0.1 dB they are rounded to. The test
patterns are bit for bit the same. Not proved: an HX or hardware sender, a busy network, Windows and Linux (CI builds and
tests there, but nobody has received a real stream on them).

`python/proof.py` repeats the loopback test.
