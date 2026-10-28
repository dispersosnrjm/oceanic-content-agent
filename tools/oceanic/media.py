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


def has_registered_copy(rec: dict) -> bool:
    """True when the inventory already records a good web copy (file + sha256, status WEB_COPY)."""
    return bool(rec.get("file") and rec.get("sha256") and str(rec.get("download_status") or "").startswith("WEB_COPY"))


def _mark_failed(rec: dict, reason: str) -> bool:
    """Write FAILED into download_status unless the record already has a registered copy.

    A download failure (DNS, timeout, 404, captcha) says nothing about a copy that was already verified, so it must not
    overwrite a good record: it only goes to the run report. Returns True if the record was modified.
    """
    if has_registered_copy(rec):
        return False
    rec["download_status"] = f"FAILED ({reason})"
    return True


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
            _mark_failed(rec, err)
            stats["failed"] += 1
            continue
        fmt, w, h = _image_info(data)
        if not fmt:
            # Not an image: e.g. an anti-bot challenge page (SiteGround "sg-captcha") instead of the file.
            snippet = data[:200].decode("utf-8", "replace")
            why = "desafío anti-bots del servidor (captcha)" if "captcha" in snippet.lower() else "la respuesta no es una imagen"
            _mark_failed(rec, why)
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


def _check_inside(model_dir: Path, rel, image_id=None) -> Path:
    """The resolved path of a record's `file` must stay inside the model folder; ValueError otherwise."""
    base = Path(model_dir).resolve()
    try:
        target = (base / str(rel)).resolve()
        inside = target.is_relative_to(base) and target != base
    except (OSError, ValueError):
        inside = False
    if not inside:
        raise ValueError(f"{base.name}: la ruta de la copia de la imagen {image_id!r} ({str(rel)[:80]!r}) sale de la "
                         f"carpeta del modelo; no se lee ni se escribe. Corrige `file` en images.json.")
    return target


def _restore_plan(rec: dict) -> tuple[list[str], str | None, str | None]:
    """URLs to try, in order, plus the (url, sha256) pair that the downloaded bytes can be checked against.

    * original.sha256 present: the original's URL (`original.fetched_from`) goes first and is the one that can be verified.
    * only original.source_copy_sha256 present (copies made with `--cdn`): the CDN URL the copy came from
      (`downloaded_from`) goes first; its raw bytes are what source_copy_sha256 fingerprints.
    * neither: nothing to compare (not verifiable).
    """
    orig = rec.get("original") or {}
    fetched, dfrom = orig.get("fetched_from"), rec.get("downloaded_from")
    if orig.get("sha256"):
        order = (fetched, dfrom, rec.get("source_url"), orig.get("url"), rec.get("cdn_url"))
        check_url, expected = fetched, orig["sha256"]
    elif orig.get("source_copy_sha256"):
        order = (dfrom, fetched, rec.get("source_url"), orig.get("url"), rec.get("cdn_url"))
        check_url, expected = dfrom, orig["source_copy_sha256"]
    else:
        order = (dfrom, fetched, rec.get("source_url"), orig.get("url"), rec.get("cdn_url"))
        check_url, expected = None, None
    urls = []
    for u in order:
        if isinstance(u, str) and u.startswith(("http://", "https://")) and u not in urls:
            urls.append(u)
    if check_url not in urls:
        check_url, expected = None, None
    return urls, check_url, expected


def restore_images(model_dir: Path, fetch=None, brand: str | None = None) -> dict:
    """Restore only missing/corrupt web copies from the official sites; never touches `original`.

    Records on disk that open as WebP are left alone. Solaris is skipped (anti-bot captcha: `media --from-dir`).
    A captcha response is never bypassed: the record gets `requires_manual_download` + `manual_download_url`.

    Each download is checked against the hash registered for what was fetched (see `_restore_plan`):
      * matches       -> `restored_verified`: the copy is written to the registered path and images.json is NOT
                         modified (not even downloaded_at), so a correct restore leaves no diff;
      * differs       -> `changed`: nothing is written, the record becomes `REQUIRES_REVIEW (original cambió)` (not
                         publishable) and keeps all its fields;
      * not checkable -> `restored_unverifiable`: the copy is written; the record is only filled in when it had no
                         copy registered yet.

    A failed download (DNS, timeout, 404, captcha, no URL) NEVER modifies a record that already has a registered copy
    (`has_registered_copy`): it only goes to the report (`failed_list`, `manual`). On success, a record whose
    download_status was left in another state (FAILED, requires_manual_download) is put back to WEB_COPY.
    """
    fetch = fetch or _fetch
    model_dir = Path(model_dir)
    brand = brand or model_dir.parent.name
    stats = {"brand": brand, "model": model_dir.name, "restored": 0, "restored_verified": 0, "restored_unverifiable": 0,
             "changed": 0, "changed_list": [], "ok_on_disk": 0, "failed": 0, "no_url": 0,
             "requires_manual_download": 0, "skipped_solaris": 0, "identical_copy": 0, "manual": [],
             "failed_list": [], "status_healed": 0, "inconsistent_status": []}
    inv_path = model_dir / "05_MULTIMEDIA" / "IMAGENES" / "images.json"
    inv = json.loads(inv_path.read_text(encoding="utf-8"))
    for rec in inv["images"]:  # antes de leer, descargar o escribir nada: todas las rutas deben quedar dentro del modelo
        if rec.get("file"):
            _check_inside(model_dir, rec["file"], rec.get("id"))
    changed = False
    for rec in inv["images"]:
        status = str(rec.get("download_status") or "")
        if rec.get("scope") != "THIS_MODEL" or status.startswith("DUPLICATE_OF"):
            continue
        if status.startswith("REQUIRES_REVIEW"):
            continue  # decision pendiente de una persona: no se vuelve a descargar ni a publicar
        if rec.get("file") and _webp_ok(model_dir / rec["file"]):
            stats["ok_on_disk"] += 1
            if rec.get("sha256") and not status.startswith("WEB_COPY"):
                stats["inconsistent_status"].append({"id": rec["id"], "status": status})  # se informa, no se toca
            continue
        if brand.lower() == "solaris":
            stats["skipped_solaris"] += 1
            continue
        urls, check_url, expected = _restore_plan(rec)
        registered = bool(rec.get("file") and rec.get("sha256"))  # ya hay una copia registrada en el inventario
        if not urls:
            changed |= _mark_failed(rec, "sin URL oficial")
            stats["no_url"] += 1
            stats["failed_list"].append({"id": rec["id"], "error": "sin URL oficial", "registered": registered})
            continue
        data, err, captcha_url, used = None, "sin URL oficial", None, None
        for url in urls:
            try:
                got = fetch(url)
            except Exception as exc:
                err = f"{type(exc).__name__}: {exc}"
                continue
            if _image_info(got)[0]:
                data, used = got, url
                break
            if "captcha" in got[:200].decode("utf-8", "replace").lower():
                captcha_url = captcha_url or url
                err = "desafío anti-bots del servidor (captcha)"
            else:
                err = "la respuesta no es una imagen"
        if data is None:
            if captcha_url:
                if not registered:  # con copia registrada el captcha solo va al informe
                    rec["download_status"] = "requires_manual_download"
                    rec["manual_download_url"] = captcha_url
                    changed = True
                stats["requires_manual_download"] += 1
                stats["manual"].append({"id": rec["id"], "url": captcha_url})
            else:
                changed |= _mark_failed(rec, err)
                stats["failed"] += 1
            stats["failed_list"].append({"id": rec["id"], "error": err, "registered": registered})
            continue
        got_sha = hashlib.sha256(data).hexdigest()
        if expected and used == check_url and got_sha != expected:
            rec["download_status"] = "REQUIRES_REVIEW (original cambió)"
            rec["restore_check"] = {"url": used, "expected_sha256": expected, "downloaded_sha256": got_sha}
            stats["changed"] += 1
            stats["changed_list"].append({"id": rec["id"], "url": used, "expected_sha256": expected,
                                          "downloaded_sha256": got_sha})
            changed = True
            continue
        verified = bool(expected and used == check_url)
        category = rec["category"] if rec.get("category") in IMAGE_CATEGORIES else "OTHER"
        edge = WEB_MAX_EDGE["hero" if rec.get("hero_rank") is not None or category == "HERO" else "default"]
        web, ww, wh = _web_copy(data, edge)
        fill = not rec.get("file") or not rec.get("sha256")
        rel = rec["file"] if rec.get("file") else f"05_MULTIMEDIA/IMAGENES/{category}/{rec['id']}.webp"
        dest = _check_inside(model_dir, rel, rec.get("id"))
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(web)
        web_sha = hashlib.sha256(web).hexdigest()
        stats["identical_copy"] += web_sha == rec.get("sha256")
        stats["restored"] += 1
        stats["restored_verified" if verified else "restored_unverifiable"] += 1
        if fill:  # the inventory had no copy registered: fill it in; otherwise the record stays exactly as it was
            rec.update(file=rel.replace("\\", "/"), sha256=web_sha, format="webp", width=ww, height=wh,
                       bytes=len(web), web_quality=WEB_QUALITY, downloaded_at=date.today().isoformat())
            changed = True
        if not str(rec.get("download_status") or "").startswith("WEB_COPY"):
            # restaurada con exito: el estado debe quedar coherente (estaba PENDING / FAILED / requires_manual_download)
            rec["download_status"] = "WEB_COPY"
            rec.pop("manual_download_url", None)
            stats["status_healed"] += 1
            changed = True
    if changed:
        with open(inv_path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(inv, ensure_ascii=False, indent=2) + "\n")
    return stats


def _resolve_restore_targets(arg: str, root: Path) -> list[Path]:
    """A model folder, a model slug, or a brand folder (all its models)."""
    root = Path(root)
    for cand in (Path(arg), root / arg, root / "biblioteca" / arg):
        if (cand / "05_MULTIMEDIA" / "IMAGENES" / "images.json").exists():
            return [cand]
        if cand.is_dir():
            models = sorted(p.parent.parent.parent for p in cand.glob("*/05_MULTIMEDIA/IMAGENES/images.json"))
            if models:
                return models
    hits = sorted((root / "biblioteca").glob(f"*/{arg}"))
    return [h for h in hits if (h / "05_MULTIMEDIA" / "IMAGENES" / "images.json").exists()]


def restore_report(all_stats: list[dict]) -> dict:
    """Totals and per-brand counts of a restore run."""
    keys = ("restored", "restored_verified", "restored_unverifiable", "changed", "ok_on_disk", "failed", "no_url",
            "requires_manual_download", "skipped_solaris", "identical_copy", "status_healed")
    total = {k: sum(s[k] for s in all_stats) for k in keys}
    by_brand = {}
    for s in all_stats:
        b = by_brand.setdefault(s["brand"], {k: 0 for k in keys})
        for k in keys:
            b[k] += s[k]
    return {"total": total, "by_brand": by_brand,
            "changed": [{"model": f"{s['brand']}/{s['model']}", **c} for s in all_stats for c in s["changed_list"]],
            "manual": [{"model": f"{s['brand']}/{s['model']}", **m} for s in all_stats for m in s["manual"]],
            "failed": [{"model": f"{s['brand']}/{s['model']}", **f} for s in all_stats for f in s["failed_list"]],
            "inconsistent_status": [{"model": f"{s['brand']}/{s['model']}", **i}
                                    for s in all_stats for i in s["inconsistent_status"]]}


def restore_main(args: list[str], root: Path, fetch=None) -> int:
    """`python -m oceanic media <modelo|marca> --restore [--report=<archivo.json>]`: only images.json may change."""
    pos = [a for a in args if not a.startswith("--")]
    if not pos:
        print("uso: python -m oceanic media <modelo|marca> --restore")
        return 1
    targets = _resolve_restore_targets(pos[0], root)
    if not targets:
        print(f"no existe images.json para {pos[0]}")
        return 1
    all_stats = []
    for model in targets:
        try:
            st = restore_images(model, fetch=fetch)
        except ValueError as exc:  # ruta de copia fuera de la carpeta del modelo
            print(f"ERROR {exc}")
            return 1
        all_stats.append(st)
        if len(targets) == 1 or st["restored"] or st["changed"] or st["failed"] or st["no_url"] \
                or st["requires_manual_download"]:
            print(f"{st['brand']}/{st['model']}: repuestas {st['restored']} (verificadas {st['restored_verified']}, "
                  f"no verificables {st['restored_unverifiable']}), cambiadas {st['changed']}, ok en disco "
                  f"{st['ok_on_disk']}, fallidas {st['failed']}, sin URL {st['no_url']}, "
                  f"descarga manual {st['requires_manual_download']}, omitidas Solaris {st['skipped_solaris']}")
    rep = restore_report(all_stats)
    t = rep["total"]
    print(f"TOTAL ({len(targets)} modelos): restauradas y verificadas {t['restored_verified']} · cambiadas "
          f"(REQUIRES_REVIEW) {t['changed']} · no verificables {t['restored_unverifiable']} · sin URL {t['no_url']} · "
          f"fallidas {t['failed']} · descarga manual {t['requires_manual_download']} · ya en disco {t['ok_on_disk']}")
    for brand, b in sorted(rep["by_brand"].items()):
        if b["restored_unverifiable"]:
            print(f"  no verificables en {brand}: {b['restored_unverifiable']}")
    for c in rep["changed"]:
        print(f"  CAMBIÓ el original ({c['model']}): {c['id']} {c['url']}")
    for m in rep["manual"]:
        print(f"  manual: {m['model']} {m['id']} -> {m['url']}")
    for f in rep["failed"][:10]:
        print(f"  FALLÓ la descarga ({f['model']}): {f['id']}: {f['error'][:90]}"
              + ("  [con copia registrada: images.json no se modificó]" if f["registered"] else ""))
    if len(rep["failed"]) > 10:
        print(f"  ... y {len(rep['failed']) - 10} fallos más en el informe (--report)")
    if t["status_healed"]:
        print(f"  estado corregido a WEB_COPY en {t['status_healed']} imágenes restauradas")
    for i in rep["inconsistent_status"][:10]:
        print(f"  AVISO estado incoherente ({i['model']}): {i['id']} tiene copia válida pero estado {i['status'][:40]!r}")
    if t["skipped_solaris"]:
        print("  Solaris: usar `media <modelo> --from-dir=<carpeta>`")
    out = next((a.split("=", 1)[1] for a in args if a.startswith("--report=")), None)
    if out:
        Path(out).write_text(json.dumps(rep, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return 1 if t["failed"] else 0
