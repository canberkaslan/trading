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
      return 'Giriş yapılamadı. Tekrar dene.';
  }
}

