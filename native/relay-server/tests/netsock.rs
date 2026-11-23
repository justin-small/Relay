//! Routed outbound sockets for modules (#662): an unrouted class connects and sends as before, and a class routed to a port
//! that is not here answers with a fixed sentence (strict) or uses the default network (fallback).

use std::collections::BTreeMap;
use std::sync::Arc;
use std::time::Duration;

use relay_core::{Config, Net, UdpOptions};
use relay_server::netroute::Policy;
use relay_server::netsock::RoutedNet;
use serde_json::json;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpListener;

fn net(network: serde_json::Value, classes: &[(&str, Policy)]) -> (Net, std::path::PathBuf) {
    let dir = std::env::temp_dir().join(format!("relay-netsock-{}-{}", std::process::id(), classes.len() + network.to_string().len()));
    std::fs::create_dir_all(&dir).unwrap();
    let path = dir.join("config.json");
    std::fs::write(&path, json!({ "network": network }).to_string()).unwrap();
    let config = Arc::new(Config::load(&path).unwrap());
    let extra: BTreeMap<String, (String, Policy)> =
        classes.iter().map(|(id, p)| ((*id).to_owned(), ((*id).to_owned(), *p))).collect();
    (Net::new(Arc::new(RoutedNet::new(config, extra))), dir)
}

#[tokio::test]
async fn an_unrouted_class_connects_and_sends() {
    let (net, dir) = net(json!({}), &[("probe", Policy::Fallback)]);
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let port = listener.local_addr().unwrap().port();
    let server = tokio::spawn(async move {
        let (mut s, _) = listener.accept().await.unwrap();
        let mut buf = [0u8; 4];
        s.read_exact(&mut buf).await.unwrap();
        s.write_all(&buf).await.unwrap();
    });
    let mut s = net.connect_tcp("probe", "127.0.0.1", port, Duration::from_secs(4)).await.unwrap();
    s.write_all(b"ping").await.unwrap();
    let mut back = [0u8; 4];
    s.read_exact(&mut back).await.unwrap();
    assert_eq!(&back, b"ping");
    server.await.unwrap();

    let a = net.udp("probe", &UdpOptions { host: Some("127.0.0.1".parse().unwrap()), ..Default::default() }).unwrap();
    let b = net.udp("probe", &UdpOptions::default()).unwrap();
    b.send_to(b"hi", a.local_addr().unwrap()).await.unwrap();
    let mut buf = [0u8; 2];
    let (n, _) = tokio::time::timeout(Duration::from_secs(4), a.recv_from(&mut buf)).await.unwrap().unwrap();
    assert_eq!(&buf[..n], b"hi");
    let _ = std::fs::remove_dir_all(dir);
}

#[tokio::test]
async fn a_strict_class_routed_to_a_missing_port_says_so_in_a_fixed_sentence() {
    let network = json!({ "routes": { "probe": { "port": "gone", "key": "mac:aa:bb:cc:dd:ee:ff" } }, "policy": { "probe": "strict" } });
    let (net, dir) = net(network, &[("probe", Policy::Strict)]);
    let err = net.connect_tcp("probe", "127.0.0.1", 9, Duration::from_secs(1)).await.unwrap_err();
    assert!(err.message.contains("not connected to this computer"), "{}", err.message);
    let err = net.udp("probe", &UdpOptions::default()).unwrap_err();
    assert!(err.message.contains("not connected to this computer"), "{}", err.message);
    let _ = std::fs::remove_dir_all(dir);
}

#[tokio::test]
async fn a_fallback_class_routed_to_a_missing_port_uses_the_default_network() {
    let network = json!({ "routes": { "probe": "mac:aa:bb:cc:dd:ee:ff" } });
    let (net, dir) = net(network, &[("probe", Policy::Fallback)]);
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let port = listener.local_addr().unwrap().port();
    let accept = tokio::spawn(async move { listener.accept().await.is_ok() });
    net.connect_tcp("probe", "127.0.0.1", port, Duration::from_secs(4)).await.unwrap();
    assert!(accept.await.unwrap());
    let _ = std::fs::remove_dir_all(dir);
}
