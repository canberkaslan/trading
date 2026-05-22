# ADR-003: React Native + Expo for mobile

**Status:** Accepted
**Date:** 2026-05-22

## Context

Mobile app must:
- Show portfolio (US + BIST positions, P&L)
- Show 7-agent decisions with expandable reasoning
- Approve/reject trades (or set auto-execute mode)
- Receive push notifications (trade executed, drawdown alert, agent disagreement)
- Display candlestick charts with technical indicators
- Multi-language (TR + EN)
- Biometric auth + 2FA

User profile: senior backend engineer, some C# (Unity) experience, mostly Go/Python/TS. Solo or small-team delivery.

## Decision

**React Native 0.76 + Expo SDK 52 + TypeScript (strict).**

Full stack:

```
State:     Zustand + TanStack Query v5
Storage:   expo-secure-store (tokens) + expo-sqlite + Drizzle ORM (cache)
Charts:    TradingView Lightweight Charts (Apache 2.0) in react-native-webview
Realtime:  WebSocket + Protobuf
Auth:      AWS Cognito + expo-local-authentication + TOTP MFA
Network:   ky + react-native-ssl-pinning
Push:      expo-notifications → SNS → APNs/FCM
i18n:      i18next + react-i18next (TR/EN)
Payments:  RevenueCat
Crash:     Sentry
Analytics: PostHog (self-hosted on existing EKS)
Build:     EAS Build + EAS Update (OTA)
```

## Alternatives considered

| Option | Why rejected |
|---|---|
| Flutter | Dart is one-off skill; no TradingView official wrapper; smaller fintech ecosystem; Shorebird ($) for OTA |
| Native (Swift + Kotlin) | 2× codebase, 2× hiring, 2× build pipelines — kills solo dev velocity |
| **.NET MAUI** | C# familiarity is the only argument. Bare fintech ecosystem, no TradingView wrappers, weak real-time chart perf, hot reload spotty. A senior engineer picks up TS in a weekend |
| Capacitor + React/Vue | Push/biometric/widgets second-class; App Store 4.2 rejection risk for WebView-heavy fintech |
| Tauri Mobile (2025) | Still beta in 2026; no production trading apps in the wild |

## Critical security decisions

1. **Broker API keys NEVER on device.** Mobile authenticates as a user (Cognito JWT); backend holds Alpaca/Matriks keys in AWS Secrets Manager and proxies orders. This is the single most important architectural decision — every other choice flexes around this line.
2. **Auto-execute toggle behind biometric + 24h delay before first auto-trade.** SMS confirm to enable.
3. **Trade approval TTL.** Manual-mode trades auto-reject after N seconds if user doesn't act.
4. **Cert pinning** at intermediate CA (not leaf — rotation safety).
5. **Biometric does NOT replace MFA.** It unlocks the stored refresh token; MFA is separate TOTP.

## Charting library

| Library | Verdict |
|---|---|
| TradingView Lightweight Charts (Apache 2.0) | **Chosen.** ~45KB, candlestick + indicator overlays, 60fps on mid-range Android with 50k candles via WebView |
| TradingView Charting Library (commercial) | Defer until we have time for license application; overkill for MVP |
| victory-native / svg-charts | Slow at >2k candles |
| react-native-wagmi-charts | Crypto-toy, weak OHLC |

## Store considerations

- **Apple:** Auto-trading allowed under 2.5.4/5.2.1 if (a) we own/authorize broker relationship, (b) display risk disclaimers, (c) subscriptions cancelable in-app. **"Guaranteed returns" = instant rejection.**
- **Risk disclaimer:** mandatory splash + footer on every trade screen
- **Turkey App Store:** No special listing restriction, but BIST trading requires SPK authorization or licensed-broker partnership — we are an *introducing technology*, Matriks/Algolab is the broker-dealer. Keep that line clear in ToS.

## Folder structure (mobile/app)

```
mobile/app/
├── app/                          # expo-router file-based routing
│   ├── (auth)/login.tsx, mfa.tsx, recovery.tsx
│   ├── (tabs)/_layout.tsx, portfolio.tsx, agents.tsx, charts.tsx, settings.tsx
│   ├── trade/[symbol].tsx, approve/[orderId].tsx
│   ├── backtest/[runId].tsx
│   └── _layout.tsx               # root, providers, biometric gate
├── src/
│   ├── api/                      # client.ts, ws.ts, proto/, endpoints/
│   ├── stores/                   # Zustand: auth, agents, ui
│   ├── features/                 # portfolio, agents, trade-approval, charts, backtest
│   ├── db/                       # schema.ts (Drizzle) + migrations
│   ├── auth/                     # cognito, biometric, secureStore
│   ├── i18n/                     # tr.json, en.json
│   ├── notifications/            # handlers, register
│   ├── theme/                    # colors (incl. colorblind-safe), typography
│   ├── widgets/                  # expo-apple-targets / glance bridge
│   └── utils/                    # currency, risk, telemetry
├── plugins/                      # withSSLPinning.js, withWidgets.js
├── eas.json, app.config.ts, tsconfig.json (strict: true)
```

## Consequences

### Accepted

- TypeScript onboarding for the user (~weekend)
- WebView for charts adds a small bridge perf tax (acceptable for swing trading UI, not for HFT)
- Expo managed workflow constraints — we use config plugins for SSL pinning and widgets
- EAS Build subscription ($19–99/mo)

### Gained

- **EAS Update OTA** — JS-only hotfixes without 24h Apple review (critical for trading UX bugs)
- Same TS types shared with FastAPI backend (via openapi-typescript)
- Single codebase, single CI pipeline
- 4–6 week MVP timeline

## MVP scope (Phase 5)

- [ ] Cognito signup/login/MFA + biometric
- [ ] Portfolio screen (positions + P&L hero)
- [ ] Agent dashboard (7 cards, reasoning expand)
- [ ] Trade approval modal (TTL countdown)
- [ ] Push notifications (trade exec, drawdown)
- [ ] Settings (auto-execute toggle behind biometric)
- [ ] TR + EN

## V1.1+

- Auto-execute mode (after risk-team review)
- iOS WidgetKit + Android Glance widgets
- Backtest results visualization
- Streaming LLM reasoning (SSE/WS) — the "wow" feature

## Sources

- Research output preserved in `docs/RESEARCH.md` §5
- TradingView Lightweight Charts: https://github.com/tradingview/lightweight-charts
- Expo SDK 52 docs: https://docs.expo.dev
- Apple App Review 2.5.4 / 5.2.1
