import email
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nexo_actions import TOOL_REGISTRY
from nexo_actions.email.base import EmailProvider, message_attachments, message_text
from nexo_actions.email.imap_provider import IMAPProvider
from nexo_actions.email.service import EmailService


class FakeProvider(EmailProvider):
    def search(self, query, limit=10):
        return [
            {"message_id": "2", "subject": f"Fatura {query}", "from": "conta@example.com", "date": "hoje"},
            {"message_id": "1", "subject": "Anterior"},
        ][:limit]

    def read(self, message_id):
        return {"message_id": message_id, "subject": "Fatura Claro", "body": "Resumo"}

    def attachments(self, message_id):
        return [{"attachment_id": "a1", "filename": "fatura.pdf", "size_bytes": 3}]

    def attachment_bytes(self, message_id, attachment_id):
        return "fatura.pdf", b"pdf"

    def send(self, to, subject, body):
        return {"sent": True, "to": to, "subject": subject}

    def reply(self, message_id, body):
        return {"sent": True, "message_id": message_id, "kind": "reply"}

    def forward(self, message_id, to, note=""):
        return {"sent": True, "message_id": message_id, "to": to, "kind": "forward"}


class EmailServiceTests(unittest.TestCase):
    def setUp(self):
        self.config = {
            "providers": {
                "Suntec": {"type": "fake", "address": "financeiro@example.com"}
            }
        }
        self.service = EmailService(
            self.config,
            providers={"fake": lambda account, config: FakeProvider(account, config)},
        )

    def test_account_routing_is_case_insensitive(self):
        self.assertEqual(self.service.provider("suntec").account, "Suntec")
        self.assertEqual(self.service.accounts()[0]["address"], "financeiro@example.com")

    def test_search_latest_read_and_attachments_share_provider_contract(self):
        self.assertEqual(self.service.find_latest("Suntec", "Claro")["message_id"], "2")
        self.assertEqual(self.service.read("Suntec", "2")["body"], "Resumo")
        self.assertEqual(self.service.attachments("Suntec", "2")[0]["filename"], "fatura.pdf")

    def test_send_reply_and_forward_share_provider_contract(self):
        self.assertTrue(self.service.send("Suntec", "ana@example.com", "Olá", "Corpo")["sent"])
        self.assertEqual(self.service.reply("Suntec", "2", "Obrigada")["kind"], "reply")
        self.assertEqual(
            self.service.forward("Suntec", "2", "bia@example.com", "Veja")["kind"],
            "forward",
        )

    @patch("nexo_actions.email.service.wsl_to_windows", side_effect=lambda value: str(value))
    @patch("nexo_actions.email.service._resolve_path")
    def test_save_attachment_never_overwrites_by_default(self, resolve, _convert):
        with tempfile.TemporaryDirectory() as temp:
            resolve.return_value = Path(temp)
            first = self.service.save_attachment("Suntec", "2", "a1")
            second = self.service.save_attachment("Suntec", "2", "a1")
            self.assertTrue(first["ok"])
            self.assertTrue(second["conflict"])
            self.assertEqual((Path(temp) / "fatura.pdf").read_bytes(), b"pdf")


class EmailMimeTests(unittest.TestCase):
    def test_plain_body_and_attachment_are_extracted(self):
        raw = (
            "From: Claro <conta@example.com>\n"
            "Subject: Fatura\n"
            "MIME-Version: 1.0\n"
            "Content-Type: multipart/mixed; boundary=x\n\n"
            "--x\nContent-Type: text/plain; charset=utf-8\n\nSua fatura chegou.\n"
            "--x\nContent-Type: application/pdf\nContent-Disposition: attachment; filename=fatura.pdf\n"
            "Content-Transfer-Encoding: base64\n\ncGRm\n--x--\n"
        )
        message = email.message_from_string(raw)
        self.assertEqual(message_text(message), "Sua fatura chegou.")
        attachments = message_attachments(message)
        self.assertEqual(attachments[0]["filename"], "fatura.pdf")
        self.assertEqual(attachments[0]["size_bytes"], 3)

    def test_imap_secret_file_must_be_private(self):
        with tempfile.TemporaryDirectory() as temp:
            secret = Path(temp) / "secret.json"
            secret.write_text(json.dumps({"password": "hidden"}), encoding="utf-8")
            secret.chmod(0o600)
            provider = IMAPProvider("Suntec", {"secret_file": str(secret)})
            self.assertEqual(provider._secret(), "hidden")
            secret.chmod(0o644)
            with self.assertRaises(PermissionError):
                provider._secret()


class EmailRegistryTests(unittest.TestCase):
    def test_email_tools_are_metadata_only(self):
        for name in (
            "email_accounts", "email_search", "email_find_latest", "email_read",
            "email_list_attachments", "email_save_attachment", "email_send",
            "email_reply", "email_forward",
        ):
            spec = TOOL_REGISTRY.get(name)
            self.assertEqual(spec.category, "email")
            self.assertEqual(spec.log_policy, "metadata_only")
        for name in ("email_send", "email_reply", "email_forward"):
            spec = TOOL_REGISTRY.get(name)
            self.assertEqual(spec.risk_level, "high")
            self.assertTrue(spec.requires_confirmation)


if __name__ == "__main__":
    unittest.main()
