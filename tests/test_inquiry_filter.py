"""Unit tests for the inbox InquiryFilter."""
import unittest

from src.core.inquiry_filter import InquiryFilter

_IGNORE = ["noreply@", "no-reply-", "lancers.co.jp", "accounts.google.com"]


class TestInquiryFilter(unittest.TestCase):
    def setUp(self):
        self.f = InquiryFilter(ignore_senders=_IGNORE, enabled=True)

    def test_blocks_noreply(self):
        self.assertFalse(self.f.is_inquiry(
            {"from_addr": "noreply@portal.com", "subject": "物件", "body": "内見"}))

    def test_blocks_known_portal(self):
        self.assertFalse(self.f.is_inquiry(
            {"from_addr": "info@lancers.co.jp", "subject": "通知", "body": ""}))

    def test_blocks_google_notification(self):
        self.assertFalse(self.f.is_inquiry(
            {"from_addr": "no-reply@accounts.google.com", "subject": "セキュリティ通知", "body": ""}))

    def test_accepts_customer(self):
        self.assertTrue(self.f.is_inquiry(
            {"from_addr": "taro@gmail.com", "subject": "物件について", "body": "内見希望です"}))

    def test_accepts_customer_without_property_signal(self):
        # A plain question with no property keyword is still processed.
        self.assertTrue(self.f.is_inquiry(
            {"from_addr": "hanako@example.com", "subject": "質問", "body": "教えてください"}))

    def test_reply_always_accepted(self):
        # Even from a normally-ignored sender, a reply must be processed.
        self.assertTrue(self.f.is_inquiry(
            {"from_addr": "noreply@portal.com", "in_reply_to": "<abc@x>", "body": ""}))

    def test_bulk_header_blocked(self):
        self.assertFalse(self.f.is_inquiry(
            {"from_addr": "news@shop.com", "subject": "セール",
             "body": "物件", "headers_present": ["List-Unsubscribe", "From"]}))

    def test_disabled_passes_everything(self):
        f = InquiryFilter(ignore_senders=_IGNORE, enabled=False)
        self.assertTrue(f.is_inquiry({"from_addr": "noreply@portal.com", "body": ""}))


class TestStrictMode(unittest.TestCase):
    def setUp(self):
        self.f = InquiryFilter(ignore_senders=_IGNORE, enabled=True,
                               require_property_signal=True)

    def test_blocks_mail_without_property_signal(self):
        # A job notification with no property keyword.
        self.assertFalse(self.f.is_inquiry(
            {"from_addr": "jobs@indeedemail.example", "subject": "新着メッセージ", "body": "応募がありました"}))

    def test_blocks_empty_sender_without_signal(self):
        self.assertFalse(self.f.is_inquiry(
            {"from_addr": "", "subject": "お知らせ", "body": "ご案内です"}))

    def test_accepts_mail_with_property_signal(self):
        self.assertTrue(self.f.is_inquiry(
            {"from_addr": "taro@gmail.com", "subject": "物件について", "body": "内見希望"}))

    def test_reply_still_accepted_in_strict_mode(self):
        self.assertTrue(self.f.is_inquiry(
            {"from_addr": "noreply@x.com", "in_reply_to": "<abc>", "body": "ありがとう"}))


if __name__ == "__main__":
    unittest.main()
