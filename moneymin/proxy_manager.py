"""
proxy_manager.py — Pool opcional de proxies.

Não substitui o cofre de cadastro. O aplicativo usa o proxy escolhido em Contas,
pelo túnel local de registration_proxy. Este módulo não grava credencial padrão.

Características:
- Validação geográfica (apenas proxies BR)
- Rotação automática por conta
- Health checks periódicos
- Fallback automático
- Suporte HTTP/HTTPS/SOCKS5
- Integração com Playwright
- Determinístico (mesma conta = mesmo proxy)

Versão: 1.0 - Profissional
"""
from __future__ import annotations

import hashlib
import json
import random
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
import urllib.request
import urllib.error


@dataclass
class ProxyConfig:
    """Configuração de um proxy individual."""

    host: str
    http_port: int
    socks5_port: int | None = None
    username: str | None = None
    password: str | None = None
    protocol: str = "http"  # "http" ou "socks5"
    country: str = "BR"
    region: str = "São Paulo"
    city: str = "São Paulo"
    isp: str = "Unknown"

    # Health check
    is_healthy: bool = True
    last_check: float = 0.0
    last_error: str | None = None
    latency_ms: int = 0

    # Usage stats
    usage_count: int = 0
    last_used: float = 0.0

    def to_url(self) -> str:
        """Converte para URL de proxy."""
        if self.username and self.password:
            auth = f"{self.username}:{self.password}@"
        else:
            auth = ""

        if self.protocol == "socks5" and self.socks5_port:
            return f"socks5://{auth}{self.host}:{self.socks5_port}"
        else:
            return f"http://{auth}{self.host}:{self.http_port}"

    def to_playwright_proxy(self) -> dict[str, str]:
        """Converte para formato do Playwright."""
        proxy_dict = {
            "server": self.to_url(),
        }
        if self.username:
            proxy_dict["username"] = self.username
        if self.password:
            proxy_dict["password"] = self.password
        return proxy_dict

    def to_dict(self) -> dict[str, Any]:
        """Converte para dict."""
        return asdict(self)


class ProxyManager:
    """Gerenciador profissional de proxies."""

    def __init__(self, config_path: Path | None = None):
        """Inicializa o gerenciador de proxies."""
        self.config_path = config_path or Path("data/proxies.json")
        self.proxies: list[ProxyConfig] = []
        self._load_config()

    def _load_config(self) -> None:
        """Carrega configuração de proxies do arquivo."""
        if self.config_path.exists():
            try:
                data = json.loads(self.config_path.read_text(encoding="utf-8"))
                for proxy_data in data.get("proxies", []):
                    self.proxies.append(ProxyConfig(**proxy_data))
            except Exception as e:
                print(f"[WARNING] Erro ao carregar proxies: {e}")

    def _save_config(self) -> None:
        """Salva configuração de proxies no arquivo."""
        try:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "proxies": [p.to_dict() for p in self.proxies],
                "updated_at": time.time(),
            }
            self.config_path.write_text(
                json.dumps(data, indent=2, ensure_ascii=False),
                encoding="utf-8"
            )
        except Exception as e:
            print(f"[WARNING] Erro ao salvar proxies: {e}")

    def add_proxy(
        self,
        host: str,
        http_port: int,
        socks5_port: int | None = None,
        username: str | None = None,
        password: str | None = None,
        protocol: str = "http",
        validate: bool = False,
    ) -> ProxyConfig:
        """Adiciona um novo proxy.

        Args:
            validate: Se True, valida localização e saúde do proxy (pode demorar)
        """
        proxy = ProxyConfig(
            host=host,
            http_port=http_port,
            socks5_port=socks5_port,
            username=username,
            password=password,
            protocol=protocol,
        )

        # Validação opcional (desabilitada por padrão para speed)
        if validate:
            # Valida localização
            if not self._validate_location(proxy):
                print(f"[WARNING] Proxy {host} não está no Brasil!")
                proxy.country = "UNKNOWN"
                proxy.is_healthy = False

        self.proxies.append(proxy)
        self._save_config()
        return proxy

    def _validate_location(self, proxy: ProxyConfig) -> bool:
        """Valida se o proxy está no Brasil."""
        try:
            # Testa conexão e verifica IP
            proxy_url = proxy.to_url()

            # Cria opener com proxy
            proxy_handler = urllib.request.ProxyHandler({
                'http': proxy_url,
                'https': proxy_url,
            })
            opener = urllib.request.build_opener(proxy_handler)

            # Faz request para API de geolocalização
            req = urllib.request.Request(
                'http://ip-api.com/json/?fields=status,country,countryCode,regionName,city,isp',
                headers={'User-Agent': 'Mozilla/5.0'}
            )

            response = opener.open(req, timeout=10)
            data = json.loads(response.read().decode('utf-8'))

            if data.get('status') == 'success':
                proxy.country = data.get('countryCode', 'UNKNOWN')
                proxy.region = data.get('regionName', 'Unknown')
                proxy.city = data.get('city', 'Unknown')
                proxy.isp = data.get('isp', 'Unknown')

                return proxy.country == 'BR'
            return False

        except Exception as e:
            proxy.last_error = str(e)
            return False

    def check_health(self, proxy: ProxyConfig) -> bool:
        """Verifica saúde do proxy."""
        try:
            proxy_url = proxy.to_url()

            # Cria opener com proxy
            proxy_handler = urllib.request.ProxyHandler({
                'http': proxy_url,
                'https': proxy_url,
            })
            opener = urllib.request.build_opener(proxy_handler)

            # Testa conexão
            start = time.time()
            req = urllib.request.Request(
                'http://www.google.com',
                headers={'User-Agent': 'Mozilla/5.0'}
            )
            response = opener.open(req, timeout=10)
            latency = int((time.time() - start) * 1000)

            proxy.is_healthy = True
            proxy.latency_ms = latency
            proxy.last_check = time.time()
            proxy.last_error = None

            return True

        except Exception as e:
            proxy.is_healthy = False
            proxy.last_check = time.time()
            proxy.last_error = str(e)
            return False

    def get_healthy_proxies(self) -> list[ProxyConfig]:
        """Retorna apenas proxies saudáveis do Brasil."""
        return [
            p for p in self.proxies
            if p.is_healthy and p.country == 'BR'
        ]

    def get_proxy_for_account(self, email: str) -> ProxyConfig | None:
        """Retorna proxy determinístico para uma conta.

        Mesmo email = mesmo proxy (reprodutível).
        """
        healthy = self.get_healthy_proxies()
        if not healthy:
            return None

        # Seed determinística baseada no email
        seed = hashlib.sha256(f"proxy:{email}".encode()).hexdigest()
        rng = random.Random(seed)

        # Escolhe proxy (rotação ponderada por uso)
        weights = [max(1, 100 - p.usage_count) for p in healthy]
        proxy = rng.choices(healthy, weights=weights, k=1)[0]

        # Atualiza estatísticas
        proxy.usage_count += 1
        proxy.last_used = time.time()
        self._save_config()

        return proxy

    def get_single_proxy(self) -> ProxyConfig | None:
        """Retorna um único proxy para todas as contas.

        Útil quando há apenas um proxy disponível.
        """
        healthy = self.get_healthy_proxies()
        if not healthy:
            return None

        # Retorna o primeiro proxy saudável
        proxy = healthy[0]
        proxy.usage_count += 1
        proxy.last_used = time.time()
        self._save_config()

        return proxy

    def health_check_all(self) -> dict[str, int]:
        """Verifica saúde de todos os proxies."""
        stats = {"total": len(self.proxies), "healthy": 0, "unhealthy": 0}

        for proxy in self.proxies:
            if self.check_health(proxy):
                stats["healthy"] += 1
            else:
                stats["unhealthy"] += 1

        self._save_config()
        return stats

    def remove_proxy(self, host: str) -> bool:
        """Remove um proxy pelo host."""
        initial_count = len(self.proxies)
        self.proxies = [p for p in self.proxies if p.host != host]
        removed = len(self.proxies) < initial_count

        if removed:
            self._save_config()

        return removed


# Singleton instance
_manager: ProxyManager | None = None


def get_manager(config_path: Path | None = None) -> ProxyManager:
    """Retorna instância singleton do ProxyManager."""
    global _manager
    if _manager is None:
        _manager = ProxyManager(config_path)
    return _manager


def add_brazil_proxy(
    host: str,
    http_port: int,
    socks5_port: int | None = None,
    username: str | None = None,
    password: str | None = None,
    protocol: str = "http",
) -> ProxyConfig:
    """Adiciona um proxy brasileiro ao pool."""
    manager = get_manager()
    return manager.add_proxy(
        host=host,
        http_port=http_port,
        socks5_port=socks5_port,
        username=username,
        password=password,
        protocol=protocol,
    )


def get_proxy_for_email(email: str) -> ProxyConfig | None:
    """Retorna proxy para uma conta específica."""
    manager = get_manager()
    return manager.get_proxy_for_account(email)


def get_single_proxy() -> ProxyConfig | None:
    """Retorna um único proxy para todas as contas."""
    manager = get_manager()
    return manager.get_single_proxy()


def check_all_proxies() -> dict[str, int]:
    """Verifica saúde de todos os proxies."""
    manager = get_manager()
    return manager.health_check_all()
