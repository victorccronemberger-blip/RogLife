"""
human_behavior.py — Simulação de comportamento humano realista.

Implementa:
- Mouse movements com curvas de Bézier (não linhas retas)
- Typing patterns humanos (velocidade variável, erros ocasionais)
- Delays aleatórios (tempo de pensamento)
- Scroll behavior natural
- Click patterns realistas
- Session warming (navegação pré-cadastro)

Objetivo: Tornar a automação indistinguível de um humano real.
"""
from __future__ import annotations

import math
import random
import time
from typing import Any


def bezier_curve(
    start: tuple[float, float],
    end: tuple[float, float],
    control_points: list[tuple[float, float]] | None = None,
    steps: int = 50,
) -> list[tuple[float, float]]:
    """Gera pontos ao longo de uma curva de Bézier.

    Args:
        start: Ponto inicial (x, y)
        end: Ponto final (x, y)
        control_points: Lista de pontos de controle (2-3 pontos)
        steps: Número de pontos a gerar

    Returns:
        Lista de pontos (x, y) ao longo da curva
    """
    if control_points is None:
        # Gera 2 pontos de controle aleatórios para curva natural
        dx = end[0] - start[0]
        dy = end[1] - start[1]

        control_points = [
            (start[0] + dx * 0.3 + random.uniform(-50, 50),
             start[1] + dy * 0.3 + random.uniform(-50, 50)),
            (start[0] + dx * 0.7 + random.uniform(-50, 50),
             start[1] + dy * 0.7 + random.uniform(-50, 50)),
        ]

    points = [start] + control_points + [end]
    n = len(points) - 1
    curve_points = []

    for i in range(steps + 1):
        t = i / steps
        x, y = 0.0, 0.0

        # Fórmula de Bézier
        for j, (px, py) in enumerate(points):
            # Coeficiente binomial
            coeff = math.comb(n, j) * (t ** j) * ((1 - t) ** (n - j))
            x += coeff * px
            y += coeff * py

        curve_points.append((x, y))

    return curve_points


def human_mouse_movement(
    page,
    target_x: float,
    target_y: float,
    start_x: float | None = None,
    start_y: float | None = None,
    duration_ms: int | None = None,
) -> None:
    """Move o mouse de forma realista até o target.

    Args:
        page: Playwright page object
        target_x: Coordenada X do target
        target_y: Coordenada Y do target
        start_x: Coordenada X inicial (None = posição atual)
        start_y: Coordenada Y inicial (None = posição atual)
        duration_ms: Duração do movimento (None = calculado automaticamente)
    """
    # Chrome via CDP frequentemente não informa viewport_size.
    if start_x is None or start_y is None:
        viewport = page.viewport_size or {}
        if start_x is None:
            start_x = float(viewport.get("width") or 1280) / 2
        if start_y is None:
            start_y = float(viewport.get("height") or 720) / 2

    # Calcula distância
    distance = math.sqrt((target_x - start_x) ** 2 + (target_y - start_y) ** 2)

    # Duração baseada na distância (humanos levam mais tempo para distâncias maiores)
    if duration_ms is None:
        # 0.3-0.8 ms por pixel, com mínimo de 200ms e máximo de 1500ms
        duration_ms = max(200, min(1500, int(distance * random.uniform(0.3, 0.8))))

    # Gera curva de Bézier
    steps = max(20, int(distance / 5))  # Mais passos para movimentos maiores
    curve_points = bezier_curve(
        (start_x, start_y),
        (target_x, target_y),
        steps=steps,
    )

    # Move o mouse ao longo da curva
    for x, y in curve_points:
        page.mouse.move(x, y)
        page.wait_for_timeout(max(1, int(duration_ms / steps * random.uniform(0.5, 1.5))))


def human_click(page, selector: str, timeout: int = 10000) -> None:
    """Clica em um elemento de forma realista.

    Args:
        page: Playwright page object
        selector: CSS selector do elemento
        timeout: Timeout para esperar o elemento
    """
    element = page.wait_for_selector(selector, timeout=timeout, state="visible")
    box = element.bounding_box() if element is not None else None
    if not box:
        raise RuntimeError("elemento sem posição para clique humano")

    target_x = box["x"] + box["width"] * random.uniform(0.3, 0.7)
    target_y = box["y"] + box["height"] * random.uniform(0.3, 0.7)
    human_mouse_movement(page, target_x, target_y)
    page.wait_for_timeout(int(random.uniform(50, 200)))
    page.mouse.click(target_x, target_y)
    page.wait_for_timeout(int(random.uniform(100, 300)))


def human_type(page, selector: str, text: str, delay_ms: int | None = None) -> None:
    """Digita texto de forma realista.

    Args:
        page: Playwright page object
        selector: CSS selector do input
        text: Texto a digitar
        delay_ms: Delay entre teclas (None = calculado automaticamente)
    """
    human_click(page, selector)
    page.wait_for_timeout(int(random.uniform(100, 300)))

    for char in text:
        if delay_ms is None:
            char_delay = random.uniform(50, 150)
            if char in " .,;:!?":
                char_delay *= random.uniform(1.5, 2.5)
            if random.random() < 0.05:
                char_delay *= random.uniform(2.0, 4.0)
        else:
            char_delay = delay_ms

        page.wait_for_timeout(int(char_delay))
        if random.random() < 0.02 and char.isalpha():
            wrong_char = chr(ord(char) + random.choice([-1, 1]))
            page.keyboard.type(wrong_char, delay=0)
            page.wait_for_timeout(int(random.uniform(100, 300)))
            page.keyboard.press("Backspace")
            page.wait_for_timeout(int(random.uniform(50, 150)))
        page.keyboard.type(char, delay=0)


def human_scroll(page, direction: str = "down", distance: int | None = None) -> None:
    """Scroll de forma realista.

    Args:
        page: Playwright page object
        direction: "up" ou "down"
        distance: Distância em pixels (None = aleatório)
    """
    if distance is None:
        distance = random.randint(100, 400)

    delta = distance if direction == "down" else -distance

    # Scroll em incrementos (humanos não scrollam de uma vez)
    steps = random.randint(3, 8)
    per_step = delta / steps

    for _ in range(steps):
        page.mouse.wheel(0, per_step)
        page.wait_for_timeout(int(random.uniform(50, 150)))


def random_delay(min_ms: int = 500, max_ms: int = 2000, page=None) -> None:
    """Delay aleatório (tempo de pensamento).

    Com uma página do Playwright, a espera fica no relógio da página.
    """
    delay = random.uniform(min_ms, max_ms)
    if page is None:
        time.sleep(delay / 1000)
        return
    page.wait_for_timeout(int(delay))


def session_warming(page, base_url: str, pages_to_visit: int = 3) -> None:
    """Aquece a sessão navegando por algumas páginas antes do cadastro.

    Args:
        page: Playwright page object
        base_url: URL base do site (ex: "https://www.crowtado.com")
        pages_to_visit: Número de páginas para visitar (3-5 recomendado)
    """
    # Não inclui a rota de cadastro: visitá-la aqui dispararia o fluxo oficial.
    pages = [
        "/pt-BR",
        "/pt-BR/for-contributors",
        "/pt-BR/community",
        "/pt-BR/contact",
    ]

    selected_pages = random.sample(pages, min(pages_to_visit, len(pages)))

    for path in selected_pages:
        try:
            page.goto(base_url + path, wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_timeout(int(random.uniform(1000, 4000)))
            if random.random() < 0.7:
                human_scroll(page, "down", random.randint(100, 500))
                page.wait_for_timeout(int(random.uniform(500, 1500)))
            if random.random() < 0.3:
                human_scroll(page, "up", random.randint(100, 300))
                page.wait_for_timeout(int(random.uniform(300, 800)))
        except Exception:
            continue


def handle_consent_checkbox(page, timeout: int = 10000) -> bool:
    """Lida com o checkbox de consentimento de termos.

    Args:
        page: Playwright page object
        timeout: Timeout para esperar o checkbox

    Returns:
        True se o checkbox foi clicado com sucesso, False caso contrário
    """
    try:
        # Tenta encontrar o checkbox de consentimento
        checkbox_selectors = [
            'input[type="checkbox"]',
            '#signup-consent',
            'input[id*="consent"]',
            'input[id*="terms"]',
            'input[id*="agree"]',
        ]

        for selector in checkbox_selectors:
            try:
                checkbox = page.wait_for_selector(selector, timeout=2000, state="visible")
                if checkbox:
                    # Verifica se já está marcado
                    if not checkbox.is_checked():
                        human_click(page, selector)
                        page.wait_for_timeout(int(random.uniform(300, 600)))
                    return True
            except Exception:
                continue

        return False

    except Exception:
        return False
