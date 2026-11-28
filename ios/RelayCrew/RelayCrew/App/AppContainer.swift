import Foundation

/// Composition root: builds and holds app-wide dependencies.
/// See `docs/SAMPLE_FEATURE.md` for the full wiring pattern.
@MainActor
final class AppContainer {
	static let shared = AppContainer()

	private init() {}
}
