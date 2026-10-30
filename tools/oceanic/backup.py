"""Respaldo de fotos en R2 (python -m oceanic backup-photos / restore-photos).

    python -m oceanic backup-photos  <marca|ruta_modelo|--all> [--apply] [--verify] [--workers=N]
    python -m oceanic restore-photos <clave|marca/modelo|marca|--all> [--apply] [--force-restore-id=<clave>]

Garantias:
  * Cada imagen registrada en un r2.json se copia, dentro del bucket y sin descargarla (copy_object), a
    recoveryphotos/<la misma clave>, y a su lado se guarda una libreta de datos en recoveryphotos/_ledger/<clave>.json.
  * Solo copia y escribe objetos nuevos: nunca borra y nunca pisa un respaldo. Un respaldo que ya existe con el mismo
    sha256 se omite; con otro sha256 es un ERROR.
  * Simulacion por defecto (solo lectura del bucket: HEAD). --verify es solo lectura: existencia y tamano.
  * restore-photos devuelve una foto a su clave original SOLO si esa clave no existe; si existe con otro sha256 lo
    informa y exige --force-restore-id=<esa clave>. Verifica el sha256 contra la libreta antes y despues.
  * Las credenciales se leen del entorno (como publish-r2); nada de cuenta, bucket ni claves en la libreta ni en la salida.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from . import r2

BACKUP_PREFIX = "recoveryphotos"
LEDGER_PREFIX = "recoveryphotos/_ledger"
LEDGER_VERSION = 1
LEDGER_CACHE = "no-store"
REASON = "respaldo preventivo de la clave publicada"


def backup_key(key: str) -> str:
    return f"{BACKUP_PREFIX}/{key}"


def ledger_key(key: str) -> str:
    return f"{LEDGER_PREFIX}/{key}.json"


def agent_version(root: Path | None = None) -> str:
    """Commit corto del repositorio (solo lectura); 'desconocida' si no se puede leer."""
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=root or r2.mediarules.ROOT, capture_output=True,
                             text=True, timeout=20)
        return out.stdout.strip() or "desconocida"
    except Exception:  # noqa: BLE001
        return "desconocida"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def collect(models) -> list[dict]:
    """Entradas activas de los r2.json de los modelos, con los datos del inventario que van a la libreta."""
    items = []
    for md in map(Path, models):
        path = r2.state_path(md)
        if not path.is_file():
            continue
        inv_path = md / "05_MULTIMEDIA" / "IMAGENES" / "images.json"
        inv = {}
        if inv_path.is_file():
            inv = {r["id"]: r for r in json.loads(inv_path.read_text(encoding="utf-8")).get("images", [])}
        for e in json.loads(path.read_text(encoding="utf-8")).get("images", []):
            if e.get("superseded"):
                continue
            rec = inv.get(e["id"], {})
            orig = rec.get("original") or {}
            parts = e["key"].split("/")
            items.append({
                "brand": parts[0], "model": parts[1], "category": rec.get("category") or parts[2].upper(), "id": e["id"],
                "key": e["key"], "sha256": e["sha256"], "bytes": e["bytes"], "uploaded_at": e.get("uploaded_at"),
                "original_sha256": e.get("original_sha256") or orig.get("sha256"),
                "source_url": orig.get("fetched_from") or orig.get("url") or rec.get("source_url"),
            })
    return items


def ledger_document(item: dict, version: str, now: str, reason: str = REASON) -> dict:
    return {"ledger_version": LEDGER_VERSION, "brand": item["brand"], "model": item["model"],
            "category": item["category"], "id": item["id"], "key": item["key"], "backup_key": backup_key(item["key"]),
            "sha256": item["sha256"], "bytes": item["bytes"], "original_sha256": item["original_sha256"],
            "source_url": item["source_url"], "uploaded_at": item["uploaded_at"], "backed_up_at": now,
            "reason": reason, "agent_version": version}


def ledger_bytes(doc: dict) -> bytes:
    return (json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=True) + "\n").encode("utf-8")


def _new_report(apply: bool, verify: bool) -> dict:
    return {"apply": apply, "verify": verify, "eligible": 0, "already": 0, "to_copy": 0, "copied": 0, "ledgers": 0,
            "bytes": 0, "verified": 0, "errors": [], "brands": {}}


def _one(client, item: dict, apply: bool, verify: bool, version: str, now: str) -> dict:
    """Procesa una imagen. Devuelve {"outcome": ..., "error": ..., "ledger": bool}."""
    key, bkey, lkey = item["key"], backup_key(item["key"]), ledger_key(item["key"])
    try:
        src = r2._with_retry(client.head_object, key)
        if src is None:
            return {"outcome": "error", "error": f"{key}: la clave original no existe en R2"}
        if src.get("sha256") != item["sha256"]:
            return {"outcome": "error", "error": f"{key}: el original no coincide con r2.json (sha256)"}
        dst = r2._with_retry(client.head_object, bkey)
        led = r2._with_retry(client.head_object, lkey)
        if verify:
            problems = []
            if dst is None:
                problems.append("falta el respaldo")
            else:
                if dst.get("sha256") != item["sha256"]:
                    problems.append("el respaldo tiene otro sha256")
                if dst.get("size") is not None and src.get("size") is not None and dst["size"] != src["size"]:
                    problems.append("el respaldo tiene otro tamano")
                if dst.get("size") is not None and dst["size"] != item["bytes"]:
                    problems.append("el tamano no coincide con r2.json")
            if led is None:
                problems.append("falta la libreta")
            return {"outcome": "error" if problems else "verified",
                    "error": f"{key}: " + "; ".join(problems) if problems else None}
        if dst is not None and dst.get("sha256") != item["sha256"]:
            return {"outcome": "error", "error": f"{bkey}: el respaldo existe con otro sha256 (no se pisa)"}
        if dst is not None and led is not None:
            return {"outcome": "already"}
        if not apply:
            return {"outcome": "planned", "copy": dst is None, "ledger": led is None}
        if dst is None:
            r2._with_retry(client.copy_object, key, bkey)
            check = r2._with_retry(client.head_object, bkey)
            if check is None or check.get("sha256") != item["sha256"]:
                return {"outcome": "error", "error": f"{bkey}: la copia no coincide con el original (sha256)"}
        wrote_ledger = False
        if led is None:
            body = ledger_bytes(ledger_document(item, version, now))
            r2._with_retry(client.put_object, key=lkey, body=body, content_type="application/json",
                           cache_control=LEDGER_CACHE, metadata={"sha256": hashlib.sha256(body).hexdigest()})
            wrote_ledger = True
        return {"outcome": "copied", "copy": dst is None, "ledger": wrote_ledger}
    except Exception as exc:  # noqa: BLE001
        return {"outcome": "error", "error": f"{key}: {type(exc).__name__}: {r2._scrub(str(exc))}"}


def backup(models, apply: bool = False, verify: bool = False, client=None, workers: int = 8, version: str | None = None,
           now: str | None = None) -> dict:
    """Respalda (o simula, o verifica) las imagenes registradas de los modelos. Devuelve el informe."""
    if client is None:
        client = r2.R2Client()  # lanza MissingCredentials si faltan variables
    version, now = version or agent_version(), now or _now()
    items = collect(models)
    rep = _new_report(apply, verify)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        results = list(ex.map(lambda it: _one(client, it, apply and not verify, verify, version, now), items))
    for it, res in zip(items, results):
        b = rep["brands"].setdefault(it["brand"], {"eligible": 0, "already": 0, "to_copy": 0, "copied": 0, "bytes": 0,
                                                    "errors": 0})
        rep["eligible"] += 1
        b["eligible"] += 1
        out = res["outcome"]
        if out == "error":
            rep["errors"].append(res["error"])
            b["errors"] += 1
        elif out == "verified":
            rep["verified"] += 1
        elif out == "already":
            rep["already"] += 1
            b["already"] += 1
        elif out == "planned":
            rep["to_copy"] += 1
            b["to_copy"] += 1
            rep["bytes"] += it["bytes"] if res["copy"] else 0
            b["bytes"] += it["bytes"] if res["copy"] else 0
        else:  # copied
            rep["copied"] += 1
            b["copied"] += 1
            rep["ledgers"] += 1 if res["ledger"] else 0
            rep["bytes"] += it["bytes"] if res["copy"] else 0
            b["bytes"] += it["bytes"] if res["copy"] else 0
    return rep


# --------------------------------------------------------------------------- restaurar

def restore(keys, apply: bool = False, force: set | None = None, client=None) -> dict:
    """Devuelve cada clave desde recoveryphotos/ solo si no existe (o con --force-restore-id). Verifica contra la libreta."""
    if client is None:
        client = r2.R2Client()
    force = set(force or ())
    rep = {"apply": apply, "restored": 0, "planned": 0, "already": 0, "needs_force": [], "errors": []}
    for key in keys:
        try:
            raw = r2._with_retry(client.get_object, ledger_key(key))
            if raw is None:
                rep["errors"].append(f"{key}: no hay libreta de respaldo")
                continue
            led = json.loads(raw.decode("utf-8"))
            sha = led["sha256"]
            bkey = backup_key(key)
            data = r2._with_retry(client.get_object, bkey)
            if data is None:
                rep["errors"].append(f"{key}: no hay respaldo")
                continue
            if hashlib.sha256(data).hexdigest() != sha:   # verificacion ANTES: el respaldo coincide con la libreta
                rep["errors"].append(f"{key}: el respaldo no coincide con la libreta (sha256)")
                continue
            cur = r2._with_retry(client.head_object, key)
            if cur is not None and cur.get("sha256") == sha:
                rep["already"] += 1
                continue
            if cur is not None and key not in force:
                rep["needs_force"].append(key)
                continue
            if not apply:
                rep["planned"] += 1
                continue
            r2._with_retry(client.copy_object, bkey, key)
            after = r2._with_retry(client.get_object, key)    # verificacion DESPUES
            if after is None or hashlib.sha256(after).hexdigest() != sha:
                rep["errors"].append(f"{key}: la restauracion no coincide con la libreta (sha256)")
                continue
            rep["restored"] += 1
        except Exception as exc:  # noqa: BLE001
            rep["errors"].append(f"{key}: {type(exc).__name__}: {r2._scrub(str(exc))}")
    return rep


# --------------------------------------------------------------------------- CLI

def _mb(n: int) -> str:
    return f"{n / 1048576:.1f} MB"


def format_backup(rep: dict) -> str:
    out = []
    for b, t in sorted(rep["brands"].items()):
        out.append(f"MARCA {b}: imagenes={t['eligible']} ya_respaldadas={t['already']} por_copiar={t['to_copy']} "
                   f"copiadas={t['copied']} errores={t['errors']} ({_mb(t['bytes'])})")
    out += [f"  ERROR {e}" for e in rep["errors"]]
    mode = "verificacion de solo lectura" if rep["verify"] else "" if rep["apply"] else "simulacion: no se escribio nada"
    out.append(f"TOTAL: imagenes={rep['eligible']} ya_respaldadas={rep['already']} por_copiar={rep['to_copy']} "
               f"copiadas={rep['copied']} libretas={rep['ledgers']} verificadas={rep['verified']} errores={len(rep['errors'])} "
               f"({_mb(rep['bytes'])})" + (f"  [{mode}]" if mode else ""))
    return "\n".join(out)


def main_backup(args: list[str], root: Path) -> int:
    apply, verify = "--apply" in args, "--verify" in args
    workers = next((int(a.split("=", 1)[1]) for a in args if a.startswith("--workers=")), 8)
    if apply and verify:
        print("ERROR --verify es de solo lectura: no se combina con --apply")
        return 2
    try:
        targets = r2._resolve_targets([a for a in args if a not in ("--apply", "--verify")], Path(root))
    except ValueError as exc:
        print(f"ERROR {exc}")
        return 2
    missing = r2.missing_credentials()
    if missing:
        print("ERROR faltan variables de entorno: " + ", ".join(missing))
        return 2
    try:
        rep = backup(targets, apply=apply, verify=verify, workers=workers, version=agent_version(Path(root)))
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR no se pudo crear el cliente R2: {type(exc).__name__}")
        return 2
    print(format_backup(rep))
    return 1 if rep["errors"] else 0


def main_restore(args: list[str], root: Path) -> int:
    apply = "--apply" in args
    force = {a.split("=", 1)[1] for a in args if a.startswith("--force-restore-id=")}
    rest = [a for a in args if a != "--apply" and not a.startswith("--force-restore-id=")]
    pos = [a for a in rest if not a.startswith("--")]
    try:
        if len(pos) == 1 and pos[0].endswith(".webp"):
            keys = [pos[0]]
        else:
            keys = [i["key"] for i in collect(r2._resolve_targets(rest, Path(root)))]
    except ValueError as exc:
        print(f"ERROR {exc}")
        return 2
    missing = r2.missing_credentials()
    if missing:
        print("ERROR faltan variables de entorno: " + ", ".join(missing))
        return 2
    try:
        rep = restore(keys, apply=apply, force=force)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR no se pudo crear el cliente R2: {type(exc).__name__}")
        return 2
    print(f"claves={len(keys)} restauradas={rep['restored']} por_restaurar={rep['planned']} ya_en_su_sitio={rep['already']} "
          f"requieren_force={len(rep['needs_force'])} errores={len(rep['errors'])}"
          + ("" if apply else "  [simulacion: no se escribio nada]"))
    for k in rep["needs_force"]:
        print(f"  EXISTE con otro sha256: {k} (usa --force-restore-id={k} para pisarla)")
    for e in rep["errors"]:
        print(f"  ERROR {e}")
    return 1 if (rep["errors"] or rep["needs_force"]) else 0


if __name__ == "__main__":
    sys.exit(main_backup(sys.argv[1:], r2.mediarules.ROOT))
