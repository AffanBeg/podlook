import Foundation

enum PoseError: Error {
    case nonFinitePose
}

/// Unit quaternion in (w, x, y, z) order. The public initializer always normalizes.
struct Quaternion: Equatable {
    let w: Double
    let x: Double
    let y: Double
    let z: Double

    init?(w: Double, x: Double, y: Double, z: Double) {
        let values = [w, x, y, z]
        guard values.allSatisfy({ $0.isFinite }) else { return nil }
        // Scale first so both very large and very small finite inputs normalize safely.
        let scale = values.map { abs($0) }.max()!
        guard scale > 0 else { return nil }
        let scaled = values.map { $0 / scale }
        let length = sqrt(scaled.reduce(0) { $0 + $1 * $1 })
        self.w = scaled[0] / length
        self.x = scaled[1] / length
        self.y = scaled[2] / length
        self.z = scaled[3] / length
    }

    static let identity = Quaternion(w: 1, x: 0, y: 0, z: 0)!

    var inverse: Quaternion { Quaternion(w: w, x: -x, y: -y, z: -z)! }

    static func * (a: Quaternion, b: Quaternion) -> Quaternion {
        // Products of finite unit quaternions remain nonzero and finite.
        Quaternion(
            w: a.w * b.w - a.x * b.x - a.y * b.y - a.z * b.z,
            x: a.w * b.x + a.x * b.w + a.y * b.z - a.z * b.y,
            y: a.w * b.y - a.x * b.z + a.y * b.w + a.z * b.x,
            z: a.w * b.z + a.x * b.y - a.y * b.x + a.z * b.w
        )!
    }

    /// Decompose Rz(yaw) * Rx(pitch) * Ry(roll); radians become degrees.
    var headAngles: Pose {
        let r01 = 2 * (x * y - w * z)
        let r11 = 1 - 2 * (x * x + z * z)
        let r20 = 2 * (x * z - w * y)
        let r21 = 2 * (y * z + w * x)
        let r22 = 1 - 2 * (x * x + y * y)
        let pitch = asin(max(-1, min(1, r21)))
        let yaw: Double
        let roll: Double
        if abs(cos(pitch)) < 1e-7 {
            // At +/-90 degrees pitch, yaw and roll are not separately identifiable.
            // Choose roll=0 and keep the equivalent combined yaw deterministic.
            let r00 = 1 - 2 * (y * y + z * z)
            let r10 = 2 * (x * y + w * z)
            yaw = atan2(r10, r00)
            roll = 0
        } else {
            yaw = atan2(-r01, r11)
            roll = atan2(-r20, r22)
        }
        let degrees = 180 / Double.pi
        return Pose(yaw: yaw * degrees, pitch: pitch * degrees, roll: roll * degrees)
    }
}

struct Pose: Equatable {
    var yaw: Double
    var pitch: Double
    var roll: Double

    static let zero = Pose(yaw: 0, pitch: 0, roll: 0)

    var isFinite: Bool { yaw.isFinite && pitch.isFinite && roll.isFinite }

    /// OpenTrack UDP input: x,y,z in cm then yaw,pitch,roll in degrees.
    /// AirPods measure rotation only, so translation is always zero.
    func openTrackPacket() throws -> Data {
        guard isFinite else { throw PoseError.nonFinitePose }
        var data = Data(capacity: 48)
        for value in [0.0, 0.0, 0.0, yaw, pitch, roll] {
            var bits = value.bitPattern.littleEndian
            withUnsafeBytes(of: &bits) { data.append(contentsOf: $0) }
        }
        return data
    }
}

func wrapDegrees(_ angle: Double) -> Double {
    guard angle.isFinite else { return .nan }
    let value = angle.truncatingRemainder(dividingBy: 360)
    if value >= 180 { return value - 360 }
    if value < -180 { return value + 360 }
    return value
}

/// Neutral-relative orientation and time-based exponential smoothing.
/// Call with the sensor's monotonic timestamp, not wall-clock time.
struct PoseTracker {
    private var reference: Quaternion?
    private var previousTime: Double?
    private var filtered = Pose.zero
    private let smoothingSeconds: Double
    private let staleSeconds: Double
    private let resetOnGap: Bool

    init(smoothingSeconds: Double = 0.04, staleSeconds: Double = 1.0, resetOnGap: Bool = true) {
        self.smoothingSeconds = smoothingSeconds.isFinite ? max(0, smoothingSeconds) : 0.04
        self.staleSeconds = staleSeconds.isFinite && staleSeconds > 0 ? staleSeconds : 1.0
        self.resetOnGap = resetOnGap
    }

    mutating func recenter() {
        reference = nil
        previousTime = nil
        filtered = .zero
    }

    /// A missing/invalid sample never changes state. After a long gap, establish a
    /// fresh neutral instead of making the game camera jump to an old reference.
    mutating func update(_ attitude: Quaternion, timestamp: Double) -> Pose? {
        guard timestamp.isFinite, timestamp >= 0 else { return nil }
        if let previousTime, timestamp <= previousTime { return nil }
        if reference == nil || (resetOnGap && previousTime.map({ timestamp - $0 > staleSeconds }) == true) {
            reference = attitude
            previousTime = timestamp
            filtered = .zero
            return .zero
        }
        let dt = timestamp - previousTime!
        previousTime = timestamp
        let target = (reference!.inverse * attitude).headAngles
        let alpha = smoothingSeconds == 0 ? 1 : 1 - exp(-dt / smoothingSeconds)
        filtered.yaw = wrapDegrees(filtered.yaw + alpha * wrapDegrees(target.yaw - filtered.yaw))
        filtered.pitch += alpha * (target.pitch - filtered.pitch)
        filtered.roll = wrapDegrees(filtered.roll + alpha * wrapDegrees(target.roll - filtered.roll))
        return filtered
    }
}
