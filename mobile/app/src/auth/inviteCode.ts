/**
 * Davet kodu kapisi.
 *
 * NE OLDUGU KONUSUNDA DURUST OLMAK GEREK: bu bir HIZ TUMSEGI, kilit degil.
 * Beklenen kod `EXPO_PUBLIC_*` oldugu icin Babel tarafindan derleme aninda
 * pakete SABIT olarak gomuluyor; paketi acan biri okuyabilir. Hash'lemek de
 * ise yaramazdi: kisa bir kod kaba kuvvetle cozulur ve kontrolu yamalamak
 * zaten mumkun. Durdurdugu sey gercek -- TestFlight/APK linkinin elden ele
 * dolasip yabancilarin hesap acmasi. Durdurmadigi sey de gercek: kararli biri.
 *
 * Gercek kilit sunucuda: emir onaylama `require_admin` istiyor, yani kayit
 * olan bir yabanci ISLEM YAPAMAZ. Ama okuyabilir (bekleyen emirler, ajan
 * kararlari, portfoy) -- kapinin savundugu sey tam olarak bu.
 *
 * Sunucu tarafli dogrulama istenirse backend'e bir uc eklenmeli; o zaman kod
 * pakete hic girmez ve burasi cagriyi yapan ince bir sarmalayiciya doner.
 */

export interface InviteGate {
  /** Kayit akisi gosterilsin mi. */
  readonly enabled: boolean;
  /** Girilen kod beklenenle eslesiyor mu. */
  isValid(code: string): boolean;
}

/**
 * Saf kurucu. Beklenen kodu PARAMETRE alir, ortamdan kendi okumaz: Babel
 * `process.env.EXPO_PUBLIC_*` ifadelerini derleme aninda sabitle degistirdigi
 * icin modul icinden okunan bir deger testte degistirilemez.
 */
export function makeInviteGate(expectedRaw: string | undefined | null): InviteGate {
  const expected = (expectedRaw ?? '').trim();
  const fold = (s: string) => s.trim().toLocaleUpperCase('en-US');
  return {
    enabled: expected.length > 0,
    isValid: (code: string) => expected.length > 0 && fold(code) === fold(expected),
  };
}

// Fail-closed: kod YAPILANDIRILMAMISSA kayit akisi hic acilmaz. Bu bilincli --
// unutulmus bir ortam degiskeni "kapi yok" anlamina gelseydi, kapiyi eklemek
// guvenligi DUSURURDU. Kodsuz uretilmis her build bugunku gibi davranir.
const gate = makeInviteGate(process.env.EXPO_PUBLIC_INVITE_CODE);

export const signUpEnabled = (): boolean => gate.enabled;
export const isInviteCodeValid = (code: string): boolean => gate.isValid(code);
