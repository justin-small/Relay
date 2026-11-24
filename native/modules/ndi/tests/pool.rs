use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;
use std::time::Duration;

use relay_module_ndi::pool::{Changes, Pool};
use relay_module_ndi::viewer::{part, Limits, Viewers, BOUNDARY};

fn failing_pool(opened: Arc<AtomicUsize>) -> (Arc<Pool>, Arc<Changes>) {
    let changes = Arc::new(Changes::default());
    let pool = Pool::with_opener(Arc::clone(&changes), None, Arc::new(move |_, _| {
        opened.fetch_add(1, Ordering::SeqCst);
        Err("no such source".to_owned())
    }));
    (pool, changes)
}

fn wait_for(what: &str, mut f: impl FnMut() -> bool) {
    let end = std::time::Instant::now() + Duration::from_secs(5);
    while !f() {
        assert!(std::time::Instant::now() < end, "timed out waiting for {what}");
        std::thread::sleep(Duration::from_millis(10)); // allow-sleep: polls a flag a thread sets
    }
}

#[test]
fn one_receiver_however_many_listeners() {
    let opened = Arc::new(AtomicUsize::new(0));
    let (pool, changes) = failing_pool(Arc::clone(&opened));
    let before = changes.version();
    let a = pool.acquire("CAM (1)", false, Arc::new(|_| {}), None).expect("lease");
    let b = pool.acquire("CAM (1)", false, Arc::new(|_| {}), None).expect("lease");
    assert_eq!(pool.counts().get("CAM (1)"), Some(&2));
    wait_for("the receiver to be tried once", || opened.load(Ordering::SeqCst) == 1);
    assert_eq!(opened.load(Ordering::SeqCst), 1, "a second listener shares the receiver");
    assert!(changes.version() > before, "acquiring is a change the page hears");
    assert_eq!(pool.error("CAM (1)"), "Could not receive that source.");
    a.release();
    a.release();
    assert_eq!(pool.counts().get("CAM (1)"), Some(&1), "releasing twice lets go once");
    drop(b);
    assert!(pool.counts().is_empty(), "the last listener closes the entry");
}

#[test]
fn the_low_bandwidth_receiver_is_its_own_entry_and_not_counted() {
    let opened = Arc::new(AtomicUsize::new(0));
    let (pool, _) = failing_pool(Arc::clone(&opened));
    let _hi = pool.acquire("S", false, Arc::new(|_| {}), None).expect("lease");
    let _lo = pool.acquire("S", true, Arc::new(|_| {}), None).expect("lease");
    wait_for("both receivers to be tried", || opened.load(Ordering::SeqCst) == 2);
    assert_eq!(pool.counts().get("S"), Some(&1));
    assert!(pool.acquire("", false, Arc::new(|_| {}), None).is_none(), "no source, no lease");
}

#[test]
fn close_all_lets_go_of_everything() {
    let (pool, _) = failing_pool(Arc::new(AtomicUsize::new(0)));
    let lease = pool.acquire("S", false, Arc::new(|_| {}), None).expect("lease");
    pool.close_all();
    assert!(pool.counts().is_empty());
    lease.release();
}

fn viewers(max: i64) -> (Viewers, Arc<Pool>) {
    let (pool, _) = failing_pool(Arc::new(AtomicUsize::new(0)));
    let limits = Limits::from_settings(&serde_json::json!({"view_max": max}));
    (Viewers::new(Arc::clone(&pool), Arc::new(move || limits)), pool)
}

#[test]
fn a_feed_is_shared_and_view_max_is_kept() {
    let (v, pool) = viewers(2);
    let a1 = v.open("A").expect("feed");
    let a2 = v.open("A").expect("same feed");
    assert!(Arc::ptr_eq(&a1, &a2));
    assert_eq!(a1.viewers(), 2);
    let _b = v.open("B").expect("second feed");
    assert!(v.open("C").is_none(), "view_max sources are already being watched");
    v.close(&a1);
    assert_eq!(pool.counts().get("A"), Some(&1), "one viewer is still watching");
    v.close(&a2);
    assert!(!pool.counts().contains_key("A"), "the last viewer gives the receiver back");
    assert!(v.open("C").is_some(), "a place is free again");
    v.close_all();
    assert!(pool.counts().is_empty());
}

#[tokio::test(flavor = "multi_thread")]
async fn a_browser_gets_frames_newer_than_the_one_it_has() {
    let (v, _) = viewers(4);
    let feed = v.open("A").expect("feed");
    assert!(feed.next(0, Duration::from_millis(50)).await.is_none(), "nothing arrives from a source that sends nothing");
    feed.publish(vec![1, 2, 3]);
    let (seq, jpeg) = feed.next(0, Duration::from_secs(1)).await.expect("a frame");
    assert_eq!((seq, jpeg.as_ref()), (1, [1u8, 2, 3].as_slice()));
    let waiting = { let f = Arc::clone(&feed); tokio::spawn(async move { f.next(seq, Duration::from_secs(2)).await }) };
    tokio::time::sleep(Duration::from_millis(50)).await; // allow-sleep: lets the waiter start before the frame is published
    feed.publish(vec![9]);
    assert_eq!(waiting.await.expect("task").map(|(s, b)| (s, b.to_vec())), Some((2, vec![9])));
    assert!(feed.next(2, Duration::from_millis(50)).await.is_none(), "the same frame is not sent twice");
}

#[test]
fn a_part_is_framed_like_the_python_stream() {
    let p = part(&[0xFF, 0xD8, 0xFF, 0xD9]);
    let text = String::from_utf8_lossy(&p).into_owned();
    assert!(text.starts_with(&format!("--{BOUNDARY}\r\nContent-Type: image/jpeg\r\nContent-Length: 4\r\n\r\n")));
    assert!(p.ends_with(&[0xFF, 0xD9, b'\r', b'\n']));
}
