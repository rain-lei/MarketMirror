import tempfile
import unittest
from pathlib import Path

from research.semantic.local_credential import KEY_NAME, load_api_key, save_api_key


class LocalCredentialTest(unittest.TestCase):
    def test_saves_replaces_and_deduplicates_only_the_marketmirror_key(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / ".env.local"
            path.write_text("# local values\nOTHER_SETTING=keep\n"
                            f"{KEY_NAME}=old\n{KEY_NAME}=duplicate\n", encoding="utf-8")
            save_api_key("fixture-only-key", path)
            contents = path.read_text(encoding="utf-8")
            self.assertEqual(contents.count(f"{KEY_NAME}="), 1)
            self.assertIn("OTHER_SETTING=keep", contents)
            self.assertEqual(load_api_key(path), "fixture-only-key")

    def test_loads_exported_or_quoted_env_values(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / ".env.local"
            path.write_text(f"export {KEY_NAME}=\"fixture-key\"\n", encoding="utf-8")
            self.assertEqual(load_api_key(path), "fixture-key")

    def test_rejects_empty_or_multiline_key(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / ".env.local"
            for value in ("", " \t", "first\nsecond"):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    save_api_key(value, path)


if __name__ == "__main__":
    unittest.main()
