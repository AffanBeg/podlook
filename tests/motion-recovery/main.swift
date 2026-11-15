import Foundation

var checks = 0
var failures = 0
func check(_ condition: @autoclosure () -> Bool, _ message: String) {
    checks += 1
    if !condition() { failures += 1; print("FAIL: \(message)") }
}

var recovery = MotionRecovery()
check(recovery.action(now: 0) == .waiting, "stopped stream does not retry")
check(!recovery.received(now: 0, sampleTimestamp: 0), "stopped stream rejects samples")
recovery.start(now: 100)
check(recovery.isStale(now: 100), "no samples is stale for neutral output")
check(recovery.action(now: 104.99) == .waiting, "initial permission/startup grace")
check(recovery.action(now: 105) == .retry(attempt: 1), "first stall restarts stream")
check(recovery.action(now: 105) == .waiting, "same watchdog instant cannot restart again")
check(recovery.action(now: 109.99) == .waiting, "retry receives its own grace")
check(recovery.action(now: 110) == .retry(attempt: 2), "second bounded retry")
check(recovery.action(now: 115) == .retry(attempt: 3), "third bounded retry")
check(recovery.action(now: 119.99) == .waiting, "final retry gets time to produce data")
check(recovery.action(now: 120) == .exhausted, "silent stream exhausts retry budget")
check(recovery.action(now: 1000) == .exhausted, "exhaustion never recreates more managers")
check(recovery.retryCount == 3, "retry limit preserved")
check(recovery.received(now: 1000, sampleTimestamp: 1), "late valid sample can recover exhausted stream")
check(recovery.retryCount == 0, "actual progress resets retry budget")
check(recovery.action(now: 1000) == .healthy, "fresh stream healthy")
check(recovery.action(now: 1000.99) == .healthy, "fresh interval remains healthy")
check(recovery.action(now: 1001) == .stale, "one second loss requests neutral state")
check(recovery.isStale(now: 1001), "neutral decision matches watchdog")
check(!recovery.received(now: 1002, sampleTimestamp: 1), "cached duplicate cannot make stream fresh")
check(!recovery.received(now: 1002, sampleTimestamp: 0.5), "out of order sample rejected")
check(recovery.action(now: 1005) == .retry(attempt: 1), "stalled established stream restarts")
check(!recovery.received(now: 1006, sampleTimestamp: 1), "cached sample after recreation cannot reset budget")
check(recovery.action(now: 1010) == .retry(attempt: 2), "duplicate leaves retry deadline intact")
check(recovery.received(now: 1010.1, sampleTimestamp: 2), "new sensor sample restores stream")
check(recovery.action(now: 1010.1) == .healthy, "recovered stream healthy")
check(!recovery.received(now: 1009, sampleTimestamp: 3), "backwards host time rejected")
check(recovery.action(now: 1009) == .waiting, "backwards watchdog does not consume retry")
check(recovery.retryCount == 0, "bad clock leaves retry budget unchanged")
for invalid in [Double.nan, .infinity, -.infinity, -1] {
    check(!recovery.received(now: 1011, sampleTimestamp: invalid), "invalid sensor timestamp rejected")
    check(!recovery.received(now: invalid, sampleTimestamp: 3), "invalid host timestamp rejected")
    check(recovery.action(now: invalid) == .waiting, "invalid watchdog time ignored")
}
recovery.stop()
check(!recovery.active && recovery.retryCount == 0 && recovery.lastReceivedAt == nil, "stop clears session")
check(recovery.action(now: 5000) == .waiting, "stop prevents all future retries")
recovery.start(now: 5000)
check(recovery.received(now: 5000, sampleTimestamp: 0), "new session accepts reset sensor clock")
recovery.start(now: .nan)
check(!recovery.active, "invalid start fails closed")
var noRetries = MotionRecovery(maximumRetries: 0)
noRetries.start(now: 0)
check(noRetries.action(now: 5) == .exhausted, "zero retry configuration supported")

print("\(checks) recovery checks, \(failures) failures")
if failures > 0 { exit(1) }
