"""Lint de texto para el contenido Oceanic (API pura, sin E/S de modelos).

Las reglas viven en schema/text-lint.json (formato documentado en su campo "_doc").
"""
import json
import re
from pathlib import Path

SCOPES = ("texts", "headlines", "short_descriptions", "brand")
RULE_TYPES = ("literal", "regex")
SEVERITIES = ("error", "warn")
FLAG_BITS = {"i": re.IGNORECASE, "m": re.MULTILINE, "s": re.DOTALL, "x": re.VERBOSE}
EXCERPT_MAX = 120
DEFAULT_RULES = Path(__file__).resolve().parents[2] / "schema" / "text-lint.json"

# Nota editorial interna en cursiva entre paréntesis: *( ... )*
INTERNAL_NOTE_RE = re.compile(r"[ \t]*\*\([^*\n]*\)\*")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _flags(flags, rid):
    value = 0
    for ch in flags or "":
        if ch not in FLAG_BITS:
            raise ValueError(f"Regla '{rid}': flag desconocido '{ch}' (válidos: i, m, s, x)")
        value |= FLAG_BITS[ch]
    return value


def _validate(rules):
    if not isinstance(rules, dict) or not isinstance(rules.get("rules"), list):
        raise ValueError("Reglas de lint: se esperaba un objeto con la lista 'rules'")
    if rules.get("version") != 1:
        raise ValueError("Reglas de lint: 'version' debe ser 1")
    seen = set()
    for i, rule in enumerate(rules["rules"]):
        if not isinstance(rule, dict):
            raise ValueError(f"Regla #{i + 1}: debe ser un objeto")
        rid = rule.get("id")
        if not isinstance(rid, str) or not rid:
            raise ValueError(f"Regla #{i + 1}: falta 'id'")
        if rid in seen:
            raise ValueError(f"Regla '{rid}': id duplicado")
        seen.add(rid)
        if rule.get("type") not in RULE_TYPES:
            raise ValueError(f"Regla '{rid}': 'type' debe ser literal o regex")
        pattern = rule.get("pattern")
        if not isinstance(pattern, str) or not pattern:
            raise ValueError(f"Regla '{rid}': falta 'pattern'")
        if rule.get("severity") not in SEVERITIES:
            raise ValueError(f"Regla '{rid}': 'severity' debe ser error o warn")
        if not isinstance(rule.get("message"), str) or not rule["message"]:
            raise ValueError(f"Regla '{rid}': falta 'message'")
        if rule["type"] == "regex":
            try:
                re.compile(pattern, _flags(rule.get("flags"), rid))
            except re.error as exc:
                raise ValueError(f"Regla '{rid}': expresión regular inválida ({exc})") from exc
        elif rule.get("flags"):
            raise ValueError(f"Regla '{rid}': 'flags' solo aplica a type regex")
        scope = rule.get("scope")
        if scope is not None:
            if not isinstance(scope, list) or not scope:
                raise ValueError(f"Regla '{rid}': 'scope' debe ser una lista no vacía")
            bad = [s for s in scope if s not in SCOPES]
            if bad:
                raise ValueError(f"Regla '{rid}': ámbito inválido {bad} (válidos: {', '.join(SCOPES)})")
    return rules


def load_rules(path=None):
    """Carga y valida las reglas (por defecto schema/text-lint.json)."""
    path = Path(path) if path else DEFAULT_RULES
    with open(path, encoding="utf-8") as fh:
        rules = json.load(fh)
    return _validate(rules)


def _excerpt(line, start, end):
    if len(line) <= EXCERPT_MAX:
        return line.strip()
    half = max((EXCERPT_MAX - (end - start)) // 2, 0)
    a = max(start - half, 0)
    return line[a:a + EXCERPT_MAX].strip()


def lint_text(text, scope, section, rules):
    """Devuelve violaciones: scope, section, rule, severity, line (1-based), excerpt."""
    if scope not in SCOPES:
        raise ValueError(f"Ámbito inválido '{scope}' (válidos: {', '.join(SCOPES)})")
    _validate(rules)
    out = []
    lines = (text or "").split("\n")
    for rule in rules["rules"]:
        if rule.get("scope") and scope not in rule["scope"]:
            continue
        if rule["type"] == "literal":
            rx = re.compile(re.escape(rule["pattern"]))
        else:
            rx = re.compile(rule["pattern"], _flags(rule.get("flags"), rule["id"]))
        for n, line in enumerate(lines, 1):
            for m in rx.finditer(line):
                out.append({"scope": scope, "section": section, "rule": rule["id"],
                            "severity": rule["severity"], "line": n,
                            "excerpt": _excerpt(line, m.start(), m.end())})
    out.sort(key=lambda v: v["line"])
    return out


def lint_ok(violations):
    return not any(v["severity"] == "error" for v in violations)


def _neighbor_sentence(before):
    """Oración que precede a la nota (dentro de su línea)."""
    line = before.split("\n")[-1].strip()
    if not line:
        return ""
    return _SENTENCE_SPLIT.split(line)[-1].strip()


def clean_internal_notes(text):
    """Quita notas internas *(...)*. Devuelve (texto_limpio, n_quitadas, [(nota, frase_vecina)])."""
    found = []
    for m in INTERNAL_NOTE_RE.finditer(text):
        found.append((m.group(0).strip(), _neighbor_sentence(text[:m.start()])))
    cleaned = INTERNAL_NOTE_RE.sub("", text)
    return cleaned, len(found), found
