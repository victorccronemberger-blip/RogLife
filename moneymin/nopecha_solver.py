"""
nopecha_solver.py — Integração com Nopecha para resolução de CAPTCHAs.

Resolve automaticamente:
- Cloudflare Turnstile
- hCaptcha
- reCAPTCHA v2/v3
- Text CAPTCHA

API: https://developers.nopecha.com/
"""
from __future__ import annotations

import json
import logging
import time
import urllib.request
import urllib.error
from typing import Any


logger = logging.getLogger(__name__)


# Configuração da API Nopecha (API key fixa)
NOPECHA_API_KEY = "sub_1UJ1lmCRwBwvt6pt2bUE0TAi"
NOPECHA_API_URL = "https://api.nopecha.com"


class NopechaError(Exception):
    """Erro na resolução de CAPTCHA via Nopecha."""
    pass


def solve_turnstile(
    site_key: str,
    url: str,
    api_key: str = NOPECHA_API_KEY,
    timeout: int = 120,
) -> str:
    """Resolve Cloudflare Turnstile via Nopecha.

    Args:
        site_key: Site key do Turnstile (encontrado no HTML)
        url: URL da página onde o CAPTCHA aparece
        api_key: API key do Nopecha
        timeout: Timeout em segundos

    Returns:
        Token de solução do CAPTCHA

    Raises:
        NopechaError: Se falhar na resolução
    """
    logger.info(f"Nopecha: Criando task para resolver Turnstile...")
    logger.info(f"  Site key: {site_key}")
    logger.info(f"  URL: {url}")
    logger.debug(f"  API Key: {api_key[:10]}...")

    # Cria task para resolver Turnstile
    task_data = {
        "type": "turnstile",
        "sitekey": site_key,
        "url": url,
    }

    # Envia request para criar task
    try:
        req = urllib.request.Request(
            f"{NOPECHA_API_URL}/create_task",
            data=json.dumps(task_data).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )
        logger.debug(f"Nopecha: Enviando request para {NOPECHA_API_URL}/create_task")
        response = urllib.request.urlopen(req, timeout=30)
        result = json.loads(response.read().decode("utf-8"))
        logger.debug(f"Nopecha: Resposta recebida: {result}")

        if "error" in result:
            raise NopechaError(f"Erro ao criar task: {result['error']}")

        task_id = result.get("task_id")
        if not task_id:
            raise NopechaError("Task ID não retornado")

        logger.info(f"Nopecha: Task ID criado: {task_id}")

    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8") if e.fp else "Sem corpo"
        raise NopechaError(f"HTTP Error {e.code}: {error_body}")
    except Exception as e:
        raise NopechaError(f"Falha ao criar task: {e}")

    # Aguarda solução
    logger.info(f"Nopecha: Aguardando solução (timeout: {timeout}s)...")
    start_time = time.time()
    attempts = 0

    while time.time() - start_time < timeout:
        attempts += 1
        try:
            req = urllib.request.Request(
                f"{NOPECHA_API_URL}/get_task_result/{task_id}",
                headers={
                    "Authorization": f"Bearer {api_key}",
                },
            )
            response = urllib.request.urlopen(req, timeout=30)
            result = json.loads(response.read().decode("utf-8"))

            status = result.get("status")
            logger.debug(f"Nopecha: Tentativa {attempts} - Status: {status}")

            if status == "ready":
                token = result.get("solution", {}).get("token")
                if token:
                    logger.info(f"Nopecha: Token recebido (length: {len(token)})")
                    return token
                raise NopechaError("Solução não contém token")
            elif status == "processing":
                logger.debug(f"Nopecha: Task ainda processando...")
                time.sleep(3)
                continue
            elif status == "failed":
                error = result.get("error", "Erro desconhecido")
                raise NopechaError(f"Task falhou: {error}")
            else:
                logger.warning(f"Nopecha: Status desconhecido: {status}")
                time.sleep(3)

        except urllib.error.URLError as e:
            logger.warning(f"Nopecha: Erro de rede: {e}")
            time.sleep(3)
            continue
        except Exception as e:
            raise NopechaError(f"Erro ao consultar resultado: {e}")

    raise NopechaError(f"Timeout após {timeout}s ({attempts} tentativas)")


def extract_turnstile_site_key(page) -> str | None:
    """Extrai o site key do Turnstile da página.

    Args:
        page: Playwright page object

    Returns:
        Site key ou None se não encontrado
    """
    try:
        logger.debug("Extraindo site key do Turnstile...")

        # Tenta encontrar o site key no iframe do Turnstile
        site_key = page.evaluate("""
            () => {
                // Método 1: Procura em iframes do Turnstile
                const iframes = document.querySelectorAll('iframe[src*="challenges.cloudflare.com"]');

                for (const iframe of iframes) {
                    const src = iframe.src;

                    // Tenta vários padrões de sitekey
                    const patterns = [
                        /[?&]sitekey=([^&]+)/,
                        /sitekey[=:]([^&"']+)/,
                        /data-sitekey="([^"]+)"/,
                    ];

                    for (const pattern of patterns) {
                        const match = src.match(pattern);
                        if (match) {
                            return match[1];
                        }
                    }
                }

                // Método 2: Procura em elementos com data-sitekey
                const elements = document.querySelectorAll('[data-sitekey]');
                for (const el of elements) {
                    const key = el.getAttribute('data-sitekey');
                    if (key) {
                        return key;
                    }
                }

                // Método 3: Procura em scripts do Clerk/Turnstile
                const scripts = document.querySelectorAll('script');
                for (const script of scripts) {
                    const text = script.textContent || '';

                    // Vários padrões para extrair sitekey de scripts
                    const patterns = [
                        /sitekey['":\\s]+['"]([^'"]+)['"]/,
                        /sitekey['":\\s]+([^'"\\s,}]+)/,
                        /TURNSTILE_SITEKEY['":\\s]+['"]([^'"]+)['"]/,
                        /turnstile.*?sitekey['":\\s]+['"]([^'"]+)['"]/i,
                    ];

                    for (const pattern of patterns) {
                        const match = text.match(pattern);
                        if (match) {
                            return match[1];
                        }
                    }
                }

                // Método 4: Procura em inputs hidden
                const inputs = document.querySelectorAll('input[type="hidden"]');
                for (const input of inputs) {
                    const name = input.name || '';
                    if (name.includes('turnstile') || name.includes('captcha')) {
                        const value = input.value;
                        if (value && value.length > 20) {
                            return value;
                        }
                    }
                }

                return null;
            }
        """)

        if site_key:
            logger.info(f"Site key extraído: {site_key[:30]}...")
        else:
            logger.warning("Site key NÃO encontrado!")

        return site_key
    except Exception as e:
        logger.error(f"Erro ao extrair site key: {e}")
        return None


def inject_turnstile_solution(page, token: str) -> bool:
    """Injeta a solução do Turnstile na página.

    Args:
        page: Playwright page object
        token: Token de solução do CAPTCHA

    Returns:
        True se injetado com sucesso
    """
    try:
        logger.debug(f"Injetando solução do Turnstile na página...")
        logger.debug(f"  Token length: {len(token)}")

        result = page.evaluate("""
            (token) => {
                let injected = false;

                // Método 1: Procura o callback do Turnstile na window
                if (window.turnstile && typeof window.turnstile.getResponse === 'function') {
                    // Tenta definir o token via método interno
                    try {
                        window.turnstile._responses = window.turnstile._responses || {};
                        window.turnstile._responses['default'] = token;
                        injected = true;
                    } catch (e) {
                        // Ignora erro
                    }
                }

                // Método 2: Procura pelo input hidden onde o token deve ser injetado
                const inputs = document.querySelectorAll('input[name*="cf-turnstile"], input[name*="captcha"], input[name*="token"]');

                for (const input of inputs) {
                    input.value = token;
                    input.dispatchEvent(new Event('input', { bubbles: true }));
                    input.dispatchEvent(new Event('change', { bubbles: true }));
                    injected = true;
                }

                // Método 3: Tenta trigger do callback se existir
                const callbackNames = ['turnstileCallback', 'onTurnstileSuccess', 'captchaCallback'];
                for (const callbackName of callbackNames) {
                    if (window[callbackName] && typeof window[callbackName] === 'function') {
                        try {
                            window[callbackName](token);
                            injected = true;
                        } catch (e) {
                            // Ignora erro
                        }
                    }
                }

                // Método 4: Procura por formulários e injeta o token
                const forms = document.querySelectorAll('form');

                for (const form of forms) {
                    let tokenInput = form.querySelector('input[name*="cf-turnstile"], input[name*="captcha"], input[name*="token"]');
                    if (!tokenInput) {
                        // Cria input hidden para o token
                        tokenInput = document.createElement('input');
                        tokenInput.type = 'hidden';
                        tokenInput.name = 'cf-turnstile-response';
                        form.appendChild(tokenInput);
                    }
                    tokenInput.value = token;
                    injected = true;
                }

                // Método 5: Dispatch custom event
                window.dispatchEvent(new CustomEvent('turnstile-solved', { detail: { token } }));

                return injected;
            }
        """, token)

        if result:
            logger.info("Solução injetada com sucesso!")
        else:
            logger.warning("Falha ao injetar solução")

        return result
    except Exception as e:
        logger.error(f"Erro ao injetar solução: {e}")
        return False


def solve_captcha_if_present(page, timeout: int = 120) -> bool:
    """Detecta e resolve CAPTCHA se presente na página.

    Args:
        page: Playwright page object
        timeout: Timeout para resolução

    Returns:
        True se CAPTCHA foi resolvido ou não estava presente
    """
    logger.info("Verificando presença de CAPTCHA Turnstile...")

    # Verifica se há Turnstile presente
    has_turnstile = page.evaluate("""
        () => {
            const iframes = document.querySelectorAll('iframe[src*="challenges.cloudflare.com"]');
            const divs = document.querySelectorAll('[class*="turnstile"], [id*="turnstile"]');
            return iframes.length > 0 || divs.length > 0;
        }
    """)

    if not has_turnstile:
        logger.debug("Nenhum CAPTCHA detectado - continuando...")
        return True  # Não há CAPTCHA

    logger.warning("CAPTCHA Cloudflare Turnstile DETECTADO!")

    # Extrai site key
    site_key = extract_turnstile_site_key(page)
    if not site_key:
        logger.error("Não foi possível extrair site key do Turnstile")
        return False

    logger.info(f"Site key: {site_key[:30]}...")

    # Resolve via Nopecha
    try:
        logger.info("Resolvendo CAPTCHA via Nopecha...")
        token = solve_turnstile(
            site_key=site_key,
            url=page.url,
            timeout=timeout,
        )
        logger.info("CAPTCHA resolvido com sucesso!")

        # Injeta solução
        if inject_turnstile_solution(page, token):
            logger.info("Solução injetada na página")

            # Aguarda um pouco para o Turnstile processar
            logger.debug("Aguardando Turnstile processar...")
            page.wait_for_timeout(2000)

            return True
        else:
            logger.error("Falha ao injetar solução")
            return False

    except NopechaError as e:
        logger.error(f"Erro ao resolver CAPTCHA: {e}")
        return False
    except Exception as e:
        logger.error(f"Erro inesperado: {e}", exc_info=True)
        return False


def wait_for_captcha_resolution(page, timeout: int = 180) -> bool:
    """Aguarda até que o CAPTCHA seja resolvido e o formulário avançar.

    Args:
        page: Playwright page object
        timeout: Timeout máximo em segundos

    Returns:
        True se CAPTCHA foi resolvido e formulário avançou
    """
    logger.info(f"Aguardando resolução do CAPTCHA (timeout: {timeout}s)...")
    start_time = time.time()
    attempts = 0

    while time.time() - start_time < timeout:
        attempts += 1
        logger.debug(f"Tentativa {attempts}...")

        # Tenta resolver CAPTCHA se presente
        if not solve_captcha_if_present(page, timeout=30):
            logger.debug("CAPTCHA ainda presente - aguardando...")
            time.sleep(2)
            continue

        # Verifica se o formulário avançou (não está mais na página de CAPTCHA)
        # Isso varia dependendo do site
        time.sleep(1)

        # Verifica se ainda há CAPTCHA
        has_turnstile = page.evaluate("""
            () => {
                const iframes = document.querySelectorAll('iframe[src*="challenges.cloudflare.com"]');
                return iframes.length > 0;
            }
        """)

        if not has_turnstile:
            logger.info("CAPTCHA resolvido e removido da página!")
            return True

        logger.debug("CAPTCHA ainda presente...")
        time.sleep(2)

    logger.error(f"Timeout após {timeout}s ({attempts} tentativas)")
    return False
