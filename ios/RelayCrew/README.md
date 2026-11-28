# Relay Crew for iOS

SwiftUI visual prototype for Relay's native crew app. It follows [crew mockup B](../../docs/mockups/crew/b-home.html) and uses native tab navigation, system controls, SF Symbols, and Relay's existing light and dark green tokens. The five bottom tabs are Home, Chat, Files, Talk, and Service; Service contains Live, Checklist, and Modules sections.

Run as Demo starts an in-memory sample service. Chat, checklists, file previews, Talk state, page acknowledgement, ProPresenter and Planning Center controls, and module visibility switches respond locally and share state across screens. Nothing connects to a Relay server, sends real audio, or changes server modules.

## Open and run

Open `RelayCrew.xcodeproj` in Xcode, choose the `RelayCrew-Dev` scheme, and run on an iPhone simulator. Tap **Run as Demo** on the start screen. Use **Me → End demo** to reset the sample session and return to the start screen. The app supports light and dark appearance; Page, Offline, Join, Tally, and Boards previews are available from the profile sheet.

## Automated app exercise

Run `scripts/run-ui-checks.sh` from this folder to launch the app in an iPhone simulator and drive its screens with XCUITest. The checks cover demo entry/reset, rapid tab changes, keyboard dismissal, chat input (including whitespace trimming), file search and preview, Talk channel/PTT state, service controls/module visibility, checklist toggles, and profile access. Set `SIMULATOR_NAME` to choose another installed simulator.

Each run creates a timestamped report in `ui-test-results/`: the Xcode test log, result summary, per-test results, simulator unified logs, screenshots, and other XCUITest attachments. App demo actions are written through LogPipe to the unified log under the app's `demo` category. A failed assertion makes the script return a failing exit code while retaining the report for diagnosis.

## Foundation and licensing

The Xcode project was scaffolded from [`konotori/iOSAppTemplate`](https://github.com/konotori/iOSAppTemplate), under its MIT license, and renamed for Relay. Its development guidance is kept in `AGENTS.md` and `docs/`. Relay's existing mark is included as an asset from `app/static/brand/relay-mark.svg`.

The starter project includes RESTKit, NaviStack, and LogPipe. This visual prototype does not use them; review the starter dependencies before adding app behavior. Keep the template's dependency rule, SwiftLint/SwiftFormat conventions, and no-new-dependencies-without-approval guardrail in mind.
