from __future__ import annotations
from datetime import datetime

from src.core.models import Inquiry, Property


_SIGNATURE = """\
■□━━━━━━━━━━━━━━━━━━━━━━━━
リノベーション・デザイナーズなどピッタリな賃貸をお探しなら
レントマガジン株式会社 / Rent Magazine

本社
〒472-0012
愛知県知立市八ツ田町1-8-3 YATSUDA APARTMENT 1F
TEL：0566-70-7117　Fax：0566-70-7228
Mail：contact@rentmagazine.jp
WEB：https://rentmagazine.jp
Instagram：https://www.instagram.com/rent_magazine/
━━━━━━━━━━━━━━━━━━━━━━━━■□"""

_SCHEDULE_TEMPLATE = """\
ご日程をお知らせ頂けましたら、確認の後ご返信させて頂きます。
下記ひな形をご活用ください。

・第一希望　月　日　午前　午後　時頃
・第二希望　月　日　午前　午後　時頃
・第三希望　月　日　午前　午後　時頃"""

_CTA_COMMON = """\
まだまだご紹介しきれない魅力を、ぜひお部屋をご覧いただいた際にお伝えしたいと思っております。
もしよろしければ弊社、割引制度も設けていますので、お部屋情報とあわせてご案内させて頂きたく、一度ご来店はいかがでしょうか？

個人割引：
https://rentmagazine.jp/campaign1/

さらにネット掲載前の最新情報や、オーナー様の意向で非公開になっている情報等も、ご条件にあわせてご紹介させて頂くことも可能です。
もちろん、お選び頂いたお部屋の現地まで弊社の車でご案内させて頂きます。
遠方などの場合は、オンライン内見もご案内させていただいています。"""

_OUT_OF_HOURS_NOTE = """\
※ 現在営業時間外（10:00〜18:00）のため、翌営業日にご確認のうえご返信いたします。
お急ぎの場合は、翌営業日に改めてご連絡をいただけますと幸いです。"""


class EmailAssembler:
    """
    Assembles the final reply email from fixed text blocks and AI-generated segments.
    AI-generated text is injected only at designated placeholders.
    All fixed sections (company info, URLs, signature) are never touched by AI.
    """

    def __init__(self, company: dict, is_business_hours: bool):
        self._co = company
        self._in_hours = is_business_hours

    def build_first_mail(
        self,
        inquiry: Inquiry,
        property_intro: str,         # AI generated (vacant path)
        visit_invitation: str,       # AI generated (both paths)
        alt_intros: list[tuple[Property, str]],  # [(Property, AI intro)] for unavailable path
    ) -> tuple[str, str]:
        """Return (subject, html_body)."""
        subject = f"【{inquiry.inquiry_property_name}】お問い合わせありがとうございます"

        if inquiry.is_vacant:
            body = self._vacant_body(inquiry, property_intro, visit_invitation)
        else:
            body = self._unavailable_body(inquiry, alt_intros, visit_invitation)

        return subject, self._wrap_html(body)

    def build_second_mail(self, inquiry: Inquiry, property_intro: str,
                          visit_invitation: str,
                          alt_intros: list[tuple[Property, str]]) -> tuple[str, str]:
        subject = f"【{inquiry.inquiry_property_name}】先日はお問い合わせありがとうございます！"
        preamble = (
            "先日はお問い合わせ誠にありがとうございます。\n"
            f"レントマガジン株式会社の{self._co['staff_name']}と申します。\n\n"
            "その後、ご検討のほどはいかがでしょうか。\n"
            "まだご案内のご予定が決まっていないようでしたら、ぜひご検討いただけますと幸いです。"
        )
        if inquiry.is_vacant:
            body = preamble + "\n\n" + self._vacant_body(inquiry, property_intro, visit_invitation)
        else:
            body = preamble + "\n\n" + self._unavailable_body(inquiry, alt_intros, visit_invitation)
        return subject, self._wrap_html(body)

    def build_third_mail(self, inquiry: Inquiry, property_intro: str,
                         visit_invitation: str,
                         alt_intros: list[tuple[Property, str]]) -> tuple[str, str]:
        subject = f"【{inquiry.inquiry_property_name}】先日はお問い合わせありがとうございます！"
        preamble = (
            "先日はお問い合わせ誠にありがとうございます。\n"
            f"レントマガジン株式会社の{self._co['staff_name']}と申します。\n\n"
            "改めてご連絡させていただきました。\n"
            "実際にご来店・ご内覧いただくお客様の中には、写真では分かりにくい広さや周辺環境、"
            "日当たり、同条件の比較物件なども確認される方が多いです。\n"
            "「まずは比較してみたい」という段階でも大丈夫ですので、"
            "ご希望に近いお部屋をいくつかあわせてご案内させていただければと思います。"
        )
        if inquiry.is_vacant:
            body = preamble + "\n\n" + self._vacant_body(inquiry, property_intro, visit_invitation)
        else:
            body = preamble + "\n\n" + self._unavailable_body(inquiry, alt_intros, visit_invitation)
        return subject, self._wrap_html(body)

    # ── private builders ─────────────────────────────────────────────────────

    def _header(self, customer_name: str) -> str:
        return (
            f"{customer_name}様\n\n"
            "この度はお問い合わせ頂きありがとうございます。\n"
            f"レントマガジン株式会社でございます。"
        )

    def _vacant_body(self, inquiry: Inquiry, property_intro: str,
                     visit_invitation: str) -> str:
        prop = inquiry.matched_property
        prop_block = ""
        if prop:
            commission = "\n※弊社でのご成約の場合、仲介手数料無料でご案内させていただいております。\n" if prop.is_commission_free else ""
            prop_block = (
                f"◆お問い合わせ物件\n"
                f"物件名：{prop.name}\n"
                f"{prop.url}\n"
                f"今回お送りさせて頂きました弊社WEBのURLの初期費用に記載させていただいています"
                f"ので、ご参考いただければと思います。{commission}\n\n"
                f"{property_intro}\n"
            )

        hours_note = f"\n{_OUT_OF_HOURS_NOTE}\n" if not self._in_hours else ""

        return "\n\n".join(filter(None, [
            self._header(inquiry.customer_name),
            prop_block,
            visit_invitation,
            _CTA_COMMON,
            _SCHEDULE_TEMPLATE,
            f"{inquiry.customer_name}様にピッタリなお部屋をしっかりとご紹介させて頂きます！\n"
            "ご返事お待ちしていますので、よろしくお願い申し上げます。",
            hours_note,
            _SIGNATURE,
        ]))

    def _unavailable_body(self, inquiry: Inquiry,
                          alt_intros: list[tuple[Property, str]],
                          visit_invitation: str) -> str:
        alt_blocks: list[str] = []
        for prop, intro in alt_intros:
            commission = "（仲介手数料無料）" if prop.is_commission_free else ""
            alt_blocks.append(
                f"◆おすすめ物件{commission}\n"
                f"物件名：{prop.name}\n"
                f"{prop.url}\n"
                f"今回お送りさせて頂きました弊社WEBのURLの初期費用に記載させていただいています"
                f"ので、ご参考いただければと思います。\n\n"
                f"{intro}"
            )
        alt_section = "\n\n".join(alt_blocks) if alt_blocks else "現在条件に近い物件を検索中です。"

        header = (
            f"{self._header(inquiry.customer_name)}\n\n"
            "せっかくお問い合わせいただきましたが、タッチ差でお申し込みが入ってしまい、"
            "ご紹介できない状況です。\n"
            "そこで弊社がオススメの物件をご紹介させていただきます。"
        )

        hours_note = f"\n{_OUT_OF_HOURS_NOTE}\n" if not self._in_hours else ""

        return "\n\n".join(filter(None, [
            header,
            alt_section,
            visit_invitation,
            _CTA_COMMON,
            _SCHEDULE_TEMPLATE,
            f"{inquiry.customer_name}様にピッタリなお部屋をしっかりとご紹介させて頂きます！\n"
            "ご返事お待ちしていますので、よろしくお願い申し上げます。",
            hours_note,
            _SIGNATURE,
        ]))

    @staticmethod
    def _wrap_html(text: str) -> str:
        escaped = (text
                   .replace("&", "&amp;")
                   .replace("<", "&lt;")
                   .replace(">", "&gt;")
                   .replace("\n", "<br>"))
        return (
            '<html><body style="font-family:\'Hiragino Mincho ProN\',serif;font-size:10pt;">'
            f"{escaped}"
            "</body></html>"
        )
