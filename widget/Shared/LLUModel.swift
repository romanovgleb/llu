import Foundation
import SwiftUI

// MARK: - Snapshot model written by the pump app, read by the widget extension.

struct ProviderRow: Codable, Identifiable {
    var id: String { name }
    let name: String
    let ok: Bool
    let pct: Double
    let detail: String
    let kind: String      // quota | spend | count | balance
    let amount: String
}

struct LLUSnapshot: Codable {
    var fetchedAt: Date
    var providers: [ProviderRow]

    static let sample = LLUSnapshot(fetchedAt: Date(), providers: [
        ProviderRow(name: "Codex 5h", ok: true, pct: 0, detail: "5h00m", kind: "quota", amount: ""),
        ProviderRow(name: "Codex wk", ok: true, pct: 0, detail: "6d Oct 10", kind: "quota", amount: ""),
        ProviderRow(name: "Codex rst", ok: true, pct: 0, detail: "18d Oct 22", kind: "count", amount: "2"),
        ProviderRow(name: "Cursor", ok: true, pct: 25.5, detail: "12d Oct 16", kind: "spend", amount: "$114.82"),
        ProviderRow(name: "Cursor API", ok: true, pct: 0, detail: "12d Oct 16", kind: "quota", amount: ""),
        ProviderRow(name: "Grok Bot", ok: true, pct: 0, detail: "5d Oct 9", kind: "quota", amount: ""),
        ProviderRow(name: "GLM 5h", ok: true, pct: 1, detail: "4h57m", kind: "quota", amount: ""),
        ProviderRow(name: "GLM wk", ok: true, pct: 1, detail: "4d Oct 9", kind: "quota", amount: ""),
        ProviderRow(name: "Kimi 5h", ok: true, pct: 0, detail: "", kind: "quota", amount: ""),
        ProviderRow(name: "Kimi wk", ok: true, pct: 0, detail: "", kind: "quota", amount: ""),
        ProviderRow(name: "DeepSeek", ok: true, pct: 0, detail: "", kind: "balance", amount: "$14.88"),
        ProviderRow(name: "Groq hr", ok: true, pct: 0, detail: "0m / 2h", kind: "quota", amount: ""),
        ProviderRow(name: "Groq day", ok: true, pct: 0, detail: "0m / 8h", kind: "quota", amount: ""),
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

    /// Dim secondary column: dollar spend next to plan %.
    var aux: String { kind == "spend" ? amount : "" }

    var barIsGhost: Bool { kind == "balance" || kind == "count" }

    var groupName: String {
        if name.hasPrefix("Codex") { return "CODEX · PLUS" }
        if name.hasPrefix("Cursor") { return "CURSOR · PRO" }
        if name.hasPrefix("GLM") || name.hasPrefix("Grok") || name.hasPrefix("Kimi") { return "GLM · GROK · KIMI" }
        return "PAY-AS-YOU-GO"
    }

    var barColor: SwiftUI.Color {
        if pct > 85 { return SwiftUI.Color(red: 1, green: 0.42, blue: 0.37) }
        if pct > 60 { return SwiftUI.Color(red: 0.96, green: 0.71, blue: 0.27) }
        return .primary
    }
}

enum SnapshotStore {
    static var url: URL {
        let dir = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Application Support/LLUWidget", isDirectory: true)
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
