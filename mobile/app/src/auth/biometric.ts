import * as LocalAuthentication from 'expo-local-authentication';

import { resolveAuthMode, type AuthMode } from './authPolicy';

export interface BiometricResult {
  success: boolean;
  /** 'none' means the device has no lock at all — surface a distinct message. */
  mode: AuthMode;
}

/**
 * Gate a sensitive action behind device auth. Falls back to the OS passcode when
 * biometrics are unavailable/unenrolled but the device still has a passcode, so
 * users without Face/Touch ID are no longer locked out of approving orders.
 */
export async function authenticate(reason: string): Promise<BiometricResult> {
  const hasHardware = await LocalAuthentication.hasHardwareAsync();
  const enrolledLevel = await LocalAuthentication.getEnrolledLevelAsync();
  const mode = resolveAuthMode(hasHardware, enrolledLevel);

  if (mode === 'none') {
    return { success: false, mode };
  }

  const result = await LocalAuthentication.authenticateAsync({
    promptMessage: reason,
    fallbackLabel: 'Şifreyi kullan',
    cancelLabel: 'İptal',
    // Allow the OS passcode both as biometric fallback and as the sole factor.
    disableDeviceFallback: false,
  });

  return { success: result.success, mode };
}

/** Back-compat boolean helper for callers that only care about pass/fail. */
export async function authenticateBiometric(reason: string): Promise<boolean> {
  const { success } = await authenticate(reason);
  return success;
}
