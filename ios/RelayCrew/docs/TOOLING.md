# Tooling

This template ships a complete, version-pinned code-quality toolchain so every teammate and CI behave identically. Everything is driven through `make` and a single pinned-versions file.

## Overview

| Concern | Tool | Where it runs |
|---|---|---|
| **Tool versions** | [Mint](https://github.com/yonaskolb/Mint) (`Mintfile`) | everywhere — one pinned version |
| **Formatting** (style) | SwiftFormat | pre-commit (auto-fix), `make` |
| **Linting** (safety/logic) | SwiftLint | build phase (warnings), pre-commit (strict), CI |
| **Asset size guard** | `scripts/check_image_size.sh` | pre-commit |
| **Editor settings** | `.editorconfig` | Xcode 16+, VS Code, Cursor |
| **Compile health** | Swift frontend flags | Dev builds |

## Version pinning with Mint

`Mintfile` pins the exact tool versions:

```
realm/SwiftLint@0.63.3
nicklockwood/SwiftFormat@0.61.1
```

Because the versions are committed, everyone runs identical tools. Every consumer — pre-commit, the Xcode build phase, the `Makefile`, and CI — invokes the binaries through `mint run`, so there is no drift.

**Bumping a version (lead):** edit `Mintfile`, commit, push.
**After pulling a version bump (everyone):** run `mint bootstrap` once (it compiles the new tool — a few minutes — so it doesn't stall a commit later). The `make bootstrap` / `scripts/bootstrap.sh` step does this for you on first setup.

## SwiftFormat vs SwiftLint — a deliberate split

The two tools overlap on style rules, which causes conflicts (e.g. one adds a trailing comma, the other removes it). To avoid that, responsibilities are split:

- **SwiftFormat owns style** — indentation, spacing, wrapping, blank lines, imports. It **auto-fixes**.
- **SwiftLint owns safety/logic** — force-unwrap, force-cast, complexity, naming, and the project's custom rules. It **reports/blocks** (most issues need a human decision).

Overlapping style rules are therefore **disabled in `.swiftlint.yml`** (`trailing_whitespace`, `colon`, `comma`, `opening_brace`, `vertical_whitespace`, `mark`, …) and left entirely to SwiftFormat.

### Custom SwiftLint rules

`.swiftlint.yml` adds a few project-specific rules SwiftFormat can't express.

**Safety / logic**

- `no_direct_standard_out_logs` — no `print` / `debugPrint` / `dump` / `_printChanges` (they write to stdout in release; use LogPipe instead).
- `no_unchecked_sendable` — discourages `@unchecked Sendable`; prefer a real `Sendable` conformance or a `@preconcurrency import`.
- `no_file_literal` / `no_filepath_literal` — prefer `#fileID` over `#file` / `#filePath` (shorter, and no absolute build path leaks into the binary).
- `no_objcMembers` — annotate each member with `@objc` instead of a blanket `@objcMembers`.

**Compile-time hygiene** — declaration patterns that measurably slow the Swift type-checker, based on [Lucas van Dongen — *Swift compiler performance*](https://lucasvandongen.dev/compiler_performance.php):

- `avoid_bare_init_in_initializers` — write `Type(...)` instead of a contextual `.init(...)`. A bare `.init()` forces the type-checker to infer the type from context: ~10 % slower in simple cases and **up to ~30× slower inside computed properties** in van Dongen's measurements. (Qualified `Type.init`, `super.init`, `Self.init` are allowed.)
- `prefer_array_literal_over_init` — use `[...]` / `[:]` instead of `Array<T>(arrayLiteral:)` (≈ **4× slower**) or empty `Array()` / `Dictionary()` initializers.

### Opt-in rules

`.swiftlint.yml` also enables SwiftLint opt-in rules that complement SwiftFormat — semantics SwiftFormat doesn't touch:

- **Performance** — `first_where`, `last_where`, `contains_over_filter_count`, `contains_over_filter_is_empty`, `flatmap_over_map_reduce`, `reduce_into` (avoid redundant collection traversals).
- **Safety** — `force_unwrapping`, `implicitly_unwrapped_optional`, `fatal_error_message`, `weak_delegate`, `discouraged_optional_boolean`.
- **Modern Swift** — `toggle_bool`, `legacy_random`.

> `force_unwrapping` and `implicitly_unwrapped_optional` are configured as `warning`, but `make verify` runs SwiftLint `--strict`, which promotes every warning to an error — so in CI a stray `!` fails the build.

### Handled by SwiftFormat, not SwiftLint

Two compile-time-friendly transforms deliberately live in SwiftFormat, so there's no lint/format tug-of-war:

- **Redundant type annotations** — `redundantType` (via `--property-types inferred`) rewrites `let x: Int = 1` → `let x = 1`. Untyped literals type-check faster (van Dongen), and SwiftFormat does it *soundly*: it keeps load-bearing non-default annotations like `let x: Double = 1` or `let x: Float = 1.5`, which the article says to keep.
- **Shorthand optional binding** — `redundantOptionalBinding` rewrites `if let x = x` → `if let x`.

Because SwiftFormat auto-fixes both (and `swiftformat --lint` flags them in CI), there's no duplicate SwiftLint rule for them.

## Where linting runs (three stages)

| Stage | Command | Behavior |
|---|---|---|
| **While coding** | Xcode build phase → `scripts/run-swiftlint-incremental.sh` | Lints only changed Swift files; warnings are click-to-navigate in Xcode; **never fails the build** (`\|\| true`). |
| **On commit** | `pre-commit` | SwiftFormat auto-fixes; SwiftLint `--strict` blocks; image-size guard. |
| **CI / on demand** | `make verify` (or `make verify-github`) | Read-only: `swiftformat --lint` + `swiftlint --strict`. `verify-github` adds `--reporter github-actions-logging` so SwiftLint warnings show up as inline PR annotations. |

Strict enforcement lives at commit time and in CI; the build phase is fast feedback only.

### The build phase (one-time Xcode setup)

Target → **Build Phases** → **+** → **New Run Script Phase** → `SwiftLint (incremental)`:

```sh
bash "${SRCROOT}/scripts/run-swiftlint-incremental.sh"
```

Uncheck **"Based on dependency analysis"**. The project already sets `ENABLE_USER_SCRIPT_SANDBOXING = NO` so the script can call `git` and `mint`.

## pre-commit hooks

`.pre-commit-config.yaml` defines three local hooks (all via `mint run`, so they use the pinned versions):

1. **SwiftFormat** — formats staged Swift files in place.
2. **SwiftLint (strict)** — blocks the commit on any violation.
3. **Check Image and Icon Sizes** — `scripts/check_image_size.sh` blocks oversized assets, with per-category budgets:

   | Category | Limit |
   |---|---|
   | Icons (`*.appiconset`, `Icons/`) | 100 KB |
   | Vectors (`pdf`, `svg`) | 100 KB |
   | Raster (`png`, `jpg`, `heic`, `webp`) | 1 MB |
   | GIF | 3 MB |

The hook is installed by `make bootstrap`.

## Editor settings — `.editorconfig`

Indentation and whitespace are defined per-project in `.editorconfig` (read natively by **Xcode 16+**, VS Code, and Cursor): **tabs**, width 4, 120-column max, trim trailing whitespace, final newline. This keeps every editor consistent; SwiftFormat is the ultimate enforcer on commit.

> One Xcode-only setting that `.editorconfig` can't express is **spell-checking**. Run `bash scripts/xcode_settings.sh` once (with Xcode closed) to enable "Check Spelling While Typing".

## Compile-time health flags

Some of the slowest code to compile is a short, perfectly clean-looking expression that sends the Swift type-checker exploring a combinatorial space of overloads — array/string concatenation with `+`, mixed-literal arithmetic, long ternary / nil-coalescing chains. **Neither SwiftLint nor SwiftFormat can detect this:** it isn't a syntactic pattern, it's a property of the constraint solver, so only the compiler knows. (The custom rules above catch a few *specific* slow constructs — `.init`, `arrayLiteral:` — but not the general case.)

The detector for the general case — popularised by [Robert Gummesson — *Regarding the Optimization of Swift Build Times*](https://medium.com/@RobertGummesson/regarding-swift-build-time-optimizations-fc92cdd91e31) — is a pair of Swift frontend flags, which `Config/Dev/Dev.xcconfig` wires in on **Dev** builds only (Prod stays clean):

```
OTHER_SWIFT_FLAGS = -Xfrontend -warn-long-function-bodies=150 -Xfrontend -warn-long-expression-type-checking=150
```

The compiler then warns whenever a function body or an expression's type-checking exceeds **150 ms**, surfacing the hotspot as a click-to-navigate warning. They're warnings only (they never fail the build); raise the threshold if it gets noisy, or lower it toward 100 ms to be stricter.

**When one fires:** split the expression into smaller sub-expressions and add an explicit type annotation to the result — that's what collapses the solver's search space (see van Dongen / Gummesson above).

## Adding CI

The template ships **no live CI** by design (to stay provider-agnostic), but includes a ready GitHub Actions starter at **`.github/workflows/ci.yml.example`**. Enable it by renaming to `ci.yml`. For *why* CI is structured the way it is — layered gates, gate vs advisory, where a new check belongs — see **[CI.md](CI.md)**.

The starter runs **three jobs in parallel**, mirroring the local gate (`make verify` + the test run) split for speed:

| Job | Runs | Why separate |
|---|---|---|
| **lint** | `make verify-github` (SwiftFormat `--lint` + SwiftLint `--strict` with inline annotations) | No Xcode/simulator needed, so it returns in seconds and surfaces lint issues on the PR without waiting for a build. |
| **test** | `xcodebuild test` on the `-Dev` scheme, piped through `xcbeautify` | Builds the app and runs the unit tests; uploads the `.xcresult` bundle as an artifact. |
| **duplicate-images** | `scripts/find_duplicate_images.py` on the PR's changed images | Soft gate that fails only on duplicate images the PR *introduces*. Runs on a cheap Ubuntu runner (no Xcode). See [Image hygiene](IMAGE_HYGIENE.md). |

The duplicate gate lives in `ci.yml` next to lint/test: on a PR it fails only on duplicates the PR *introduces*, and on a push to `main` it fails on *any* duplicate (the whole-project backstop). A companion **`.github/workflows/hygiene.yml.example`** holds the weekly unused-image scan and a self-test for the tools. Full details — including exactly what each check does and does **not** cover — are in **[docs/IMAGE_HYGIENE.md](IMAGE_HYGIENE.md)**.

What the starter does for you:

- **Pinned tools, cached.** Both the Mint tool cache (keyed on `Mintfile`) and the resolved SPM checkouts (keyed on `Package.resolved`) are cached, so CI uses the same pinned versions as everyone else and skips re-resolving packages on every run.
- **Latest stable Xcode.** `maxim-lobanov/setup-xcode@v1` with `xcode-version: latest-stable` floats to the newest released Xcode on the runner image. Pin an explicit version (e.g. `'16.2'`) in that step if you need byte-reproducible builds.
- **Debuggable failures.** `-resultBundlePath TestResults.xcresult` is uploaded on every run, and `xcodebuild.log` is uploaded on failure — open the `.xcresult` in Xcode to inspect failing tests instead of scrolling raw logs. (We deliberately *don't* try to parse Swift Testing failures out of the bundle in CI — uploading the artifact is simpler and more reliable.)
- **Fast, safe scheduling.** `concurrency` cancels superseded runs on the same ref, and `timeout-minutes: 30` caps a hung test job.

> ⚠️ **`Package.resolved` must be committed.** The SPM cache key hashes it, and reproducible app builds depend on it. The template's `.gitignore` no longer ignores it — make sure it's tracked (`git add iOSAppTemplate.xcodeproj/project.xcworkspace/xcshareddata/swiftpm/Package.resolved`).

If you scaffolded with `make new-app`, update the `PROJECT` / `SCHEME` env vars **and the `Package.resolved` path in the SPM cache key** to your renamed project (the rename script doesn't touch CI YAML). Pick a runner image whose Xcode matches the project — use a macOS 26 image if you adopt iOS 26 APIs such as Liquid Glass.

### Scaling up in production

The starter is tuned for a small-to-medium app. As the project grows, the commented **"Production tuning"** block at the bottom of the workflow sketches the common next steps:

- **Parallel tests** — once the suite is large, shard it across simulator clones with `-parallel-testing-enabled YES -parallel-testing-worker-count N` (Swift Testing already parallelizes in-process; this adds cross-process sharding). Raise `timeout-minutes` to match the longer wall-clock budget.
- **Simulator / OS matrix** — run the `test` job under a `strategy.matrix` of destinations to catch version-specific breakage.
- **Code coverage** — add `-enableCodeCoverage YES`, then parse and upload (or gate on) the report.
- **Release smoke build** — a build-only job on the `-Prod` scheme catches config-gated breakage the Dev tests miss.
- **Branch protection** — mark `lint` and `test` as required status checks so PRs can't merge red.
