import copy
import unittest

try:
    from tests import _netguard  # noqa: F401  (bloquea la red en las pruebas)
except ImportError:  # unittest discover -s tests
    import _netguard  # noqa: F401
from oceanic import lint

RULES = {"version": 1, "rules": [
    {"id": "em-dash", "type": "literal", "pattern": "—", "severity": "error", "message": "m"},
    {"id": "word", "type": "regex", "pattern": "gran\\w*", "flags": "i", "severity": "warn", "message": "m",
     "scope": ["headlines"]},
]}


class Lint(unittest.TestCase):
    def test_em_dash_line_excerpt(self):
        v = lint.lint_text("uno\ndos — tres", "texts", "hero", RULES)
        self.assertEqual(len(v), 1)
        self.assertEqual(set(v[0]), {"scope", "section", "rule", "severity", "line", "excerpt"})
        self.assertEqual((v[0]["line"], v[0]["rule"], v[0]["section"]), (2, "em-dash", "hero"))
        self.assertIn("—", v[0]["excerpt"])
        self.assertFalse(lint.lint_ok(v))

    def test_excerpt_limit(self):
        v = lint.lint_text("a" * 300 + "—" + "b" * 300, "texts", "s", RULES)
        self.assertLessEqual(len(v[0]["excerpt"]), 120)
        self.assertIn("—", v[0]["excerpt"])

    def test_clean(self):
        v = lint.lint_text("Texto limpio.", "texts", "hero", RULES)
        self.assertEqual(v, [])
        self.assertTrue(lint.lint_ok(v))

    def test_warn_keeps_ok(self):
        v = lint.lint_text("Gran barco", "headlines", "hero", RULES)
        self.assertEqual(v[0]["severity"], "warn")
        self.assertTrue(lint.lint_ok(v))

    def test_scope_filter(self):
        self.assertEqual(lint.lint_text("Gran barco", "texts", "hero", RULES), [])
        with self.assertRaises(ValueError):
            lint.lint_text("x", "otro", "hero", RULES)

    def test_invalid_rules(self):
        def bad(**kw):
            r = copy.deepcopy(RULES)
            r["rules"][1].update(kw)
            with self.assertRaises(ValueError):
                lint.lint_text("x", "texts", "s", r)
        bad(id="em-dash")
        bad(type="otro")
        bad(pattern="(")
        bad(severity="fatal")
        bad(scope=["nada"])
        bad(flags="z")

    def test_real_file(self):
        rules = lint.load_rules()
        self.assertEqual([r["id"] for r in rules["rules"]], ["em-dash", "internal-note-residue"])
        v = lint.lint_text("a — b", "brand", "historia", rules)
        self.assertEqual(v[0]["rule"], "em-dash")

    def test_clean_internal_notes_real_cases(self):
        t1 = "Axopar declara hasta 30 % menos consumo entre 20 y 40 nudos. *(Velocidad crucero en la tabla: '-'.)*"
        c, n, found = lint.clean_internal_notes(t1)
        self.assertEqual((c, n), ("Axopar declara hasta 30 % menos consumo entre 20 y 40 nudos.", 1))
        self.assertEqual(found[0][1], "Axopar declara hasta 30 % menos consumo entre 20 y 40 nudos.")
        t2 = ("Algo previo. Axopar declara unas 75 millas náuticas más que el modelo anterior. "
              "*(No publicar una cifra calculada.)*")
        c, n, found = lint.clean_internal_notes(t2)
        self.assertEqual(c, "Algo previo. Axopar declara unas 75 millas náuticas más que el modelo anterior.")
        self.assertEqual(found[0], ("*(No publicar una cifra calculada.)*",
                                    "Axopar declara unas 75 millas náuticas más que el modelo anterior."))
        t3 = "- Fixed aluminum fuel tank 730l with overflow prevention *(no publicar: prevalece la ficha técnica, 722 l)*"
        c, n, found = lint.clean_internal_notes(t3)
        self.assertEqual(c, "- Fixed aluminum fuel tank 730l with overflow prevention")
        self.assertEqual(found[0][1], "- Fixed aluminum fuel tank 730l with overflow prevention")

    def test_residue_rule(self):
        rules = lint.load_rules()
        v = lint.lint_text("Frase. *(No publicar una cifra calculada.)*", "texts", "hero", rules)
        self.assertEqual([x["rule"] for x in v], ["internal-note-residue"])
        c, _, _ = lint.clean_internal_notes("Frase. *(nota)*")
        self.assertEqual(lint.lint_text(c, "texts", "hero", rules), [])


if __name__ == "__main__":
    unittest.main()
