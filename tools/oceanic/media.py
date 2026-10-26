"""Image inventory, downloads and document downloads.

images.json is the curated inventory for a model: one record per official
image with its category, whether it belongs to this exact model, and hero
candidacy. `download_images` fetches each original only to fingerprint it
(sha256, real size) and derive a WebP web copy, which is what the library
stores. Duplicates are detected on the original's hash. Records that are
excluded (other model) are never downloaded. Documents are kept as links.
"""

from __future__ import annotations

import hashlib
import http.cookiejar
import json
import re
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

IMAGE_CATEGORIES = ("HERO", "EXTERIOR", "INTERIOR", "COCKPIT", "CABIN", "HELM",
                    "DETAIL", "UNDERWAY", "PLANS", "OTHER")
DOC_CATEGORIES = ("BROCHURES", "TECHNICAL", "MANUALS", "OTHER")
USER_AGENT = "Mozilla/5.0 (oceanic-content-agent; +https://oceanic.cl)"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:80] or "file"


def _fetch(url: str, timeout: int = 120) -> bytes:
    # Some servers (manuals.axopar.com) redirect through a cookie check, so keep cookies across redirects.
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    # Non-ASCII file names (xoboats.com: "D8-sisältä-top.jpg") must be percent-encoded; "%" is kept as is.
    url = urllib.parse.quote(url, safe=":/?&=%#+,;@~")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with opener.open(req, timeout=timeout) as resp:
        return resp.read()


def _image_info(data: bytes) -> tuple[str | None, int | None, int | None]:
    try:
        from PIL import Image
        import io
        with Image.open(io.BytesIO(data)) as im:
            return (im.format or "").lower(), im.width, im.height
    except Exception:
        return None, None, None


def init_inventory(extract: dict) -> list[dict]:
    """Seed records from a source extract; category and scope start unclassified."""
    records = []
    for img in extract["images"]:
        if img.get("role") == "video_cover":
            continue
        records.append({
            "id": _slug(img["title"] or img["src"].rsplit("/", 1)[-1]),
            "title": img["title"],
            "category": "UNCLASSIFIED",
            "scope": "REQUIRES_REVIEW",
            "scope_evidence": "",
            "hero_candidate": False,
            "hero_reason": None,
            "source_url": img["original_download_url"] or img["src"],
            "cdn_url": img["src"],
            "source_page": extract["source_url"],
            "source_context": img["context"],
            "declared_width": img["width"],
            "declared_height": img["height"],
            "declared_format": img["ext"],
            "dam_tags": img["tags"],
            "file": None,
            "width": None,
            "height": None,
            "format": None,
            "sha256": None,
            "collected_at": extract["accessed_at"],
            "downloaded_at": None,
            "download_status": "PENDING",
        })
    return records


# Decisión Oceanic 2026-10-08: carpetas más livianas (1920 px / q82 → 1600 px / q75; ~40 % menos por imagen).
WEB_MAX_EDGE = {"hero": 2560, "default": 1600}
WEB_QUALITY = 75


def _web_copy(data: bytes, max_edge: int) -> tuple[bytes, int, int]:
    """Web derivative in WebP: long edge capped at max_edge, alpha kept for renders."""
    import io
    from PIL import Image
    with Image.open(io.BytesIO(data)) as im:
        im = im.convert("RGBA" if im.mode in ("RGBA", "LA", "P") else "RGB")
        im.thumbnail((max_edge, max_edge), Image.LANCZOS)
        out = io.BytesIO()
        im.save(out, "WEBP", quality=WEB_QUALITY, method=6)
        return out.getvalue(), im.width, im.height


def _local_original(folder: Path, url: str):
    """A file saved by hand from the browser, matched by the original's file name (WordPress may add -1, (1)...)."""
    from urllib.parse import unquote
    name = unquote(url.split("?")[0].rsplit("/", 1)[-1])
    stem = name.rsplit(".", 1)[0]
    exact = folder / name
    if exact.exists():
        return exact
    for f in sorted(folder.rglob("*")):
        if f.is_file() and f.stem in (stem, f"{stem} (1)", f"{stem}(1)"):
            return f
    return None


def download_images(model_dir: Path, source: str = "original") -> dict:
    """Store a WebP web copy per image; the original is referenced (URL, sha256, size), not stored.

    Originals stay in the manufacturer's DAM and can be fetched again from `original.url`.
    """
    inv_path = model_dir / "05_MULTIMEDIA" / "IMAGENES" / "images.json"
    inv = json.loads(inv_path.read_text())
    by_hash = {(r["original"].get("sha256") or r["original"].get("source_copy_sha256")): r["id"]
               for r in inv["images"] if r.get("original") and r["file"]}
    stats = {"stored": 0, "duplicates": 0, "failed": 0, "skipped": 0, "from_cdn": 0}
    for rec in inv["images"]:
        done = rec["file"] and rec["file"].endswith(".webp") and rec.get("original") \
            and (model_dir / rec["file"]).exists()
        if rec["scope"] != "THIS_MODEL" or rec["download_status"].startswith("DUPLICATE_OF") or done:
            stats["skipped"] += 1
            continue
        data, used, err = None, None, None
        legacy = model_dir / rec["file"] if rec["file"] else None
        if legacy and legacy.exists() and rec.get("width") == rec["declared_width"]:
            data, used = legacy.read_bytes(), rec.get("downloaded_from") or rec["source_url"]
        elif source.startswith("dir:"):
            # Originals downloaded by hand in a browser (sites behind an anti-bot challenge, e.g. solarisyachts.com).
            f = _local_original(Path(source[4:]), rec["source_url"])
            if f:
                data, used = f.read_bytes(), "descarga manual en navegador"
                # Same-named copy taken from a mirror (e.g. oceanic.cl): `mirror.json` maps file name -> mirror URL.
                mj = next((m for m in (f.parent / "mirror.json", Path(source[4:]) / "mirror.json") if m.exists()), None)
                if mj:
                    mirror = json.loads(mj.read_text())
                    mirror = mirror.get(model_dir.name, mirror)
                    if f.name in mirror:
                        used = f"copia con el mismo nombre de archivo publicada en {mirror[f.name]}"
            else:
                err = "no está en la carpeta de descargas manuales"
        elif source == "cdn":
            # Fast path: the CDN serves a resized copy, enough for a 2560 px web copy.
            try:
                url = rec["cdn_url"] + "?width=2560&quality=90"
                data, used = _fetch(url), url
            except Exception as exc:
                err = f"{type(exc).__name__}: {exc}"
        else:
            for url in (rec["source_url"], rec["cdn_url"]):
                try:
                    data, used = _fetch(url), url
                    break
                except Exception as exc:  # network policy, 404, timeout
                    err = f"{type(exc).__name__}: {exc}"
        if data is None:
            rec["download_status"] = f"FAILED ({err})"
            stats["failed"] += 1
            continue
        fmt, w, h = _image_info(data)
        if not fmt:
            # Not an image: e.g. an anti-bot challenge page (SiteGround "sg-captcha") instead of the file.
            snippet = data[:200].decode("utf-8", "replace")
            why = "desafío anti-bots del servidor (captcha)" if "captcha" in snippet.lower() else "la respuesta no es una imagen"
            rec["download_status"] = f"FAILED ({why})"
            stats["failed"] += 1
            continue
        digest = hashlib.sha256(data).hexdigest()
        # Fetched from the original URL = original, whatever its size; otherwise compare with the declared size.
        full = used in (rec["source_url"], "descarga manual en navegador") or bool(w and rec["declared_width"] and w >= rec["declared_width"])
        if source == "cdn":
            # Original not fetched: keep its reference and declared size from the DAM.
            rec["original"] = {"url": rec["source_url"], "fetched_from": None, "sha256": None,
                               "width": rec["declared_width"], "height": rec["declared_height"],
                               "format": rec["declared_format"], "bytes": None, "full_resolution": None,
                               "source_copy_sha256": digest}
            full = True
        else:
            rec["original"] = {"url": rec["source_url"],
                               "fetched_from": used,
                               "sha256": digest,
                               "width": w, "height": h, "format": fmt, "bytes": len(data),
                               "full_resolution": full}
        if digest in by_hash:
            rec.update(file=None, download_status=f"DUPLICATE_OF {by_hash[digest]}")
            stats["duplicates"] += 1
            continue
        category = rec["category"] if rec["category"] in IMAGE_CATEGORIES else "OTHER"
        edge = WEB_MAX_EDGE["hero" if rec["hero_candidate"] else "default"]
        web, ww, wh = _web_copy(data, edge)
        dest = model_dir / "05_MULTIMEDIA" / "IMAGENES" / category / f"{rec['id']}.webp"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(web)
        if legacy and legacy.exists() and legacy != dest:
            legacy.unlink()
        by_hash[digest] = rec["id"]
        stats["from_cdn"] += not full
        rec.update(file=str(dest.relative_to(model_dir)), sha256=hashlib.sha256(web).hexdigest(),
                   format="webp", width=ww, height=wh, bytes=len(web), web_quality=WEB_QUALITY,
                   downloaded_at=date.today().isoformat(), downloaded_from=used,
                   download_status="WEB_COPY" if full else "WEB_COPY (original no disponible; desde CDN)")
        stats["stored"] += 1
    inv_path.write_text(json.dumps(inv, ensure_ascii=False, indent=2) + "\n")
    return stats


def optimize_images(model_dir: Path) -> dict:
    """Re-encode existing web copies to the current size/quality (WEB_MAX_EDGE, WEB_QUALITY) without re-downloading.

    Copies already at the current settings (`web_quality`) are left alone; the original stays referenced in `original`.
    """
    import io
    from PIL import Image
    inv_path = model_dir / "05_MULTIMEDIA" / "IMAGENES" / "images.json"
    inv = json.loads(inv_path.read_text())
    stats = {"optimized": 0, "skipped": 0, "bytes_before": 0, "bytes_after": 0}
    for rec in inv["images"]:
        f = model_dir / rec["file"] if rec.get("file") else None
        if not f or not f.exists() or rec.get("web_quality") == WEB_QUALITY:
            stats["skipped"] += 1
            continue
        edge = WEB_MAX_EDGE["hero" if rec.get("hero_candidate") else "default"]
        before = f.read_bytes()
        with Image.open(io.BytesIO(before)) as im:
            im = im.convert("RGBA" if im.mode in ("RGBA", "LA", "P") else "RGB")
            im.thumbnail((edge, edge), Image.LANCZOS)
            out = io.BytesIO()
            im.save(out, "WEBP", quality=WEB_QUALITY, method=6)
            web, ww, wh = out.getvalue(), im.width, im.height
        if len(web) >= len(before):  # already lighter (small source): keep it
            web, (ww, wh) = before, (rec.get("width"), rec.get("height"))
        f.write_bytes(web)
        stats["bytes_before"] += len(before)
        stats["bytes_after"] += len(web)
        stats["optimized"] += 1
        rec.update(sha256=hashlib.sha256(web).hexdigest(), width=ww, height=wh, bytes=len(web), web_quality=WEB_QUALITY)
    inv_path.write_text(json.dumps(inv, ensure_ascii=False, indent=2) + "\n")
    return stats


def download_documents(model_dir: Path) -> dict:
    path = model_dir / "06_DOCUMENTOS" / "documents.json"
    docs = json.loads(path.read_text())
    stats = {"downloaded": 0, "failed": 0, "skipped": 0}
    for doc in docs["documents"]:
        # Documents are referenced by URL; only those marked keep_file are stored in the library.
        if (doc.get("file") or not doc.get("download_url") or doc.get("scope") != "THIS_MODEL"
                or not doc.get("keep_file")):
            stats["skipped"] += 1
            continue
        try:
            data = _fetch(doc["download_url"], timeout=300)
        except Exception as exc:
            doc["download_status"] = f"FAILED ({type(exc).__name__}: {exc})"
            stats["failed"] += 1
            continue
        category = doc["category"] if doc["category"] in DOC_CATEGORIES else "OTHER"
        ext = doc["download_url"].rsplit(".", 1)[-1].split("?")[0].lower()[:5] or "bin"
        dest = model_dir / "06_DOCUMENTOS" / category / f"{doc['id']}.{ext}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        doc.update(file=str(dest.relative_to(model_dir)), sha256=hashlib.sha256(data).hexdigest(),
                   size_bytes=len(data), downloaded_at=date.today().isoformat(),
                   download_status="DOWNLOADED")
        stats["downloaded"] += 1
    path.write_text(json.dumps(docs, ensure_ascii=False, indent=2) + "\n")
    return stats


def render_inventory(model_dir: Path) -> None:
    """05_MULTIMEDIA/multimedia.md: human-readable view of images.json and videos.json."""
    base = model_dir / "05_MULTIMEDIA"
    inv = json.loads((base / "IMAGENES" / "images.json").read_text())
    vids_path = base / "VIDEOS" / "videos.json"
    vids = json.loads(vids_path.read_text())["videos"] if vids_path.exists() else []
    imgs = inv["images"]
    lines = [f"# Multimedia · {inv['model']}", "",
             "> Generado desde `images.json` y `videos.json` (`python -m oceanic render`). No editar a mano.", "",
             f"- Uso: {inv.get('usage_terms', '')}",
             f"- Clasificación: {inv.get('classification_note', '')}",
             f"- Descarga: {inv.get('download_note', '')}", ""]
    heroes = sorted((r for r in imgs if r["hero_candidate"]), key=lambda r: r.get("hero_rank") or 99)
    lines += ["## HERO_CANDIDATE", "", "| Rank | id | Motivo | URL |", "| --- | --- | --- | --- |"]
    lines += [f"| {r.get('hero_rank')} | `{r['id']}` | {r['hero_reason']} | [original]({r['cdn_url']}) |"
              for r in heroes]
    lines.append("")
    for scope, title in (("THIS_MODEL", "Imágenes del modelo"),
                         ("REQUIRES_REVIEW", "REQUIRES REVIEW: modelo no confirmado"),
                         ("OTHER_MODEL", "Excluidas: otro modelo"),
                         ("NOT_MODEL_SPECIFIC", "Excluidas: no son del barco")):
        group = [r for r in imgs if r["scope"] == scope]
        if not group:
            continue
        lines += [f"## {title} ({len(group)})", "",
                  "| id | Categoría | Conf. | MY | Declarado | Descarga | Evidencia |",
                  "| --- | --- | --- | --- | --- | --- | --- |"]
        for r in sorted(group, key=lambda r: (IMAGE_CATEGORIES + ("UNCLASSIFIED",)).index(r["category"])):
            lines.append(
                f"| [`{r['id']}`]({r['cdn_url']}) | {r['category']} | {r.get('category_confidence', '')} | "
                f"{r.get('model_year_tag') or ''} | {r['declared_width']}x{r['declared_height']} "
                f"{r['declared_format']} | {r['download_status']} | {r['scope_evidence']} |")
        lines.append("")
    if vids:
        lines += [f"## Videos ({len(vids)})", "", "| Título | Alcance | Resolución | Fecha DAM | Nota |",
                  "| --- | --- | --- | --- | --- |"]
        lines += [f"| [{v['title']}]({v['url']}) | {v['scope']} | {v.get('resolution') or ''} | "
                  f"{v.get('date') or ''} | {v.get('notes') or ''} |" for v in vids]
        lines.append("")
    (base / "multimedia.md").write_text("\n".join(lines))


def _webp_ok(path: Path) -> bool:
    """True if the file exists and fully decodes as WebP."""
    try:
        from PIL import Image
        with Image.open(path) as im:
            if (im.format or "").upper() != "WEBP":
                return False
            im.load()
        return True
    except Exception:
        return False


def restore_images(model_dir: Path, fetch=None, brand: str | None = None) -> dict:
    """Restore only missing/corrupt web copies from the official sites; never touches `original`.

    Records on disk that open as WebP are left alone. Solaris is skipped (anti-bot captcha: `media --from-dir`).
    A captcha response is never bypassed: the record gets `requires_manual_download` + `manual_download_url`.
    """
    fetch = fetch or _fetch
    model_dir = Path(model_dir)
    brand = brand or model_dir.parent.name
    stats = {"restored": 0, "ok_on_disk": 0, "failed": 0, "requires_manual_download": 0,
             "skipped_solaris": 0, "manual": []}
    inv_path = model_dir / "05_MULTIMEDIA" / "IMAGENES" / "images.json"
    inv = json.loads(inv_path.read_text(encoding="utf-8"))
    changed = False
    for rec in inv["images"]:
        if rec.get("scope") != "THIS_MODEL" or str(rec.get("download_status") or "").startswith("DUPLICATE_OF"):
            continue
        if rec.get("file") and _webp_ok(model_dir / rec["file"]):
            stats["ok_on_disk"] += 1
            continue
        if brand.lower() == "solaris":
            stats["skipped_solaris"] += 1
            continue
        orig = rec.get("original") or {}
        urls = []
        # downloaded_from primero: reproduce como se hizo la copia (p. ej. la CDN 2560 px de Axopar, sin bajar el original)
        for u in (rec.get("downloaded_from"), orig.get("fetched_from"), rec.get("source_url"), orig.get("url"), rec.get("cdn_url")):
            if isinstance(u, str) and u.startswith(("http://", "https://")) and u not in urls:
                urls.append(u)
        data, err, captcha_url = None, "sin URL oficial", None
        for url in urls:
            try:
                got = fetch(url)
            except Exception as exc:
                err = f"{type(exc).__name__}: {exc}"
                continue
            if _image_info(got)[0]:
                data = got
                break
            if "captcha" in got[:200].decode("utf-8", "replace").lower():
                captcha_url = captcha_url or url
                err = "desafío anti-bots del servidor (captcha)"
            else:
                err = "la respuesta no es una imagen"
        if data is None:
            if captcha_url:
                rec["download_status"] = "requires_manual_download"
                rec["manual_download_url"] = captcha_url
                stats["requires_manual_download"] += 1
                stats["manual"].append({"id": rec["id"], "url": captcha_url})
            else:
                rec["download_status"] = f"FAILED ({err})"
                stats["failed"] += 1
            changed = True
            continue
        category = rec["category"] if rec.get("category") in IMAGE_CATEGORIES else "OTHER"
        edge = WEB_MAX_EDGE["hero" if rec.get("hero_rank") is not None or category == "HERO" else "default"]
        web, ww, wh = _web_copy(data, edge)
        dest = model_dir / "05_MULTIMEDIA" / "IMAGENES" / category / f"{rec['id']}.webp"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(web)
        rec.update(file=str(dest.relative_to(model_dir)).replace("\\", "/"),
                   sha256=hashlib.sha256(web).hexdigest(), format="webp", width=ww, height=wh,
                   bytes=len(web), web_quality=WEB_QUALITY, downloaded_at=date.today().isoformat())
        rec.pop("manual_download_url", None)
        if not str(rec.get("download_status") or "").startswith("WEB_COPY"):
            rec["download_status"] = "WEB_COPY"  # was PENDING / FAILED / requires_manual_download
        stats["restored"] += 1
        changed = True
    if changed:
        with open(inv_path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(inv, ensure_ascii=False, indent=2) + "\n")
    return stats


def restore_main(args: list[str], root: Path) -> int:
    """`python -m oceanic media <modelo> --restore`: only images.json of the model may change."""
    pos = [a for a in args if not a.startswith("--")]
    if not pos:
        print("uso: python -m oceanic media <modelo> --restore")
        return 1
    model = Path(pos[0])
    if not (model / "05_MULTIMEDIA").exists():
        model = Path(root) / pos[0]
    if not (model / "05_MULTIMEDIA").exists():
        hits = sorted((Path(root) / "biblioteca").glob(f"*/{pos[0]}"))
        model = hits[0] if hits else model
    if not (model / "05_MULTIMEDIA" / "IMAGENES" / "images.json").exists():
        print(f"no existe images.json en {model}")
        return 1
    st = restore_images(model)
    print(f"{model.name}: repuestas {st['restored']}, ok en disco {st['ok_on_disk']}, fallidas {st['failed']}, "
          f"descarga manual {st['requires_manual_download']}, omitidas Solaris {st['skipped_solaris']}")
    for m in st["manual"]:
        print(f"  manual: {m['id']} -> {m['url']}")
    if st["skipped_solaris"]:
        print("  Solaris: usar `media <modelo> --from-dir=<carpeta>`")
    return 1 if st["failed"] else 0
