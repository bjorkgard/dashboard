import EventKit
import Foundation

@main
struct CalendarBridge {
    static func emit(_ value: [String: Any]) {
        guard let data = try? JSONSerialization.data(withJSONObject: value),
              let text = String(data: data, encoding: .utf8) else { return }
        print(text)
    }

    static func status() -> String {
        let state = EKEventStore.authorizationStatus(for: .event)
        if state == .fullAccess { return "authorized" }
        switch state {
        case .notDetermined: return "not_determined"
        case .denied: return "denied"
        case .restricted, .writeOnly: return "restricted"
        default: return "unavailable"
        }
    }

    @MainActor
    static func main() async {
        let operation = CommandLine.arguments.dropFirst().first ?? "status"
        guard ["status", "authorize", "events"].contains(operation) else {
            emit(["schema_version": 1, "status": "unavailable"])
            return
        }
        if operation == "status" {
            emit(["schema_version": 1, "status": status()])
            return
        }
        // Normal collection never triggers an OS consent prompt.
        if operation == "events" && status() != "authorized" {
            emit(["schema_version": 1, "status": status()])
            return
        }
        let store = EKEventStore()
        if operation == "authorize" {
            do {
                _ = try await store.requestFullAccessToEvents()
                emit(["schema_version": 1, "status": status()])
            } catch {
                emit(["schema_version": 1, "status": status()])
            }
            return
        }
        let now = Date()
        let until = now.addingTimeInterval(7 * 24 * 60 * 60)
        let selectedIDs = Set(CommandLine.arguments.dropFirst(2))
        let calendars = selectedIDs.isEmpty ? nil : store.calendars(for: .event).filter {
            selectedIDs.contains($0.calendarIdentifier)
        }
        if !selectedIDs.isEmpty && (calendars?.isEmpty ?? true) {
            emit(["schema_version": 1, "status": "authorized",
                  "events": [], "selection_missing": true])
            return
        }
        let predicate = store.predicateForEvents(withStart: now, end: until, calendars: calendars)
        let events = store.events(matching: predicate)
            .filter { $0.status != .canceled }
            .sorted { $0.startDate < $1.startDate }
        let formatter = ISO8601DateFormatter()
        let rows: [[String: Any]] = events.prefix(100).map { event in
            [
                "id": String(event.calendarItemIdentifier.prefix(256)),
                "title": String((event.title ?? "Untitled event").prefix(400)),
                "start": formatter.string(from: event.startDate),
                "end": formatter.string(from: event.endDate),
                "all_day": event.isAllDay,
                "calendar_id": String(event.calendar.calendarIdentifier.prefix(256)),
                "calendar_name": String(
                    "\(event.calendar.title) · \(event.calendar.source.title)".prefix(100)
                ),
            ]
        }
        emit([
            "schema_version": 1, "status": "authorized",
            "events": rows, "truncated": events.count > 100,
        ])
    }
}
