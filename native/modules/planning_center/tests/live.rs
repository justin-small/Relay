//! A read-only run of the client against a real Planning Center account (#666). Ignored by default: it needs a token, so
//! `PCO_APP_ID=... PCO_SECRET=... cargo test -p relay-module-planning_center --test live -- --ignored --nocapture`.
//!
//! It only reads (service types, time zone, who the token is, the next plan, its items, team and catalog) and never sends a LIVE action, so it
//! cannot move a real plan. It prints counts, not names.

use relay_core::Net;
use relay_module_planning_center::client::{Client, Op, Services};
use serde_json::Value;

fn count(v: &Value) -> usize {
    v.as_array().map_or_else(|| usize::from(!v.is_null()), Vec::len)
}

#[tokio::test]
#[ignore = "needs PCO_APP_ID and PCO_SECRET for a real Planning Center account"]
async fn the_client_reads_a_real_account() {
    let (Ok(id), Ok(secret)) = (std::env::var("PCO_APP_ID"), std::env::var("PCO_SECRET")) else { panic!("set PCO_APP_ID and PCO_SECRET") };
    let client = Client::new(Net::unrouted(), &id, &secret);
    let types = client.call(Op::ServiceTypes).await.expect("service types");
    println!("service types: {}", count(&types));
    println!("time zone known: {}", client.call(Op::TimeZone).await.is_ok());
    println!("me: {}", client.call(Op::Me).await.is_ok());
    for st in types.as_array().into_iter().flatten() {
        let Some(st) = st["id"].as_str() else { continue };
        let plans = client.call(Op::PlansFrom { st: st.to_owned(), after: 0 }).await.expect("plans");
        let teams = client.call(Op::Teams { st: st.to_owned() }).await.expect("teams");
        println!("type {st}: plans {} teams {}", count(&plans), count(&teams));
        let Some(plan) = plans.as_array().and_then(|p| p.first()).and_then(|p| p["id"].as_str()).map(str::to_owned) else { continue };
        let (stn, pl) = (st.to_owned(), plan);
        for (name, op) in [
            ("plan", Op::Plan { st: stn.clone(), plan: pl.clone() }),
            ("live", Op::Live { st: stn.clone(), plan: pl.clone() }),
            ("items", Op::Items { st: stn.clone(), plan: pl.clone() }),
            ("team members", Op::TeamMembers { st: stn.clone(), plan: pl.clone() }),
            ("needed positions", Op::NeededPositions { st: stn.clone(), plan: pl.clone() }),
            ("plan files", Op::PlanAttachments { st: stn.clone(), plan: pl.clone() }),
        ] {
            match client.call(op).await {
                Ok(v) => println!("  {name}: ok ({})", count(&v)),
                Err(e) => println!("  {name}: FAILED {}", e.operator_message()),
            }
        }
        break;
    }
}
