import io
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from oceanic import media


def _png(color=(200, 30, 30), size=(64, 48)):
    b = io.BytesIO()
    Image.new("RGB", size, color).save(b, "PNG")
    return b.getvalue()


def _rec(i, **kw):
    r = {"id": i, "title": i, "category": "EXTERIOR", "scope": "THIS_MODEL", "hero_candidate": False,
         "source_url": f"https://x.test/{i}.jpg", "cdn_url": f"https://cdn.test/{i}.jpg",
         "file": f"05_MULTIMEDIA/IMAGENES/EXTERIOR/{i}.webp", "width": 1, "height": 1, "format": "webp",
         "sha256": "old", "bytes": 1, "download_status": "WEB_COPY",
         "original": {"url": f"https://x.test/{i}.jpg", "fetched_from": f"https://x.test/{i}.jpg",
                      "sha256": "orig-" + i, "width": 4000, "height": 3000, "format": "jpeg",
                      "bytes": 999, "full_resolution": True}}
    r.update(kw)
    return r


class Restore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name) / "biblioteca" / "marca" / "modelo"
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

    def test_restores_missing_keeps_ok_and_original(self):
        good = self._good("ok")
        self._write([_rec("ok"), _rec("gone")])
        before = {r["id"]: json.dumps(r["original"], sort_keys=True) for r in self._load()}
        st = media.restore_images(self.dir, fetch=self._fetch)
        self.assertEqual((st["restored"], st["ok_on_disk"]), (1, 1))
        self.assertEqual(self.calls, ["https://x.test/gone.jpg"])
        self.assertEqual((self.imgs / "EXTERIOR" / "ok.webp").read_bytes(), good)
        recs = {r["id"]: r for r in self._load()}
        for i in recs:
            self.assertEqual(json.dumps(recs[i]["original"], sort_keys=True), before[i])
        r = recs["gone"]
        self.assertTrue((self.dir / r["file"]).exists())
        self.assertEqual((r["width"], r["height"], r["web_quality"]), (64, 48, media.WEB_QUALITY))
        self.assertEqual(r["download_status"], "WEB_COPY")
        self.assertEqual(recs["ok"]["sha256"], "old")

    def test_corrupt_file_is_restored(self):
        (self.imgs / "EXTERIOR" / "bad.webp").write_bytes(b"not a webp")
        self._write([_rec("bad")])
        st = media.restore_images(self.dir, fetch=self._fetch)
        self.assertEqual(st["restored"], 1)
        self.assertTrue(media._webp_ok(self.imgs / "EXTERIOR" / "bad.webp"))

    def test_solaris_skipped(self):
        d = Path(self.tmp.name) / "biblioteca" / "solaris" / "s"
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
        self._write([_rec("rank", hero_rank=2), _rec("cat", category="HERO", file="05_MULTIMEDIA/IMAGENES/HERO/cat.webp"),
                     _rec("normal")])
        media.restore_images(self.dir, fetch=lambda url: big.getvalue())
        recs = {r["id"]: r for r in self._load()}
        self.assertEqual(max(recs["rank"]["width"], recs["rank"]["height"]), 2560)
        self.assertEqual(max(recs["cat"]["width"], recs["cat"]["height"]), 2560)
        self.assertEqual(max(recs["normal"]["width"], recs["normal"]["height"]), 1600)

    def test_duplicates_and_other_scopes_ignored(self):
        self._write([_rec("d", download_status="DUPLICATE_OF x", file=None),
                     _rec("o", scope="OTHER_MODEL")])
        raw = (self.imgs / "images.json").read_bytes()
        st = media.restore_images(self.dir, fetch=self._fetch)
        self.assertEqual((st["restored"], st["failed"], st["ok_on_disk"]), (0, 0, 0))
        self.assertEqual(self.calls, [])
        self.assertEqual((self.imgs / "images.json").read_bytes(), raw)


if __name__ == "__main__":
    unittest.main()
