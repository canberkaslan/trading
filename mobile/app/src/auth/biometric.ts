import * as LocalAuthentication from 'expo-local-authentication';

export async function authenticateBiometric(reason: string): Promise<boolean> {
  const hasHardware = await LocalAuthentication.hasHardwareAsync();
  const isEnrolled = await LocalAuthentication.isEnrolledAsync();
  if (!hasHardware || !isEnrolled) {
    return false;
  }

  const result = await LocalAuthentication.authenticateAsync({
    promptMessage: reason,
    fallbackLabel: 'Use passcode',
    cancelLabel: 'Cancel',
    disableDeviceFallback: false,
  });

  return result.success;
}
