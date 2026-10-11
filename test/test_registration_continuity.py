"""Real browser regression for OTP, native profile and task in one signup tab."""
from contextlib import nullcontext
import json
from types import SimpleNamespace
from urllib.parse import urlsplit
from unittest.mock import Mock

import pytest

from moneymin import crowtado, hostinger_mail, human_behavior, registration_proxy


EMAIL = "fixture@example.invalid"
PASSWORD = "fixture-only"
TASK_PATH = "/pt-BR/dashboard/tasks/egocentric-household-mobile-latam"

HTML = r"""<!doctype html><html><head><meta charset="utf-8"></head><body>
<label><input type="checkbox" id="signup-consent">Fixture terms</label>
<input id="emailAddress-field"><input id="password-field" type="password">
<button class="cl-formButtonPrimary" onclick="startSignup()">Create fixture account</button>
<script>
window.fixtureEvents = [];
history.replaceState({}, '', '/pt-BR/sign-up');
async function startSignup() {
    fixtureEvents.push('signup');
    await fetch('https://clerk.crowtado.com/v1/client/sign_ups', {method: 'POST'});
    history.pushState({}, '', '/pt-BR/sign-up/verify-email-address');
    document.body.innerHTML = '<input data-input-otp oninput="verifyEmail(this.value)">';
}
function verifyEmail(code) {
    if (code.length !== 6) return;
    fixtureEvents.push('otp');
    window.Clerk = {session: {id: 'fixture-session'}, user: {
        primaryEmailAddress: {emailAddress: 'fixture@example.invalid'}, banned: false, locked: false}};
    history.replaceState({}, '', '/pt-BR/complete-profile?next=%2Fpt-BR%2Fdashboard');
    document.body.innerHTML = `<div role="dialog" aria-labelledby="required-demographics-title">
        <h1 id="required-demographics-title">Required profile fixture</h1>
        <form onsubmit="saveProfile(event)">
        <select autocomplete="bday-month"><option value="">Month</option><option value="5">May</option></select>
        <input type="number" autocomplete="bday-year">
        <label><input type="radio" name="gender" value="male">Male fixture</label>
        <button type="submit">Continue fixture</button></form></div>`;
}
async function saveProfile(event) {
    event.preventDefault();
    fixtureEvents.push('profile');
    const profile = {birthMonth: Number(document.querySelector('[autocomplete="bday-month"]').value),
        birthYear: Number(document.querySelector('[autocomplete="bday-year"]').value),
        gender: document.querySelector('[name="gender"]:checked').value};
    const response = await fetch('/api/trpc/profile.saveDemographics?batch=1', {
        method: 'POST', body: JSON.stringify({'0': {json: profile}})});
    if (!response.ok) return;
    history.replaceState({}, '', '/pt-BR/dashboard');
    document.body.innerHTML = '<a href="/pt-BR/dashboard/tasks" onclick="showTasks(event)">Tasks</a>';
}
function showTasks(event) {
    event.preventDefault();
    fixtureEvents.push('tasks-list');
    history.pushState({}, '', '/pt-BR/dashboard/tasks');
    document.body.innerHTML = '<a href="/pt-BR/dashboard/tasks/egocentric-household-mobile-latam" onclick="showTask(event)">Minute fixture</a>';
}
function showTask(event) {
    event.preventDefault();
    fixtureEvents.push('task');
    history.pushState({}, '', '/pt-BR/dashboard/tasks/egocentric-household-mobile-latam');
    document.body.innerHTML = '<button onclick="showEquipment()">Prossiga</button>';
}
function showEquipment() {
    fixtureEvents.push('proceed');
    document.body.innerHTML = '<p>Esta tarefa exige um suporte de cabeça</p><button onclick="showEmail()">Não, já tenho um</button>';
}
function showEmail() {
    fixtureEvents.push('equipment');
    document.body.innerHTML = '<label>E-mail usado no Minute<input id="minute-email"></label><button onclick="saveEmail()">Salvar e-mail</button>';
}
async function saveEmail() {
    fixtureEvents.push('link');
    const email = document.querySelector('#minute-email').value;
    const response = await fetch('/api/trpc/externalMobileCapture.saveEmail?batch=1', {
        method: 'POST', body: JSON.stringify({'0': {json: {capturedEmail: email}}})});
    if (!response.ok) return;
    document.body.innerHTML = '<label>E-mail usado no Minute<input id="minute-email"></label><p>E-mail salvo</p>';
    document.querySelector('#minute-email').value = email;
}
</script></body></html>"""


def test_otp_profile_and_task_continue_in_same_real_browser_without_forced_navigation(tmp_path, monkeypatch):
    from playwright.sync_api import sync_playwright

    try:
        executable = crowtado._chrome_exe()
    except crowtado.CrowtadoError:
        pytest.skip("Local Chromium is required for the isolated continuation contract")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=executable, headless=True)
        try:
            context = browser.new_context(service_workers="block")
            unexpected, documents, writes = [], [], []
            profile, link = {}, {}

            def fixture_route(route):
                url = urlsplit(route.request.url)
                if route.request.is_navigation_request():
                    documents.append(url.path)
                if url.netloc == 'www.crowtado.com' and url.path == '/sign-up':
                    route.fulfill(status=200, content_type='text/html', body=HTML)
                elif url.netloc == 'clerk.crowtado.com' and url.path == '/v1/client/sign_ups':
                    writes.append('signup')
                    route.fulfill(status=200, content_type='application/json', body='{}')
                elif url.netloc == 'www.crowtado.com' and url.path == '/api/trpc/profile.saveDemographics':
                    writes.append('profile')
                    profile.update(json.loads(route.request.post_data)['0']['json'])
                    route.fulfill(status=200, content_type='application/json', body='[{"result":{"data":{"json":{"state":"complete"}}}}]')
                elif url.netloc == 'www.crowtado.com' and url.path == '/api/trpc/externalMobileCapture.saveEmail':
                    writes.append('link')
                    link.update(json.loads(route.request.post_data)['0']['json'])
                    route.fulfill(status=200, content_type='application/json', body='[{"result":{"data":{"json":{}}}}]')
                elif url.path == '/favicon.ico':
                    route.fulfill(status=204)
                else:
                    unexpected.append(route.request.url)
                    route.abort()

            context.route('**/*', fixture_route)
            context.set_offline(True)
            bridge = SimpleNamespace(contexts=[context], close=lambda: None)
            stub = SimpleNamespace(chromium=SimpleNamespace(connect_over_cdp=lambda _url: bridge))
            monkeypatch.setattr('playwright.sync_api.sync_playwright', lambda: nullcontext(stub))
            monkeypatch.setattr('subprocess.Popen', Mock(return_value=Mock(poll=lambda: None)))
            monkeypatch.setattr(crowtado, 'CHROME_PROFILE', tmp_path / 'profile')
            monkeypatch.setattr(crowtado, '_wait_port', lambda *_args: None)
            monkeypatch.setattr(registration_proxy, 'endpoint', lambda: None)
            monkeypatch.setattr(hostinger_mail, 'max_uid', lambda _email: 0)
            monkeypatch.setattr(hostinger_mail, 'wait_for_code', lambda *_args, **_kwargs: '123456')
            monkeypatch.setattr(human_behavior, 'random_delay', lambda *_args, **_kwargs: None)
            monkeypatch.setattr(crowtado, 'login', Mock(side_effect=AssertionError('must keep the signup session')))

            def read_status(session, procedure, _payload, method='POST'):
                assert method == 'GET', 'signup must use the native form for writes'
                assert session._registration_page is context.pages[0]
                if procedure == 'profile.demographicsStatus':
                    return {'state': 'complete' if profile else 'incomplete'}
                assert procedure == 'externalMobileCapture.myStatus'
                return [{'taskId': crowtado.TASK_ID_MINUTE, 'linkedAt': 'fixture-confirmed',
                         'capturedEmail': link['capturedEmail'], 'emailVerificationPending': False}] if link else []

            monkeypatch.setattr(crowtado, '_site_trpc', read_status)
            continued = []

            def continue_signup(session):
                continued.append(session)
                crowtado.preencher_demografia(EMAIL, PASSWORD, 5, 1999, 'male', session=session)
                crowtado.vincular_minute(EMAIL, PASSWORD, session=session)

            try:
                crowtado.criar_conta(EMAIL, PASSWORD, on_authenticated=continue_signup)
            except crowtado.CrowtadoError as error:
                page = context.pages[0]
                raise AssertionError(f"Local continuation failed: {page.evaluate('window.fixtureEvents')}; {error.__context__}") from error
            assert len(context.pages) == len(continued) == 1
            page = context.pages[0]
            assert page.url == crowtado.SITE_BASE + TASK_PATH
            assert page.evaluate('window.fixtureEvents') == [
                'signup', 'otp', 'profile', 'tasks-list', 'task', 'proceed', 'equipment', 'link']
            assert profile == {'birthMonth': 5, 'birthYear': 1999, 'gender': 'male'}
            assert link == {'capturedEmail': EMAIL}
            assert writes == ['signup', 'profile', 'link']
            assert documents == ['/sign-up']
            assert unexpected == []
        finally:
            browser.close()
