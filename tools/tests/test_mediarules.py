import unittest

try:
    from tests import _netguard  # noqa: F401  (bloquea la red en las pruebas)
except ImportError:  # unittest discover -s tests
    import _netguard  # noqa: F401
from oceanic import mediarules as mr


class MediaRulesTests(unittest.TestCase):
    def test_vectors(self):
        self.assertEqual(mr.run_test_vectors(), [])

    def test_validators(self):
        self.assertTrue(mr.valid_slug("axopar-37"))
        self.assertFalse(mr.valid_slug("a--b"))
        self.assertTrue(mr.valid_filename("foto-1-5f1c2a9e.webp"))
        self.assertFalse(mr.valid_filename("Foto.webp"))
        self.assertTrue(mr.valid_image_id("a" * 120))
        self.assertFalse(mr.valid_image_id("a" * 121))
        self.assertFalse(mr.valid_image_id("A"))

    def test_category_folder_and_errors(self):
        self.assertEqual(mr.category_folder("PLANS"), "plans")
        with self.assertRaises(ValueError):
            mr.category_folder("XX")
        with self.assertRaises(ValueError):
            mr.image_key("b", "m", "HERO", "id", "zz")


if __name__ == "__main__":
    unittest.main()
