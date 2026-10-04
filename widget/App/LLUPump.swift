import Foundation

// MARK: - Pump: runs llu --json and writes the widget snapshot.

enum LLUPump {
    struct FetchError: LocalizedError {
        let message: String
        var errorDescription: String? { message }
    }

    static let scriptPath = FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent("base/code/llu/llm_usage.py").path

    static func refresh() throws {
        let output = try runLLU()
        guard let data = output.data(using: .utf8),
              let raw = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let providers = raw["providers"] as? [[String: Any]] else {
            throw FetchError(message: "llu --json returned unparseable output")
        }

        let rows: [ProviderRow] = providers.map { p in
            ProviderRow(
                name: p["name"] as? String ?? "?",
                ok: p["ok"] as? Bool ?? false,
                pct: (p["pct"] as? Double) ?? ((p["pct"] as? NSNumber)?.doubleValue ?? 0),
                detail: p["detail"] as? String ?? "",
                kind: p["kind"] as? String ?? "quota",
                amount: p["amount"] as? String ?? "")
        }
        try SnapshotStore.write(LLUSnapshot(fetchedAt: Date(), providers: rows))
    }

    private static func runLLU() throws -> String {
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        let candidates = [
            ProcessInfo.processInfo.environment["SKILLS_PYTHON"],
            "\(home)/.cache/agents-skills-venvs/pack/bin/python",
            "/opt/homebrew/bin/python3",
            "/usr/bin/python3",
        ].compactMap { $0 }.filter { FileManager.default.isExecutableFile(atPath: $0) }

        guard let python = candidates.first else {
            throw FetchError(message: "no python with rich found")
        }

        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: python)
        proc.arguments = [scriptPath, "--json"]
        let pipe = Pipe()
        proc.standardOutput = pipe
        proc.standardError = Pipe()
        try proc.run()
        proc.waitUntilExit()
        guard proc.terminationStatus == 0 else {
            throw FetchError(message: "llu exited \(proc.terminationStatus)")
        }
        return String(data: pipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
    }
}
