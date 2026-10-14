"""Offline ACL regression checks for the dedicated OSD observer account."""
from pathlib import Path
import unittest

ACL = Path(__file__).resolve().parents[2] / "telemetry" / "emqx" / "acl.conf"
USER = 'aeronexus_osd'


class OsdAclTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rules = [line.strip() for line in ACL.read_text().splitlines()
                     if line.strip() and not line.lstrip().startswith("%%")]

    def test_dedicated_account_has_only_osd_subscription(self):
        account_rules = [rule for rule in self.rules if '"aeronexus_osd"' in rule]
        self.assertEqual(account_rules, [
            '{deny,  {username, "aeronexus_osd"}, publish,   ["#"]}.',
            '{allow, {username, "aeronexus_osd"}, subscribe, ["thing/product/+/osd"]}.',
            '{deny,  {username, "aeronexus_osd"}, subscribe, ["#"]}.',
        ])

    def test_dedicated_rules_precede_generic_allows(self):
        text = "\n".join(self.rules)
        self.assertLess(text.index('{deny,  {username, "aeronexus_osd"}, publish'),
                        text.index('{allow, all, publish'))
        self.assertLess(text.index('{deny,  {username, "aeronexus_osd"}, subscribe'),
                        text.index('{allow, all, subscribe'))

    def test_companion_rules_preserved(self):
        text = "\n".join(self.rules)
        for rule in (
            '{deny,  {username, "companion"}, publish,   ["#"]}.',
            '{allow, {username, "companion"}, subscribe, ["thing/product/+/osd"]}.',
            '{deny,  {username, "companion"}, subscribe, ["#"]}.',
        ):
            self.assertIn(rule, text)

    def test_final_deny_all_preserved(self):
        self.assertEqual(self.rules[-1], '{deny, all}.')


if __name__ == "__main__":
    unittest.main()
