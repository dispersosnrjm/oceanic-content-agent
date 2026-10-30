import contextlib
import hashlib
import io
import json
import os
import unittest
from pathlib import Path
from unittest import mock

try:
    from tests import _netguard  # noqa: F401  (bloquea la red en las pruebas)
    from tests.test_r2 import R2Base
except ImportError:  # unittest discover -s tests
    import _netguard  # noqa: F401
    from test_r2 import R2Base
from oceanic import backup, r2


class Bucket:
    """Cliente simulado de R2 con head/get/put/copy; cualquier borrado lanza un error."""

    def __init__(self):
        self.objects = {}   # key -> {"body", "sha256"}
        self.copies = []
        self.puts = []

    def head_object(self, key):
        o = self.objects.get(key)
        return None if o is None else {"sha256": o["sha256"], "size": len(o["body"])}

    def get_object(self, key):
        o = self.objects.get(key)
        return None if o is None else o["body"]

    def put_object(self, key, body, content_type, cache_control, metadata):
        if key in self.objects:
            raise AssertionError(f"no se debe pisar {key}")
        self.puts.append(key)
        self.objects[key] = {"body": body, "sha256": metadata["sha256"]}

    def copy_object(self, src_key, dst_key):
        self.copies.append((src_key, dst_key))
        self.objects[dst_key] = dict(self.objects[src_key])

    def __getattr__(self, name):
        if name.startswith("delete") or name.startswith("remove"):
            raise AssertionError("borrado prohibido")
        raise AttributeError(name)


class Base(R2Base):
    def setUp(self):
        super().setUp()
        self.md = self.lib.model("marca", "uno", [{"id": "a", "seed": 1}, {"id": "b", "seed": 2}, {"id": "c", "seed": 3}])
        self.cur = self.lib.model("marca", "cur", [{"id": "z", "seed": 9}], curated=True)
        self.bucket = Bucket()
        r2.publish([self.md, self.cur], apply=True, client=self.bucket)   # deja las imagenes "en R2" y sus r2.json
        self.keys = sorted(k for k in self.bucket.objects)
        self.assertEqual(len(self.keys), 4)

    def run_backup(self, **kw):
        return backup.backup([self.md, self.cur], client=self.bucket, version="abc1234", now="2026-10-29T12:00:00Z", **kw)


class BackupTests(Base):
    def test_dry_run_plans_but_writes_nothing(self):
        before = dict(self.bucket.objects)
        rep = self.run_backup()
        self.assertEqual(rep["eligible"], 4)
        self.assertEqual(rep["to_copy"], 4)
        self.assertEqual(rep["copied"], 0)
        self.assertEqual(self.bucket.copies, [])
        self.assertEqual(self.bucket.objects, before)
        self.assertEqual(rep["bytes"], sum(len(self.bucket.objects[k]["body"]) for k in self.keys))

    def test_apply_copies_each_image_server_side_and_writes_the_ledger(self):
        rep = self.run_backup(apply=True)
        self.assertEqual((rep["copied"], rep["ledgers"], rep["errors"]), (4, 4, []))
        self.assertEqual(sorted(s for s, _ in self.bucket.copies), self.keys)       # copia en el servidor, sin descargar
        for k in self.keys:
            self.assertEqual(self.bucket.objects[backup.backup_key(k)]["body"], self.bucket.objects[k]["body"])
            self.assertEqual(self.bucket.objects[backup.backup_key(k)]["sha256"], self.bucket.objects[k]["sha256"])
            self.assertIn(backup.ledger_key(k), self.bucket.objects)

    def test_ledger_has_all_the_fields_and_no_secrets(self):
        self.run_backup(apply=True)
        key = next(k for k in self.keys if "/uno/" in k and k.endswith(".webp") and "-" in k)
        led = json.loads(self.bucket.objects[backup.ledger_key(key)]["body"].decode("utf-8"))
        for field in ("brand", "model", "category", "id", "key", "backup_key", "sha256", "bytes", "original_sha256",
                      "source_url", "uploaded_at", "backed_up_at", "reason", "agent_version", "ledger_version"):
            self.assertIn(field, led)
        self.assertEqual(led["brand"], "marca")
        self.assertEqual(led["model"], "uno")
        self.assertEqual(led["key"], key)
        self.assertEqual(led["backup_key"], f"recoveryphotos/{key}")
        self.assertEqual(led["sha256"], self.bucket.objects[key]["sha256"])
        self.assertEqual(led["bytes"], len(self.bucket.objects[key]["body"]))
        self.assertEqual((led["backed_up_at"], led["agent_version"]), ("2026-10-29T12:00:00Z", "abc1234"))
        text = json.dumps(led)
        self.assertNotIn("@", text)
        for secret in ("R2_", "endpoint", "cloudflarestorage"):
            self.assertNotIn(secret, text)

    def test_second_run_is_idempotent(self):
        self.run_backup(apply=True)
        copies, puts = len(self.bucket.copies), len(self.bucket.puts)
        rep = self.run_backup(apply=True)
        self.assertEqual((rep["copied"], rep["to_copy"], rep["already"], rep["errors"]), (0, 0, 4, []))
        self.assertEqual((len(self.bucket.copies), len(self.bucket.puts)), (copies, puts))
        self.assertEqual(self.run_backup()["to_copy"], 0)          # el dry-run posterior tambien da 0 por copiar

    def test_an_existing_backup_with_another_sha_is_an_error_and_is_not_overwritten(self):
        k = self.keys[0]
        self.bucket.objects[backup.backup_key(k)] = {"body": b"otra cosa", "sha256": "f" * 64}
        rep = self.run_backup(apply=True)
        self.assertEqual(len(rep["errors"]), 1)
        self.assertIn("otro sha256", rep["errors"][0])
        self.assertEqual(self.bucket.objects[backup.backup_key(k)]["body"], b"otra cosa")   # intacto
        self.assertEqual(rep["copied"], 3)

    def test_partial_backup_is_completed_without_touching_what_exists(self):
        k = self.keys[0]
        self.bucket.objects[backup.backup_key(k)] = dict(self.bucket.objects[k])   # copia sin libreta (corte a mitad)
        rep = self.run_backup(apply=True)
        self.assertEqual(rep["errors"], [])
        self.assertIn(backup.ledger_key(k), self.bucket.objects)
        self.assertNotIn((k, backup.backup_key(k)), self.bucket.copies)

    def test_missing_or_different_original_is_an_error(self):
        gone = self.keys[0]
        other = self.keys[1]
        del self.bucket.objects[gone]
        self.bucket.objects[other]["sha256"] = "e" * 64
        rep = self.run_backup(apply=True)
        self.assertEqual(len(rep["errors"]), 2)
        self.assertEqual(rep["copied"], 2)

    def test_verify_is_read_only_and_checks_existence_and_size(self):
        self.run_backup(apply=True)
        copies, puts = len(self.bucket.copies), len(self.bucket.puts)
        ok = self.run_backup(verify=True)
        self.assertEqual((ok["verified"], ok["errors"]), (4, []))
        k = self.keys[0]
        self.bucket.objects[backup.backup_key(k)]["body"] += b"x"     # distinto tamano
        del self.bucket.objects[backup.ledger_key(self.keys[1])]      # sin libreta
        bad = self.run_backup(verify=True)
        self.assertEqual((bad["verified"], len(bad["errors"])), (2, 2))
        self.assertEqual((len(self.bucket.copies), len(self.bucket.puts)), (copies, puts))

    def test_never_deletes(self):
        self.run_backup(apply=True)
        self.run_backup(apply=True)
        self.run_backup(verify=True)
        backup.restore(self.keys, apply=True, client=self.bucket)

    def test_source_has_no_delete_word_and_client_only_adds_a_copy(self):
        self.assertNotIn("delete", Path(backup.__file__).read_text(encoding="utf-8").lower())
        self.assertNotIn("delete", Path(r2.__file__).read_text(encoding="utf-8").lower())
        public = {n for n in dir(r2.R2Client) if not n.startswith("_")}
        self.assertEqual(public, {"head_object", "get_object", "put_object", "copy_object"})

    def test_cli_needs_credentials_and_never_prints_them(self):
        buf = io.StringIO()
        with mock.patch.dict(os.environ, {}, clear=True), contextlib.redirect_stdout(buf):
            rc = backup.main_backup(["--all"], self.lib.root)
        self.assertEqual(rc, 2)
        self.assertIn("faltan variables", buf.getvalue())


class RestoreTests(Base):
    def setUp(self):
        super().setUp()
        self.run_backup(apply=True)

    def test_restore_recovers_a_lost_image_and_verifies_it(self):
        k = self.keys[0]
        original = self.bucket.objects.pop(k)
        rep = backup.restore([k], apply=True, client=self.bucket)
        self.assertEqual((rep["restored"], rep["errors"]), (1, []))
        self.assertEqual(self.bucket.objects[k]["body"], original["body"])
        self.assertEqual(self.bucket.objects[k]["sha256"], original["sha256"])

    def test_restore_dry_run_writes_nothing(self):
        k = self.keys[0]
        self.bucket.objects.pop(k)
        n = len(self.bucket.copies)
        rep = backup.restore([k], client=self.bucket)
        self.assertEqual((rep["planned"], rep["restored"]), (1, 0))
        self.assertNotIn(k, self.bucket.objects)
        self.assertEqual(len(self.bucket.copies), n)

    def test_restore_skips_what_is_already_in_place(self):
        rep = backup.restore(self.keys, apply=True, client=self.bucket)
        self.assertEqual((rep["already"], rep["restored"], rep["errors"], rep["needs_force"]), (4, 0, [], []))

    def test_restore_does_not_overwrite_a_different_original_without_force(self):
        k = self.keys[0]
        self.bucket.objects[k] = {"body": b"cambiada", "sha256": "d" * 64}
        rep = backup.restore([k], apply=True, client=self.bucket)
        self.assertEqual(rep["needs_force"], [k])
        self.assertEqual(rep["restored"], 0)
        self.assertEqual(self.bucket.objects[k]["body"], b"cambiada")          # sin tocar
        forced = backup.restore([k], apply=True, force={k}, client=self.bucket)
        self.assertEqual((forced["restored"], forced["errors"]), (1, []))
        self.assertNotEqual(self.bucket.objects[k]["body"], b"cambiada")

    def test_restore_rejects_a_backup_whose_hash_differs_from_the_ledger(self):
        k = self.keys[0]
        self.bucket.objects.pop(k)
        self.bucket.objects[backup.backup_key(k)]["body"] = b"corrupto"        # el respaldo ya no coincide con la libreta
        rep = backup.restore([k], apply=True, client=self.bucket)
        self.assertEqual(rep["restored"], 0)
        self.assertIn("libreta", rep["errors"][0])
        self.assertNotIn(k, self.bucket.objects)

    def test_restore_without_ledger_or_backup_is_an_error(self):
        k = self.keys[0]
        del self.bucket.objects[backup.ledger_key(k)]
        self.assertIn("libreta", backup.restore([k], apply=True, client=self.bucket)["errors"][0])
        k2 = self.keys[1]
        del self.bucket.objects[backup.backup_key(k2)]
        self.assertIn("respaldo", backup.restore([k2], apply=True, client=self.bucket)["errors"][0])


if __name__ == "__main__":
    unittest.main()
