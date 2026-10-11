import Foundation
import ServiceManagement
import SwiftUI
import WidgetKit

@main
struct LLUApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    /// Bound so removing the tray icon hides it instead of quitting the pump.
    /// Default off: pump runs headless; reveal via reopen / --reveal / menu Show.
    @AppStorage("menuBarExtraVisible") private var menuBarExtraVisible = false

    var body: some Scene {
        MenuBarExtra("llu", systemImage: "gauge.with.needle", isInserted: $menuBarExtraVisible) {
            Button("Refresh now") { delegate.refresh() }
            Button(menuBarExtraVisible ? "Hide menu bar icon" : "Show menu bar icon") {
                menuBarExtraVisible.toggle()
            }
            Button(delegate.launchAtLogin ? "Disable launch at login" : "Enable launch at login") {
                delegate.toggleLaunchAtLogin()
            }
            Divider()
            Button("Quit") { NSApp.terminate(nil) }
        }
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    private var timer: Timer?
    private let pumpQueue = DispatchQueue(label: "ru.romanovgleb.llu.pump", qos: .utility)
    private var pumpRunning = false
    private(set) var launchAtLogin = SMAppService.mainApp.status == .enabled

    func applicationDidFinishLaunching(_ notification: Notification) {
        // Restore icon when user cold-starts with --reveal (icon can stay
        // hidden across launches via AppStorage after a tray remove).
        if CommandLine.arguments.contains("--reveal") {
            UserDefaults.standard.set(true, forKey: "menuBarExtraVisible")
        }
        refresh()
        timer = Timer.scheduledTimer(withTimeInterval: 60, repeats: true) { [weak self] _ in
            self?.refresh()
        }
    }

    /// Clicking the app again in Finder/Spotlight re-shows a hidden icon.
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        UserDefaults.standard.set(true, forKey: "menuBarExtraVisible")
        return false
    }

    func refresh() {
        pumpQueue.async { [weak self] in
            guard let self else { return }
            // Skip if a previous fetch is still in flight (timeout lives in LLUPump).
            if self.pumpRunning { return }
            self.pumpRunning = true
            defer { self.pumpRunning = false }
            do {
                try LLUPump.refresh()
                WidgetCenter.shared.reloadAllTimelines()
            } catch {
                NSLog("LLUWidget pump failed: \(error.localizedDescription)")
            }
        }
    }

    func toggleLaunchAtLogin() {
        do {
            if launchAtLogin {
                try SMAppService.mainApp.unregister()
            } else {
                try SMAppService.mainApp.register()
            }
            launchAtLogin.toggle()
        } catch {
            NSLog("LLUWidget launch-at-login toggle failed: \(error.localizedDescription)")
        }
    }
}
