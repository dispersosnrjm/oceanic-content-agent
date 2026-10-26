import json
import unittest
from pathlib import Path

try:
    from tests import _netguard  # noqa: F401  (bloquea la red en las pruebas)
except ImportError:  # unittest discover -s tests
    import _netguard  # noqa: F401
from oceanic import media, readiness, rsc, specs

PILOT = Path(__file__).resolve().parents[2] / "biblioteca" / "axopar" / "axopar-37-xc-cross-cabin"


def _record(value, sid="S1"):
    return {"source_id": sid, "source_name": "x", "url": "u", "accessed_at": "2026-01-01",
            "source_field": "f", "source_value": str(value), "source_unit": "l",
            "normalized_value": value, "normalized_unit": "l", "model_year": "2027"}


def _spec(fields):
    return {"model": {"boat_type": "vela"}, "fields": fields}


class TextRows(unittest.TestCase):
    def test_back_to_back_text_rows(self):
        # Text rows are not newline-terminated; the second one follows the first directly.
        # Lengths are UTF-8 bytes: "héllo" is 6 bytes.
        payload = '1:["$a"]\n33:T6,héllo34:T3,abc2:{"k":1}\n'
        rows = rsc.text_rows(payload)
        self.assertEqual(rows["$33"], "héllo")
        self.assertEqual(rows["$34"], "abc")

    def test_resolve_refs(self):
        self.assertEqual(rsc.resolve_refs({"d": "$34"}, {"$34": "abc"}), {"d": "abc"})


class Validation(unittest.TestCase):
    def test_verified_with_different_values_is_rejected(self):
        spec = _spec([{"oceanic_field": "capacidad_combustible", "label": "c", "status": "VERIFIED",
                       "display_value": "722 l", "records": [_record(722), _record(730, "S2")]}])
        self.assertTrue(any("debe ser CONFLICT" in e for e in specs.validate(spec)))

    def test_conflict_needs_two_records(self):
        spec = _spec([{"oceanic_field": "capacidad_combustible", "label": "c", "status": "CONFLICT",
                       "display_value": None, "records": [_record(722)]}])
        self.assertTrue(any("al menos dos" in e for e in specs.validate(spec)))

    def test_not_found_cannot_carry_values(self):
        spec = _spec([{"oceanic_field": "lastre", "label": "l", "status": "NOT_FOUND",
                       "display_value": None, "records": [_record(1)]}])
        self.assertTrue(any("NOT_FOUND" in e for e in specs.validate(spec)))

    def test_missing_critical_field_is_reported(self):
        errors = specs.validate(_spec([]))
        self.assertTrue(any("superficie_velica" in e for e in errors))


class DataSources(unittest.TestCase):
    def test_non_web_source_cannot_feed_data(self):
        import tempfile, shutil
        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "m"
            shutil.copytree(PILOT, model)
            spec_path = model / "02_ESPECIFICACIONES" / "specifications.json"
            spec = json.loads(spec_path.read_text())
            spec["fields"][0]["records"].append(dict(spec["fields"][0]["records"][0], source_id="S2"))
            spec_path.write_text(json.dumps(spec))
            self.assertTrue(any("no es web oficial" in e for e in specs.render(model)))


class WebCopy(unittest.TestCase):
    def test_web_copy_is_webp_and_capped(self):
        import io
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (5000, 3000), "navy").save(buf, "JPEG")
        data, w, h = media._web_copy(buf.getvalue(), 1920)
        self.assertEqual((w, h), (1920, 1152))
        self.assertEqual(Image.open(io.BytesIO(data)).format, "WEBP")

    def test_pilot_stores_no_originals(self):
        files = [p for p in PILOT.rglob("*") if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".pdf")]
        self.assertEqual(files, [])


class AxoparBuilder(unittest.TestCase):
    BRAND = PILOT.parent

    def spec(self, slug):
        return {f["oceanic_field"]: f for f in json.loads(
            (self.BRAND / slug / "02_ESPECIFICACIONES" / "specifications.json").read_text())["fields"]}

    def test_mislabeled_fuel_capacity_is_not_published_as_capacity(self):
        f = self.spec("axopar-38-xc-cross-cabin")
        self.assertEqual(f["capacidad_combustible"]["display_value"], "830 l")
        self.assertIn("error de etiquetado", f["capacidad_combustible"]["notes"])

    def test_tech_spec_prevails_over_engine_options(self):
        f = self.spec("axopar-38-xc-cross-cabin")
        self.assertEqual(f["potencia_motor_maxima"]["display_value"], "2 x 350 hp")
        self.assertIn("425", f["potencia_motor_maxima"]["notes"])

    def test_electric_uses_battery_and_declared_power(self):
        f = self.spec("ax-e-25")
        self.assertEqual(f["capacidad_bateria"]["display_value"], "2 x 63 kWh")
        self.assertEqual(f["eslora_total"]["status"], "NOT_FOUND")

    def test_builder_does_not_touch_curated_package(self):
        from oceanic.builders import axopar
        with self.assertRaises(SystemExit):
            axopar.build("axopar-37-xc-cross-cabin", {}, Path("/nonexistent"))


class Dash(unittest.TestCase):
    def test_not_found_dash_is_shown_in_table(self):
        spec = {"model": {"model": "m", "model_year": "1", "variant": "v", "configuration": "c",
                          "engine_option": "e", "boat_type": "motor"},
                "fields": [{"oceanic_field": "velocidad_crucero", "label": "Velocidad crucero",
                            "status": "NOT_FOUND", "display_value": "-", "records": [], "notes": ""}]}
        self.assertIn("| Velocidad crucero | - | NOT_FOUND |", specs.render_table(spec))


class Pilot(unittest.TestCase):
    def test_pilot_package_passes_rules(self):
        self.assertEqual(specs.render(PILOT), [])

    def test_pilot_is_not_green(self):
        # Missing critical fields and pending downloads must keep the pilot out of GREEN.
        result = readiness.evaluate(PILOT)
        self.assertEqual(result["CONTENT_STATUS"], "YELLOW")

    def test_base_table_is_complete(self):
        spec = json.loads((PILOT / "02_ESPECIFICACIONES" / "specifications.json").read_text())
        status = {f["oceanic_field"]: f["status"] for f in spec["fields"]}
        for key in specs.BASE_TABLE["motor"]:
            self.assertEqual(status.get(key), "VERIFIED", key)

    def test_missing_base_field_is_reported(self):
        spec = {"model": {"boat_type": "motor"}, "fields": []}
        self.assertTrue(any("tabla base desplazamiento" in e for e in specs.validate(spec)))

    def test_tech_spec_block_prevails(self):
        spec = json.loads((PILOT / "02_ESPECIFICACIONES" / "specifications.json").read_text())
        fuel = next(f for f in spec["fields"] if f["oceanic_field"] == "capacidad_combustible")
        self.assertEqual((fuel["status"], fuel["display_value"]), ("VERIFIED", "722 l"))
        self.assertEqual([r["source_value"] for r in fuel["records"]], ["722 l (191 gal)"])

    def test_no_other_model_image_marked_this_model(self):
        inv = json.loads((PILOT / "05_MULTIMEDIA" / "IMAGENES" / "images.json").read_text())
        for rec in inv["images"]:
            if "ax37st" in rec["dam_tags"] or "sun-top" in rec["id"]:
                self.assertNotEqual(rec["scope"], "THIS_MODEL", rec["id"])


if __name__ == "__main__":
    unittest.main()


class Beneteau(unittest.TestCase):
    HTML = """<html><body>
    <section class="o-section-wrapper o-section-hero"><ol>
      <li itemprop="itemListElement"><a href="/"><span itemprop="name">Homepage</span></a></li>
      <li itemprop="itemListElement"><a href="/sailing-yachts"><span itemprop="name">Sailboats</span></a></li>
      <li itemprop="itemListElement"><a href="/sailing-yachts/oceanis"><span itemprop="name">Oceanis</span></a></li>
      <li itemprop="itemListElement"><span itemprop="name">Oceanis 99</span></li></ol>
      <img src="https://www.beneteau.com/sites/default/files/styles/article_main_desktop/public/2026-06/oc99-header.jpg.webp?itok=x" width="3840" height="1100">
      <h1>Oceanis 99</h1><div>Tagline</div><p> From 1 000 € (VAT excluded) </p></section>
    <div class="o-section-wrapper o-section-product-description"><p>Intro text.</p>
      <p><strong>Naval Architect:</strong> Studio A<br><strong>Interior Designer:</strong> Studio B</p></div>
    <div class="o-section-wrapper o-section-attributes"><h2>Specifications</h2>
      <div class="o-attributes-item"><p class="o-title">Length Overall</p><p class="o-desc">42'2"</p><p class="o-desc">12.86 m</p></div>
      <div class="o-attributes-item"><p class="o-title">Lightship Displacement</p><p class="o-desc">11,020 lbs</p><p class="o-desc">21700 kg</p></div>
      <div class="o-attributes-item"><p class="o-title">Cabin Number</p><p class="o-desc">2-4</p></div></div>
    </body></html>"""

    def test_adapter_and_unit_conflict(self):
        from oceanic.adapters import beneteau as adapter
        from oceanic.builders import beneteau as builder
        ext = adapter.extract(self.HTML, "https://www.beneteau.com/oceanis/oceanis-99", "2026-09-27")
        self.assertEqual(ext["page_title"], "Oceanis 99")
        self.assertEqual([c["value"] for c in ext["credits"]], ["Studio A", "Studio B"])
        self.assertEqual(ext["images"][0]["original"],
                         "https://www.beneteau.com/sites/default/files/2026-06/oc99-header.jpg")
        ident = builder.identity(ext)
        self.assertEqual((ident["slug"], ident["boat_type"]), ("oceanis-99", "vela"))
        spec = builder.build_specs(ext, ident, {"title": "t", "url": "u", "accessed_at": "d"})
        by = {f["oceanic_field"]: f for f in spec["fields"]}
        self.assertEqual(by["eslora_total"]["status"], "VERIFIED")
        self.assertEqual(by["desplazamiento"]["status"], "VERIFIED")   # metric prevails over 11,020 lbs
        self.assertIn("error de la web", by["desplazamiento"]["notes"])
        self.assertEqual(by["camarotes"]["display_value"], "2 a 4")
        self.assertEqual(by["superficie_velica"]["status"], "NOT_FOUND")
        self.assertEqual(specs.validate(spec), [])


class Lagoon(unittest.TestCase):
    HTML = """<html><body><main>
    <img src="https://admin.catamarans-lagoon.com/sites/default/files/2024-09/cover-lagoon-99.jpg" width="1920" height="1080">
    <h1>Lagoon 99</h1><h2>A Tagline</h2>
    <div class="lead-1">The Lagoon 99 is a long enough description of the boat for the introduction block.</div>
    <img src="https://admin.catamarans-lagoon.com/sites/default/files/2024-10/slider-lagoon-99-01.jpg" width="1920" height="720">
    <h3>A Highlight</h3><p>Highlight text.</p>
    <h2>Specifications</h2><div><ul>
      <li><span>Length overall*</span> <span>13.85m / 45'5''ft</span></li>
      <li><span>Beam overall</span> <span>7.69m / 25'3'ft</span></li>
      <li><span>Light displacement (EEC)</span> <span>13,9 t / 30 848 Lbs</span></li>
      <li><span>Water tank capacity</span> <span>300 L / 159 US GAL</span></li>
      <li><span>Motorisation - standard</span> <span>2 x 57 CV / HP</span></li>
      <li><span>CE approval</span> <span>A : 12 - B : 14 - C : 20 - D : 30</span></li>
      <li><span>Upwind sail area</span> <span>105 m² / 1,130 sq.ft</span></li>
    </ul></div></main>
    <script>window.__NUXT__=(function(){return {x:[{name:"3 cabins",parent:a,image:{type:n,id:"1",meta:{drupal_internal__target_id:1,url:"https:\\u002F\\u002Fadmin.catamarans-lagoon.com\\u002Fsites\\u002Fdefault\\u002Ffiles\\u002Fl99-3c.jpg",alt:"x"}}},{name:"4 cabins",parent:a,image:{type:n,id:"2",meta:{drupal_internal__target_id:2,url:"https:\\u002F\\u002Fadmin.catamarans-lagoon.com\\u002Fsites\\u002Fdefault\\u002Ffiles\\u002Fl99-4c.jpg",alt:"x"}}}]}}())</script>
    </body></html>"""

    def test_lagoon_specs(self):
        from oceanic.adapters import lagoon as adapter
        from oceanic.builders import lagoon as builder, beneteau
        ext = adapter.extract(self.HTML, "https://www.catamarans-lagoon.com/boats/lagoon-99", "2026-09-30")
        self.assertEqual([l["title"] for l in ext["layouts"]], ["3 cabins", "4 cabins"])
        ident = builder._with_cfg(beneteau.identity, ext)
        self.assertEqual(ident["boat_type"], "catamaran_vela")
        spec = builder.build_specs(ext, ident, {"title": "t", "url": "u", "accessed_at": "d"})
        by = {f["oceanic_field"]: f for f in spec["fields"]}
        self.assertEqual(by["desplazamiento"]["display_value"], "13.900 kg")
        self.assertEqual(by["capacidad_agua_dulce"]["display_value"], "300 l")   # metric prevails over 159 US gal
        self.assertEqual(by["certificacion"]["display_value"], "A12 / B14 / C20 / D30")
        self.assertEqual(by["superficie_velica"]["display_value"], "105 m²")
        self.assertEqual(by["potencia_motor_auxiliar"]["display_value"], "2 x 57 hp")
        self.assertEqual(by["camarotes"]["display_value"], "3 / 4")
        self.assertEqual(specs.validate(spec), [])


class XO(unittest.TestCase):
    HTML = """<html><body><h1>DFNDR 9</h1><p>The award winning DFNDR 9 is a true pathfinder.</p>
    <h2>Ready for a Rough Ride</h2><p>Inside the boat you’ll find a cozy cabin, where you can rest inbetween destinations.</p>
    <p>The aft deck can be a sunbed while private head and berths for two enable comfortable overnight stay.</p>
    <img data-guid="https://xoboats.com/wp-content/uploads/2022/02/XO_Boats-DFNDR9-2b.jpg" src="x-uai-720x480.jpg">
    <img src="https://xoboats.com/wp-content/uploads/2022/02/XO_Boats-EXPLR9-0707-300x200.jpg">
    <table class="woocommerce-product-attributes shop_attributes">
    <tr><th>Overall Lenght (exc. engine)</th><td><p>8,8m</p></td></tr><tr><th>Beam</th><td><p>2,6m</p></td></tr>
    <tr><th>Weight (excl. engine)</th><td><p>2710kg</p></td></tr><tr><th>Passengers</th><td><p>8/6</p></td></tr>
    <tr><th>Fuel capacity</th><td><p>450</p></td></tr><tr><th>Classification</th><td><p>B/C</p></td></tr>
    <tr><th>Outboard engines</th><td><p>2 x 225 – 450 hp</p></td></tr></table>
    <p>XO Boats Oy Ludviginkatu</p></body></html>"""

    def test_xo_specs_and_scope(self):
        from oceanic.adapters import xo as adapter
        from oceanic.builders import xo as builder, beneteau
        ext = adapter.extract(self.HTML, "https://xoboats.com/xo-fleet/dfndr-9/", "2026-09-30")
        self.assertEqual(ext["slug"], "xo-dfndr-9")
        self.assertEqual(adapter.original_url("https://xoboats.com/wp-content/uploads/a/B-uai-720x480.jpg"),
                         "https://xoboats.com/wp-content/uploads/a/B.jpg")
        builder.prepare([ext, dict(ext, page_title="XO EXPLR 9", slug="xo-explr-9", images=[])])
        ident = builder._with_cfg(beneteau.identity, ext)
        spec = builder.build_specs(ext, ident, {"title": "t", "url": "u", "accessed_at": "d"})
        by = {f["oceanic_field"]: f for f in spec["fields"]}
        self.assertEqual(by["eslora_total"]["display_value"], "8,8 m")
        self.assertEqual(by["desplazamiento"]["display_value"], "2.710 kg")
        self.assertEqual(by["capacidad_combustible"]["display_value"], "450 l")        # unit missing on the web
        self.assertEqual(by["certificacion"]["status"], "REQUIRES_REVIEW")           # B8 / C6: fewer people in C
        self.assertEqual(by["potencia_motor_maxima"]["display_value"], "450 hp (2 x 225 hp)")
        self.assertEqual(by["camarotes"]["display_value"], "1")                       # text cross-reference
        self.assertEqual(specs.validate(spec), [])
        imgs = builder._with_cfg(beneteau.classify_images, ext, ident)["images"]
        self.assertEqual({i["id"]: i["scope"] for i in imgs},
                         {"xo-boats-dfndr9-2b": "THIS_MODEL", "xo-boats-explr9-0707": "OTHER_MODEL"})


class SolarisSaffier(unittest.TestCase):
    def test_solaris_number_parsing(self):
        from oceanic.builders import solaris
        self.assertEqual(solaris._num("Kg 9.850", "kg"), 9850)
        self.assertEqual(solaris._num("9,400 kg", "kg"), 9400)
        self.assertEqual(solaris._num("Kg 46,0 light", "kg"), 46.0)      # flagged by plausibility, not converted
        self.assertEqual(solaris._num("M2 100 - std", "m2"), 100)
        self.assertEqual(solaris._num("M 22.OO", "m"), 22.0)
        self.assertEqual(solaris._num("M 16,80", "m"), 16.8)

    def test_saffier_specs(self):
        from oceanic.adapters import saffier as adapter
        from oceanic.builders import saffier as builder, beneteau
        html = """<html><head><title>Saffier SE 99 Test | Saffier Yachts</title></head><body>
        <section id="intro"><h2>Sail</h2><div class="content-holder"><p>A daysailer.</p></div></section>
        <section id="specifications"><div class="specs-group"><div class="group-title">Dimensions</div>
        <div class="spec-item"><div class="item-label">Length (without bowsprit)</div><div class="item-value">9.85m</div></div>
        <div class="spec-item"><div class="item-label">L.O.A. (with bowsprit)</div><div class="item-value">11m</div></div>
        <div class="spec-item"><div class="item-label">Draft shallow keel</div><div class="item-value">1.45m</div></div>
        <div class="spec-item"><div class="item-label">Draft standard keel</div><div class="item-value">1.70m</div></div>
        <div class="spec-item"><div class="item-label">CE-category</div><div class="item-value">C | B</div></div></div>
        <div class="specs-group"><div class="group-title">Engine</div>
        <div class="spec-item"><div class="item-label">Engine power (Std.)</div><div class="item-value">15 HP</div></div>
        <div class="spec-item"><div class="item-label">Engine power (Opt.)</div><div class="item-value">10 kW</div></div></div>
        </section></body></html>"""
        ext = adapter.extract(html, "https://saffieryachts.com/models/saffier-se-99-test/", "2026-10-01")
        self.assertEqual(ext["page_title"], "Saffier SE 99 Test")
        builder.prepare([ext])
        ident = builder._with_cfg(beneteau.identity, ext)
        spec = builder.build_specs(ext, ident, {"title": "t", "url": "u", "accessed_at": "d"})
        by = {f["oceanic_field"]: f for f in spec["fields"]}
        self.assertEqual(by["eslora_total"]["display_value"], "11 m")
        self.assertEqual(by["eslora_casco"]["display_value"], "9,85 m")
        self.assertEqual(by["calado"]["display_value"], "1,70 m")
        self.assertEqual(by["certificacion"]["display_value"], "C / B")
        self.assertEqual(by["superficie_velica"]["status"], "NOT_FOUND")     # no sums
        self.assertEqual(by["potencia_motor_auxiliar"]["display_value"], "15 hp")   # 10 kW ≈ 13,4 hp < 15 HP
        self.assertEqual(specs.validate(spec), [])


class Excess(unittest.TestCase):
    def test_excess_specs(self):
        from oceanic.adapters import excess as adapter
        from oceanic.builders import excess as builder, beneteau
        item = ('<div class="feature-item"><h3>{g}</h3><div class="content">{rows}</div></div>')
        row = '<p><span class="text-primary">{l}</span></p><p><span>{v}</span></p>'
        rows = lambda pairs: "".join(row.format(l=l, v=v) for l, v in pairs)
        html = f"""<html><body><main>
        <section class="banner"><h1><div class="title">Excess 99</div><div class="subtitle">A TEST BOAT</div></h1></section>
        <section class="summary"><div class="text">The Excess 99 is a test.</div></section>
        <section class="plans" id="layout"><ul><li class="plan-item"><a class="big-picture" data-title="3 CABIN"
          href="https://www.excess-catamarans.com/media/2020/10/1-excess-99-3c.jpg">x</a></li>
          <li class="plan-item"><a class="big-picture" data-title="4 CABIN"
          href="https://www.excess-catamarans.com/media/2020/10/2-excess-99-4c.jpg">x</a></li></ul></section>
        <section id="features">
        {item.format(g="Sails", rows=rows([("Upwind sail area", "90m² | 969 sq ft")]))}
        {item.format(g="Dimensions", rows=rows([("Length overall", "11.42 m | 37’6’’"), ("Beam", "6,59 m | 21'7''"),
            ("Mast clearance [std./Pulse Line]", "19.05 m | 62’6’’ / 20.15 m | 66’1’’"),
            ("Light displacement [EEC]", "9,000 kg | 19,845 lbs")]))}
        {item.format(g="Equipment", rows=rows([("Fuel capacity", "2 x 200 L | 2 x 53 US Gal"),
            ("Engines", "2 x 29 HP"), ("EC Certification", "A: 8 - B: 12 - C: 16 - D: 20")]))}
        </section></main></body></html>"""
        ext = adapter.extract(html, "https://www.excess-catamarans.com/our-catamarans/excess-99", "2026-10-05")
        self.assertEqual((ext["page_title"], ext["tagline"]), ("Excess 99", "A test boat"))
        builder.prepare([ext])
        ident = builder._with_cfg(beneteau.identity, ext)
        spec = builder.build_specs(ext, ident, {"title": "t", "url": "u", "accessed_at": "d"})
        by = {f["oceanic_field"]: f for f in spec["fields"]}
        self.assertEqual(by["desplazamiento"]["display_value"], "9.000 kg")          # comma = thousands
        self.assertEqual(by["manga_casco"]["display_value"], "6,59 m")               # comma = decimal
        self.assertEqual(by["capacidad_combustible"]["display_value"], "400 L (2 x 200 L)")
        self.assertEqual(by["altura_linea_flotacion"]["display_value"], "19,05 m")   # standard rig
        self.assertEqual(by["camarotes"]["display_value"], "3 / 4")
        self.assertEqual(by["certificacion"]["display_value"], "A8 / B12 / C16 / D20")
        self.assertEqual(by["potencia_motor_auxiliar"]["display_value"], "2 x 29 hp")
        self.assertEqual(specs.validate(spec), [])


class Optimize(unittest.TestCase):
    def test_optimize_resizes_and_is_idempotent(self):
        import io
        import tempfile
        from PIL import Image
        with tempfile.TemporaryDirectory() as tmp:
            m = Path(tmp)
            img_dir = m / "05_MULTIMEDIA" / "IMAGENES" / "EXTERIOR"
            img_dir.mkdir(parents=True)
            buf = io.BytesIO()
            Image.new("RGB", (3000, 2000), (20, 90, 160)).save(buf, "WEBP", quality=90)
            (img_dir / "a.webp").write_bytes(buf.getvalue())
            inv = {"images": [{"id": "a", "file": "05_MULTIMEDIA/IMAGENES/EXTERIOR/a.webp", "hero_candidate": False,
                               "width": 3000, "height": 2000}]}
            (m / "05_MULTIMEDIA" / "IMAGENES" / "images.json").write_text(json.dumps(inv))
            st = media.optimize_images(m)
            rec = json.loads((m / "05_MULTIMEDIA" / "IMAGENES" / "images.json").read_text())["images"][0]
            self.assertEqual((st["optimized"], rec["width"], rec["web_quality"]), (1, 1600, media.WEB_QUALITY))
            self.assertEqual(media.optimize_images(m)["optimized"], 0)
