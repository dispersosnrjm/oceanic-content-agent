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
- `media --restore` verifica cada descarga contra el hash registrado del original (`original.sha256`, o
  `original.source_copy_sha256` en las copias de la CDN de Axopar): si coincide, `images.json` no cambia; si difiere, no
  sobrescribe nada y marca `REQUIRES_REVIEW (original cambió)` (no se publica ni sale en el export); si no hay hash, restaura sin
  bloquear y lo cuenta como no verificable por marca. Acepta una marca entera y `--report=<archivo.json>`.
- `publish-export`: antes de sobrescribir `catalog/latest.json` lo lee y guarda una copia inmutable en
  `catalog/history/<fecha UTC>-<hash>.json` (si falla, no sobrescribe); nuevo `--rollback=<hash>` (simulación por defecto).
- `R2Client.get_object` (solo lectura).
- `r2.json`: cada entrada nueva registra `encoder` (Pillow, libwebp, quality, method) y `original_sha256` cuando existe (aditivo; `version`
  sigue en 1). `publish-r2` compara primero con la entrada activa de `r2.json`, así que ya no avisa "distinta al inventario" cuando
  r2.json registra esa misma clave. Nuevo `python -m oceanic r2-backfill` (simulación por defecto) para completar una sola vez las entradas
  ya subidas sin tocar `images.json` ni R2.
- Dependencias: `pillow==13.0.0` fijada (el resto sigue con `>=`).
- `docs/OPERACION_R2.md`: instrucciones de la primera carga, por marca, de menor a mayor.
- Pruebas nuevas: `test_mediarules`, `test_r2`, `test_restore`, `test_lint`, `test_export`, `test_catalog`.
- Dependencias: `boto3`, `jsonschema`.
- **Contrato 1.1.0 del export** (`schema_version` 1.1.0, campos nuevos opcionales; un export 1.0.0 sigue validando). Cada imagen
  exportada trae `alfa` (1 % o más de los píxeles con alfa < 255, leyendo la copia web en solo lectura) y `fondo_sugerido`
  (`claro` u `oscuro` según la luminosidad media de los píxeles opacos; umbral 128). Una imagen con `alfa` verdadero nunca se
  ofrece como portada ni para compartir: su `hero_rank` sale en `null` y su candidata sale con `available: false` y
  `blocked_reason: "alfa"`. El informe cuenta las imágenes con transparencia y los modelos que pierden la portada por esto.
- **`python -m oceanic backup-photos` y `restore-photos`**: respaldo de las fotos en R2. Copia cada imagen registrada en un
  `r2.json` a `recoveryphotos/<clave>` con una copia dentro del servidor (sin descargar) y guarda a su lado una libreta
  `recoveryphotos/_ledger/<clave>.json` (marca, modelo, categoría, id, claves, sha256, bytes, origen, fechas, versión del agente).
  Simulación por defecto, `--apply` escribe, `--verify` es de solo lectura (existencia, sha256 y tamaño). Nunca borra ni pisa.
  `restore-photos` devuelve una foto solo si su clave original no existe, verificando el sha256 contra la libreta; si existe con
  otro hash exige `--force-restore-id=<clave>`. El cliente R2 suma únicamente `copy_object` (copia en el bucket) y el tamaño en
  `head_object`; sigue sin ningún método de borrado.
- **Aceptación por regla de imágenes cambiadas**: `media --restore --accept-by-rule --by=ap-NNN` acepta una imagen en
  `REQUIRES_REVIEW (original cambió)` solo si su `last_modified` es anterior al registro y las dimensiones coinciden, y deja la
  decisión en `review_log` (aprobador, regla, hash observado y esperado). `--by` solo admite un código de aprobador `ap-NNN`, nunca
  correos ni nombres. El bloque `original` no se toca. Aplicada a las 134 imágenes de Aquila el 2026-10-28 (aprobador ap-001).
- Cargas a R2: Aquila (308), Lagoon (416), Axopar (750) y Beneteau (1.158) más las marcas anteriores: 3.051 imágenes, con su
  `r2.json` versionado, verificación remota sin diferencias y respaldo en `recoveryphotos/`. Quedan fuera de esta entrega las 21
  muestras de color del Axopar 37 XC (en `REQUIRES_REVIEW`), Saffier (bloqueo antibots) y Solaris (captcha).
- `docs/pendientes.md`: pendientes formales y notas para el Pull Request.
- Pruebas: las que usan datos reales leen una copia temporal de la biblioteca (`tests/_realdata.py`), sin las `.webp`, y ya no leen
  ni escriben el árbol real; se acabó la intermitencia por escrituras concurrentes. La suite tiene 208 pruebas.

### Cambiado
- R2 es el destino único de las copias web (antes, ZIP a Drive); `zip` queda solo para los usados.
- `.gitignore`: `dist/portal/`.
- `media --restore` ya no pisa registros buenos si la red falla: un fallo solo va al informe cuando el registro ya tiene copia, y acepta URLs con espacios (las usa sin espacios y las informa como `url_con_espacios`).
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
