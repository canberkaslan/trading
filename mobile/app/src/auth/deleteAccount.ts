/**
 * Account deletion, in the one order that cannot strand an account.
 *
 * Two systems hold the account — the backend's rows (`DELETE /v1/me`) and the
 * Firebase identity — and they are deleted by two calls that cannot be one
 * transaction. So the order is the whole design.
 *
 * 1. **Re-authenticate.** Firebase refuses `deleteUser` with
 *    `auth/requires-recent-login` once the last sign-in is a few minutes old,
 *    and sessions persist on the device, so for a returning user that refusal
 *    is the normal case. It used to arrive AFTER the backend had deleted
 *    everything, leaving a live Firebase account with no data behind it and no
 *    in-app way to remove it — exactly what App Store 5.1.1(v) forbids.
 *    Nothing is deleted until the password is proven.
 * 2. **Delete the server data.** It has to precede the identity: the server
 *    knows the caller only by this user's ID token, and once the Firebase user
 *    is gone the API client falls back to the shared bearer — a `DELETE /v1/me`
 *    sent then would remove the shared identity's rows, not this user's.
 * 3. **Delete the identity.** A fresh sign-in is good for five minutes, so what
 *    can still fail here is transient (the network). The user is still signed
 *    in and `DELETE /v1/me` is idempotent, so running the whole flow again
 *    finishes the job — which is what the message tells them to do.
 *
 * Kept free of Firebase and React so the order is unit-tested, not just
 * described.
 */

import { statusOf } from '@/utils/apiError';

export type DeleteAccountStage = 'reauth' | 'server' | 'identity';

export interface DeleteAccountSteps {
  reauthenticate: (password: string) => Promise<void>;
  deleteServerData: () => Promise<unknown>;
  deleteIdentity: () => Promise<void>;
}

export type DeleteAccountOutcome =
  | { ok: true }
  | { ok: false; stage: DeleteAccountStage; message: string };

export async function deleteAccountInOrder(
  password: string,
  steps: DeleteAccountSteps,
): Promise<DeleteAccountOutcome> {
  const ordered: [DeleteAccountStage, () => Promise<unknown>][] = [
    ['reauth', () => steps.reauthenticate(password)],
    ['server', () => steps.deleteServerData()],
    ['identity', () => steps.deleteIdentity()],
  ];
  for (const [stage, step] of ordered) {
    try {
      await step();
    } catch (e) {
      return { ok: false, stage, message: deleteAccountErrorTr(stage, e) };
    }
  }
  return { ok: true };
}

/** Said whenever a failure happened before anything was deleted. */
const NOTHING_DELETED = 'Hesabın silinmedi.';

/**
 * What failed, in Turkish, and — the part that matters — what state the
 * account is in now. A deletion that half-happened must say which half.
 */
export function deleteAccountErrorTr(stage: DeleteAccountStage, e: unknown): string {
  const code = (e as { code?: string } | null)?.code ?? '';
  switch (stage) {
    case 'reauth':
      return reauthErrorTr(code);
    case 'server':
      // ky throws an HTTPError carrying the response when the server answered;
      // no status means nothing answered.
      return statusOf(e) === null
        ? `Sunucuya ulaşılamadı. Bağlantını kontrol edip tekrar dene. ${NOTHING_DELETED}`
        : `Sunucudaki veriler silinemedi. Tekrar dene. ${NOTHING_DELETED}`;
    case 'identity':
      switch (code) {
        case 'auth/requires-recent-login':
          return 'Verilerin silindi ama giriş hesabın silinemedi: doğrulamanın süresi doldu. Şifreni girip tekrar dene.';
        case 'auth/network-request-failed':
          return 'Verilerin silindi ama giriş hesabın silinemedi: sunucuya ulaşılamadı. Bağlantını kontrol edip tekrar dene.';
        default:
          return 'Verilerin silindi ama giriş hesabın silinemedi. Tekrar dene.';
      }
  }
}

/**
 * Unlike the sign-in form, this one may say the PASSWORD is what was wrong.
 * `signInErrorTr` collapses user-not-found and wrong-password so the form is
 * not an account-existence oracle; here the account is the one already signed
 * in, so there is nothing to leak and "Şifre hatalı" is just the truth.
 */
function reauthErrorTr(code: string): string {
  switch (code) {
    case 'auth/invalid-credential':
    case 'auth/wrong-password':
      return `Şifre hatalı. ${NOTHING_DELETED}`;
    case 'auth/missing-password':
      return 'Hesabı silmek için şifreni gir.';
    case 'auth/too-many-requests':
      return `Çok fazla deneme yapıldı. Biraz bekleyip tekrar dene. ${NOTHING_DELETED}`;
    case 'auth/network-request-failed':
      return `Sunucuya ulaşılamadı. Bağlantını kontrol edip tekrar dene. ${NOTHING_DELETED}`;
    case 'auth/user-signed-out':
    case 'auth/user-token-expired':
    case 'auth/user-not-found':
    case 'auth/user-disabled':
      return 'Oturumun geçersiz. Çıkış yapıp e-posta ve şifre ile yeniden giriş yap, sonra tekrar dene.';
    default:
      return `Kimliğin doğrulanamadı. Tekrar dene. ${NOTHING_DELETED}`;
  }
}
