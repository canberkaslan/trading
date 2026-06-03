# Mobile Release Playbook

This is the one-page guide for shipping a build to TestFlight / Play
Internal Track or pushing an OTA JS update.

## Profiles (mobile/app/eas.json)

| Profile | Distribution | API URL | Use case |
|---|---|---|---|
| `development` | internal (simulator OK) | `http://localhost:8000` | Local dev with `expo run:ios` |
| `preview` | internal | `https://api-dev.trading.canberkaslan.co` | Quick QA, install via TestFlight invite |
| `paper` | internal | `https://api-paper.trading.canberkaslan.co` | Live paper trading, real Alpaca account |
| `production` | App Store / Play | `https://api.trading.canberkaslan.co` | Public release (deferred) |

## Prerequisites (one-time)

1. **Apple Developer Program** (\$99/yr) → `appleTeamId`, `ascAppId` in `eas.json`
2. **Google Play Console** (\$25 one-time) → `google-service-account.json` at `mobile/app/`
3. **Expo account** → `EXPO_TOKEN` secret in GitHub repo
4. **EAS CLI** locally: `npm install -g eas-cli && eas login`

Replace the `REPLACE_AFTER_*` placeholders in `eas.json` once you have the IDs.

## Local build (no GitHub)

```bash
cd mobile/app
eas build --platform ios --profile preview                 # to TestFlight pipeline
eas build --platform android --profile preview             # to Play Internal
eas build --platform all --profile paper                   # both platforms, paper env
```

Build runs on Expo's servers (~10–20 min). Result: TestFlight build automatically appears under "External Testers → Builds" in App Store Connect after upload.

## OTA JS update (no rebuild needed)

For JS-only changes — typography tweaks, copy edits, new screens — skip the full build:

```bash
cd mobile/app
eas update --branch preview --message "fix: trade approval button copy"
```

The change reaches every device on that channel within ~5 minutes. Native code changes (new Expo plugin, new permission) still need a full rebuild.

## GitHub Actions

`.github/workflows/mobile-build.yml`:

- **Manual trigger** (`workflow_dispatch`): pick profile + platform + whether to submit. Used for previews from any branch.
- **Tag trigger** (`mobile-v*`): full production build + submit on both stores.

  ```bash
  git tag mobile-v0.1.0 && git push origin mobile-v0.1.0
  ```

- **OTA update on every main push** (skipped on tag pushes).

## Pre-submission checklist (TestFlight)

- [ ] Risk disclaimer visible on login + every trade screen (Apple 5.2.1)
- [ ] No "guaranteed returns" copy anywhere (Apple 5.2.5 rejection)
- [ ] `NSFaceIDUsageDescription` in `app.config.ts` is human-readable
- [ ] Push notifications work end-to-end (`POST /v1/notifications/test`)
- [ ] Backend `/healthz` returns OK from the configured `EXPO_PUBLIC_API_URL`
- [ ] App Privacy nutrition label declares: Contact Info, Financial Info,
      Identifiers, Usage Data (all linked to identity)
- [ ] KVKK aydınlatma metni link in Settings (Türkiye App Store)

## Troubleshooting

| Error | Cause | Fix |
|---|---|---|
| `Apple Developer Program enrollment is required` | No `appleTeamId` set | Sign up, paste team ID into `eas.json` |
| `Build is taking too long` (>30m) | Expo build farm queue | Wait, or use `--profile development` with `simulator: true` for local |
| `Invalid app ID` on submit | `ascAppId` is the placeholder | Create app in App Store Connect, replace |
| TestFlight builds rejected by Apple review | Usually 4.2.6 (incomplete feature) or 5.2.1 (financial trading) | Make sure the backend `EXPO_PUBLIC_API_URL` is reachable from outside your LAN, and disclaimers are explicit |

## Rollback

OTA: `eas update:rollback --branch preview` reverts to the previous JS bundle.

Native: re-submit the prior build from TestFlight ("Activate this build") — no rebuild needed.
