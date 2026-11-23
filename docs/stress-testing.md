# Stress-testing Relay

`tools/stress.py` loads a throwaway Relay and shows where it slows down. Nothing real is touched: each scenario starts
`run.py` on ports 18080 and 18443 with its own config and database, in rehearsal mode (`RELAY_DEMO=1`, canned captions, no
OpenAI), signs up an Owner, and stops it afterwards.

```bash
.venv/bin/python tools/stress.py all --quick          # 3 to 4 minutes: check everything runs
.venv/bin/python tools/stress.py http --steps 10,50,200 --seconds 30
.venv/bin/python tools/stress.py sse --viewers 50,200,500 --seconds 30
.venv/bin/python tools/stress.py alerts --listeners 200 --rate 30
.venv/bin/python tools/stress.py modules --cycles 20 --viewers 50
.venv/bin/python tools/stress.py bus --seconds 1800 --scenarios BEG   # a 30 minute soak per scenario
.venv/bin/python tools/stress.py http --pyspy         # also a flame graph (pip install py-spy; macOS needs sudo)
```

| Scenario | Load | What it shows |
|---|---|---|
| `http` | Viewer pages, static files and `/api/admin` reads at rising concurrency | Slow or failing routes (p50/p95/p99 per route) |
| `sse` | Caption viewers held open on `/stream` | How many viewers one process carries; silences and drops |
| `alerts` | Stage messages sent at a rate to many `/alert` pages | Send-to-screen latency, lost messages |
| `modules` | Every module switched on and off, with viewers connected | Switches that block the event loop, leaked threads |
| `bus` | `native/relay-bus/bench/bench.py` run long, memory sampled over time | Audio dropouts under load, memory slope (the 149 MB per run question) |

Every scenario but `bus` also runs a **canary**: `/api/targets` asked ten times a second. If its latency jumps while a
load runs, the event loop was blocked by something that load reached; the p99 and max next to each step are that lag. The
server's memory, CPU, threads and open files are sampled every two seconds, so growth shows as MB per minute and a
thread or file count that does not come back.

Results are written to `stress-results/` (ignored by git) as JSON. If the server logged an error, its log is kept and the
path printed.

## Reading the numbers

- Canary p99 over about 100 ms under moderate load means something is running on the event loop that should be in a thread.
  Find it with `--pyspy`.
- `rss` growing in the `sse` and `modules` runs after the load stops means a per-connection or per-switch leak.
- Threads or open files that rise on every module cycle are listeners that do not close.
- The load generator shares the machine with the server, so absolute requests per second are a floor, not a limit. Compare
  runs on the same machine before and after a change.

## Not covered

The remote intercom (it needs a Cloudflare tunnel) and anything that needs real hardware. The bus scenarios use the
benchmark's synthetic devices; real interfaces are a separate check.
