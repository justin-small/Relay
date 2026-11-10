"""Try a Planning Center Personal Access Token against the real API.

Read-only: it lists the account's service types and prints the rate-limit
reading. It never prints the ID or the secret.

  1. Fill in planningcenter-test.env (git-ignored) at the repo root.
  2. .venv/bin/python tools/check_planningcenter.py
"""
import sys
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
