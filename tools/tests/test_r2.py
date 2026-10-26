import contextlib
import hashlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

try:
    from tests import _netguard  # noqa: F401  (bloquea la red en las pruebas)
except ImportError:  # unittest discover -s tests
    import _netguard  # noqa: F401
from oceanic import mediarules, r2

SECRETS = {"R2_ACCOUNT_ID": "acct123secret", "R2_ACCESS_KEY_ID": "AKIAKEYSECRET99",
           "R2_SECRET_ACCESS_KEY": "sEcReTvAlUe777", "R2_BUCKET": "bucket-secret-name"}


class FakeClient:
    def __init__(self, fail_after=None, exc=None):
        self.objects = {}  # key -> {"body", "sha256", "headers"}
        self.heads = 0
        self.puts = 0
        self.fail_after = fail_after
        self.exc = exc

    def head_object(self, key):
        self.heads += 1
        o = self.objects.get(key)
        return None if o is None else {"sha256": o["sha256"]}

    def put_object(self, key, body, content_type, cache_control, metadata):
        if self.fail_after is not None and self.puts >= self.fail_after:
            raise self.exc or r2.R2Error("fallo permanente")
        self.puts += 1
        self.objects[key] = {"body": body, "sha256": metadata["sha256"],
                             "headers": {"content_type": content_type, "cache_control": cache_control,
                                         "metadata": metadata}}

    def delete_object(self, *a, **k):
        raise AssertionError("delete_object no debe llamarse")

    def __getattr__(self, name):
        if name.startswith("delete"):
            raise AssertionError("borrado prohibido")
        raise AttributeError(name)


def _webp(seed: int, size=(8, 6)) -> bytes:
    im = Image.new("RGB", size)
    im.putdata([(seed * 7 % 256, (i * 13 + seed) % 256, (i * seed) % 256) for i in range(size[0] * size[1])])
    b = io.BytesIO()
    im.save(b, "WEBP", quality=75)
    return b.getvalue()


class Lib:
    def __init__(self, tmp: Path):
        self.root = tmp
        self.bib = tmp / "biblioteca"

    def model(self, brand, name, imgs, curated=False):
        """imgs: lista de dicts con id, category, seed, y opciones (mode, status, scope)."""
        md = self.bib / brand / name
        (md / "00_MODELO").mkdir(parents=True)
        (md / "00_MODELO" / "00_MODELO.md").write_text("# m\n", encoding="utf-8", newline="\n")
        if curated:
            (md / "00_MODELO" / "model.json").write_text(json.dumps({"slug": name, "curated": True}),
                                                         encoding="utf-8", newline="\n")
        recs = []
        for i in imgs:
            rec = {"id": i["id"], "category": i.get("category", "EXTERIOR"), "scope": i.get("scope", "THIS_MODEL"),
                   "file": None, "width": None, "height": None, "sha256": None,
                   "download_status": i.get("status", "WEB_COPY")}
            mode = i.get("mode", "ok")
            if mode != "nofile" and "seed" in i:
                data = _webp(i["seed"])
                rel = f"05_MULTIMEDIA/IMAGENES/{rec['category']}/{i['id']}.webp"
                p = md / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                rec.update(file=rel, width=8, height=6, sha256=hashlib.sha256(data).hexdigest())
                if mode == "corrupt":
                    data = b"no es webp"
                elif mode == "missing":
                    data = None
                elif mode == "different":
                    rec["sha256"] = "0" * 64
                if data is not None:
                    p.write_bytes(data)
            elif mode == "nofile":
                rec["file"] = "05_MULTIMEDIA/IMAGENES/X/nada.webp"
            recs.append(rec)
        inv = md / "05_MULTIMEDIA" / "IMAGENES" / "images.json"
        inv.parent.mkdir(parents=True, exist_ok=True)
        inv.write_text(json.dumps({"images": recs}), encoding="utf-8", newline="\n")
        return md


class R2Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.lib = Lib(Path(self._tmp.name))
        p = mock.patch.object(r2, "_sleep", lambda s: None)
        p.start()
        self.addCleanup(p.stop)

    def snapshot(self):
        return sorted((str(p), p.stat().st_mtime_ns) for p in self.lib.root.rglob("*") if p.is_file())


class DryRunAndPlan(R2Base):
    def test_dry_run_no_writes_no_client(self):
        self.lib.model("marca", "uno", [{"id": "a", "seed": 1}, {"id": "b", "seed": 2}])
        before = self.snapshot()
        client = FakeClient()
        rep = r2.publish([self.lib.bib / "marca" / "uno"], client=client)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual((client.heads, client.puts), (0, 0))
        self.assertEqual(rep["total"]["to_upload"], 2)
        self.assertEqual(rep["total"]["uploaded"], 0)

    def test_dry_run_needs_no_credentials(self):
        self.lib.model("marca", "uno", [{"id": "a", "seed": 1}])
        with mock.patch.dict(os.environ, {}, clear=True):
            rep = r2.publish([self.lib.bib / "marca" / "uno"])
        self.assertEqual(rep["total"]["to_upload"], 1)

    def test_skips_by_reason_and_audit(self):
        self.lib.model("marca", "uno", [
            {"id": "ok", "seed": 1},
            {"id": "dist", "seed": 2, "mode": "different"},
            {"id": "corr", "seed": 3, "mode": "corrupt"},
            {"id": "miss", "seed": 4, "mode": "missing"},
            {"id": "nof", "mode": "nofile"},
            {"id": "fail", "status": "FAILED (x)", "mode": "none"},
            {"id": "pend", "status": "PENDING", "mode": "none"},
            {"id": "dup", "status": "DUPLICATE_OF ok", "mode": "none"},
            {"id": "man", "status": "FAILED (desafio anti-bots)", "mode": "none"},
            {"id": "otro", "status": "algo", "mode": "none"},
            {"id": "fuera", "seed": 9, "scope": "OTHER_MODEL"},
        ])
        rep = r2.publish([self.lib.bib / "marca" / "uno"])
        m = rep["models"][0]
        self.assertEqual(m["audit"], {"ok": 1, "distinta": 1, "corrupta": 1, "ausente": 2})
        self.assertEqual(m["skipped"]["corrupta"], 1)
        self.assertEqual(m["skipped"]["sin_archivo"], 2)
        self.assertEqual(m["skipped"]["DUPLICATE_OF"], 1)
        self.assertEqual(m["skipped"]["PENDING"], 1)
        self.assertEqual(m["skipped"]["FAILED"], 2)
        self.assertEqual(m["skipped"]["otro"], 1)
        self.assertEqual(m["to_upload"], 2)
        self.assertTrue(any("distinta" in w for w in m["warnings"]))
        self.assertEqual(rep["brands"]["marca"]["eligible"], 3)

    def test_requires_manual_download_reason(self):
        md = self.lib.model("marca", "uno", [{"id": "a", "status": "X", "mode": "none"}])
        inv = md / "05_MULTIMEDIA" / "IMAGENES" / "images.json"
        d = json.loads(inv.read_text(encoding="utf-8"))
        d["images"][0]["requires_manual_download"] = True
        inv.write_text(json.dumps(d), encoding="utf-8", newline="\n")
        rep = r2.publish([md])
        self.assertEqual(rep["models"][0]["skipped"], {"requires_manual_download": 1})


class UploadTests(R2Base):
    def test_key_headers_and_real_hash(self):
        md = self.lib.model("marca", "uno", [{"id": "a", "seed": 1, "category": "HERO"},
                                             {"id": "b", "seed": 2, "mode": "different"}])
        c = FakeClient()
        rep = r2.publish([md], apply=True, client=c)
        self.assertEqual(rep["total"]["uploaded"], 2)
        for key, o in c.objects.items():
            sha = hashlib.sha256(o["body"]).hexdigest()
            self.assertEqual(o["sha256"], sha)
            self.assertEqual(o["headers"], {"content_type": "image/webp",
                                            "cache_control": "public, max-age=31536000, immutable",
                                            "metadata": {"sha256": sha}})
            self.assertNotIn("\\", key)
        a = next(iter(k for k in c.objects if "/hero/" in k))
        self.assertEqual(a, mediarules.image_key("marca", "uno", "HERO", "a", hashlib.sha256(c.objects[a]["body"]).hexdigest()))
        self.assertTrue(a.startswith("marca/uno/hero/a-") and a.endswith(".webp"))

    def test_state_file_contents_no_secrets(self):
        md = self.lib.model("marca", "uno", [{"id": "a", "seed": 1}])
        with mock.patch.dict(os.environ, SECRETS):
            r2.publish([md], apply=True, client=FakeClient())
        p = md / "05_MULTIMEDIA" / "IMAGENES" / "r2.json"
        raw = p.read_text(encoding="utf-8")
        for v in SECRETS.values():
            self.assertNotIn(v, raw)
        e = json.loads(raw)["images"][0]
        self.assertEqual(set(e), {"id", "key", "sha256", "bytes", "uploaded_at"})
        self.assertEqual([f.name for f in p.parent.glob("*.tmp*")], [])

    def test_idempotent(self):
        md = self.lib.model("marca", "uno", [{"id": "a", "seed": 1}, {"id": "b", "seed": 2}])
        c = FakeClient()
        r2.publish([md], apply=True, client=c)
        self.assertEqual(c.puts, 2)
        rep = r2.publish([md], apply=True, client=c)
        self.assertEqual(c.puts, 2)
        self.assertEqual(rep["total"]["uploaded"], 0)
        self.assertEqual(rep["total"]["already"], 2)

    def test_idempotent_without_state_via_head(self):
        md = self.lib.model("marca", "uno", [{"id": "a", "seed": 1}])
        c = FakeClient()
        r2.publish([md], apply=True, client=c)
        (md / "05_MULTIMEDIA" / "IMAGENES" / "r2.json").unlink()
        rep = r2.publish([md], apply=True, client=c)
        self.assertEqual(c.puts, 1)
        self.assertEqual(rep["total"]["already"], 1)
        self.assertTrue((md / "05_MULTIMEDIA" / "IMAGENES" / "r2.json").exists())

    def test_resume_after_interruption(self):
        md = self.lib.model("marca", "uno", [{"id": f"i{n}", "seed": n} for n in range(1, 6)])
        c = FakeClient(fail_after=2)
        rep = r2.publish([md], apply=True, client=c, workers=1)
        self.assertEqual(rep["total"]["uploaded"], 2)
        self.assertEqual(rep["total"]["errors"], 3)
        c.fail_after = None
        rep = r2.publish([md], apply=True, client=c, workers=1)
        self.assertEqual(rep["total"]["uploaded"], 3)
        self.assertEqual(c.puts, 5)
        self.assertEqual(len(c.objects), 5)

    def test_periodic_save_survives_crash(self):
        md = self.lib.model("marca", "uno", [{"id": f"i{n}", "seed": n} for n in range(1, 5)])
        c = FakeClient(fail_after=3, exc=KeyboardInterrupt())
        with mock.patch.object(r2, "SAVE_EVERY", 2):
            with self.assertRaises(KeyboardInterrupt):
                r2.publish([md], apply=True, client=c, workers=1)
        st = json.loads((md / "05_MULTIMEDIA" / "IMAGENES" / "r2.json").read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(st["images"]), 2)

    def test_transient_errors_retry(self):
        md = self.lib.model("marca", "uno", [{"id": "a", "seed": 1}])

        class Flaky(FakeClient):
            n = 0

            def put_object(self, *a, **k):
                Flaky.n += 1
                if Flaky.n < 3:
                    raise r2.R2Transient("temporal")
                return super().put_object(*a, **k)

        c = Flaky()
        rep = r2.publish([md], apply=True, client=c)
        self.assertEqual(rep["total"]["uploaded"], 1)
        self.assertEqual(Flaky.n, 3)

    def test_never_overwrites_different_sha(self):
        md = self.lib.model("marca", "uno", [{"id": "a", "seed": 1}])
        c = FakeClient()
        r2.publish([md], apply=False, client=c)
        r2.publish([md], apply=True, client=c)
        key = next(iter(c.objects))
        c.objects[key]["sha256"] = "f" * 64
        c.objects[key]["body"] = b"otro"
        (md / "05_MULTIMEDIA" / "IMAGENES" / "r2.json").unlink()
        puts = c.puts
        rep = r2.publish([md], apply=True, client=c)
        self.assertEqual(c.puts, puts)
        self.assertEqual(c.objects[key]["body"], b"otro")
        self.assertEqual(rep["total"]["errors"], 1)

    def test_changed_sha_marks_superseded(self):
        md = self.lib.model("marca", "uno", [{"id": "a", "seed": 1}])
        c = FakeClient()
        r2.publish([md], apply=True, client=c)
        f = next((md / "05_MULTIMEDIA" / "IMAGENES").rglob("a.webp"))
        f.write_bytes(_webp(77))
        r2.publish([md], apply=True, client=c)
        st = json.loads((md / "05_MULTIMEDIA" / "IMAGENES" / "r2.json").read_text(encoding="utf-8"))["images"]
        self.assertEqual(len(st), 2)
        self.assertEqual([bool(e.get("superseded")) for e in st], [True, False])
        self.assertEqual(len(c.objects), 2)  # la vieja no se toca

    def test_verify_remote_dry_run_does_head_only(self):
        md = self.lib.model("marca", "uno", [{"id": "a", "seed": 1}])
        c = FakeClient()
        before = self.snapshot()
        rep = r2.publish([md], verify_remote=True, client=c)
        self.assertEqual((c.heads, c.puts), (1, 0))
        self.assertEqual(rep["total"]["to_upload"], 1)
        self.assertEqual(self.snapshot(), before)

    def test_curated_state_in_underscore_r2(self):
        md = self.lib.model("marca", "cur", [{"id": "a", "seed": 1}], curated=True)
        r2.publish([md], apply=True, client=FakeClient())
        self.assertTrue((self.lib.bib / "marca" / "_r2" / "cur.json").exists())
        self.assertFalse((md / "05_MULTIMEDIA" / "IMAGENES" / "r2.json").exists())
        self.assertFalse((md / "00_MODELO" / "model.json").read_text(encoding="utf-8").count("r2"))


class NoDeleteTests(R2Base):
    def test_source_has_no_delete_word(self):
        src = Path(r2.__file__).read_text(encoding="utf-8").lower()
        self.assertNotIn("delete", src)

    def test_client_has_only_head_get_and_put(self):
        # get_object es de solo lectura (catalog/latest.json antes de sobrescribirlo); no hay ningun metodo de borrado
        public = {n for n in dir(r2.R2Client) if not n.startswith("_")}
        self.assertEqual(public, {"head_object", "get_object", "put_object"})

    def test_publish_never_calls_delete(self):
        md = self.lib.model("marca", "uno", [{"id": "a", "seed": 1}])
        c = FakeClient()  # lanza AssertionError ante cualquier delete*
        r2.publish([md], apply=True, client=c)
        r2.publish([md], apply=True, verify_remote=True, client=c)


class CredentialTests(R2Base):
    def test_missing_names_only(self):
        md = self.lib.model("marca", "uno", [{"id": "a", "seed": 1}])
        env = {"R2_ACCOUNT_ID": SECRETS["R2_ACCOUNT_ID"], "R2_BUCKET": SECRETS["R2_BUCKET"]}
        for flag in ("--apply", "--verify-remote"):
            out = io.StringIO()
            with mock.patch.dict(os.environ, env, clear=True), contextlib.redirect_stdout(out):
                code = r2.main_cli([str(md), flag], self.lib.root)
            text = out.getvalue()
            self.assertNotEqual(code, 0)
            self.assertIn("R2_ACCESS_KEY_ID", text)
            self.assertIn("R2_SECRET_ACCESS_KEY", text)
            self.assertNotIn("R2_ACCOUNT_ID", text)
            self.assertNotIn("R2_BUCKET", text)
            for v in env.values():
                self.assertNotIn(v, text)

    def test_missing_credentials_exception(self):
        with mock.patch.dict(os.environ, {"R2_BUCKET": "b-secret-x"}, clear=True):
            with self.assertRaises(r2.MissingCredentials) as cm:
                r2.R2Client()
        self.assertNotIn("b-secret-x", str(cm.exception))
        self.assertEqual(cm.exception.names, ["R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY"])

    def test_error_messages_are_scrubbed(self):
        md = self.lib.model("marca", "uno", [{"id": "a", "seed": 1}])

        class Leaky(FakeClient):
            def put_object(self, *a, **k):
                raise RuntimeError(f"https://{SECRETS['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com/"
                                   f"{SECRETS['R2_BUCKET']} {SECRETS['R2_SECRET_ACCESS_KEY']}")

        with mock.patch.dict(os.environ, SECRETS):
            rep = r2.publish([md], apply=True, client=Leaky())
            text = r2.format_report(rep)
        self.assertEqual(rep["total"]["errors"], 1)
        blob = json.dumps(rep) + text
        for v in SECRETS.values():
            self.assertNotIn(v, blob)
        self.assertNotIn("cloudflarestorage", blob)

    def test_boto3_wrapper_scrubs(self):
        e = Exception("boom acct123secret")
        e.response = {"Error": {"Code": "AccessDenied"}, "ResponseMetadata": {"HTTPStatusCode": 403}}
        w = r2.R2Client._wrap(e)
        self.assertNotIsInstance(w, r2.R2Transient)
        self.assertNotIn("acct123secret", str(w))
        e.response["ResponseMetadata"]["HTTPStatusCode"] = 503
        self.assertIsInstance(r2.R2Client._wrap(e), r2.R2Transient)


class CliTests(R2Base):
    def test_cli_dry_run_brand_and_all(self):
        self.lib.model("marca", "uno", [{"id": "a", "seed": 1}])
        self.lib.model("marca", "dos", [{"id": "a", "seed": 2}])
        self.lib.model("otra", "tres", [{"id": "a", "seed": 3}])
        for args, n in ((["--all"], 3), (["marca"], 2), ([str(self.lib.bib / "otra" / "tres")], 1),
                        (["marca/uno"], 1)):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = r2.main_cli(args, self.lib.root)
            self.assertEqual(code, 0, out.getvalue())
            self.assertIn(f"TOTAL: modelos={n}", out.getvalue())
            self.assertIn("dry-run", out.getvalue())

    def test_cli_unknown_target(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(r2.main_cli(["nada"], self.lib.root), 2)

    def test_requires_review_images_are_never_published(self):
        md = self.lib.model("marca", "uno", [
            {"id": "ok", "seed": 1},
            {"id": "cambio", "seed": 2, "status": "REQUIRES_REVIEW (original cambió)"}])  # hay un archivo viejo en disco
        rep = r2.publish([md])
        m = rep["models"][0]
        self.assertEqual(m["eligible"], 1)
        self.assertEqual(m["skipped"], {"REQUIRES_REVIEW": 1})
        self.assertEqual(len(m["keys"]), 1)
        self.assertIn("/ok-", m["keys"][0])

    def test_windows_separator_in_keys(self):
        md = self.lib.model("marca", "uno", [{"id": "a", "seed": 1, "category": "PLANS"}])
        rep = r2.publish([md])
        for k in rep["models"][0]["keys"]:
            self.assertNotIn("\\", k)
            self.assertEqual(k.count("/"), 3)


if __name__ == "__main__":
    unittest.main()
