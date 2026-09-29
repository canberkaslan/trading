/**
 * Uppercasing that knows which language the string is in.
 *
 * `textTransform: 'uppercase'` is NOT used for copy in this app, and a test
 * enforces it. The platform applies it with the DEVICE locale, not the
 * string's: Android on a Turkish phone turns the English rating "Underweight"
 * into "UNDERWEİGHT", and an English phone turns the Turkish label "Giriş" into
 * "GIRIŞ" (dotless where Turkish needs the dot). Both shipped. The language a
 * string is written in is known here, at the call site — the renderer can only
 * guess — so casing happens in JS against that language.
 *
 * Deliberately not `toLocaleUpperCase(lang)`: that routes through the JS
 * engine's ICU, and whether Hermes carries full locale data differs between
 * builds. The only casing rule Turkish changes is i → İ (ı → I is already the
 * default mapping), so it is stated here and behaves the same on every engine.
 */
export function toCaps(text: string, lang: string): string {
  if (lang.toLowerCase().startsWith('tr')) return text.replace(/i/g, 'İ').toUpperCase();
  return text.toUpperCase();
}
