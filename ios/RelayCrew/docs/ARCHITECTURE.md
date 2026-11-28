# Architecture

This template follows a pragmatic **Clean Architecture** — minimal ceremony, but with clear separation of concerns and a strict one-way dependency rule.

## Layers

| Layer | Responsibility | Depends on |
|---|---|---|
| **App** | Composition root: `@main` entry, `AppDelegate`, DI container. Wires everything and starts the first flow. | everything |
| **Presentation** | SwiftUI views, navigation, view state. | Domain, Foundation |
| **Domain** | Pure business logic: models, use cases, repository protocols. **No framework imports.** | nothing |
| **Data** | Implementations of the Domain protocols: API services, DTOs, database, mappers. | Domain, Foundation |
| **Foundation** | Shared, stateless helpers and extensions used across layers. | — |

## Dependency Rule (one-way)

```
Presentation ──▶ Domain ◀── Data
        │                     │
        └──────▶ Foundation ◀─┘
```

- **Domain** is the center and depends on nothing.
- **Data** depends on Domain (it implements Domain's protocols), never on Presentation.
- **Presentation** depends only on Domain (via use cases / protocols) and Foundation.
- **App** is the only place that knows about concrete implementations.

This keeps business rules testable in isolation and lets you swap any implementation (network, database, UI) without touching the others.

## Typical Data Flow

1. A SwiftUI screen calls a **Use Case** (Domain).
2. The use case calls a **Repository protocol** (Domain).
3. A **Repository implementation** (Data) executes it via network/database.
4. A **Mapper** (Data) converts the DTO/entity into a Domain model.
5. The Domain model flows back to the screen.

A full worked example is in [SAMPLE_FEATURE.md](SAMPLE_FEATURE.md).

## Dependency Injection

Dependencies are wired in the **App** layer (`App/AppContainer.swift`) using simple constructor injection. When the graph grows or gains complex lifetimes, consider a dedicated container such as [Factory](https://github.com/hmlongco/Factory) or [swift-dependencies](https://github.com/pointfreeco/swift-dependencies).

## Bundled Packages

Three focused, zero-dependency Swift packages are resolved via SPM (not vendored locally), so they can be reused across projects and version-bumped independently:

| Package | Role |
|---|---|
| **[RESTKit](https://github.com/konotori/RESTKit)** | Networking: an `Endpoint` declares its response type, so requesting the wrong type is a compile error. Fully `Sendable`. |
| **[NaviStack](https://github.com/konotori/NaviStack)** | Navigation: centralized, type-safe SwiftUI router with a two-phase interceptor pipeline. |
| **[LogPipe](https://github.com/konotori/LogPipe)** | Logging: one API for every layer, structured events, per-destination levels, redaction. |

## Scaling Up: Feature Modules

The default layout slices by **layer**. As the app grows you can move to **feature slicing**, where each feature owns its own layers:

```
Features/
  Home/
    Presentation/
    Data/
    Domain/
```

For the Domain layer you have two options (and can combine them):

- **Shared Domain** — cross-feature models/use cases stay in the top-level `Domain/`.
- **Feature Domain** — feature-specific domain lives in `Features/<Feature>/Domain/`.

When build times or merge conflicts start to hurt, extract features into their own SPM modules — the dependency rule above makes this a mechanical refactor.
