import AppKit
import Foundation
import WidgetKit

@main
final class AppDelegate: NSObject, NSApplicationDelegate {
    private var timer: Timer?
    private let pumpQueue = DispatchQueue(label: "ru.romanovgleb.llu.pump", qos: .utility)
    private var pumpRunning = false

    static func main() {
        let app = NSApplication.shared
        let delegate = AppDelegate()
        app.delegate = delegate
        app.setActivationPolicy(.accessory)
        withExtendedLifetime(delegate) {
            app.run()
        }
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        refresh()
        timer = Timer.scheduledTimer(withTimeInterval: 60, repeats: true) { [weak self] _ in
            self?.refresh()
        }
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
}
