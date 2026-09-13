/**
 * Classifying what a failed request actually means.
 *
 * Every screen used to render the same line for every failure —
 * "Sunucuya ulaşılamıyor", check your connection. For a 401 that is simply
 * false: the server answered, promptly and correctly. What is missing is the
 * bearer. Telling an operator to check their network when the fix is to paste
 * a token into Settings sends them to debug the wrong layer, and the "Tekrar
 * dene" button underneath makes it worse — retrying an unauthenticated request
 * returns the identical 401 forever.
 *
 * So failures are split by what the operator can DO about them:
 *
 *   auth     the request was understood and refused — fix the token
 *   network  nothing answered — fix the connection, then retry
 *
 * `ky` throws an `HTTPError` carrying the `Response`, but this checks the shape
 * rather than using `instanceof`. Two copies of ky in a bundle produce two
 * distinct classes, and an `instanceof` that silently returns false would fall
 * back to the network message — the exact bug this module exists to remove.
 *
 * The API answers 401 for every auth case (missing header, malformed bearer,
 * bad JWT — see agent/api/deps.py `require_token`) and never 403, so 401 is the
 * whole auth surface. 403 is treated as auth too, cheaply, so that a future
 * server-side change cannot silently reopen the same wrong-message hole.
 */

/** HTTP status carried by a thrown request error, when there is one. */
export function statusOf(err: unknown): number | null {
  if (typeof err !== 'object' || err === null) return null;
  const response = (err as { response?: unknown }).response;
  if (typeof response !== 'object' || response === null) return null;
  const status = (response as { status?: unknown }).status;
  return typeof status === 'number' ? status : null;
}

/** The server refused the request for want of a valid credential. */
export function isAuthError(err: unknown): boolean {
  const status = statusOf(err);
  return status === 401 || status === 403;
}

/**
 * Which auth message to show.
 *
 * The server's 401 body distinguishes "missing bearer token" from
 * "invalid token", but reading it costs an async hop the render path does not
 * have. The client already knows the same fact for free: if no token is
 * stored, it was never sent. That gives the operator the right sentence
 * without a round trip.
 */
export type AuthErrorKind = 'missing' | 'invalid';

export function authErrorKind(err: unknown, hasStoredToken: boolean): AuthErrorKind | null {
  if (!isAuthError(err)) return null;
  return hasStoredToken ? 'invalid' : 'missing';
}
