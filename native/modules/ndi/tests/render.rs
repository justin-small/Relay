//! The Chromium renderer on a real browser (skipped when none is installed): an opaque page comes back as BGRA, a transparent one keeps its
//! alpha, and the frame rate is what an NDI output can count on.

use std::time::Instant;

use relay_module_ndi::render::{available, Renderer};

fn page(body_style: &str, inner: &str) -> String {
    let html = format!("<html><body style='margin:0;{body_style}'>{inner}</body></html>");
    let enc: String = html.bytes().map(|b| format!("%{b:02X}")).collect();
    format!("data:text/html,{enc}")
}

fn px(f: &relay_module_ndi::render::Bgra, x: usize, y: usize) -> [u8; 4] {
    let i = (y * f.width + x) * 4;
    [f.data[i], f.data[i + 1], f.data[i + 2], f.data[i + 3]]
}

#[tokio::test(flavor = "multi_thread")]
async fn an_opaque_page_is_a_bgra_frame_of_the_asked_size() {
    if !available().is_empty() {
        eprintln!("skipped: no Chrome or Chromium installed");
        return;
    }
    let r = Renderer::open(&page("background:rgb(255,0,0)", ""), 640, 360, false).await.expect("open");
    let f = r.frame().await.expect("frame");
    assert_eq!((f.width, f.height, f.data.len()), (640, 360, 640 * 360 * 4));
    let p = px(&f, 320, 180);
    assert!(p[2] > 230 && p[1] < 30 && p[0] < 30 && p[3] == 255, "red in BGRA order, opaque: {p:?}");
    r.close().await;
}

#[tokio::test(flavor = "multi_thread")]
async fn a_transparent_page_keeps_its_alpha() {
    if !available().is_empty() {
        eprintln!("skipped: no Chrome or Chromium installed");
        return;
    }
    let r = Renderer::open(&page("background:transparent", "<div style='width:100px;height:100px;background:rgb(0,200,0)'></div>"), 640, 360, true).await.expect("open");
    let f = r.frame().await.expect("frame");
    assert_eq!(px(&f, 600, 300)[3], 0, "the page background is clear");
    let g = px(&f, 50, 50);
    assert!(g[1] > 180 && g[2] < 40 && g[3] == 255, "the square is opaque green: {g:?}");
    r.close().await;
}

#[tokio::test(flavor = "multi_thread")]
async fn it_keeps_up_with_a_frame_rate() {
    if !available().is_empty() {
        eprintln!("skipped: no Chrome or Chromium installed");
        return;
    }
    let r = Renderer::open(&page("background:#123", "<div style='width:50px;height:50px;background:#fa0;animation:m 1s linear infinite'></div><style>@keyframes m{to{margin-left:500px}}</style>"), 1280, 720, false).await.expect("open");
    let (n, t0) = (60, Instant::now());
    for _ in 0..n {
        r.frame_within(std::time::Duration::from_millis(200)).await.expect("frame");
    }
    let fps = f64::from(n) / t0.elapsed().as_secs_f64();
    eprintln!("720p opaque, animated: {fps:.1} frames a second");
    assert!(fps > 20.0, "{fps} frames a second is too slow to send 30");
    r.close().await;
}

#[tokio::test(flavor = "multi_thread")]
async fn a_still_page_gives_its_last_picture_again_so_the_output_keeps_sending() {
    if !available().is_empty() {
        eprintln!("skipped: no Chrome or Chromium installed");
        return;
    }
    let r = Renderer::open(&page("background:rgb(0,0,255)", ""), 640, 360, false).await.expect("open");
    let first = r.frame_within(std::time::Duration::from_secs(2)).await.expect("frame");
    let again = r.frame_within(std::time::Duration::from_millis(100)).await.expect("the page did not change");
    assert_eq!((first.width, first.height), (again.width, again.height));
    assert_eq!(px(&again, 100, 100), px(&first, 100, 100));
    r.close().await;
}

#[tokio::test(flavor = "multi_thread")]
async fn a_missing_browser_says_so() {
    let e = Renderer::open_with(std::path::Path::new("/nonexistent/chrome"), "about:blank", 320, 180, false).await.err().expect("an error");
    assert_eq!(e, "Chromium would not start.");
}
