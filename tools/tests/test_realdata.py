import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

try:
    from tests import _netguard  # noqa: F401  (bloquea la red en las pruebas)
    from tests import _realdata
except ImportError:  # unittest discover -s tests
    import _netguard  # noqa: F401
    import _realdata


class Snapshot(unittest.TestCase):
    def test_snapshot_is_a_copy_without_webp_and_not_the_working_tree(self):
        root = _realdata.snapshot_root()
        self.assertNotEqual(root.resolve(), _realdata.REAL.resolve())
        self.assertEqual(list(root.rglob("*.webp")), [])
        for name in _realdata.SUBDIRS:
            self.assertTrue((root / name).is_dir(), name)
        real = {p.relative_to(_realdata.REAL) for p in (_realdata.REAL / "biblioteca").glob("*/*/00_MODELO/00_MODELO.md")}
        copy = {p.relative_to(root) for p in (root / "biblioteca").glob("*/*/00_MODELO/00_MODELO.md")}
        self.assertEqual(real, copy)
        self.assertEqual(_realdata.unreadable_json(root), [])

    def test_one_copy_per_process(self):
        self.assertEqual(_realdata.snapshot_root(), _realdata.snapshot_root())

    def test_unreadable_json_detects_a_half_written_file(self):
        with tempfile.TemporaryDirectory() as t:
            (Path(t) / "bueno.json").write_text(json.dumps({"a": 1}), encoding="utf-8")
            (Path(t) / "roto.json").write_text('{"images": [{"id": "a"', encoding="utf-8")   # cortado a medias
            (Path(t) / "vacio.json").write_text("", encoding="utf-8")
            self.assertEqual(sorted(_realdata.unreadable_json(Path(t))), ["roto.json", "vacio.json"])

    def test_a_copy_taken_during_a_concurrent_write_is_discarded_and_redone(self):
        calls = []

        def fake_copy(dst):
            calls.append(dst)
            (dst / "x.json").write_text('{"roto":' if len(calls) == 1 else "{}", encoding="utf-8")

        with mock.patch.object(_realdata, "_root", None), mock.patch.object(_realdata, "_copy", fake_copy), \
                mock.patch.object(_realdata, "atexit"), mock.patch.object(_realdata.time, "sleep", lambda s: None):
            root = _realdata.snapshot_root()
        self.assertEqual(len(calls), 2)
        self.assertFalse(calls[0].exists())          # la primera copia, inconsistente, se descarto
        self.assertTrue(root.exists())
        _realdata.shutil.rmtree(root, ignore_errors=True)

    def test_gives_up_with_a_clear_error_if_the_copy_never_becomes_consistent(self):
        def always_bad(dst):
            (dst / "x.json").write_text('{"roto":', encoding="utf-8")

        with mock.patch.object(_realdata, "_root", None), mock.patch.object(_realdata, "_copy", always_bad), \
                mock.patch.object(_realdata.time, "sleep", lambda s: None):
            with self.assertRaises(RuntimeError) as cm:
                _realdata.snapshot_root(attempts=2)
        self.assertIn("copia consistente", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
