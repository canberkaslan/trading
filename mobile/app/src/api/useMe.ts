/**
 * Who the signed-in user is, and whether they may take privileged actions.
 *
 * The server now refuses order approval, rejection, cancellation, the kill
 * switch and starting an analysis unless the caller is on the administrator
 * list. Without asking, the app would keep drawing those controls and the
 * answer would arrive as a 403 after the decision had been made — the same
 * failure this codebase already fixed once, when the kill switch stayed armed
 * and tappable while its state was unknown. An unusable control invites a tap
 * to find out.
 *
 * `is_admin` is a fact about the SESSION, not the device, so it is refetched
 * on sign-in rather than cached anywhere durable — signing in as someone else
 * must not inherit the previous person's buttons.
 *
 * When the query has not answered yet, `isAdmin` is false. Assuming privilege
 * during the gap would flash controls that then disappear, which reads as a
 * bug and teaches the operator to distrust what the screen says.
 */

import { useQuery } from '@tanstack/react-query';

import { api } from '@/api/endpoints';

export interface Me {
  uid: string;
  is_admin: boolean;
}

export function useMe() {
  return useQuery({
    queryKey: ['me'],
    queryFn: api.getMe,
    // Privilege does not change mid-session; a stale answer here is not a risk
    // because the server enforces it regardless of what the app believes.
    staleTime: 5 * 60_000,
    retry: false,
  });
}

/** Convenience for gating a control. False until the answer is in. */
export function useIsAdmin(): boolean {
  return useMe().data?.is_admin === true;
}
