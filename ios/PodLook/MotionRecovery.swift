import Foundation

/// Bounded recovery for a motion stream that starts silently or later stalls.
/// Host uptime drives deadlines; sensor timestamps only detect duplicate samples.
struct MotionRecovery {
    enum Action: Equatable {
        case waiting
        case healthy
        case stale
        case retry(attempt: Int)
        case exhausted
    }

    private(set) var active = false
    private(set) var retryCount = 0
    private(set) var lastReceivedAt: TimeInterval?
    private var lastSensorTimestamp: TimeInterval?
    private var attemptStartedAt: TimeInterval = 0
    private var lastObservedAt: TimeInterval = 0
    let retryInterval: TimeInterval
    let staleInterval: TimeInterval
    let maximumRetries: Int

    init(retryInterval: TimeInterval = 5, staleInterval: TimeInterval = 1,
         maximumRetries: Int = 3) {
        precondition(retryInterval.isFinite && staleInterval.isFinite)
        precondition(retryInterval > staleInterval && staleInterval > 0 && maximumRetries >= 0)
        self.retryInterval = retryInterval
        self.staleInterval = staleInterval
        self.maximumRetries = maximumRetries
    }

    mutating func start(now: TimeInterval) {
        stop()
        guard now.isFinite, now >= 0 else { return }
        active = true
        attemptStartedAt = now
        lastObservedAt = now
    }

    mutating func stop() {
        active = false
        retryCount = 0
        lastReceivedAt = nil
        lastSensorTimestamp = nil
        attemptStartedAt = 0
        lastObservedAt = 0
    }

    /// Returns false for duplicates, old samples, and invalid/backwards host time.
    /// Keep this object across manager recreations: cached samples must not reset
    /// the retry budget. An explicit new session calls start(now:) instead.
    @discardableResult
    mutating func received(now: TimeInterval, sampleTimestamp: TimeInterval) -> Bool {
        guard active, now.isFinite, now >= lastObservedAt,
              sampleTimestamp.isFinite, sampleTimestamp >= 0,
              lastSensorTimestamp.map({ sampleTimestamp > $0 }) ?? true else { return false }
        lastObservedAt = now
        lastReceivedAt = now
        lastSensorTimestamp = sampleTimestamp
        retryCount = 0
        return true
    }

    func isStale(now: TimeInterval) -> Bool {
        guard active, now.isFinite, now >= lastObservedAt else { return true }
        guard let receivedAt = lastReceivedAt else { return true }
        return now - receivedAt >= staleInterval
    }

    /// Calling action consumes a retry only once per interval. Repeated watchdog
    /// ticks at the same time cannot create an unbounded restart loop.
    mutating func action(now: TimeInterval) -> Action {
        guard active, now.isFinite, now >= lastObservedAt else { return .waiting }
        lastObservedAt = now
        let baseline = max(attemptStartedAt, lastReceivedAt ?? attemptStartedAt)
        if now - baseline >= retryInterval {
            guard retryCount < maximumRetries else { return .exhausted }
            retryCount += 1
            attemptStartedAt = now
            return .retry(attempt: retryCount)
        }
        guard lastReceivedAt != nil else { return .waiting }
        return isStale(now: now) ? .stale : .healthy
    }
}
