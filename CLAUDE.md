# Oceanic Content Engine

Biblioteca de contenido oficial de las marcas náuticas que representa Oceanic. Cada barco termina como un
paquete completo en `biblioteca/<marca>/<modelo>/`, listo para construir **después** su página Oceanic.
Especificación completa: `docs/ESPECIFICACION.md`. Proceso paso a paso: `docs/PROCESO.md`.
Paquete de referencia ya construido: `biblioteca/axopar/axopar-37-xc-cross-cabin/`.
El catálogo maestro (`data/`, `dashboard/`, `research/`, `output/`, inventario de marcas y modelos) tiene su propia
constitución en `docs/catalogo/CLAUDE-catalogo.md`: léela antes de tocar esas carpetas (sus menciones a "CLAUDE.md
sección N" se refieren a ese documento).

## Reglas absolutas

1. **Datos solo de la web oficial del fabricante.** Las especificaciones y las afirmaciones factuales salen únicamente
   de lo declarado en el sitio web oficial (fuentes de tipo `official_product_page` u `official_range_page`; el
   validador rechaza cualquier otra). Manuales, fichas PDF y brochures oficiales se registran en `06_DOCUMENTOS` pero
   no aportan datos. Distribuidores, portales, reseñas y la página Oceanic de referencia
   (https://oceanicsite.netlify.app/marcas/axopar/axopar-37-xc/) van en `sources.json › excluded_sources`. De la
   página Oceanic solo se usa la estructura.
2. **La tabla base siempre se llena desde la web del producto** (`schema/field-catalog.json › base_table`; para
   motor: Eslora Total, Manga Casco, Desplazamiento en rosca, Camarotes, Capacidad Combustible, Capacidad Agua Dulce,
   Certificación, Potencia motor máx). El dato siempre está en la web oficial del producto (o en su versión en
   inglés), pero no siempre literal: hay que **cruzar secciones** (ficha técnica, equipamiento estándar y opcional,
   pestañas, layouts). Cada cruce se documenta en el registro con `location` (sección) y `cross_reference` (por qué
   equivale). Ejemplos del 37 XC: "Weight (excl. Engine)" → Desplazamiento en rosca; cabina de proa de serie + cabina
   de popa opcional → Camarotes "1 (+1 opcional)"; "Category" + "Passengers" → Certificación "B10 / C12";
   extremo superior de "Outboard engines" y motor más potente ofrecido → Potencia motor máx.
   **Sin inventar:** prohibido usar datos de otro modelo, variante, model year o embarcación similar, datos de
   distribuidor, estimaciones, cálculos (p. ej. autonomía a partir del consumo) o IA como sustituto de la web. Fuera de
   la tabla base, si un dato de verdad no está: `NOT_FOUND`; con `display_value: "-"` la tabla muestra un guion.
3. **La ficha técnica de la web prevalece.** Si un campo aparece en el bloque de especificaciones técnicas de la web,
   ese es el valor publicado; las menciones del mismo dato en otras secciones de la página (equipamiento, textos) se
   anotan en `notes` y no se publican. Un campo que no está en la ficha técnica se busca en otras secciones de la
   web oficial del producto (cruce, ver regla 2).
   **Métrico e imperial del mismo campo que no coinciden** → se publica el métrico y el imperial se anota como error
   de la web (decisión Oceanic 2026-09-30). **Dos valores oficiales distintos** fuera de esos casos → `CONFLICT`,
   conservando ambos. Si dos registros podrían no referirse a lo
   mismo (otra medida, otra variante, otro año) → `REQUIRES_REVIEW`.
4. **Modelo exacto.** Diferenciar MODEL / MODEL YEAR / VARIANT / CONFIGURATION / ENGINE OPTION. No mezclar variantes
   (p. ej. 37 XC vs 37 Sun Top) ni generaciones. El model year de los datos es el que declara la web.
5. **Imágenes solo del modelo.** Cada imagen lleva `scope`: `THIS_MODEL`, `OTHER_MODEL`, `NOT_MODEL_SPECIFIC` o
   `REQUIRES_REVIEW`. Solo se descargan las `THIS_MODEL`. Las páginas oficiales a veces mezclan imágenes de otras
   variantes (el 37 XC trae fotos del 37 Sun Top): revisar tags y títulos. Excepción (decisión Oceanic 2026-09-30):
   una imagen de la galería oficial del modelo que también publica una variante hermana del mismo casco (misma gama y
   eslora, p. ej. Antares 8 / 8 Fishing) se acepta en ambas.
6. **Primero SOURCE CONTENT (literal), después OCEANIC CONTENT (candidato en español).** El candidato solo afirma
   lo que dice la fuente, queda marcado como BORRADOR y no copia textos de la página Oceanic de referencia.
7. **Equipamiento separado:** STANDARD, OPTIONAL, PACKAGES y CONFIGURATIONS, cada uno en su archivo.
8. **No crear archivos vacíos** para cumplir la estructura. Si algo no existe, se registra como faltante.
9. **Readiness honesto.** `CONTENT_STATUS` lo calculan las reglas de `tools/oceanic/readiness.py`, no el volumen
   de texto. Un modelo no está listo solo porque existe su página oficial.

## Estructura de un paquete

```
biblioteca/<marca>/<modelo>/
├── 00_MODELO/00_MODELO.md          identificación, alcance, conflictos, faltantes, bloque READINESS (generado)
├── 00_MODELO/readiness.json        (generado)
├── 01_CONTENIDO/                   hero, introduccion, diseno, ingenieria, experiencia-a-bordo, performance
│                                   (cada uno con "## SOURCE CONTENT" y "## OCEANIC CONTENT")
├── 02_ESPECIFICACIONES/specifications.json   ← fuente de verdad técnica (editar esto)
│   ├── tabla-caracteristicas.md    (generado)
│   └── specifications.md           (generado, con trazabilidad por valor)
├── 03_CARACTERISTICAS/caracteristicas.md
├── 04_EQUIPAMIENTO/standard.md, optional.md, packages.md, configurations.md
├── 05_MULTIMEDIA/IMAGENES/images.json (+ carpetas por categoría al descargar), VIDEOS/videos.json, multimedia.md (generado)
├── 06_DOCUMENTOS/documents.json (+ BROCHURES/ TECHNICAL/ MANUALS/ OTHER/ al descargar)
└── 07_FUENTES/sources.json, source-map.md, extract/
```

Slugs en minúsculas con guiones (`axopar-37-xc-cross-cabin`).

## specifications.json

Campos Oceanic en `schema/field-catalog.json` (por tipo: universal, motor, vela, catamarán). Mostrar solo los
relevantes; los críticos que falten van como `NOT_FOUND`. Cada valor es un registro con: `source_id`, `source_name`,
`url`, `accessed_at`, `source_field` (nombre original), `source_value` (literal), `source_unit`, `normalized_value`,
`normalized_unit`, `model_year` y opcionalmente `location`. `VERIFIED` exige que todos los registros coincidan.

## Comandos

```
cd tools && pip install -r requirements.txt
python -m oceanic extract <adaptador> <raw.html> <url> <out.json> [--accessed=AAAA-MM-DD]  # p. ej. axopar
python -m oceanic render <modelo>      # tablas + multimedia.md + validación de reglas
python -m oceanic media <modelo>       # descarga imágenes THIS_MODEL y documentos, con hash y dimensiones
python -m oceanic readiness <modelo>   # CONTENT_STATUS
python -m oceanic check                # render + readiness de toda la biblioteca (falla si hay errores de reglas)
python -m oceanic build <marca> <extract.json>...  # axopar | beneteau | lagoon | aquila | xo | solaris | saffier | vxone | switch | oceanicpower | excess: paquetes completos (no toca los curated)
python -m oceanic media <modelo> --cdn               # copias web desde el CDN (rápido, sin bajar originales)
python -m oceanic media <modelo> --from-dir=<carpeta> # importa originales bajados a mano en el navegador (webs con captcha anti-bots)
python -m oceanic optimize <carpeta>                 # recomprime copias web ya bajadas al estándar actual (sin re-descargar)
python -m oceanic zip <dir>...                       # ZIP en dist/ (solo para los usados; las imágenes de la biblioteca van a R2)
python -m oceanic media <modelo> --restore           # repone solo las copias web que faltan o están corruptas, desde la web oficial (no toca `original`; omite Solaris)
python -m oceanic publish-r2 [modelo|marca|--all] [--apply] [--verify-remote]  # sube las copias web a R2 (simulación por defecto; nunca borra)
python -m oceanic r2-backfill <modelo|marca|--all> --pillow=<v> --libwebp=<v> [--apply]  # completa una sola vez encoder y original_sha256 en r2.json ya existentes (simulación por defecto; sin red)
python -m oceanic export-portal [--out=<carpeta>] [--preview-keys]             # portal.json por modelo, brand.json e index.json (docs/EXPORT_PORTAL.md)
python -m oceanic publish-export [--out=<carpeta>] [--apply] [--verify-images] # sube el export a R2: catalog/{schema_version}/{hash}/... y catalog/latest.json
python -m oceanic publish-export --rollback=<hash> [--schema-version=<v>] [--apply]  # apunta catalog/latest.json a un catálogo ya publicado (simulación por defecto; archiva el puntero actual en catalog/history/)
python -m unittest discover -s tests   # pruebas
```

**Dónde vive cada cosa:** GitHub guarda motor, textos, datos e inventarios (`images.json` con URL del original).
Las copias web de imágenes NO se versionan (`.gitignore`): **R2 es el destino único** (`publish-r2`); el registro de lo subido
(`05_MULTIMEDIA/IMAGENES/r2.json`: clave, sha256, fecha; sin cuenta ni bucket) sí se versiona, y el paquete curated lo guarda
fuera de su carpeta (`biblioteca/<marca>/_r2/<modelo>.json`). Las copias se regeneran con `media --restore`. `zip` queda solo para
los usados. El portal importa siempre desde R2, nunca desde carpetas locales. Operación de la carga: `docs/OPERACION_R2.md`.
Este repositorio entrega contenido y candidatos; el portal (administrador con base de datos) decide qué se publica: aprobación,
portada, titular y autorización de cada marca no se registran aquí.

**Contrato con el portal (versionado, fuente única):** `schema/media-rules.json` (categorías, formatos, dimensiones, calidad,
patrones de nombre y de clave R2, también para usados), `schema/portal.schema.json` (formato del export),
`schema/publish-rules.json` (niveles de publicación; los aprueban personas, no el agente; el export solo informa el nivel),
`schema/text-lint.json` (lint de texto) y `docs/EXPORT_PORTAL.md`. Un cambio de contrato sube `schema_version`.

**Credenciales (repositorio público):** solo por variables de entorno de la sesión (`R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`,
`R2_SECRET_ACCESS_KEY`, `R2_BUCKET`) o secretos del repositorio. Nunca en archivos, logs, mensajes de error ni commits. El motor
nunca borra objetos en R2; `catalog/latest.json` es la única clave que se sobrescribe (excepción documentada) y `publish-export`
se niega a subir un export con `preview: true`.

**Generación masiva (Axopar):** `tools/oceanic/builders/axopar.py` aplica todas las reglas de este archivo. Los textos
OCEANIC CONTENT salen de `drafts/axopar/<slug>.json` (redactados a mano, solo con lo que dice la web); sin draft,
el bloque queda "PENDIENTE". Paquetes con `00_MODELO/model.json › curated: true` no se regeneran.

**Generación masiva (Beneteau):** web Drupal server-rendered; `adapters/beneteau.py` lee el HTML y `builders/beneteau.py`
arma el paquete (slug = nombre del modelo, p. ej. `oceanis-30-1`). Particularidades: la web no declara model year
(`NO DECLARADO`) ni superficie vélica (queda `-`: no se toma del PDF, decisión Oceanic); el bloque técnico da cada valor
en imperial y métrico: si no coinciden se publica el métrico (si el imperial solo tiene mal el símbolo, p. ej. `20''` por
20 ft, es el mismo valor); la versión EE. UU. de la página es la fuente S3 (se usa si el valor internacional no es
plausible, p. ej. manga del GT 40 Open); camarotes/baños se cruzan con los títulos de Layouts; una imagen publicada en varias páginas de modelo
queda `REQUIRES_REVIEW` salvo entre variantes hermanas; revisiones visuales en `drafts/<marca>/<slug>.json ›
image_overrides`. Equipamiento: la lista completa es un PDF (documento); standard/optional solo con menciones
literales de la página.

**Generación masiva (Lagoon):** web Nuxt con back office Drupal (`admin.catamarans-lagoon.com`); `adapters/lagoon.py`
produce el mismo extract que Beneteau y `builders/lagoon.py` reutiliza el builder de Beneteau con su propia `CFG`
(mismo grupo). Tipo `catamaran_vela`. La ficha técnica es completa (superficie vélica de ceñida, motorización estándar,
depósitos, CE, literas); camarotes y baños salen de las pestañas "Versions" (en el payload Nuxt). Si un campo aparece
dos veces en la ficha con valores distintos → `CONFLICT`. La galería no trae categorías: se clasifican a la vista (hojas de contacto) y quedan en
`drafts/lagoon/<slug>.json › image_overrides`. Los buques insignia se nombran en palabras (SIXTY 5 = 65, EIGHTY 2 = 82).
El brochure se pide por formulario (sin enlace directo). Las citas de prensa van a `excluded_sources`.

**Generación masiva (Aquila):** web HubSpot; `adapters/aquila.py` (mismo extract que Beneteau) y `builders/aquila.py`
(reutiliza el builder de Beneteau con su `CFG`). Tipos `catamaran_motor` / `catamaran_vela` (gama Sail). Las etiquetas de
la ficha cambian por modelo ("Dry Weight", "Light Displacement"...) y se mapean por patrones; algunos modelos publican una
segunda lista (Tankage, Propulsion). El alcance de las imágenes sale de la carpeta del CMS (`hubfs/46 Yacht/...`).
Rendimientos "estimated / non-contractual" → `REQUIRES_REVIEW`. Videos de terceros (BoatTEST, propietarios) se marcan.

**Generación masiva (XO Boats):** WordPress + WooCommerce; `adapters/xo.py` lee la tabla de atributos (ficha técnica) y
los bloques de texto; `builders/xo.py` reutiliza el builder de Beneteau. Tipo `motor`. "Overall Lenght (exc. engine)" →
Eslora Total; "Weight (excl. engine)" → Desplazamiento; "Classification" + "Passengers" → Certificación (posición a
posición; si las personas no crecen de B a C → `REQUIRES_REVIEW`). Valores sin unidad (combustible, motor intraborda):
se aplica la unidad que publica el resto de la gama y se anota. Camarotes/baños no están en la tabla: se cruzan con citas
literales del texto (`TEXT_XREF`; el build falla si la cita desaparece). Slugs con "+" → "plus". El catálogo 2026 de la
gama (FlippingBook) se registra como documento. Las reseñas de prensa enlazadas van a `excluded_sources`.

**Generación masiva (Solaris):** solo la gama a vela de solarisyachts.com (Solaris Power queda fuera, decisión Oceanic
2026-10-01). `adapters/solaris.py` lee carruseles, textos, planos, la rejilla "Technical specifications" y los créditos;
`builders/solaris.py` reutiliza el builder de Beneteau. La ficha está escrita a mano: unidades delante o detrás, miles con
punto o coma ("Kg 9.850", "9,400 kg"), erratas ("M 22.OO"). Un valor imposible para la eslora (46 kg en un 80 RS) →
`REQUIRES_REVIEW`; mayor + génova que no cuadra con la superficie vélica → `REQUIRES_REVIEW` (111 RS repite el aparejo del
80 RS). Potencia auxiliar = la mayor de la fila de motor. Camarotes/baños por citas literales (`TEXT_XREF`). Un RS y un
Flush Deck de la misma eslora (74 / 74 RS) no son variantes hermanas. Las imágenes no se pueden bajar desde el contenedor
(captcha anti-bots de SiteGround; no se elude): se bajan a mano en el navegador y se importan con `media --from-dir`. Alternativa (decisión Oceanic 2026-10-04): las copias que oceanic.cl publica con el **mismo nombre de archivo** que una imagen de la galería oficial, revisadas a la vista, se importan con `media --from-dir` + `mirror.json` (nombre → URL en oceanic.cl; queda en `original.fetched_from`). Las demás imágenes de las páginas Solaris de oceanic.cl son copias oficiales confirmadas por Oceanic (decisión 2026-10-05): van en `drafts/solaris/<slug>.json › extra_images` (URL, página, categoría visual) y el builder compartido las agrega como `THIS_MODEL` con id `oceanic-...`.

**Generación masiva (Saffier):** saffieryachts.com (WordPress); ficha completa por grupos (Dimensions, Sails, Engine, Tanks,
Accomodations). "L.O.A. (with bowsprit)" → Eslora Total y "Length (without bowsprit)" → Eslora Casco; calado y lastre de la
quilla estándar (las otras quillas en notas); "Air draft" → Altura sobre flotación. La web no publica superficie vélica
total (mayor y foque por separado): queda `-`, no se suman. Potencia en HP o kW (kW → hp solo para el máximo). Videos de
canales de terceros se marcan; reseñas de revistas (PDF) a `excluded_sources`. SL 46 MED | NORTH son dos cubiertas del
mismo modelo (un paquete).

**VX One:** un solo modelo (sportboat one-design) en vxone.com (Wix). `adapters/vxone.py`: `extract()` por página y
`merge(home, specs)` arma el extract del modelo (S1 = página Specifications, S3 = portada con el mismo bloque "THE BOAT" y el
texto de presentación). La web solo publica eslora, flotación, manga, superficie mayor+foque (cifra única), gennaker, calado
con la quilla abajo y peso de tripulación (2–3 personas): el resto queda `NOT_FOUND` (RED honesto). vxone.org (clase) se
excluye. Imágenes en `static.wixstatic.com` (original = URL sin `/v1/...`).

**Switch One Design:** un solo modelo (foiler one-design de carbono, ElementSIX Evolution) en switchonedesign.com (Wix).
`adapters/switch.py`: `extract()` por página y `merge(pages)` (Home = S1 con el bloque "THE BOAT", Technology = S3 que
repite el bloque, Formula Switch y Switch is Smart como textos atribuidos). Los 3 aparejos (6.5 / 7.5 / 8.5) son
configuraciones de la misma plataforma. El bloque técnico publica "6.5-7.5-8.4 sqm" (prevalece) aunque el texto y el nombre
del aparejo dicen 8.5 (anotado). "Platform Weight ~25 kg" → desplazamiento (aprox.). Sin depósitos, CE ni motor: RED honesto.

**Oceanic Power Boats:** semirrígidos (RIB ALUM y RIB LUX, 18 modelos) de la marca propia de Oceanic; su web oficial es
oceanic.cl (`/oceanic-power-boats/<modelo>/`), que aquí sí es fuente S1 (no es la página de referencia excluida por la regla 1).
"Ficha Técnica": largo total sin motor, manga, peso sin motor (→ desplazamiento), carga máxima, pasajeros, combustible y motor
máx. (fueraborda no incluido). La lista "Esta embarcación Incluye" va a `standard.md` (hook `CFG["write_equipment"]` del builder
compartido). Precio "DESDE USD" publicado. Sin CE, agua ni camarotes. Imágenes en `oceanic.cl/wp-content/uploads/`.

**Excess Catamarans:** excess-catamarans.com (Groupe Beneteau, Symfony server-rendered). Gama vigente: Excess 11, 13 y
14 (el 12 y el 15 ya no están en la web). `adapters/excess.py` lee portada, resumen, intro, bloques (Riders' Edition,
Hybrid), puntos fuertes, galería por categorías (original = URL sin `/media/cache/<filtro>/`), layouts y el bloque
"THE ESSENTIALS IN FIGURES" (pares etiqueta/valor métrico | imperial); `builders/excess.py` reutiliza el builder de
Beneteau. Tipo `catamaran_vela`. "9,000 kg" = miles; "6,59 m" = decimal. Depósitos "2 x 200 L" → 400 L (configuración
publicada, anotada). "Upwind sail area" estándar → superficie vélica (Pulse Line y Code 0 en notas); "Mast clearance" →
altura sobre flotación (aparejo estándar). Camarotes de "Cabin" (13) o de los títulos de layouts; arquitecto/baños por
citas literales (`TEXT_XREF`). Testimonios de propietarios, configurador y visita 360° a `excluded_sources`. Skeeta ya no
es representada (decisión Oceanic 2026-10-05): sin paquete.

**Usados (excepción, decisión Oceanic 2026-10-09):** `python3 tools/usados.py` captura https://oceanic.cl/usados/. Solo para
usados, los datos son variables por aviso y NO se normalizan al catálogo de campos ni pasan por `biblioteca/`: cada aviso
queda como bloc de notas literal en `usados/<slug>/ficha.txt` (texto del aviso, tarjeta del índice, documentos y URL de
fotos) + `fotos/` (WebP 1600 px q75, no versionadas, van en ZIP). `usados/LEEME.txt` es el índice (incluye vendidos y
tarjetas sin ficha) y `usados/OBSERVACIONES.txt` anota lo que hay que corregir en la web actual.

**Contenido de marca (decisión Oceanic 2026-10-09):** `marcas/<marca>/` guarda el texto institucional de la web oficial de
cada marca (historia, ADN, valores) para las páginas de marca de la nueva web: `original.md` (literal, con URL y fecha) y
`es.md` (traducción fiel al español, BORRADOR, sin agregar ni quitar afirmaciones; las dudas van como «Nota para la web»).
`marcas/TODAS-es.md` las reúne. Se omiten equipos directivos, contactos, cookies, testimonios y condiciones legales.

`check` debe terminar sin errores antes de hacer commit.

## Captura con Firecrawl

- Usar el MCP de Firecrawl: `firecrawl_search` para encontrar URLs oficiales y `firecrawl_scrape` con `rawHtml` para
  las fichas. Muchas webs (Next.js) esconden pestañas como el equipamiento opcional, que solo aparecen en los datos
  embebidos: por eso se usa rawHtml con un adaptador y no el markdown.
- Los PDF oficiales solo se identifican y registran como documentos (no aportan datos).
- Las descargas de binarios (imágenes, PDF) las hace `python -m oceanic media` y necesitan acceso de red a los CDN
  del fabricante (Axopar: `media.ffycdn.net`, `axopar.frontify.com`, `brand.axopar.com`, `manuals.axopar.com`;
  Beneteau: `www.beneteau.com`, originales en `/sites/default/files/`; Lagoon: `admin.catamarans-lagoon.com`; Aquila: `www.aquilaboats.com`; XO: `xoboats.com`, originales en `/wp-content/uploads/`; Solaris: `www.solarisyachts.com`; Saffier: `saffieryachts.com`; VX One y Switch: `static.wixstatic.com`; Oceanic Power: `oceanic.cl`; Excess: `www.excess-catamarans.com`, originales en `/media/`).
- **Peso:** no se guardan originales. Por imagen se guarda una copia web WebP (2560 px lado mayor si es
  HERO_CANDIDATE, 1600 px el resto, calidad WebP 75: decisión Oceanic 2026-10-08 para carpetas más livianas) y el original queda referenciado en `images.json › original` (URL, sha256,
  dimensiones, peso). Los documentos se guardan como enlace (`keep_file: true` solo si hace falta la copia).
  Objetivo: ~10–15 MB por modelo.
