# Mobile App

React Native + Expo SDK 52 app for the AI trading system. iOS + Android.

See [ADR-003](../docs/adr/003-mobile-react-native.md) for the framework decision and architecture details.

## Stack

| Layer | Choice |
|---|---|
| Framework | React Native 0.76 + Expo SDK 52 |
| Language | TypeScript (strict) |
| State | Zustand + TanStack Query v5 |
| Storage | expo-secure-store + expo-sqlite (Drizzle ORM) |
| Charts | TradingView Lightweight Charts in WebView |
| Real-time | WebSocket + Protobuf |
| Auth | AWS Cognito + biometric + TOTP MFA |
| Push | expo-notifications → SNS → APNs/FCM |
| i18n | i18next (TR/EN) |
| Build | EAS Build + EAS Update |
| Crash | Sentry |
| Analytics | PostHog (self-hosted) |

## Quick start

```bash
cd mobile/app
pnpm install
pnpm start            # Expo Dev Tools
pnpm ios              # iOS Simulator
pnpm android          # Android Emulator
```

Requires:
- Node 20+
- pnpm 9+
- Xcode 16 (iOS)
- Android Studio (Android)
- EAS CLI: `npm install -g eas-cli`

## Security guardrails

**Broker API keys NEVER live on device.** Mobile authenticates as a user via Cognito JWT; the backend (AWS Secrets Manager) holds Alpaca/Matriks keys and proxies all order placement. This is the single most important architectural decision.

Other invariants:
- Auto-execute toggle gated behind biometric + 24h cooldown before first auto-trade
- Manual-mode trade approvals have a countdown TTL — auto-reject if user doesn't act
- Cert-pin the intermediate CA (never the leaf)
- Risk disclaimer on every trade screen
