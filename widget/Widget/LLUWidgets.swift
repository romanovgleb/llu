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

// MARK: - Root

struct LLURootView: View {
    @Environment(\.widgetFamily) private var family
    let entry: LLUEntry

    var body: some View {
        if family == .systemSmall {
            LLUSmallView(snapshot: entry.snapshot)
                .padding(10)
        } else {
            let groups = WidgetLayout.groups(entry.snapshot.providers)
            VStack(alignment: .leading, spacing: 0) {
                if family == .systemMedium {
                    UsageListView(groups: Array(groups.prefix(3)), compact: true)
                } else {
                    UsageListView(groups: groups)
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            .padding(.horizontal, 13)
            .padding(.vertical, 8)
        }
    }
}

// MARK: - Small family

struct LLUSmallView: View {
    let snapshot: LLUSnapshot

    private var groups: [UsageGroup] { WidgetLayout.groups(snapshot.providers) }
    private var dense: [UsageGroup] { Array(groups.prefix(4)) }

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text("LLM Usage").font(.system(size: 10.5, weight: .semibold)).foregroundColor(.secondary)
                Spacer()
                Text(timestamp).font(.system(size: 9)).foregroundColor(.secondary)
            }
            Spacer(minLength: 2)
            ForEach(dense) { group in
                HStack(spacing: 5) {
                    Text(group.meta.name)
                        .font(.system(size: 10))
                        .lineLimit(1)
                        .minimumScaleFactor(0.75)
                        .frame(width: 48, alignment: .leading)
                    if let tier = group.tiers.max(by: { $0.row.pct < $1.row.pct }) {
                        BarView(pct: tier.row.pct, ghost: tier.row.barIsGhost)
                            .frame(height: 2)
                        Text(tier.row.value)
                            .font(.system(size: 10, weight: .semibold))
                            .monospacedDigit()
                            .lineLimit(1)
                            .frame(width: 38, alignment: .trailing)
                    } else {
                        Spacer()
                        Text("—").font(.system(size: 10)).foregroundColor(.secondary)
                            .frame(width: 38, alignment: .trailing)
                    }
                }
                .frame(height: 13)
            }
            Spacer(minLength: 0)
        }
    }

    private var timestamp: String {
        let f = DateFormatter()
        f.dateFormat = "HH:mm"
        return f.string(from: snapshot.fetchedAt)
    }
}
