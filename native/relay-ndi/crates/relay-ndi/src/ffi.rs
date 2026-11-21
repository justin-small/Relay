//! The slice of the NDI C API Relay uses, declared by hand from the NDI SDK 6 headers and resolved from the runtime
//! library at run time. The runtime is the user's own install (NDI Tools or the NDI Runtime); nothing of NDI's is
//! compiled in or shipped.

use libloading::Library;
use std::ffi::{c_char, c_int, c_void};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex, OnceLock};

pub const FRAME_NONE: c_int = 0;
pub const FRAME_VIDEO: c_int = 1;
pub const FRAME_AUDIO: c_int = 2;
pub const FRAME_METADATA: c_int = 3;

pub const fn fourcc(a: u8, b: u8, c: u8, d: u8) -> u32 {
    (a as u32) | ((b as u32) << 8) | ((c as u32) << 16) | ((d as u32) << 24)
}
pub const FOURCC_UYVY: u32 = fourcc(b'U', b'Y', b'V', b'Y');
pub const FOURCC_BGRA: u32 = fourcc(b'B', b'G', b'R', b'A');
pub const FOURCC_BGRX: u32 = fourcc(b'B', b'G', b'R', b'X');
pub const FOURCC_RGBA: u32 = fourcc(b'R', b'G', b'B', b'A');
pub const FOURCC_RGBX: u32 = fourcc(b'R', b'G', b'B', b'X');
pub const FOURCC_FLTP: u32 = fourcc(b'F', b'L', b'T', b'p');

/// UYVY (4:2:2) for pictures without alpha and BGRA for those with: the receiver converts anything else for us.
pub const COLOR_UYVY_BGRA: c_int = 1;
pub const BANDWIDTH_LOWEST: c_int = 0;
pub const BANDWIDTH_HIGHEST: c_int = 100;
pub const FORMAT_PROGRESSIVE: c_int = 1;
pub const TIMECODE_SYNTHESIZE: i64 = i64::MAX;

#[repr(C)]
#[derive(Clone, Copy)]
pub struct Source {
    pub name: *const c_char,
    pub url: *const c_char,
}
#[repr(C)]
pub struct FindCreate {
    pub show_local_sources: bool,
    pub groups: *const c_char,
    pub extra_ips: *const c_char,
}
#[repr(C)]
pub struct RecvCreate {
    pub source: Source,
    pub color_format: c_int,
    pub bandwidth: c_int,
    pub allow_video_fields: bool,
    pub name: *const c_char,
}
#[repr(C)]
pub struct VideoFrame {
    pub xres: c_int,
    pub yres: c_int,
    pub fourcc: u32,
    pub frame_rate_n: c_int,
    pub frame_rate_d: c_int,
    pub aspect: f32,
    pub format: c_int,
    pub timecode: i64,
    pub data: *mut u8,
    pub stride: c_int,
    pub metadata: *const c_char,
    pub timestamp: i64,
}
#[repr(C)]
pub struct AudioFrame {
    pub sample_rate: c_int,
    pub channels: c_int,
    pub samples: c_int,
    pub timecode: i64,
    pub fourcc: u32,
    pub data: *mut u8,
    pub channel_stride: c_int,
    pub metadata: *const c_char,
    pub timestamp: i64,
}
#[repr(C)]
pub struct MetadataFrame {
    pub length: c_int,
    pub timecode: i64,
    pub data: *mut c_char,
}
#[repr(C)]
#[derive(Default, Clone, Copy)]
pub struct Performance {
    pub video: i64,
    pub audio: i64,
    pub metadata: i64,
}
#[repr(C)]
pub struct SendCreate {
    pub name: *const c_char,
    pub groups: *const c_char,
    pub clock_video: bool,
    pub clock_audio: bool,
}
#[repr(C)]
pub struct RoutingCreate {
    pub name: *const c_char,
    pub groups: *const c_char,
}

pub type Handle = *mut c_void;

macro_rules! api {
    ($($field:ident : $name:literal = fn($($arg:ty),*) $(-> $ret:ty)?;)*) => {
        /// The resolved functions. The library handle lives as long as they do.
        pub struct Api {
            _lib: Library,
            pub path: PathBuf,
            $(pub $field: unsafe extern "C" fn($($arg),*) $(-> $ret)?,)*
        }
        impl Api {
            fn open(path: &Path) -> Result<Api, String> {
                // SAFETY: loading the NDI runtime runs its initialisers, which is what the user installed it for.
                let lib = unsafe { Library::new(path) }.map_err(|e| format!("{}: {e}", path.display()))?;
                $(
                    // SAFETY: the declared signature is NDI SDK 6's for this name.
                    let $field = *unsafe { lib.get::<unsafe extern "C" fn($($arg),*) $(-> $ret)?>(concat!($name, "\0").as_bytes()) }
                        .map_err(|e| format!("{}: {} is missing ({e})", path.display(), $name))?;
                )*
                Ok(Api { _lib: lib, path: path.to_path_buf(), $($field),* })
            }
        }
    };
}

api! {
    initialize: "NDIlib_initialize" = fn() -> bool;
    find_create: "NDIlib_find_create_v2" = fn(*const FindCreate) -> Handle;
    find_destroy: "NDIlib_find_destroy" = fn(Handle);
    find_wait: "NDIlib_find_wait_for_sources" = fn(Handle, u32) -> bool;
    find_sources: "NDIlib_find_get_current_sources" = fn(Handle, *mut u32) -> *const Source;
    recv_create: "NDIlib_recv_create_v3" = fn(*const RecvCreate) -> Handle;
    recv_destroy: "NDIlib_recv_destroy" = fn(Handle);
    recv_capture: "NDIlib_recv_capture_v3" = fn(Handle, *mut VideoFrame, *mut AudioFrame, *mut MetadataFrame, u32) -> c_int;
    recv_free_video: "NDIlib_recv_free_video_v2" = fn(Handle, *const VideoFrame);
    recv_free_audio: "NDIlib_recv_free_audio_v3" = fn(Handle, *const AudioFrame);
    recv_free_metadata: "NDIlib_recv_free_metadata" = fn(Handle, *const MetadataFrame);
    recv_performance: "NDIlib_recv_get_performance" = fn(Handle, *mut Performance, *mut Performance);
    send_create: "NDIlib_send_create" = fn(*const SendCreate) -> Handle;
    send_destroy: "NDIlib_send_destroy" = fn(Handle);
    send_video: "NDIlib_send_send_video_v2" = fn(Handle, *const VideoFrame);
    send_audio: "NDIlib_send_send_audio_v3" = fn(Handle, *const AudioFrame);
    routing_create: "NDIlib_routing_create" = fn(*const RoutingCreate) -> Handle;
    routing_destroy: "NDIlib_routing_destroy" = fn(Handle);
    routing_change: "NDIlib_routing_change" = fn(Handle, *const Source) -> bool;
    routing_clear: "NDIlib_routing_clear" = fn(Handle) -> bool;
}

// SAFETY: the NDI library documents its functions as thread-safe for distinct instances, and the handles carry no
// thread affinity.
unsafe impl Send for Api {}
unsafe impl Sync for Api {}

/// The library's file name on this platform.
pub fn library_name() -> &'static str {
    if cfg!(target_os = "macos") {
        "libndi.dylib"
    } else if cfg!(windows) {
        "Processing.NDI.Lib.x64.dll"
    } else {
        "libndi.so.6"
    }
}

/// Where the runtime usually lives, after the folders the caller names and `NDI_RUNTIME_DIR_V6`.
pub fn candidates(extra_dirs: &[PathBuf]) -> Vec<PathBuf> {
    let name = library_name();
    let mut out: Vec<PathBuf> = Vec::new();
    if let Some(dir) = std::env::var_os("NDI_RUNTIME_DIR_V6") {
        out.push(Path::new(&dir).join(name));
    }
    out.extend(extra_dirs.iter().map(|d| d.join(name)));
    if cfg!(target_os = "macos") {
        out.push(PathBuf::from("/usr/local/lib").join(name));
        out.push(PathBuf::from("/Library/NDI SDK for Apple/lib/macOS").join(name));
    }
    // The system's own search path (Linux ldconfig, Windows PATH) last.
    out.push(PathBuf::from(name));
    out
}

static API: OnceLock<Result<Arc<Api>, String>> = OnceLock::new();
static ORDER: Mutex<Vec<PathBuf>> = Mutex::new(Vec::new());

/// Folders to look in before the usual places. Only the first `api()` call reads them.
pub fn set_search_dirs(dirs: Vec<PathBuf>) {
    *ORDER.lock().unwrap_or_else(|p| p.into_inner()) = dirs;
}

/// The NDI runtime, loaded and initialised once. The error says why not.
pub fn api() -> Result<Arc<Api>, String> {
    API.get_or_init(|| {
        let dirs = ORDER.lock().unwrap_or_else(|p| p.into_inner()).clone();
        let mut last = String::from("The NDI runtime was not found.");
        for path in candidates(&dirs) {
            if path.is_absolute() && !path.exists() {
                continue;
            }
            match Api::open(&path) {
                Ok(api) => {
                    // SAFETY: first call into the library, from one thread, after a successful load.
                    if !unsafe { (api.initialize)() } {
                        return Err("This computer cannot run NDI.".to_string());
                    }
                    return Ok(Arc::new(api));
                }
                Err(e) => last = e,
            }
        }
        Err(last)
    })
    .clone()
}

/// A C string the library owns, copied out.
///
/// # Safety
/// `p` must be null or point at a NUL-terminated string.
pub unsafe fn text(p: *const c_char) -> String {
    if p.is_null() {
        String::new()
    } else {
        std::ffi::CStr::from_ptr(p).to_string_lossy().into_owned()
    }
}
