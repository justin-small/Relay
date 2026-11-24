use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;
use std::time::Duration;

use relay_module_ndi::listen::{parse_channels, Listeners, Stream, MAX_LISTENERS, QUEUE};
use relay_module_ndi::pool::{Changes, Pool};

#[test]
fn channels_are_numbers_from_one() {
    assert_eq!(parse_channels(""), Some(vec![]));
    assert_eq!(parse_channels("1,2"), Some(vec![0, 1]));
    assert_eq!(parse_channels(" 3 , 3 ,1"), Some(vec![2, 0]), "repeats are dropped, order kept");
    assert_eq!(parse_channels("0"), None);
    assert_eq!(parse_channels("65"), None);
    assert_eq!(parse_channels("1,,2"), None);
    assert_eq!(parse_channels("a"), None);
    assert_eq!(parse_channels("-1"), None);
    let many = (1..=40).map(|n| n.to_string()).collect::<Vec<_>>().join(",");
    assert_eq!(parse_channels(&many).map(|v| v.len()), Some(32), "at most 32 channels");
}

#[test]
fn only_four_people_listen_at_once() {
    let l = Listeners::default();
    for _ in 0..MAX_LISTENERS {
        assert!(l.take());
    }
    assert!(!l.take());
    l.give();
    assert!(l.take(), "a place frees when one leaves");
    for _ in 0..10 {
        l.give();
    }
    assert_eq!(l.count(), 0, "giving back never goes below none");
}

#[tokio::test(flavor = "multi_thread")]
async fn a_slow_browser_loses_the_oldest_sound_not_the_newest() {
    let opened = Arc::new(AtomicUsize::new(0));
    let o2 = Arc::clone(&opened);
    let pool = Pool::with_opener(Arc::new(Changes::default()), None, Arc::new(move |_, _| {
        o2.fetch_add(1, Ordering::SeqCst);
        Err("no such source".to_owned())
    }));
    let s = Stream::open(&pool, "MIC (Desk)", vec![]).expect("a stream");
    assert!(s.next(Duration::from_millis(50)).await.is_none(), "a source that sends nothing gives nothing");
    for i in 0..(QUEUE + 5) {
        s.push((48000, 2, vec![i as u8]));
    }
    let first = s.next(Duration::from_secs(1)).await.expect("a chunk");
    assert_eq!(first.2, vec![5u8], "the five oldest were dropped");
    assert_eq!((first.0, first.1), (48000, 2));
    let mut left = 0;
    while s.next(Duration::from_millis(20)).await.is_some() {
        left += 1;
    }
    assert_eq!(left, QUEUE - 1);
    s.close();
    assert!(pool.counts().is_empty(), "closing gives the receiver back");
}

#[tokio::test(flavor = "multi_thread")]
async fn a_waiting_stream_wakes_when_sound_arrives() {
    let pool = Pool::with_opener(Arc::new(Changes::default()), None, Arc::new(|_, _| Err("no".to_owned())));
    let s = Stream::open(&pool, "MIC", vec![]).expect("a stream");
    let waiting = {
        let s = Arc::clone(&s);
        tokio::spawn(async move { s.next(Duration::from_secs(3)).await })
    };
    tokio::time::sleep(Duration::from_millis(50)).await; // allow-sleep: lets the waiter start before the sound is pushed
    s.push((44100, 1, vec![1, 2]));
    assert_eq!(waiting.await.expect("task").map(|c| c.0), Some(44100));
    s.close();
}
