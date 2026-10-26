# Cambios

Formato: [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/). Versionado semántico.

## [1.1.0]: integración con el portal

Rama `integracion-portal`. Cambios aditivos: no se reformateó ningún archivo existente y no se tocaron los paquetes curated,
`data/`, `dashboard/`, los builders ni los adaptadores.

### Añadido
- **Contrato con el portal** (versionado, fuente única):
  - `schema/media-rules.json`: categorías (con `label_es` para la interfaz y `alt_label_es` para el texto alternativo), formatos,
    dimensiones (2560 px portada, 1600 px el resto; hero = `hero_rank` no nulo o categoría HERO), WebP q75, patrones de nombre y
    de slug, duplicados por sha256, patrones de clave R2 (biblioteca y usados) y vectores de prueba. Longitud máxima del id de
    imagen: 120.
  - `schema/portal.schema.json`: formato de `portal.json`, `brand.json` e `index.json` (JSON Schema 2020-12).
  - `schema/publish-rules.json`: niveles de publicación `auto`, `review` y `blocked` (pendiente de aprobación por personas).
  - `schema/text-lint.json`: reglas de lint de texto (raya larga y nota interna residual; listo para cargar más patrones).
  - `docs/EXPORT_PORTAL.md` y ejemplos en `schema/examples/` (37 XC y Switch One Design).
- **`python -m oceanic export-portal`**: `portal.json` por modelo (148 modelos, 11 marcas, incluidos los RED), `brand.json` por
  marca, `index.json` con `catalog_hash` y `preview`, y `export-report.json`. Solo texto OCEANIC CONTENT, sin marcas internas,
  sin `>` de cita; `draft_marker_removed` e `internal_notes_removed` por sección; `model_year` `null` si el sitio no lo declara;
  `alt.es` determinista; videos con `third_party` (`true`/`false`/`null`), `host`, `review_status` y alerta `VIDEO_UNVERIFIED`;
  `publish_tier` estático (todo `change_kind: "new"`); lint con conteo por marca y por regla; `brand_authorization: "unknown"`.
- **`python -m oceanic publish-r2`**: sube las copias web `THIS_MODEL` a R2 con clave inmutable
  `{marca}/{modelo}/{categoria}/{id}-{sha256[:8]}.webp`. Simulación por defecto, idempotente y reanudable, nunca borra ni pisa;
  registro por modelo en `r2.json` (el paquete curated lo guarda en `biblioteca/<marca>/_r2/`). Credenciales solo por
  variables de entorno.
- **`python -m oceanic publish-export`**: sube el export a `catalog/{schema_version}/{catalog_hash}/...` y, al final, el
  puntero `catalog/latest.json` (única clave mutable). Se niega a subir un export con `preview: true`; verifica hashes,
  archivos y, con `--verify-images`, que las claves de imagen existan en R2.
- **`python -m oceanic media <modelo> --restore`**: repone solo las copias web ausentes o corruptas desde la web oficial,
  sin tocar `original`. Omite Solaris. Un captcha marca `requires_manual_download` con la URL exacta.
- `docs/OPERACION_R2.md`: instrucciones de la primera carga, por marca, de menor a mayor.
- Pruebas nuevas: `test_mediarules`, `test_r2`, `test_restore`, `test_lint`, `test_export`, `test_catalog`.
- Dependencias: `boto3`, `jsonschema`.

### Cambiado
- R2 es el destino único de las copias web (antes, ZIP a Drive); `zip` queda solo para los usados.
- `.gitignore`: `dist/portal/`.
- `docs/PROCESO.md` y `CLAUDE.md`: comandos nuevos, "Dónde vive cada cosa", contrato con el portal y regla de credenciales.

### Pendiente después del lanzamiento
Workflow de GitHub Actions y `tools/pipeline.py`; comparación contra una línea base para `publish_tier`; reglas de lint
adicionales (cuando se entregue la lista); pantalla del informe; videos livianos en R2; traducir o decidir la publicación de
características y equipamiento en inglés.

## [1.0.0]: estado del commit `c34dec6`

Biblioteca de contenido oficial de las marcas náuticas que representa Oceanic: 148 modelos en 11 marcas (Axopar, Beneteau,
Lagoon, Aquila, XO, Solaris, Saffier, VX One, Switch, Oceanic Power y Excess), cada uno como paquete completo en
`biblioteca/<marca>/<modelo>/` (especificaciones con trazabilidad, contenido SOURCE y OCEANIC, equipamiento, multimedia,
documentos, fuentes y readiness). Motor `python -m oceanic` (`extract`, `render`, `media`, `optimize`, `readiness`, `check`,
`build`, `zip`), copias web WebP de 1600 px (portada 2560 px) calidad 75, usados de oceanic.cl y contenido de marca traducido al
español (`marcas/`). Resumen del estado de ese commit; no es la historia completa.
