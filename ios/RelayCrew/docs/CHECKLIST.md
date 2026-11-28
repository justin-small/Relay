# Feature Checklist

Follow the dependency order — Domain first, App last. See [SAMPLE_FEATURE.md](SAMPLE_FEATURE.md) for a worked example.

## Adding a feature

1. **Domain** — add the model in `Domain/Models/` (if needed).
2. **Domain** — define the repository protocol in `Domain/RepositoryProtocols/`.
3. **Domain** — add the use case in `Domain/UseCases/`.
4. **Data** — add DTOs and the API service (RESTKit `Endpoint`) in `Data/Network/`, or a database entity in `Data/Database/`.
5. **Data** — add the mapper in `Data/Mappers/` (DTO/entity → Domain model).
6. **Data** — implement the repository in `Data/Repositories/`.
7. **Presentation** — build the screen in `Presentation/Screens/` and any reusable parts in `UIComponents/`.
8. **Presentation** — add navigation (route enum + NaviStack wiring) in `Presentation/Navigation/`.
9. **App** — wire the dependency graph in `App/AppContainer.swift`.
10. **Test** — unit-test the use case against a stub repository.

## Adding a shared service

1. Decide the scope:
   - reusable **across projects** → a separate SPM package (like RESTKit/NaviStack/LogPipe);
   - shared **within this app only** → `Foundation/`.
2. Wire it in `App/AppContainer.swift`.

## Where do constants go?

| Constant type | Location |
|---|---|
| Environment / config (URLs, keys per env) | `Config/<Env>/<Env>.xcconfig` |
| UI (colors, spacing, typography) | `Presentation/Theme/` |
| Shared, non-UI | `Foundation/` |

## Before you commit

- Run `make fix` (format + auto-fix).
- The pre-commit hooks will block on remaining SwiftLint violations and oversized assets.
- For the full CI gate, run `make verify`.
