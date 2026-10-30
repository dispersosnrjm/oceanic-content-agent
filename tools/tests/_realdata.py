"""Copia temporal de lo que algunas pruebas leen de la biblioteca real.

Las pruebas que usan datos reales (export de los 148 modelos, inventarios, el paquete piloto) leian el arbol de trabajo:
si otro proceso del agente escribia un images.json, un r2.json o un readiness.json mientras corrian, fallaban de forma
intermitente (JSONDecodeError). Ahora leen una copia hecha una sola vez por proceso, sin las .webp (que ninguna prueba
necesita y pesan unos 300 MB), con biblioteca/, schema/ y marcas/. La copia se verifica entera (cada .json se lee completo);
si una escritura concurrente dejo algun archivo a medias, se vuelve a copiar. Se borra al terminar. Ninguna prueba toca ya
el arbol real.
"""

from __future__ import annotations

import atexit
import json
import shutil
import tempfile
import time
from pathlib import Path

REAL = Path(__file__).resolve().parents[2]
SUBDIRS = ("biblioteca", "schema", "marcas")
IGNORE = shutil.ignore_patterns("*.webp", "*.tmp*", "__pycache__")
_root: Path | None = None


def _copy(dst: Path) -> None:
    for name in SUBDIRS:
        src = REAL / name
        if src.exists():
            shutil.copytree(src, dst / name, ignore=IGNORE)


def unreadable_json(root: Path) -> list[str]:
    """Rutas (relativas) de los .json de la copia que no se pueden leer completos."""
    bad = []
    for p in root.rglob("*.json"):
        try:
            json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            bad.append(str(p.relative_to(root)))
    return bad


def snapshot_root(attempts: int = 5) -> Path:
    """Raiz de la copia temporal (se crea la primera vez que se pide, una por proceso)."""
    global _root
    if _root is not None:
        return _root
    last: list[str] = []
    for i in range(attempts):
        tmp = Path(tempfile.mkdtemp(prefix="oceanic-tests-"))
        try:
            _copy(tmp)
            last = unreadable_json(tmp)
            if not last:
                atexit.register(shutil.rmtree, tmp, True)
                _root = tmp
                return tmp
        except OSError:  # un archivo desaparecio o cambio mientras se copiaba: se vuelve a empezar
            last = ["error de copia"]
        shutil.rmtree(tmp, ignore_errors=True)
        time.sleep(0.5 * (i + 1))
    raise RuntimeError("no se pudo hacer una copia consistente de la biblioteca (¿hay otro comando escribiendo?): "
                       + ", ".join(last[:5]))
