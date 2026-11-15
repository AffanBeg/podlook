import Foundation

var checks = 0
var failures = 0
func check(_ condition: @autoclosure () -> Bool, _ message: String) {
    checks += 1
    if !condition() { failures += 1; print("FAIL: \(message)") }
}
let box = StreamMailbox<Int>()
let first = UUID()
box.reset(generation: first)
var scheduled = 0
for i in 0..<10_000 {
    if box.put(value: i, generation: first, arrival: Double(i) / 100) { scheduled += 1 }
}
check(scheduled == 1, "blocked consumer schedules only one drain")
check(box.statistics().count == 10_000, "arrivals counted despite replacement")
check(box.take()?.value == 9_999, "newest value returned instead of backlog")
check(box.take() == nil, "drain is destructive")
check(box.put(value: 10_000, generation: first, arrival: 100), "next drain schedules")
for invalid in [Double.nan, .infinity, -.infinity, -1, 99] {
    check(!box.put(value: -1, generation: first, arrival: invalid), "invalid or backwards arrival rejected")
}
check(!box.put(value: -2, generation: UUID(), arrival: 101), "old generation rejected")
check(box.take()?.value == 10_000, "rejected events leave payload intact")
_ = box.put(value: 1, generation: first, arrival: 101)
let second = UUID()
box.reset(generation: second)
check(box.take() == nil, "reset discards queued old sample")
check(!box.put(value: 2, generation: first, arrival: 102), "late callback rejected after reset")
check(box.put(value: 3, generation: second, arrival: 0), "new session accepts new clock epoch")
check(box.take()?.generation == second, "event retains generation")
_ = box.put(value: 4, generation: second, arrival: 2)
_ = box.put(value: 5, generation: second, arrival: 2.1)
check(abs(box.statistics().maxGap - 2) < 0.000001, "maximum callback gap retained")

// Main/UI deliberately waits without reading. Background ingress must finish
// without UI acknowledgements, and no stale backlog may accumulate.
let producer = DispatchQueue(label: "mailbox.test.producer")
let done = DispatchSemaphore(value: 0)
box.reset(generation: second)
producer.async {
    for i in 0..<50_000 {
        _ = box.put(value: i, generation: second, arrival: Double(i))
    }
    done.signal()
}
check(done.wait(timeout: .now() + 10) == .success, "producer progresses while main/UI blocked")
check(box.take()?.value == 49_999, "UI stall does not accumulate replay work")
check(box.statistics().count == 50_000, "arrival accounting survives UI stall")
print("\(checks) stream mailbox checks, \(failures) failures")
if failures > 0 { exit(1) }
