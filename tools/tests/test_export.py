import copy
import json
import re
import tempfile
import unittest
from pathlib import Path

from oceanic import exporter, mediarules

ROOT = Path(__file__).resolve().parents[2]
GEN = "2026-10-19T12:00:00Z"


def _load(path):
    return json.loads(path.read_text(encoding="utf-8"))


class CleanText(unittest.TestCase):
    def test_removes_internal_marks_and_quotes(self):
        block = ("\n> Candidato. Estado: **BORRADOR, requiere revisión editorial**.\n\n"
                 "**Subtítulo**\n\n> Primer párrafo con cita.\n> Segunda línea.\n\n- punto\n"
                 "> Nota para la web: confirmar la cifra.\n> segunda línea de la nota\n\nFin.")
        r = exporter.clean_text(block)
        self.assertTrue(r["draft_marker_removed"])
        self.assertEqual(r["internal_notes_removed"], 1)
        self.assertNotIn(">", r["text"])
        self.assertNotIn("BORRADOR", r["text"])
        self.assertNotIn("Nota para la web", r["text"])
        self.assertTrue(r["text"].startswith("**Subtítulo**"))
        self.assertIn("Primer párrafo con cita.\nSegunda línea.", r["text"])

    def test_pending_placeholder_is_null(self):
        r = exporter.clean_text("\nPENDIENTE: sin borrador redactado.\n")
        self.assertIsNone(r["text"])

    def test_internal_notes_counted_and_flagged(self):
        block = ("> Axopar declara hasta 30 % menos consumo. *(Velocidad crucero en la tabla: '-'.)*\n"
                 "> Unas 75 millas más que el modelo anterior. *(No publicar una cifra calculada.)*")
        r = exporter.clean_text(block)
        self.assertEqual(r["internal_notes_removed"], 2)
        self.assertNotIn("*(", r["text"])
        self.assertEqual([n[2] for n in r["notes"]], [False, True])

    def test_no_text_starts_with_quote(self):
        r = exporter.clean_text("> Texto citado\n\n> otro")
        self.assertFalse(r["text"].lstrip().startswith(">"))


class Hash(unittest.TestCase):
    def test_hash_ignores_times_and_changes_with_content(self):
        a = {"generated_at": "x", "identity": {"content_hash": None}, "texts": {"es": {"a": "uno"}},
             "specs": [{"source": {"accessed_at": "2026-01-01"}}]}
        b = copy.deepcopy(a)
        b["generated_at"] = "y"
        b["specs"][0]["source"]["accessed_at"] = "2026-02-02"
        self.assertEqual(exporter.model_content_hash(a), exporter.model_content_hash(b))
        b["texts"]["es"]["a"] = "dos"
        self.assertNotEqual(exporter.model_content_hash(a), exporter.model_content_hash(b))


class FullExport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.res = exporter.export_portal(ROOT, preview_keys=True, generated_at=GEN, write=False)
        cls.docs = cls.res["docs"]

    def test_all_models_and_brands(self):
        self.assertEqual(len(self.docs), len(list((ROOT / "biblioteca").glob("*/*/00_MODELO/00_MODELO.md"))))
        self.assertEqual(self.res["index"]["models"].__len__(), len(self.docs))
        self.assertEqual(len(self.res["brands"]), len({d["identity"]["brand"]["slug"] for d in self.docs.values()}))
        states = {d["readiness"]["content_status"] for d in self.docs.values()}
        self.assertIn("RED", states)  # los RED se exportan tambien

    def test_texts_are_clean(self):
        for key, d in self.docs.items():
            for sec, t in d["texts"]["es"].items():
                x = t["text"]
                if x is None:
                    continue
                self.assertFalse(x.lstrip().startswith(">"), f"{key}/{sec} empieza con >")
                self.assertIsNone(re.search(r"(^|\n)\s*>", x), f"{key}/{sec} tiene cita")
                self.assertIsNone(re.search(r"BORRADOR|PENDIENTE|Nota para la web|\*\(", x), f"{key}/{sec} marca interna")
                self.assertIsInstance(t["draft_marker_removed"], bool)
                self.assertIsInstance(t["internal_notes_removed"], int)

    def test_model_year_null_not_no_declarado(self):
        nulls = 0
        for d in self.docs.values():
            self.assertNotEqual(d["identity"]["model_year"], "NO DECLARADO")
            nulls += d["identity"]["model_year"] is None
            for s in d["specs"]:
                self.assertNotEqual(s["model_year"], "NO DECLARADO")
        self.assertGreater(nulls, 0)

    def test_images_alt_keys_and_media_rules(self):
        for key, d in self.docs.items():
            for im in d["images"]:
                self.assertEqual(im["alt"]["es"], mediarules.alt_text(d["identity"]["model"], im["category"]))
                self.assertTrue(im["key"].startswith(f"{d['identity']['brand']['slug']}/{d['identity']['slug']}/"))
                self.assertTrue(im["key"].endswith(".webp"))
                self.assertLessEqual(max(im["width"], im["height"]), mediarules.max_edge(im["category"], im["hero_rank"]))

    def test_alt_format_example(self):
        d = self.docs["beneteau/antares-11-coupe"]
        alts = {i["alt"]["es"] for i in d["images"]}
        self.assertTrue(all(a.startswith("Antares 11 Coupe, ") for a in alts))
        self.assertIn("Antares 11 Coupe, exterior", alts)

    def test_videos_rules(self):
        unverified = 0
        for d in self.docs.values():
            for v in d["videos"]:
                self.assertIsNot(v["third_party"], True)
                self.assertTrue(v["host"])
                self.assertIn(v["host"], v["url"])
                if v["third_party"] is None:
                    unverified += 1
                    self.assertEqual(v["review_status"], "unverified")
                    self.assertEqual(v["alert"]["code"], "VIDEO_UNVERIFIED")
                else:
                    self.assertEqual(v["review_status"], "verified")
        self.assertGreater(unverified, 0)
        self.assertTrue(self.res["report"]["third_party_videos"])  # third_party true: listados en el informe

    def test_video_tier_is_auto_with_alert(self):
        for d in self.docs.values():
            for c in d["publish_tier"]["changes"]:
                if c["rule"] == "video-unverified":
                    self.assertEqual(c["tier"], "auto")

    def test_37xc_notes_removed_and_flagged(self):
        d = self.docs["axopar/axopar-37-xc-cross-cabin"]
        perf = d["texts"]["es"]["performance"]
        self.assertEqual(perf["internal_notes_removed"], 2)
        flagged = [c for c in d["publish_tier"]["changes"] if c.get("reason")]
        self.assertEqual([c["target"] for c in flagged], ["text:performance"])
        self.assertTrue(all(c["tier"] == "review" for c in flagged))
        self.assertEqual(self.res["report"]["internal_notes_removed"]["total"], 2)
        self.assertTrue(d["lint_ok"])  # no queda ninguna nota con el patron

    def test_brand_notes_and_lint(self):
        lagoon = self.res["brands"]["lagoon"]
        self.assertGreaterEqual(lagoon["texts"]["es"]["internal_notes_removed"], 1)
        self.assertNotIn("Nota para la web", lagoon["texts"]["es"]["text"])
        self.assertEqual(lagoon["brand_authorization"], "unknown")
        self.assertFalse(self.res["brands"]["beneteau"]["lint_ok"])  # rayas largas en la traduccion
        self.assertTrue(any(v["rule"] == "em-dash" for v in self.res["brands"]["beneteau"]["lint_violations"]))

    def test_lint_stats_by_brand_and_rule(self):
        lint = self.res["report"]["lint"]
        self.assertEqual(lint["total"], sum(lint["by_rule"].values()))
        self.assertGreater(lint["by_rule"]["em-dash"], 0)
        self.assertNotIn("internal-note-residue", lint["by_rule"])  # las notas del traductor se quitan en el export
        by_brand = lint["by_brand"]
        self.assertEqual(set(by_brand), set(self.res["brands"]))
        for brand, v in by_brand.items():
            self.assertEqual(v["model_violations"] + v["brand_text_violations"], sum(v["by_rule"].values()), brand)
        self.assertGreater(by_brand["beneteau"]["brand_text_violations"], 0)
        self.assertEqual(by_brand["axopar"]["by_rule"], {})
        self.assertEqual(sum(v["model_violations"] + v["brand_text_violations"] for v in by_brand.values()), lint["total"])

    def test_brand_translator_note_removed(self):
        beneteau = self.res["brands"]["beneteau"]["texts"]["es"]
        self.assertNotIn("*(Nota:", beneteau["text"])
        self.assertGreaterEqual(beneteau["internal_notes_removed"], 1)

    def test_long_ids_recovered_and_hero_candidate_back(self):
        d = self.docs["axopar/axopar-38-cross-top"]
        long_ids = [i["id"] for i in d["images"] if len(i["id"]) > 80]
        self.assertTrue(long_ids)
        self.assertTrue(any(h["available"] for h in d["candidates"]["hero_images"]))
        self.assertNotIn("axopar/axopar-38-cross-top", self.res["report"]["models_without_hero_candidate"])
        om = self.res["report"]["images"]["omitted"]
        self.assertNotIn("id_invalido", om)
        self.assertNotIn("portada_candidata_id_invalido", om)

    def test_preview_flag_in_index_and_report(self):
        self.assertIs(self.res["index"]["preview"], True)
        self.assertIs(self.res["report"]["preview"], True)
        real = exporter.export_portal(ROOT, generated_at=GEN, only="switch/switch-one-design", write=False)
        self.assertIs(real["index"]["preview"], False)
        self.assertIs(real["report"]["preview"], False)

    def test_catalog_hash_recomputable(self):
        body = json.loads(json.dumps(self.res["index"]))
        body["catalog_hash"] = None
        self.assertEqual(exporter.canonical_hash(body), self.res["index"]["catalog_hash"])

    def test_report_counts_reconcile(self):
        r = self.res["report"]
        self.assertEqual(r["models"]["total"], len(self.docs))
        self.assertEqual(r["images"]["published"], sum(len(d["images"]) for d in self.docs.values()))
        self.assertEqual(sum(r["publish_tier"].values()), len(self.docs))

    def test_deterministic(self):
        again = exporter.export_portal(ROOT, preview_keys=True, generated_at="2030-01-01T00:00:00Z", write=False)
        self.assertEqual(again["index"]["catalog_hash"], self.res["index"]["catalog_hash"])
        for k, d in self.docs.items():
            self.assertEqual(again["docs"][k]["identity"]["content_hash"], d["identity"]["content_hash"])


class WriteOutput(unittest.TestCase):
    def test_writes_files_and_validates_examples(self):
        with tempfile.TemporaryDirectory() as tmp:
            res = exporter.export_portal(ROOT, Path(tmp), preview_keys=True, generated_at=GEN,
                                         only="switch/switch-one-design")
            out = Path(tmp)
            self.assertTrue((out / "index.json").exists())
            self.assertTrue((out / "export-report.json").exists())
            self.assertTrue((out / "models/switch/switch-one-design/portal.json").exists())
            self.assertTrue((out / "brands/switch/brand.json").exists())
            for name in exporter.SCHEMA_COPIES:
                self.assertTrue((out / "schema" / name).exists())
            self.assertEqual(res["report"]["models"]["total"], 1)

    def test_without_r2_json_and_without_preview_images_are_omitted(self):
        res = exporter.export_portal(ROOT, generated_at=GEN, only="switch/switch-one-design", write=False)
        d = res["docs"]["switch/switch-one-design"]
        self.assertEqual(d["images"], [])
        self.assertTrue(all(not h["available"] for h in d["candidates"]["hero_images"]))
        self.assertGreater(res["report"]["images"]["omitted"].get("sin_subir_a_r2", 0), 0)


class Examples(unittest.TestCase):
    def test_examples_validate(self):
        from jsonschema import Draft202012Validator, FormatChecker
        schema = _load(ROOT / "schema" / "portal.schema.json")
        v = Draft202012Validator(schema, format_checker=FormatChecker())
        for p in sorted((ROOT / "schema" / "examples").glob("portal.*.json")):
            self.assertEqual(list(v.iter_errors(_load(p))), [], p.name)


if __name__ == "__main__":
    unittest.main()
