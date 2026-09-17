# Mobile App — Local Demo (Phase 5)

Bu rehber: **backend'i lokal başlat → iOS simulator'da mobile app aç → portfolio + agent decisions canlı verisini gör.**

## 1. Backend'i başlat (terminal #1)

```bash
cd ~/claude_works/trading/agent
set -a && source .env && set +a

# (İlk seferse: uvicorn ve fastapi yüklü değilse)
uv pip install "fastapi>=0.115" "uvicorn[standard]>=0.32"

# Mobile dev için 8000 portu (default)
./.venv/bin/uvicorn api.main:app --reload --port 8000
```

Doğrula:
```bash
curl http://localhost:8000/healthz                # -> {"status":"ok"}
curl http://localhost:8000/readyz                 # -> alpaca + db status
curl http://localhost:8000/v1/portfolio/snapshot  # -> live Alpaca equity
curl http://localhost:8000/v1/agents/decisions    # -> son LLM kararları
```

> Backend Alpaca'ya gerçek bağlanır (paper account). Local SQLite (`agent/local.db`) decision + order log'unu tutar.

## 2. Mobile app'i başlat (terminal #2)

```bash
cd ~/claude_works/trading/mobile/app

# İlk seferse
pnpm install

# iOS simulator'da
pnpm ios
# veya Android emulator
pnpm android
# veya web preview (en kolay, browser'da)
pnpm web
```

`EXPO_PUBLIC_API_URL` set edilmemişse `http://localhost:8000` kullanılır. Cihazdan (iPhone fiziksel) test edersen Mac'in LAN IP'sini ver:

```bash
EXPO_PUBLIC_API_URL=http://192.168.1.42:8000 pnpm ios
```

## 3. Ne göreceksin

### Portfolio tabı
- Total equity (Alpaca'dan canlı, $99,994.36 şu an)
- Daily P&L (paper başlangıcı $100k'ya göre)
- Cash + buying power
- Açık position'lar (varsa) - quantity, avg entry, current, P&L
- 10 saniyede bir auto-refresh
- Pull-to-refresh

### Agents tabı
- Son 25 LLM kararı (DB'den)
- Her karar kartında: ticker, rating (renkli), entry/stop/PT, horizon, timestamp
- Karta tıklayınca → trade approval ekranı (`/trade/[ticker]`)

### Trade approval ekranı
- Kararın detayı + tam Portfolio Manager metni
- Reject + Approve butonları (Approve'ın biometric integration'ı henüz tam değil — TODO Phase 5d)

## 4. Sorun giderme

| Belirti | Sebep | Çözüm |
|---|---|---|
| "Backend unreachable" | uvicorn çalışmıyor | terminal #1'i kontrol et |
| Boş portfolio | Alpaca key yanlış | `agent/.env` ALPACA_API_KEY/SECRET doğru mu, `curl http://localhost:8000/readyz` |
| Boş decisions | DB'de karar yok | `cd agent && ./.venv/bin/python -m scripts.trade --ticker AAPL --use-cached` (en az bir kez çalıştır) |
| iOS simulator açılmıyor | Xcode yok | `xcode-select --install` veya web preview kullan |

## 5. iOS TestFlight deployment

**Prerequisites:**
- Apple Developer Program ($99/year) — **BLOCKER: bu olmadan TestFlight'a yüklenemez**
- Apple ID: `canberkaslan@gmail.com` (eas.json'da tanımlı)

### 5.1. Apple Developer Program aktif olduktan sonra

1. **App Store Connect'te app oluştur**
   - https://appstoreconnect.apple.com
   - Create new app → bundle ID: `com.canberkaslan.trading` (app.config.ts'de tanımlı)
   - App Store Connect App ID'yi kaydet → `eas.json`'da `ascAppId` güncelle

2. **Developer Portal'dan Team ID al**
   - https://developer.apple.com/account
   - Membership → Team ID'yi kaydet → `eas.json`'da `appleTeamId` güncelle

3. **GitHub secrets ekle**
   ```bash
   gh secret set EXPO_TOKEN -R canberkaslan/trading
   # EAS credentials otomatik yönetilir, manuel Apple API key opsiyonel
   ```

4. **EAS credentials setup** (ilk build'de otomatik)
   ```bash
   cd mobile/app
   eas build --platform ios --profile preview
   # İlk seferde Expo signing credential'ları otomatik oluşturur
   ```

5. **TestFlight'a submit** (manual test)
   ```bash
   eas submit --platform ios --profile preview --latest
   ```

6. **Production release** (tag-driven, otomatik)
   ```bash
   git tag mobile-v0.1.0
   git push origin mobile-v0.1.0
   # .github/workflows/mobile-build.yml tetiklenir
   # → EAS build + submit → TestFlight'a yüklenir
   ```

### 5.2. Mevcut durum (Apple Developer Program YOK)

✅ **Hazır olanlar:**
- `eas.json`: build profiles tanımlı (preview, paper, production)
- `mobile-build.yml`: GitHub Actions workflow hazır
- iOS native proje: `expo prebuild` ile oluşturulabilir
- Xcode project: `ios/Trading.xcodeproj` oluşturuluyor

⚠️ **Eksik credential'lar** (eas.json'da placeholder):
```json
"appleTeamId": "REPLACE_AFTER_DEV_PROGRAM"
"ascAppId": "REPLACE_AFTER_APP_STORE_CONNECT_CREATE"
```

🔴 **Yapılamayan:**
- EAS cloud build (iOS) → signing için Developer Program gerekli
- TestFlight submit → App Store Connect app gerekli

**Geçici çözüm:** Simulator build (local, Developer Program gerekmez):
```bash
npx expo prebuild --platform ios --clean
# CocoaPods local env sorunları olabilir ama Xcode project oluşur
```

## 6. Şu an mobile'da ne **henüz yok**

Phase 5 alt-task'larında planlı:
- **5d** Trade approval — biometric (FaceID/TouchID) gating, gerçek POST /v1/orders/{id}/approve
- **5g** Push notifications — Expo Notifications + SNS
- **5h** Cognito auth (şimdi dev token veya açık)
- **5i** EAS Build + TestFlight — **Apple Developer Program bekliyor**

Şu an iyi olan: backend gerçek dataya bağlı, mobile UI'da görüyorsun. Bu Phase 5 MVP.
