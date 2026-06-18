# Android APK — Xiaomi (kişisel kullanım, ücretsiz)

iOS yerine Android: **APK direkt yükle = $0, hesap/onay/ödeme yok.** Sadece ücretsiz Expo hesabı gerekiyor (build için).

## Mimari (şu an)

```
Xiaomi telefon  ──HTTP──►  http://167.233.102.179:8000  (FastAPI backend)
                            agentmesh box (.102.179)
                            ↑ ai-trader-api.service (systemd, port 8000)
                            ↑ ai-trader.timer (günlük paper trading)
```

Backend agentmesh box'ta `ai-trader-api.service` olarak çalışıyor (port 8000, firewall açık). Mobil app oraya bağlanıp portfolio + kararları gösterir. Plain HTTP — kişisel paper kullanım için yeterli; sonra HTTPS eklenebilir.

## Build adımları

### 1. Expo hesabı (ücretsiz, bir kerelik)
```bash
npm install -g eas-cli
eas login          # https://expo.dev → ücretsiz signup (Google ile 30 sn)
```

### 2. Proje + deps
```bash
cd mobile/app
pnpm install        # expo-build-properties dahil
eas init            # Expo projesine bağla (proje ID üretir)
```

### 3. Dev token'ı EAS secret olarak ekle (repo'ya yazmadan)
Backend'in `DEV_API_TOKEN`'ı ile aynı olmalı (agentmesh box `/opt/ai-trader/secrets.env`):
```bash
eas secret:create --scope project --name EXPO_PUBLIC_DEV_API_TOKEN --value <BACKEND_DEV_TOKEN>
```

### 4. APK build (preview profili → Android APK)
```bash
eas build --platform android --profile preview
```
- Expo sunucularında ~10-20 dk
- Bitince **indirme linki** verir (`.apk`)

### 5. Xiaomi'ye kur
1. APK linkini telefonda aç (veya Mac'ten indirip aktar)
2. **Ayarlar → Gizlilik → Bilinmeyen kaynaklardan uygulama** → tarayıcıya/dosya yöneticisine izin ver
3. APK'ya dokun → Kur
4. MIUI "APK tarama" yapabilir, birkaç saniye bekle

### 6. Push bildirimleri için MIUI ayarı (önemli)
MIUI/HyperOS arka plan uygulamaları agresif kapatır:
- **Ayarlar → Uygulamalar → Trading → Pil tasarrufu → Kısıtlama yok**
- **Otomatik başlatma (Autostart) → Aç**
- Bildirim izni → Aç

Yoksa trade bildirimleri (decision pending, order filled) gelmez.

## Ne görürsün
- **Portfolio:** canlı equity + AAPL/MSFT pozisyonları + P&L
- **Pending:** onay bekleyen emirler (varsa) → biometric onay
- **Agents:** günlük LLM kararları (rating, entry, stop, PT)

## Sorun giderme
| Belirti | Sebep | Çözüm |
|---|---|---|
| "Backend unreachable" | Port 8000 erişilemiyor | `curl http://167.233.102.179:8000/healthz` test et; ufw/servis kontrol |
| Boş ekran / 401 | Token yanlış | EAS secret = backend DEV_API_TOKEN olmalı |
| HTTP bloklandı | Cleartext kapalı | app.config.ts'de `usesCleartextTraffic: true` var (preview build'de aktif) |
| Push gelmiyor | MIUI kapatmış | Autostart + pil kısıtlama yok yap |

## Sonra: HTTPS'e geçiş (opsiyonel, güvenlik)
Plain HTTP yerine: agentmesh box'a Cloudflare Tunnel (`cloudflared`) kurup stabil HTTPS URL al, eas.json'daki API URL'yi onunla değiştir, `usesCleartextTraffic`'i kaldır.
