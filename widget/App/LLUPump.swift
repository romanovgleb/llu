import Foundation

// MARK: - Pump: runs llu --json and writes the widget snapshot.

enum LLUPump {
    struct FetchError: LocalizedError {
        let message: String
        var errorDescription: String? { message }
    }

    /// Hard cap so a hung provider HTTP call cannot pin the menu-bar app forever.
    private static let runTimeout: TimeInterval = 45

    static let scriptPath = FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent("base/code/llu/llm_usage.py").path

    static func refresh() throws {
        let output = try runLLU()
        guard let data = output.data(using: .utf8),
              let raw = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let providers = raw["providers"] as? [[String: Any]],
              !providers.isEmpty else {
            throw FetchError(message: "llu --json returned unparseable output")
        }

        let rows: [ProviderRow] = providers.map { p in
            ProviderRow(
                name: p["name"] as? String ?? "?",
                ok: p["ok"] as? Bool ?? false,
                pct: (p["pct"] as? Double) ?? ((p["pct"] as? NSNumber)?.doubleValue ?? 0),
                detail: p["detail"] as? String ?? "",
                kind: p["kind"] as? String ?? "quota",
                amount: p["amount"] as? String ?? "",
                plan: p["plan"] as? String,
                error: p["error"] as? String ?? "")
        }
        // Prefer writing partial success (some ok:false rows) over freezing the
        // last good snapshot when one provider is down.
        guard rows.contains(where: \.ok) else {
            throw FetchError(message: "llu --json returned no live providers")
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
        let outPipe = Pipe()
        let errPipe = Pipe()
        proc.standardOutput = outPipe
        proc.standardError = errPipe
        try proc.run()

        let group = DispatchGroup()
        group.enter()
        DispatchQueue.global(qos: .utility).async {
            proc.waitUntilExit()
            group.leave()
        }
        let waited = group.wait(timeout: .now() + runTimeout)
        if waited == .timedOut {
            proc.terminate()
            // Give it a moment, then escalate.
            usleep(200_000)
            if proc.isRunning { kill(proc.processIdentifier, SIGKILL) }
            throw FetchError(message: "llu timed out after \(Int(runTimeout))s")
        }

        let stdout = String(data: outPipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
        // Exit non-zero used to mean "discard everything". Accept parseable
        // stdout when at least one provider is live (checked in refresh()).
        if proc.terminationStatus != 0 && stdout.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            let err = String(data: errPipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
            throw FetchError(message: "llu exited \(proc.terminationStatus)\(err.isEmpty ? "" : ": \(err)")")
        }
        return stdout
    }
}
