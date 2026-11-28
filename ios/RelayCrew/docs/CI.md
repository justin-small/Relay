# CI philosophy

How and **why** CI is structured in this template. This is the mindset; the
concrete jobs and tools live in [TOOLING.md](TOOLING.md) (lint / test),
[IMAGE_HYGIENE.md](IMAGE_HYGIENE.md) (image checks), and
[DEAD_CODE.md](DEAD_CODE.md) (dead Swift code).

> **Scope.** This is about **continuous integration** — checks on every change.
> Continuous *delivery* (code signing & distribution) is environment-specific and
> intentionally out of scope.

## The idea: layered gates

CI is a series of checks run at different moments. Each moment trades **speed**,
**coverage**, and **noise** differently, so every check belongs at the layer
that fits its nature.

| Layer | Runs | Best for | Default | Examples here |
|---|---|---|---|---|
| **pre-commit** | every commit (local) | fast, local, only the staged files | block | SwiftFormat, SwiftLint, image-size guard |
| **PR** | every pull request | precise checks needed before merge | gate | lint, test, duplicate-image gate (incremental) |
| **push to `main`** | merge / direct push | catch what a per-PR view can't see | gate | duplicate-image (whole-project) |
| **scheduled (weekly)** | cron | whole-project, fuzzy, batch cleanup | advisory | unused images, dead Swift code, tool self-test |

A check moves **earlier** (up the table) when it's cheap and precise — fast
feedback, caught before it spreads. It moves **later** when it's slow, fuzzy, or
whole-project — where blocking every change would create more noise than value.

## Principles

**1. Right check at the right layer.** Decide with four questions: is it *fast*?
*local* (needs only the changed files)? *precise* (few false positives)? *urgent*
(cheaper to catch now than later)? The more "yes", the earlier it belongs.

**2. Gate vs advisory.** Only *gate* (fail / block) on checks that are precise
and actionable — a red result must mean "there is a real problem, and here's
where". Fuzzy or batch checks are *advisory* (report, don't block). A check that
cries wolf gets ignored or bypassed, and then it protects nothing.

**3. Only flag what a change introduces.** A gate should fail on what *this*
PR / push adds, never on pre-existing issues — otherwise it blocks unrelated work
and trains people to ignore it. (The duplicate-image PR gate diffs against the
PR's merge-base for exactly this reason.)

**4. Precision over recall for gates.** A gate that occasionally blocks wrongly
is worse than one that misses a little — people stop trusting it. Bias gates
toward zero false positives, and push broad-but-fuzzy detection to advisory
layers where a human reviews it.

**5. Robust over clever.** Prefer a check that cannot silently miss things. A
whole-project scan re-evaluates the full state on every run, so a cancelled or
superseded run never leaves a permanent gap — unlike an incremental "diff since
the last run", whose window can be skipped under `cancel-in-progress`
concurrency.

**6. Fast feedback first.** Order independent jobs so the cheapest fail fastest:
lint (seconds, no Xcode) runs alongside the slow test build, and the
duplicate-image gate runs on a cheap Ubuntu runner in parallel — you learn about
a lint error without waiting for a simulator to boot.

## Adding a new check — where does it go?

```
Fast AND needs only the files being changed?
  └─ yes → pre-commit (block) or the PR lint job

Needs a build / the whole project, and should block before merge?
  └─ precise     → PR gate (fail only on what the PR introduces)
  └─ not precise → PR advisory (report), or move to weekly

Only judgeable across the whole project, or fuzzy / batch?
  └─ yes → weekly schedule (advisory)

Could something reach `main` outside a PR (direct push, cross-PR interaction)?
  └─ yes → add a push-to-`main` job as a whole-project backstop
```

When in doubt, start **advisory** and promote to a **gate** once it's proven
precise — never the other way around.

## See also

- [TOOLING.md](TOOLING.md) — lint / format / Mint / pre-commit, and the CI
  starter jobs in detail.
- [IMAGE_HYGIENE.md](IMAGE_HYGIENE.md) — the duplicate / unused / oversized image
  checks, and exactly what each does and does **not** cover.
- [DEAD_CODE.md](DEAD_CODE.md) — the Periphery unused-Swift-code scan and the
  false-positive rules in `.periphery.yml`.
