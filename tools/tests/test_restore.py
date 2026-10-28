import contextlib
import hashlib
import io
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

try:
    from tests import _netguard  # noqa: F401  (bloquea la red en las pruebas)
except ImportError:  # unittest discover -s tests
    import _netguard  # noqa: F401
from oceanic import exporter, media, r2


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
        self.assertEqual(self._load()[0]["download_status"], "WEB_COPY")  # copia registrada: el estado no se pisa
        self.assertEqual(st["failed_list"][0]["error"], "sin URL oficial")

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
        self._write([_rec("c", file=None, sha256=None, bytes=None, download_status="PENDING")])  # sin copia registrada
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
        self.assertEqual(recs["a"]["download_status"], "WEB_COPY")  # tiene copia registrada: el fallo no la toca
        self.assertEqual([f["id"] for f in st["failed_list"]], ["a"])
        self.assertEqual(recs["b"]["download_status"], "WEB_COPY")

    # --- un fallo de descarga no puede dañar un registro bueno ---------------------------------------------------

    def _dns_down(self, url):
        import urllib.error
        self.calls.append(url)
        raise urllib.error.URLError("[Errno 11001] getaddrinfo failed")

    def test_failed_download_leaves_registered_record_byte_identical_then_recovers(self):
        self._write([_rec("a"), _rec("b")])  # WEB_COPY con copia registrada, pero el archivo no esta en disco
        before = self._raw()
        st = media.restore_images(self.dir, fetch=self._dns_down)  # corte de red transitorio
        self.assertEqual((st["failed"], st["restored"], st["no_url"]), (2, 0, 0))
        self.assertEqual(self._raw(), before)  # images.json byte a byte igual
        self.assertEqual({f["id"]: f["registered"] for f in st["failed_list"]}, {"a": True, "b": True})
        self.assertIn("getaddrinfo", st["failed_list"][0]["error"])
        self.assertFalse((self.imgs / "EXTERIOR" / "a.webp").exists())
        # la corrida siguiente, con red, repone y verifica: images.json sigue igual y el estado es coherente
        st2 = media.restore_images(self.dir, fetch=self._fetch)
        self.assertEqual((st2["restored"], st2["restored_verified"], st2["failed"]), (2, 2, 0))
        self.assertEqual(self._raw(), before)
        self.assertTrue(all(r["download_status"] == "WEB_COPY" for r in self._load()))
        self.assertTrue(media._webp_ok(self.imgs / "EXTERIOR" / "a.webp"))

    def test_status_left_in_failed_by_an_old_run_is_healed_on_success(self):
        self._write([_rec("a", download_status="FAILED (URLError: <urlopen error [Errno 11001] getaddrinfo failed>)"),
                     _rec("m", download_status="requires_manual_download", manual_download_url="https://x.test/m.jpg")])
        orig = {r["id"]: r for r in self._load()}
        st = media.restore_images(self.dir, fetch=self._fetch)
        self.assertEqual((st["restored_verified"], st["status_healed"]), (2, 2))
        recs = {r["id"]: r for r in self._load()}
        for i in ("a", "m"):
            self.assertEqual(recs[i]["download_status"], "WEB_COPY")
            for k in ("sha256", "bytes", "width", "height", "downloaded_at", "original"):  # lo demas no cambia
                self.assertEqual(recs[i][k], orig[i][k], (i, k))
        self.assertNotIn("manual_download_url", recs["m"])

    def test_no_url_and_captcha_do_not_modify_a_registered_record(self):
        self._write([_rec("x", source_url=None, cdn_url=None,
                          original={"url": None, "fetched_from": None, "sha256": None})])
        before = self._raw()
        st = media.restore_images(self.dir, fetch=self._fetch)
        self.assertEqual((st["no_url"], st["failed_list"][0]["registered"]), (1, True))
        self.assertEqual(self._raw(), before)
        self._write([_rec("c")])
        before = self._raw()
        st = media.restore_images(self.dir, fetch=lambda u: b"<html>sg-captcha</html>")
        self.assertEqual((st["requires_manual_download"], st["manual"][0]["id"]), (1, "c"))  # va al informe
        self.assertEqual(self._raw(), before)  # pero el registro no se toca

    def test_failure_on_a_record_without_registered_copy_is_still_recorded(self):
        self._write([_rec("n", file=None, sha256=None, bytes=None, download_status="PENDING")])
        media.restore_images(self.dir, fetch=self._dns_down)
        self.assertTrue(self._load()[0]["download_status"].startswith("FAILED"))
        self._write([_rec("n", file=None, sha256=None, bytes=None, download_status="PENDING")])
        media.restore_images(self.dir, fetch=lambda u: b"<html>sg-captcha</html>")
        r = self._load()[0]
        self.assertEqual((r["download_status"], r["manual_download_url"]), ("requires_manual_download",
                                                                         "https://x.test/n.jpg"))

    def test_inconsistent_status_with_good_file_is_reported_not_modified(self):
        self._good("a")
        self._write([_rec("a", download_status="FAILED (URLError: x)")])
        before = self._raw()
        st = media.restore_images(self.dir, fetch=self._fetch)
        self.assertEqual(st["inconsistent_status"], [{"id": "a", "status": "FAILED (URLError: x)"}])
        self.assertEqual((self._raw(), self.calls), (before, []))

    def test_restore_main_reports_failures_without_modifying_images_json(self):
        self._write([_rec("a")])
        before = self._raw()
        out = self.root / "informe.json"
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = media.restore_main(["marca", "--restore", f"--report={out}"], self.root, fetch=self._dns_down)
        self.assertEqual(rc, 1)  # hubo fallos: codigo de salida 1
        self.assertIn("FALLÓ la descarga", buf.getvalue())
        self.assertIn("images.json no se modificó", buf.getvalue())
        self.assertEqual(json.loads(out.read_text(encoding="utf-8"))["failed"][0]["id"], "a")
        self.assertEqual(self._raw(), before)

    def test_download_images_does_not_overwrite_a_registered_record_on_failure(self):
        rec = _rec("a", declared_width=1, declared_height=1, declared_format="jpg")
        self._write([rec, dict(rec, id="n", file=None, sha256=None, bytes=None, download_status="PENDING")])

        def boom(url, timeout=120):
            raise OSError("getaddrinfo failed")
        with mock.patch.object(media, "_fetch", boom):
            st = media.download_images(self.dir)
        recs = {r["id"]: r for r in self._load()}
        self.assertEqual(st["failed"], 2)
        self.assertEqual(recs["a"]["download_status"], "WEB_COPY")  # copia registrada: no se pisa
        self.assertTrue(recs["n"]["download_status"].startswith("FAILED"))  # sin copia: se registra como antes

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

    # --- URLs con espacios y metadatos de la descarga en el informe (images.json no cambia) -------------------

    META = {"status": 200, "content_type": "image/png", "etag": '"abc123"',
            "last_modified": "Fri, 25 Oct 2024 19:17:40 GMT"}

    def _with_meta(self, meta=None):
        def fetch(url):
            self.calls.append(url)
            return _png(), dict(self.META if meta is None else meta)
        return fetch

    def _row(self, record, meta=None):
        for old in (self.imgs / "EXTERIOR").glob("*.webp"):  # estado limpio: una corrida anterior dejo la copia en disco
            old.unlink()
        self._write([record])
        st = media.restore_images(self.dir, fetch=self._with_meta(meta))
        self.assertEqual(len(st["downloads"]), 1)
        return st["downloads"][0], st

    def test_trailing_space_url_is_stripped_reported_and_inventory_not_rewritten(self):
        spaced = "https://x.test/a.jpg "
        self._write([_rec("a", source_url=spaced, original={"url": spaced, "fetched_from": spaced, "sha256": PNG_SHA,
                                                            "width": 64, "height": 48, "format": "png"}),
                     _rec("b")])
        before = self._raw()
        st = media.restore_images(self.dir, fetch=self._with_meta())
        self.assertIn("https://x.test/a.jpg", self.calls)  # sin el espacio
        self.assertNotIn(spaced, self.calls)
        self.assertEqual((st["restored_verified"], st["failed"], st["changed"]), (2, 0, 0))  # se verifica igual
        self.assertEqual(st["url_con_espacios"], [{"id": "a", "campos": ["original.fetched_from", "source_url", "original.url"]}])
        self.assertEqual(self._raw(), before)  # el dato con espacio sigue en el inventario: lo corrige Nico
        self.assertIn(spaced.encode(), self._raw())

    def test_report_row_has_every_field(self):
        row, _ = self._row(_rec("a", original={"url": "https://x.test/a.jpg", "fetched_from": "https://x.test/a.jpg",
                                               "sha256": PNG_SHA, "width": 64, "height": 48, "format": "png"}))
        self.assertEqual(row, {
            "id": "a", "modelo": "marca/modelo", "url_usada": "https://x.test/a.jpg", "http": 200,
            "content_type": "image/png", "bytes_descargados": len(_png()), "etag": '"abc123"',
            "last_modified": "Fri, 25 Oct 2024 19:17:40 GMT", "sha256_obtenido": PNG_SHA, "sha256_esperado": PNG_SHA,
            "formato_descargado": "png", "dimensiones_descargadas": "64x48", "formato_registrado": "png",
            "dimensiones_registradas": "64x48", "fecha_registro": "2026-10-04",
            "last_modified_anterior_al_registro": "sí", "dimensiones_coinciden": "sí", "resultado": "verificada"})

    def test_last_modified_flag(self):
        for lm, expected in (("Fri, 25 Oct 2024 19:17:40 GMT", "sí"),   # anterior al dia del registro
                             ("Sun, 04 Oct 2026 03:00:00 GMT", "no"),   # el mismo dia: no se puede afirmar que sea anterior
                             ("Tue, 20 Oct 2026 10:00:00 GMT", "no"),   # posterior
                             (None, "desconocido"),                     # el servidor no envio Last-Modified
                             ("no es una fecha", "desconocido")):
            row, _ = self._row(_rec("a"), meta={**self.META, "last_modified": lm})
            self.assertEqual(row["last_modified_anterior_al_registro"], expected, lm)
        row, _ = self._row(_rec("a", downloaded_at=None, collected_at=None))  # sin fecha de registro
        self.assertEqual(row["last_modified_anterior_al_registro"], "desconocido")
        row, _ = self._row(_rec("a", downloaded_at=None, collected_at="2026-10-01"))  # usa collected_at si falta downloaded_at
        self.assertEqual((row["fecha_registro"], row["last_modified_anterior_al_registro"]), ("2026-10-01", "sí"))

    def test_dimensions_flag(self):
        def orig(w, h):
            return {"url": "https://x.test/a.jpg", "fetched_from": "https://x.test/a.jpg", "sha256": PNG_SHA,
                    "width": w, "height": h, "format": "png"}
        for w, h, expected in ((64, 48, "sí"), (4000, 3000, "no"), (None, None, "desconocido"), (64, None, "desconocido")):
            row, _ = self._row(_rec("a", original=orig(w, h)))
            self.assertEqual(row["dimensiones_coinciden"], expected, (w, h))
        row, _ = self._row(_rec("a", original={"url": "https://x.test/a.jpg", "fetched_from": "https://x.test/a.jpg",
                                               "sha256": PNG_SHA}))  # el original no registra dimensiones
        self.assertEqual((row["dimensiones_registradas"], row["dimensiones_coinciden"]), (None, "desconocido"))

    def test_fetch_without_metadata_leaves_unknown_fields(self):
        self._write([_rec("a")])
        st = media.restore_images(self.dir, fetch=self._fetch)  # devuelve solo bytes, como antes
        row = st["downloads"][0]
        self.assertEqual((row["http"], row["content_type"], row["etag"], row["last_modified"]), (None, None, None, None))
        self.assertEqual((row["last_modified_anterior_al_registro"], row["resultado"]), ("desconocido", "verificada"))

    def test_changed_and_unverifiable_rows_carry_the_right_result(self):
        row, _ = self._row(_rec("c", original={"url": "https://x.test/c.jpg", "fetched_from": "https://x.test/c.jpg",
                                               "sha256": "f" * 64, "width": 64, "height": 48, "format": "png"}))
        self.assertEqual((row["resultado"], row["sha256_esperado"], row["sha256_obtenido"]), ("cambiada", "f" * 64, PNG_SHA))
        row, _ = self._row(_rec("u", original={"url": "https://x.test/u.jpg", "fetched_from": None, "sha256": None,
                                               "width": 64, "height": 48, "format": "png"}))
        self.assertEqual((row["resultado"], row["sha256_esperado"]), ("no verificable", None))

    def test_running_the_restore_twice_leaves_images_json_identical(self):
        spaced = "https://x.test/s.jpg "
        self._write([_rec("a"), _rec("s", source_url=spaced, original={"url": spaced, "fetched_from": spaced,
                                                                         "sha256": PNG_SHA, "width": 64, "height": 48,
                                                                         "format": "png"})])
        before = self._raw()
        first = io.StringIO()
        with contextlib.redirect_stdout(first):
            media.restore_main(["marca", "--restore", f"--report={self.root / 'r1.json'}"], self.root, fetch=self._with_meta())
        after_first = self._raw()
        second = io.StringIO()
        with contextlib.redirect_stdout(second):
            media.restore_main(["marca", "--restore", f"--report={self.root / 'r2.json'}"], self.root, fetch=self._with_meta())
        self.assertEqual(after_first, before)  # el informe ampliado no escribe nada en images.json
        self.assertEqual(self._raw(), after_first)  # y la segunda corrida lo deja igual
        for forbidden in (b"last_modified", b"etag", b"bytes_descargados", b"url_con_espacios", b"sha256_obtenido"):
            self.assertNotIn(forbidden, self._raw())
        r1 = json.loads((self.root / "r1.json").read_text(encoding="utf-8"))
        r2 = json.loads((self.root / "r2.json").read_text(encoding="utf-8"))
        self.assertEqual(len(r1["descargas"]), 2)  # la primera descarga y la segunda ya no (copias en disco)
        self.assertEqual(r2["descargas"], [])
        self.assertEqual([u["id"] for u in r1["url_con_espacios"]], ["s"])
        self.assertEqual([u["id"] for u in r2["url_con_espacios"]], ["s"])  # el dato sigue mal hasta que Nico lo corrija
        self.assertEqual(r1["resumen_descargas"]["verificada"]["last_modified_anterior_al_registro"], {"sí": 2})
        self.assertIn("url_con_espacios", first.getvalue())

    def test_fetch_meta_and_fetch_strip_the_url_and_send_the_same_headers(self):
        import email.message

        class Resp:
            status = 200

            def __init__(self):
                self.headers = email.message.Message()
                self.headers["Content-Type"] = "image/webp"
                self.headers["ETag"] = '"e1"'
                self.headers["Last-Modified"] = "Thu, 16 Jul 2026 17:14:31 GMT"

            def read(self):
                return b"datos"

            def getcode(self):
                return 200

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        requests = []

        class Opener:
            def open(self, req, timeout=None):
                requests.append(req)
                return Resp()

        with mock.patch("urllib.request.build_opener", lambda *h: Opener()):
            data, meta = media._fetch_meta("https://x.test/a b.webp ")
            self.assertEqual(media._fetch("https://x.test/a.webp  "), b"datos")
        self.assertEqual((data, meta), (b"datos", {"status": 200, "content_type": "image/webp", "etag": '"e1"',
                                                    "last_modified": "Thu, 16 Jul 2026 17:14:31 GMT"}))
        self.assertEqual(requests[0].full_url, "https://x.test/a%20b.webp")  # sin %20 final
        self.assertEqual(requests[1].full_url, "https://x.test/a.webp")
        for req in requests:
            self.assertEqual(dict(req.header_items()), {"User-agent": media.USER_AGENT})  # las mismas cabeceras de siempre

    # --- aceptacion por regla objetiva (--accept-by-rule --by=ap-NNN) ----------------------------------------------

    LM_BEFORE = "Fri, 25 Oct 2024 19:17:40 GMT"   # anterior al registro (2026-10-04)
    LM_AFTER = "Tue, 20 Oct 2026 10:00:00 GMT"    # posterior

    def _changed_rec(self, rid="a", width=64, height=48, **kw):
        """El original registrado tiene OTRO sha256 que el que sirve el servidor; mismas dimensiones que `_png()`."""
        orig = {"url": f"https://x.test/{rid}.jpg", "fetched_from": f"https://x.test/{rid}.jpg", "sha256": "f" * 64,
                "width": width, "height": height, "format": "png", "bytes": 999, "full_resolution": True}
        return _rec(rid, original=orig, sha256="a" * 64, **kw)

    def _accept_run(self, meta=None, png=None, accept_by="ap-001"):
        data = png or _png()
        return media.restore_images(self.dir, fetch=lambda url: (data, dict(self.META if meta is None else meta)),
                                    accept_by=accept_by)

    def _fresh(self, *records):
        for old in (self.imgs / "EXTERIOR").glob("*.webp"):
            old.unlink()
        self._write(list(records))

    def test_accept_by_rule_when_last_modified_is_earlier_and_dimensions_match(self):
        self._fresh(self._changed_rec("a"))
        orig_before = json.dumps(self._load()[0]["original"], sort_keys=True)
        web_before = {k: self._load()[0][k] for k in ("sha256", "bytes", "width", "height", "downloaded_at")}
        st = self._accept_run()
        self.assertEqual((st["accepted_by_rule"], st["changed"], st["failed"]), (1, 0, 0))
        self.assertEqual(st["downloads"][0]["resultado"], "aceptada por regla")
        r = self._load()[0]
        self.assertEqual(r["download_status"], "WEB_COPY")
        self.assertNotIn("restore_check", r)
        self.assertEqual(json.dumps(r["original"], sort_keys=True), orig_before)  # `original` intacto
        self.assertEqual({k: r[k] for k in web_before}, web_before)  # los campos de la copia tampoco cambian
        self.assertTrue(media._webp_ok(self.imgs / "EXTERIOR" / "a.webp"))
        (entry,) = r["review_log"]
        self.assertEqual((entry["decision"], entry["rule"], entry["by"]), ("accepted_by_rule", media.ACCEPT_RULE, "ap-001"))
        self.assertRegex(entry["at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertEqual((entry["sha256_observado"], entry["last_modified"], entry["etag"], entry["content_type"],
                          entry["bytes"]), (PNG_SHA, self.LM_BEFORE, '"abc123"', "image/png", len(_png())))
        self.assertEqual((entry["url"], entry["sha256_esperado_anterior"]), ("https://x.test/a.jpg", "f" * 64))

    def test_rule_rejects_every_other_case_and_leaves_review_required(self):
        cases = {
            "last_modified posterior (no / sí)": (dict(meta={**self.META, "last_modified": self.LM_AFTER}), {}),
            "dimensiones distintas (sí / no)": ({}, dict(width=4000, height=3000)),
            "last_modified ausente (desconocido / sí)": (dict(meta={**self.META, "last_modified": None}), {}),
            "last_modified ilegible (desconocido / sí)": (dict(meta={**self.META, "last_modified": "ayer"}), {}),
            "dimensiones registradas ausentes (sí / desconocido)": ({}, dict(width=None, height=None)),
        }
        for label, (run_kw, rec_kw) in cases.items():
            self._fresh(self._changed_rec("a", **rec_kw))
            orig_before = json.dumps(self._load()[0]["original"], sort_keys=True)
            st = self._accept_run(**run_kw)
            r = self._load()[0]
            self.assertEqual((st["accepted_by_rule"], st["changed"]), (0, 1), label)
            self.assertEqual(r["download_status"], "REQUIRES_REVIEW (original cambió)", label)
            self.assertNotIn("review_log", r, label)
            self.assertIn("restore_check", r, label)
            self.assertFalse((self.imgs / "EXTERIOR" / "a.webp").exists(), label)  # no se escribe la copia
            self.assertEqual(json.dumps(r["original"], sort_keys=True), orig_before, label)

    def test_without_the_option_nothing_is_accepted(self):
        self._fresh(self._changed_rec("a"))
        st = media.restore_images(self.dir, fetch=lambda url: (_png(), dict(self.META)))  # sin accept_by
        r = self._load()[0]
        self.assertEqual((st["accepted_by_rule"], st["changed"], r["download_status"]),
                         (0, 1, "REQUIRES_REVIEW (original cambió)"))
        self.assertNotIn("review_log", r)

    def test_later_restore_verifies_against_the_accepted_hash_and_detects_a_new_change(self):
        self._fresh(self._changed_rec("a"))
        self._accept_run()
        after_accept = self._raw()
        # con la copia en disco no hay nada que hacer
        st = self._accept_run()
        self.assertEqual((st["ok_on_disk"], st["downloads"]), (1, []))
        self.assertEqual(self._raw(), after_accept)
        # sin la copia: se baja de nuevo y se verifica contra el hash ACEPTADO (con y sin la opcion)
        for accept_by in ("ap-001", None):
            (self.imgs / "EXTERIOR" / "a.webp").unlink()
            st = media.restore_images(self.dir, fetch=lambda url: (_png(), dict(self.META)), accept_by=accept_by)
            self.assertEqual((st["restored_verified"], st["changed"]), (1, 0), accept_by)
            self.assertEqual(st["downloads"][0]["sha256_esperado"], PNG_SHA)  # el aceptado, no el original ("f"*64)
            self.assertEqual(self._raw(), after_accept, accept_by)  # images.json idéntico
        # el servidor cambia otra vez: vuelve a REQUIRES_REVIEW y NO se acepta una segunda vez
        (self.imgs / "EXTERIOR" / "a.webp").unlink()
        other = _png(color=(10, 200, 10))
        st = self._accept_run(png=other)
        r = self._load()[0]
        self.assertEqual((st["accepted_by_rule"], st["changed"]), (0, 1))
        self.assertEqual(r["download_status"], "REQUIRES_REVIEW (original cambió)")
        self.assertEqual(len(r["review_log"]), 1)  # se conserva la aceptación anterior
        self.assertEqual((r["restore_check"]["expected_sha256"], r["restore_check"]["downloaded_sha256"]),
                         (PNG_SHA, hashlib.sha256(other).hexdigest()))
        self.assertEqual(r["original"]["sha256"], "f" * 64)
        # y una aceptada que quedo en REQUIRES_REVIEW no se reevalua aunque se use la opcion
        again = self._raw()
        self.calls.clear()
        st = media.restore_images(self.dir, fetch=self._fetch, accept_by="ap-001")
        self.assertEqual((self.calls, self._raw()), ([], again))

    def test_accepting_twice_is_idempotent(self):
        self._fresh(self._changed_rec("a"), self._changed_rec("b", width=4000, height=3000))
        self._accept_run()
        first = self._raw()
        self._accept_run()
        self.assertEqual(self._raw(), first)  # una aceptada y una pendiente: la segunda corrida no cambia nada
        self.assertEqual([r["download_status"][:15] for r in self._load()], ["WEB_COPY", "REQUIRES_REVIEW"])

    def test_requires_review_from_an_earlier_run_is_only_reevaluated_with_the_option(self):
        flagged = self._changed_rec("a", download_status="REQUIRES_REVIEW (original cambió)")
        flagged["restore_check"] = {"url": "https://x.test/a.jpg", "expected_sha256": "f" * 64, "downloaded_sha256": PNG_SHA}
        self._fresh(flagged)
        before = self._raw()
        st = media.restore_images(self.dir, fetch=self._fetch)  # sin opcion: sigue pendiente de una persona
        self.assertEqual((self.calls, st["accepted_by_rule"], self._raw()), ([], 0, before))
        st = self._accept_run()
        self.assertEqual((st["accepted_by_rule"], self._load()[0]["download_status"]), (1, "WEB_COPY"))

    def test_cli_requires_by_and_reports_the_acceptance(self):
        self._fresh(self._changed_rec("a"))
        before = self._raw()
        for args in (["marca", "--restore", "--accept-by-rule"], ["marca", "--restore", "--accept-by-rule", "--by="],
                     ["marca", "--restore", "--by=ap-001"]):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = media.restore_main(args, self.root, fetch=self._fetch)
            self.assertEqual((rc, self.calls, self._raw()), (2, [], before), args)
            self.assertIn("ERROR", buf.getvalue())
        out = self.root / "informe.json"
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = media.restore_main(["marca", "--restore", "--accept-by-rule", "--by=ap-001", f"--report={out}"],
                                    self.root, fetch=lambda url: (_png(), dict(self.META)))
        self.assertEqual(rc, 0)
        self.assertIn("aceptadas por regla 1 (por ap-001)", buf.getvalue())
        rep = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(rep["total"]["accepted_by_rule"], 1)
        self.assertEqual(rep["aceptadas_por_regla"][0]["id"], "a")
        self.assertEqual(rep["resumen_descargas"]["aceptada por regla"]["total"], 1)

    def test_publish_r2_and_export_respect_the_state(self):
        # ok: aceptada por regla; no: no cumple la regla (REQUIRES_REVIEW); again: aceptada y luego cambio otra vez
        recs = [self._changed_rec(i) for i in ("ok", "no", "again")]
        self._fresh(*recs)

        def fetch(url):
            meta = dict(self.META, last_modified=self.LM_AFTER) if "/no." in url else dict(self.META)
            return _png(), meta
        media.restore_images(self.dir, fetch=fetch, accept_by="ap-001")
        (self.imgs / "EXTERIOR" / "again.webp").unlink()
        def second(url):  # el servidor sigue igual salvo "again", que cambia de nuevo
            png = _png(color=(1, 2, 3)) if "/again." in url else _png()
            return png, (dict(self.META, last_modified=self.LM_AFTER) if "/no." in url else dict(self.META))
        media.restore_images(self.dir, fetch=second, accept_by="ap-001")
        states = {r["id"]: r["download_status"][:15] for r in self._load()}
        self.assertEqual(states, {"ok": "WEB_COPY", "no": "REQUIRES_REVIEW", "again": "REQUIRES_REVIEW"})
        # publish-r2 (simulacion): solo la aceptada es elegible
        rep = r2.publish([self.dir])
        m = rep["models"][0]
        self.assertEqual((m["eligible"], m["skipped"].get("REQUIRES_REVIEW")), (1, 2))
        self.assertEqual(len(m["keys"]), 1)
        self.assertIn("/ok-", m["keys"][0])
        # export-portal: solo la aceptada entra en images[]
        from collections import Counter
        omitted = Counter()
        images, _, _ = exporter._build_images(self.dir, "marca", "modelo", "Modelo", True, omitted, [])
        self.assertEqual([i["id"] for i in images], ["ok"])
        self.assertEqual(omitted["REQUIRES_REVIEW"], 2)
    # --- identidad: --by solo acepta codigos de aprobador ap-NNN (el repo es publico) ----------------------------------

    def _bad_by_values(self):
        at = chr(64)  # sin escribir un correo literal en el repositorio
        return ["Johnny", "Johnny Muñoz", f"persona{at}ejemplo.com", f"{at}", "ap-", "ap-abc", "AP-001", "ap-001 ", " ap-001",
                "ap-001;x", "ap_001", "ap-00１", "001", "ap--001", ""]

    def test_valid_approver_codes(self):
        for good in ("ap-001", "ap-002", "ap-1", "ap-0001", "ap-999999"):
            self.assertTrue(media.valid_approver(good), good)
        for bad in self._bad_by_values() + [None, 1, ["ap-001"]]:
            self.assertFalse(media.valid_approver(bad), repr(bad))

    def test_cli_rejects_anything_that_is_not_an_approver_code_without_touching_anything(self):
        self._fresh(self._changed_rec("a"))
        before = self._raw()
        for bad in self._bad_by_values():
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = media.restore_main(["marca", "--restore", "--accept-by-rule", f"--by={bad}"], self.root, fetch=self._fetch)
            out = buf.getvalue()
            self.assertEqual((rc, self.calls, self._raw()), (2, [], before), repr(bad))  # nada se descarga ni se escribe
            self.assertIn("usa el código del aprobador", out, repr(bad))
            self.assertIn("ver aprobadores.md", out, repr(bad))
            self.assertIn("no escribas correos ni nombres", out, repr(bad))
            if bad.strip() and bad.strip() not in media.APPROVER_HELP:  # ("ap-" aparece en el propio mensaje de ayuda)
                self.assertNotIn(bad.strip(), out, repr(bad))  # el valor recibido no se repite en la salida
        self.assertFalse((self.imgs / "EXTERIOR" / "a.webp").exists())

    def test_api_also_refuses_a_value_that_is_not_a_code(self):
        self._fresh(self._changed_rec("a"))
        before = self._raw()
        for bad in self._bad_by_values()[:-1]:  # "" equivale a no pasar la opcion
            with self.assertRaises(ValueError) as cm:
                media.restore_images(self.dir, fetch=self._fetch, accept_by=bad)
            if bad.strip() and bad.strip() not in media.APPROVER_HELP:
                self.assertNotIn(bad.strip(), str(cm.exception))  # el valor recibido no se repite en el error
        self.assertEqual((self.calls, self._raw()), ([], before))

    def test_no_accepted_entry_carries_an_email_or_a_name(self):
        self._fresh(self._changed_rec("a"), self._changed_rec("b"))
        self._accept_run()
        raw = self._raw().decode("utf-8")
        self.assertNotIn(chr(64), raw)
        for entry in (e for r in self._load() for e in r.get("review_log", [])):
            self.assertRegex(entry["by"], r"^ap-[0-9]+$")
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
