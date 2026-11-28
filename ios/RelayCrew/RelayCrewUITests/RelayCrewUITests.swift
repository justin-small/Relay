import XCTest

@MainActor
final class RelayCrewUITests: XCTestCase {
	private var app = XCUIApplication()

	override func setUpWithError() throws {
		continueAfterFailure = false
		app = XCUIApplication()
		app.launch()
	}

	func testChatInputAndKeyboardDismissal() throws {
		startDemo()
		app.tabBars.buttons["Chat"].tap()

		let messageField = app.textFields["chat.message"]
		XCTAssertTrue(messageField.waitForExistence(timeout: 5))
		messageField.tap()
		messageField.typeText("  Automated check: hello Relay!  ")
		app.buttons["chat.send"].tap()
		XCTAssertTrue(app.staticTexts["Automated check: hello Relay!"].waitForExistence(timeout: 3))

		let sentMessageAuthors = app.staticTexts.matching(NSPredicate(format: "label == %@", "You · now"))
		XCTAssertTrue(sentMessageAuthors.firstMatch.exists, "Sent message is missing its author/time label")
		let sentMessageCount = sentMessageAuthors.count
		messageField.tap()
		messageField.typeText("   ")
		app.buttons["chat.send"].tap()
		XCTAssertEqual(sentMessageAuthors.count, sentMessageCount, "Whitespace-only chat input created another message")

		messageField.tap()
		messageField.typeText("keyboard dismiss check")
		let keyboard = app.keyboards.firstMatch
		XCTAssertTrue(keyboard.waitForExistence(timeout: 3), "Typing did not show the keyboard")
		app.buttons["Everyone"].tap()
		expectation(for: NSPredicate(format: "exists == false"), evaluatedWith: keyboard)
		waitForExpectations(timeout: 3)
		app.tabBars.buttons["Files"].tap()
		XCTAssertTrue(app.textFields["Search files and links"].waitForExistence(timeout: 5))
		capture("chat-keyboard-dismissed")
	}

	func testFileSearchAndPreview() throws {
		startDemo()
		app.tabBars.buttons["Files"].tap()
		let search = app.textFields["Search files and links"]
		XCTAssertTrue(search.waitForExistence(timeout: 5))
		search.tap()
		search.typeText("no such resource")
		XCTAssertTrue(app.staticTexts["No matching files or links."].waitForExistence(timeout: 3))
		search.clearAndType("Shot list")
		app.buttons["Done"].tap()

		let sampleFile = app.buttons["files.open.Shot list, Sunday"]
		XCTAssertTrue(sampleFile.waitForExistence(timeout: 3))
		sampleFile.tap()
		XCTAssertTrue(app.alerts.firstMatch.waitForExistence(timeout: 3), "Opening a sample file did not show its preview alert")
		app.alerts.buttons["Done"].tap()
		capture("files-preview")
	}

	func testTalkServiceControlsAndChecklist() throws {
		startDemo()
		app.tabBars.buttons["Talk"].tap()
		app.buttons.matching(NSPredicate(format: "label CONTAINS %@", "Stage")).firstMatch.tap()
		XCTAssertTrue(app.staticTexts["Talking to Stage"].waitForExistence(timeout: 3))
		let talkButton = app.buttons["talk.toggle"]
		talkButton.tap()
		XCTAssertTrue(app.buttons["Talking · tap to stop"].waitForExistence(timeout: 3))
		talkButton.tap()
		XCTAssertTrue(app.buttons["Tap to talk"].waitForExistence(timeout: 3))
		capture("talk")

		app.tabBars.buttons["Service"].tap()
		XCTAssertTrue(app.staticTexts["Announcements"].waitForExistence(timeout: 3))
		app.buttons["service.propresenter.next"].tap()
		XCTAssertTrue(app.staticTexts["Sermon: Mercy at the Table"].waitForExistence(timeout: 3))
		app.buttons["service.propresenter.previous"].tap()
		app.buttons["service.planning.next"].tap()
		XCTAssertTrue(app.staticTexts["NOW · Sermon: Mercy at the Table"].waitForExistence(timeout: 3))
		app.buttons["service.planning.previous"].tap()

		app.buttons["Modules"].tap()
		let proPresenter = app.switches.matching(NSPredicate(format: "label CONTAINS %@", "ProPresenter")).firstMatch
		let planningCenter = app.switches.matching(NSPredicate(format: "label CONTAINS %@", "Planning Center")).firstMatch
		XCTAssertTrue(proPresenter.waitForExistence(timeout: 3))
		XCTAssertTrue(planningCenter.exists)
		proPresenter.tap()
		planningCenter.tap()
		app.buttons["Live"].tap()
		XCTAssertTrue(app.staticTexts["No live controls shown"].waitForExistence(timeout: 3))

		app.buttons["Modules"].tap()
		proPresenter.tap()
		app.buttons["Checklist"].tap()
		XCTAssertTrue(app.staticTexts["Camera 2"].waitForExistence(timeout: 3))
		let checklistItem = app.buttons["White balance to the stage wash"]
		XCTAssertTrue(checklistItem.waitForExistence(timeout: 3))
		checklistItem.tap()
		checklistItem.tap()
		capture("service-checklist")
	}

	func testRapidTabChangesAndDemoReset() throws {
		startDemo()
		let tabs = ["Chat", "Files", "Talk", "Service", "Home", "Talk", "Chat", "Service", "Home"]
		for tab in tabs {
			let button = app.tabBars.buttons[tab]
			XCTAssertTrue(button.waitForExistence(timeout: 3), "Missing tab: \(tab)")
			button.tap()
		}
		app.buttons["Open profile"].tap()
		let profileList = app.collectionViews.firstMatch
		XCTAssertTrue(profileList.exists, "Profile options list is missing")
		for _ in 0 ..< 8 {
			if app.buttons["End demo"].exists { break }
			profileList.swipeUp()
		}
		app.buttons["End demo"].tap()
		XCTAssertTrue(app.buttons["demo.run"].waitForExistence(timeout: 5), "Ending the demo did not return to its start screen")
		capture("demo-reset")
	}

	private func startDemo() {
		let runDemo = app.buttons["demo.run"]
		XCTAssertTrue(runDemo.waitForExistence(timeout: 10), "Demo welcome screen did not appear")
		runDemo.tap()
		XCTAssertTrue(app.tabBars.buttons["Home"].waitForExistence(timeout: 5))
	}

	private func capture(_ name: String) {
		let attachment = XCTAttachment(screenshot: XCUIScreen.main.screenshot())
		attachment.name = name
		attachment.lifetime = .keepAlways
		add(attachment)
	}
}

private extension XCUIElement {
	func clearAndType(_ value: String) {
		guard let existing = self.value as? String else {
			tap()
			typeText(value)
			return
		}
		tap()
		let deleteSequence = String(repeating: XCUIKeyboardKey.delete.rawValue, count: existing.count)
		typeText(deleteSequence + value)
	}
}
