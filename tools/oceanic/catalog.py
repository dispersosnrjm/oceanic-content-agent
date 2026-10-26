"""Publicacion del export del portal a R2 (python -m oceanic publish-export).

    python -m oceanic publish-export [--out=<carpeta>] [--apply] [--verify-remote] [--verify-images]
    python -m oceanic publish-export --rollback=<hash> [--schema-version=<v>] [--apply]

Sube index.json, cada portal.json y brand.json, portal.schema.json, media-rules.json, field-catalog.json y
publish-rules.json a  catalog/{schema_version}/{catalog_hash}/...  y, al final, el puntero catalog/latest.json.

Garantias:
  * Dry-run por defecto: sin red ni credenciales.
  * Se niega a subir un export con preview: true (claves de imagen no verificadas en R2) o con cualquier
    inconsistencia (hashes, archivos faltantes, rutas locales).
  * Nunca borra. Las claves de catalog/{schema_version}/{hash}/ son inmutables: si existen con el mismo sha256 se
    omiten; con otro sha256 es un error y no se pisan.
  * catalog/latest.json es la UNICA clave mutable (excepcion documentada). Se escribe al final y solo si todo lo
    demas subio sin errores. Es la unica escritura permitida sobre una clave existente.
  * Antes de sobrescribirla se lee (get_object) y, si existe, se guarda una copia inmutable en
    catalog/history/{fecha UTC}-{hash}.json; si esa copia falla, no se sobrescribe.
  * --rollback=<hash> apunta latest.json a un catalogo ya publicado (simulacion por defecto).
  * Credenciales solo por variables de entorno (las mismas que publish-r2); nunca se imprimen.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from . import exporter, r2

LATEST_KEY = "catalog/latest.json"  # unica clave mutable
HISTORY_PREFIX = "catalog/history"  # copias inmutables de cada puntero anterior: {fecha UTC}-{hash}.json
IMMUTABLE_CACHE = "public, max-age=31536000, immutable"
POINTER_CACHE = "no-cache"
JSON_TYPE = "application/json; charset=utf-8"
LOCAL_PATH = re.compile(r"^[A-Za-z]:[\\/]|^biblioteca[\\/]|^/(?:Users|home|mnt|tmp)/|^\.\.?[\\/]")


class PublishRefused(Exception):
    pass


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _reference_strings(doc: dict):
    """Campos que apuntan a recursos (claves R2 y URL): no pueden ser rutas locales."""
    yield doc["identity"]["source_url"]
    for im in doc["images"]:
        yield im["key"]
    for v in doc["videos"]:
        yield v["url"]
        if v.get("cover_image"):
            yield v["cover_image"]
    for d in doc["documents"]:
        yield d["url"]
    for h in doc["candidates"]["hero_images"]:
        if h.get("key"):
            yield h["key"]


def load_export(out: Path) -> dict:
    """Lee y comprueba un export. Lanza PublishRefused ante cualquier inconsistencia."""
    out = Path(out)
    index_path = out / "index.json"
    if not index_path.exists():
        raise PublishRefused(f"no hay index.json en {out}: ejecuta primero export-portal")
    index = json.loads(index_path.read_text(encoding="utf-8"))
    if index.get("preview") is not False:
        raise PublishRefused("el export tiene preview: true (o no declara preview): usa claves de vista previa no "
                             "verificadas en R2; reexporta sin --preview-keys, con r2.json de una carga real")
    body = json.loads(json.dumps(index))
    body["catalog_hash"] = None
    if exporter.canonical_hash(body) != index.get("catalog_hash"):
        raise PublishRefused("catalog_hash de index.json no coincide con su contenido")
    files = {"index.json": index_path}
    for b in index["brands"]:
        files[b["path"]] = out / b["path"]
    for m in index["models"]:
        files[m["path"]] = out / m["path"]
    for name in exporter.SCHEMA_COPIES:
        files[f"schema/{name}"] = out / "schema" / name
    missing = [rel for rel, p in files.items() if not p.exists()]
    if missing:
        raise PublishRefused(f"faltan {len(missing)} archivos del export, p. ej. {missing[0]}")
    keys = []
    for m in index["models"]:
        doc = json.loads(files[m["path"]].read_text(encoding="utf-8"))
        if doc["identity"]["content_hash"] != m["content_hash"] or exporter.model_content_hash(doc) != m["content_hash"]:
            raise PublishRefused(f"content_hash inconsistente en {m['path']}")
        if doc["schema_version"] != index["schema_version"]:
            raise PublishRefused(f"schema_version distinta en {m['path']}")
        for ref in _reference_strings(doc):
            if LOCAL_PATH.search(ref):
                raise PublishRefused(f"{m['path']}: valor que parece una ruta local ({ref[:40]})")
        keys += [(m["path"], im["key"]) for im in doc["images"]]
    return {"index": index, "files": files, "image_keys": keys}


def catalog_prefix(index: dict) -> str:
    return f"catalog/{index['schema_version']}/{index['catalog_hash']}"


def plan(export: dict) -> list[dict]:
    """Objetos a subir (sin el puntero): clave, ruta local, sha256, bytes."""
    prefix = catalog_prefix(export["index"])
    items = []
    for rel, path in sorted(export["files"].items()):
        data = path.read_bytes()
        items.append({"key": f"{prefix}/{rel}", "path": path, "sha256": _sha(data), "bytes": len(data)})
    return items


def pointer_document(index: dict) -> dict:
    return {"schema_version": index["schema_version"], "content_hash": index["catalog_hash"],
            "generated_at": index["generated_at"], "path": f"{catalog_prefix(index)}/index.json"}


def pointer_bytes(index: dict) -> bytes:
    return (json.dumps(pointer_document(index), ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _put_immutable(client, item: dict) -> str:
    """'skipped' | 'uploaded'. Nunca pisa una clave existente con otro contenido."""
    head = r2._with_retry(client.head_object, item["key"])
    if head is not None:
        if head.get("sha256") == item["sha256"]:
            return "skipped"
        raise r2.R2Error("la clave ya existe con otro contenido; no se pisa")
    r2._with_retry(client.put_object, item["key"], item["path"].read_bytes(), JSON_TYPE, IMMUTABLE_CACHE,
                   {"sha256": item["sha256"]})
    return "uploaded"


def _now_stamp(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")


def _archive_current_pointer(client, new_sha: str, now: datetime | None = None):
    """Lee catalog/latest.json (get_object) y, si existe, guarda una copia INMUTABLE en
    catalog/history/{fecha UTC}-{hash}.json antes de sobrescribirlo.

    Devuelve (estado, clave): 'absent' (no habia puntero), 'same' (ya es el nuevo: nada que hacer) o 'archived'.
    Si la lectura o la copia fallan, lanza R2Error y el puntero NO se sobrescribe.
    """
    old = r2._with_retry(client.get_object, LATEST_KEY)
    if old is None:
        return "absent", None
    if _sha(old) == new_sha:
        return "same", None
    try:
        old_hash = json.loads(old.decode("utf-8")).get("content_hash")
    except (ValueError, AttributeError):
        old_hash = None
    if not (isinstance(old_hash, str) and re.fullmatch(r"[0-9a-f]{64}", old_hash)):
        old_hash = _sha(old)  # puntero ilegible: se archiva igual, con el hash de sus bytes
    key = f"{HISTORY_PREFIX}/{_now_stamp(now)}-{old_hash}.json"
    if r2._with_retry(client.head_object, key) is not None:
        raise r2.R2Error("la copia historica ya existe; no se pisa")
    r2._with_retry(client.put_object, key, old, JSON_TYPE, IMMUTABLE_CACHE, {"sha256": _sha(old)})
    return "archived", key


def _put_pointer(client, index: dict, now: datetime | None = None) -> str:
    """Unica escritura sobre una clave mutable: LATEST_KEY, con la clave fija.

    Antes de sobrescribirla guarda una copia inmutable del puntero anterior; si esa copia falla, aborta.
    """
    data = pointer_bytes(index)
    sha = _sha(data)
    state, _ = _archive_current_pointer(client, sha, now)
    if state == "same":
        return "skipped"
    r2._with_retry(client.put_object, LATEST_KEY, data, JSON_TYPE, POINTER_CACHE, {"sha256": sha})
    return "uploaded"


def rollback(client, content_hash: str, schema_version: str | None = None, apply: bool = False,
             now: datetime | None = None) -> dict:
    """Apunta catalog/latest.json a un catalogo ya publicado. Simulacion por defecto; nunca borra.

    Verifica que catalog/{schema_version}/{hash}/index.json exista en R2 y sea coherente (catalog_hash,
    schema_version, preview false). Con apply, archiva el puntero actual (catalog/history/) y luego escribe el nuevo.
    """
    schema_version = schema_version or exporter.SCHEMA_VERSION
    rep = {"hash": content_hash, "schema_version": schema_version, "apply": apply, "ok": False, "action": None,
           "errors": [], "archived": None, "previous_hash": None}
    if not re.fullmatch(r"[0-9a-f]{64}", content_hash or ""):
        rep["errors"].append("el hash debe tener 64 caracteres hexadecimales en minusculas")
        return rep
    prefix = f"catalog/{schema_version}/{content_hash}"
    raw = r2._with_retry(client.get_object, f"{prefix}/index.json")
    if raw is None:
        rep["errors"].append(f"no existe {prefix}/index.json en R2: no hay catalogo con ese hash")
        return rep
    try:
        index = json.loads(raw.decode("utf-8"))
    except ValueError:
        rep["errors"].append("el index.json de ese catalogo no es JSON valido")
        return rep
    if index.get("catalog_hash") != content_hash or index.get("schema_version") != schema_version:
        rep["errors"].append("el index.json no corresponde al hash o a la schema_version pedidos")
        return rep
    if index.get("preview") is not False:
        rep["errors"].append("ese catalogo tiene preview: true (o no lo declara): no se apunta latest.json ahi")
        return rep
    data = pointer_bytes(index)
    sha = _sha(data)
    current = r2._with_retry(client.get_object, LATEST_KEY)
    if current is not None:
        try:
            rep["previous_hash"] = json.loads(current.decode("utf-8")).get("content_hash")
        except ValueError:
            pass
    if current is not None and _sha(current) == sha:
        rep.update(ok=True, action="ya apunta ahi")
        return rep
    rep["ok"] = True
    if not apply:
        rep["action"] = "simulacion: se apuntaria latest.json ahi"
        return rep
    try:
        _state, key = _archive_current_pointer(client, sha, now)
        rep["archived"] = key
        r2._with_retry(client.put_object, LATEST_KEY, data, JSON_TYPE, POINTER_CACHE, {"sha256": sha})
    except r2.R2Error as exc:
        rep["ok"] = False
        rep["errors"].append(f"{exc}")
        return rep
    rep["action"] = "latest.json apunta ahora a ese catalogo"
    return rep


def publish_export(out: Path, apply: bool = False, verify_remote: bool = False, verify_images: bool = False,
                   client=None, workers: int = 8) -> dict:
    export = load_export(out)
    items = plan(export)
    rep = {"prefix": catalog_prefix(export["index"]), "files": len(items), "bytes": sum(i["bytes"] for i in items),
           "uploaded": 0, "skipped": 0, "errors": [], "pointer": None, "apply": apply,
           "images_checked": 0, "images_missing": []}
    if (apply or verify_remote or verify_images) and client is None:
        raise ValueError("se necesita un cliente R2")
    if verify_images:
        seen = {}
        for model_path, key in export["image_keys"]:
            seen.setdefault(key, model_path)

        def check(key):
            try:
                return key, r2._with_retry(client.head_object, key) is not None
            except r2.R2Error as exc:
                return key, str(exc)

        with ThreadPoolExecutor(max_workers=workers) as pool:
            for key, ok in pool.map(check, sorted(seen)):
                rep["images_checked"] += 1
                if ok is not True:
                    rep["images_missing"].append({"model": seen[key], "key": key, "error": None if ok is False else ok})
        if rep["images_missing"]:
            rep["errors"].append(f"{len(rep['images_missing'])} claves de imagen del export no existen en R2")
            return rep
    if not apply:
        if verify_remote:
            for it in items:
                head = r2._with_retry(client.head_object, it["key"])
                rep["skipped" if head is not None and head.get("sha256") == it["sha256"] else "uploaded"] += 1
            rep["uploaded"], rep["to_upload"] = 0, rep["uploaded"]
        return rep

    def one(item):
        try:
            return item["key"], _put_immutable(client, item)
        except r2.R2Error as exc:
            return item["key"], f"error: {exc}"

    # index.json al final de los inmutables y el puntero despues de todo
    first = [i for i in items if not i["key"].endswith("/index.json")]
    last = [i for i in items if i["key"].endswith("/index.json")]
    for group in (first, last):
        if rep["errors"]:
            break
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for key, res in pool.map(one, group):
                if res.startswith("error"):
                    rep["errors"].append(f"{key.split('/', 3)[-1]}: {res}")
                else:
                    rep[res] += 1
    if not rep["errors"]:
        try:
            rep["pointer"] = _put_pointer(client, export["index"])
        except r2.R2Error as exc:
            rep["errors"].append(f"{LATEST_KEY}: {exc}")
    return rep


def format_report(rep: dict) -> str:
    mode = "APPLY" if rep["apply"] else "dry-run: no se subio nada"
    lines = [f"Catalogo: {rep['prefix']}/  ({rep['files']} archivos, {rep['bytes'] / 1048576:.1f} MB)  [{mode}]"]
    if rep["apply"]:
        lines.append(f"Subidos: {rep['uploaded']} · ya existian: {rep['skipped']} · puntero {LATEST_KEY}: "
                     f"{rep['pointer'] or 'no se escribio'}")
    elif "to_upload" in rep:
        lines.append(f"Por subir: {rep['to_upload']} · ya existen: {rep['skipped']}")
    if rep["images_checked"]:
        lines.append(f"Claves de imagen verificadas en R2: {rep['images_checked']} · faltan: {len(rep['images_missing'])}")
        for m in rep["images_missing"][:10]:
            lines.append(f"  falta {m['key']} ({m['model']})")
    for e in rep["errors"]:
        lines.append(f"ERROR {e}")
    return "\n".join(lines)


def format_rollback(rep: dict) -> str:
    mode = "APPLY" if rep["apply"] else "simulacion: no se escribio nada"
    lines = [f"Rollback a catalog/{rep['schema_version']}/{rep['hash']}/  [{mode}]"]
    if rep["previous_hash"]:
        lines.append(f"Puntero actual: {rep['previous_hash']}")
    if rep["action"]:
        lines.append(rep["action"])
    if rep["archived"]:
        lines.append(f"Copia del puntero anterior: {rep['archived']}")
    lines += [f"ERROR {e}" for e in rep["errors"]]
    return "\n".join(lines)


def main_cli(args: list[str], root: Path) -> int:
    back = next((a.split("=", 1)[1] for a in args if a.startswith("--rollback=")), None)
    if back is not None:
        missing = r2.missing_credentials()
        if missing:
            print("ERROR faltan variables de entorno: " + ", ".join(missing))
            return 2
        try:
            client = r2.R2Client()
        except Exception as exc:  # noqa: BLE001
            print(f"ERROR no se pudo crear el cliente R2: {type(exc).__name__}")
            return 2
        schema_version = next((a.split("=", 1)[1] for a in args if a.startswith("--schema-version=")), None)
        rep = rollback(client, back.strip().lower(), schema_version, apply="--apply" in args)
        print(format_rollback(rep))
        return 0 if rep["ok"] else 1
    out = next((a.split("=", 1)[1] for a in args if a.startswith("--out=")), None)
    out = Path(out) if out else Path(root) / "dist" / "portal"
    apply, verify, verify_images = "--apply" in args, "--verify-remote" in args, "--verify-images" in args
    try:
        load_export(out)
    except PublishRefused as exc:
        print(f"ERROR no se publica: {exc}")
        return 2
    client = None
    if apply or verify or verify_images:
        missing = r2.missing_credentials()
        if missing:
            print("ERROR faltan variables de entorno: " + ", ".join(missing))
            return 2
        try:
            client = r2.R2Client()
        except Exception as exc:  # noqa: BLE001
            print(f"ERROR no se pudo crear el cliente R2: {type(exc).__name__}")
            return 2
    rep = publish_export(out, apply=apply, verify_remote=verify, verify_images=verify_images, client=client)
    print(format_report(rep))
    return 1 if rep["errors"] else 0


if __name__ == "__main__":
    sys.exit(main_cli(sys.argv[1:], exporter.ROOT))
