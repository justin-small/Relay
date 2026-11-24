//! The whole path on real NDI: a sender in this process, the pool's receiver, the viewer's JPEG. Skipped when the NDI runtime is not installed.

use std::sync::Arc;
use std::time::Duration;

use relay_module_ndi::pool::{Changes, Pool};
use relay_module_ndi::viewer::{Limits, Viewers};

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_sent_picture_comes_out_as_a_jpeg() {
    if relay_ndi::api().is_err() {
        eprintln!("skipped: the NDI runtime is not installed");
        return;
    }
    let name = format!("RELAYTEST{} (loop)", std::process::id());
    let sender = Arc::new(relay_ndi::send::Sender::open(&name, 320, 180, 15.0, "BGRA").expect("sender"));
    let stop = Arc::new(std::sync::atomic::AtomicBool::new(false));
    let feeder = {
        let (s, stop) = (Arc::clone(&sender), Arc::clone(&stop));
        std::thread::spawn(move || {
            let mut px = vec![0u8; 320 * 180 * 4];
            for (i, c) in px.chunks_mut(4).enumerate() {
                c.copy_from_slice(&[(i % 255) as u8, 80, 200, 255]);
            }
            while !stop.load(std::sync::atomic::Ordering::SeqCst) {
                let _ = s.send(&px, None);
            }
        })
    };
    let pool = Pool::new(Arc::new(Changes::default()), None);
    let limits = Limits::from_settings(&serde_json::json!({"view_fps": 30, "view_width": 160}));
    let viewers = Viewers::new(Arc::clone(&pool), Arc::new(move || limits));
    let found = tokio::task::spawn_blocking({
        let name = name.clone();
        move || relay_ndi::find::Finder::open(true).ok().and_then(|mut f| f.locate(&name, 8.0))
    })
    .await
    .expect("join");
    let Some(found) = found else {
        stop.store(true, std::sync::atomic::Ordering::SeqCst);
        let _ = feeder.join();
        panic!("the finder did not see the sender this test started");
    };
    let feed = viewers.open(&found.name).expect("feed");
    let got = feed.next(0, Duration::from_secs(8)).await;
    stop.store(true, std::sync::atomic::Ordering::SeqCst);
    viewers.close(&feed);
    let _ = feeder.join();
    let (_, jpeg) = got.expect("a JPEG within 8 seconds");
    assert_eq!(&jpeg[..2], &[0xFF, 0xD8], "a JPEG starts with its marker");
    assert!(pool.info(&found.name).is_none() || pool.info(&found.name).is_some_and(|i| i.width == 320 && i.height == 180));
}
