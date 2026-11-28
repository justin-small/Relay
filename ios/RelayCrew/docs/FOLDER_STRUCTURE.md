# Folder Structure

Every folder maps to a layer from [ARCHITECTURE.md](ARCHITECTURE.md). Empty skeleton folders keep a `README.md` describing their purpose (these are excluded from the build target, so they cost nothing) — replace them with real files as you build.

## Top level

| Folder | Purpose |
|---|---|
| `App/` | Composition root: `@main` entry point, `AppDelegate`, and the `AppContainer` DI container. |
| `Config/` | Per-environment `.xcconfig` files: `Dev/`, `Staging/`, `Prod/`. |
| `Domain/` | Pure business logic. No framework dependencies. |
| `Data/` | Concrete implementations of Domain protocols (network/database/repository). |
| `Presentation/` | SwiftUI UI layer. |
| `Foundation/` | Shared, stateless helpers and extensions. |
| `Resources/` | Assets, fonts, localized strings, and media. |

## App

The composition root — the only place that knows about concrete types.

- `App/iOSAppTemplateApp.swift` — the `@main` `App`.
- `App/AppDelegate.swift` — UIKit lifecycle hooks (push notifications, etc.).
- `App/AppContainer.swift` — builds and holds the dependency graph.

## Domain

The center of the architecture — pure business logic.

- `Domain/Models/` — domain entities (plain `struct`s, no framework types).
- `Domain/UseCases/` — application business rules (interactors).
- `Domain/RepositoryProtocols/` — interfaces the Data layer implements.

## Data

Implements the Domain protocols.

- `Data/Network/` — API services and DTOs, built on **RESTKit**.
- `Data/Database/` — local persistence implementations.
- `Data/Repositories/` — repository implementations.
- `Data/Mappers/` — convert DTOs/entities ↔ Domain models.

## Presentation

The SwiftUI UI layer.

| Folder | Purpose |
|---|---|
| `Screens/` | Flow-level screens (`LoginScreen`, `HomeScreen`, …). |
| `UIComponents/` | Reusable, app-agnostic components (`PrimaryButton`, `EmptyStateView`, …). |
| `Views/` | Reusable composite views. |
| `Theme/` | Design tokens: colors, typography, spacing, shadows, animation constants. |
| `Modifiers/` | Shared SwiftUI view modifiers (`.cardStyle()`, `.screenBackground()`). |
| `Navigation/` | Route/destination enums and the **NaviStack** ↔ SwiftUI wiring (`NavigationStack`, `navigationDestination`, sheet/cover bindings). |
| `Sheets/` `Covers/` `Popups/` `Alerts/` | Modal/overlay presentations by type. |
| `Extensions/` | Presentation-only SwiftUI extensions. |

## Foundation

Shared, app-wide code that isn't business logic.

- `Foundation/Extensions/` — broadly reusable Swift/SwiftUI/Foundation extensions (`String+`, `Date+`, `View+`, `Color+`). No business logic.
- `Foundation/Helpers/` — stateless utilities: formatters, validators, small builders.

> Cross-project, reusable services live in their own SPM packages instead (see [RESTKit](https://github.com/konotori/RESTKit), [NaviStack](https://github.com/konotori/NaviStack), [LogPipe](https://github.com/konotori/LogPipe)). `Foundation/` is for code that's shared *within this app* but not worth a package.

## Resources

- `Resources/Assets.xcassets` — images, colors, app icon.
- `Resources/Fonts/` — custom fonts (remember to register them in `Info.plist`).
- `Resources/Documents/`, `GIFs/`, `Videos/` — bundled media.
- Localized strings (`Localizable.strings` / string catalogs) live here too.
