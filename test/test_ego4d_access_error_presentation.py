"""Access failures must not be presented as a proven license refusal."""
import unittest

from moneymin.web.runner import friendly_campaign_error


class Ego4DAccessErrorPresentationTests(unittest.TestCase):
    def test_head_denial_does_not_assert_license_refusal(self):
        for error in (
            "acesso negado ao Ego4D para s3://private-bucket/private-key",
            "Ego4D: AccessDenied private-diagnostic",
        ):
            with self.subTest(error=error):
                message = friendly_campaign_error(error)
                self.assertIn("armazenamento do Ego4D", message)
                self.assertIn("credenciais AWS", message)
                self.assertIn("não confirma", message)
                self.assertNotIn("licença Ego4D não autorizou", message)
                self.assertNotIn("private", message)

    def test_explicit_invalid_key_takes_precedence_over_generic_denial(self):
        message = friendly_campaign_error(
            "Ego4D: AccessDenied; InvalidAccessKeyId private-diagnostic")
        self.assertIn("credenciais do Ego4D não foram aceitas", message)
        self.assertNotIn("licença", message)
        self.assertNotIn("private", message)

    def test_other_service_denial_does_not_blame_ego4d_or_accounts(self):
        message = friendly_campaign_error("AccessDenied private-diagnostic")
        self.assertIn("serviço recusou", message)
        for term in ("Ego4D", "licença", "Contas", "private"):
            self.assertNotIn(term, message)
