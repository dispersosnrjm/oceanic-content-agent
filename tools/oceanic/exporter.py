"""Exportacion para el portal (python -m oceanic export-portal).

Lee la biblioteca (solo lectura) y escribe, en una carpeta de salida, un portal.json por modelo, un brand.json
por marca, index.json y export-report.json, validando contra schema/portal.schema.json. No usa red ni
credenciales y no filtra por aprobacion. Contrato: docs/EXPORT_PORTAL.md.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from . import lint as textlint
from . import mediarules

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_VERSION = "1.0.0"
TIME_KEYS = {"generated_at", "accessed_at", "uploaded_at", "collected_at"}
SECTIONS = {"introduccion": "introduccion", "diseno": "diseno", "performance": "performance",
            "ingenieria": "ingenieria", "experiencia_a_bordo": "experiencia-a-bordo"}
# Nota interna cuya frase vecina podria contener una cifra calculada: la seccion queda marcada para revision.
CALC_HINT = re.compile(r"calculad|no publicar", re.I)
SCHEMA_COPIES = ("portal.schema.json", "media-rules.json", "field-catalog.json", "publish-rules.json")


class ExportError(Exception):
    pass


# ----------------------------------------------------------------------------------------------- utilidades

def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _json(path: Path):
    return json.loads(_read(path))


def _write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def _strip_times(obj):
    if isinstance(obj, dict):
        return {k: _strip_times(v) for k, v in obj.items() if k not in TIME_KEYS}
    if isinstance(obj, list):
        return [_strip_times(v) for v in obj]
    return obj


def canonical_hash(obj) -> str:
    """sha256 del JSON canonico (claves ordenadas, UTF-8) sin marcas de tiempo."""
    raw = json.dumps(_strip_times(obj), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def model_content_hash(doc: dict) -> str:
    body = json.loads(json.dumps(doc))
    body["identity"]["content_hash"] = None
    return canonical_hash(body)


def _model_year(value):
    return None if value in (None, "", "NO DECLARADO") else value


# ----------------------------------------------------------------------------------------------- textos

def oceanic_block(md: str) -> str:
    m = re.search(r"^## OCEANIC CONTENT\s*$(.*?)(?=^## |\Z)", md, re.M | re.S)
    return m.group(1) if m else ""


def _drop_note_paragraphs(lines: list[str]) -> tuple[list[str], int]:
    """Quita los parrafos de cita '> Nota para la web: ...' (y sus lineas de continuacion)."""
    out, removed, skipping = [], 0, False
    for line in lines:
        if re.match(r"\s*>\s*Nota para la web", line):
            skipping = True
            removed += 1
            continue
        if skipping and re.match(r"\s*>", line) and line.strip() != ">":
            continue
        skipping = False
        out.append(line)
    return out, removed


def clean_text(block: str) -> dict:
    """Limpia un bloque OCEANIC CONTENT.

    Devuelve {text, draft_marker_removed, internal_notes_removed, notes}: markdown simple sin '>' de cita, sin marcas
    internas (BORRADOR, PENDIENTE, 'Nota para la web', notas *( )*). `notes` = [(nota, frase_vecina, marcar_revision)].
    """
    draft = False
    kept = []
    for line in block.splitlines():
        if re.match(r"\s*>\s*(Candidato|⚠)", line) or re.match(r"\s*⚠", line):
            draft = draft or "BORRADOR" in line
            continue
        kept.append(line)
    kept, web_notes = _drop_note_paragraphs(kept)
    text = "\n".join(re.sub(r"^\s*>+\s?", "", ln) for ln in kept)  # sin '>' de cita
    if "BORRADOR" in text:
        draft = True
        text = "\n".join(ln for ln in text.splitlines() if "BORRADOR" not in ln)
    text = "\n".join(ln for ln in text.splitlines() if not re.search(r"\bPENDIENTE\b", ln))
    text, n_notes, found = textlint.clean_internal_notes(text)
    text = "\n".join(ln.rstrip() for ln in text.splitlines())
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    notes = [(note, neighbor, bool(CALC_HINT.search(note)) or bool(CALC_HINT.search(neighbor)))
             for note, neighbor in found]
    return {"text": text or None, "draft_marker_removed": draft,
            "internal_notes_removed": n_notes + web_notes, "notes": notes}


def parse_sections(pkg: Path) -> dict:
    res = {}
    for key, fname in SECTIONS.items():
        path = pkg / "01_CONTENIDO" / f"{fname}.md"
        if not path.exists():
            res[key] = {"text": None, "draft_marker_removed": False, "internal_notes_removed": 0, "notes": []}
        else:
            res[key] = clean_text(oceanic_block(_read(path)))
    return res


def parse_hero_candidates(pkg: Path) -> tuple[list[str], list[str]]:
    path = pkg / "01_CONTENIDO" / "hero.md"
    if not path.exists():
        return [], []
    block = oceanic_block(_read(path))
    headlines, shorts, mode, buf = [], [], None, []

    def flush():
        if mode == "short" and buf:
            for para in re.split(r"\n\s*\n", "\n".join(buf)):
                txt = " ".join(re.sub(r"^\s*>+\s?", "", ln).strip() for ln in para.splitlines() if ln.strip())
                if txt:
                    shorts.append(txt)
        buf.clear()

    for line in block.splitlines():
        m = re.match(r"\*\*(.+?)\*\*", line.strip())
        if m and not line.strip().startswith(">"):
            flush()
            title = m.group(1).lower()
            mode = "head" if title.startswith("headlines") else "short" if title.startswith("descripción corta") else None
            continue
        if mode == "head":
            hm = re.match(r"\s*\d+\.\s+(.*\S)", line)
            if hm:
                headlines.append(hm.group(1).strip())
        elif mode == "short":
            buf.append(line)
    flush()
    return headlines, shorts


# ----------------------------------------------------------------------------------- caracteristicas / equipamiento

def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _clean_heading(text: str) -> str:
    text = re.sub(r"`", "", text).replace(" · ", " / ").strip()
    return re.sub(r"\s+", " ", text)


def parse_literal_items(path: Path) -> list[dict]:
    """Filas literales del fabricante de un .md de caracteristicas/equipamiento (sin las columnas de candidato ES)."""
    if not path.exists():
        return []
    lines = _read(path).splitlines()
    items, heading, i, in_oceanic, quote = [], "", 0, False, []

    def flush_quote():
        txt = " ".join(quote).strip()
        if txt and heading:
            items.append({"label": heading, "text": txt})
        quote.clear()

    while i < len(lines):
        line = lines[i]
        h = re.match(r"^(#{2,3})\s+(.*)", line)
        if h:
            flush_quote()
            title = _clean_heading(h.group(2))
            if re.match(r"(OCEANIC|Candidato)", title, re.I):  # subtitulo de bloque, no una seccion nueva
                in_oceanic = True
            elif re.match(r"SOURCE", title, re.I):
                in_oceanic = False
            else:
                heading, in_oceanic = title, False
            i += 1
            continue
        if re.match(r"^\*\*(OCEANIC|Candidato)", line.strip()):
            flush_quote()
            in_oceanic = True
        elif re.match(r"^\*\*SOURCE", line.strip()):
            flush_quote()
            in_oceanic = False
        if line.startswith("|") and i + 1 < len(lines) and re.match(r"^\|\s*:?-{3}", lines[i + 1]):
            flush_quote()
            header = _cells(line)
            literal = [c for c, name in enumerate(header)
                       if not re.search(r"candidato|oceanic", name, re.I)]
            numbered = header[0].strip() == "#"
            i += 2
            while i < len(lines) and lines[i].startswith("|"):
                row = _cells(lines[i])
                i += 1
                if len(row) < len(header):
                    continue
                if numbered:
                    cols = [row[c] for c in literal if c != 0]
                    label, text = heading or "Equipamiento", " · ".join(x for x in cols if x)
                elif len(literal) == 1:
                    label, text = heading or "Equipamiento", row[literal[0]]
                else:
                    cols = [row[c] for c in literal]
                    label, text = cols[0], " · ".join(x for x in cols[1:] if x)
                label, text = re.sub(r"[*`]", "", label).strip(), text.strip()
                if text and not re.fullmatch(r"-+", text):
                    items.append({"label": label or "Equipamiento", "text": text})
            continue
        if line.startswith(">") and heading and not in_oceanic:
            body = re.sub(r"^\s*>+\s?", "", line).strip()
            if re.match(r"(Fuente|Candidato|⚠|Nota|Decisi)", body):
                flush_quote()
            elif body:
                quote.append(body.strip('"'))
            else:
                flush_quote()
        elif quote:
            flush_quote()
        i += 1
    flush_quote()
    return items


def _literal_block(items: list[dict], source_url: str | None):
    if not items:
        return None
    return {"origin": "manufacturer_literal", "lang": "en", "source_url": source_url, "items": items}


# ------------------------------------------------------------------------------------------------ r2.json / medios

def _r2_path(pkg: Path, brand: str, slug: str) -> Path:
    model_json = pkg / "00_MODELO" / "model.json"
    curated = model_json.exists() and _json(model_json).get("curated")
    if curated:
        return pkg.parent / "_r2" / f"{slug}.json"
    return pkg / "05_MULTIMEDIA" / "IMAGENES" / "r2.json"


def _load_r2_keys(pkg: Path, brand: str, slug: str) -> dict:
    path = _r2_path(pkg, brand, slug)
    if not path.exists():
        return {}
    return {e["id"]: e["key"] for e in _json(path).get("images", []) if not e.get("superseded")}


def _build_images(pkg, brand, slug, model_name, preview_keys, omitted: Counter, manual: list):
    inv = _json(pkg / "05_MULTIMEDIA" / "IMAGENES" / "images.json")["images"]
    r2 = _load_r2_keys(pkg, brand, slug)
    images, key_source = [], Counter()
    for rec in inv:
        if rec.get("scope") != "THIS_MODEL":
            continue
        status = str(rec.get("download_status") or "")
        if status.startswith("DUPLICATE_OF"):
            omitted["DUPLICATE_OF"] += 1
            continue
        if status == "requires_manual_download" or rec.get("manual_download_url"):
            omitted["requires_manual_download"] += 1
            manual.append({"id": rec["id"], "url": rec.get("manual_download_url") or rec.get("source_url")})
            continue
        if status.startswith("FAILED"):
            omitted["FAILED"] += 1
            continue
        if status.startswith("PENDING"):
            omitted["PENDING"] += 1
            continue
        if status.startswith("REQUIRES_REVIEW"):
            omitted["REQUIRES_REVIEW"] += 1
            continue
        if not rec.get("file") or not rec.get("sha256"):
            omitted["sin_archivo"] += 1
            continue
        category = rec.get("category") if rec.get("category") in mediarules.categories() else "OTHER"
        if not mediarules.valid_image_id(rec["id"]):
            omitted["id_invalido"] += 1
            continue
        w, h = rec.get("width"), rec.get("height")
        if not isinstance(w, int) or not isinstance(h, int):
            omitted["sin_dimensiones"] += 1
            continue
        if max(w, h) > mediarules.max_edge(category, rec.get("hero_rank")):
            omitted["fuera_de_reglas_de_medios"] += 1
            continue
        key = r2.get(rec["id"])
        if key:
            key_source["r2.json"] += 1
        elif preview_keys:
            key = mediarules.image_key(brand, slug, category, rec["id"], rec["sha256"])
            key_source["preview"] += 1
        else:
            omitted["sin_subir_a_r2"] += 1
            continue
        images.append({"id": rec["id"], "category": category, "key": key, "width": w, "height": h,
                       "hero_rank": rec.get("hero_rank"), "copyright_notice": rec.get("copyright_notice"),
                       "alt": {"es": mediarules.alt_text(model_name, category)}})
    images.sort(key=lambda i: (i["hero_rank"] if i["hero_rank"] is not None else 10 ** 6, i["id"]))
    heroes = sorted((r for r in inv if r.get("hero_candidate") and r.get("scope") == "THIS_MODEL"),
                    key=lambda r: (r.get("hero_rank") or 10 ** 6, r["id"]))
    bad = [r for r in heroes if not mediarules.valid_image_id(r["id"])]
    if bad:  # el contrato exige ids validos: no entran como candidatas (se cuentan en el informe)
        omitted["portada_candidata_id_invalido"] += len(bad)
        heroes = [r for r in heroes if mediarules.valid_image_id(r["id"])]
    keys = {i["id"]: i["key"] for i in images}
    hero_images = [{"image_id": r["id"], "hero_rank": r.get("hero_rank"), "reason": r.get("hero_reason"),
                    "available": r["id"] in keys, "key": keys.get(r["id"])} for r in heroes]
    return images, hero_images, key_source


def _build_videos(pkg: Path, third_party_listed: list):
    path = pkg / "05_MULTIMEDIA" / "VIDEOS" / "videos.json"
    videos, used = [], Counter()
    if not path.exists():
        return videos
    for v in _json(path).get("videos", []):
        if v.get("scope") != "THIS_MODEL":
            continue
        tp = v.get("third_party")
        if tp is True:
            third_party_listed.append({"title": v.get("title"), "url": v.get("url")})
            continue
        host = urlparse(v.get("url") or "").netloc.lower()
        if not host:
            continue
        vid = re.sub(r"[^a-z0-9]+", "-", (v.get("title") or "video").lower()).strip("-") or "video"
        used[vid] += 1
        if used[vid] > 1:
            vid = f"{vid}-{used[vid]}"
        unverified = tp is None
        videos.append({
            "id": vid, "title": v.get("title") or vid, "url": v["url"], "host": host,
            "platform": v.get("platform") or host, "cover_image": v.get("cover_image"),
            "resolution": v.get("resolution"), "third_party": tp,
            "review_status": "unverified" if unverified else "verified",
            "alert": {"level": "warning", "code": "VIDEO_UNVERIFIED",
                      "message": {"es": "Video sin verificar: confirmar que es oficial del modelo o retirarlo."},
                      "actions": ["mark_verified", "remove"]} if unverified else None})
    return videos


def _build_documents(pkg: Path):
    path = pkg / "06_DOCUMENTOS" / "documents.json"
    if not path.exists():
        return []
    cats = {"BROCHURES", "TECHNICAL", "MANUALS", "OTHER"}
    docs = []
    for d in _json(path).get("documents", []):
        url = d.get("download_url") or ""
        if d.get("scope") != "THIS_MODEL" or not url.startswith(("http://", "https://")):
            continue
        docs.append({"id": d["id"], "title": d["title"], "category": d["category"] if d.get("category") in cats else "OTHER",
                     "url": url, "format": d.get("format")})
    return docs


# --------------------------------------------------------------------------------------------------- un modelo

def build_model(pkg: Path, root: Path, generated_at: str, preview_keys: bool = False, rules: dict | None = None):
    """Devuelve (portal_doc, info) para un paquete. `info` alimenta el informe."""
    brand_dir, slug = pkg.parent.name, pkg.name
    spec = _json(pkg / "02_ESPECIFICACIONES" / "specifications.json")
    ready = _json(pkg / "00_MODELO" / "readiness.json")
    mdl = spec["model"]
    verified = [f for f in spec["fields"] if f["status"] == "VERIFIED" and f.get("records")]
    model_json = _json(pkg / "00_MODELO" / "model.json") if (pkg / "00_MODELO" / "model.json").exists() else {}
    source_url = model_json.get("source_url") or (verified[0]["records"][0]["url"] if verified else None)
    if not source_url:
        src = pkg / "07_FUENTES" / "sources.json"
        data = _json(src) if src.exists() else {}
        sources = data.get("sources", []) if isinstance(data, dict) else data
        source_url = next((s.get("url") for s in sources if isinstance(s, dict) and s.get("url")), None)
    if not source_url:
        raise ExportError(f"{brand_dir}/{slug}: sin URL de fuente")

    specs = []
    for f in verified:
        r = f["records"][0]
        specs.append({"key": f["oceanic_field"], "label": {"es": f["label"]}, "display_value": f["display_value"],
                      "unit": r.get("normalized_unit"), "model_year": _model_year(r.get("model_year") or mdl.get("model_year")),
                      "source": {"id": r["source_id"], "url": r["url"], "accessed_at": r["accessed_at"]}})

    sections = parse_sections(pkg)
    headlines, shorts = parse_hero_candidates(pkg)
    omitted, manual, third_party, key_source = Counter(), [], [], Counter()
    images, hero_images, key_source = _build_images(pkg, brand_dir, slug, mdl["model"], preview_keys, omitted, manual)
    videos = _build_videos(pkg, third_party)

    eq = pkg / "04_EQUIPAMIENTO"
    texts = {k: {"text": v["text"], "draft_marker_removed": v["draft_marker_removed"],
                 "internal_notes_removed": v["internal_notes_removed"]} for k, v in sections.items()}
    missing = [{"item": k, "group": v["group"], "status": v["status"], "detail": v["detail"]}
               for k, v in ready["items"].items() if v["status"] != "OK"]
    doc = {
        "schema_version": SCHEMA_VERSION, "generated_at": generated_at,
        "identity": {"brand": {"slug": brand_dir, "name": mdl["brand"]}, "model": mdl["model"], "slug": slug,
                     "boat_type": mdl["boat_type"], "model_year": _model_year(mdl.get("model_year")),
                     "range": mdl.get("range"), "variant": mdl.get("variant"), "content_hash": "0" * 64,
                     "source_url": source_url},
        "texts": {"es": texts},
        "candidates": {"headlines": {"es": headlines}, "short_descriptions": {"es": shorts}, "hero_images": hero_images},
        "specs": specs,
        "features": _literal_block(parse_literal_items(pkg / "03_CARACTERISTICAS" / "caracteristicas.md"), source_url),
        "equipment": {name: _literal_block(parse_literal_items(eq / f"{name}.md"), source_url)
                      for name in ("standard", "optional", "packages", "configurations")},
        "images": images, "videos": videos, "documents": _build_documents(pkg),
        "readiness": {"content_status": ready["CONTENT_STATUS"], "completeness_pct": ready["completeness_pct"],
                      "critical_missing": ready["critical_missing"], "unresolved_conflicts": ready["unresolved_conflicts"],
                      "missing": missing},
    }

    rules = rules or textlint.load_rules()
    violations = []
    for key, sec in texts.items():
        if sec["text"]:
            violations += textlint.lint_text(sec["text"], "texts", key, rules)
    for h in headlines:
        violations += textlint.lint_text(h, "headlines", "headlines", rules)
    for s in shorts:
        violations += textlint.lint_text(s, "short_descriptions", "short_descriptions", rules)
    doc["lint_ok"], doc["lint_violations"] = textlint.lint_ok(violations), violations

    flagged = {k: [n for n in v["notes"] if n[2]] for k, v in sections.items()}
    doc["publish_tier"] = _publish_tier(doc, flagged)
    doc["identity"]["content_hash"] = model_content_hash(doc)
    info = {"omitted": omitted, "manual": manual, "third_party": third_party, "key_source": key_source,
            "notes": {k: v["notes"] for k, v in sections.items() if v["notes"]},
            "unverified_videos": [v["id"] for v in videos if v["review_status"] == "unverified"]}
    return doc, info


def _publish_tier(doc: dict, flagged: dict) -> dict:
    """Niveles estaticos de schema/publish-rules.json (sin comparar contra linea base: todo es 'new')."""
    def change(target, tier, rule, reason=None):
        c = {"target": target, "change_kind": "new", "tier": tier, "rule": rule}
        if reason:
            c["reason"] = reason
        return c

    changes = [change("model", "review", "new-model")]
    if doc["candidates"]["headlines"]["es"]:
        changes.append(change("headlines", "review", "headline"))
    if doc["candidates"]["short_descriptions"]["es"]:
        changes.append(change("short_descriptions", "review", "editorial-text"))
    for key, sec in doc["texts"]["es"].items():
        if sec["text"]:
            reason = ("nota interna junto a una posible cifra calculada: revisar la frase vecina"
                      if flagged.get(key) else None)
            changes.append(change(f"text:{key}", "review", "editorial-text", reason))
    if any(h["available"] for h in doc["candidates"]["hero_images"]):
        changes.append(change("hero", "review", "hero-image"))
    changes += [change(f"image:{i['id']}", "auto", "image-same-model") for i in doc["images"] if i["hero_rank"] is None]
    changes += [change(f"video:{v['id']}", "auto", "video-unverified") for v in doc["videos"]
                if v["review_status"] == "unverified"]
    overall = "review" if any(c["tier"] == "review" for c in changes) else "auto"
    return {"overall": overall, "mode": "static", "rules_version": "1.0.0", "changes": changes}


# ------------------------------------------------------------------------------------------------------ marcas

def build_brand(root: Path, brand_dir: str, name: str, generated_at: str, rules: dict):
    path = root / "marcas" / brand_dir / "es.md"
    if not path.exists():
        return None
    md = _read(path)
    m = re.search(r"^## ", md, re.M)
    body = md[m.start():] if m else md  # sin titulo ni cabecera BORRADOR
    draft = bool(re.search(r"BORRADOR", md[:m.start() if m else len(md)]))
    lines, notes = _drop_note_paragraphs(body.splitlines())
    text, inline_notes, _ = textlint.clean_internal_notes("\n".join(ln.rstrip() for ln in lines))
    notes += inline_notes  # notas del traductor en cursiva *( ... )*
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(ln.rstrip() for ln in text.splitlines())).strip()
    violations = textlint.lint_text(text, "brand", "brand", rules)
    return {"schema_version": SCHEMA_VERSION, "generated_at": generated_at,
            "brand": {"slug": brand_dir, "name": name},
            "texts": {"es": {"text": text, "draft_marker_removed": draft, "internal_notes_removed": notes}},
            "brand_authorization": "unknown", "source": {"file": f"marcas/{brand_dir}/es.md"},
            "lint_ok": textlint.lint_ok(violations), "lint_violations": violations}


# ------------------------------------------------------------------------------------------------------ export

def _validators(schema: dict):
    from jsonschema import Draft202012Validator, FormatChecker
    Draft202012Validator.check_schema(schema)
    fc = FormatChecker()
    defs = schema["$defs"]
    return (Draft202012Validator(schema, format_checker=fc),
            Draft202012Validator({"$ref": "#/$defs/brand", "$defs": defs}, format_checker=fc),
            Draft202012Validator({"$ref": "#/$defs/index", "$defs": defs}, format_checker=fc))


def _errors(validator, doc, label) -> list[str]:
    return [f"{label}: {'/'.join(map(str, e.path)) or '(raiz)'} -> {e.message[:200]}"
            for e in sorted(validator.iter_errors(doc), key=lambda e: list(e.path))[:10]]


def list_models(base: Path) -> list[Path]:
    return sorted(p.parent.parent for p in base.glob("*/*/00_MODELO/00_MODELO.md"))


def export_portal(root: Path = ROOT, out: Path | None = None, preview_keys: bool = False,
                  only: str | None = None, generated_at: str | None = None, write: bool = True) -> dict:
    """Exporta todo. Lanza ExportError (sin escribir nada) si algun documento no valida contra el esquema."""
    out = Path(out) if out else root / "dist" / "portal"
    generated_at = generated_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    schema = _json(root / "schema" / "portal.schema.json")
    v_portal, v_brand, v_index = _validators(schema)
    rules = textlint.load_rules()

    docs, infos, errors = {}, {}, []
    for pkg in list_models(root / "biblioteca"):
        if only and f"{pkg.parent.name}/{pkg.name}" != only:
            continue
        key = f"{pkg.parent.name}/{pkg.name}"
        try:
            doc, info = build_model(pkg, root, generated_at, preview_keys, rules)
        except ExportError:
            raise
        except Exception as exc:  # noqa: BLE001 - un paquete mal formado se informa con su nombre
            raise ExportError(f"{key}: {type(exc).__name__}: {exc}") from exc
        errors += _errors(v_portal, doc, key)
        docs[key], infos[key] = doc, info
    brand_names = {}
    for key, doc in docs.items():
        brand_names.setdefault(doc["identity"]["brand"]["slug"], doc["identity"]["brand"]["name"])
    brands = {}
    for slug, name in sorted(brand_names.items()):
        b = build_brand(root, slug, name, generated_at, rules)
        if b:
            errors += _errors(v_brand, b, f"brands/{slug}")
            brands[slug] = b

    index = {
        "schema_version": SCHEMA_VERSION, "generated_at": generated_at, "catalog_hash": "0" * 64,
        "preview": bool(preview_keys),
        "brands": [{"slug": s, "name": brand_names[s], "path": f"brands/{s}/brand.json",
                    "models": sum(1 for d in docs.values() if d["identity"]["brand"]["slug"] == s),
                    "brand_authorization": "unknown"} for s in sorted(brands)],
        "models": [{"brand": d["identity"]["brand"]["slug"], "slug": d["identity"]["slug"],
                    "model": d["identity"]["model"],
                    "path": f"models/{d['identity']['brand']['slug']}/{d['identity']['slug']}/portal.json",
                    "content_hash": d["identity"]["content_hash"], "content_status": d["readiness"]["content_status"],
                    "lint_ok": d["lint_ok"], "publish_tier": d["publish_tier"]["overall"],
                    "images": len(d["images"]), "videos": len(d["videos"])}
                   for _, d in sorted(docs.items())],
    }
    body = json.loads(json.dumps(index))
    body["catalog_hash"] = None
    index["catalog_hash"] = canonical_hash(body)
    errors += _errors(v_index, index, "index.json")
    if errors:
        raise ExportError("El export no valida contra schema/portal.schema.json:\n  " + "\n  ".join(errors))

    report = build_report(docs, infos, brands, index, preview_keys, generated_at)
    if write:
        for key, doc in docs.items():
            _write(out / "models" / key / "portal.json", doc)
        for slug, b in brands.items():
            _write(out / "brands" / slug / "brand.json", b)
        _write(out / "index.json", index)
        _write(out / "export-report.json", report)
        for name in SCHEMA_COPIES:
            (out / "schema").mkdir(parents=True, exist_ok=True)
            shutil.copyfile(root / "schema" / name, out / "schema" / name)
    return {"docs": docs, "brands": brands, "index": index, "report": report, "out": out}


def build_report(docs, infos, brands, index, preview_keys, generated_at) -> dict:
    by_brand: dict[str, Counter] = {}
    omitted, key_source, tiers = Counter(), Counter(), Counter()
    manual, third_party, no_hero, lint_bad, notes = [], [], [], [], []
    per_model_omitted = {}
    unverified = []
    for key, doc in sorted(docs.items()):
        info = infos[key]
        by_brand.setdefault(doc["identity"]["brand"]["slug"], Counter())[doc["readiness"]["content_status"]] += 1
        omitted.update(info["omitted"])
        key_source.update(info["key_source"])
        if info["omitted"]:
            per_model_omitted[key] = dict(info["omitted"])
        manual += [{"model": key, **m} for m in info["manual"]]
        if info["third_party"]:
            third_party.append({"model": key, "videos": info["third_party"]})
        if info["unverified_videos"]:
            unverified.append({"model": key, "videos": info["unverified_videos"]})
        if not any(h["available"] for h in doc["candidates"]["hero_images"]):
            no_hero.append(key)
        if not doc["lint_ok"]:
            lint_bad.append({"model": key, "violations": len(doc["lint_violations"]),
                             "rules": sorted({v["rule"] for v in doc["lint_violations"]})})
        tiers[doc["publish_tier"]["overall"]] += 1
        for section, found in info["notes"].items():
            for note, neighbor, flag in found:
                notes.append({"model": key, "section": section, "note": note, "neighbor_sentence": neighbor,
                              "flagged_for_review": flag})
    notes_total = sum(d_sec["internal_notes_removed"] for d in docs.values() for d_sec in d["texts"]["es"].values())
    return {
        "schema_version": SCHEMA_VERSION, "generated_at": generated_at, "preview": bool(preview_keys),
        "models": {"total": len(docs), "by_brand": {b: {"total": sum(c.values()), **dict(c)}
                                                    for b, c in sorted(by_brand.items())}},
        "brands": {"total": len(brands), "lint_failed": [b for b, v in brands.items() if not v["lint_ok"]]},
        "images": {"published": sum(len(d["images"]) for d in docs.values()), "omitted": dict(omitted),
                   "omitted_by_model": per_model_omitted, "key_source": dict(key_source),
                   "preview_keys": preview_keys},
        "requires_manual_download": manual,
        "third_party_videos": third_party,
        "unverified_videos": unverified,
        "lint_failed_models": lint_bad,
        "lint": _lint_stats(docs, brands),
        "models_without_hero_candidate": no_hero,
        "internal_notes_removed": {"total": notes_total, "details": notes,
                                   "brands": {b: v["texts"]["es"]["internal_notes_removed"] for b, v in brands.items()}},
        "publish_tier": dict(tiers),
        "catalog_hash": index["catalog_hash"],
    }


def _lint_stats(docs, brands) -> dict:
    """Violaciones de lint por marca y por regla (textos de modelos + texto de marca)."""
    by_brand, total = {}, Counter()
    for slug in sorted(brands):
        by_brand[slug] = {"models_with_violations": 0, "model_violations": 0, "brand_text_violations": 0,
                          "by_rule": Counter()}
    for doc in docs.values():
        b = by_brand.setdefault(doc["identity"]["brand"]["slug"], {
            "models_with_violations": 0, "model_violations": 0, "brand_text_violations": 0, "by_rule": Counter()})
        if doc["lint_violations"]:
            b["models_with_violations"] += 1
        for v in doc["lint_violations"]:
            b["model_violations"] += 1
            b["by_rule"][v["rule"]] += 1
            total[v["rule"]] += 1
    for slug, brand in brands.items():
        for v in brand["lint_violations"]:
            by_brand[slug]["brand_text_violations"] += 1
            by_brand[slug]["by_rule"][v["rule"]] += 1
            total[v["rule"]] += 1
    for b in by_brand.values():
        b["by_rule"] = dict(sorted(b["by_rule"].items()))
    return {"total": sum(total.values()), "by_rule": dict(sorted(total.items())), "by_brand": by_brand}


def summary_lines(report: dict) -> list[str]:
    lines = (["VISTA PREVIA: claves de imagen no verificadas en R2; publish-export no subira este export"]
             if report["preview"] else [])
    lines += [f"Modelos exportados: {report['models']['total']} en {report['brands']['total']} marcas"]
    for brand, c in report["models"]["by_brand"].items():
        states = ", ".join(f"{k}={v}" for k, v in c.items() if k != "total")
        lines.append(f"  {brand}: {c['total']} ({states})")
    img = report["images"]
    lines.append(f"Imagenes publicadas: {img['published']}" + ("  [claves de vista previa, no verificadas en R2]"
                                                                if img["preview_keys"] else ""))
    lines.append("Imagenes omitidas: " + (", ".join(f"{k}={v}" for k, v in sorted(img["omitted"].items())) or "ninguna"))
    lint = report["lint"]
    lines.append(f"Violaciones de lint: {lint['total']} " + ", ".join(
        f"{b}={v['model_violations'] + v['brand_text_violations']}"
        for b, v in lint["by_brand"].items() if v["model_violations"] + v["brand_text_violations"]))
    lines.append(f"Modelos con lint_ok false: {len(report['lint_failed_models'])}"
                 f" · marcas con lint_ok false: {len(report['brands']['lint_failed'])}")
    lines.append(f"Modelos sin portada candidata disponible: {len(report['models_without_hero_candidate'])}")
    lines.append(f"Descargas manuales (requires_manual_download): {len(report['requires_manual_download'])}")
    lines.append(f"Videos third_party excluidos (modelos): {len(report['third_party_videos'])}"
                 f" · modelos con videos sin verificar: {len(report['unverified_videos'])}")
    lines.append(f"Notas internas quitadas: {report['internal_notes_removed']['total']}")
    lines.append("Niveles de publicacion: " + ", ".join(f"{k}={v}" for k, v in sorted(report["publish_tier"].items())))
    lines.append(f"catalog_hash: {report['catalog_hash']}")
    return lines


def main_cli(args: list[str], root: Path = ROOT) -> int:
    out = next((a.split("=", 1)[1] for a in args if a.startswith("--out=")), None)
    only = next((a.split("=", 1)[1] for a in args if a.startswith("--only=")), None)
    gen = next((a.split("=", 1)[1] for a in args if a.startswith("--generated-at=")), None)
    try:
        result = export_portal(root, Path(out) if out else None, "--preview-keys" in args, only, gen)
    except ExportError as exc:
        print("ERROR", exc)
        return 1
    for line in summary_lines(result["report"]):
        print(line)
    print(f"Salida: {result['out']}")
    return 0
