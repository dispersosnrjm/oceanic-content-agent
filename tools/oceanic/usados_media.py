"""Fotos de usados: bajada desde oceanic.cl y subida a R2.

Solo para usados (decision Oceanic 2026-10-09): las URLs de las fotos son las que ya estan en cada `usados/<carpeta>/ficha.txt`
(`--- FOTOS (N) ---`). `usados-fotos` las baja de a una, con pausa, como copias web WebP (las de `tools/usados.py`) en
`usados/<carpeta>/fotos/NN.webp` (no versionadas). `publish-usados` las sube con la clave del contrato
`usados/{slug}/other/foto-NN-{sha256[:8]}.webp` (inmutable) y anota lo subido en `usados/<slug>.r2.json` (versionado).
El slug es el id del aviso en el sitio (`SLUGS`). Nunca borra ni pisa una clave.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import re
import sys
import time
from pathlib import Path

from . import mediarules
from .r2 import (
    R2Client,
    State,
    _scrub,
    _with_retry,
    encoder_info,
    missing_credentials,
    put_headers,
)

PAUSE = 1.5            # segundos entre peticiones a oceanic.cl
MAX_FAILURES = 10      # si fallan mas, la bajada se detiene y se avisa antes de subir
CATEGORY = "OTHER"     # los avisos no traen categorias: carpeta `other`

# carpeta de usados/ -> id del aviso en el sitio (slug de la clave). None = sin fotos publicadas (no se sube nada)
SLUGS = {
    "antares8": "beneteau-antares-8",
    "oceanis-41": "beneteau-oceanis-41",
    "lagoon39": "lagoon-39",
    "yate-isla-jechica-ii": "yate-isla-jechica-ii",
    "jeanneau-sun-oddyseay-349": "jeanneau-sun-odyssey-349",
    "barco20": "barco-20",
    "motorsailer-nauticat-44": "nauticat-44",
    "skeeta": "skeeta",
    "oyster56": "oyster-56",
    "aguantao": "aguantao",
    "oceanis473": "oceanis-47-3",
    "ola": "sin-fin",
}


def photo_urls(ficha: Path) -> list[str]:
    """URLs de la seccion `--- FOTOS (N) ---` de una ficha, en orden."""
    out, inside = [], False
    for line in ficha.read_text(encoding="utf-8").splitlines():
        if line.startswith("--- FOTOS"):
            inside = True
        elif line.startswith("---"):
            inside = False
        elif inside and re.match(r"https?://", line.strip()):
            out.append(line.strip())
    return out


def _usados_tool():
    """tools/usados.py (fetch con User-Agent propio y web_copy 1600 px q75), cargado por ruta."""
    path = Path(__file__).resolve().parent.parent / "usados.py"
    spec = importlib.util.spec_from_file_location("oceanic_usados_tool", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def download(root: Path, only: str | None = None, fetch=None, convert=None, pause: float = PAUSE,
             sleep=time.sleep, max_failures: int = MAX_FAILURES) -> dict:
    """Baja las fotos que falten. Secuencial, con pausa. Se detiene si fallan mas de `max_failures`."""
    if fetch is None or convert is None:
        tool = _usados_tool()
        fetch = fetch or (lambda u: tool.fetch(u, binary=True))
        convert = convert or tool.web_copy
    rep = {"folders": {}, "downloaded": 0, "existing": 0, "failed": [], "stopped": False}
    first = True
    for folder, slug in SLUGS.items():
        if only and folder not in (only, slug):
            continue
        ficha = root / "usados" / folder / "ficha.txt"
        if not ficha.is_file():
            continue
        urls = photo_urls(ficha)
        fd = root / "usados" / folder / "fotos"
        r = {"declared": len(urls), "downloaded": 0, "existing": 0, "failed": 0}
        rep["folders"][folder] = r
        for i, url in enumerate(urls, 1):
            dest = fd / f"{i:02d}.webp"
            if dest.exists():
                r["existing"] += 1
                rep["existing"] += 1
                continue
            if not first:
                sleep(pause)
            first = False
            try:
                data = convert(fetch(url))
                fd.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
                r["downloaded"] += 1
                rep["downloaded"] += 1
            except Exception as exc:  # noqa: BLE001 - se informa la URL y el tipo de error
                r["failed"] += 1
                rep["failed"].append({"folder": folder, "n": i, "url": url, "error": f"{type(exc).__name__}: {exc}"})
                if len(rep["failed"]) > max_failures:
                    rep["stopped"] = True
                    return rep
    return rep


def _audit(path: Path):
    data = path.read_bytes()
    from PIL import Image
    with Image.open(io.BytesIO(data)) as im:
        if im.format != "WEBP":
            raise ValueError(f"formato {im.format}")
        im.load()
        size = im.size
    return data, hashlib.sha256(data).hexdigest(), size


def publish(root: Path, only: str | None = None, apply: bool = False, client=None) -> dict:
    """Sube las fotos de usados a R2 (simulacion por defecto). Idempotente: si la clave existe con el mismo sha256, no sube."""
    rep = {"slugs": {}, "uploaded": 0, "already": 0, "to_upload": 0, "bytes": 0, "errors": []}
    for folder, slug in SLUGS.items():
        if only and only not in (folder, slug):
            continue
        fd = root / "usados" / folder / "fotos"
        files = sorted(fd.glob("*.webp")) if fd.is_dir() else []
        if not files:
            continue
        state = State(root / "usados" / f"{slug}.r2.json")
        r = {"photos": len(files), "uploaded": 0, "already": 0, "to_upload": 0, "keys": []}
        rep["slugs"][slug] = r
        enc = encoder_info() if apply else None
        for f in files:
            image_id = f"foto-{f.stem}"
            try:
                data, sha, _ = _audit(f)
                key = mediarules.usados_key(slug, CATEGORY, image_id, sha)
            except Exception as exc:  # noqa: BLE001
                rep["errors"].append(f"{slug}/{f.name}: {type(exc).__name__}: {exc}")
                continue
            r["keys"].append(key)
            try:
                head = _with_retry(client.head_object, key) if client is not None else None
                if head is not None:
                    if head.get("sha256") != sha:
                        rep["errors"].append(f"{key}: la clave existe con otro sha256 (no se pisa)")
                        continue
                    r["already"] += 1
                    rep["already"] += 1
                    if apply:
                        state.record(image_id, key, sha, len(data), source="head", encoder=enc)
                    continue
                if not apply:
                    r["to_upload"] += 1
                    rep["to_upload"] += 1
                    rep["bytes"] += len(data)
                    continue
                _with_retry(client.put_object, key=key, body=data, **put_headers(sha))
                state.record(image_id, key, sha, len(data), encoder=enc)
                r["uploaded"] += 1
                rep["uploaded"] += 1
                rep["bytes"] += len(data)
            except Exception as exc:  # noqa: BLE001
                rep["errors"].append(f"{key}: {type(exc).__name__}: {_scrub(str(exc))}")
        state.save()
    return rep


def format_download(rep: dict) -> str:
    out = [f"{f}: declaradas={r['declared']} bajadas={r['downloaded']} ya_estaban={r['existing']} fallidas={r['failed']}"
           for f, r in rep["folders"].items()]
    out.append(f"TOTAL bajadas={rep['downloaded']} ya_estaban={rep['existing']} fallidas={len(rep['failed'])}")
    for x in rep["failed"]:
        out.append(f"  FALLO {x['folder']} #{x['n']} {x['url']} {x['error']}")
    if rep["stopped"]:
        out.append(f"DETENIDO: fallaron mas de {MAX_FAILURES} fotos; revisar antes de subir")
    return "\n".join(out)


def format_publish(rep: dict, apply: bool) -> str:
    out = [f"{s}: fotos={r['photos']} subidas={r['uploaded']} ya_estaban={r['already']} por_subir={r['to_upload']}"
           for s, r in rep["slugs"].items()]
    out.append(f"TOTAL subidas={rep['uploaded']} ya_estaban={rep['already']} por_subir={rep['to_upload']} "
               f"errores={len(rep['errors'])} ({rep['bytes'] / 1048576:.1f} MB)" + ("" if apply else "  [dry-run: no se escribio nada]"))
    out += [f"  ERROR {e}" for e in rep["errors"]]
    return "\n".join(out)


def _only(args: list[str]) -> str | None:
    return next((a.split("=", 1)[1] for a in args if a.startswith("--only=")), None)


def main_download(args: list[str], root: Path) -> int:
    rep = download(Path(root), only=_only(args))
    print(format_download(rep))
    return 3 if rep["stopped"] else (1 if rep["failed"] else 0)


def main_publish(args: list[str], root: Path) -> int:
    apply, verify = "--apply" in args, "--verify-remote" in args
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
    rep = publish(Path(root), only=_only(args), apply=apply, client=client)
    print(format_publish(rep, apply))
    return 1 if rep["errors"] else 0


if __name__ == "__main__":
    sys.exit(main_publish(sys.argv[1:], mediarules.ROOT))
