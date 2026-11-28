import Combine
import Foundation
import LogPipe

struct DemoChatMessage: Identifiable {
	let id = UUID()
	let channel: String
	let author: String
	let time: String
	let text: String
	let isMine: Bool
}

struct DemoResource: Identifiable {
	let icon: String
	let title: String
	let detail: String
	var id: String { title }
}

@MainActor
final class DemoStore: ObservableObject {
	private let logger = LogPipe.Logger(
		config: LoggerConfiguration(includeSourceInfo: false, includeThread: false),
		destinations: [LogDestination(formatter: JSONLogFormatter(), sink: OSLogSink(category: "demo"))]
	)
	@Published var isRunning = false
	@Published var connectionStatus = "Connected"
	@Published var messages = DemoStore.sampleMessages
	@Published var completedChecklist: Set<String> = [
		"Batteries charged and in", "Tripod level", "Camera card formatted", "Lens clean"
	]
	@Published var showProPresenter = true {
		didSet {
			logger.info("Demo module visibility changed", tags: ["ui"], context: ["module": "ProPresenter", "enabled": showProPresenter])
		}
	}
	@Published var showPlanningCenter = true {
		didSet {
			logger.info("Demo module visibility changed", tags: ["ui"], context: ["module": "Planning Center", "enabled": showPlanningCenter])
		}
	}
	@Published var proPresenterIndex = 2
	@Published var planIndex = 2
	@Published var selectedTalkChannel = "Production"
	@Published var isTalking = false
	@Published var pagesEnabled = true
	@Published var chatNotificationsEnabled = false
	@Published var wifiCheckInEnabled = true
	@Published var pageAcknowledged = false
	@Published var didPair = false
	@Published var lastOpenedFile: String?

	let checklistItems = [
		"White balance to the stage wash", "Frame the pulpit wide and tight", "Record card in, 1 hour free",
		"Batteries charged and in", "Tripod level", "Camera card formatted", "Lens clean"
	]
	let serviceItems = ["Welcome and countdown", "Goodness of God", "Announcements", "Sermon: Mercy at the Table", "Offering", "Great Are You Lord"]
	let proPresenterSlides = ["Welcome", "Goodness of God", "Announcements", "Sermon: Mercy at the Table", "Offering"]
	let talkChannels: [(String, String)] = [
		("Production", "4 here"), ("Stage", "Listen"), ("Worship", "Off"), ("Hosts", "Off")
	]
	let resources = [
		DemoResource(icon: "doc.text", title: "Shot list, Sunday", detail: "PDF · 2 pages"),
		DemoResource(icon: "play.rectangle", title: "Framing the pulpit", detail: "YouTube"),
		DemoResource(icon: "doc.richtext", title: "Volunteer handbook", detail: "Google Docs")
	]

	var currentPlanItem: String { serviceItems[boundedPlanIndex] }
	var nextPlanItem: String { serviceItems[min(boundedPlanIndex + 1, serviceItems.count - 1)] }
	var currentSlide: String { proPresenterSlides[boundedSlideIndex] }
	var checklistCount: Int { completedChecklist.count }
	var remainingChecklistCount: Int { max(0, checklistItems.count - completedChecklist.count) }
	var pageStatus: String { pageAcknowledged ? "7:52 · read" : "7:52 · tap to read" }

	func startDemo() {
		reset()
		isRunning = true
		logger.info("Demo session started", tags: ["ui"])
	}

	func endDemo() {
		reset()
		isRunning = false
		logger.info("Demo session ended", tags: ["ui"])
	}

	func messages(in channel: String) -> [DemoChatMessage] {
		messages.filter { $0.channel == channel }
	}

	func sendMessage(_ text: String, in channel: String) {
		let clean = text.trimmingCharacters(in: .whitespacesAndNewlines)
		guard !clean.isEmpty else {
			logger.warn("Empty chat message ignored", tags: ["ui"], context: ["channel": channel])
			return
		}
		messages.append(DemoChatMessage(channel: channel, author: "You", time: "now", text: clean, isMine: true))
		logger.info("Demo chat message sent", tags: ["ui"], context: ["channel": channel, "characterCount": clean.count])
	}

	func toggleChecklist(_ item: String) {
		if completedChecklist.contains(item) {
			completedChecklist.remove(item)
		} else {
			completedChecklist.insert(item)
		}
		logger.info("Demo checklist changed", tags: ["ui"], context: ["completedCount": completedChecklist.count])
	}

	func moveProPresenter(by offset: Int) {
		proPresenterIndex = min(max(proPresenterIndex + offset, 0), proPresenterSlides.count - 1)
		logger.info("Demo ProPresenter slide changed", tags: ["ui"], context: ["slideIndex": proPresenterIndex])
	}

	func movePlan(by offset: Int) {
		planIndex = min(max(planIndex + offset, 0), serviceItems.count - 1)
		logger.info("Demo service plan changed", tags: ["ui"], context: ["planIndex": planIndex])
	}

	func toggleTalk() {
		isTalking.toggle()
		logger.info("Demo talk state changed", tags: ["ui"], context: ["isTalking": isTalking])
	}

	func openFile(named title: String) {
		lastOpenedFile = title
		logger.info("Demo file opened", tags: ["ui"], context: ["title": title])
	}

	func acknowledgePage() {
		pageAcknowledged = true
	}

	func tryReconnect() {
		connectionStatus = "Connected"
	}

	private var boundedPlanIndex: Int { min(max(planIndex, 0), serviceItems.count - 1) }
	private var boundedSlideIndex: Int { min(max(proPresenterIndex, 0), proPresenterSlides.count - 1) }

	private func reset() {
		connectionStatus = "Connected"
		messages = Self.sampleMessages
		completedChecklist = ["Batteries charged and in", "Tripod level", "Camera card formatted", "Lens clean"]
		showProPresenter = true
		showPlanningCenter = true
		proPresenterIndex = 2
		planIndex = 2
		selectedTalkChannel = "Production"
		isTalking = false
		pagesEnabled = true
		chatNotificationsEnabled = false
		wifiCheckInEnabled = true
		pageAcknowledged = false
		didPair = false
		lastOpenedFile = nil
	}

	private static let sampleMessages = [
		DemoChatMessage(channel: "Production", author: "Dev Shah", time: "7:55", text: "Cam 3 battery is at 20%, swapping now", isMine: false),
		DemoChatMessage(channel: "Production", author: "You", time: "7:56", text: "I've got a spare in the cage", isMine: true),
		DemoChatMessage(channel: "Production", author: "Priya Nair", time: "8:01", text: "Lyrics are loaded. Slide 4 has a typo; fixing.", isMine: false),
		DemoChatMessage(channel: "Production", author: "Sam Ortiz", time: "8:02", text: "Lighting check is done.", isMine: false),
		DemoChatMessage(channel: "Production", author: "You", time: "8:03", text: "Copy. Thanks.", isMine: true),
		DemoChatMessage(channel: "Production", author: "Dev Shah", time: "8:05", text: "Camera 3 is back online.", isMine: false),
		DemoChatMessage(channel: "Production", author: "Priya Nair", time: "8:07", text: "Ready for rehearsal.", isMine: false),
		DemoChatMessage(channel: "Everyone", author: "Maya Chen", time: "8:12", text: "Good morning! The team huddle is at 8:45.", isMine: false),
		DemoChatMessage(channel: "Everyone", author: "You", time: "8:13", text: "See you there.", isMine: true),
		DemoChatMessage(channel: "Camera ops", author: "Dev Shah", time: "8:18", text: "Camera 3 is ready on the center aisle.", isMine: false)
	]
}
