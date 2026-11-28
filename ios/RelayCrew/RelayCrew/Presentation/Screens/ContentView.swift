import SwiftUI
import UIKit

private enum CrewTab: Hashable {
	case home
	case chat
	case files
	case talk
	case service
}

private enum ServiceSection: String, CaseIterable, Identifiable {
	case live = "Live"
	case checklist = "Checklist"
	case modules = "Modules"

	var id: String { rawValue }
}

private enum PreviewRoute: String, Identifiable, Equatable {
	case page
	case offline
	case join
	case tally
	case boards

	var id: String { rawValue }
}

struct ContentView: View {
	@StateObject private var demo = DemoStore()
	@State private var selectedTab: CrewTab = .home
	@State private var isShowingProfile = false
	@State private var previewRoute: PreviewRoute?
	@State private var serviceSection: ServiceSection = .live
	@FocusState private var focusedTab: CrewTab?

	var body: some View {
		Group {
			if demo.isRunning {
				demoApp
			} else {
				DemoWelcomeScreen(onRunDemo: demo.startDemo)
			}
		}
		.environmentObject(demo)
	}

	private var demoApp: some View {
		TabView(selection: $selectedTab) {
			NavigationStack {
				HomeScreen(
					onOpenChecklist: {
						serviceSection = .checklist
						selectedTab = .service
					},
						onOpenPage: { previewRoute = .page },
					onShowProfile: { isShowingProfile = true }
				)
			}
				.tabItem { Label("Home", systemImage: "house") }
				.tag(CrewTab.home)
			NavigationStack { ChatScreen(focusedTab: $focusedTab) }
				.tabItem { Label("Chat", systemImage: "bubble.left.and.bubble.right") }
				.tag(CrewTab.chat)
			NavigationStack { FilesScreen() }
				.tabItem { Label("Files", systemImage: "folder") }
				.tag(CrewTab.files)
			NavigationStack { TalkScreen() }
				.tabItem { Label("Talk", systemImage: "headphones") }
				.tag(CrewTab.talk)
			NavigationStack { ServiceScreen(section: $serviceSection) }
				.tabItem { Label("Service", systemImage: "switch.2") }
				.tag(CrewTab.service)
		}
		.tint(RelayPalette.accent)
		.onChange(of: selectedTab) { newTab in
			if newTab != .chat {
				focusedTab = nil
				UIApplication.shared.sendAction(#selector(UIResponder.resignFirstResponder), to: nil, from: nil, for: nil)
			}
		}
		.sheet(isPresented: $isShowingProfile) {
			ProfileSheet(onPreview: { previewRoute = $0 })
				.presentationDetents([.medium, .large])
				.presentationDragIndicator(.visible)
		}
		.fullScreenCover(item: $previewRoute) { route in
			PreviewStateScreen(route: route, onClose: {
				if route == .page { demo.acknowledgePage() }
				if route == .offline { demo.tryReconnect() }
				previewRoute = nil
			})
		}
	}
}

private struct DemoWelcomeScreen: View {
	var onRunDemo: () -> Void

	var body: some View {
		VStack(alignment: .leading, spacing: 22) {
			Spacer()
			HStack(spacing: 14) {
				RelayMark()
				VStack(alignment: .leading, spacing: 3) {
					Text("Relay Crew").font(.title2.weight(.bold))
					Text("A Sunday at Grace Church").font(.subheadline).foregroundStyle(RelayPalette.muted)
				}
			}
			Text("Explore the crew app").font(.largeTitle.weight(.bold))
			Text("Try a sample service with chat, checklists, Talk, live controls, and files.")
				.font(.body).foregroundStyle(RelayPalette.muted)
			CrewCard {
				VStack(alignment: .leading, spacing: 14) {
					Label("Sample Relay server · Connected", systemImage: "checkmark.circle.fill")
						.font(.subheadline.weight(.semibold)).foregroundStyle(RelayPalette.success)
					Label("Sample crew: Maya Okafor · Camera 2", systemImage: "person.crop.circle")
						.font(.subheadline)
					Label("Changes reset when you end the demo", systemImage: "arrow.counterclockwise")
						.font(.subheadline).foregroundStyle(RelayPalette.muted)
				}
			}
			Button(action: onRunDemo) {
				Text("Run as Demo").font(.headline).frame(maxWidth: .infinity, minHeight: 56)
					.background(RelayPalette.accent, in: RoundedRectangle(cornerRadius: 16)).foregroundStyle(.black)
			}
			.buttonStyle(.plain)
			.accessibilityIdentifier("demo.run")
			Spacer()
		}
		.padding(24)
		.frame(maxWidth: 760, maxHeight: .infinity, alignment: .leading)
		.frame(maxWidth: .infinity, maxHeight: .infinity)
		.background(RelayPalette.background.ignoresSafeArea())
	}
}

private struct HomeScreen: View {
	var onOpenChecklist: () -> Void
	var onOpenPage: () -> Void
	var onShowProfile: () -> Void
	@EnvironmentObject private var demo: DemoStore
	@Environment(\.horizontalSizeClass) private var horizontalSizeClass
	@State private var isPlanExpanded = true

	private var columns: [GridItem] {
		Array(repeating: GridItem(.flexible(), spacing: 16), count: horizontalSizeClass == .regular ? 2 : 1)
	}

	var body: some View {
		GeometryReader { geometry in
			let cardHeight = horizontalSizeClass == .regular ? max(220, geometry.size.height * 0.37) : 0

			ScrollView {
				VStack(alignment: .leading, spacing: 16) {
				HStack(spacing: 12) {
					RelayMark()
					Text("Good morning, Maya")
						.font(.title3.weight(.semibold))
						.lineLimit(1)
						.minimumScaleFactor(0.8)
					Spacer(minLength: 0)
					Button(action: onShowProfile) {
						Text("MO")
							.font(.caption.weight(.bold))
							.foregroundStyle(RelayPalette.accent)
							.frame(width: 40, height: 40)
							.background(RelayPalette.accent.opacity(0.14), in: Circle())
					}
					.buttonStyle(.plain)
					.accessibilityLabel("Open profile")
				}
				.padding(.top, 4)

					LazyVGrid(columns: columns, alignment: .center, spacing: 16) {
						CrewCard(minHeight: cardHeight) {
						VStack(alignment: .leading, spacing: 12) {
							CardHeading(title: "Now", detail: "Sunday Worship · 9:00")
							Text(demo.currentPlanItem).font(.title2.weight(.bold))
							Text("Live · Sam Ortiz on mic 08")
								.font(.subheadline)
								.foregroundStyle(RelayPalette.muted)
							Divider().overlay(RelayPalette.border)
							HStack {
								Text("NEXT").font(.caption.weight(.semibold)).foregroundStyle(RelayPalette.muted)
								Text(demo.nextPlanItem).font(.subheadline.weight(.semibold))
								Spacer(minLength: 0)
								Text("9:09").font(.caption.monospacedDigit()).foregroundStyle(RelayPalette.muted)
							}
						}
					}

						CrewCard(minHeight: cardHeight) {
						VStack(alignment: .leading, spacing: 4) {
							CardHeading(title: "Your morning", detail: "Camera 2")
							ScheduleRow(time: "7:30", title: "Call time", state: "In 7:24", isComplete: true)
							ScheduleRow(time: "Now", title: "Checklist, \(demo.remainingChecklistCount) left", action: "Open", onAction: onOpenChecklist)
							ScheduleRow(time: "8:45", title: "Pray with the team")
							ScheduleRow(time: "9:00", title: "Service")
						}
					}

					if demo.pagesEnabled {
						Button(action: onOpenPage) {
							CrewCard(borderColor: RelayPalette.warning, minHeight: cardHeight) {
								VStack(alignment: .leading, spacing: 8) {
									Spacer(minLength: 16)
									CardHeading(title: "Last page", detail: demo.pageStatus, color: RelayPalette.warning)
									Text("Production meeting at the back table in 5.").font(.body)
									Spacer(minLength: 16)
								}
								.frame(maxHeight: .infinity, alignment: .topLeading)
							}
						}
						.buttonStyle(.plain)
					}

						CrewCard(minHeight: cardHeight) {
							DisclosureGroup(isExpanded: $isPlanExpanded) {
							VStack(spacing: 2) {
								ScheduleRow(time: "8:58", title: "Welcome and countdown", isComplete: true)
								ScheduleRow(time: "9:00", title: "Goodness of God", isComplete: true)
								ScheduleRow(time: "9:06", title: "Announcements", isCurrent: true)
								ScheduleRow(time: "9:09", title: "Sermon")
								ScheduleRow(time: "9:41", title: "Offering")
								ScheduleRow(time: "9:45", title: "Great Are You Lord")
							}
							.padding(.top, 8)
							} label: {
							Text("Full plan · 6 items").font(.subheadline.weight(.semibold))
						}
						.tint(RelayPalette.foreground)
					}
					}
					.padding(.horizontal, 16)
					.padding(.vertical, 16)
					.frame(maxWidth: 1320)
					.frame(maxWidth: .infinity, minHeight: geometry.size.height, alignment: .top)
				}
		}
		.background(RelayPalette.background)
		.toolbar(.hidden, for: .navigationBar)
	}
}

private struct ChatScreen: View {
	@State private var selectedChannel = "Production"
	@State private var message = ""
	@FocusState.Binding var focusedTab: CrewTab?
	@EnvironmentObject private var demo: DemoStore
	private let channels = ["Production", "Everyone", "Camera ops"]

	var body: some View {
		VStack(spacing: 0) {
			Picker("Chat channel", selection: $selectedChannel) {
				ForEach(channels, id: \.self) { Text($0).tag($0) }
			}
			.pickerStyle(.segmented)
			.padding(.horizontal, 16)
			.padding(.vertical, 10)
			.frame(maxWidth: 1000)
			.frame(maxWidth: .infinity)
			.background(RelayPalette.background)
			.onChange(of: selectedChannel) { _ in
				focusedTab = nil
				UIApplication.shared.sendAction(#selector(UIResponder.resignFirstResponder), to: nil, from: nil, for: nil)
			}

			Divider().overlay(RelayPalette.border)

			ScrollViewReader { proxy in
				ScrollView {
					LazyVStack(alignment: .leading, spacing: 12) {
						ForEach(demo.messages(in: selectedChannel)) { entry in
							MessageBubble(author: "\(entry.author) · \(entry.time)", text: entry.text, isMine: entry.isMine)
								.id(entry.id)
						}
					}
					.padding(16)
					.frame(maxWidth: .infinity, alignment: .leading)
				}
				.scrollDismissesKeyboard(.interactively)
				.simultaneousGesture(TapGesture().onEnded {
					if focusedTab == .chat { focusedTab = nil }
				})
				.background(RelayPalette.background)
				.onChange(of: demo.messages.count) { _ in
					if let latest = demo.messages(in: selectedChannel).last {
						withAnimation { proxy.scrollTo(latest.id, anchor: .bottom) }
					}
				}
				.frame(maxWidth: 1000)
				.frame(maxWidth: .infinity)
			}
		}
		.safeAreaInset(edge: .bottom, spacing: 0) {
			HStack(spacing: 10) {
				TextField("Message \(selectedChannel)", text: $message)
					.textFieldStyle(.roundedBorder)
					.focused($focusedTab, equals: .chat)
					.submitLabel(.send)
					.accessibilityIdentifier("chat.message")
					.onSubmit(send)
				Button(action: send) { Image(systemName: "arrow.up.circle.fill").font(.title) }
					.accessibilityLabel("Send message")
					.accessibilityIdentifier("chat.send")
			}
			.padding(.horizontal, 16)
			.padding(.vertical, 10)
			.frame(maxWidth: 1000)
			.frame(maxWidth: .infinity)
			.background(RelayPalette.background)
		}
		.toolbar(.hidden, for: .navigationBar)
		.toolbar {
			ToolbarItemGroup(placement: .keyboard) {
				Spacer()
				Button("Done") { focusedTab = nil }
			}
		}
	}

	private func send() {
		demo.sendMessage(message, in: selectedChannel)
		message = ""
	}
}

private struct ServiceScreen: View {
	@Binding var section: ServiceSection
	@EnvironmentObject private var demo: DemoStore
	@Environment(\.horizontalSizeClass) private var horizontalSizeClass

	private var liveColumns: [GridItem] {
		Array(repeating: GridItem(.flexible(), spacing: 16), count: horizontalSizeClass == .regular ? 2 : 1)
	}

	var body: some View {
		VStack(spacing: 0) {
			Picker("Service section", selection: $section) {
				ForEach(ServiceSection.allCases) { value in
					Text(value.rawValue).tag(value)
				}
			}
			.pickerStyle(.segmented)
			.padding(.horizontal, 16)
			.padding(.vertical, 10)
			.frame(maxWidth: 1000)
			.frame(maxWidth: .infinity)
			.background(RelayPalette.background)

			Divider().overlay(RelayPalette.border)

			Group {
				switch section {
				case .live:
					liveControls
				case .checklist:
					ChecklistScreen()
				case .modules:
					moduleVisibility
				}
			}
			.frame(maxWidth: .infinity, maxHeight: .infinity)
		}
		.background(RelayPalette.background)
		.toolbar(.hidden, for: .navigationBar)
	}

	private var liveControls: some View {
		ScrollView {
			LazyVGrid(columns: liveColumns, alignment: .center, spacing: 16) {
				if demo.showProPresenter {
					CrewCard {
						VStack(alignment: .leading, spacing: 14) {
							HStack {
								CardHeading(title: "ProPresenter")
								Spacer()
								Label("Connected", systemImage: "circle.fill")
									.font(.caption.weight(.semibold)).foregroundStyle(RelayPalette.success)
							}
							Text("LIVE NOW").font(.caption.weight(.semibold)).tracking(1).foregroundStyle(RelayPalette.muted)
							Text(demo.currentSlide).font(.title2.weight(.bold))
							HStack(spacing: 10) {
								Button("Previous") { demo.moveProPresenter(by: -1) }
									.frame(maxWidth: .infinity, minHeight: 46)
									.buttonStyle(.bordered)
									.accessibilityIdentifier("service.propresenter.previous")
								Button("Next") { demo.moveProPresenter(by: 1) }
									.frame(maxWidth: .infinity, minHeight: 46)
									.buttonStyle(.borderedProminent)
									.accessibilityIdentifier("service.propresenter.next")
							}
						}
					}
				}

				if demo.showPlanningCenter {
					CrewCard {
						VStack(alignment: .leading, spacing: 14) {
							HStack {
								CardHeading(title: "Planning Center Live")
								Spacer()
								Label("In progress", systemImage: "circle.fill")
									.font(.caption.weight(.semibold)).foregroundStyle(RelayPalette.success)
							}
							Text("Sunday Worship").font(.headline)
							VStack(alignment: .leading, spacing: 8) {
								Label("NOW · \(demo.currentPlanItem)", systemImage: "play.fill")
									.font(.subheadline.weight(.semibold))
								Label("NEXT · \(demo.nextPlanItem)", systemImage: "arrow.right")
									.font(.subheadline).foregroundStyle(RelayPalette.muted)
							}
							HStack(spacing: 10) {
								Button("Previous") { demo.movePlan(by: -1) }
									.frame(maxWidth: .infinity, minHeight: 46)
									.buttonStyle(.bordered)
								.accessibilityIdentifier("service.planning.previous")
								Button("Next") { demo.movePlan(by: 1) }
									.frame(maxWidth: .infinity, minHeight: 46)
									.buttonStyle(.borderedProminent)
								.accessibilityIdentifier("service.planning.next")
							}
						}
					}
				}

				if !demo.showProPresenter && !demo.showPlanningCenter {
					VStack(spacing: 10) {
						Image(systemName: "switch.2").font(.title2).foregroundStyle(RelayPalette.muted)
						Text("No live controls shown").font(.headline)
						Text("Choose which modules appear in Service from the Modules section.")
							.font(.subheadline).foregroundStyle(RelayPalette.muted).multilineTextAlignment(.center)
					}
					.frame(maxWidth: .infinity)
					.padding(.top, 36)
				}
			}
			.padding(16)
			.frame(maxWidth: 1120)
			.frame(maxWidth: .infinity)
		}
	}

	private var moduleVisibility: some View {
		List {
			Section {
				Toggle(isOn: $demo.showProPresenter) {
					Label {
						VStack(alignment: .leading, spacing: 3) {
							Text("ProPresenter")
							Text("Live slide controls").font(.caption).foregroundStyle(RelayPalette.muted)
						}
					} icon: {
						Image(systemName: "play.rectangle")
					}
				}
				Toggle(isOn: $demo.showPlanningCenter) {
					Label {
						VStack(alignment: .leading, spacing: 3) {
							Text("Planning Center")
							Text("Service plan live controls").font(.caption).foregroundStyle(RelayPalette.muted)
						}
					} icon: {
						Image(systemName: "calendar")
					}
				}
			} header: {
				Text("Show in Service")
			} footer: {
				Text("These switches control which module panels appear in the Live section.")
			}
		}
		.frame(maxWidth: 900)
		.frame(maxWidth: .infinity)
		.scrollContentBackground(.hidden)
		.background(RelayPalette.background)
	}
}

private struct ChecklistScreen: View {
	@EnvironmentObject private var demo: DemoStore

	var body: some View {
		ScrollView {
			VStack(alignment: .leading, spacing: 16) {
				CrewCard {
					VStack(alignment: .leading, spacing: 12) {
						CardHeading(title: "Camera 2", detail: "\(demo.checklistCount) of \(demo.checklistItems.count)")
						ProgressView(value: Double(demo.checklistCount), total: Double(demo.checklistItems.count)).tint(RelayPalette.accent)
						ForEach(demo.checklistItems, id: \.self) { item in
							Button {
								demo.toggleChecklist(item)
							} label: {
								Label(item, systemImage: demo.completedChecklist.contains(item) ? "checkmark.circle.fill" : "circle")
									.font(.body)
									.foregroundStyle(demo.completedChecklist.contains(item) ? RelayPalette.muted : RelayPalette.foreground)
									.strikethrough(demo.completedChecklist.contains(item))
									.frame(maxWidth: .infinity, alignment: .leading)
							}
							.buttonStyle(.plain)
						}
					}
				}
				CrewCard {
					VStack(alignment: .leading, spacing: 12) {
						CardHeading(title: "Everyone", detail: "1 of 2")
						Label("Pray with the team at 8:45", systemImage: "circle")
						Label("Check in with your team lead", systemImage: "checkmark.circle.fill")
								.foregroundStyle(RelayPalette.muted)
					}
				}
			}
			.padding(16)
			.frame(maxWidth: 900)
			.frame(maxWidth: .infinity)
		}
		.background(RelayPalette.background)
		.toolbar(.hidden, for: .navigationBar)
	}
}

private struct FilesScreen: View {
	@State private var searchText = ""
	@State private var isShowingFile = false
	@FocusState private var isSearchFocused: Bool
	@EnvironmentObject private var demo: DemoStore

	private var matchingResources: [DemoResource] {
		demo.resources.filter { searchText.isEmpty || $0.title.localizedCaseInsensitiveContains(searchText) }
	}

	var body: some View {
		ScrollView {
			VStack(alignment: .leading, spacing: 16) {
				TextField("Search files and links", text: $searchText)
					.textFieldStyle(.roundedBorder)
					.focused($isSearchFocused)
				CrewCard {
					VStack(alignment: .leading, spacing: 8) {
						CardHeading(title: "Service files and links")
						ForEach(matchingResources) { resource in
							Button {
								demo.openFile(named: resource.title)
								isShowingFile = true
							} label: {
								ResourceRow(icon: resource.icon, title: resource.title, detail: resource.detail)
							}
							.buttonStyle(.plain)
							.accessibilityIdentifier("files.open.\(resource.id)")
						}
						if matchingResources.isEmpty {
							Text("No matching files or links.").font(.subheadline).foregroundStyle(RelayPalette.muted)
						}
					}
				}
			}
			.padding(16)
			.frame(maxWidth: 900)
			.frame(maxWidth: .infinity)
		}
		.scrollDismissesKeyboard(.interactively)
		.background(RelayPalette.background)
		.toolbar(.hidden, for: .navigationBar)
		.toolbar {
			ToolbarItemGroup(placement: .keyboard) {
				Spacer()
				Button("Done") {
					isSearchFocused = false
					UIApplication.shared.sendAction(#selector(UIResponder.resignFirstResponder), to: nil, from: nil, for: nil)
				}
			}
		}
		.alert(demo.lastOpenedFile ?? "File", isPresented: $isShowingFile) {
			Button("Done", role: .cancel) {}
		} message: {
			Text("This sample file is open in the demo.")
		}
	}
}

private struct TalkScreen: View {
	@EnvironmentObject private var demo: DemoStore

	var body: some View {
		ScrollView {
			VStack(spacing: 14) {
				ForEach(demo.talkChannels, id: \.0) { channel, detail in
					Button { demo.selectedTalkChannel = channel } label: {
						HStack {
							VStack(alignment: .leading, spacing: 4) {
								Text(channel).font(.headline)
								Text(detail).font(.subheadline).foregroundStyle(RelayPalette.muted)
							}
							Spacer()
							Image(systemName: demo.selectedTalkChannel == channel ? "checkmark.circle.fill" : "circle")
								.foregroundStyle(demo.selectedTalkChannel == channel ? RelayPalette.accent : RelayPalette.muted)
						}
						.padding(16)
						.background(RelayPalette.card, in: RoundedRectangle(cornerRadius: 16))
						.overlay(RoundedRectangle(cornerRadius: 16).stroke(demo.selectedTalkChannel == channel ? RelayPalette.accent : RelayPalette.border, lineWidth: 1))
					}
					.buttonStyle(.plain)
				}
				Spacer(minLength: 40)
				Button {
					demo.toggleTalk()
				} label: {
					Label(demo.isTalking ? "Talking · tap to stop" : "Tap to talk", systemImage: demo.isTalking ? "waveform" : "mic.fill")
						.font(.headline)
						.foregroundStyle(.white)
						.frame(maxWidth: .infinity, minHeight: 58)
						.background(demo.isTalking ? RelayPalette.warning : RelayPalette.accent, in: Capsule())
				}
				.buttonStyle(.plain)
				.accessibilityIdentifier("talk.toggle")
				.accessibilityHint("Demo mode simulates push-to-talk; no audio is sent")
				Text("Talking to \(demo.selectedTalkChannel)")
					.font(.caption)
					.foregroundStyle(RelayPalette.muted)
			}
			.padding(16)
			.frame(maxWidth: 900)
			.frame(maxWidth: .infinity)
		}
		.background(RelayPalette.background)
		.toolbar(.hidden, for: .navigationBar)
	}
}

private struct ProfileSheet: View {
	var onPreview: (PreviewRoute) -> Void
	@Environment(\.dismiss) private var dismiss
	@EnvironmentObject private var demo: DemoStore

	var body: some View {
		NavigationStack {
			List {
				Section {
					HStack(spacing: 12) {
						Text("MO").font(.headline).foregroundStyle(RelayPalette.accent)
							.frame(width: 48, height: 48).background(RelayPalette.accent.opacity(0.14), in: Circle())
						VStack(alignment: .leading, spacing: 3) {
							Text("Maya Okafor").font(.headline)
							Text("Camera 2 · Production").font(.subheadline).foregroundStyle(RelayPalette.muted)
							Text("Demo · Grace Church · \(demo.connectionStatus)").font(.caption).foregroundStyle(RelayPalette.success)
						}
						Spacer()
						Label(demo.wifiCheckInEnabled ? "Checked in 7:24" : "Check-in paused", systemImage: demo.wifiCheckInEnabled ? "checkmark.circle.fill" : "pause.circle")
							.font(.caption).foregroundStyle(demo.wifiCheckInEnabled ? RelayPalette.success : RelayPalette.warning)
					}
				}
				Section("Preferences") {
					Toggle(isOn: $demo.pagesEnabled) {
						Label("Pages from the booth", systemImage: "bell.badge")
					}
					Toggle(isOn: $demo.chatNotificationsEnabled) {
						Label("Chat notifications", systemImage: "bubble.left")
					}
					Toggle(isOn: $demo.wifiCheckInEnabled) {
						Label("Wi-Fi check-in on this phone", systemImage: "iphone")
					}
				}
				Section("Other tools") {
					Button { onPreview(.tally); dismiss() } label: {
						Label("Tally · Which camera is live", systemImage: "video")
					}
					Button { onPreview(.boards); dismiss() } label: {
						Label("Boards · Foyer and green room", systemImage: "rectangle.on.rectangle")
					}
				}
				Section("Screen previews") {
					Button("Page") { onPreview(.page); dismiss() }
					Button("Offline") { onPreview(.offline); dismiss() }
					Button("Join Relay") { onPreview(.join); dismiss() }
				}
				Section {
					Button("End demo", role: .destructive) {
						demo.endDemo()
						dismiss()
					}
				}
			}
			.scrollContentBackground(.hidden)
			.background(RelayPalette.background)
			.navigationTitle("Me")
			.navigationBarTitleDisplayMode(.inline)
			.toolbar { ToolbarItem(placement: .topBarTrailing) { Button("Done") { dismiss() } } }
		}
	}
}

private struct PreviewStateScreen: View {
	let route: PreviewRoute
	var onClose: () -> Void
	@EnvironmentObject private var demo: DemoStore
	@State private var cameraIsProgram = true
	@State private var foyerBoardEnabled = true
	@State private var greenRoomBoardEnabled = true

	var body: some View {
		NavigationStack {
			Group {
				switch route {
				case .page: pageView
				case .offline: offlineView
				case .join: joinView
				case .tally: tallyView
				case .boards: boardsView
				}
			}
			.background(RelayPalette.background)
			.toolbar { ToolbarItem(placement: .topBarTrailing) { Button("Close", action: onClose) } }
		}
		.tint(RelayPalette.accent)
	}

	private var pageView: some View {
		VStack(alignment: .leading, spacing: 18) {
			Label("PAGE · 9:14", systemImage: "bell.badge.fill")
				.font(.subheadline.weight(.bold)).foregroundStyle(RelayPalette.warning)
			Text("From Sam Ortiz in the booth, to Production")
				.font(.subheadline).foregroundStyle(RelayPalette.muted)
			Text("Camera 2, go wide for the baptism on stage left.")
				.font(.largeTitle.weight(.bold))
			Spacer()
			CrewCard {
				VStack(alignment: .leading, spacing: 4) {
					Text("ON STAGE NOW").font(.caption.weight(.semibold)).foregroundStyle(RelayPalette.muted)
					Text("Baptism").font(.headline)
					Text("Next: Great Are You Lord").font(.subheadline).foregroundStyle(RelayPalette.muted)
				}
			}
			Button("Got it", action: onClose)
				.font(.headline).frame(maxWidth: .infinity, minHeight: 54)
				.background(RelayPalette.warning, in: RoundedRectangle(cornerRadius: 14)).foregroundStyle(.black)
		}
		.padding(20)
		.navigationTitle("Page")
	}

	private var offlineView: some View {
		VStack(alignment: .leading, spacing: 16) {
			Image(systemName: "wifi.slash").font(.system(size: 42)).foregroundStyle(RelayPalette.warning)
			Text("Relay is offline").font(.largeTitle.weight(.bold))
			Text("Your last position and service times are saved on this phone.")
				.font(.body).foregroundStyle(RelayPalette.muted)
			CrewCard {
				VStack(alignment: .leading, spacing: 6) {
					Text("Camera 2 · Production").font(.headline)
					Text("Call time 7:30 · Service 9:00").font(.subheadline).foregroundStyle(RelayPalette.muted)
				}
			}
				Button("Try again", action: onClose)
				.font(.headline).frame(maxWidth: .infinity, minHeight: 52)
				.background(RelayPalette.accent, in: RoundedRectangle(cornerRadius: 14)).foregroundStyle(.black)
			Spacer()
		}
		.padding(20)
		.navigationTitle("Offline")
	}

	private var joinView: some View {
		VStack(alignment: .leading, spacing: 16) {
			RelayMark()
			Text("Join Grace Church")
				.font(.largeTitle.weight(.bold))
			Text("Scan the crew QR code at church to securely pair this phone with Relay.")
				.font(.body).foregroundStyle(RelayPalette.muted)
			CrewCard {
				VStack(alignment: .leading, spacing: 12) {
					JoinStep(number: "1", title: "Scan the join code", detail: "Find it on the Crew setup page")
					JoinStep(number: "2", title: "Sign in to Relay", detail: "Use your crew account")
					JoinStep(number: "3", title: "You're ready", detail: "Your team and schedule appear here")
				}
			}
			Button(demo.didPair ? "Paired · Continue" : "Pair with Grace Church") {
				demo.didPair = true
				onClose()
			}
				.font(.headline).frame(maxWidth: .infinity, minHeight: 52)
				.background(RelayPalette.accent, in: RoundedRectangle(cornerRadius: 14)).foregroundStyle(.black)
			Spacer()
		}
		.padding(20)
		.navigationTitle("Join")
	}

	private var tallyView: some View {
		VStack(alignment: .leading, spacing: 18) {
			Text("Camera 2").font(.largeTitle.weight(.bold))
			Text("Program and preview tally").font(.subheadline).foregroundStyle(RelayPalette.muted)
			HStack(spacing: 12) {
				tallyTile(title: "PROGRAM", active: cameraIsProgram, color: .red)
				tallyTile(title: "PREVIEW", active: !cameraIsProgram, color: RelayPalette.success)
			}
			.frame(maxHeight: .infinity)
			Button("Switch camera state") { cameraIsProgram.toggle() }
				.font(.headline).frame(maxWidth: .infinity, minHeight: 52)
				.buttonStyle(.borderedProminent)
		}
		.padding(20)
		.navigationTitle("Tally")
	}

	private func tallyTile(title: String, active: Bool, color: Color) -> some View {
		VStack(spacing: 10) {
			Image(systemName: active ? "video.fill" : "video")
				.font(.largeTitle)
			Text(title).font(.headline.weight(.bold))
			Text(active ? "LIVE" : "STANDBY").font(.caption.weight(.semibold))
		}
		.foregroundStyle(active ? .black : RelayPalette.foreground)
		.frame(maxWidth: .infinity, maxHeight: .infinity)
		.background(active ? color : RelayPalette.card, in: RoundedRectangle(cornerRadius: 18))
	}

	private var boardsView: some View {
		List {
			Section("Screens") {
				Toggle("Foyer", isOn: $foyerBoardEnabled)
				Toggle("Green room", isOn: $greenRoomBoardEnabled)
			}
			Section {
				Text("Enabled screens are showing the current Sunday Worship board.")
					.font(.subheadline).foregroundStyle(RelayPalette.muted)
			}
		}
		.scrollContentBackground(.hidden)
		.background(RelayPalette.background)
		.navigationTitle("Boards")
	}
}

private struct CrewCard<Content: View>: View {
	var borderColor: Color = RelayPalette.border
	var minHeight: CGFloat = 0
	@ViewBuilder var content: Content

	var body: some View {
		content
			.padding(16)
			.frame(maxWidth: .infinity, minHeight: minHeight, alignment: .leading)
			.background(RelayPalette.card, in: RoundedRectangle(cornerRadius: 18))
			.overlay(RoundedRectangle(cornerRadius: 18).stroke(borderColor, lineWidth: 1))
	}
}

private struct CardHeading: View {
	let title: String
	var detail: String? = nil
	var color: Color = RelayPalette.muted

	var body: some View {
		HStack(alignment: .firstTextBaseline) {
			Text(title.uppercased())
				.font(.caption.weight(.semibold))
				.tracking(1)
				.foregroundStyle(color)
			Spacer(minLength: 8)
			if let detail {
				Text(detail).font(.caption).foregroundStyle(RelayPalette.muted)
			}
		}
	}
}

private struct ScheduleRow: View {
	let time: String
	let title: String
	var state: String? = nil
	var action: String? = nil
	var isComplete = false
	var isCurrent = false
	var onAction: (() -> Void)? = nil

	var body: some View {
		HStack(spacing: 10) {
			Text(time).font(.caption.monospacedDigit()).foregroundStyle(RelayPalette.muted).frame(width: 42, alignment: .leading)
			Text(title).font(.subheadline.weight(isCurrent ? .semibold : .regular))
				.foregroundStyle(isComplete ? RelayPalette.muted : RelayPalette.foreground)
				.strikethrough(isComplete)
			Spacer(minLength: 4)
			if let state {
				Text(state).font(.caption).foregroundStyle(isComplete ? RelayPalette.success : RelayPalette.muted)
			} else if let action {
				Button(action) { onAction?() }.font(.caption.weight(.semibold))
			}
		}
		.padding(.vertical, 7)
		.overlay(alignment: .top) { Rectangle().fill(RelayPalette.border).frame(height: 0.5) }
	}
}

private struct MessageBubble: View {
	let author: String
	let text: String
	var isMine = false

	var body: some View {
		VStack(alignment: .leading, spacing: 4) {
			Text(author).font(.caption).foregroundStyle(RelayPalette.muted)
			Text(text).font(.body)
		}
		.padding(12)
		.frame(maxWidth: 310, alignment: .leading)
		.background(isMine ? RelayPalette.accent.opacity(0.16) : RelayPalette.card, in: RoundedRectangle(cornerRadius: 16))
		.frame(maxWidth: .infinity, alignment: isMine ? .trailing : .leading)
	}
}

private struct ResourceRow: View {
	let icon: String
	let title: String
	let detail: String

	var body: some View {
		HStack(spacing: 12) {
			Image(systemName: icon).font(.headline).foregroundStyle(RelayPalette.accent)
			VStack(alignment: .leading, spacing: 3) {
				Text(title).font(.subheadline.weight(.semibold))
				Text(detail).font(.caption).foregroundStyle(RelayPalette.muted)
			}
			Spacer()
			Image(systemName: "chevron.right").font(.caption.weight(.semibold)).foregroundStyle(RelayPalette.muted)
		}
		.padding(.vertical, 8)
	}
}

private struct JoinStep: View {
	let number: String
	let title: String
	let detail: String

	var body: some View {
		HStack(alignment: .top, spacing: 12) {
			Text(number).font(.caption.weight(.bold)).foregroundStyle(RelayPalette.accent)
				.frame(width: 26, height: 26).background(RelayPalette.accent.opacity(0.14), in: Circle())
			VStack(alignment: .leading, spacing: 3) {
				Text(title).font(.subheadline.weight(.semibold))
				Text(detail).font(.caption).foregroundStyle(RelayPalette.muted)
			}
		}
	}
}

private struct RelayMark: View {
	var body: some View {
		Image("RelayMark")
			.resizable()
			.frame(width: 34, height: 34)
			.accessibilityLabel("Relay")
	}
}

private enum RelayPalette {
	static let background = Color(uiColor: UIColor { traits in
		traits.userInterfaceStyle == .dark ? UIColor(red: 0.035, green: 0.035, blue: 0.043, alpha: 1) : UIColor(red: 0.957, green: 0.957, blue: 0.969, alpha: 1)
	})
	static let card = Color(uiColor: UIColor { traits in
		traits.userInterfaceStyle == .dark ? UIColor(red: 0.067, green: 0.067, blue: 0.075, alpha: 1) : .white
	})
	static let foreground = Color(uiColor: UIColor { traits in
		traits.userInterfaceStyle == .dark ? UIColor(red: 0.98, green: 0.98, blue: 0.98, alpha: 1) : UIColor(red: 0.094, green: 0.094, blue: 0.106, alpha: 1)
	})
	static let muted = Color(uiColor: UIColor { traits in
		traits.userInterfaceStyle == .dark ? UIColor(red: 0.63, green: 0.63, blue: 0.67, alpha: 1) : UIColor(red: 0.39, green: 0.39, blue: 0.42, alpha: 1)
	})
	static let border = Color(uiColor: UIColor { traits in
		traits.userInterfaceStyle == .dark ? UIColor(red: 0.153, green: 0.153, blue: 0.165, alpha: 1) : UIColor(red: 0.89, green: 0.89, blue: 0.91, alpha: 1)
	})
	static let accent = Color(uiColor: UIColor { traits in
		traits.userInterfaceStyle == .dark ? UIColor(red: 0.29, green: 0.855, blue: 0.502, alpha: 1) : UIColor(red: 0.082, green: 0.49, blue: 0.239, alpha: 1)
	})
	static let warning = Color(uiColor: UIColor { traits in
		traits.userInterfaceStyle == .dark ? UIColor(red: 0.984, green: 0.733, blue: 0.255, alpha: 1) : UIColor(red: 0.70, green: 0.325, blue: 0.035, alpha: 1)
	})
	static let success = Color(uiColor: UIColor { traits in
		traits.userInterfaceStyle == .dark ? UIColor(red: 0.29, green: 0.855, blue: 0.502, alpha: 1) : UIColor(red: 0.082, green: 0.49, blue: 0.239, alpha: 1)
	})
}

#Preview {
	ContentView()
}
