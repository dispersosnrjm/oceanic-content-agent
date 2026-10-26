import contextlib
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

try:
    from tests import _netguard  # noqa: F401  (bloquea la red en las pruebas)
except ImportError:  # unittest discover -s tests
    import _netguard  # noqa: F401
from oceanic import media


def _png(color=(200, 30, 30), size=(64, 48)):
    b = io.BytesIO()
    Image.new("RGB", size, color).save(b, "PNG")
    return b.getvalue()


PNG_SHA = hashlib.sha256(_png()).hexdigest()


def _rec(i, **kw):
    """Registro con la copia ya inventariada y el sha256 del original que `_png()` reproduce (verificable)."""
    r = {"id": i, "title": i, "category": "EXTERIOR", "scope": "THIS_MODEL", "hero_candidate": False,
         "source_url": f"https://x.test/{i}.jpg", "cdn_url": f"https://cdn.test/{i}.jpg",
         "file": f"05_MULTIMEDIA/IMAGENES/EXTERIOR/{i}.webp", "width": 1, "height": 1, "format": "webp",
         "sha256": "old", "bytes": 1, "downloaded_at": "2026-10-04", "download_status": "WEB_COPY",
         "original": {"url": f"https://x.test/{i}.jpg", "fetched_from": f"https://x.test/{i}.jpg",
                      "sha256": PNG_SHA, "width": 4000, "height": 3000, "format": "jpeg",
                      "bytes": 999, "full_resolution": True}}
    r.update(kw)
    return r


class Restore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.dir = self.root / "biblioteca" / "marca" / "modelo"
        self.imgs = self.dir / "05_MULTIMEDIA" / "IMAGENES"
        (self.imgs / "EXTERIOR").mkdir(parents=True)
        self.calls = []

    def _write(self, records):
        (self.imgs / "images.json").write_text(
            json.dumps({"model": "m", "images": records}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _good(self, i):
        b = io.BytesIO()
        Image.new("RGB", (10, 10)).save(b, "WEBP")
        (self.imgs / "EXTERIOR" / f"{i}.webp").write_bytes(b.getvalue())
        return b.getvalue()

    def _fetch(self, url):
        self.calls.append(url)
        return _png()

    def _load(self):
        return json.loads((self.imgs / "images.json").read_text(encoding="utf-8"))["images"]

    def _raw(self):
        return (self.imgs / "images.json").read_bytes()

    # --- los cuatro casos de la verificacion -----------------------------------------------------------------

    def test_verified_restore_leaves_images_json_untouched(self):
        good = self._good("ok")
        self._write([_rec("ok"), _rec("gone")])
        before = self._raw()
        st = media.restore_images(self.dir, fetch=self._fetch)
        self.assertEqual((st["restored"], st["restored_verified"], st["restored_unverifiable"], st["changed"]),
                         (1, 1, 0, 0))
        self.assertEqual(st["ok_on_disk"], 1)
        self.assertEqual(self.calls, ["https://x.test/gone.jpg"])
        self.assertEqual(self._raw(), before)  # ni sha256 ni downloaded_at ni nada: git diff vacio
        self.assertTrue(media._webp_ok(self.imgs / "EXTERIOR" / "gone.webp"))  # la copia si se escribio
        self.assertEqual((self.imgs / "EXTERIOR" / "ok.webp").read_bytes(), good)

    def test_changed_original_is_not_written_and_marked_for_review(self):
        self._write([_rec("gone", original={"url": "https://x.test/gone.jpg", "fetched_from": "https://x.test/gone.jpg",
                                            "sha256": "f" * 64, "bytes": 999})])
        st = media.restore_images(self.dir, fetch=self._fetch)
        self.assertEqual((st["changed"], st["restored"], st["failed"]), (1, 0, 0))
        self.assertFalse((self.imgs / "EXTERIOR" / "gone.webp").exists())  # no se sobrescribe ni se crea la copia
        self.assertEqual(st["changed_list"][0]["expected_sha256"], "f" * 64)
        self.assertEqual(st["changed_list"][0]["downloaded_sha256"], PNG_SHA)
        r = self._load()[0]
        self.assertEqual(r["download_status"], "REQUIRES_REVIEW (original cambió)")
        self.assertEqual((r["sha256"], r["width"], r["bytes"], r["downloaded_at"]), ("old", 1, 1, "2026-10-04"))
        self.assertEqual(r["original"]["sha256"], "f" * 64)
        self.assertEqual(r["restore_check"]["downloaded_sha256"], PNG_SHA)
        # una nueva corrida no vuelve a bajarla ni a tocarla: decide una persona
        raw = self._raw()
        self.calls.clear()
        st2 = media.restore_images(self.dir, fetch=self._fetch)
        self.assertEqual((self.calls, st2["changed"], self._raw()), ([], 0, raw))

    def test_changed_original_with_existing_corrupt_copy_keeps_the_file(self):
        (self.imgs / "EXTERIOR" / "bad.webp").write_bytes(b"not a webp")
        self._write([_rec("bad", original={"url": "https://x.test/bad.jpg", "fetched_from": "https://x.test/bad.jpg",
                                           "sha256": "e" * 64})])
        media.restore_images(self.dir, fetch=self._fetch)
        self.assertEqual((self.imgs / "EXTERIOR" / "bad.webp").read_bytes(), b"not a webp")

    def test_unverifiable_is_restored_counted_and_record_unchanged(self):
        self._write([_rec("gone", original={"url": "https://x.test/gone.jpg", "fetched_from": None, "sha256": None,
                                            "bytes": None})])
        before = self._raw()
        st = media.restore_images(self.dir, fetch=self._fetch)
        self.assertEqual((st["restored"], st["restored_unverifiable"], st["restored_verified"], st["changed"]),
                         (1, 1, 0, 0))
        self.assertTrue(media._webp_ok(self.imgs / "EXTERIOR" / "gone.webp"))
        self.assertEqual(self._raw(), before)  # no bloquea ni cambia el inventario

    def test_cdn_copy_is_checked_against_source_copy_sha256(self):
        cdn = "https://cdn.test/gone.jpg?width=2560&quality=90"

        def rec(sha):
            return _rec("gone", downloaded_from=cdn, original={"url": "https://x.test/gone.jpg", "fetched_from": None,
                                                              "sha256": None, "source_copy_sha256": sha})
        self._write([rec(PNG_SHA)])
        before = self._raw()
        st = media.restore_images(self.dir, fetch=self._fetch)
        self.assertEqual(self.calls, [cdn])  # la CDN, no el original
        self.assertEqual((st["restored_verified"], st["changed"]), (1, 0))
        self.assertEqual(self._raw(), before)
        (self.imgs / "EXTERIOR" / "gone.webp").unlink()
        self._write([rec("a" * 64)])
        st = media.restore_images(self.dir, fetch=self._fetch)
        self.assertEqual((st["changed"], st["restored"]), (1, 0))
        self.assertFalse((self.imgs / "EXTERIOR" / "gone.webp").exists())

    def test_record_without_any_url_is_counted_as_no_url(self):
        self._write([_rec("x", source_url=None, cdn_url=None, original={"url": None, "fetched_from": None,
                                                                         "sha256": None})])
        st = media.restore_images(self.dir, fetch=self._fetch)
        self.assertEqual((st["no_url"], st["failed"], st["restored"]), (1, 0, 0))
        self.assertEqual(self.calls, [])
        self.assertTrue(self._load()[0]["download_status"].startswith("FAILED"))

    # --- resto del comportamiento --------------------------------------------------------------------------

    def test_original_block_never_changes(self):
        self._write([_rec("a"), _rec("b", original={"url": "https://x.test/b.jpg", "fetched_from": "https://x.test/b.jpg",
                                                    "sha256": "c" * 64})])
        before = {r["id"]: json.dumps(r["original"], sort_keys=True) for r in self._load()}
        media.restore_images(self.dir, fetch=self._fetch)
        for r in self._load():
            self.assertEqual(json.dumps(r["original"], sort_keys=True), before[r["id"]])

    def test_record_without_registered_copy_is_filled_in(self):
        self._write([_rec("n", file=None, sha256=None, width=None, height=None, bytes=None, downloaded_at=None,
                          download_status="PENDING")])
        st = media.restore_images(self.dir, fetch=self._fetch)
        self.assertEqual(st["restored"], 1)
        r = self._load()[0]
        self.assertEqual((r["width"], r["height"], r["web_quality"]), (64, 48, media.WEB_QUALITY))
        self.assertEqual(r["download_status"], "WEB_COPY")
        self.assertTrue((self.dir / r["file"]).exists())
        self.assertNotIn("manual_download_url", r)

    def test_corrupt_file_is_restored(self):
        (self.imgs / "EXTERIOR" / "bad.webp").write_bytes(b"not a webp")
        self._write([_rec("bad")])
        st = media.restore_images(self.dir, fetch=self._fetch)
        self.assertEqual(st["restored"], 1)
        self.assertTrue(media._webp_ok(self.imgs / "EXTERIOR" / "bad.webp"))

    def test_solaris_skipped(self):
        d = self.root / "biblioteca" / "solaris" / "s"
        (d / "05_MULTIMEDIA" / "IMAGENES").mkdir(parents=True)
        (d / "05_MULTIMEDIA" / "IMAGENES" / "images.json").write_text(
            json.dumps({"images": [_rec("a")]}), encoding="utf-8")
        st = media.restore_images(d, fetch=self._fetch)
        self.assertEqual((st["skipped_solaris"], st["restored"]), (1, 0))
        self.assertEqual(self.calls, [])

    def test_captcha_marks_manual(self):
        self._write([_rec("c")])
        st = media.restore_images(self.dir, fetch=lambda u: b"<html>sg-captcha challenge</html>")
        self.assertEqual(st["requires_manual_download"], 1)
        self.assertEqual(st["manual"], [{"id": "c", "url": "https://x.test/c.jpg"}])
        r = self._load()[0]
        self.assertEqual(r["download_status"], "requires_manual_download")
        self.assertEqual(r["manual_download_url"], "https://x.test/c.jpg")

    def test_failure_does_not_stop_rest(self):
        self._write([_rec("a"), _rec("b")])

        def fetch(url):
            if "/a." in url:
                raise OSError("boom")
            return _png()
        st = media.restore_images(self.dir, fetch=fetch)
        self.assertEqual((st["failed"], st["restored"]), (1, 1))
        recs = {r["id"]: r for r in self._load()}
        self.assertTrue(recs["a"]["download_status"].startswith("FAILED"))
        self.assertEqual(recs["b"]["download_status"], "WEB_COPY")

    def test_downloaded_from_is_tried_first_so_cdn_copies_do_not_fetch_originals(self):
        cdn = "https://cdn.test/gone.jpg?width=2560&quality=90"
        self._write([_rec("gone", downloaded_from=cdn, original={"url": "https://x.test/gone.jpg", "fetched_from": None,
                                                                 "sha256": None, "bytes": None})])
        media.restore_images(self.dir, fetch=self._fetch)
        self.assertEqual(self.calls, [cdn])  # no se descarga el original

    def test_hero_rank_or_hero_category_uses_hero_edge(self):
        big = io.BytesIO()
        Image.new("RGB", (3000, 2000)).save(big, "PNG")
        data = big.getvalue()
        sha = hashlib.sha256(data).hexdigest()
        orig = {"url": "https://x.test/o.jpg", "fetched_from": None, "sha256": None}
        (self.imgs / "HERO").mkdir()
        self._write([_rec("rank", hero_rank=2, original=dict(orig)),
                     _rec("cat", category="HERO", file="05_MULTIMEDIA/IMAGENES/HERO/cat.webp", original=dict(orig)),
                     _rec("normal", original=dict(orig))])
        media.restore_images(self.dir, fetch=lambda url: data)
        sizes = {}
        for rel in ("EXTERIOR/rank", "HERO/cat", "EXTERIOR/normal"):
            with Image.open(self.imgs / f"{rel}.webp") as im:
                sizes[rel] = max(im.size)
        self.assertEqual(sizes, {"EXTERIOR/rank": 2560, "HERO/cat": 2560, "EXTERIOR/normal": 1600})
        self.assertNotEqual(sha, PNG_SHA)

    def test_duplicates_and_other_scopes_ignored(self):
        self._write([_rec("d", download_status="DUPLICATE_OF x", file=None),
                     _rec("o", scope="OTHER_MODEL")])
        raw = self._raw()
        st = media.restore_images(self.dir, fetch=self._fetch)
        self.assertEqual((st["restored"], st["failed"], st["ok_on_disk"]), (0, 0, 0))
        self.assertEqual(self.calls, [])
        self.assertEqual(self._raw(), raw)

    # --- la ruta de la copia no puede salir de la carpeta del modelo --------------------------------------------

    def test_file_path_outside_model_folder_is_refused_before_any_write(self):
        outside = self.root / "afuera"
        outside.mkdir()
        for bad in ("../../../afuera/evil.webp", "..\\..\\..\\afuera\\evil.webp", str(outside / "evil.webp"),
                    "05_MULTIMEDIA/../../../afuera/evil.webp", "."):
            self._write([_rec("ok"), _rec("x", file=bad)])
            before = self._raw()
            with self.assertRaises(ValueError) as cm:
                media.restore_images(self.dir, fetch=self._fetch)
            self.assertIn("sale de la carpeta del modelo", str(cm.exception))
            self.assertIn("'x'", str(cm.exception))  # nombra la imagen
            self.assertEqual(self.calls, [], bad)  # no descargo nada
            self.assertEqual(list(outside.iterdir()), [], bad)  # no escribio fuera
            self.assertEqual(self._raw(), before, bad)  # images.json intacto

    def test_nested_paths_inside_the_model_folder_still_work(self):
        self._write([_rec("n", file="05_MULTIMEDIA/IMAGENES/EXTERIOR/sub/n.webp")])
        st = media.restore_images(self.dir, fetch=self._fetch)
        self.assertEqual(st["restored"], 1)
        self.assertTrue(media._webp_ok(self.imgs / "EXTERIOR" / "sub" / "n.webp"))

    def test_restore_main_reports_the_bad_path_and_fails(self):
        self._write([_rec("x", file="../../evil.webp")])
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = media.restore_main(["marca", "--restore"], self.root, fetch=self._fetch)
        self.assertEqual(rc, 1)
        self.assertIn("ERROR", buf.getvalue())
        self.assertIn("sale de la carpeta del modelo", buf.getvalue())
        self.assertEqual(self.calls, [])

    def test_all_real_inventories_pass_the_path_check(self):
        lib = Path(__file__).resolve().parents[2] / "biblioteca"
        n = 0
        for f in lib.glob("*/*/05_MULTIMEDIA/IMAGENES/images.json"):
            for rec in json.loads(f.read_text(encoding="utf-8"))["images"]:
                if rec.get("file"):
                    media._check_inside(f.parents[2], rec["file"], rec.get("id"))
                    n += 1
        self.assertGreater(n, 3000)

    # --- informe -------------------------------------------------------------------------------------------

    def test_report_counts_by_case_and_brand(self):
        self._write([
            _rec("v"),
            _rec("c", original={"url": "https://x.test/c.jpg", "fetched_from": "https://x.test/c.jpg", "sha256": "d" * 64}),
            _rec("u", original={"url": "https://x.test/u.jpg", "fetched_from": None, "sha256": None}),
            _rec("n", source_url=None, cdn_url=None, original={"url": None, "fetched_from": None, "sha256": None}),
        ])
        out = self.root / "informe.json"
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = media.restore_main(["marca", "--restore", f"--report={out}"], self.root, fetch=self._fetch)
        self.assertEqual(rc, 0)
        rep = json.loads(out.read_text(encoding="utf-8"))
        t = rep["total"]
        self.assertEqual((t["restored_verified"], t["changed"], t["restored_unverifiable"], t["no_url"]), (1, 1, 1, 1))
        self.assertEqual(rep["by_brand"]["marca"]["restored_unverifiable"], 1)
        self.assertEqual(rep["changed"][0]["id"], "c")
        text = buf.getvalue()
        self.assertIn("restauradas y verificadas 1", text)
        self.assertIn("cambiadas (REQUIRES_REVIEW) 1", text)
        self.assertIn("no verificables 1", text)
        self.assertIn("sin URL 1", text)
        self.assertIn("no verificables en marca: 1", text)
        self.assertIn("CAMBIÓ el original", text)


if __name__ == "__main__":
    unittest.main()
