"""Publicacion de imagenes a Cloudflare R2 (compatible S3).

    python -m oceanic publish-r2 <ruta_modelo|marca|--all> [--apply] [--verify-remote]

Garantias:
  * Dry-run por defecto: sin red ni credenciales.
  * Solo lectura y escritura de objetos nuevos: el cliente expone SOLO head_object y put_object.
    Una clave existente con el mismo sha256 se omite; con otro sha256 es un error y no se pisa.
  * Credenciales solo por variables de entorno; nunca se imprimen ni se guardan (ni valores, ni
    endpoint, ni bucket). r2.json no guarda cuenta ni bucket.
  * Idempotente y reanudable: r2.json por modelo (escritura atomica) y, si se pide, HEAD remoto.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from . import mediarules

ENV_VARS = ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET")
CONTENT_TYPE = "image/webp"
CACHE_CONTROL = "public, max-age=31536000, immutable"
SAVE_EVERY = 25
RETRIES = 4
BACKOFF = 0.5  # segundos; se duplica en cada reintento
_sleep = time.sleep
STATE_VERSION = 1

SKIP_REASONS = ("sin_archivo", "FAILED", "PENDING", "DUPLICATE_OF", "requires_manual_download",
                "corrupta", "categoria_invalida", "id_invalido", "otro")


class R2Error(Exception):
    """Error permanente (el mensaje nunca incluye credenciales, endpoint ni bucket)."""


class R2Transient(R2Error):
    """Error transitorio: se reintenta con backoff."""


class MissingCredentials(Exception):
    def __init__(self, names):
        self.names = list(names)
        super().__init__("faltan variables de entorno: " + ", ".join(self.names))


def missing_credentials(env=None) -> list[str]:
    env = os.environ if env is None else env
    return [n for n in ENV_VARS if not env.get(n)]


def _scrub(text: str, env=None) -> str:
    env = os.environ if env is None else env
    for n in ENV_VARS:
        v = env.get(n)
        if v and len(v) >= 3:
            text = text.replace(v, "***")
    return re.sub(r"[\w.-]*r2\.cloudflarestorage\.com", "***", text)


def put_headers(sha256_hex: str) -> dict:
    return {"content_type": CONTENT_TYPE, "cache_control": CACHE_CONTROL, "metadata": {"sha256": sha256_hex}}


_TRANSIENT_CODES = {"RequestTimeout", "RequestTimeoutException", "Throttling", "ThrottlingException",
                    "SlowDown", "InternalError", "ServiceUnavailable", "RequestLimitExceeded", "TooManyRequests"}
_TRANSIENT_NAMES = {"EndpointConnectionError", "ConnectTimeoutError", "ReadTimeoutError", "ConnectionClosedError",
                    "ConnectionError", "ProxyConnectionError"}


class R2Client:
    """Envoltorio minimo de boto3: SOLO head_object y put_object."""

    def __init__(self, env=None):
        env = os.environ if env is None else env
        missing = missing_credentials(env)
        if missing:
            raise MissingCredentials(missing)
        import boto3  # importacion perezosa
        from botocore.config import Config
        self._bucket = env["R2_BUCKET"]
        self._s3 = boto3.client(
            "s3",
            endpoint_url=f"https://{env['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com",
            aws_access_key_id=env["R2_ACCESS_KEY_ID"],
            aws_secret_access_key=env["R2_SECRET_ACCESS_KEY"],
            region_name="auto",
            config=Config(retries={"max_attempts": 1}, signature_version="s3v4"),
        )

    @staticmethod
    def _wrap(exc: Exception) -> R2Error:
        resp = getattr(exc, "response", None) or {}
        code = str((resp.get("Error") or {}).get("Code", ""))
        status = (resp.get("ResponseMetadata") or {}).get("HTTPStatusCode") or 0
        name = type(exc).__name__
        msg = f"{name} {code} {status}".strip()
        transient = (code in _TRANSIENT_CODES or status >= 500 or name in _TRANSIENT_NAMES
                     or isinstance(exc, (TimeoutError, ConnectionError)))
        return (R2Transient if transient else R2Error)(msg)

    def head_object(self, key: str):
        """None si no existe; si existe, {"sha256": <metadata o None>}."""
        try:
            r = self._s3.head_object(Bucket=self._bucket, Key=key)
        except Exception as exc:  # noqa: BLE001
            resp = getattr(exc, "response", None) or {}
            code = str((resp.get("Error") or {}).get("Code", ""))
            status = (resp.get("ResponseMetadata") or {}).get("HTTPStatusCode")
            if code in ("404", "NoSuchKey", "NotFound") or status == 404:
                return None
            raise self._wrap(exc) from None
        return {"sha256": (r.get("Metadata") or {}).get("sha256")}

    def put_object(self, key: str, body: bytes, content_type: str, cache_control: str, metadata: dict):
        try:
            self._s3.put_object(Bucket=self._bucket, Key=key, Body=body, ContentType=content_type,
                                CacheControl=cache_control, Metadata=metadata)
        except Exception as exc:  # noqa: BLE001
            raise self._wrap(exc) from None


def _with_retry(fn, *args, **kwargs):
    for attempt in range(RETRIES):
        try:
            return fn(*args, **kwargs)
        except R2Transient:
            if attempt == RETRIES - 1:
                raise
            _sleep(BACKOFF * (2 ** attempt))


# --------------------------------------------------------------------------- estado r2.json

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _write_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp{os.getpid()}")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(data, ensure_ascii=False, indent=1) + "\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def is_curated(model_dir: Path) -> bool:
    mj = model_dir / "00_MODELO" / "model.json"
    try:
        return bool(json.loads(mj.read_text(encoding="utf-8")).get("curated"))
    except (OSError, ValueError):
        return False


def state_path(model_dir: Path) -> Path:
    if is_curated(model_dir):
        return model_dir.parent / "_r2" / f"{model_dir.name}.json"
    return model_dir / "05_MULTIMEDIA" / "IMAGENES" / "r2.json"


class State:
    def __init__(self, path: Path):
        self.path = path
        self.entries: list[dict] = []
        self.dirty = False
        if path.exists():
            self.entries = json.loads(path.read_text(encoding="utf-8")).get("images", [])

    def active(self, key: str):
        return next((e for e in self.entries if e["key"] == key and not e.get("superseded")), None)

    def record(self, image_id: str, key: str, sha: str, size: int, source: str = "put") -> None:
        for e in self.entries:
            if e["id"] == image_id and e["key"] != key and not e.get("superseded"):
                e["superseded"] = True  # la clave vieja queda en R2 (no se toca)
                e["superseded_at"] = _now()
        if not self.active(key):
            self.entries.append({"id": image_id, "key": key, "sha256": sha, "bytes": size,
                                 "uploaded_at": _now(), **({"source": source} if source != "put" else {})})
        self.dirty = True

    def save(self) -> None:
        if self.dirty:
            _write_atomic(self.path, {"version": STATE_VERSION, "images": self.entries})
            self.dirty = False


# --------------------------------------------------------------------------- auditoria y plan

def _audit(model_dir: Path, rec: dict) -> dict:
    """Devuelve {"status": ok|distinta|corrupta|ausente, "sha256", "bytes", "data"}."""
    path = model_dir / rec["file"]
    if not path.is_file():
        return {"status": "ausente"}
    data = path.read_bytes()
    try:
        from PIL import Image
        with Image.open(io.BytesIO(data)) as im:
            if im.format != "WEBP":
                return {"status": "corrupta", "detail": f"formato {im.format}"}
            im.load()
            size = im.size
    except Exception as exc:  # noqa: BLE001
        return {"status": "corrupta", "detail": type(exc).__name__}
    sha = hashlib.sha256(data).hexdigest()
    same = (sha == rec.get("sha256") and size == (rec.get("width"), rec.get("height")))
    return {"status": "ok" if same else "distinta", "sha256": sha, "bytes": len(data), "data": data,
            "size": size}


def _skip_reason(rec: dict) -> str:
    st = str(rec.get("download_status") or "")
    if rec.get("requires_manual_download") or "manual" in st.lower():
        return "requires_manual_download"
    if st.startswith("DUPLICATE_OF"):
        return "DUPLICATE_OF"
    if st.startswith("FAILED"):
        return "FAILED"
    if st.startswith("PENDING"):
        return "PENDING"
    if rec.get("file"):
        return "sin_archivo"
    return "otro"


def _model_report(model_dir: Path) -> dict:
    return {"brand": model_dir.parent.name, "model": model_dir.name, "eligible": 0, "audit":
            {"ok": 0, "distinta": 0, "corrupta": 0, "ausente": 0}, "skipped": {}, "already": 0,
            "to_upload": 0, "uploaded": 0, "bytes": 0, "errors": [], "warnings": [], "keys": []}


def _process_model(model_dir: Path, apply: bool, verify_remote: bool, client, workers: int) -> dict:
    rep = _model_report(model_dir)
    brand, model = rep["brand"], rep["model"]
    inv_path = model_dir / "05_MULTIMEDIA" / "IMAGENES" / "images.json"
    if not inv_path.is_file():
        rep["errors"].append("sin images.json")
        return rep
    images = json.loads(inv_path.read_text(encoding="utf-8")).get("images", [])
    state = State(state_path(model_dir))

    def skip(reason):
        rep["skipped"][reason] = rep["skipped"].get(reason, 0) + 1

    cands = []
    for rec in images:
        if rec.get("scope") != "THIS_MODEL":
            continue
        if not rec.get("file"):
            skip(_skip_reason(rec))
            continue
        cands.append(rec)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        audits = list(ex.map(lambda r: _audit(model_dir, r), cands))

        todo = []
        for rec, au in zip(cands, audits):
            st = au["status"]
            if st == "ausente":
                rep["audit"]["ausente"] += 1
                skip("sin_archivo")
                continue
            rep["eligible"] += 1
            rep["audit"][st] += 1
            if st == "corrupta":
                skip("corrupta")
                rep["errors"].append(f"{rec['id']}: copia corrupta ({au.get('detail')})")
                continue
            if st == "distinta":
                rep["warnings"].append(f"{rec['id']}: copia distinta al inventario; se sube con su hash real")
            try:
                key = mediarules.image_key(brand, model, rec.get("category"), rec["id"], au["sha256"])
            except ValueError as exc:
                reason = "categoria_invalida" if "categoria" in str(exc) else "id_invalido"
                skip(reason)
                rep["errors"].append(f"{rec['id']}: {exc}")
                continue
            rep["keys"].append(key)
            act = state.active(key)
            if act and act["sha256"] == au["sha256"] and not verify_remote:
                rep["already"] += 1
                continue
            todo.append((rec, au, key))

        remote = apply or verify_remote

        def work(item):
            rec, au, key = item
            try:
                head = _with_retry(client.head_object, key) if remote else None
                if head is not None:
                    if head.get("sha256") == au["sha256"]:
                        return item, "exists", None
                    return item, "error", "la clave existe con otro sha256 (no se pisa)"
                if not apply:
                    return item, "planned", None
                _with_retry(client.put_object, key=key, body=au["data"], **put_headers(au["sha256"]))
                return item, "uploaded", None
            except Exception as exc:  # noqa: BLE001
                return item, "error", f"{type(exc).__name__}: {_scrub(str(exc))}"

        pending_saves = 0
        try:
            for item, outcome, err in ex.map(work, todo):
                rec, au, key = item
                if outcome == "error":
                    rep["errors"].append(f"{rec['id']}: {err}")
                elif outcome == "planned":
                    rep["to_upload"] += 1
                    rep["bytes"] += au["bytes"]
                elif outcome == "exists":
                    rep["already"] += 1
                    if apply:
                        state.record(rec["id"], key, au["sha256"], au["bytes"], source="head")
                else:
                    rep["uploaded"] += 1
                    rep["bytes"] += au["bytes"]
                    state.record(rec["id"], key, au["sha256"], au["bytes"])
                    pending_saves += 1
                    if pending_saves >= SAVE_EVERY:
                        state.save()
                        pending_saves = 0
        finally:
            if apply:
                state.save()
    return rep


# --------------------------------------------------------------------------- API publica

def publish(models, apply: bool = False, verify_remote: bool = False, client=None, workers: int = 8) -> dict:
    """Publica (o simula) las imagenes de los modelos. Devuelve el informe completo."""
    if (apply or verify_remote) and client is None:
        client = R2Client()  # lanza MissingCredentials si faltan variables
    reports = [_process_model(Path(m), apply, verify_remote, client, workers) for m in models]
    brands: dict[str, dict] = {}
    for r in reports:
        b = brands.setdefault(r["brand"], {"models": 0, "eligible": 0, "already": 0, "to_upload": 0,
                                           "uploaded": 0, "bytes": 0, "errors": 0})
        b["models"] += 1
        for k in ("eligible", "already", "to_upload", "uploaded", "bytes"):
            b[k] += r[k]
        b["errors"] += len(r["errors"])
    total = {k: sum(b[k] for b in brands.values())
             for k in ("models", "eligible", "already", "to_upload", "uploaded", "bytes", "errors")}
    return {"apply": apply, "verify_remote": verify_remote, "models": reports, "brands": brands, "total": total}


def _resolve_targets(args: list[str], root: Path) -> list[Path]:
    biblioteca = root / "biblioteca"
    if "--all" in args:
        return sorted(p.parent.parent for p in biblioteca.glob("*/*/00_MODELO/00_MODELO.md"))
    pos = [a for a in args if not a.startswith("--")]
    if len(pos) != 1:
        raise ValueError("indica <ruta_modelo|marca|--all>")
    t = pos[0]
    for cand in (Path(t), root / t, biblioteca / t):
        if (cand / "00_MODELO").is_dir():
            return [cand]
        if cand.is_dir() and cand.parent.name == "biblioteca":
            found = sorted(p.parent.parent for p in cand.glob("*/00_MODELO/00_MODELO.md"))
            if found:
                return found
    raise ValueError(f"no se encontro modelo ni marca: {t}")


def _mb(n: int) -> str:
    return f"{n / 1048576:.1f} MB"


def format_report(rep: dict) -> str:
    out = []
    for r in rep["models"]:
        sk = ", ".join(f"{k}={v}" for k, v in r["skipped"].items()) or "-"
        a = r["audit"]
        out.append(f"{r['brand']}/{r['model']}: elegibles={r['eligible']} (ok={a['ok']} distinta={a['distinta']} "
                   f"corrupta={a['corrupta']} ausente={a['ausente']}) ya_subidas={r['already']} "
                   f"{'subidas' if rep['apply'] else 'por_subir'}={r['uploaded'] if rep['apply'] else r['to_upload']} "
                   f"({_mb(r['bytes'])}) omitidas: {sk}")
        out += [f"  AVISO {w}" for w in r["warnings"]] + [f"  ERROR {e}" for e in r["errors"]]
    for b, t in sorted(rep["brands"].items()):
        out.append(f"MARCA {b}: modelos={t['models']} elegibles={t['eligible']} ya_subidas={t['already']} "
                   f"por_subir={t['to_upload']} subidas={t['uploaded']} errores={t['errors']} ({_mb(t['bytes'])})")
    t = rep["total"]
    out.append(f"TOTAL: modelos={t['models']} elegibles={t['eligible']} ya_subidas={t['already']} "
               f"por_subir={t['to_upload']} subidas={t['uploaded']} errores={t['errors']} ({_mb(t['bytes'])})"
               + ("" if rep["apply"] else "  [dry-run: no se escribio nada]"))
    return "\n".join(out)


def main_cli(args: list[str], root: Path) -> int:
    apply, verify = "--apply" in args, "--verify-remote" in args
    workers = next((int(a.split("=", 1)[1]) for a in args if a.startswith("--workers=")), 8)
    try:
        targets = _resolve_targets(args, Path(root))
    except ValueError as exc:
        print(f"ERROR {exc}")
        return 2
    client = None
    if apply or verify:
        missing = missing_credentials()
        if missing:
            print("ERROR faltan variables de entorno: " + ", ".join(missing))
            return 2
        try:
            client = R2Client()
        except Exception as exc:  # noqa: BLE001
            print(f"ERROR no se pudo crear el cliente R2: {type(exc).__name__}")
            return 2
    rep = publish(targets, apply=apply, verify_remote=verify, client=client, workers=workers)
    print(format_report(rep))
    return 1 if rep["total"]["errors"] else 0


if __name__ == "__main__":
    sys.exit(main_cli(sys.argv[1:], mediarules.ROOT))
