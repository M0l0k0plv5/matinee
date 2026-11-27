"""Matinee's UI in French, Spanish and Dutch (web/i18n-more.js), beside English and German."""
import unittest

from test_ui import UiBase


class UiLanguagesTest(UiBase):
    language = "fr"

    def test_every_string_in_each_language(self):
        for lang in ("fr", "es", "nl"):
            self.assertEqual(self.b.js(f"Object.keys(STRINGS.en).filter((k) => !(k in STRINGS.{lang}))"), [], f"{lang}: none missing")
            self.assertEqual(self.b.js(f"Object.keys(STRINGS.{lang}).filter((k) => !(k in STRINGS.en))"), [], f"{lang}: none left over")
            # the same placeholders as English ({name}, {n}, …), or t() leaves some empty
            self.assertEqual(self.b.js(f"""Object.keys(STRINGS.en).filter((k) => {{
                const marks = (s) => (s.match(/\\{{\\w+\\}}/g) || []).sort().join();
                return marks(STRINGS.en[k]) !== marks(STRINGS.{lang}[k] ?? STRINGS.en[k]); }})"""), [], f"{lang}: placeholders")

    def test_french_labels(self):
        self.assertEqual(self.b.js("LANG"), "fr")
        self.assertEqual(self.texts(".row h2")[0], "Reprendre")
        self.assertEqual(self.b.js("$('[data-k=tab-search]').getAttribute('aria-label')"), "Rechercher")
        self.go_to_row("resume")
        self.b.key("Enter")
        self.b.wait_js("current.name === 'item'")
        self.assertEqual(self.texts(".actions .btn")[:2], ["Reprendre à 0:05", "Recommencer"])

    def test_the_languages_to_choose(self):
        self.assertEqual(self.b.js("LANG_CHOICES.map(([v]) => v)"), ["en", "de", "fr", "es", "nl"])


if __name__ == "__main__":
    unittest.main()
