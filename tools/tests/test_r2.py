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

    # claves permitidas en una entrada de r2.json (lista cerrada): cualquier otra, p. ej. cuenta, bucket o token, falla
    ENTRY_REQUIRED = {"id", "key", "sha256", "bytes", "uploaded_at"}
    ENTRY_OPTIONAL = {"encoder", "original_sha256", "source", "superseded", "superseded_at"}
    ENCODER_KEYS = {"pillow", "libwebp", "quality", "method"}

    def _unexpected(self, entry: dict) -> set:
        extra = set(entry) - self.ENTRY_REQUIRED - self.ENTRY_OPTIONAL
        extra |= {f"encoder.{k}" for k in set(entry.get("encoder") or {}) - self.ENCODER_KEYS}
        return extra | {f"falta:{k}" for k in self.ENTRY_REQUIRED - set(entry)}

    def test_state_file_contents_no_secrets(self):
        md = self.lib.model("marca", "uno", [{"id": "a", "seed": 1}, {"id": "b", "seed": 2}])
        inv = md / "05_MULTIMEDIA" / "IMAGENES" / "images.json"
        data = json.loads(inv.read_text(encoding="utf-8"))
        data["images"][0]["original"] = {"sha256": "ab" * 32}  # "a" tiene original.sha256, "b" no
        inv.write_text(json.dumps(data), encoding="utf-8", newline="\n")
        with mock.patch.dict(os.environ, SECRETS):
            r2.publish([md], apply=True, client=FakeClient())
        p = md / "05_MULTIMEDIA" / "IMAGENES" / "r2.json"
        raw = p.read_text(encoding="utf-8")
        for v in SECRETS.values():
            self.assertNotIn(v, raw)
        for name in list(SECRETS) + ["account", "bucket", "token", "secret", "endpoint", "credential"]:
            self.assertNotIn(name.lower(), raw.lower())
        entries = {e["id"]: e for e in json.loads(raw)["images"]}
        for e in entries.values():
            self.assertEqual(self._unexpected(e), set(), e)  # solo claves permitidas
        self.assertEqual(entries["a"]["original_sha256"], "ab" * 32)  # original_sha256 esta permitido
        self.assertNotIn("original_sha256", entries["b"])
        self.assertEqual([f.name for f in p.parent.glob("*.tmp*")], [])

    def test_unexpected_entry_keys_are_detected(self):
        base = {"id": "a", "key": "k", "sha256": "1" * 64, "bytes": 1, "uploaded_at": "t",
                "encoder": {"pillow": "13.0.0", "libwebp": "1.6.0", "quality": 75, "method": 6},
                "original_sha256": "2" * 64}
        self.assertEqual(self._unexpected(base), set())
        for extra in ({"account_id": "x"}, {"bucket": "b"}, {"token": "t"}, {"R2_SECRET_ACCESS_KEY": "s"}):
            self.assertTrue(self._unexpected({**base, **extra}), extra)
        self.assertTrue(self._unexpected({**base, "encoder": {**base["encoder"], "endpoint": "e"}}))
        self.assertTrue(self._unexpected({k: v for k, v in base.items() if k != "key"}))

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

    def test_client_has_only_head_get_put_and_copy(self):
        # get_object es de solo lectura (catalog/latest.json antes de sobrescribirlo); copy_object solo copia dentro del
        # bucket (backup-photos / restore-photos); no hay ningun metodo de borrado
        public = {n for n in dir(r2.R2Client) if not n.startswith("_")}
        self.assertEqual(public, {"head_object", "get_object", "put_object", "copy_object"})

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


class MetadataTests(R2Base):
    """encoder y original_sha256 en r2.json; auditoria contra r2.json; completar entradas existentes."""

    def _state_path(self, md):
        return md / "05_MULTIMEDIA" / "IMAGENES" / "r2.json"

    def _add_original(self, md, image_id, sha):
        inv = md / "05_MULTIMEDIA" / "IMAGENES" / "images.json"
        data = json.loads(inv.read_text(encoding="utf-8"))
        for rec in data["images"]:
            if rec["id"] == image_id:
                rec["original"] = {"url": "https://x.test/o.jpg", "sha256": sha}
        inv.write_text(json.dumps(data), encoding="utf-8", newline="\n")

    def test_requirements_pin_pillow_exactly_and_nothing_else(self):
        import PIL
        lines = (Path(r2.__file__).resolve().parents[1] / "requirements.txt").read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines[0], "pillow==13.0.0")
        self.assertEqual(PIL.__version__, "13.0.0")  # el entorno debe coincidir con lo fijado
        self.assertTrue(all(">=" in ln for ln in lines[1:]))  # las demas dependencias no se tocaron

    def test_encoder_info_matches_environment_and_media_rules(self):
        import PIL
        from PIL import features
        info = r2.encoder_info()
        self.assertEqual(info, {"pillow": PIL.__version__, "libwebp": features.version("webp"),
                                "quality": mediarules.rules()["webp"]["quality"],
                                "method": mediarules.rules()["webp"]["method"]})
        self.assertEqual((info["quality"], info["method"]), (75, 6))

    def test_new_entries_record_encoder_and_original_sha256_when_it_exists(self):
        md = self.lib.model("marca", "uno", [{"id": "a", "seed": 1}, {"id": "b", "seed": 2}])
        self._add_original(md, "a", "ab" * 32)  # "b" no tiene original.sha256 (p. ej. copia de la CDN)
        r2.publish([md], apply=True, client=FakeClient())
        entries = {e["id"]: e for e in json.loads(self._state_path(md).read_text(encoding="utf-8"))["images"]}
        self.assertEqual(entries["a"]["original_sha256"], "ab" * 32)
        self.assertNotIn("original_sha256", entries["b"])
        for e in entries.values():
            self.assertEqual(e["encoder"], r2.encoder_info())
            self.assertEqual(set(e), {"id", "key", "sha256", "bytes", "uploaded_at", "encoder"} | (
                {"original_sha256"} if e["id"] == "a" else set()))
        self.assertEqual(json.loads(self._state_path(md).read_text(encoding="utf-8"))["version"], 1)

    def test_old_r2_json_without_new_fields_is_still_read_and_not_rewritten(self):
        md = self.lib.model("marca", "uno", [{"id": "a", "seed": 1}])
        r2.publish([md], apply=True, client=FakeClient())
        p = self._state_path(md)
        data = json.loads(p.read_text(encoding="utf-8"))
        for e in data["images"]:
            e.pop("encoder", None)
            e.pop("original_sha256", None)
        p.write_text(json.dumps(data, indent=1), encoding="utf-8", newline="\n")
        before = p.read_bytes()
        client = FakeClient()
        rep = r2.publish([md], apply=True, client=client)
        self.assertEqual((rep["total"]["uploaded"], rep["total"]["already"], rep["total"]["errors"]), (0, 1, 0))
        self.assertEqual(p.read_bytes(), before)  # no se reescribe ni se completa solo

    def test_audit_trusts_r2_json_over_the_inventory_sha(self):
        md = self.lib.model("marca", "uno", [{"id": "a", "seed": 1, "mode": "different"}])  # sha del inventario distinto
        inv = md / "05_MULTIMEDIA" / "IMAGENES" / "images.json"
        inv_before = inv.read_bytes()
        first = r2.publish([md], apply=True, client=FakeClient())
        self.assertEqual(first["models"][0]["audit"]["distinta"], 1)
        self.assertTrue(first["models"][0]["warnings"])  # la primera vez aun no hay nada registrado
        second = r2.publish([md])
        m = second["models"][0]
        self.assertEqual((m["audit"]["ok"], m["audit"]["distinta"], m["warnings"], m["already"]), (1, 0, [], 1))
        self.assertEqual(inv.read_bytes(), inv_before)  # no se toca el inventario

    def test_audit_still_warns_when_r2_json_registers_another_key(self):
        md = self.lib.model("marca", "uno", [{"id": "a", "seed": 1, "mode": "different"}])
        r2.publish([md], apply=True, client=FakeClient())
        p = self._state_path(md)
        data = json.loads(p.read_text(encoding="utf-8"))
        data["images"][0]["key"] = data["images"][0]["key"].replace(".webp", "x.webp")  # r2.json registra otra clave
        p.write_text(json.dumps(data), encoding="utf-8", newline="\n")
        m = r2.publish([md])["models"][0]
        self.assertEqual((m["audit"]["distinta"], m["audit"]["ok"]), (1, 0))
        self.assertTrue(m["warnings"])

    # --- completar las entradas ya subidas (una sola vez) -------------------------------------------------------

    def _old_state(self, md, entries):
        p = self._state_path(md)
        p.write_text(json.dumps({"version": 1, "images": entries}, indent=1), encoding="utf-8", newline="\n")
        return p

    ENC = {"pillow": "13.0.0", "libwebp": "1.6.0", "quality": 75, "method": 6}

    def test_backfill_dry_run_then_apply_fills_only_missing_fields(self):
        md = self.lib.model("marca", "uno", [{"id": "a", "seed": 1}, {"id": "b", "seed": 2}, {"id": "c", "seed": 3}])
        for i, sha in (("a", "aa" * 32), ("b", "bb" * 32), ("c", "cc" * 32)):
            self._add_original(md, i, sha)
        p = self._old_state(md, [
            {"id": "a", "key": "marca/uno/exterior/a-11111111.webp", "sha256": "1" * 64, "bytes": 5, "uploaded_at": "t"},
            {"id": "b", "key": "marca/uno/exterior/b-22222222.webp", "sha256": "2" * 64, "bytes": 5, "uploaded_at": "t",
             "encoder": {"pillow": "9.9.9", "libwebp": "0.0", "quality": 1, "method": 1}, "original_sha256": "zz"},
            {"id": "c", "key": "marca/uno/exterior/c-33333333.webp", "sha256": "3" * 64, "bytes": 5, "uploaded_at": "t",
             "superseded": True}])
        inv = md / "05_MULTIMEDIA" / "IMAGENES" / "images.json"
        inv_before, state_before = inv.read_bytes(), p.read_bytes()
        rep = r2.backfill_metadata([md], self.ENC)  # simulacion
        self.assertEqual((rep["encoder_added"], rep["original_added"], rep["entries"]), (1, 1, 2))
        self.assertEqual(p.read_bytes(), state_before)
        rep = r2.backfill_metadata([md], self.ENC, apply=True)
        data = json.loads(p.read_text(encoding="utf-8"))
        a, b, c = data["images"]
        self.assertEqual((a["encoder"], a["original_sha256"]), (self.ENC, "aa" * 32))
        self.assertEqual((b["encoder"]["pillow"], b["original_sha256"]), ("9.9.9", "zz"))  # nunca sobrescribe
        self.assertNotIn("encoder", c)  # las superseded no se tocan
        self.assertEqual(data["version"], 1)
        self.assertEqual(inv.read_bytes(), inv_before)  # images.json intacto
        again = r2.backfill_metadata([md], self.ENC, apply=True)  # una sola vez: la segunda no cambia nada
        self.assertEqual((again["encoder_added"], again["original_added"]), (0, 0))

    def test_backfill_needs_no_client_and_no_credentials(self):
        md = self.lib.model("marca", "uno", [{"id": "a", "seed": 1}])
        self._old_state(md, [{"id": "a", "key": "marca/uno/exterior/a-11111111.webp", "sha256": "1" * 64, "bytes": 5,
                              "uploaded_at": "t"}])
        with mock.patch.object(r2, "R2Client", side_effect=AssertionError("no debe crear cliente")):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = r2.main_backfill(["marca", "--pillow=13.0.0", "--libwebp=1.6.0"], self.lib.root)
        self.assertEqual(rc, 0)
        self.assertIn("simulacion", buf.getvalue())
        self.assertIn("encoder agregado: 1", buf.getvalue())

    def test_backfill_cli_requires_explicit_versions_and_validates_them(self):
        md = self.lib.model("marca", "uno", [{"id": "a", "seed": 1}])
        self._old_state(md, [{"id": "a", "key": "marca/uno/exterior/a-11111111.webp", "sha256": "1" * 64, "bytes": 5,
                              "uploaded_at": "t"}])
        for args in (["marca"], ["marca", "--pillow=13.0.0"], ["marca", "--pillow=x", "--libwebp=1.6.0"]):
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(r2.main_backfill(args, self.lib.root), 2, args)
        self.assertNotIn("encoder", json.loads(self._state_path(md).read_text(encoding="utf-8"))["images"][0])

    def test_backfill_uses_curated_state_path(self):
        md = self.lib.model("marca", "cur", [{"id": "a", "seed": 1}], curated=True)
        p = md.parent / "_r2" / "cur.json"
        p.parent.mkdir()
        p.write_text(json.dumps({"version": 1, "images": [{"id": "a", "key": "k", "sha256": "1" * 64, "bytes": 1,
                                                          "uploaded_at": "t"}]}), encoding="utf-8", newline="\n")
        r2.backfill_metadata([md], self.ENC, apply=True)
        self.assertEqual(json.loads(p.read_text(encoding="utf-8"))["images"][0]["encoder"], self.ENC)
        self.assertFalse(self._state_path(md).exists())  # el paquete curated no se toca


if __name__ == "__main__":
    unittest.main()
