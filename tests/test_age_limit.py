"""The age limit (backend/age_limit.py): ratings read as ages, what is above the limit left out of Jellyfin's answers
and not played, the limit kept by the simple settings' code."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend.age_limit import rating_age  # noqa: E402
from helpers import Backend  # noqa: E402
from mockjf import MockJellyfin  # noqa: E402


class RatingAgeTest(unittest.TestCase):
    def test_ratings_read_as_ages(self):
        for rating, age in (("FSK-12", 12), ("FSK 0", 0), ("DE-16", 16), ("de-6", 6), ("18", 18), ("16+", 16),
                            ("12A", 12), ("PG-13", 13), ("PG", 10), ("R", 17), ("G", 0), ("TV-MA", 17), ("TV-Y7", 7),
                            ("NR", None), ("", None), (None, None), ("Not Rated", None), ("M", None)):
            with self.subTest(rating):
                self.assertEqual(rating_age(rating), age)


class AgeLimitTest(unittest.TestCase):
    def setUp(self):
        self.mock = MockJellyfin()
        self.mock.lib.items["m1"]["OfficialRating"] = "FSK-16"
        self.mock.lib.items["m2"]["OfficialRating"] = "FSK-6"
        self.mock.lib.items["m3"]["OfficialRating"] = "PG-13"
        # m4 and the rest: no rating
        self.mock.lib.items["s1"]["OfficialRating"] = "FSK-16"  # its seasons and episodes have none of their own
        self.mock.start()
        self.addCleanup(self.mock.stop)
        self.backend = Backend(self.mock.url).start()
        self.addCleanup(self.backend.stop)
        self.user = self.backend.request("/session")[1]["userId"]

    def movies(self):
        code, r = self.backend.request(f"/jf/Items?userId={self.user}&parentId=lm")
        self.assertEqual(code, 200, r)
        return r

    def limit(self, value):
        return self.backend.request("/settings", {"maxage": value})

    def test_no_limit_shows_everything(self):
        self.assertEqual(self.backend.request("/settings")[1]["maxage"], "off")
        names = {x["Id"] for x in self.movies()["Items"]}
        self.assertTrue({"m1", "m2", "m3", "m4"} <= names)

    def test_titles_above_the_limit_left_out(self):
        before = self.movies()
        self.assertEqual(self.limit("12")[0], 200)
        after = self.movies()
        ids = {x["Id"] for x in after["Items"]}
        self.assertNotIn("m1", ids, "rated 16")
        self.assertNotIn("m3", ids, "PG-13 is 13")
        self.assertIn("m2", ids, "rated 6")
        self.assertIn("m4", ids, "no rating: kept, as Jellyfin's own limit does")
        if "TotalRecordCount" in before:
            self.assertEqual(after["TotalRecordCount"], before["TotalRecordCount"] - 2, "the count without them")

    def test_items_own_page_and_playing_refused(self):
        self.limit("12")
        self.assertEqual(self.backend.request(f"/jf/Items/m1?userId={self.user}")[0], 403)
        self.assertEqual(self.backend.request(f"/jf/Items/m2?userId={self.user}")[0], 200)
        self.assertEqual(self.backend.request("/play", {"itemId": "m1"}), (403, {"error": "age_limit"}))

    def test_episodes_take_their_series_rating(self):
        self.limit("12")
        self.assertEqual(self.backend.request(f"/jf/Items/e11?userId={self.user}")[0], 403, "the series is rated 16")
        self.assertEqual(self.backend.request("/play", {"itemId": "e11"})[0], 403)
        self.limit("16")
        self.assertEqual(self.backend.request(f"/jf/Items/e11?userId={self.user}")[0], 200)

    def test_continue_watching_gets_nothing_above_it_from_this_device(self):
        # m1 stopped early here: Jellyfin keeps no position for it, Matinee adds it to Continue Watching itself
        (self.backend.conf / "early.json").write_text(json.dumps({"early": [{"id": "m1", "pos": 30}]}))
        resume = lambda: {x["Id"] for x in self.backend.request(f"/jf/UserItems/Resume?userId={self.user}")[1]["Items"]}
        self.assertIn("m1", resume())
        self.limit("12")
        self.assertNotIn("m1", resume(), "rated 16")

    def test_for_all_ages_only(self):
        self.limit("0")
        ids = {x["Id"] for x in self.movies()["Items"]}
        self.assertNotIn("m2", ids, "rated 6")
        self.assertIn("m4", ids)

    def test_limit_kept_by_the_simple_settings_code(self):
        self.limit("6")
        self.assertEqual(self.backend.request("/simple", {"on": True, "pin": "1234"})[0], 200)
        self.assertEqual(self.limit("off"), (400, {"error": "locked"}), "not without the code")
        self.assertEqual(self.backend.request("/settings")[1]["maxage"], 6)
        self.assertEqual(self.backend.request("/simple", {"on": False, "pin": "1234"})[0], 200)
        self.assertEqual(self.limit("off")[0], 200)

    def test_only_its_values(self):
        self.assertEqual(self.limit("14")[0], 400)


if __name__ == "__main__":
    unittest.main()
