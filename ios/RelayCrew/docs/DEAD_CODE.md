# Dead-code scan (Periphery)

A weekly, **advisory** scan that reports unused Swift code with
[Periphery](https://github.com/peripheryapp/periphery). It builds + indexes the
project, so it runs on macOS in `hygiene.yml`; it **never gates** (dead code is
not a bug, and the detection is inherently fuzzy). Config lives in
[`.periphery.yml`](../.periphery.yml).

## Why advisory, never a gate

Unused-code detection cannot be fully precise: code reached only through the
Objective-C runtime, reflection (`NSClassFromString`), selectors, or KVC looks
unused to static analysis. A gate that fails on those would block real work, so
the scan only produces a report a human reviews — the same "fuzzy ⇒ weekly
advisory" reasoning as the unused-image scan (see [CI.md](CI.md)).

## False-positive rules (and why)

`.periphery.yml` is tuned against the patterns that commonly trip Periphery up
in a SwiftUI/iOS app. Each rule trades a little recall for far fewer false
alarms — the right bias for an advisory check.

| Pattern that looks "unused" | Rule | Why it's a false positive |
| --- | --- | --- |
| DTO properties decoded from JSON | `retain_codable_properties` | The synthesized `Codable` init assigns them; they're read from JSON, not from code. |
| `#Preview` / `PreviewProvider` | `retain_swift_ui_previews` | Tooling entry points, not dead code. |
| `@objc` members | `retain_objc_accessible` | Often called by the ObjC runtime — KVO, target/action, NotificationCenter selectors — invisibly to static analysis. |
| Delegate params unused in every conformance | `retain_unused_protocol_func_params` | Required by the protocol signature. |
| `AnyCancellable` / observers held for lifetime | `retain_assign_only_property_types` | Assigned for their side effect, never read. |
| Code used only by tests | `report_exclude: **/*Tests/**` | Tests are indexed (so they count as usage) but not reported on. |

These were verified empirically: the same patterns are flagged with no config
and silenced once each rule is on.

## What you must still handle by hand

Static analysis genuinely **cannot** resolve names built at runtime. For those,
annotate the declaration in code:

```swift
// periphery:ignore — instantiated via NSClassFromString("…")
final class FeatureFlagPlugin {}

func handle(event: Event, context: Context) { … } // periphery:ignore:parameters context
```

Unused *parameters* of free functions are also reported (there's no blanket
suppression) — usually a real finding; annotate if intentional.

## Tuning for your project

- After `make new-app`, update `project` / `schemes` in `.periphery.yml`.
- Add your own held types to `retain_assign_only_property_types`, and your own
  dynamic patterns as `// periphery:ignore` annotations.
- `retain_objc_accessible: true` errs toward silence; drop it if you want to
  surface genuinely-dead `@objc` members and accept some runtime-call noise.
- The scan reports *everything* each run (no baseline) — review the artifact and
  clean up in batches.
