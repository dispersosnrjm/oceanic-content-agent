import io
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from oceanic import mediarules, usados_media

REAL = Path(__file__).resolve().parents[2]


def _webp(color=(10, 120, 200), size=(16, 16)) -> bytes:
    from PIL import Image
    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, "WEBP", quality=75)
    return out.getvalue()


def _ficha(root: Path, folder: str, urls: list[str]) -> Path:
    d = root / "usados" / folder
    d.mkdir(parents=True)
    f = d / "ficha.txt"
    listing = "\n".join(urls)
    f.write_text(f"AVISO\n\n--- TEXTO DEL AVISO (literal) ---\ntexto\n\n--- FOTOS ({len(urls)}) ---\n{listing}\n", encoding="utf-8")
    return f


class FakeClient:
    """Solo head y put, como R2Client para este flujo; guarda lo subido."""

    def __init__(self):
        self.objects = {}
        self.puts = []

    def head_object(self, key):
        o = self.objects.get(key)
        return None if o is None else {"sha256": o["metadata"]["sha256"], "size": len(o["body"])}

    def put_object(self, key, body, content_type, cache_control, metadata):
        self.puts.append(key)
        self.objects[key] = {"body": body, "content_type": content_type, "cache_control": cache_control, "metadata": metadata}


class PhotoUrls(unittest.TestCase):
    def test_reads_only_the_photo_section(self):
        with tempfile.TemporaryDirectory() as t:
            f = _ficha(Path(t), "skeeta", ["https://oceanic.cl/a.jpeg", "https://oceanic.cl/b.jpeg"])
            f.write_text(f.read_text(encoding="utf-8") + "\n--- DOCUMENTOS ENLAZADOS ---\nhttps://oceanic.cl/x.pdf\n", encoding="utf-8")
            self.assertEqual(usados_media.photo_urls(f), ["https://oceanic.cl/a.jpeg", "https://oceanic.cl/b.jpeg"])

    def test_empty_section(self):
        with tempfile.TemporaryDirectory() as t:
            self.assertEqual(usados_media.photo_urls(_ficha(Path(t), "ola", [])), [])


class Slugs(unittest.TestCase):
    def test_slugs_are_valid_and_unique(self):
        vals = list(usados_media.SLUGS.values())
        self.assertEqual(len(vals), len(set(vals)))
        self.assertTrue(all(mediarules.valid_slug(s) for s in vals))

    def test_every_real_ficha_folder_is_mapped(self):
        folders = {p.parent.name for p in (REAL / "usados").glob("*/ficha.txt")}
        self.assertEqual(folders - set(usados_media.SLUGS), set())

    def test_key_has_the_contract_shape(self):
        key = mediarules.usados_key("skeeta", usados_media.CATEGORY, "foto-01", "ab" * 32)
        self.assertEqual(key, "usados/skeeta/other/foto-01-abababab.webp")


class Download(unittest.TestCase):
    def _run(self, root, urls, fetch, **kw):
        _ficha(root, "skeeta", urls)
        sleeps = []
        rep = usados_media.download(root, fetch=fetch, convert=lambda b: b, sleep=sleeps.append, **kw)
        return rep, sleeps

    def test_downloads_in_order_with_pause_between_requests(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            seen = []
            rep, sleeps = self._run(root, ["https://oceanic.cl/1.jpeg", "https://oceanic.cl/2.jpeg", "https://oceanic.cl/3.jpeg"],
                                    lambda u: seen.append(u) or _webp())
            self.assertEqual(rep["downloaded"], 3)
            self.assertEqual(seen, ["https://oceanic.cl/1.jpeg", "https://oceanic.cl/2.jpeg", "https://oceanic.cl/3.jpeg"])
            self.assertEqual(sleeps, [usados_media.PAUSE] * 2)   # sin pausa antes de la primera
            self.assertEqual(sorted(p.name for p in (root / "usados" / "skeeta" / "fotos").glob("*")), ["01.webp", "02.webp", "03.webp"])

    def test_existing_files_are_not_downloaded_again(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            _ficha(root, "skeeta", ["https://oceanic.cl/1.jpeg"])
            fd = root / "usados" / "skeeta" / "fotos"
            fd.mkdir()
            (fd / "01.webp").write_bytes(_webp())
            calls = []
            rep = usados_media.download(root, fetch=lambda u: calls.append(u), convert=lambda b: b, sleep=lambda s: None)
            self.assertEqual((rep["existing"], rep["downloaded"], calls), (1, 0, []))

    def test_failures_are_listed_and_do_not_stop_below_the_limit(self):
        with tempfile.TemporaryDirectory() as t:
            def fetch(u):
                if u.endswith("2.jpeg"):
                    raise OSError("HTTP 404")
                return _webp()
            rep, _ = self._run(Path(t), [f"https://oceanic.cl/{i}.jpeg" for i in (1, 2, 3)], fetch)
            self.assertEqual((rep["downloaded"], len(rep["failed"]), rep["stopped"]), (2, 1, False))
            self.assertEqual(rep["failed"][0]["n"], 2)

    def test_stops_when_more_than_ten_fail(self):
        with tempfile.TemporaryDirectory() as t:
            calls = []
            def fetch(u):
                calls.append(u)
                raise OSError("boom")
            rep, _ = self._run(Path(t), [f"https://oceanic.cl/{i}.jpeg" for i in range(30)], fetch)
            self.assertTrue(rep["stopped"])
            self.assertEqual(len(rep["failed"]), usados_media.MAX_FAILURES + 1)
            self.assertEqual(len(calls), usados_media.MAX_FAILURES + 1)    # no sigue pidiendo

    def test_folder_without_ficha_is_ignored(self):
        with tempfile.TemporaryDirectory() as t:
            rep = usados_media.download(Path(t), fetch=lambda u: _webp(), convert=lambda b: b, sleep=lambda s: None)
            self.assertEqual((rep["downloaded"], rep["folders"]), (0, {}))


class Publish(unittest.TestCase):
    def _photos(self, root, folder="skeeta", n=2):
        fd = root / "usados" / folder / "fotos"
        fd.mkdir(parents=True)
        for i in range(1, n + 1):
            (fd / f"{i:02d}.webp").write_bytes(_webp(color=(i * 20, 50, 90)))

    def test_dry_run_writes_nothing_and_needs_no_client(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            self._photos(root)
            rep = usados_media.publish(root)
            self.assertEqual((rep["to_upload"], rep["uploaded"], rep["errors"]), (2, 0, []))
            self.assertFalse((root / "usados" / "skeeta.r2.json").exists())

    def test_apply_uploads_with_contract_keys_headers_and_registry(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            self._photos(root)
            c = FakeClient()
            rep = usados_media.publish(root, apply=True, client=c)
            self.assertEqual((rep["uploaded"], rep["errors"]), (2, []))
            self.assertEqual(len(c.puts), 2)
            for k in c.puts:
                self.assertRegex(k, r"^usados/skeeta/other/foto-0[12]-[0-9a-f]{8}\.webp$")
                o = c.objects[k]
                self.assertEqual(o["content_type"], "image/webp")
                self.assertIn("immutable", o["cache_control"])
                self.assertEqual(o["metadata"]["sha256"][:8], re.search(r"-([0-9a-f]{8})\.webp$", k).group(1))
            reg = json.loads((root / "usados" / "skeeta.r2.json").read_text(encoding="utf-8"))
            self.assertEqual([e["key"] for e in reg["images"]], c.puts)
            self.assertTrue(all(e["sha256"] and e["bytes"] for e in reg["images"]))

    def test_second_run_uploads_nothing(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            self._photos(root)
            c = FakeClient()
            usados_media.publish(root, apply=True, client=c)
            again = usados_media.publish(root, apply=True, client=c)
            self.assertEqual((again["uploaded"], again["already"]), (0, 2))
            self.assertEqual(len(c.puts), 2)

    def test_existing_key_with_other_hash_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            self._photos(root, n=1)
            c = FakeClient()
            key = mediarules.usados_key("skeeta", "OTHER", "foto-01", __import__("hashlib").sha256(
                (root / "usados" / "skeeta" / "fotos" / "01.webp").read_bytes()).hexdigest())
            c.objects[key] = {"body": b"x", "metadata": {"sha256": "0" * 64}}
            rep = usados_media.publish(root, apply=True, client=c)
            self.assertEqual((rep["uploaded"], len(rep["errors"]), c.puts), (0, 1, []))

    def test_folder_without_photos_is_skipped_and_only_filters(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            self._photos(root, "skeeta", 1)
            self._photos(root, "oyster56", 1)
            (root / "usados" / "ola").mkdir()
            rep = usados_media.publish(root, only="oyster-56")
            self.assertEqual(list(rep["slugs"]), ["oyster-56"])

    def test_corrupt_file_is_reported_not_uploaded(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            self._photos(root, n=1)
            (root / "usados" / "skeeta" / "fotos" / "02.webp").write_bytes(b"no es una imagen")
            c = FakeClient()
            rep = usados_media.publish(root, apply=True, client=c)
            self.assertEqual((rep["uploaded"], len(rep["errors"])), (1, 1))

    def test_module_has_no_delete_capability(self):
        src = (Path(usados_media.__file__)).read_text(encoding="utf-8").lower()
        self.assertNotIn("delete", src)


if __name__ == "__main__":
    unittest.main()
