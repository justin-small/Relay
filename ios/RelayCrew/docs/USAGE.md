# Usage

How to turn this template into a new app, and the day-to-day workflow afterwards.

## Create a new app

### 1. Get the template

Use the GitHub **"Use this template"** button (or clone/download the repo). You'll have a folder named `iOSAppTemplate`.

### 2. Install the toolchain (once per machine)

```bash
make bootstrap
```

Installs Mint + the pinned SwiftLint/SwiftFormat versions, installs `pre-commit`, and registers the git hook.

### 3. Configure your app name

Edit **`.env`**:

```bash
NEW_PROJECT_NAME=MyApp                 # hyphen-free
NEW_BUNDLE_ID=com.yourcompany.myapp
```

> ⚠️ Use a hyphen-free name. Hyphens become underscores in Swift type names (`my-app` → `my_appApp`) and trip the SwiftLint `type_name` rule.

### 4. Scaffold

```bash
make new-app
```

This validates your input, renames everything, and **builds the result to verify it compiles**. It renames:

- the source and test folders,
- the `.xcodeproj` and all schemes (`MyApp-Dev` / `-Staging` / `-Prod`),
- the target, module, and `@main` `App` struct,
- bundle IDs per target and per environment (`Dev`/`Staging`/`Prod` xcconfig),
- the **root folder** itself (open the project from the new path afterwards).

### 5. Add the SwiftLint build phase (one-time, in Xcode)

See [TOOLING.md → build phase](TOOLING.md#the-build-phase-one-time-xcode-setup). This gives you click-to-navigate lint warnings while coding.

### 6. Start fresh git history (optional)

If you scaffolded from a clone rather than "Use this template":

```bash
rm -rf .git
git init
git branch -m main        # this git build has no `git init -b`
git add -A
git commit -m "Initial commit"
git remote add origin <your-repo-url>
git push -u origin main
```

## Day-to-day workflow

| When | Command |
|---|---|
| Before committing | `make fix` (format + auto-fix lint) |
| Quick lint check | `make lint` |
| Full gate (what CI runs) | `make verify` |
| See all commands | `make help` |

Committing automatically runs the pre-commit hooks (format, strict lint, image-size guard). Building in Xcode runs the incremental SwiftLint build phase.

## Adding a feature

Follow the layer order — Domain → Data → Presentation → App. There's a step-by-step list in [CHECKLIST.md](CHECKLIST.md) and a full worked example in [SAMPLE_FEATURE.md](SAMPLE_FEATURE.md).

## Switching environments

Select the matching scheme in Xcode (`MyApp-Dev`, `MyApp-Staging`, `MyApp-Prod`). Each scheme uses its own `.xcconfig` (`Config/<Env>/`), so bundle ID, app name, and any environment values change automatically.
