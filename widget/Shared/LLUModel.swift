import Foundation

// MARK: - Snapshot model written by the pump app, read by the widget extension.

struct ProviderRow: Codable, Identifiable {
    var id: String { name }
    let name: String
    let ok: Bool
    let pct: Double
    let detail: String
    let kind: String      // quota | spend | count | balance
    let amount: String
    let plan: String?
    let error: String?
}

struct LLUSnapshot: Codable {
    var fetchedAt: Date
    var providers: [ProviderRow]

    static let sample = LLUSnapshot(fetchedAt: Date(), providers: [
        ProviderRow(name: "Codex 5h", ok: true, pct: 16, detail: "4h27m", kind: "quota", amount: "", plan: "Plus", error: ""),
        ProviderRow(name: "Codex wk", ok: true, pct: 52, detail: "5d Nov 5", kind: "quota", amount: "", plan: "Plus", error: ""),
        ProviderRow(name: "Codex rst", ok: true, pct: 0, detail: "6d Nov 7", kind: "count", amount: "1", plan: nil, error: ""),
        ProviderRow(name: "Cursor", ok: true, pct: 78, detail: "11d Nov 12", kind: "spend", amount: "$353.02", plan: nil, error: ""),
        ProviderRow(name: "Cursor API", ok: true, pct: 0, detail: "11d Nov 12", kind: "quota", amount: "", plan: nil, error: ""),
        ProviderRow(name: "GLM 5h", ok: true, pct: 8, detail: "4h33m", kind: "quota", amount: "", plan: "Lite", error: ""),
        ProviderRow(name: "GLM wk", ok: true, pct: 72, detail: "5d Nov 6", kind: "quota", amount: "", plan: "Lite", error: ""),
        ProviderRow(name: "Kimi 5h", ok: true, pct: 0, detail: "", kind: "quota", amount: "", plan: nil, error: ""),
        ProviderRow(name: "Kimi wk", ok: true, pct: 0, detail: "", kind: "quota", amount: "", plan: nil, error: ""),
        ProviderRow(name: "DeepSeek", ok: true, pct: 0, detail: "", kind: "balance", amount: "$14.06", plan: "PAYG", error: ""),
        ProviderRow(name: "Groq hr", ok: true, pct: 0, detail: "0m / 2h", kind: "quota", amount: "", plan: "Free", error: ""),
        ProviderRow(name: "Groq day", ok: true, pct: 0, detail: "0m / 8h", kind: "quota", amount: "", plan: "Free", error: ""),
    ])
}

extension ProviderRow {
    /// Primary bright value column: %, $ or count — keeps every row aligned.
    var value: String {
        switch kind {
        case "spend", "quota": return String(format: "%.0f%%", pct)
        case "count", "balance": return amount
        default: return amount.isEmpty ? String(format: "%.0f%%", pct) : amount
        }
    }

    var barIsGhost: Bool { kind == "balance" || kind == "count" }

    var groupName: String {
        if name.hasPrefix("Codex") { return "CODEX" }
        if name.hasPrefix("Cursor") { return "CURSOR" }
        if name.hasPrefix("GLM") { return "GLM" }
        if name.hasPrefix("Kimi") { return "KIMI" }
        if name.hasPrefix("Groq") { return "GROQ" }
        if name.hasPrefix("DeepSeek") { return "DEEPSEEK" }
        return "OTHER"
    }
}

enum SnapshotStore {
    static let groupId = "group.ru.romanovgleb.llu"   // unused, kept for docs

    static var url: URL {
        let dir: URL
        if getenv("APP_SANDBOX_CONTAINER_ID") != nil {
            // sandboxed extension: home is already the container Data dir
            dir = FileManager.default.homeDirectoryForCurrentUser
                .appendingPathComponent("Library/Application Support/LLUWidget", isDirectory: true)
        } else {
            // unsandboxed pump app: write into the extension's container
            dir = FileManager.default.homeDirectoryForCurrentUser
                .appendingPathComponent("Library/Containers/ru.romanovgleb.llu.widget/Data/Library/Application Support/LLUWidget", isDirectory: true)
        }
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir.appendingPathComponent("snapshot.json")
    }

    static func write(_ snapshot: LLUSnapshot) throws {
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        try encoder.encode(snapshot).write(to: url, options: .atomic)
    }

    static func load() -> LLUSnapshot? {
        guard let data = try? Data(contentsOf: url) else { return nil }
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        return try? decoder.decode(LLUSnapshot.self, from: data)
    }
}
