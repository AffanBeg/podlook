import Foundation

var checks = 0
var failures = 0

func check(_ condition: @autoclosure () -> Bool, _ message: String) {
    checks += 1
    if !condition() { failures += 1; print("FAIL: \(message)") }
}

func close(_ actual: Double, _ expected: Double, _ message: String, tolerance: Double = 1e-8) {
    check(actual.isFinite && abs(actual - expected) <= tolerance, "\(message): \(actual) != \(expected)")
}

func rotation(_ degrees: Double, axis: Int) -> Quaternion {
    let half = degrees * .pi / 360
    return Quaternion(w: cos(half), x: axis == 0 ? sin(half) : 0,
                      y: axis == 1 ? sin(half) : 0, z: axis == 2 ? sin(half) : 0)!
}

func composed(yaw: Double, pitch: Double, roll: Double) -> Quaternion {
    rotation(yaw, axis: 2) * rotation(pitch, axis: 0) * rotation(roll, axis: 1)
}

func checkPose(_ pose: Pose, yaw: Double, pitch: Double, roll: Double, _ message: String) {
    close(wrapDegrees(pose.yaw - yaw), 0, "\(message) yaw")
    close(pose.pitch, pitch, "\(message) pitch")
    close(wrapDegrees(pose.roll - roll), 0, "\(message) roll")
}

check(Quaternion(w: 0, x: 0, y: 0, z: 0) == nil, "reject zero quaternion")
for bad in [Double.nan, .infinity, -.infinity] {
    check(Quaternion(w: bad, x: 0, y: 0, z: 0) == nil, "reject invalid w")
    check(Quaternion(w: 1, x: bad, y: 0, z: 0) == nil, "reject invalid x")
    check(Quaternion(w: 1, x: 0, y: bad, z: 0) == nil, "reject invalid y")
    check(Quaternion(w: 1, x: 0, y: 0, z: bad) == nil, "reject invalid z")
}
for magnitude in [1e-300, 1.0, 1e300] {
    let q = Quaternion(w: magnitude, x: magnitude, y: magnitude, z: magnitude)!
    close(q.w, 0.5, "stable quaternion normalization")
    checkPose((q.inverse * q).headAngles, yaw: 0, pitch: 0, roll: 0, "inverse")
}

checkPose(Quaternion.identity.headAngles, yaw: 0, pitch: 0, roll: 0, "identity")
for value in [-75.0, -30, 0, 30, 75] {
    checkPose(rotation(value, axis: 2).headAngles, yaw: value, pitch: 0, roll: 0, "Z is yaw")
    checkPose(rotation(value, axis: 0).headAngles, yaw: 0, pitch: value, roll: 0, "X is pitch")
    checkPose(rotation(value, axis: 1).headAngles, yaw: 0, pitch: 0, roll: value, "Y is roll")
}
for yaw in [-170.0, -45, 0, 45, 170] {
    for pitch in [-70.0, 0, 70] {
        for roll in [-60.0, 0, 60] {
            let q = composed(yaw: yaw, pitch: pitch, roll: roll)
            checkPose(q.headAngles, yaw: yaw, pitch: pitch, roll: roll, "combined Euler round trip")
            let opposite = Quaternion(w: -q.w, x: -q.x, y: -q.y, z: -q.z)!
            checkPose(opposite.headAngles, yaw: yaw, pitch: pitch, roll: roll, "q and -q equivalence")
        }
    }
}
for pitch in [-90.0, 90.0] {
    let pose = composed(yaw: 20, pitch: pitch, roll: 15).headAngles
    check(pose.isFinite, "singularity stays finite")
    close(pose.pitch, pitch, "singularity pitch", tolerance: 1e-6)
    let reconstructed = composed(yaw: pose.yaw, pitch: pose.pitch, roll: pose.roll)
    let original = composed(yaw: 20, pitch: pitch, roll: 15)
    close(abs(reconstructed.w * original.w + reconstructed.x * original.x +
              reconstructed.y * original.y + reconstructed.z * original.z), 1,
          "singularity equivalent rotation", tolerance: 1e-6)
}

var raw = PoseTracker(smoothingSeconds: 0)
let neutral = composed(yaw: 65, pitch: 12, roll: -8)
check(raw.update(neutral, timestamp: 1) == .zero, "first sample is neutral")
let relative = composed(yaw: 22, pitch: -11, roll: 7)
checkPose(raw.update(neutral * relative, timestamp: 1.01)!, yaw: 22, pitch: -11, roll: 7,
          "nonidentity neutral uses relative multiplication in correct order")
check(raw.update(.identity, timestamp: 1.01) == nil, "duplicate sample ignored")
check(raw.update(.identity, timestamp: 0.5) == nil, "out of order sample ignored")
check(raw.update(.identity, timestamp: -.infinity) == nil, "invalid timestamp ignored")
check(raw.update(.identity, timestamp: .nan) == nil, "NaN timestamp ignored")
check(raw.update(.identity, timestamp: -1) == nil, "negative timestamp ignored")
checkPose(raw.update(neutral * relative, timestamp: 1.02)!, yaw: 22, pitch: -11, roll: 7,
          "invalid samples leave state untouched")
raw.recenter()
check(raw.update(neutral * relative, timestamp: 2) == .zero, "manual recenter")
check(raw.update(neutral, timestamp: 4) == .zero, "stale stream recenters")

// Calibrating near the global yaw seam must produce a small relative turn, not
// the almost-full revolution produced by subtracting absolute Euler angles.
var seamNeutral = PoseTracker(smoothingSeconds: 0)
_ = seamNeutral.update(rotation(179, axis: 2), timestamp: 0)
checkPose(seamNeutral.update(rotation(-179, axis: 2), timestamp: 0.01)!,
          yaw: 2, pitch: 0, roll: 0, "neutral yaw straddles global seam")
let seamAttitude = rotation(-179, axis: 2)
let oppositeSeamAttitude = Quaternion(w: -seamAttitude.w, x: -seamAttitude.x,
                                     y: -seamAttitude.y, z: -seamAttitude.z)!
checkPose(seamNeutral.update(oppositeSeamAttitude, timestamp: 0.02)!,
          yaw: 2, pitch: 0, roll: 0, "sensor quaternion sign flip keeps relative pose")

var smooth = PoseTracker(smoothingSeconds: 0.1)
_ = smooth.update(.identity, timestamp: 0)
let filtered = smooth.update(rotation(30, axis: 2), timestamp: 0.1)!
close(filtered.yaw, 30 * (1 - exp(-1)), "time-based smoothing")
smooth.recenter()
check(smooth.update(.identity, timestamp: 1) == .zero, "recenter clears smoothing")

// Equal elapsed time should yield the same response at different sample rates.
var slow = PoseTracker(smoothingSeconds: 0.2)
var fast = PoseTracker(smoothingSeconds: 0.2)
_ = slow.update(.identity, timestamp: 0)
_ = fast.update(.identity, timestamp: 0)
var slowPose = Pose.zero
var fastPose = Pose.zero
for step in 1...10 { slowPose = slow.update(rotation(25, axis: 2), timestamp: Double(step) / 10)! }
for step in 1...100 { fastPose = fast.update(rotation(25, axis: 2), timestamp: Double(step) / 100)! }
close(slowPose.yaw, fastPose.yaw, "smoothing independent of sample rate")

var seam = PoseTracker(smoothingSeconds: 0.01)
_ = seam.update(.identity, timestamp: 0)
_ = seam.update(rotation(179, axis: 2), timestamp: 0.5)
let across = seam.update(rotation(-179, axis: 2), timestamp: 0.51)!
check(abs(across.yaw) > 178, "yaw smoothing crosses seam, not zero")
close(wrapDegrees(181), -179, "positive wrap")
close(wrapDegrees(-181), 179, "negative wrap")
close(wrapDegrees(720), 0, "multiple turn wrap")

let packet = try Pose(yaw: 1, pitch: -2, roll: 0.5).openTrackPacket()
check(packet.count == 48, "OpenTrack datagram is exactly 48 bytes")
let expected: [UInt8] = Array(repeating: 0, count: 24) + [
    0, 0, 0, 0, 0, 0, 0xf0, 0x3f, // +1 double LE
    0, 0, 0, 0, 0, 0, 0x00, 0xc0, // -2 double LE
    0, 0, 0, 0, 0, 0, 0xe0, 0x3f  // +0.5 double LE
]
check(Array(packet) == expected, "wire format matches independent IEEE754 fixture")
let neutralPacket = try Pose.zero.openTrackPacket()
check(Array(neutralPacket) == Array(repeating: UInt8(0), count: 48), "neutral packet")
for bad in [Double.nan, .infinity, -.infinity] {
    for pose in [Pose(yaw: bad, pitch: 0, roll: 0), Pose(yaw: 0, pitch: bad, roll: 0),
                 Pose(yaw: 0, pitch: 0, roll: bad)] {
        do { _ = try pose.openTrackPacket(); check(false, "nonfinite packet must throw") }
        catch PoseError.nonFinitePose { check(true, "reject nonfinite packet") }
        catch { check(false, "unexpected error type") }
    }
}

print("\(checks) checks, \(failures) failures")
if failures > 0 { exit(1) }
