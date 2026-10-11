"""Real Chromium DOM checks against intercepted, local-only signup fixtures."""
from contextlib import nullcontext
from types import SimpleNamespace
from urllib.parse import urlsplit
from unittest.mock import Mock

import pytest

from moneymin import browser_fingerprint, crowtado, hostinger_mail, human_behavior, registration_proxy


@pytest.mark.parametrize("challenge", ["none", "pending", "completed", "pending_disabled_fields", "delayed_form"])
def test_real_browser_form_and_manual_verification_without_provider_requests(tmp_path, monkeypatch, challenge):
    from playwright.sync_api import sync_playwright

    try:
        executable = crowtado._chrome_exe()
    except crowtado.CrowtadoError:
        pytest.skip("Local Chrome/Chromium is required for the isolated DOM contract")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=executable, headless=True)
        try:
            context = browser.new_context(service_workers="block")
            unexpected = []
            widget = "" if challenge in {"none", "delayed_form"} else (
                '<div class="cf-turnstile" style="width:200px;height:50px">Local verification fixture</div>'
                '<button id="finish-fixture" onclick="document.querySelector(\'.cf-turnstile\').remove(); document.querySelectorAll(\'input\').forEach(el => el.disabled = false)">Complete fixture</button>'
                f'<input type="hidden" name="cf-turnstile-response" value="{"local-fixture-response" if challenge == "completed" else ""}">'
            )
            html = """<!doctype html><html><body>
                <div id="terms-gate" aria-disabled="true">
                <input id="emailAddress-field" value="stale@example.invalid">
                <input id="password-field" type="password" value="stale-password">
                <button class="cl-formButtonPrimary" onclick="window.signupClicks++;
                    history.pushState({}, '', '/pt-BR/dashboard')">Submit local fixture</button>
                </div>
                <div style="height:1800px"></div>
                <label><input type="checkbox" id="signup-consent"
                    onchange="document.querySelector('#terms-gate').setAttribute('aria-disabled', String(!this.checked))">
                    Accept fixture terms</label>
                <script>window.signupClicks = 0;
                    history.replaceState({}, '', '/pt-BR/sign-up');</script>
            """ + widget + "</body></html>"
            if challenge == "pending_disabled_fields":
                html = html.replace('<input id=', '<input disabled id=')
            if challenge == "delayed_form":
                html = html.replace('<div id="terms-gate"', '<template id="delayed-form"><div id="terms-gate"')
                html = html.replace('</div>\n                <div style=', '</div></template>\n                <div style=', 1)
                html = html.replace("<script>window.signupClicks = 0;", """<script>
                    setTimeout(() => {
                        const template = document.querySelector('#delayed-form');
                        template.replaceWith(template.content.cloneNode(true));
                        document.querySelector('#terms-gate').setAttribute('aria-disabled',
                            String(!document.querySelector('#signup-consent').checked));
                    }, 5500);
                    window.signupClicks = 0;""")

            def fixture_route(route):
                url = urlsplit(route.request.url)
                if url.netloc == "www.crowtado.com" and url.path in {"/sign-up", "/pt-BR/sign-up"}:
                    route.fulfill(status=200, content_type="text/html", body=html)
                elif url.path == "/favicon.ico":
                    route.fulfill(status=204)
                else:
                    unexpected.append(route.request.url)
                    route.abort()

            # Every page request is intercepted. No provider account, credentials,
            # verification service or user's browser profile is used.
            context.route("**/*", fixture_route)
            context.set_offline(True)
            bridge = SimpleNamespace(contexts=[context], close=lambda: None)
            stub = SimpleNamespace(chromium=SimpleNamespace(connect_over_cdp=lambda _url: bridge))
            monkeypatch.setattr("playwright.sync_api.sync_playwright", lambda: nullcontext(stub))
            monkeypatch.setattr("subprocess.Popen", Mock(return_value=Mock(poll=lambda: None)))
            monkeypatch.setattr(crowtado, "CHROME_PROFILE", tmp_path / "profile")
            monkeypatch.setattr(crowtado, "_wait_port", lambda *_args: None)
            monkeypatch.setattr(registration_proxy, "endpoint", lambda: None)
            monkeypatch.setattr(hostinger_mail, "max_uid", lambda _email: 0)
            def unexpected_identity_override(*_args, **_kwargs):
                raise AssertionError("Signup must use the browser's native identity")
            monkeypatch.setattr(browser_fingerprint, "generate_fingerprint", unexpected_identity_override)
            monkeypatch.setattr(browser_fingerprint, "apply_fingerprint_to_context", unexpected_identity_override)
            monkeypatch.setattr(human_behavior, "session_warming", unexpected_identity_override)
            monkeypatch.setattr(human_behavior, "handle_consent_checkbox", lambda *_args: True)
            monkeypatch.setattr(human_behavior, "random_delay", lambda *_args, **_kwargs: None)
            events = []

            def progress(event):
                events.append(event)
                if event == "captcha":
                    page = context.pages[-1]
                    assert page.evaluate("window.signupClicks") == 0
                    # This button belongs to our local HTML fixture, not a real
                    # CAPTCHA. It simulates the user completing verification.
                    page.locator("#finish-fixture").click()

            try:
                crowtado.criar_conta("fixture@example.invalid", "fixture-password", on_progress=progress)
            except crowtado.CrowtadoError as exc:
                raise AssertionError(f"Local fixture failed at {exc.phase}: {exc.account_issue_code}; {exc.__context__}") from exc
            page = context.pages[-1]
            assert page.url == "https://www.crowtado.com/pt-BR/dashboard"
            assert page.input_value("#emailAddress-field") == "fixture@example.invalid"
            assert page.input_value("#password-field") == "fixture-password"
            assert page.evaluate("window.signupClicks") == 1
            assert page.locator("#signup-consent").is_checked()
            assert ("captcha" in events) is challenge.startswith("pending")
            assert unexpected == []
        finally:
            browser.close()
