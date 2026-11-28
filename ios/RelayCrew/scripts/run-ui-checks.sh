#!/bin/bash
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT="$ROOT/RelayCrew.xcodeproj"
RESULTS="$ROOT/ui-test-results"
RUN_ID="$(date +%Y%m%d-%H%M%S)"
REPORT="$RESULTS/$RUN_ID"
RESULT_BUNDLE="$REPORT/RelayCrew.xcresult"
SIMULATOR_NAME="${SIMULATOR_NAME:-iPhone 18 Pro}"
SIMULATOR_ID="${SIMULATOR_ID:-$(xcrun simctl list -j devices available | python3 -c 'import json, sys; devices = json.load(sys.stdin)["devices"]; name = sys.argv[1]; print(next((device["udid"] for runtime in devices.values() for device in runtime if device["name"] == name), ""))' "$SIMULATOR_NAME")}"

mkdir -p "$REPORT"
echo "Running RelayCrew UI checks on $SIMULATOR_NAME"
echo "Reports: $REPORT"

if [[ -z "$SIMULATOR_ID" ]]; then
	echo "Simulator not found: $SIMULATOR_NAME" >&2
	exit 2
fi
xcrun simctl boot "$SIMULATOR_ID" 2>/dev/null || true
xcrun simctl bootstatus "$SIMULATOR_ID" -b

set -o pipefail
xcodebuild test \
	-project "$PROJECT" \
	-scheme RelayCrew-Dev \
	-destination "platform=iOS Simulator,id=$SIMULATOR_ID" \
	-parallel-testing-enabled NO \
	-resultBundlePath "$RESULT_BUNDLE" \
	"$@" 2>&1 | tee "$REPORT/xcodebuild.log"
TEST_STATUS=$?
set +o pipefail

if [[ -d "$RESULT_BUNDLE" ]]; then
	xcrun xcresulttool get test-results summary --path "$RESULT_BUNDLE" > "$REPORT/summary.json" || true
	xcrun xcresulttool get test-results tests --path "$RESULT_BUNDLE" > "$REPORT/tests.json" || true
	xcrun xcresulttool export attachments --path "$RESULT_BUNDLE" --output-path "$REPORT/attachments" || true
	xcrun xcresulttool export diagnostics --path "$RESULT_BUNDLE" --output-path "$REPORT/diagnostics" || true
fi

xcrun simctl spawn "$SIMULATOR_ID" log show --last 30m --style compact \
	--predicate 'process == "RelayCrew" OR subsystem BEGINSWITH "com.justinsmall.relay.crew.dev"' \
	> "$REPORT/simulator.log" 2>&1 || true

if [[ -d "$REPORT/diagnostics" ]]; then
	find "$REPORT/diagnostics" -type f -name 'StandardOutputAndStandardError*.txt' -print0 \
		| xargs -0 rg --no-filename '\[demo\]' > "$REPORT/app.log" || true
fi

if [[ -f "$REPORT/summary.json" ]]; then
	python3 - "$REPORT/summary.json" "$REPORT/tests.json" "$REPORT/app.log" "$REPORT/report.md" <<'PY'
import json
import re
import sys

with open(sys.argv[1], encoding="utf-8") as summary_file:
	summary = json.load(summary_file)
with open(sys.argv[2], encoding="utf-8") as tests_file:
	tests = json.load(tests_file)

failures = []
def visit(node):
	if isinstance(node, dict):
		if node.get("nodeType") == "Test Case" and node.get("result") == "Failed":
			messages = []
			def find_messages(child):
				if isinstance(child, dict):
					if child.get("nodeType") == "Failure Message":
						location = child.get("sourceLocation", {})
						line = location.get("lineNumber")
						message = child.get("name", "Test failed")
						if line:
							message += f" (line {line})"
						messages.append(message)
					for value in child.values():
						find_messages(value)
				elif isinstance(child, list):
					for value in child:
						find_messages(value)
			find_messages(node.get("children", []))
			failures.append((node.get("name", "Unknown test"), messages))
		for value in node.values():
			visit(value)
	elif isinstance(node, list):
		for value in node:
			visit(value)
visit(tests)

events = []
try:
	with open(sys.argv[3], encoding="utf-8") as app_log:
		for line in app_log:
			match = re.search(r"\[demo\]\s+(\{.*\})", line)
			if match:
				event = json.loads(match.group(1))
				context = event.get("context", {})
				events.append((event.get("timestamp", ""), event.get("level", "INFO"), event.get("message", ""), json.dumps(context, sort_keys=True)))
except FileNotFoundError:
	pass

total = summary.get("totalTestCount", 0)
passed = summary.get("passedTests", 0)
failed = summary.get("failedTests", 0)
lines = ["# RelayCrew UI check", "", f"Result: **{summary.get('result', 'Unknown')}** — {passed}/{total} tests passed, {failed} failed.", "", "## Failing checks"]
if failures:
	for name, messages in failures:
		lines.append(f"- **{name}**")
		for message in messages:
			lines.append(f"  - {message}")
else:
	lines.append("- None")
lines.extend(["", "## Demo app log"])
if events:
	lines.extend(f"- `{timestamp}` [{level}] {message} — `{context}`" for timestamp, level, message, context in events)
else:
	lines.append("- No demo events were captured.")
with open(sys.argv[4], "w", encoding="utf-8") as report_file:
	report_file.write("\n".join(lines) + "\n")

for key in ("title", "result", "totalTestCount", "passedTests", "failedTests", "skippedTests"):
	if key in summary:
		print(f"{key}: {summary[key]}")
print(f"Readable report: {sys.argv[4]}")
PY
fi

echo "Full Xcode output: $REPORT/xcodebuild.log"
echo "Simulator log: $REPORT/simulator.log"
echo "Demo action log: $REPORT/app.log"
echo "Readable report: $REPORT/report.md"
echo "Xcode diagnostics: $REPORT/diagnostics"
echo "Screenshots and test attachments: $REPORT/attachments"
echo "Result bundle: $RESULT_BUNDLE"
exit "$TEST_STATUS"
