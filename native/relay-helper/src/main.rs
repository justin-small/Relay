use std::path::PathBuf;
use std::sync::Arc;

use relay_helper::{client, config, propresenter};

fn usage() -> ! {
    eprintln!(
        "Relay Helper {}\n\n  relay-helper pair <relay address> <pairing code> [name]   connect this computer to Relay once\n  \
         relay-helper port <ProPresenter network port>              tell the helper where ProPresenter's API listens\n  \
         relay-helper run                                           stay connected (leave this running)\n  \
         relay-helper status                                        show the settings\n\n\
         The address looks like ws://192.168.1.20:8080 (or wss://relay.example.org for a Relay with a certificate).\n\
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
            if let Some(name) = args.get(3) {
                cfg.name = name.clone();
            }
            match client::pair(cfg, &args[2]).await {
                Ok(paired) => paired.save(&path).map(|_| println!("Paired. Now run: relay-helper run")),
                Err(e) => Err(e),
            }
        }
        Some("port") if args.len() == 2 => match args[1].parse::<u16>() {
            Ok(p) if p > 0 => {
                let mut cfg = cfg;
                cfg.propresenter.api_port = p;
                cfg.save(&path).map(|_| println!("ProPresenter's API port is {p}."))
            }
            _ => Err("The port is a number from 1 to 65535.".to_string()),
        },
        Some("run") => {
            if cfg.token.is_empty() || cfg.relay_url.is_empty() {
                Err("Pair this computer first: relay-helper pair <relay address> <code>".to_string())
            } else {
                let registry = client::registry_for(&cfg, Arc::new(propresenter::SystemOpener));
                client::run(cfg, registry).await
            }
        }
        Some("status") => {
            println!("settings: {}\nrelay: {}\npaired: {}\nProPresenter API port: {}", path.display(), cfg.relay_url,
                     !cfg.token.is_empty(), cfg.propresenter.api_port);
            Ok(())
        }
        _ => usage(),
    };
    if let Err(e) = result {
        eprintln!("{e}");
        std::process::exit(1);
    }
}
