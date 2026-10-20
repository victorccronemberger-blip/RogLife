# `roglife_appcheck` — App Check + identidade real do S22

## Princípio

Com o aparelho conectado, **não forjar** identidade de device.  
Ler via `adb`: SSAID, Build.MODEL, SDK, release, versionName do Minute, uptime.

## Módulos

| Arquivo | Função |
| --- | --- |
| `adb.py` | devices / shell / SSAID |
| `capture.py` | JWT App Check nas SharedPreferences |
| `device_info.py` | getprop + dumpsys + `/proc/uptime` |
| `provider.py` | cache, token, helpers de payload |
| `config.py` | `vendor/roglife/config.json` |

## Helpers de payload

- `physical_device_id()` → `X-Device-Id`  
- `physical_app_version()` → `X-App-Version` / `meta.appVersion`  
- `physical_upload_device_meta()` → `{"model": ...}` (POST curto)  
- `physical_sidecar_device_meta()` → model + systemName + systemVersion  
- `physical_sidecar_platform_meta()` → `{os, version: sdkInt}`  
- `physical_uptime_ns()` → timebase (approx. elapsedRealtime)  

## Fronteira

Este pacote **não** substitui Ego4D/Nymeria.  
Mídia, IMU e candidatos de clipe continuam nos módulos de dataset/campanha.  
Aqui só: App Check + identidade/props do S22 para headers e meta de aparelho.

## Ativação

A captura e a consulta do painel usam o mesmo cache: no Windows,
`%LOCALAPPDATA%/QMoney/data/appcheck_device.json`. Um `QMONEY_USER_ROOT`
explícito continua selecionando um perfil isolado. O script
`app/examples/refresh_device_appcheck.py` não redireciona para um perfil
temporário. Consultar o status lê somente metadados locais; não captura nem
renova tokens. Os dados coletados continuam visíveis com a captura desativada,
sem serem apresentados como credenciais ativas.

```powershell
$env:QMONEY_APPCHECK_SOURCE = "device"
# Campanha/admissão também ativam SOURCE=device se a variável estiver vazia
# (não sobrescreve off/bearer/debug). Serial: cache de identidade ou ANDROID_SERIAL.
python app/examples/probe_create.py --serial RQCT804KN2N
```
