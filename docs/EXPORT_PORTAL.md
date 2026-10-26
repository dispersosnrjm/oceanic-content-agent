# Exportación para el portal (contrato v1.0.0) · BORRADOR

> Borrador de la tarea 1 (contrato). Describe lo que el motor va a entregar; los comandos `export-portal` y
> `publish-export` y el código llegan en las tareas siguientes. Lo que ya existe y puede usar el equipo del portal:
> `schema/portal.schema.json`, `schema/media-rules.json`, `schema/publish-rules.json` y dos ejemplos en
> `schema/examples/`.

Este repositorio entrega **contenido y candidatos**; el portal decide qué se publica (aprobación editorial, portada,
titular y autorización de cada marca se hacen en el administrador). La exportación **no filtra por aprobación**: incluye
todos los modelos, también los RED, con su estado y lo que les falta. Los usados quedan fuera (los carga el administrador).

## Comando

```
python -m oceanic export-portal [--out=<carpeta>] [--preview-keys] [--only=<marca>/<modelo>] [--generated-at=<ISO>]
```

Por defecto escribe en `dist/portal/` (ignorado por git). Solo lee la biblioteca; sin red ni credenciales. Todo documento se
valida contra `schema/portal.schema.json` antes de escribir: si alguno no valida, el comando falla y no escribe nada.
Las claves de imagen salen de `r2.json`; `--preview-keys` calcula la clave con el sha256 del inventario cuando aún no hay
`r2.json` (solo para vista previa y ejemplos: no está verificada en R2). Un export así sale marcado con `preview: true` en
`index.json` y en `export-report.json`, y `publish-export` se niega a subirlo. Sin `r2.json` ni `--preview-keys`, las imágenes
se omiten como `sin_subir_a_r2`.

`export-report.json` informa: modelos por marca y estado, imágenes publicadas y omitidas con motivo, modelos y marcas con
`lint_ok: false`, violaciones de lint por marca y por regla (`lint.by_brand`, `lint.by_rule`), modelos sin portada candidata disponible, lista aparte `requires_manual_download`, videos `third_party:
true` por modelo, videos sin verificar, notas internas quitadas (con la frase vecina y si quedó marcada para revisión) y
conteo por nivel de publicación.

Limitaciones conocidas: en `packages` y `configurations` solo se exportan filas literales de tablas y citas del fabricante;
las listas y comentarios redactados por Oceanic (p. ej. "Contenido declarado") no se exportan.

## Archivos

```
index.json                                  catálogo: marcas, modelos, estado, hash, conteos
models/<marca>/<modelo>/portal.json         un modelo completo
brands/<marca>/brand.json                   texto institucional de la marca (es)
schema/portal.schema.json                   contrato (valida portal.json; $defs.brand e $defs.index validan los otros dos)
schema/media-rules.json                     reglas de medios compartidas
schema/field-catalog.json                   catálogo de campos (los usados usarán los mismos)
schema/publish-rules.json                   niveles de publicación
export-report.json                          informe de la corrida
```

En R2 (todas las claves son inmutables salvo `latest.json`):

```
catalog/{schema_version}/{content_hash}/index.json, models/..., brands/..., schema/...
catalog/latest.json      único objeto mutable: { schema_version, content_hash, generated_at, path }
```

`content_hash` del catálogo = `index.json › catalog_hash` (sha256 del `index.json` canónico sin marcas de tiempo; el
índice contiene el `content_hash` de cada modelo, así que dos exports iguales dan la misma ruta). **Excepción documentada:**
`catalog/latest.json` es la única clave que se sobrescribe; el código no borra nada. **El portal importa siempre desde R2**;
ningún campo del contrato es una ruta local: las imágenes son claves R2 y los videos y documentos, URL.

## Versionado

`schema_version` es semver. Cambio incompatible (quitar o renombrar un campo, cambiar un tipo) = versión mayor; campo
nuevo opcional = menor. El portal debe rechazar una versión mayor que no conoce. Un `portal.json` que no valida contra el
esquema es un error del motor y detiene la exportación (no se confunde con el lint, que no bloquea).

## Idiomas

Todo texto visible va por idioma: `texts.es`, `candidates.headlines.es`, `alt.es`, `label.es`. Hoy solo existe `es`; se
agrega `en` o `pt` como una clave más, sin cambiar la estructura.

## Qué trae `portal.json`

| Bloque | Contenido |
| --- | --- |
| `identity` | marca (slug, nombre), modelo (sin marca), slug, tipo de barco, model year tal como lo declara la web (`NO DECLARADO` si no), gama, variante, `content_hash` (sha256 del JSON canónico sin marcas de tiempo: cambia solo si cambia el contenido), `source_url` |
| `texts.es` | `introduccion`, `diseno`, `performance`, `ingenieria`, `experiencia_a_bordo`: solo el bloque OCEANIC CONTENT, en Markdown, con `draft_marker_removed` por sección. `text: null` si no hay candidato |
| `candidates` | **todos** los titulares, **todas** las descripciones cortas y **todas** las portadas candidatas (`hero_candidate`) con `hero_rank`, motivo y si están disponibles (archivo y clave R2) |
| `specs` | solo campos `VERIFIED`: clave, etiqueta, `display_value`, unidad, model year, fuente (id, url, fecha de acceso) |
| `features`, `equipment` | características y equipamiento `standard`, `optional`, `packages`, `configurations`, literales (ver abajo) |
| `images` | solo `THIS_MODEL` con archivo y clave R2: id, categoría, clave, ancho, alto, `hero_rank`, `copyright_notice`, `alt.es` |
| `videos` | los que no estén marcados `third_party`; url, plataforma, portada, resolución, estado de revisión y alerta |
| `documents` | solo enlaces directos disponibles |
| `readiness` | `CONTENT_STATUS`, porcentaje, faltantes críticos, conflictos y la lista de ítems que no están OK con su detalle |
| `lint_ok`, `lint_violations` | calidad del texto en español; **no bloquea**, el administrador la muestra |
| `publish_tier` | nivel de publicación informado por el motor (ver abajo) |

### Formato de los textos

`texts.es.<sección>.text` es **markdown simple**: sin `>` de cita (ninguna línea empieza con `>`; el esquema lo exige con
un patrón y una prueba falla si ocurre) y con los subtítulos como líneas en negrita sin cita (`**Subtítulo**`). Cada sección
trae además `draft_marker_removed` e `internal_notes_removed` (entero), igual que `brand.json`.

### `model_year`

`identity.model_year` y el `model_year` de cada spec aceptan `null`. Cuando el sitio no declara el año, el export emite `null`,
no `"NO DECLARADO"`.

### Limpieza de textos

- Se exporta **solo** el bloque `## OCEANIC CONTENT`. Nunca el SOURCE CONTENT literal del fabricante.
- Se quitan las marcas internas: líneas `> Candidato ...`, `BORRADOR`, `PENDIENTE` y `Nota para la web`. Si en una sección
  se quitó una marca BORRADOR, esa sección lleva `draft_marker_removed: true` (trazabilidad: el texto sigue siendo un
  candidato que alguien debe aprobar en el administrador).
- Las notas editoriales internas en cursiva entre paréntesis (`*(No publicar una cifra calculada.)*`; hay 3 en el paquete
  curated del 37 XC, que es intocable) se quitan **solo en el export**, nunca en el origen. Si la frase vecina contiene una
  cifra calculada, no basta con borrar la nota: esa sección se marca `review`. El número de notas quitadas se cuenta en
  `export-report.json` (`internal_notes_removed`) y el lint avisa si queda alguna con ese patrón.
- Los textos vienen de los `.md` del paquete (no de `drafts/`): son lo que valida `readiness.py` y cubren los 148 modelos.

### Imágenes

- Clave R2 (`schema/media-rules.json`): `{marca}/{modelo}/{categoria}/{id}-{sha256[:8]}.webp`, con `categoria` en
  minúsculas. Cabeceras: `Content-Type: image/webp`, `Cache-Control: public, max-age=31536000, immutable`. La clave nunca
  cambia de contenido: si cambia el archivo, cambia el hash y por lo tanto la clave.
- `alt.es` = `"<modelo>, <alt_label_es>"`, por ejemplo `Antares 11 Coupe, exterior`. Es determinista: no se
  inventan descripciones. Cada categoría de `media-rules.json` tiene dos etiquetas: `label_es` para la interfaz del
  administrador (HERO `portada`, OTHER `galería`) y `alt_label_es` para el texto alternativo (HERO `vista principal`,
  OTHER `otra vista`); en las demás categorías son iguales (exterior, interior, cockpit, camarote, puesto de mando,
  detalle, navegando, planos). El administrador podrá editar el alt de una imagen concreta; cambiar `alt_label_es` solo
  cambia el texto alternativo al reexportar y no pisa lo editado por imagen.
- `hero_rank` ordena las portadas candidatas; la elección final es del administrador.
- Los ejemplos de `schema/examples/` usan el sha256 de la copia web registrado en `images.json` (aún no hay `r2.json`); en
  la exportación real la clave sale del archivo subido.

### Videos

`third_party` se exporta como `true`, `false` o `null`. Si el inventario no trae la marca (`null`) el video va con
`review_status: "unverified"` y se **publica igual**, con `alert` (`VIDEO_UNVERIFIED`, acciones `mark_verified` y
`remove`): el administrador debe mostrar una alerta visible por cada video sin verificar. **El nivel de un video sin
verificar es `auto` con la alerta `VIDEO_UNVERIFIED`** (se publica con alerta; ver `schema/publish-rules.json`). Cada video
trae `host` (dominio de la URL). Hoy el motor excluye los marcados
`true` y exige `scope == THIS_MODEL`; esos videos `third_party: true` no son publicables pero se **listan por modelo** en
`export-report.json`. `resolution` permite que el portal no reproduzca 4K automáticamente.

### Características y equipamiento

Se exportan **literales** del fabricante, con `origin: "manufacturer_literal"` y `lang: "en"`. No son texto de Oceanic.

## Niveles de publicación (`publish_tier`)

El motor solo **informa** el nivel; no publica nada. Las reglas viven en `schema/publish-rules.json`, las aprueban personas
(hoy `status: "pending_approval"`) y no las genera el agente.

| Nivel | Casos |
| --- | --- |
| `auto` | imagen nueva o reemplazada del mismo modelo que pasa `media-rules.json`; dato de ficha técnica que cambió en la fuente oficial (conserva `previous_value`; requiere comparación, después del lanzamiento) |
| `review` | texto editorial nuevo o modificado (con `lint_ok`), modelo nuevo, portada, titular |
| `blocked` | marca sin autorización registrada |
| alerta | video sin verificar: se publica con alerta |

En la primera importación **todo es `change_kind: "new"`** y el nivel sale solo de las reglas estáticas (`mode: "static"`).
**Autorización de marca:** el motor la desconoce y exporta `brand_authorization: "unknown"` en `brand.json` e `index.json`; el
portal aplica `blocked` hasta que el administrador la registre. Modo sombra y reversión se implementan en el portal.

## Reglas de medios compartidas

`schema/media-rules.json` es la fuente única para el motor y para el administrador (que también cargará fotos de usados):
categorías válidas, formatos (entrada jpeg, png, webp; salida WebP), dimensiones máximas (2560 px la portada y 1600 px el
resto, sin ampliar), calidad WebP 75, patrón de nombres de archivo y de slug, duplicados por sha256 y patrones de clave R2:

```
{marca}/{modelo}/{categoria}/{id}-{sha256[:8]}.webp
usados/{slug}/{categoria}/{id}-{sha256[:8]}.webp
```

`test_vectors` trae entradas y salidas esperadas para que el administrador compruebe que su implementación coincide con la
del motor. `schema/field-catalog.json` (campos de la ficha) se exporta junto al contrato porque los usados usarán los mismos.

## Lint de texto

Regla integrada: no se admiten rayas largas (`—`). La estructura de `schema/text-lint.json` (reglas literales o regex con
severidad `error` o `warn`) quedará lista para cargar más patrones cuando se entreguen. `lint_ok` es `false` si hay alguna
violación `error`; `lint_violations` detalla ámbito, sección, regla, línea y fragmento.

## Revisión posterior al lanzamiento: traducir o decidir su publicación

Las características y el equipamiento salen hoy en inglés literal del fabricante (`manufacturer_literal`, `lang: "en"`).
Después del lanzamiento hay que decidir, modelo por modelo o por marca, entre: (a) traducirlos al español como candidato
(BORRADOR, sin agregar ni quitar afirmaciones), (b) publicarlos en inglés tal cual, o (c) no mostrarlos. Hasta entonces el
portal debe tratarlos como contenido sin revisar editorial y no presentarlos como texto de Oceanic.

## Decisiones abiertas

- Resueltas: videos `third_party: true` fuera de lo publicable pero listados en el informe; notas internas del 37 XC quitadas en
  el export; etiquetas `label_es` / `alt_label_es`.
