/**
 * Firebase error codes, in Turkish, without telling an attacker which half was
 * wrong.
 *
 * `auth/user-not-found` and `auth/wrong-password` get the SAME message on
 * purpose: distinguishing them turns the login form into an account-existence
 * oracle. Newer Firebase versions already collapse both into
 * `auth/invalid-credential` for this reason; this handles either.
 */
export function signInErrorTr(e: unknown): string {
  const code = (e as { code?: string } | null)?.code ?? '';
  switch (code) {
    case 'auth/invalid-credential':
    case 'auth/user-not-found':
    case 'auth/wrong-password':
      return 'E-posta veya şifre hatalı.';
    case 'auth/invalid-email':
      return 'E-posta adresi geçersiz.';
    case 'auth/user-disabled':
      return 'Bu hesap devre dışı bırakılmış.';
    case 'auth/too-many-requests':
      return 'Çok fazla deneme yapıldı. Biraz bekleyip tekrar dene.';
    case 'auth/network-request-failed':
      return 'Sunucuya ulaşılamadı. Bağlantını kontrol et.';
    case 'auth/email-already-in-use':
      // Sign-up only. It does say the address has an account, but sign-up is
      // behind an invite code and Firebase answers the same to anyone who asks
      // it directly — what the reader needs is to switch to the sign-in tab.
      return 'Bu e-posta ile zaten bir hesap var. Giriş yap sekmesinden devam et.';
    case 'auth/weak-password':
    case 'auth/password-does-not-meet-requirements':
      // Sign-up only: the project's policy is 6 characters minimum.
      return 'Şifre en az 6 karakter olmalı.';
    case 'auth/internal-error':
      // Firebase's catch-all. It is what a failed keystore write looked like
      // (PR #97): the real cause rides in `customData.originalError` and the
      // message says nothing useful, so the cause is logged in dev builds.
      if (__DEV__) {
        console.warn(
          '[auth] auth/internal-error',
          (e as { customData?: { originalError?: unknown } }).customData?.originalError,
        );
      }
      return 'Beklenmeyen bir hata oluştu. Uygulamayı kapatıp açarak tekrar dene.';
    default:
      // An unmapped outcome carries its code, short, so a screenshot names
      // the cause. EAS Insights records nothing for this app and every
      // failure report so far arrived as a screenshot of this exact line,
      // which on its own could not tell a stale bundle from a new bug. The
      // credential cases above stay code-free, so this is no account oracle.
      return `Giriş yapılamadı (${errorTag(e, code)}). Tekrar dene.`;
  }
}

/** `auth/operation-not-allowed` -> `operation-not-allowed`; no code -> the error's name. */
function errorTag(e: unknown, code: unknown): string {
  if (typeof code === 'string' && code) return code.replace(/^auth\//, '').slice(0, 40);
  const name = e instanceof Error ? e.name : '';
  return name && name !== 'Error' ? name.slice(0, 40) : 'kod yok';
}

