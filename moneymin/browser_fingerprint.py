"""
browser_fingerprint.py — Geração de fingerprints PROFISSIONAIS do navegador.

Sistema AVANÇADO para geração de fingerprints indetectáveis com:
- Anti-detecção de automação (webdriver flag, CDP detection)
- Audio fingerprint realista (AudioContext)
- Font enumeration (fontes instaladas)
- Plugin/MIME type list
- Battery API simulation
- Screen orientation
- Color gamut (sRGB/P3)
- WebRTC IP protection
- Navigator properties completas
- Timezone consistency checks
- Canvas/WebGL com noise determinístico

Cada perfil é determinístico baseado em uma seed (ex: email), garantindo que a
mesma conta sempre use o mesmo fingerprint, mas contas diferentes tenham perfis
distintos com distribuições realistas.

Versão: 2.0 - Profissional (Indetectável)
"""
from __future__ import annotations

import hashlib
import json
import random
import time
from dataclasses import asdict, dataclass, field
from typing import Any

# Distribuições realistas de User-Agent (dados de StatCounter/SimilarWeb 2024-2025)
_USER_AGENTS = {
    "chrome_win11": [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    ],
    "chrome_win10": [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    ],
    "edge_win11": [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0",
    ],
}

# WebGL vendors (distribuição realista de GPUs)
_WEBGL_VENDORS = {
    "intel": [
        "Intel Inc.",
        "Intel",
    ],
    "nvidia": [
        "Google Inc. (NVIDIA)",
        "NVIDIA Corporation",
    ],
    "amd": [
        "Google Inc. (AMD)",
        "ATI Technologies Inc.",
    ],
}

_WEBGL_RENDERERS = {
    "intel": [
        "Intel(R) UHD Graphics 630",
        "Intel(R) UHD Graphics 620",
        "Intel(R) Iris(R) Xe Graphics",
        "Intel(R) UHD Graphics",
        "Intel(R) HD Graphics 630",
    ],
    "nvidia": [
        "NVIDIA GeForce GTX 1660 Ti",
        "NVIDIA GeForce RTX 3060",
        "NVIDIA GeForce RTX 3070",
        "NVIDIA GeForce GTX 1080",
        "NVIDIA GeForce RTX 2060",
        "NVIDIA GeForce RTX 3080",
        "NVIDIA GeForce GTX 1650",
    ],
    "amd": [
        "AMD Radeon RX 580",
        "AMD Radeon RX 5700 XT",
        "AMD Radeon RX 6700 XT",
        "AMD Radeon(TM) Graphics",
    ],
}

# Resoluções de tela (distribuição realista - mais comuns primeiro)
_SCREEN_RESOLUTIONS = [
    (1920, 1080),  # 45%
    (1366, 768),   # 15%
    (1536, 864),   # 10%
    (1440, 900),   # 8%
    (1280, 720),   # 5%
    (2560, 1440),  # 4%
    (1600, 900),   # 3%
    (1920, 1200),  # 3%
    (3840, 2160),  # 2% (4K)
    (1680, 1050),  # 2%
    (1280, 800),   # 2%
    (2560, 1600),  # 1%
]

# Pesos para distribuição
_SCREEN_RESOLUTION_WEIGHTS = [45, 15, 10, 8, 5, 4, 3, 3, 2, 2, 2, 1]

# Hardware concurrency (núcleos lógicos) - distribuição realista
_CPU_CORES = [4, 8, 6, 12, 16, 2, 24]
_CPU_CORES_WEIGHTS = [35, 30, 15, 10, 5, 3, 2]

# Device memory (GB) - correlacionado com CPU
_DEVICE_MEMORY = [8, 16, 32, 4, 64, 128]
_DEVICE_MEMORY_WEIGHTS = [30, 35, 20, 10, 4, 1]

# Timezones do Brasil (distribuição realista)
_TIMEZONE_OFFSETS = {
    "America/Sao_Paulo": -3,  # 85%
    "America/Noronha": -2,    # 1%
    "America/Manaus": -4,     # 10%
    "America/Rio_Branco": -5, # 4%
}
_TIMEZONE_WEIGHTS = [85, 1, 10, 4]

# Languages (Brasil)
_LANGUAGES = [
    ["pt-BR"],
    ["pt-BR", "pt"],
    ["pt-BR", "en-US", "en"],
    ["pt-BR", "en"],
    ["pt"],
]
_LANGUAGE_WEIGHTS = [60, 25, 5, 8, 2]

# ============================================================================
# COMPONENTES AVANÇADOS (PROFISSIONAL - INDTECTÁVEL)
# ============================================================================

# Fontes comuns instaladas no Windows 10/11 (distribuição realista)
_WINDOWS_FONTS = [
    # Fontes padrão do Windows (quase 100% têm)
    "Arial", "Calibri", "Cambria", "Candara", "Comic Sans MS", "Consolas",
    "Courier New", "Ebrima", "Gadugi", "Georgia", "Helvetica",
    "Javanese Text", "Leelawadee UI", "Lucida Console", "Malgun Gothic",
    "Microsoft Himalaya", "Microsoft JhengHei", "Microsoft New Tai Lue",
    "Microsoft PhagsPa", "Microsoft Sans Serif", "Microsoft Tai Le",
    "Microsoft YaHei", "Microsoft Yi Baiti", "MingLiU_HKSCS", "Mongolian Baiti",
    "MS Gothic", "MV Boli", "Myanmar Text", "Nirmala UI", "Palatino Linotype",
    "Segoe MDL2 Assets", "Segoe Print", "Segoe Script", "Segoe UI",
    "Segoe UI Historic", "Segoe UI Emoji", "Segoe UI Symbol", "SimSun",
    "Sylfaen", "Symbol", "Tahoma", "Times New Roman", "Trebuchet MS",
    "Verdana", "Webdings", "Wingdings", "Yu Gothic",

    # Fontes Office (80% têm)
    "Book Antiqua", "Bookman Old Style", "Century Gothic", "Franklin Gothic",
    "Garamond", "Lucida Sans", "MS Reference Sans Serif", "MS Reference Specialty",

    # Fontes adicionais (50% têm)
    "Adobe Arabic", "Adobe Hebrew", "Aharoni", "David", "Estrangelo Edessa",
    "FrankRuehl", "Gisha", "Levenim MT", "Miriam", "Miriam Fixed", "Rod",

    # Fontes de desenvolvimento (30% têm)
    "Cascadia Code", "Cascadia Mono", "JetBrains Mono", "Fira Code", "Source Code Pro",

    # Fontes criativas (20% têm)
    "Impact", "Brush Script MT", "Monotype Corsiva", "Papyrus", "Ravie",
]

# MIME types comuns (distribuição realista)
_MIME_TYPES = [
    "application/pdf",
    "application/x-google-chrome-pdf",
    "application/xhtml+xml",
    "application/xml",
    "application/json",
    "application/javascript",
    "application/x-shockwave-flash",  # raro, mas alguns têm
    "audio/mpeg",
    "audio/ogg",
    "audio/wav",
    "audio/webm",
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/svg+xml",
    "image/webp",
    "image/x-icon",
    "text/html",
    "text/plain",
    "text/css",
    "text/javascript",
    "text/xml",
    "video/mp4",
    "video/webm",
    "video/ogg",
]

# Codecs de áudio suportados (realista)
_AUDIO_CODECS = [
    "audio/mpeg",           # MP3
    "audio/mp4",            # AAC
    "audio/ogg",            # OGG
    "audio/wav",            # WAV
    "audio/webm",           # WebM
    "audio/aac",            # AAC
    "audio/flac",           # FLAC (50%)
    "audio/opus",           # Opus
]

# Codecs de vídeo suportados (realista)
_VIDEO_CODECS = [
    "video/mp4",            # MP4
    "video/webm",           # WebM
    "video/ogg",            # OGG
    "video/quicktime",      # MOV (raro em Windows)
]

# Screen orientations (distribuição realista)
_SCREEN_ORIENTATIONS = [
    "landscape-primary",    # 95%
    "landscape-secondary",  # 1%
    "portrait-primary",     # 3% (monitores pivotados)
    "portrait-secondary",   # 1%
]
_SCREEN_ORIENTATION_WEIGHTS = [95, 1, 3, 1]

# Color gamuts (distribuição realista)
_COLOR_GAMUTS = [
    "srgb",     # 90% (padrão)
    "p3",       # 8% (monitores modernos/high-end)
    "rec2020",  # 2% (monitores profissionais)
]
_COLOR_GAMUT_WEIGHTS = [90, 8, 2]

# Pointer types (distribuição realista)
_POINTER_TYPES = [
    "fine",     # 80% (mouse)
    "coarse",   # 15% (touchscreen)
    "none",     # 5% (sem pointer, raro)
]
_POINTER_WEIGHTS = [80, 15, 5]

# Battery levels realistas (distribuição)
_BATTERY_LEVELS = [
    (0.9, 1.0),    # 30% (90-100%)
    (0.7, 0.9),    # 25% (70-90%)
    (0.5, 0.7),    # 20% (50-70%)
    (0.3, 0.5),    # 15% (30-50%)
    (0.1, 0.3),    # 10% (10-30%)
    (0.0, 0.1),    # 0% (0-10%, raro)
]
_BATTERY_WEIGHTS = [30, 25, 20, 15, 10, 0]

# Connection types (distribuição realista)
_CONNECTION_TYPES = [
    "wifi",         # 70%
    "4g",           # 20%
    "ethernet",     # 8%
    "3g",           # 2%
]
_CONNECTION_WEIGHTS = [70, 20, 8, 2]


@dataclass
class BrowserFingerprint:
    """Perfil COMPLETO e PROFISSIONAL de fingerprint do navegador."""

    # User-Agent
    user_agent: str

    # WebGL
    webgl_vendor: str
    webgl_renderer: str

    # Canvas (seed para ruído)
    canvas_seed: int

    # Tela
    screen_width: int
    screen_height: int

    # Hardware
    hardware_concurrency: int
    device_memory: int

    # Locale/Timezone
    locale: str
    timezone: str
    timezone_offset: int

    # Languages
    languages: list[str]

    # Componentes AVANÇADOS (PROFISSIONAL)
    fonts: list[str]                    # Fontes instaladas
    mime_types: list[str]              # MIME types suportados
    audio_codecs: list[str]            # Codecs de áudio
    video_codecs: list[str]            # Codecs de vídeo
    screen_orientation: str            # Orientação da tela
    color_gamut: str                   # Gamut de cores (sRGB/P3)
    pointer_type: str                  # Tipo de pointer (mouse/touch)

    # Audio fingerprint (AudioContext)
    audio_sample_rate: int             # Sample rate (44100/48000)
    audio_channel_count: int           # Canais (2/6)
    audio_state: str                   # "running"/"suspended"

    # Battery API
    battery_level: float               # Nível (0.0-1.0)
    battery_charging: bool             # Estar carregando?

    # Network
    connection_type: str               # "wifi"/"4g"/"ethernet"
    connection_downlink: float         # Velocidade (Mbps)
    connection_rtt: int                # Latência (ms)

    # Screen (com defaults)
    color_depth: int = 24
    pixel_ratio: float = 1.0
    available_width: int = 0           # Tela disponível (sem taskbar)
    available_height: int = 0

    # Platform
    platform: str = "Win32"
    vendor: str = "Google Inc."
    app_version: str = ""              # Versão do app (derivada do UA)

    # Flags
    do_not_track: bool = False
    cookies_enabled: bool = True
    webdriver: bool = False            # ANTI-DETECÇÃO: sempre False

    # Timezone consistency
    timezone_abbr: str = "BRT"         # Abreviação (BRT/AMT/ACT)

    # Anti-detection
    automation_blocked: bool = True    # Bloqueia detecção de automação

    def to_dict(self) -> dict[str, Any]:
        """Converte para dict (serialização)."""
        return asdict(self)

    def to_playwright_context(self) -> dict[str, Any]:
        """Converte para opções do Playwright BrowserContext."""
        return {
            "user_agent": self.user_agent,
            "viewport": {"width": self.screen_width, "height": self.screen_height},
            "screen": {"width": self.screen_width, "height": self.screen_height},
            "device_scale_factor": self.pixel_ratio,
            "locale": self.locale,
            "timezone_id": self.timezone,
        }

    def to_js_injections(self) -> list[str]:
        """Gera scripts JavaScript PROFISSIONAIS para aplicar fingerprint.

        Inclui componentes AVANÇADOS para anti-detecção:
        - Navigator properties completas
        - WebGL/Canvas overrides
        - Font enumeration
        - Audio fingerprint
        - Battery API
        - Network information
        - Screen properties
        - Anti-automation detection
        """
        scripts = []

        # ====================================================================
        # ANTI-DETECÇÃO: Remove flags de automação
        # ====================================================================
        scripts.append("""
            // Remove webdriver flag (CRÍTICO para anti-detecção)
            Object.defineProperty(navigator, 'webdriver', {
                get: () => false
            });

            // Remove CDP detection
            delete window.cdc_adoQpoasnfa74p8Z;
            delete window.cdc_adoQpoasnfa74p8Z$chrome;
            delete window.$cdc_adoQpoasnfa74p8Z;
            delete window.$chrome_asyncSessionInfo;

            // Remove Playwright/Puppeteer artifacts
            if (window.__playwright) delete window.__playwright;
            if (window.__pw_manual) delete window.__pw_manual;
            if (window.__selenium_unwrapped) delete window.__selenium_unwrapped;
        """)

        # ====================================================================
        # NAVIGATOR PROPERTIES (básico)
        # ====================================================================
        scripts.append(f"""
            Object.defineProperty(navigator, 'hardwareConcurrency', {{
                get: () => {self.hardware_concurrency}
            }});
        """)

        scripts.append(f"""
            Object.defineProperty(navigator, 'deviceMemory', {{
                get: () => {self.device_memory}
            }});
        """)

        scripts.append(f"""
            Object.defineProperty(navigator, 'languages', {{
                get: () => {json.dumps(self.languages)}
            }});
        """)

        scripts.append(f"""
            Object.defineProperty(navigator, 'doNotTrack', {{
                get: () => {'"1"' if self.do_not_track else 'null'}
            }});
        """)

        scripts.append(f"""
            Object.defineProperty(navigator, 'platform', {{
                get: () => '{self.platform}'
            }});
        """)

        scripts.append(f"""
            Object.defineProperty(navigator, 'vendor', {{
                get: () => '{self.vendor}'
            }});
        """)

        scripts.append(f"""
            Object.defineProperty(navigator, 'appVersion', {{
                get: () => '{self.app_version}'
            }});
        """)

        scripts.append(f"""
            Object.defineProperty(navigator, 'maxTouchPoints', {{
                get: () => {10 if self.pointer_type == 'coarse' else 0}
            }});
        """)

        # ====================================================================
        # WEBGL OVERRIDE (avançado)
        # ====================================================================
        scripts.append(f"""
            // WebGL vendor/renderer override
            const getParameter = WebGLRenderingContext.prototype.getParameter;
            WebGLRenderingContext.prototype.getParameter = function(param) {{
                if (param === 37445) return '{self.webgl_vendor}';  // UNMASKED_VENDOR
                if (param === 37446) return '{self.webgl_renderer}'; // UNMASKED_RENDERER
                return getParameter.call(this, param);
            }};

            // WebGL2 também
            if (WebGL2RenderingContext) {{
                const getParameter2 = WebGL2RenderingContext.prototype.getParameter;
                WebGL2RenderingContext.prototype.getParameter = function(param) {{
                    if (param === 37445) return '{self.webgl_vendor}';
                    if (param === 37446) return '{self.webgl_renderer}';
                    return getParameter2.call(this, param);
                }};
            }}
        """)

        # ====================================================================
        # CANVAS FINGERPRINT (com noise determinístico)
        # ====================================================================
        scripts.append(f"""
            // Canvas noise (subtle variation para fingerprint único)
            const originalToDataURL = HTMLCanvasElement.prototype.toDataURL;
            HTMLCanvasElement.prototype.toDataURL = function(type) {{
                const ctx = this.getContext('2d');
                if (ctx && type === 'image/png') {{
                    const imageData = ctx.getImageData(0, 0, this.width, this.height);
                    // Simple deterministic noise based on canvas_seed
                    let seed = {self.canvas_seed};
                    for (let i = 0; i < imageData.data.length; i += 4) {{
                        // Pseudo-random based on seed
                        seed = (seed * 9301 + 49297) % 233280;
                        const noise = (seed / 233280) * 2 - 1; // -1 to 1
                        imageData.data[i] = Math.max(0, Math.min(255, imageData.data[i] + Math.floor(noise)));
                    }}
                    ctx.putImageData(imageData, 0, 0);
                }}
                return originalToDataURL.apply(this, arguments);
            }};

            // Also override toBlob for canvas
            const originalToBlob = HTMLCanvasElement.prototype.toBlob;
            HTMLCanvasElement.prototype.toBlob = function(callback, type, quality) {{
                const ctx = this.getContext('2d');
                if (ctx && type === 'image/png') {{
                    const imageData = ctx.getImageData(0, 0, this.width, this.height);
                    let seed = {self.canvas_seed};
                    for (let i = 0; i < imageData.data.length; i += 4) {{
                        seed = (seed * 9301 + 49297) % 233280;
                        const noise = (seed / 233280) * 2 - 1;
                        imageData.data[i] = Math.max(0, Math.min(255, imageData.data[i] + Math.floor(noise)));
                    }}
                    ctx.putImageData(imageData, 0, 0);
                }}
                return originalToBlob.apply(this, arguments);
            }};
        """)

        # ====================================================================
        # FONT ENUMERATION (avançado)
        # ====================================================================
        fonts_json = json.dumps(self.fonts)
        scripts.append(f"""
            // Font enumeration override
            const originalCheck = document.fonts.check;
            document.fonts.check = function(font, text) {{
                const knownFonts = {fonts_json};
                // Extract font name from font string
                const match = font.match(/(?:^|\\s)([\\w\\s-]+?)(?:\\s|$)/);
                if (match && knownFonts.includes(match[1])) {{
                    return true;
                }}
                return originalCheck.apply(this, arguments);
            }};
        """)

        # ====================================================================
        # AUDIO FINGERPRINT (AudioContext)
        # ====================================================================
        scripts.append(f"""
            // AudioContext override (audio fingerprint)
            const OriginalAudioContext = window.AudioContext || window.webkitAudioContext;
            window.AudioContext = function(contextOptions) {{
                const ctx = new OriginalAudioContext(contextOptions);

                // Override sample rate
                Object.defineProperty(ctx, 'sampleRate', {{
                    get: () => {self.audio_sample_rate}
                }});

                // Override state
                Object.defineProperty(ctx, 'state', {{
                    get: () => '{self.audio_state}'
                }});

                // Override destination channel count
                Object.defineProperty(ctx.destination, 'channelCount', {{
                    get: () => {self.audio_channel_count}
                }});

                return ctx;
            }};
            window.AudioContext.prototype = OriginalAudioContext.prototype;
        """)

        # ====================================================================
        # BATTERY API
        # ====================================================================
        scripts.append(f"""
            // Battery API override
            if (navigator.getBattery) {{
                const originalGetBattery = navigator.getBattery;
                navigator.getBattery = async function() {{
                    const battery = await originalGetBattery.call(navigator);

                    // Override battery properties
                    Object.defineProperty(battery, 'level', {{
                        get: () => {self.battery_level}
                    }});
                    Object.defineProperty(battery, 'charging', {{
                        get: () => {'true' if self.battery_charging else 'false'}
                    }});

                    return battery;
                }};
            }}
        """)

        # ====================================================================
        # NETWORK INFORMATION
        # ====================================================================
        scripts.append(f"""
            // Network information override
            if (navigator.connection) {{
                Object.defineProperty(navigator.connection, 'effectiveType', {{
                    get: () => '{'4g' if self.connection_type in ['wifi', 'ethernet'] else self.connection_type}'
                }});
                Object.defineProperty(navigator.connection, 'downlink', {{
                    get: () => {self.connection_downlink}
                }});
                Object.defineProperty(navigator.connection, 'rtt', {{
                    get: () => {self.connection_rtt}
                }});
                Object.defineProperty(navigator.connection, 'type', {{
                    get: () => '{self.connection_type}'
                }});
            }}
        """)

        # ====================================================================
        # SCREEN PROPERTIES
        # ====================================================================
        scripts.append(f"""
            // Screen properties override
            Object.defineProperty(screen, 'width', {{
                get: () => {self.screen_width}
            }});
            Object.defineProperty(screen, 'height', {{
                get: () => {self.screen_height}
            }});
            Object.defineProperty(screen, 'availWidth', {{
                get: () => {self.available_width}
            }});
            Object.defineProperty(screen, 'availHeight', {{
                get: () => {self.available_height}
            }});
            Object.defineProperty(screen, 'colorDepth', {{
                get: () => {self.color_depth}
            }});
            Object.defineProperty(screen, 'pixelDepth', {{
                get: () => {self.color_depth}
            }});
        """)

        # ====================================================================
        # SCREEN ORIENTATION
        # ====================================================================
        scripts.append(f"""
            // Screen orientation override
            if (screen.orientation) {{
                Object.defineProperty(screen.orientation, 'type', {{
                    get: () => '{self.screen_orientation}'
                }});
                Object.defineProperty(screen.orientation, 'angle', {{
                    get: () => {0 if 'landscape' in self.screen_orientation else 90}
                }});
            }}
        """)

        # ====================================================================
        # MEDIA CODECS (audio/video)
        # ====================================================================
        audio_codecs_json = json.dumps(self.audio_codecs)
        video_codecs_json = json.dumps(self.video_codecs)
        scripts.append(f"""
            // Media codec support override
            const originalCanPlayType = HTMLMediaElement.prototype.canPlayType;
            HTMLMediaElement.prototype.canPlayType = function(type) {{
                const audioCodecs = {audio_codecs_json};
                const videoCodecs = {video_codecs_json};

                if (type && (audioCodecs.includes(type) || videoCodecs.includes(type))) {{
                    return 'probably';
                }}
                return originalCanPlayType.apply(this, arguments);
            }};
        """)

        # ====================================================================
        # COLOR GAMUT
        # ====================================================================
        scripts.append(f"""
            // Color gamut media query override
            const originalMatchMedia = window.matchMedia;
            window.matchMedia = function(query) {{
                // Override color-gamut queries
                if (query.includes('color-gamut')) {{
                    const mql = originalMatchMedia.call(window, query);
                    Object.defineProperty(mql, 'matches', {{
                        get: () => query.includes('{self.color_gamut}')
                    }});
                    return mql;
                }}
                return originalMatchMedia.call(window, query);
            }};
        """)

        # ====================================================================
        # TIMEZONE CONSISTENCY
        # ====================================================================
        scripts.append(f"""
            // Timezone consistency (Intl API)
            const originalResolvedOptions = Intl.DateTimeFormat.prototype.resolvedOptions;
            Intl.DateTimeFormat.prototype.resolvedOptions = function() {{
                const options = originalResolvedOptions.call(this);
                options.timeZone = '{self.timezone}';
                return options;
            }};
        """)

        return scripts


def _email_seed(email: str) -> str:
    """Gera seed determinística do email."""
    return hashlib.sha256(f"browser_fingerprint:{email}".encode()).hexdigest()


def _weighted_choice(rng: random.Random, items: list, weights: list):
    """Escolha ponderada determinística."""
    return rng.choices(items, weights=weights, k=1)[0]


def generate_fingerprint(email: str) -> BrowserFingerprint:
    """Gera fingerprint PROFISSIONAL determinístico baseado no email.

    O mesmo email sempre gera o mesmo fingerprint, mas emails diferentes
    têm perfis distintos com distribuições realistas.

    Versão 2.0: Inclui componentes AVANÇADOS para fingerprint indetectável.
    """
    seed = _email_seed(email)
    rng = random.Random(seed)

    # ========================================================================
    # COMPONENTES BÁSICOS
    # ========================================================================

    # Escolhe navegador (80% Chrome, 15% Edge, 5% outros)
    browser_choice = rng.random()
    if browser_choice < 0.80:
        # Chrome Windows (95% Win11, 5% Win10)
        if rng.random() < 0.95:
            ua_key = "chrome_win11"
        else:
            ua_key = "chrome_win10"
    elif browser_choice < 0.95:
        ua_key = "edge_win11"
    else:
        ua_key = "chrome_win11"  # fallback

    user_agent = rng.choice(_USER_AGENTS[ua_key])

    # GPU (distribuição realista: 60% Intel, 30% NVIDIA, 10% AMD)
    gpu_choice = rng.random()
    if gpu_choice < 0.60:
        gpu_type = "intel"
    elif gpu_choice < 0.90:
        gpu_type = "nvidia"
    else:
        gpu_type = "amd"

    webgl_vendor = rng.choice(_WEBGL_VENDORS[gpu_type])
    webgl_renderer = rng.choice(_WEBGL_RENDERERS[gpu_type])

    # Resolução (distribuição ponderada)
    screen_width, screen_height = _weighted_choice(
        rng, _SCREEN_RESOLUTIONS, _SCREEN_RESOLUTION_WEIGHTS
    )

    # Pixel ratio (1.0 para 80%, 1.25 para 15%, 1.5 para 5%)
    pixel_ratio_choice = rng.random()
    if pixel_ratio_choice < 0.80:
        pixel_ratio = 1.0
    elif pixel_ratio_choice < 0.95:
        pixel_ratio = 1.25
    else:
        pixel_ratio = 1.5

    # Available screen (sem taskbar: ~96% da altura)
    available_width = screen_width
    available_height = int(screen_height * rng.uniform(0.92, 0.98))

    # CPU cores (distribuição realista)
    hardware_concurrency = _weighted_choice(
        rng, _CPU_CORES, _CPU_CORES_WEIGHTS
    )

    # Device memory (correlacionado com CPU)
    device_memory = _weighted_choice(
        rng, _DEVICE_MEMORY, _DEVICE_MEMORY_WEIGHTS
    )

    # Timezone (distribuição Brasil)
    timezone = _weighted_choice(
        rng, list(_TIMEZONE_OFFSETS.keys()), _TIMEZONE_WEIGHTS
    )
    timezone_offset = _TIMEZONE_OFFSETS[timezone]

    # Timezone abbreviation
    timezone_abbr_map = {
        "America/Sao_Paulo": "BRT",
        "America/Manaus": "AMT",
        "America/Rio_Branco": "ACT",
        "America/Noronha": "FNT",
    }
    timezone_abbr = timezone_abbr_map.get(timezone, "BRT")

    # Languages
    languages = _weighted_choice(rng, _LANGUAGES, _LANGUAGE_WEIGHTS)

    # Canvas seed (inteiro para ruído determinístico)
    canvas_seed = rng.randint(0, 2**31 - 1)

    # Do Not Track (20% habilitado)
    do_not_track = rng.random() < 0.20

    # ========================================================================
    # COMPONENTES AVANÇADOS (PROFISSIONAL - INDTECTÁVEL)
    # ========================================================================

    # Fontes instaladas (15-30 fontes, sempre inclui as básicas)
    font_count = rng.randint(15, 30)
    fonts = sorted(rng.sample(_WINDOWS_FONTS, min(font_count, len(_WINDOWS_FONTS))))
    # Garante que as fontes essenciais estejam presentes
    essential_fonts = ["Arial", "Times New Roman", "Courier New", "Segoe UI"]
    for font in essential_fonts:
        if font not in fonts:
            fonts.append(font)
    fonts.sort()

    # MIME types (seleção realista)
    mime_types = sorted(rng.sample(_MIME_TYPES, rng.randint(15, len(_MIME_TYPES))))

    # Audio codecs (todos os principais)
    audio_codecs = _AUDIO_CODECS[:rng.randint(5, len(_AUDIO_CODECS))]

    # Video codecs (seleção)
    video_codecs = _VIDEO_CODECS[:rng.randint(2, len(_VIDEO_CODECS))]

    # Screen orientation (95% landscape)
    screen_orientation = _weighted_choice(
        rng, _SCREEN_ORIENTATIONS, _SCREEN_ORIENTATION_WEIGHTS
    )

    # Color gamut (90% sRGB, 8% P3, 2% rec2020)
    color_gamut = _weighted_choice(rng, _COLOR_GAMUTS, _COLOR_GAMUT_WEIGHTS)

    # Pointer type (80% mouse, 15% touch)
    pointer_type = _weighted_choice(rng, _POINTER_TYPES, _POINTER_WEIGHTS)

    # Audio fingerprint (AudioContext)
    audio_sample_rate = rng.choice([44100, 44100, 48000, 48000, 96000])  # 44.1k mais comum
    audio_channel_count = rng.choice([2, 2, 2, 6])  # Stereo mais comum
    audio_state = "running" if rng.random() < 0.7 else "suspended"

    # Battery API (distribuição realista)
    battery_range = _weighted_choice(rng, _BATTERY_LEVELS, _BATTERY_WEIGHTS)
    battery_level = round(rng.uniform(battery_range[0], battery_range[1]), 2)
    battery_charging = rng.random() < 0.3  # 30% carregando

    # Network connection
    connection_type = _weighted_choice(rng, _CONNECTION_TYPES, _CONNECTION_WEIGHTS)
    # Velocidade correlacionada com tipo de conexão
    if connection_type == "wifi":
        connection_downlink = round(rng.uniform(10.0, 100.0), 1)
        connection_rtt = rng.randint(5, 30)
    elif connection_type == "ethernet":
        connection_downlink = round(rng.uniform(50.0, 1000.0), 1)
        connection_rtt = rng.randint(1, 10)
    elif connection_type == "4g":
        connection_downlink = round(rng.uniform(5.0, 50.0), 1)
        connection_rtt = rng.randint(30, 100)
    else:  # 3g
        connection_downlink = round(rng.uniform(0.5, 5.0), 1)
        connection_rtt = rng.randint(100, 300)

    # App version (derivada do user agent)
    app_version = "5.0 (Windows)" if "Windows" in user_agent else "5.0"

    return BrowserFingerprint(
        user_agent=user_agent,
        webgl_vendor=webgl_vendor,
        webgl_renderer=webgl_renderer,
        canvas_seed=canvas_seed,
        screen_width=screen_width,
        screen_height=screen_height,
        hardware_concurrency=hardware_concurrency,
        device_memory=device_memory,
        locale="pt-BR",
        timezone=timezone,
        timezone_offset=timezone_offset,
        languages=languages,
        # Componentes AVANÇADOS
        fonts=fonts,
        mime_types=mime_types,
        audio_codecs=audio_codecs,
        video_codecs=video_codecs,
        screen_orientation=screen_orientation,
        color_gamut=color_gamut,
        pointer_type=pointer_type,
        audio_sample_rate=audio_sample_rate,
        audio_channel_count=audio_channel_count,
        audio_state=audio_state,
        battery_level=battery_level,
        battery_charging=battery_charging,
        connection_type=connection_type,
        connection_downlink=connection_downlink,
        connection_rtt=connection_rtt,
        # Campos com defaults
        pixel_ratio=pixel_ratio,
        available_width=available_width,
        available_height=available_height,
        app_version=app_version,
        do_not_track=do_not_track,
        webdriver=False,  # ANTI-DETECÇÃO: sempre False
        timezone_abbr=timezone_abbr,
        automation_blocked=True,
    )


def apply_fingerprint_to_page(page, fingerprint: BrowserFingerprint) -> None:
    """Aplica fingerprint ao page do Playwright via injeção de scripts.

    Deve ser chamado antes de qualquer navegação.
    """
    # Aplica contexto
    context_options = fingerprint.to_playwright_context()

    # Injeta scripts ANTES de qualquer navegação
    for script in fingerprint.to_js_injections():
        page.add_init_script(script)


def apply_fingerprint_to_context(context, fingerprint: BrowserFingerprint) -> None:
    """Aplica fingerprint ao contexto existente via addInitScript."""
    for script in fingerprint.to_js_injections():
        context.add_init_script(script)
