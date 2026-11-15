import Foundation

/// Bounded, latest-value handoff. A blocked consumer never queues one closure
/// per event. `put` returns true only when the caller should schedule a drain.
/// Arrival is host uptime at entry to a scheduled callback, not radio arrival.
final class StreamMailbox<Value> {
    struct Event {
        let value: Value
        let generation: UUID
        let arrival: TimeInterval
    }
    struct Statistics {
        var lastArrival: TimeInterval?
        var maxGap: TimeInterval = 0
        var count = 0
    }
    private let lock = NSLock()
    private var generation = UUID()
    private var latest: Event?
    private var scheduled = false
    private var stats = Statistics()

    func reset(generation: UUID) {
        lock.lock(); defer { lock.unlock() }
        self.generation = generation
        latest = nil
        scheduled = false
        stats = Statistics()
    }
    func put(value: Value, generation: UUID, arrival: TimeInterval) -> Bool {
        lock.lock(); defer { lock.unlock() }
        guard generation == self.generation, arrival.isFinite, arrival >= 0,
              stats.lastArrival.map({ arrival >= $0 }) ?? true else { return false }
        if let previous = stats.lastArrival { stats.maxGap = max(stats.maxGap, arrival - previous) }
        stats.lastArrival = arrival
        stats.count += 1
        latest = Event(value: value, generation: generation, arrival: arrival)
        if scheduled { return false }
        scheduled = true
        return true
    }
    func take() -> Event? {
        lock.lock(); defer { lock.unlock() }
        defer { latest = nil; scheduled = false }
        return latest
    }
    func statistics() -> Statistics {
        lock.lock(); defer { lock.unlock() }
        return stats
    }
}
