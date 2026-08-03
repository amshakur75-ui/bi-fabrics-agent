"""user_matches — tolerant user-handle matching (short display name vs full UPN).

Regression lock for a live-observed bug: the per-user lookup did an exact match on the full
email stored by Log Analytics (e.g. ``jordan.rivera@example.com``), but the agent passes the
short display name it prints in tables (``Jordan.Rivera``), so real users returned
``found: false`` — including a user who was in the ranking that same turn.
"""
import unittest

from fabric_audit_agent.key_utils import user_matches


class TestUserMatches(unittest.TestCase):
    def test_short_display_name_matches_full_upn(self):
        # the live-miss case: agent shows "Jordan.Rivera", data stores the full UPN
        self.assertTrue(user_matches("jordan.rivera@example.com", "Jordan.Rivera"))

    def test_full_upn_query_still_matches(self):
        self.assertTrue(user_matches("jordan.rivera@example.com", "jordan.rivera@example.com"))

    def test_case_insensitive(self):
        self.assertTrue(user_matches("Jordan.Rivera@EXAMPLE.com", "jordan.rivera"))

    def test_reverse_direction(self):
        # stored short, query full — either direction reduces to the local part
        self.assertTrue(user_matches("jordan.rivera", "jordan.rivera@example.com"))

    def test_different_people_do_not_match(self):
        # a genuine typo / different name must still miss
        self.assertFalse(user_matches("jordan.rivera@example.com", "jorden.rivera"))
        self.assertFalse(user_matches("jordan.rivera@example.com", "alex.kim"))

    def test_empty_never_matches(self):
        self.assertFalse(user_matches("", "jordan.rivera"))
        self.assertFalse(user_matches("jordan.rivera@example.com", ""))
        self.assertFalse(user_matches(None, None))


if __name__ == "__main__":
    unittest.main()
