"""
The reference wording written into the sheet must reproduce the built-in mail.

It is offered to the client as "copy this and edit it". If rendering it produced
something different from what the system sends today, their first edit would
silently change wording they never touched.
"""
import unittest
from datetime import datetime

import seed_templates as seed
from src.email_builder import template_store as ts
from src.email_builder.assembler import EmailAssembler

CO = {"name": "レントマガジン株式会社", "staff_name": "新家",
      "tel_reservation": "0566-70-8282", "discount_url": "https://x/c",
      "mypage_url": "https://x/m", "line_url": "https://x/l"}


class TestReferenceWordingRoundTrips(unittest.TestCase):
    def setUp(self):
        self.wording = seed.current_wording(CO)
        self.inq = seed._sample_inquiry()

    def _built_in(self, kind):
        a = EmailAssembler(CO, True)
        build = {ts.FIRST: a.build_first_mail_parts,
                 ts.SECOND: a.build_second_mail_parts,
                 ts.THIRD: a.build_third_mail_parts}[kind]
        return build(self.inq, seed._MARK["AI紹介文"], "", [])

    def _rendered(self, kind):
        a = EmailAssembler(CO, True)
        subject, body = self.wording[kind]
        ctx = {
            "顧客名": self.inq.customer_name,
            "物件名": self.inq.inquiry_property_name,
            "物件URL": self.inq.matched_property.url,
            "AI紹介文": seed._MARK["AI紹介文"],
            "最寄駅": a._station_text(self.inq.matched_property),
            "担当者名": CO["staff_name"],
            "署名": __import__("src.email_builder.assembler", fromlist=["x"])._SIGNATURE,
            "物件情報": a._property_section(self.inq, seed._MARK["AI紹介文"], []),
        }
        return ts.render(subject, ctx), ts.render(body, ctx)

    def test_every_mail_round_trips_to_the_same_subject(self):
        for kind in ts.KINDS:
            with self.subTest(kind=kind):
                self.assertEqual(self._rendered(kind)[0], self._built_in(kind)[0])

    def test_every_mail_round_trips_to_the_same_body(self):
        for kind in ts.KINDS:
            with self.subTest(kind=kind):
                self.assertEqual(self._rendered(kind)[1].strip(),
                                 self._built_in(kind)[1].strip())


class TestReferenceWordingIsValid(unittest.TestCase):
    def test_the_offered_templates_all_pass_validation(self):
        """Pasting a reference row unedited must not trip the validator."""
        for kind, (subject, body) in seed.current_wording(CO).items():
            with self.subTest(kind=kind):
                self.assertEqual(ts.validate(subject, body, kind), [])

    def test_no_sentinel_leaks_into_the_sheet(self):
        for subject, body in seed.current_wording(CO).values():
            for mark in seed._MARK.values():
                self.assertNotIn(mark, subject)
                self.assertNotIn(mark, body)
            self.assertNotIn("〘URL〙", body)

    def test_the_staff_name_is_a_placeholder_not_baked_in(self):
        _, body = seed.current_wording(CO)[ts.SECOND]
        self.assertIn("{担当者名}", body)
        self.assertNotIn("新家", body)


class TestSheetLayout(unittest.TestCase):
    def setUp(self):
        self.rows = seed.build_rows(CO)
        self.names = [r[0] for r in self.rows]

    def test_editable_rows_ship_empty_so_behaviour_is_unchanged(self):
        by_name = {r[0]: r[1] for r in self.rows}
        for kind in ts.KINDS:
            self.assertEqual(by_name[ts.SUBJECT_ROW[kind]], "")
            self.assertEqual(by_name[ts.BODY_ROW[kind]], "")

    def test_every_placeholder_is_documented(self):
        for name in ts.PLACEHOLDERS:
            self.assertIn(f"{{{name}}}", self.names)

    def test_reference_rows_are_not_read_by_the_store(self):
        """They are prefixed （参考） so the store ignores them."""
        for kind in ts.KINDS:
            self.assertIn(f"（参考）{ts.BODY_ROW[kind]}", self.names)
            self.assertNotEqual(f"（参考）{ts.BODY_ROW[kind]}", ts.BODY_ROW[kind])


if __name__ == "__main__":
    unittest.main()
