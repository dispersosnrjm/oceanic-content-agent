# Proceso: de un modelo del catálogo a un paquete completo

Ejemplo real: `biblioteca/axopar/axopar-37-xc-cross-cabin/`.

## 1. Identificar el modelo exacto

- Encontrar la ficha oficial (`firecrawl_search`, y confirmar el dominio del fabricante).
- Fijar MODEL, MODEL YEAR, VARIANT, CONFIGURATION base y ENGINE OPTIONS. Revisar si la marca tiene generaciones
  (en Axopar lo dice el portal de manuales) y variantes vecinas que no se deben mezclar.

## 2. Capturar las fuentes oficiales

| Qué | Cómo |
| --- | --- |
| Ficha del modelo | `firecrawl_scrape` con `formats: ["rawHtml"]`. El resultado grande se guarda como archivo: tomar `rawHtml` y pasarlo al adaptador. |
| Página de gama | `firecrawl_scrape` markdown (contexto y posicionamiento) |
| Manual / ficha técnica PDF | Solo identificar la URL y registrarla en `documents.json` (no aporta datos) |
| Portal de documentos | `firecrawl_scrape` con `formats: ["links"]` |
| Condiciones de uso de imágenes | página de prensa o media library |

Registrar cada fuente en `07_FUENTES/sources.json` (id S1, S2…, `type`, URL, fecha, alcance de model year). Solo las
de tipo `official_product_page` u `official_range_page` pueden aportar datos. Registrar
también las descartadas (distribuidores, portales) con el motivo.

## 3. Extraer

```
cd tools
python -m oceanic extract axopar <raw.html> <url> ../biblioteca/<marca>/<modelo>/07_FUENTES/extract/S1-<modelo>.page.json
```

El adaptador guarda literal: hero, secciones, pestañas (incluidas las ocultas), modales de ediciones, ficha técnica,
equipamiento estándar y opcional, imágenes y videos con sus metadatos del DAM.
**Marca nueva:** crear `tools/oceanic/adapters/<marca>.py` con una función `extract(raw_html, url, accessed_at)` que
devuelva las mismas claves.

## 4. Especificaciones

Editar `02_ESPECIFICACIONES/specifications.json`: un registro por cada valor declarado en la web oficial, con el campo y el valor originales.
Comparar fuentes y marcar `VERIFIED`, `CONFLICT`, `REQUIRES_REVIEW` o `NOT_FOUND`. Después:

```
python -m oceanic render ../biblioteca/<marca>/<modelo>
```

Genera `tabla-caracteristicas.md` y `specifications.md`, y falla si se rompe alguna regla (VERIFIED con valores
distintos, CONFLICT con un solo valor, campo crítico ausente, fuente no registrada o que no sea web oficial).

## 5. Contenido

- `01_CONTENIDO/*.md`: `## SOURCE CONTENT` con citas literales y su fuente; `## OCEANIC CONTENT` con el candidato en
  español, marcado como BORRADOR.
- `03_CARACTERISTICAS`, `04_EQUIPAMIENTO` (standard / optional / packages / configurations por separado).

## 6. Multimedia y documentos

- `images.json`: se crea con `media.init_inventory(extract)` y después se clasifica cada imagen (categoría, `scope`,
  confianza, evidencia) y se marcan las HERO_CANDIDATE con su motivo.
- `videos.json` y `documents.json`.
- Descarga, cuando la red lo permita: `python -m oceanic media ../biblioteca/<marca>/<modelo>`. Guarda los archivos
  por categoría y registra sha256, formato y dimensiones reales. Omite duplicados y lo que no sea `THIS_MODEL`.
- Reponer copias web que falten (clon nuevo o carpetas vacías): `python -m oceanic media <modelo> --restore`. Repone solo lo
  ausente o corrupto desde la web oficial, sin tocar `original`; omite Solaris (captcha: `--from-dir`). Un captcha deja la
  imagen como `requires_manual_download` con la URL exacta.
- Publicar a R2 (destino único de las copias web): `python -m oceanic publish-r2 <marca>` (simulación) y `--apply`; después
  `--verify-remote`. Ver `docs/OPERACION_R2.md`.
- Export para el portal: `python -m oceanic export-portal` y `publish-export` (ver `docs/EXPORT_PORTAL.md`). El orden es
  copias web → `publish-r2` → `export-portal` (sin `--preview-keys`) → `publish-export`.

## 7. Readiness y cierre

```
python -m oceanic check
```

Actualiza el bloque READINESS de `00_MODELO.md`. Completar a mano en `00_MODELO.md` la identificación, los
conflictos, la información faltante y las observaciones.
