import Foundation
import ServiceManagement
import SwiftUI
import WidgetKit

@main
struct LLUApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate

    var body: some Scene {
        MenuBarExtra("llu", systemImage: "gauge.with.needle") {
            Button("Refresh now") { delegate.refresh() }
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
    private(set) var launchAtLogin = SMAppService.mainApp.status == .enabled

    func applicationDidFinishLaunching(_ notification: Notification) {
        refresh()
        timer = Timer.scheduledTimer(withTimeInterval: 60, repeats: true) { [weak self] _ in
            self?.refresh()
        }
    }

    func refresh() {
        DispatchQueue.global(qos: .utility).async {
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
