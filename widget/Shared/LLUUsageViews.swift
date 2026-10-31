import SwiftUI

// MARK: - Shared usage layout

// MARK: - Groups

struct GroupMeta {
    let key: String
    let logo: String
    let name: String
    let badge: String?

    static func `for`(_ key: String) -> GroupMeta {
        switch key {
        case "GLM":      return GroupMeta(key: key, logo: "Т", name: "GLM", badge: nil)
        case "DEEPSEEK": return GroupMeta(key: key, logo: "$", name: "DeepSeek", badge: nil)
        case "CODEX":    return GroupMeta(key: key, logo: "✳", name: "Codex", badge: nil)
        case "CURSOR":   return GroupMeta(key: key, logo: "⬡", name: "Cursor", badge: "Pro")
        case "KIMI":     return GroupMeta(key: key, logo: "K", name: "Kimi", badge: nil)
        case "GROQ":     return GroupMeta(key: key, logo: "◉", name: "Groq", badge: nil)
        default:         return GroupMeta(key: key, logo: "•", name: key, badge: nil)
        }
    }
}

struct QuotaTier: Identifiable {
    var id: String { "\(label)-\(row.name)" }
    let label: String
    let row: ProviderRow
}

struct UsageGroup: Identifiable {
    let id: String
    let meta: GroupMeta
    let badge: String?
    let tiers: [QuotaTier]
    let errors: [ProviderRow]
    let reset: String?
}

enum WidgetLayout {
    static let order = ["GLM", "DEEPSEEK", "CODEX", "CURSOR", "KIMI", "GROQ"]

    private static let windows: [String: [(String, String)]] = [
        "GLM":      [("5h", "GLM 5h"), ("wk", "GLM wk")],
        "DEEPSEEK": [("bal", "DeepSeek")],
        "CODEX":    [("5h", "Codex 5h"), ("wk", "Codex wk")],
        "CURSOR":   [("plan", "Cursor"), ("api", "Cursor API")],
        "KIMI":     [("5h", "Kimi 5h"), ("wk", "Kimi wk")],
        "GROQ":     [("hr", "Groq hr"), ("day", "Groq day")],
    ]

    static func groups(_ rows: [ProviderRow]) -> [UsageGroup] {
        order.compactMap { key -> UsageGroup? in
            let groupRows = rows.filter { $0.groupName == key }
            guard !groupRows.isEmpty else { return nil }
            let meta = GroupMeta.for(key)

            var tiers: [QuotaTier] = []
            for pair in windows[key] ?? [] {
                if let row = groupRows.first(where: { $0.name == pair.1 && $0.ok }) {
                    tiers.append(QuotaTier(label: pair.0, row: row))
                }
            }
            let errors = groupRows.filter { !$0.ok && !$0.name.hasSuffix("rst") }
            let badge = groupRows.compactMap(\.plan).first(where: { !$0.isEmpty }) ?? meta.badge
            return UsageGroup(
                id: key,
                meta: meta,
                badge: badge,
                tiers: tiers,
                errors: errors,
                reset: resetLine(key: key, rows: groupRows)
            )
        }
    }

    private static func resetLine(key: String, rows: [ProviderRow]) -> String? {
        switch key {
        case "GLM":
            let five = rows.first { $0.name == "GLM 5rst" && $0.ok }
            let week = rows.first { $0.name == "GLM wrst" && $0.ok }
            guard five != nil || week != nil else { return nil }
            var parts: [String] = []
            if let row = five {
                parts.append("5h ×\(row.amount)" + (row.detail.isEmpty ? "" : " · \(row.detail)"))
            }
            if let row = week {
                parts.append("wk ×\(row.amount)" + (row.detail.isEmpty ? "" : " · \(row.detail)"))
            }
            return parts.joined(separator: "   ")
        case "CODEX":
            guard let row = rows.first(where: { $0.name == "Codex rst" && $0.ok }) else { return nil }
            return "reset ×\(row.amount)" + (row.detail.isEmpty ? "" : " · \(row.detail)")
        default:
            return nil
        }
    }
}

// MARK: - Group block

struct UsageGroupView: View {
    let group: UsageGroup
    var compact: Bool = false

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack(spacing: 0) {
                Text(group.meta.logo)
                    .font(.system(size: 11.5, weight: .medium))
                    .foregroundColor(.secondary)
                    .frame(width: 22, alignment: .leading)
                Text(group.meta.name)
                    .font(.system(size: 12.5, weight: .semibold))
                    .foregroundColor(.primary)
                    .lineLimit(1)
                    .padding(.leading, 4)
                Spacer(minLength: 4)
                if let badge = group.badge, !badge.isEmpty {
                    Text(badge)
                        .font(.system(size: 8.5, weight: .medium))
                        .foregroundColor(Color.primary.opacity(0.45))
                        .lineLimit(1)
                }
            }
            .frame(height: 15)

            VStack(alignment: .leading, spacing: 2) {
                ForEach(group.tiers) { tier in
                    TierView(tier: tier)
                }
                ForEach(group.errors) { row in
                    let message = row.error ?? ""
                    Text(message.isEmpty ? "error" : message)
                        .font(.system(size: 9.5))
                        .foregroundColor(.red)
                        .lineLimit(1)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)

            if let reset = group.reset, !compact {
                HStack(spacing: 6) {
                    Text("↺")
                        .font(.system(size: 10.5))
                        .foregroundColor(.secondary)
                    Text(reset)
                        .font(.system(size: 10))
                        .foregroundColor(.secondary)
                        .monospacedDigit()
                        .lineLimit(1)
                        .minimumScaleFactor(0.8)
                    Spacer(minLength: 0)
                }
                .padding(.leading, 26)
            }
        }
    }
}

// MARK: - Rows

struct TierView: View {
    let tier: QuotaTier

    var body: some View {
        HStack(spacing: 4) {
            Text(tier.label)
                .font(.system(size: 10, weight: .semibold))
                .foregroundColor(.secondary)
                .lineLimit(1)
                .frame(width: 22, alignment: .leading)
            BarView(pct: tier.row.pct, ghost: tier.row.barIsGhost)
                .frame(height: 2)
            Text(tier.row.value)
                .font(.system(size: 11.5, weight: .semibold))
                .monospacedDigit()
                .lineLimit(1)
                .frame(width: 46, alignment: .trailing)
            Text(tier.row.detail.isEmpty
                 ? (tier.row.kind == "balance" ? "" : "—")
                 : tier.row.detail)
                .font(.system(size: 10))
                .foregroundColor(.secondary)
                .monospacedDigit()
                .lineLimit(1)
                .minimumScaleFactor(0.8)
                .frame(width: 62, alignment: .trailing)
        }
        .frame(height: 13)
    }
}

struct BarView: View {
    let pct: Double
    let ghost: Bool

    var body: some View {
        GeometryReader { geo in
            ZStack(alignment: .leading) {
                Capsule().fill(Color.secondary.opacity(0.12))
                if !ghost {
                    Capsule()
                        .fill(LinearGradient(
                            stops: [
                                .init(color: Color.primary.opacity(0.45), location: 0),
                                .init(color: Color.primary.opacity(0.95), location: 1),
                            ],
                            startPoint: .leading, endPoint: .trailing))
                        .frame(width: max(2, geo.size.width * min(max(pct, 0), 100) / 100))
                }
            }
        }
        .frame(height: 2)
    }
}

// MARK: - List

struct UsageListView: View {
    let groups: [UsageGroup]
    var compact: Bool = false

    var body: some View {
        VStack(alignment: .leading, spacing: compact ? 4 : 8) {
            ForEach(groups) { group in
                UsageGroupView(group: group, compact: compact)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}
