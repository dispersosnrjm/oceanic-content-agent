"""Reglas de medios compartidas: todo sale de schema/media-rules.json (fuente unica)."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RULES_PATH = ROOT / "schema" / "media-rules.json"


@lru_cache(maxsize=1)
def rules() -> dict:
    return json.loads(RULES_PATH.read_text(encoding="utf-8"))


def _pat(name: str) -> re.Pattern:
    return re.compile(rules()["patterns"][name])


def categories() -> dict:
    return {c["key"]: c for c in rules()["categories"]}


def _category(cat: str) -> dict:
    try:
        return categories()[cat]
    except KeyError:
        raise ValueError(f"categoria invalida: {cat!r}") from None


def category_folder(cat: str) -> str:
    return _category(cat)["folder"]


def valid_slug(s) -> bool:
    return isinstance(s, str) and bool(_pat("slug").match(s))


def valid_filename(s) -> bool:
    return isinstance(s, str) and bool(_pat("file_name").match(s))


def valid_image_id(s) -> bool:
    return (isinstance(s, str) and bool(_pat("image_id").match(s))
            and len(s) <= rules()["patterns"]["image_id_max_length"])


def _sha8(sha256_hex: str) -> str:
    if not isinstance(sha256_hex, str) or not re.fullmatch(r"[0-9a-f]{64}", sha256_hex):
        raise ValueError("sha256 invalido (64 hex en minusculas)")
    return sha256_hex[:8]


def _check_id(image_id: str) -> None:
    if not valid_image_id(image_id):
        raise ValueError(f"id de imagen invalido: {image_id!r}")


def image_key(brand: str, model: str, category: str, image_id: str, sha256_hex: str) -> str:
    for s in (brand, model):
        if not valid_slug(s):
            raise ValueError(f"slug invalido: {s!r}")
    _check_id(image_id)
    return f"{brand}/{model}/{category_folder(category)}/{image_id}-{_sha8(sha256_hex)}.webp"


def usados_key(slug: str, category: str, image_id: str, sha256_hex: str) -> str:
    if not valid_slug(slug):
        raise ValueError(f"slug invalido: {slug!r}")
    _check_id(image_id)
    return f"usados/{slug}/{category_folder(category)}/{image_id}-{_sha8(sha256_hex)}.webp"


def alt_text(model: str, category: str) -> str:
    return f"{model}, {_category(category)['alt_label_es']}"


def is_hero(category: str, hero_rank) -> bool:
    """Hero = hero_rank no nulo (en cualquier categoria) o categoria HERO."""
    return hero_rank is not None or category == "HERO"


def max_edge(category: str, hero_rank=None) -> int:
    """Lado mayor maximo de la copia web (px)."""
    _category(category)
    edges = rules()["dimensions"]["max_long_edge_px"]
    return edges["hero" if is_hero(category, hero_rank) else "default"]


def run_test_vectors() -> list[str]:
    """Ejecuta los test_vectors del JSON. Devuelve la lista de fallos (vacia = todo bien)."""
    failures = []
    for v in rules()["test_vectors"]:
        name, inp = v["name"], v["input"]
        try:
            if "expect_key" in v:
                if "marca" in inp:
                    got = image_key(inp["marca"], inp["modelo"], inp["categoria"], inp["id"], inp["sha256"])
                else:
                    got = usados_key(inp["slug"], inp["categoria"], inp["id"], inp["sha256"])
                want = v["expect_key"]
            elif "expect_valid_image_id" in v:
                got, want = valid_image_id(inp), v["expect_valid_image_id"]
            elif "expect_valid" in v:
                got, want = valid_slug(inp), v["expect_valid"]
            elif "expect_max_edge" in v:
                got, want = max_edge(inp["categoria"], inp.get("hero_rank")), v["expect_max_edge"]
            elif "expect_alt" in v:
                got, want = alt_text(inp["modelo"], inp["categoria"]), v["expect_alt"]
            else:
                failures.append(f"{name}: vector sin expectativa conocida")
                continue
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{name}: {type(exc).__name__}: {exc}")
            continue
        if got != want:
            failures.append(f"{name}: esperado {want!r}, obtenido {got!r}")
    return failures
