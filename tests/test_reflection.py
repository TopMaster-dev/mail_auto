"""
Reflection identification, exercised against the real mail formats.

The inbox carries vendor mail, portal notifications and other WordPress forms
on the same address, so identification must be positive: only a recognised
reflection format counts as an inquiry.
"""
import unittest

from src.core.reflection import identify

# Real 自社フォーム inquiry (values replaced, structure verbatim).
WEBFORM_INQUIRY = """\
お問い合わせ物件：ガレージ付き戸建（https://rentmagazine.jp/estate/garage-house/）
お問い合わせ内容：実際に内覧したい

お引越し時期：いいものがあれば
お引越し人数：2人
お引越し理由：その他
希望条件：
お名前：石原慎也
メールアドレス：i.shinya@example.com
お電話番号：08085382412
"""

# Real 解約フォーム — same address, contains bank details and date of birth.
WEBFORM_CANCELLATION = """\
■賃貸借契約情報
建物名：ボニートロッサ2
部屋号室：303
■ご契約者様情報
名前：角谷伶実
生年月日：1999年1月6日
メールアドレス：remi@example.com
電話番号：08051276740
■精算金振込先口座
金融機関名：楽天銀行
口座番号：1678636
■ご解約情報
解約希望日：2026年09月21日
"""

# Real SUUMO / リクルートJDS reflection, after NFKC normalisation.
SUUMO = """\
レントマガジン(株)  新家 一朗様
反響到着日時 2026/08/12 02:04:10
ID:118218700103
お問合せ企画:SUUMOネット/賃貸物件枠問い合わせ(モバイル)
問合せ物件
会社名:レントマガジン(株)
物件種別:マンション
物件コード:100363623020
物件名:サンステージエクセル203
最寄り駅:名鉄西尾線/南安城
所在地:愛知県安城市東新町
賃料:6.3万円
間取り:1LDK
物件詳細画面:https://suumo.jp/chintai/bc_100363623020/
お客様プロフィール
名前(漢字):三宅しのぶ
名前(カナ):
メールアドレス:shinobu@example.com
TEL:090-9315-6945
お問合せ内容:この部屋を実際に見学したい
"""

VENDOR_NEWSLETTER = """\
【アグライア久が原】新築物件のご紹介です！！
好条件の物件が出ましたのでご案内いたします。
物件名：アグライア久が原
お問い合わせはこちらまで。
"""


def _raw(body, sender="contact@rentmagazine.jp", subject=""):
    return {"from_addr": sender, "subject": subject, "body": body}


class TestWebForm(unittest.TestCase):
    def test_inquiry_form_is_identified(self):
        r = identify(_raw(WEBFORM_INQUIRY, subject="空室状況を問い合わせる｜レントマガジン株式会社"))
        self.assertIsNotNone(r)
        self.assertEqual(r.source, "webform")

    def test_customer_not_the_form_sender(self):
        # The whole point: the envelope sender is the company's own address.
        r = identify(_raw(WEBFORM_INQUIRY, subject="空室状況を問い合わせる"))
        self.assertEqual(r.customer_name, "石原慎也")
        self.assertEqual(r.customer_email, "i.shinya@example.com")
        self.assertNotIn("rentmagazine", r.customer_email)

    def test_property_name_and_url_split(self):
        r = identify(_raw(WEBFORM_INQUIRY, subject="空室状況を問い合わせる"))
        self.assertEqual(r.property_name, "ガレージ付き戸建")
        self.assertEqual(r.property_url,
                         "https://rentmagazine.jp/estate/garage-house/")

    def test_inquiry_text_collected(self):
        r = identify(_raw(WEBFORM_INQUIRY, subject="空室状況を問い合わせる"))
        self.assertIn("実際に内覧したい", r.inquiry_text)


class TestNonInquiries(unittest.TestCase):
    def test_cancellation_form_is_rejected(self):
        # Carries bank account, DOB and home address — must never be drafted.
        self.assertIsNone(
            identify(_raw(WEBFORM_CANCELLATION,
                          subject="ご解約お申し込み｜レントマガジン株式会社")))

    def test_cancellation_rejected_even_without_subject_hint(self):
        # It lacks お問い合わせ物件, which is the discriminating field.
        self.assertIsNone(identify(_raw(WEBFORM_CANCELLATION, subject="")))

    def test_vendor_newsletter_is_rejected(self):
        self.assertIsNone(
            identify(_raw(VENDOR_NEWSLETTER, sender="info@ielove-propertyinfo.jp",
                          subject="【アグライア久が原】新築物件のご紹介です！！")))

    def test_answering_service_report_is_rejected(self):
        self.assertIsNone(
            identify(_raw("応対記録\n受付日時：2026/08/12",
                          sender="bell24-tas-report1@e-hisyo.com",
                          subject="応対記録")))


class TestSuumo(unittest.TestCase):
    def _r(self):
        return identify(_raw(SUUMO, sender="system@jds.suumo.jp",
                             subject="[リクルートJDS]反響お知らせメール"))

    def test_identified(self):
        self.assertIsNotNone(self._r())
        self.assertEqual(self._r().source, "suumo")

    def test_customer_from_profile_block(self):
        r = self._r()
        self.assertEqual(r.customer_name, "三宅しのぶ")
        self.assertEqual(r.customer_email, "shinobu@example.com")

    def test_property_name_taken_not_suumo_url(self):
        r = self._r()
        self.assertEqual(r.property_name, "サンステージエクセル203")
        self.assertEqual(r.property_url, "")   # suumo.jp link is not our listing

    def test_context_extras(self):
        r = self._r()
        self.assertEqual(r.extras["所在地"], "愛知県安城市東新町")
        self.assertEqual(r.extras["間取り"], "1LDK")
        self.assertIn("見学", r.inquiry_text)

    def test_jds_notification_without_customer_block_is_rejected(self):
        self.assertIsNone(
            identify(_raw("システムメンテナンスのお知らせ\n平素より…",
                          sender="system@jds.suumo.jp",
                          subject="[リクルートJDS]メンテナンスのお知らせ")))


if __name__ == "__main__":
    unittest.main()
