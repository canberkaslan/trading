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
    default:
      return 'Giriş yapılamadı. Tekrar dene.';
  }
}

