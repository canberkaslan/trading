// Pure device-auth policy — decides how to gate a money action (approve/kill)
// given the device's biometric hardware + enrolled security level. Kept free of
// native imports so it is unit-testable and shared by biometric.ts.
//
// Mirrors expo-local-authentication's SecurityLevel enum (SDK 52):
//   NONE = 0, SECRET = 1 (device passcode/PIN), BIOMETRIC_WEAK = 2, BIOMETRIC_STRONG = 3
export const SECURITY_LEVEL = {
  NONE: 0,
  SECRET: 1,
  BIOMETRIC_WEAK: 2,
  BIOMETRIC_STRONG: 3,
} as const;

export type AuthMode = 'biometric' | 'passcode' | 'none';

/**
 * Decide which authentication factor to use.
 *
 * The old logic bailed to `none` whenever biometrics were not enrolled, which
 * permanently locked users with a device passcode (but no Face/Touch ID) out of
 * approving orders. We now fall back to the OS passcode when the device is
 * secured at SECRET level, and only refuse when there is no device lock at all.
 */
export function resolveAuthMode(hasHardware: boolean, enrolledLevel: number): AuthMode {
  if (hasHardware && enrolledLevel >= SECURITY_LEVEL.BIOMETRIC_WEAK) {
    return 'biometric';
  }
  if (enrolledLevel >= SECURITY_LEVEL.SECRET) {
    return 'passcode';
  }
  return 'none';
}

/**
 * How an order approval is confirmed on this platform.
 *
 * `device` — the OS lock (Face/Touch ID or passcode) via expo-local-authentication.
 * `hold`   — the design's no-device-lock variant: a deliberate 1.2 s press-and-hold.
 *
 * Only web gets `hold`. expo-local-authentication's web build hard-codes "no
 * hardware, nothing enrolled", so on web every approval ended in "set up a
 * device lock" — advice a browser user cannot follow. A native device with no
 * lock keeps being refused: there the OS lock exists and the user can set it.
 * The server-side gate (signed-in admin) is the same on both.
 */
export type ApprovalGate = 'device' | 'hold';

export function approvalGateFor(os: string): ApprovalGate {
  return os === 'web' ? 'hold' : 'device';
}

/** The design's hold duration for the no-device-lock confirmation. */
export const HOLD_TO_CONFIRM_MS = 1200;
