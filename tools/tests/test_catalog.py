import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from oceanic import catalog, exporter, r2

ROOT = Path(__file__).resolve().parents[2]
GEN = "2026-10-19T12:00:00Z"
MODEL = "switch/switch-one-design"


class FakeClient:
    """Cliente S3 falso: solo head_object y put_object; cualquier otra llamada falla la prueba."""

    def __init__(self, objects=None):
        self.objects = dict(objects or {})
        self.puts = []

    def head_object(self, key):
        o = self.objects.get(key)
        return None if o is None else {"sha256": o["meta"].get("sha256")}

    def put_object(self, key, body, content_type, cache_control, metadata):
        self.puts.append(key)
        self.objects[key] = {"body": body, "type": content_type, "cache": cache_control, "meta": metadata}

    def __getattr__(self, name):  # delete_*, copy_*, etc.: no deben llamarse nunca
        raise AssertionError(f"llamada no permitida al cliente: {name}")


def make_export(tmp: Path, preview: bool) -> Path:
    exporter.export_portal(ROOT, tmp, preview_keys=True, generated_at=GEN, only=MODEL)
    if not preview:  # simula un export con claves verificadas: baja la marca y recalcula el hash del catalogo
        p = tmp / "index.json"
        idx = json.loads(p.read_text(encoding="utf-8"))
        idx["preview"] = False
        body = dict(idx, catalog_hash=None)
        idx["catalog_hash"] = exporter.canonical_hash(body)
        p.write_text(json.dumps(idx, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return tmp


class Refusals(unittest.TestCase):
    def test_preview_export_is_refused(self):
        with tempfile.TemporaryDirectory() as t:
            make_export(Path(t), preview=True)
            with self.assertRaises(catalog.PublishRefused) as cm:
                catalog.load_export(Path(t))
            self.assertIn("preview", str(cm.exception))
            client = FakeClient()
            with self.assertRaises(catalog.PublishRefused):
                catalog.publish_export(Path(t), apply=True, client=client)
            self.assertEqual(client.puts, [])

    def test_preview_is_refused_by_cli_even_in_dry_run(self):
        with tempfile.TemporaryDirectory() as t:
            make_export(Path(t), preview=True)
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = catalog.main_cli([f"--out={t}"], ROOT)
            self.assertEqual(rc, 2)
            self.assertIn("preview", buf.getvalue())

    def test_export_marks_preview_in_index_and_report(self):
        with tempfile.TemporaryDirectory() as t:
            make_export(Path(t), preview=True)
            self.assertIs(json.loads((Path(t) / "index.json").read_text(encoding="utf-8"))["preview"], True)
            self.assertIs(json.loads((Path(t) / "export-report.json").read_text(encoding="utf-8"))["preview"], True)

    def test_inconsistent_hash_or_missing_file_is_refused(self):
        with tempfile.TemporaryDirectory() as t:
            make_export(Path(t), preview=False)
            doc = Path(t) / "models" / MODEL / "portal.json"
            data = json.loads(doc.read_text(encoding="utf-8"))
            data["texts"]["es"]["diseno"]["text"] = "texto cambiado a mano"
            doc.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            with self.assertRaises(catalog.PublishRefused):
                catalog.load_export(Path(t))
        with tempfile.TemporaryDirectory() as t:
            make_export(Path(t), preview=False)
            (Path(t) / "schema" / "media-rules.json").unlink()
            with self.assertRaises(catalog.PublishRefused):
                catalog.load_export(Path(t))

    def test_local_paths_are_refused(self):
        with tempfile.TemporaryDirectory() as t:
            make_export(Path(t), preview=False)
            doc = Path(t) / "models" / MODEL / "portal.json"
            data = json.loads(doc.read_text(encoding="utf-8"))
            data["images"][0]["key"] = "C:\\biblioteca\\switch\\foto.webp"
            data["identity"]["content_hash"] = exporter.model_content_hash(data)
            doc.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            idx = json.loads((Path(t) / "index.json").read_text(encoding="utf-8"))
            for m in idx["models"]:
                m["content_hash"] = data["identity"]["content_hash"]
            idx["catalog_hash"] = exporter.canonical_hash(dict(idx, catalog_hash=None))
            (Path(t) / "index.json").write_text(json.dumps(idx, ensure_ascii=False), encoding="utf-8")
            with self.assertRaises(catalog.PublishRefused):
                catalog.load_export(Path(t))


class Publish(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = make_export(Path(self.tmp.name), preview=False)
        self.index = json.loads((self.out / "index.json").read_text(encoding="utf-8"))
        self.prefix = f"catalog/{self.index['schema_version']}/{self.index['catalog_hash']}"

    def tearDown(self):
        self.tmp.cleanup()

    def test_dry_run_does_not_touch_the_client(self):
        rep = catalog.publish_export(self.out)  # sin cliente: no necesita red ni credenciales
        self.assertEqual(rep["uploaded"], 0)
        self.assertGreaterEqual(rep["files"], 7)  # index + modelo + marca + 4 esquemas

    def test_apply_uploads_layout_headers_and_pointer(self):
        client = FakeClient()
        rep = catalog.publish_export(self.out, apply=True, client=client)
        self.assertEqual(rep["errors"], [])
        keys = set(client.objects)
        self.assertIn(f"{self.prefix}/index.json", keys)
        self.assertIn(f"{self.prefix}/models/{MODEL}/portal.json", keys)
        self.assertIn(f"{self.prefix}/brands/switch/brand.json", keys)
        for name in ("portal.schema.json", "media-rules.json", "field-catalog.json", "publish-rules.json"):
            self.assertIn(f"{self.prefix}/schema/{name}", keys)
        self.assertNotIn(f"{self.prefix}/export-report.json", keys)
        self.assertIn("catalog/latest.json", keys)
        for k, o in client.objects.items():
            if k == "catalog/latest.json":
                self.assertEqual(o["cache"], "no-cache")
            else:
                self.assertTrue(k.startswith(self.prefix + "/"))
                self.assertEqual(o["cache"], "public, max-age=31536000, immutable")
            self.assertTrue(o["type"].startswith("application/json"))
        ptr = json.loads(client.objects["catalog/latest.json"]["body"])
        self.assertEqual(ptr["content_hash"], self.index["catalog_hash"])
        self.assertEqual(ptr["path"], f"{self.prefix}/index.json")
        # el puntero se escribe al final y una sola vez
        self.assertEqual(client.puts[-1], "catalog/latest.json")
        self.assertEqual(client.puts.count("catalog/latest.json"), 1)
        self.assertEqual(client.puts.index(f"{self.prefix}/index.json"), len(client.puts) - 2)

    def test_second_run_uploads_nothing_new(self):
        client = FakeClient()
        catalog.publish_export(self.out, apply=True, client=client)
        n = len(client.puts)
        rep = catalog.publish_export(self.out, apply=True, client=client)
        self.assertEqual(len(client.puts), n)
        self.assertEqual((rep["uploaded"], rep["pointer"]), (0, "skipped"))

    def test_never_overwrites_immutable_key_with_other_content(self):
        client = FakeClient()
        catalog.publish_export(self.out, apply=True, client=client)
        key = f"{self.prefix}/models/{MODEL}/portal.json"
        client.objects[key]["meta"] = {"sha256": "0" * 64}
        n = len(client.puts)
        rep = catalog.publish_export(self.out, apply=True, client=client)
        self.assertTrue(any("no se pisa" in e for e in rep["errors"]))
        self.assertEqual(len(client.puts), n)
        self.assertEqual(client.objects[key]["meta"], {"sha256": "0" * 64})

    def test_pointer_not_written_when_something_fails(self):
        class Flaky(FakeClient):
            def put_object(self, key, *a, **k):
                if key.endswith("/brand.json"):
                    raise r2.R2Error("falla")
                super().put_object(key, *a, **k)

        client = Flaky()
        rep = catalog.publish_export(self.out, apply=True, client=client)
        self.assertTrue(rep["errors"])
        self.assertNotIn("catalog/latest.json", client.objects)

    def test_pointer_is_the_only_key_that_changes(self):
        old = FakeClient({"catalog/latest.json": {"body": b"{}", "type": "x", "cache": "x", "meta": {"sha256": "1" * 64}}})
        catalog.publish_export(self.out, apply=True, client=old)
        self.assertEqual(old.puts.count("catalog/latest.json"), 1)
        self.assertEqual(json.loads(old.objects["catalog/latest.json"]["body"])["content_hash"],
                         self.index["catalog_hash"])

    def test_verify_images_reports_missing_and_stops(self):
        client = FakeClient()
        rep = catalog.publish_export(self.out, apply=True, verify_images=True, client=client)
        self.assertTrue(rep["images_missing"])
        self.assertEqual(client.puts, [])  # nada se sube si faltan imagenes
        doc = json.loads((self.out / "models" / MODEL / "portal.json").read_text(encoding="utf-8"))
        keys = {k: {"body": b"", "type": "image/webp", "cache": "", "meta": {}} for k in (i["key"] for i in doc["images"])}
        rep2 = catalog.publish_export(self.out, apply=True, verify_images=True, client=FakeClient(keys))
        self.assertEqual(rep2["images_missing"], [])
        self.assertEqual(rep2["errors"], [])

    def test_missing_credentials_names_only(self):
        buf = io.StringIO()
        with mock.patch.dict(os.environ, {}, clear=True), redirect_stdout(buf):
            rc = catalog.main_cli([f"--out={self.out}", "--apply"], ROOT)
        self.assertEqual(rc, 2)
        text = buf.getvalue()
        for name in r2.ENV_VARS:
            self.assertIn(name, text)

    def test_no_delete_in_catalog_code(self):
        src = (ROOT / "tools" / "oceanic" / "catalog.py").read_text(encoding="utf-8").lower()
        self.assertNotIn("delete", src)


if __name__ == "__main__":
    unittest.main()
