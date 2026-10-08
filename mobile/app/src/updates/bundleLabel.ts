/**
 * Which JS bundle this launch is running, short enough for a footer.
 *
 * "I updated and it is the same" cannot be told apart from "the update has
 * not been applied yet" without it — EAS Insights records nothing for this
 * app, so a screenshot of the login screen is the only report that arrives.
 * The id prefix matches `eas update:list`; the time is when the bundle was
 * published (or, for the one embedded in the binary, built).
 */
export interface RunningBundle {
  readonly isEnabled: boolean;
  readonly isEmbeddedLaunch: boolean;
  readonly updateId: string | null;
  readonly createdAt: Date | null;
}

const pad = (n: number) => String(n).padStart(2, '0');

export function bundleLabel(b: RunningBundle): string {
  // Off means the embedded bundle and no OTA at all — worth saying apart
  // from a dev client, which has updates on but no published id.
  if (!b.isEnabled) return 'Paket: OTA kapalı';
  if (!b.updateId) return 'Paket: geliştirme';
  const source = b.isEmbeddedLaunch ? 'gömülü' : `OTA ${b.updateId.slice(0, 8)}`;
  const d = b.createdAt;
  if (!d || Number.isNaN(d.getTime())) return `Paket: ${source}`;
  const when = `${pad(d.getDate())}.${pad(d.getMonth() + 1)} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  return `Paket: ${source} · ${when}`;
}
