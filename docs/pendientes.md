# Pendientes formales

Registro de lo que queda abierto y las decisiones ya tomadas. Cada entrada dice la causa, la decisión, el respaldo y el
criterio de cierre. Los datos de `biblioteca/` no se modifican para registrar un pendiente.

## Saffier: 266 imágenes sin recuperar

- **Estado:** abierto. Fuera de este ciclo (decisión de Johnny, 28 de octubre de 2026).
- **Alcance:** 266 imágenes `THIS_MODEL` con copia registrada (`WEB_COPY`) en los 7 modelos de Saffier: SC 6.50 Cruise,
  SC 8M Cabin, SE 24 Lite, SE 28 Leopard, SE 33 Life, SE 38 Leader y SL 46 MED | NORTH. Todas viven en `saffieryachts.com`
  (`/wp-content/uploads/AAAA/MM/`, por `https`) y las 266 tienen `original.sha256`. Otras 36 tienen una segunda URL oficial,
  que es la miniatura de WordPress (`...-1024x683.jpg`) y no el original: no sirve para verificar.

### Causa
El sitio está detrás de Cloudflare y responde con un desafío antibots al agente.

- Confirmado el 28 de octubre de 2026 con **una sola petición** (cabeceras exactas del agente: `User-Agent: Mozilla/5.0
  (oceanic-content-agent; +https://oceanic.cl)`, `Accept-Encoding: identity`, `Connection: close`) a
  `https://saffieryachts.com/wp-content/uploads/2025/03/Saffier-SC-6.5-Cruise-Classic-Sailing-Yacht-Saffier-Yachts-1.jpg`.
- Respuesta: **HTTP 403 desde la primera petición**, `Server: cloudflare`, `cf-ray: ...-SCL`, cookie `__cf_bm`, `Cache-Control:
  no-store` y un cuerpo HTML de 1.790 bytes con título "Just a moment...". No hay cabeceras de Sucuri ni de Wordfence. No
  fue un límite de velocidad.
- `robots.txt` no lo impide: solo bloquea `/wp-json/` y `/?rest_route=` y no fija `Crawl-delay`.
- En un navegador las imágenes sí cargan (el navegador ejecuta el desafío). El bloqueo es solo contra el agente.
- No existen copias respaldadas: las copias web las generaron sesiones de Claude en un contenedor y no se conservaron. Por eso
  existe `media --restore`, que aquí no puede completar la descarga.

### Decisión
- Dejar Saffier fuera de la carga a R2 y de este ciclo: no se suben ni se exportan sus imágenes.
- Pedir a Saffier, a través de Oceanic, una excepción para el User-Agent del agente o un kit de prensa con las fotos.
- No se cambian cabeceras, no se usa un navegador automatizado ni se reutilizan cookies de un navegador: eso sería eludir una
  medida de bloqueo.
- `images.json` de Saffier no se toca. Un fallo de descarga no modifica un registro con copia registrada.

### Respaldo (no implementado): `media saffier --import-dir=<carpeta>`
Una persona descarga los originales en el navegador y el agente los incorpora verificándolos.

- Comparar el sha256 de cada archivo con `original.sha256`. Si coincide, genera la copia web en la ruta registrada sin
  modificar `images.json`; si no coincide, marca `REQUIRES_REVIEW (original cambió)`.
- No sirve el `--from-dir` actual tal como está: reescribe el bloque `original` con el hash del archivo importado y no compara.
- Esfuerzo estimado: unas 3 horas con pruebas.
- **Riesgo:** Cloudflare puede recomprimir la imagen que entrega al navegador (por ejemplo Polish o conversión a WebP según las
  cabeceras). Entonces el hash no coincidiría con `original.sha256` aunque la imagen sea la misma. Se vería en la primera
  comparación. Si pasa, no se debe aceptar un hash distinto sin revisión de una persona.
- Solo se implementa si Johnny lo pide.

### Criterio de cierre
Las 266 imágenes de Saffier están en R2 y `python -m oceanic publish-r2 saffier --verify-remote` termina con 0 por subir y 0
errores, con sus `r2.json` creados.

### Visibilidad hoy (28 de octubre)
- `check` muestra los 7 modelos de Saffier en YELLOW y sus ítems de imágenes en OK ("descargadas 48/48"), porque cuenta las
  copias registradas en `images.json`, no las que existen en disco ni en R2. **No aparece como incompleto.**
- `export-portal` omite las 266 imágenes con el motivo `sin_subir_a_r2`, sin ninguna en `requires_manual_download`, y los 7
  modelos quedan sin portada candidata disponible.
- El ítem de imágenes de `readiness.py` cuenta copias **registradas** en `images.json`, no las existentes en disco ni en R2. Por
  eso Saffier aparece como "48/48 OK" y eso es engañoso. Queda solo anotado; el cambio en `readiness.py` no se hace todavía.
- Cambio mínimo de visibilidad (aprobado, **va después de resolver Aquila**, no implementado): un archivo versionado
  `schema/media-pending.json` con las marcas que esperan descarga manual (por ahora `saffier`). Con él, `export-portal` contaría
  esas omisiones como `requires_manual_download` y las listaría en `export-report.json`, sin tocar `images.json`. Unas 25 líneas
  más una prueba.

### Nota para la descripción del Pull Request
> **Saffier queda fuera de este ciclo.** Sus 266 imágenes (7 modelos) no se pudieron recuperar: `saffieryachts.com` responde 403
> con un desafío antibots de Cloudflare desde la primera petición (confirmado el 28/10/2026), aunque cargan en un navegador. No
> hay copias respaldadas. No se suben a R2 ni se exportan; `images.json` de Saffier no se modificó. Se pedirá una excepción o un
> kit de prensa a Saffier a través de Oceanic. Respaldo registrado en `docs/pendientes.md`: `media saffier --import-dir` con
> verificación contra `original.sha256` (riesgo de recompresión del hash). Cierre: 266 imágenes en R2 verificadas con
> `--verify-remote`.

## Aquila: 134 imágenes en REQUIRES_REVIEW (en investigación)

- **Estado (28 de octubre de 2026):** una corrida de `media --restore` sobre Aquila dejó 134 imágenes en
  `REQUIRES_REVIEW (original cambió)` en 9 modelos (`aquila-28-molokai` 18, `aquila-28-molokai-cuddy` 14, `aquila-32-sport` 12,
  `aquila-36-sport` 19, `aquila-42-yacht` 18, `aquila-47-molokai` 2, `aquila-50-yacht` 15, `aquila-54-yacht` 11 y
  `aquila-70-luxury` 25). Los cambios en los `images.json` de esos modelos están **sin commitear**.
- **Causa:** sin determinar. La descarga no coincide con `original.sha256`; hay que comprobar si el original cambió de verdad o si
  la URL oficial sirve bytes distintos del mismo archivo (falso positivo de la verificación).
- Esas imágenes no se publican ni se exportan mientras estén en `REQUIRES_REVIEW`. No se ha subido nada de Aquila a R2 (no hay
  `r2.json` de Aquila).
- No se commitean esos `images.json` hasta cerrar la investigación.

## Otros pendientes ya acordados

- **Pruebas con copia temporal de la biblioteca** (antes del Pull Request). Las pruebas que leen datos reales fallan si otro
  proceso escribe `images.json`, `r2.json` o `readiness.json` mientras corren (`JSONDecodeError`). Solución: trabajar sobre una
  copia temporal de unos 19 MB, con verificación de lectura completa. Mientras tanto, no correr las pruebas durante una carga.
- **CHANGELOG.md:** se actualiza en un solo commit al final de la integración.
- **`--accept-changed` y `--reject`** para imágenes `REQUIRES_REVIEW`, y **idempotencia por imagen** (id + `original_sha256`):
  se reabren cuando el agente corra en más de un equipo o cuando se midan las marcas grandes.
- **Después del lanzamiento:** workflow de GitHub Actions y `tools/pipeline.py`, línea base para `publish_tier`, videos livianos
  y traducir o decidir la publicación de las características y el equipamiento en inglés.
