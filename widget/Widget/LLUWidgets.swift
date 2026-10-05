import SwiftUI
import WidgetKit

// MARK: - Timeline

struct LLUEntry: TimelineEntry {
    let date: Date
    let snapshot: LLUSnapshot
}

struct LLUProvider: TimelineProvider {
    func placeholder(in context: Context) -> LLUEntry {
        LLUEntry(date: Date(), snapshot: SnapshotStore.load() ?? .sample)
    }

    func getSnapshot(in context: Context, completion: @escaping (LLUEntry) -> Void) {
        let s = SnapshotStore.load()
        if s == nil { NSLog("LLUWidgetExtension: snapshot missing, showing sample") }
        completion(LLUEntry(date: Date(), snapshot: s ?? .sample))
    }

    func getTimeline(in context: Context, completion: @escaping (Timeline<LLUEntry>) -> Void) {
        let s = SnapshotStore.load()
        if s == nil { NSLog("LLUWidgetExtension: snapshot missing, showing sample") }
        let entry = LLUEntry(date: Date(), snapshot: s ?? .sample)
        let next = Calendar.current.date(byAdding: .second, value: 120, to: Date()) ?? Date()
        completion(Timeline(entries: [entry], policy: .after(next)))
    }
}

// MARK: - Widget

struct LLUUsageWidget: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: "ru.romanovgleb.llu.usage", provider: LLUProvider()) { entry in
            LLURootView(entry: entry)
                .containerBackground(for: .widget) { Color(nsColor: .windowBackgroundColor) }
        }
        .configurationDisplayName("LLM Usage")
        .description("llu quotas, spend and resets")
        .supportedFamilies([.systemSmall, .systemMedium, .systemLarge])
        .contentMarginsDisabled()
    }
}

@main
struct LLUWidgetBundle: WidgetBundle {
    var body: some Widget { LLUUsageWidget() }
}

// MARK: - Groups (bb-style: mono logo + name + badge)

struct GroupMeta {
    let key: String
    let logo: String
    let name: String
    let badge: String?

    static let all: [GroupMeta] = [
        GroupMeta(key: "CODEX", logo: "✳", name: "romanovgleb@gmail.com", badge: "Plus"),
        GroupMeta(key: "CURSOR", logo: "⬡", name: "Cursor", badge: "Pro"),
        GroupMeta(key: "GLM", logo: "Т", name: "GLM · Kimi · Grok", badge: "GLM lite"),
        GroupMeta(key: "PAYG", logo: "$", name: "Pay-as-you-go", badge: nil),
    ]

    static func `for`(_ key: String) -> GroupMeta {
        all.first { $0.key == key } ?? GroupMeta(key: key, logo: "•", name: key, badge: nil)
    }
}

struct LLURootView: View {
    @Environment(\.widgetFamily) private var family
    let entry: LLUEntry

    var body: some View {
        Group {
            let groups = Dictionary(grouping: entry.snapshot.providers, by: \.groupName)
            let order = ["CODEX", "CURSOR", "GLM", "PAYG"]
            switch family {
            case .systemSmall:
                LLUSmallView(snapshot: entry.snapshot)
            case .systemMedium:
                VStack(alignment: .leading, spacing: 0) {
                    ForEach(order.prefix(2), id: \.self) { g in
                        groupBlock(name: g, rows: groups[g] ?? [])
                    }
                }
            default:
                VStack(alignment: .leading, spacing: 0) {
                    ForEach(order, id: \.self) { g in
                        groupBlock(name: g, rows: groups[g] ?? [])
                    }
                }
            }
        }
        .padding(.horizontal, 13)
        .padding(.vertical, 9)
    }

    private func groupBlock(name: String, rows: [ProviderRow]) -> some View {
        let meta = GroupMeta.for(name)
        return VStack(alignment: .leading, spacing: 3) {
            HStack(spacing: 6) {
                Text(meta.logo)
                    .font(.system(size: 12, weight: .medium))
                    .foregroundColor(.secondary)
                    .frame(width: 15)
                Text(meta.name)
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundColor(.primary)
                    .lineLimit(1)
                if let badge = meta.badge {
                    Spacer()
                    Text(badge)
                        .font(.system(size: 9.5, weight: .medium))
                        .foregroundColor(.secondary)
                        .padding(.horizontal, 6).padding(.vertical, 1.5)
                        .background(Color.secondary.opacity(0.14))
                        .clipShape(Capsule())
                }
            }
            .padding(.bottom, 2)
            ForEach(rows) { row in
                RowView(row: row)
            }
        }
        .padding(.bottom, 9)
    }
}

// MARK: - Rows

struct RowView: View {
    let row: ProviderRow

    var body: some View {
        HStack(spacing: 7) {
            Text(row.name)
                .font(.system(size: 11))
                .foregroundColor(row.ok ? .primary : .red)
                .lineLimit(1)
                .frame(width: 56, alignment: .leading)
            GeometryReader { geo in
                ZStack(alignment: .leading) {
                    Capsule().fill(Color.secondary.opacity(0.10))
                    Capsule()
                        .fill(LinearGradient(
                            stops: [
                                .init(color: Color.primary.opacity(0.45), location: 0),
                                .init(color: Color.primary.opacity(0.95), location: 1),
                            ],
                            startPoint: .leading, endPoint: .trailing))
                        .frame(width: max(2, geo.size.width * min(row.pct, 100) / 100))
                        .opacity(row.barIsGhost ? 0.35 : 1)
                }
            }
            .frame(height: 2)
            .padding(.vertical, 1)
            Text(row.value)
                .font(.system(size: 11, weight: .semibold))
                .monospacedDigit()
                .frame(width: 52, alignment: .trailing)
            Text(row.aux)
                .font(.system(size: 10))
                .foregroundColor(.secondary)
                .monospacedDigit()
                .frame(width: 56, alignment: .trailing)
                .lineLimit(1)
            Text(row.detail)
                .font(.system(size: 9.5))
                .foregroundColor(.secondary)
                .monospacedDigit()
                .frame(width: 46, alignment: .trailing)
                .lineLimit(1)
        }
    }
}

// MARK: - Small family

struct LLUSmallView: View {
    let snapshot: LLUSnapshot

    private var pick: (String) -> ProviderRow? { { prefix in snapshot.providers.first { $0.name.hasPrefix(prefix) } } }

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text("LLM Usage").font(.system(size: 10.5, weight: .semibold)).foregroundColor(.secondary)
                Spacer()
                Text(timestamp).font(.system(size: 9)).foregroundColor(.secondary)
            }
            Spacer()
            if let row = pick("Codex 5h") { RowView(row: row) }
            if let row = pick("Codex wk") { RowView(row: row) }
            if let row = pick("Cursor") { RowView(row: row) }
            if let row = pick("GLM 5h") { RowView(row: row) }
            Spacer()
            HStack {
                if let cursor = pick("Cursor") { Text(cursor.amount).font(.system(size: 9.5, weight: .semibold)).monospacedDigit() }
                Spacer()
                if let rst = pick("Codex rst") {
                    Text("rst ").font(.system(size: 9.5)).foregroundColor(.secondary)
                        + Text(rst.amount).font(.system(size: 9.5, weight: .semibold)).monospacedDigit()
                }
            }
        }
    }

    private var timestamp: String {
        let f = DateFormatter()
        f.dateFormat = "HH:mm"
        return f.string(from: snapshot.fetchedAt)
    }
}
