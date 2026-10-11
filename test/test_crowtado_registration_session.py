"""One authenticated registration session, real task gates, and safe resumption."""
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from moneymin import crowtado

EMAIL = "fixture@example.invalid"
PASSWORD = "fixture-only"


def linked(**changes):
    return [{"taskId": crowtado.TASK_ID_MINUTE, "capturedEmail": EMAIL,
             "linkedAt": "2026-10-09", "emailVerificationPending": False, **changes}]


def test_existing_link_is_read_once_without_browser_write_or_login(monkeypatch):
    session = object()
    login = Mock(side_effect=AssertionError("must not log in again"))
    browser = Mock(side_effect=AssertionError("must not reopen completed task"))
    trpc = Mock(return_value=linked())
    monkeypatch.setattr(crowtado, "login", login)
    monkeypatch.setattr(crowtado, "_registration_task_page", browser)
    monkeypatch.setattr(crowtado, "_site_trpc", trpc)
    assert crowtado.vincular_minute(EMAIL, PASSWORD, session=session) == linked()
    trpc.assert_called_once_with(session, "externalMobileCapture.myStatus",
                                {"taskId": crowtado.TASK_ID_MINUTE, "locale": "pt-BR"}, method="GET")


def test_task_scoped_legacy_response_can_omit_redundant_id(monkeypatch):
    status = linked()
    del status[0]["taskId"]
    monkeypatch.setattr(crowtado, "_site_trpc", Mock(return_value=status))
    monkeypatch.setattr(crowtado, "_registration_task_page", Mock(side_effect=AssertionError("already linked")))
    assert crowtado.vincular_minute(EMAIL, PASSWORD, session=object()) == status


@pytest.mark.parametrize("changes,code", [
    ({"capturedEmail": "another@example.invalid"}, "minute_email_conflict"),
    ({"emailVerificationPending": True}, "email_verification"),
])
def test_saved_conflict_or_unverified_email_stops_without_rewriting(monkeypatch, changes, code):
    monkeypatch.setattr(crowtado, "_site_trpc", Mock(return_value=linked(**changes)))
    browser = Mock(side_effect=AssertionError("must preserve existing binding"))
    monkeypatch.setattr(crowtado, "_registration_task_page", browser)
    with pytest.raises(crowtado.CrowtadoError) as error:
        crowtado.vincular_minute(EMAIL, PASSWORD, session=object())
    assert error.value.account_issue_code == code


@pytest.mark.parametrize("status", [[], [{}], linked(taskId="different-task"), linked(linkedAt=None)])
def test_unconfirmed_final_status_never_completes_or_repeats_task(monkeypatch, status):
    session, page = object(), object()
    @contextmanager
    def browser(s):
        assert s is session
        yield page
    monkeypatch.setattr(crowtado, "_registration_task_page", browser)
    mutation = Mock()
    monkeypatch.setattr(crowtado, "_complete_minute_task_page", mutation)
    monkeypatch.setattr(crowtado, "_site_trpc", Mock(side_effect=[[], status]))
    with pytest.raises(crowtado.CrowtadoError, match="não confirmou"):
        crowtado.vincular_minute(EMAIL, PASSWORD, session=session)
    mutation.assert_called_once_with(page, EMAIL)


def test_rate_limit_on_read_does_not_start_task_or_another_session(monkeypatch):
    request = Mock(side_effect=crowtado.CrowtadoError("Limited", code="rate_limit", http_status=429))
    monkeypatch.setattr(crowtado, "_site_trpc", request)
    monkeypatch.setattr(crowtado, "login", Mock(side_effect=AssertionError("unexpected login")))
    monkeypatch.setattr(crowtado, "_registration_task_page", Mock(side_effect=AssertionError("unexpected browser")))
    with pytest.raises(crowtado.CrowtadoError) as error:
        crowtado.vincular_minute(EMAIL, PASSWORD, session=object())
    assert error.value.http_status == 429
    assert request.call_count == 1


def test_complete_demographics_are_not_written_again(monkeypatch):
    session = object()
    trpc = Mock(return_value={"state": "complete"})
    monkeypatch.setattr(crowtado, "_site_trpc", trpc)
    monkeypatch.setattr(crowtado, "login", Mock(side_effect=AssertionError("unexpected login")))
    assert crowtado.preencher_demografia(EMAIL, PASSWORD, 5, 1999, "male", session=session) == {"state": "complete"}
    assert trpc.call_count == 1
    assert trpc.call_args.args[1] == "profile.demographicsStatus"


class TaskPage:
    """The email field is inaccessible until the two task gates are passed."""
    def __init__(self, *, fail_save=False, saved=False):
        self.events = []
        self.phase = "email" if saved else "proceed"
        self.email = EMAIL if saved else ""
        self.saved = saved
        self.fail_save = fail_save
        self.url = crowtado.SITE_BASE + "/pt-BR/dashboard/tasks/egocentric-household-mobile-latam"

    def goto(self, *_args, **_kwargs):
        raise AssertionError("must preserve the task already open in signup's tab")

    def wait_for_function(self, *_args, **_kwargs):
        pass

    def evaluate(self, *_args):
        return EMAIL

    def get_by_role(self, role, *, name, **kwargs):
        kind = "headmount" if not isinstance(name, str) else {"Prossiga": "proceed", "Salvar e-mail": "save"}[name]
        return self.Element(self, kind)

    def get_by_label(self, name, **kwargs):
        assert name == "E-mail usado no Minute"
        return self.Element(self, "email")

    def get_by_text(self, *_args, **_kwargs):
        return self.Element(self, "saved")

    @contextmanager
    def expect_response(self, predicate, **kwargs):
        response = SimpleNamespace(url="https://www.crowtado.com/api/trpc/externalMobileCapture.saveEmail",
                                   request=SimpleNamespace(method="POST"), status=200)
        assert predicate(response)
        yield SimpleNamespace(value=response)

    class Element:
        def __init__(self, page, kind):
            self.page, self.kind = page, kind
        @property
        def first(self):
            return self
        def count(self):
            return int(self.page.phase == self.kind or self.kind == "save" and not self.page.saved
                       or self.kind == "saved" and self.page.saved)
        def is_visible(self):
            return bool(self.count())
        def is_enabled(self):
            return not self.page.saved
        def click(self, **kwargs):
            self.page.events.append(self.kind)
            if self.kind == "save" and self.page.fail_save:
                raise TimeoutError("uncertain response containing private provider text")
            self.page.phase = {"proceed": "headmount", "headmount": "email", "save": "saved"}[self.kind]
        def wait_for(self, **kwargs):
            assert self.page.phase == "email"
        def input_value(self):
            return self.page.email
        def fill(self, email, **kwargs):
            assert self.page.phase == "email"
            self.page.events.append("fill")
            self.page.email = email


def test_task_clicks_proceed_headmount_no_and_save_in_order():
    page = TaskPage()
    crowtado._complete_minute_task_page(page, EMAIL)
    assert page.events == ["proceed", "headmount", "fill", "save"]


def test_ambiguous_save_is_not_clicked_twice_and_hides_provider_content():
    page = TaskPage(fail_save=True)
    with pytest.raises(crowtado.CrowtadoError) as error:
        crowtado._complete_minute_task_page(page, EMAIL)
    assert page.events.count("save") == 1
    assert error.value.remote_effect_possible is True
    assert "private provider" not in str(error.value)


def test_already_saved_task_does_not_click_or_fill_again():
    page = TaskPage(saved=True)
    crowtado._complete_minute_task_page(page, EMAIL)
    assert page.events == []


def test_signup_page_is_reused_without_opening_a_new_browser():
    page = object()
    session = SimpleNamespace(_registration_page=page)
    with crowtado._registration_task_page(session) as reused:
        assert reused is page


def test_session_transfer_rejects_wrong_account_before_reading_cookies():
    page = Mock()
    page.evaluate.return_value = {"id": "fixture-session", "email": "other@example.invalid"}
    with pytest.raises(crowtado.CrowtadoError):
        crowtado._registration_session_from_page(page, EMAIL)
    page.context.cookies.assert_not_called()


def test_missing_authenticated_session_is_actionable_without_second_login(monkeypatch):
    page = Mock()
    page.wait_for_function.side_effect = TimeoutError('private browser details')
    login = Mock(side_effect=AssertionError('must not retry authentication'))
    monkeypatch.setattr(crowtado, 'login', login)
    with pytest.raises(crowtado.CrowtadoError) as error:
        crowtado._registration_session_from_page(page, EMAIL)
    assert error.value.account_issue_code == 'signup_session_unconfirmed'
    assert error.value.phase == 'signup_confirmation'
    assert error.value.remote_effect_possible is True
    assert 'private browser' not in str(error.value)
    login.assert_not_called()


@pytest.mark.parametrize('url,phase', [
    ('https://untrusted.invalid/v1/client', 'signup_confirmation'),
    (crowtado.CLERK_BASE + '/v1/client', 'signup_submission'),
    (crowtado.CLERK_BASE + '/v1/environment', 'signup_confirmation'),
])
def test_session_observer_ignores_unrelated_response_without_reading_body(url, phase):
    response = SimpleNamespace(url=url, status=403, json=Mock(side_effect=AssertionError('unrelated body')))
    assert crowtado._signup_session_response_error(response, phase) is None
    response.json.assert_not_called()


def test_generic_session_403_is_not_a_confirmed_account_ban():
    response = SimpleNamespace(url=crowtado.CLERK_BASE + '/v1/client', status=403, json=lambda: {})
    assert crowtado._signup_session_response_error(response, 'signup_confirmation') is None
