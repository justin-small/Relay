use std::path::PathBuf;
use std::sync::Arc;

use relay_helper::{autostart, client, config, propresenter, tls};

fn usage() -> ! {
    eprintln!(
        "Relay Helper {}\n\n  relay-helper pair <relay address> <pairing code> [name] [--pin <fingerprint>]   connect this computer to Relay once\n  \
         relay-helper port <ProPresenter network port>              tell the helper where ProPresenter's API listens\n  \
         relay-helper run                                           stay connected (leave this running)\n  \
         relay-helper status                                        show the settings\n  \
         relay-helper install                                       start at login (macOS and Windows), and start now\n  \
         relay-helper uninstall                                     stop starting at login\n\n\
         The address looks like ws://192.168.1.20:8080 (or wss://192.168.1.20 for Relay's own HTTPS: add --pin with the\ncertificate fingerprint Relay's admin page shows, so only that certificate is trusted).\n\
         Settings: {}",
        client::VERSION,
        config::default_path().display()
    );
    std::process::exit(2)
}

#[tokio::main]
async fn main() {
    let path: PathBuf = std::env::var_os("RELAY_HELPER_CONFIG").map(PathBuf::from).unwrap_or_else(config::default_path);
    let args: Vec<String> = std::env::args().skip(1).collect();
    let cfg = match config::Config::load(&path) {
        Ok(c) => c,
        Err(e) => {
            eprintln!("{e}");
            std::process::exit(1)
        }
    };
    let result = match args.first().map(String::as_str) {
        Some("pair") if args.len() >= 3 => {
            let mut cfg = cfg;
            cfg.relay_url = args[1].clone();
            let mut rest = args[3..].iter();
            let mut pin: Result<Option<String>, String> = Ok(None);
            while let Some(a) = rest.next() {
                if a == "--pin" {
                    pin = rest.next().ok_or("--pin needs the certificate fingerprint.".to_string()).and_then(|v| tls::clean_pin(v)).map(Some);
                } else {
                    cfg.name = a.clone();
                }
            }
            match pin {
                Err(e) => Err(e),
                Ok(pin) => {
                    // A new pairing starts from what the operator typed now, never a pin left from the last Relay.
                    cfg.tls_pin = pin.unwrap_or_default();
                    match client::pair(cfg, &args[2]).await {
                        Ok(paired) => paired.save(&path).map(|_| println!("Paired. Now run: relay-helper run (or relay-helper install to start at login)")),
                        Err(e) => Err(e),
                    }
                }
            }
        }
        Some("install") => autostart::install(std::env::var_os("RELAY_HELPER_CONFIG").map(PathBuf::from).as_deref()).map(|m| println!("{m}")),
        Some("uninstall") => autostart::uninstall().map(|m| println!("{m}")),
        Some("port") if args.len() == 2 => match args[1].parse::<u16>() {
            Ok(p) if p > 0 => {
                let mut cfg = cfg;
                cfg.propresenter.api_port = p;
                cfg.save(&path).map(|_| println!("ProPresenter's API port is {p}."))
            }
            _ => Err("The port is a number from 1 to 65535.".to_string()),
        },
        Some("run") => {
            let background = args.get(1).map(String::as_str) == Some("--background");
            if background {
                autostart::detach_console();
            }
            let mut cfg = cfg;
            if cfg.token.is_empty() || cfg.relay_url.is_empty() {
                if background {
                    // Started at login before it was paired: wait for `relay-helper pair`, which writes the settings.
                    println!("Not paired yet. Waiting: run relay-helper pair <relay address> <code>");
                    loop {
                        tokio::time::sleep(std::time::Duration::from_secs(5)).await;
                        if let Ok(c) = config::Config::load(&path) {
                            if !c.token.is_empty() && !c.relay_url.is_empty() {
                                cfg = c;
                                break;
                            }
                        }
                    }
                } else {
                    return finish(Err("Pair this computer first: relay-helper pair <relay address> <code>".to_string()));
                }
            }
            let registry = client::registry_for(&cfg, Arc::new(propresenter::SystemOpener));
            client::run(cfg, registry).await
        }
        Some("status") => {
            println!("settings: {}\nrelay: {}\ncertificate pin: {}\npaired: {}\nProPresenter API port: {}", path.display(), cfg.relay_url,
                     if cfg.tls_pin.is_empty() { "none" } else { &cfg.tls_pin }, !cfg.token.is_empty(), cfg.propresenter.api_port);
            Ok(())
        }
        _ => usage(),
    };
    finish(result);
}

fn finish(result: Result<(), String>) {
    if let Err(e) = result {
        eprintln!("{e}");
        std::process::exit(1);
    }
}
