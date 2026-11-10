"""Try a Planning Center Personal Access Token against the real API.

Read-only: it lists the account's service types and prints the rate-limit
reading. It never prints the ID or the secret.

  1. Fill in planningcenter-test.env (git-ignored) at the repo root.
  2. .venv/bin/python tools/check_planningcenter.py
  3. .venv/bin/python tools/check_planningcenter.py --live
     also answers what the docs leave open about following a plan: the
     organisation's time zone as sent, whether plan_times come back linked to
     their plan, whether the service-type LIVE path works, what `length` looks
     like next to the plan's own total, and whether Webhooks offers a Services
     LIVE event. It prints shapes and numbers, not titles, names or notes.
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import planningcenter as pc  # noqa: E402

ENV = ROOT / "planningcenter-test.env"


def read_env() -> dict:
    out = {}
    if ENV.exists():
        for line in ENV.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip("'\"")
    return out


def main() -> int:
    env = read_env()
    app_id, secret = env.get("PCO_APP_ID", ""), env.get("PCO_SECRET", "")
    if not app_id or not secret:
        print(f"Fill in PCO_APP_ID and PCO_SECRET in {ENV.name} first.")
        return 2
    client = pc.Client(app_id, secret)
    print(f"User-Agent: {pc.user_agent()}   X-PCO-API-Version: {pc.API_VERSION}")
    try:
        raw = client.get("/services/v2/service_types", {"per_page": 1})
        types = client.service_types()
    except pc.PlanningCenterError as exc:
        print(f"FAILED ({exc.kind}): {exc}")
        return 1
    print("Connected.")
    print(f"Rate limit reading: {client.rate}")
    print(f"Response keys: {sorted(raw)}  links: {sorted(raw.get('links') or {})}  meta: {raw.get('meta')}")
    print(f"{len(types)} service type(s) (archived and deleted left out):")
    for t in types:
        print(f"  {t['id']:>10}  {t['name']}")
    if "--live" in sys.argv[1:]:
        try:
            live_checks(client, types)
        except pc.PlanningCenterError as exc:
            print(f"FAILED ({exc.kind}): {exc}")
            return 1
    return 0


def live_checks(client: pc.Client, types: list) -> None:
    name = client.time_zone()
    print(f"\nTime zone as sent: {name!r} -> {getattr(pc.zone_for(name), 'key', 'NOT RECOGNISED')}")
    after = datetime.now(timezone.utc) - timedelta(days=7)
    for t in types:
        base = f"/services/v2/service_types/{t['id']}/plans"
        raw = client.get(base, {"filter": "after", "after": after.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                "order": "sort_date", "include": "plan_times", "per_page": 5})
        rows = raw.get("data") or []
        print(f"\nService type {t['id']}: {len(rows)} plan(s) after {after:%Y-%m-%d}")
        if not rows:
            continue
        print(f"  plan relationships: {sorted((rows[0].get('relationships') or {}))}"
              f"  (plan_times linked: {'plan_times' in (rows[0].get('relationships') or {})})")
        print(f"  sort_date as sent: {rows[0]['attributes'].get('sort_date')!r}")
        plan = client.plans_from(t["id"], after)[0]
        print(f"  first plan {plan['id']}: times {[(x['type'], x['starts_at'] is not None) for x in plan['times']]}")
        items = client.items(t["id"], plan["id"])
        total = (client.get(f"{base}/{plan['id']}").get("data") or {}).get("attributes", {}).get("total_length")
        during = sum(i["length"] for i in items if i["position"] == "during")
        print(f"  item lengths: {[i['length'] for i in items][:12]}  sum of 'during' = {during}, "
              f"plan total_length = {total}  (compare with the minutes shown in Planning Center)")
        try:
            raw = client.get(f"{base}/{plan['id']}/live", {"include": "current_item_time,next_item_time,controller"})
            rel = (raw.get("data") or {}).get("relationships") or {}
            print(f"  LIVE on the service-type path: OK. relationships {sorted(rel)}; "
                  f"controller present: {bool((rel.get('controller') or {}).get('data'))}; "
                  f"included types {sorted({r.get('type') for r in raw.get('included') or []})}")
        except pc.PlanningCenterError as exc:
            print(f"  LIVE on the service-type path: {exc.kind} (HTTP {exc.status})")
        break
    try:
        events, offset = [], 0
        while True:
            page = client.get("/webhooks/v2/available_events", {"per_page": 100, "offset": offset}).get("data") or []
            events += [str((r.get("attributes") or {}).get("name") or r.get("id")) for r in page]
            if len(page) < 100:
                break
            offset += 100
        hits = [e for e in events if "services" in e.lower() and "live" in e.lower()]
        print(f"\nWebhooks: {len(events)} available event(s); Services LIVE events: {hits or 'none'}")
    except pc.PlanningCenterError as exc:
        print(f"\nWebhooks: could not list available events ({exc.kind}, HTTP {exc.status})")


if __name__ == "__main__":
    sys.exit(main())
