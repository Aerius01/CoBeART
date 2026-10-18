// Hub velocity stage: derives per-role motion values from successive pose_frames, on the source clock.
// Port of the former Python MetricsTracker (cobeart/input/optitrack/metrics.py), keyed by role.

/** Smallest source-time step (s) the stage divides by; closer poses keep the previous velocities. */
const MIN_DT_S = 0.0005;

/** @typedef {{ x: number, y: number, z: number, qx: number, qy: number, qz: number, qw: number }} Pose */
/**
 * @typedef {Pose & { vx: number, vy: number, vz: number, wx: number, wy: number, wz: number,
 *   abs_vel: number, norm_abs_vel: number }} Body
 */
/** @typedef {{ max_vel: number, history_window: number, max_gap_s: number }} MetricsConfig */
/** @typedef {[number, number, number]} Vec3 */

const ZERO = /** @type {Vec3} */ ([0, 0, 0]);

/**
 * Horizontal speed from the x and y velocity components.
 * @param {number} vx
 * @param {number} vy
 * @returns {number}
 */
const absVelocity = (vx, vy) => Math.hypot(vx, vy);

/**
 * abs_vel scaled by max_vel and capped at 1.
 * @param {number} absVel
 * @param {number} maxVel
 * @returns {number}
 */
const normalizeAbsVelocity = (absVel, maxVel) => Math.min(absVel / maxVel, 1.0);

/**
 * Arena-frame angular velocity (deg/s) as the rotation vector of qNow * qPrev^-1 over dt.
 * @param {Pose} qPrev unit quaternion (qx, qy, qz, qw)
 * @param {Pose} qNow unit quaternion (qx, qy, qz, qw)
 * @param {number} dt seconds
 * @returns {Vec3}
 */
function angularVelocity(qPrev, qNow, dt) {
  const { qx: ax, qy: ay, qz: az, qw: aw } = qNow;
  // The conjugate of a unit quaternion is its inverse.
  const [bx, by, bz, bw] = [-qPrev.qx, -qPrev.qy, -qPrev.qz, qPrev.qw];
  let dw = aw * bw - ax * bx - ay * by - az * bz;
  let dx = aw * bx + ax * bw + ay * bz - az * by;
  let dy = aw * by - ax * bz + ay * bw + az * bx;
  let dz = aw * bz + ax * by - ay * bx + az * bw;
  if (dw < 0) {
    // q and -q are the same rotation; take the short way round.
    [dw, dx, dy, dz] = [-dw, -dx, -dy, -dz];
  }
  const sinHalf = Math.sqrt(dx * dx + dy * dy + dz * dz);
  if (sinHalf < 1e-12) return ZERO;
  const angle = 2.0 * Math.atan2(sinHalf, dw);
  const scale = (angle * 180) / Math.PI / (sinHalf * dt);
  return [dx * scale, dy * scale, dz * scale];
}

/**
 * Builds an outgoing body from a pose and its motion values (field order as in frame.schema.json).
 * @param {Pose} pose
 * @param {{ velocity: Vec3, angular: Vec3, absVel: number, normAbsVel: number }} motion
 * @returns {Body}
 */
const toBody = ({ x, y, z, qx, qy, qz, qw }, { velocity, angular, absVel, normAbsVel }) => ({
  x, y, z, qx, qy, qz, qw,
  vx: velocity[0], vy: velocity[1], vz: velocity[2],
  wx: angular[0], wy: angular[1], wz: angular[2],
  abs_vel: absVel, norm_abs_vel: normAbsVel
});

const STILL = Object.freeze({ velocity: ZERO, angular: ZERO, absVel: 0, normAbsVel: 0 });

/**
 * Validates the metrics config and throws a RangeError naming the bad key.
 * @param {MetricsConfig} metrics
 */
function checkMetrics({ max_vel, history_window, max_gap_s }) {
  if (!(Number.isFinite(max_vel) && max_vel > 0)) throw new RangeError(`metrics.max_vel must be > 0, got ${max_vel}`);
  if (!(Number.isInteger(history_window) && history_window >= 1)) {
    throw new RangeError(`metrics.history_window must be an integer >= 1, got ${history_window}`);
  }
  if (!(Number.isFinite(max_gap_s) && max_gap_s > 0)) {
    throw new RangeError(`metrics.max_gap_s must be > 0, got ${max_gap_s}`);
  }
}

/**
 * Creates a velocity stage with per-role state. Not reentrant: feed it pose_frames in arrival order.
 * A role restarts (all derived values 0) on first sighting, after a source-time gap above `max_gap_s`, or when the
 * source clock goes backwards, which resets every role.
 * @param {MetricsConfig} metrics
 * @param {{ minDtS?: number }} [options]
 * @returns {{ track: (sourceTimeS: number, poses: Record<string, Pose>) => Record<string, Body> }}
 */
function createMotionTracker(metrics, { minDtS = MIN_DT_S } = {}) {
  checkMetrics(metrics);
  const { max_vel: maxVel, history_window: windowLength, max_gap_s: maxGapS } = metrics;
  /** @type {Map<string, { pose: Pose, time: number, history: number[], last: typeof STILL }>} */
  const states = new Map();
  let lastSourceTime = -Infinity;

  /** Records one role's pose at `time` (s) and returns its body. */
  const updateRole = (role, time, pose) => {
    const state = states.get(role);
    const dt = state ? time - state.time : 0;
    if (!state || dt < 0 || dt > maxGapS) {
      // The window starts full of zeros so a newly appearing still body does not report speed.
      states.set(role, { pose, time, history: new Array(windowLength).fill(0), last: STILL });
      return toBody(pose, STILL);
    }
    if (dt < minDtS) {
      // Too close to the reference pose to divide by: report the new pose with the previous motion values and
      // keep the reference, so the next pose differentiates over the full interval.
      return toBody(pose, state.last);
    }
    const prev = state.pose;
    const velocity = /** @type {Vec3} */ ([(pose.x - prev.x) / dt, (pose.y - prev.y) / dt, (pose.z - prev.z) / dt]);
    const angular = angularVelocity(prev, pose, dt);
    const absVel = absVelocity(velocity[0], velocity[1]);
    const history = [...state.history.slice(1), normalizeAbsVelocity(absVel, maxVel)];
    // Zero only after a full window of zeros, otherwise hold the window maximum.
    const normAbsVel = history.every((v) => v === 0) ? 0 : Math.max(...history);
    const last = { velocity, angular, absVel, normAbsVel };
    states.set(role, { pose, time, history, last });
    return toBody(pose, last);
  };

  return {
    /** Derives the frame bodies for one pose_frame (`source_time_s`, `bodies`). */
    track(sourceTimeS, poses) {
      if (sourceTimeS < lastSourceTime) states.clear();
      lastSourceTime = sourceTimeS;
      return Object.fromEntries(Object.entries(poses).map(([role, pose]) => [role, updateRole(role, sourceTimeS, pose)]));
    }
  };
}

module.exports = { MIN_DT_S, createMotionTracker, angularVelocity };
