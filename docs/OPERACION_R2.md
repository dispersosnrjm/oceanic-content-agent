# Operación: primera carga de imágenes a R2 (por marca)

Instrucciones para ejecutar la primera carga completa desde tu PC (Windows, PowerShell) con tu token de R2. Nadie más necesita
las claves: no las pegues en chats, archivos ni commits. Referencia de diseño: `docs/EXPORT_PORTAL.md` y
`tools/oceanic/r2.py`.

## Qué hace cada comando

| Comando | Qué hace | Red | Credenciales |
| --- | --- | --- | --- |
| `media <modelo> --restore` | Repone solo las copias web que faltan o están corruptas, desde la web oficial. No toca `original`. Omite Solaris | sí (webs oficiales) | no |
| `publish-r2 <marca>` | **Simulación** (dry-run): qué se subiría y qué se omite, y por qué | no | no |
| `publish-r2 <marca> --apply` | Sube a R2. Repetirlo es seguro (no vuelve a subir lo que ya existe) | sí (R2) | sí |
| `publish-r2 <marca> --verify-remote` | Comprueba en R2 que cada clave existe con el mismo sha256 | sí (R2) | sí |

`publish-r2` nunca borra ni pisa una clave existente con otro contenido. Las copias web no se versionan en git.

## Antes de empezar (una vez)

```powershell
cd C:\local\claude\oceanic-content-agent
git switch integracion-portal
git status                      # biblioteca\ sin cambios antes de empezar
cd tools
python --version                # Python 3.12.10
python -m pip install -r requirements.txt
$env:PYTHONUTF8 = '1'           # en CADA ventana de PowerShell
python -m unittest discover -s tests
python -m oceanic check         # sin líneas ERROR
```

Credenciales: solo en variables de la sesión de PowerShell (se pierden al cerrar la ventana; no uses `setx` ni archivos):

```powershell
$env:R2_ACCOUNT_ID        = '...'
$env:R2_ACCESS_KEY_ID     = '...'
$env:R2_SECRET_ACCESS_KEY = '...'
$env:R2_BUCKET            = '...'
```

Si falta alguna, `publish-r2 --apply` se detiene y lista solo los **nombres** de las que faltan. No se asume que el token no
pueda borrar: eso no está verificado en la documentación de Cloudflare. **La garantía de no borrado es el código más sus
pruebas** (el cliente solo expone `head_object` y `put_object`). Usa un token limitado a este bucket, pero no lo tomes como
la garantía.

## Ciclo para una marca

Sustituye `<marca>` por el nombre de la carpeta en `biblioteca\` (`oceanic-power`, `vx-one`, `switch`, ...). Desde `tools\`:

```powershell
$m = '<marca>'

# 1. Reponer las copias web que falten (puede tardar; si se corta, repítelo: solo repone lo que falta).
#    Acepta una marca entera o un modelo. El informe JSON va FUERA del repositorio (la carpeta temporal de tu usuario):
#    dist\ no está ignorada por git para este tipo de archivo, así que no lo guardes ahí
python -m oceanic media $m --restore --report="$env:TEMP\oceanic-restore-$m.json"

# 2. Simulación: revisa el informe (elegibles, por subir, omitidas con su motivo)
python -m oceanic publish-r2 $m

# 3. Subir (necesita las variables R2_*). Repetirlo es seguro
python -m oceanic publish-r2 $m --apply

# 4. Verificar en R2: 0 diferencias
python -m oceanic publish-r2 $m --verify-remote

# 5. Revisar qué cambió en el repositorio
cd ..; git status --short; cd tools
```

### Cómo verifica `--restore` lo que descarga

Cada descarga se compara con el hash registrado de lo que se bajó (nunca con el del WebP, que depende del codificador):

| Resultado | Qué significa | Qué hace con `images.json` |
| --- | --- | --- |
| **verificada** | El archivo oficial es el mismo que revisó Nico (`original.sha256`; en las copias de la CDN de Axopar, `original.source_copy_sha256`) | **No cambia nada** (ni `downloaded_at`): git diff vacío. Escribe la copia en disco |
| **cambiada** | El archivo oficial ya no es el mismo | **No escribe la copia** ni toca sus campos. Marca `REQUIRES_REVIEW (original cambió)` y guarda `restore_check` (URL, hash esperado y hash descargado). No se publica ni se vuelve a descargar hasta que una persona decida. Se lista en el informe |
| **no verificable** | No hay hash registrado con el que comparar | Escribe la copia; el registro no cambia. Se cuenta por marca en el informe y no bloquea |
| **sin URL** | El registro no tiene ninguna URL oficial | Marca `FAILED (sin URL oficial)` y se cuenta aparte |

El resumen final da: restauradas y verificadas, cambiadas (REQUIRES_REVIEW), no verificables (por marca), sin URL, fallidas y
descargas manuales. Una imagen `REQUIRES_REVIEW` no se sube con `publish-r2` ni sale en el export. Los hashes del WebP regenerado
pueden diferir de los registrados (versión de Pillow): `publish-r2` sube con el hash real del archivo y lo avisa.

Qué debe aparecer en `git status` tras el ciclo (y solo esto):

- Con descargas **verificadas**, `images.json` no cambia. Se modifica solo si hubo imágenes **cambiadas** (`REQUIRES_REVIEW`),
  **fallidas**, con **captcha** (`requires_manual_download`) o registros que no tenían copia inventariada. `original` nunca cambia.
- `biblioteca\<marca>\<modelo>\05_MULTIMEDIA\IMAGENES\r2.json` nuevo (clave, sha256, fecha; sin cuenta ni bucket).
- Para el paquete curated de Axopar, su registro va en `biblioteca\axopar\_r2\axopar-37-xc-cross-cabin.json` y el paquete
  no se toca.
- Las imágenes `.webp` **no** aparecen: están en `.gitignore`.

Cualquier otro cambio en `biblioteca\` es inesperado: detente y avisa antes de continuar. Los commits y el push los decides tú.

## Orden: de menor a mayor

Estimaciones de la reposición (no medidas): red 10 MB/s y ≈ 1,5 s por imagen en un proceso. Si ya tienes en `biblioteca\` las
copias del 2026-10-08 que entregue tu socio, `--restore` solo repara lo que falte y tarda mucho menos.

| # | Marca | Copias | Reposición estimada | Notas |
| --- | --- | --- | --- | --- |
| 1 | oceanic-power | 18 | < 1 min | Primera: valida el circuito completo |
| 2 | vx-one | 7 | < 1 min | |
| 3 | switch | 12 | < 1 min | |
| 4 | excess | 182 | ~5 min | |
| 5 | xo | 200 | ~5 min | |
| 6 | saffier | 266 | ~7 min | |
| 7 | aquila | 308 | ~8 min | |
| 8 | lagoon | 416 | ~10 min | |
| 9 | axopar | 797 | ~20 min | 747 copias salen de la CDN a 2560 px (no se bajan los originales: ~0,8 GB en total) |
| 10 | beneteau | 1.258 | ~32 min | |
| 11 | solaris | 66 con copia pendiente | manual | Ver abajo: `--restore` la omite |

Total estimado: ≈ 2,2 GB y ≈ 1,5 h de máquina sin atender, más ≈ 360 MB de subida. Haz primero las tres marcas pequeñas
completas (pasos 1 a 5 del ciclo) y revisa su informe antes de seguir con las demás.

## Casos especiales

**Imágenes que fallan por captcha.** Si una descarga devuelve un desafío anti-bots, `--restore` marca la imagen con
`download_status: "requires_manual_download"` y guarda la URL exacta en `manual_download_url`; no se intenta saltar el captcha.
La lista aparece en el resumen de `--restore` y en `export-report.json` (`requires_manual_download`). Esas imágenes se bajan a
mano desde el navegador y se importan como las de Solaris (abajo).

**Solaris.** `--restore` no descarga imágenes de Solaris (su web tiene captcha). Tiene 66 copias pendientes y 142 imágenes sin
resolver (74 FAILED, 68 PENDING). Se mantiene el atajo de una sola vez: bajar los originales a mano en el navegador y ejecutar
`python -m oceanic media <modelo> --from-dir=<carpeta>` (con `mirror.json` para las copias de oceanic.cl, según `CLAUDE.md`).
Después, el mismo ciclo desde el paso 2.

**Corte a mitad.** `--restore` y `publish-r2` son reanudables: vuelve a ejecutar el mismo paso y solo hace lo que falta.

**Errores de subida.** El informe lista los modelos con error; reintenta `--apply`. Si una clave aparece como "existe con otro
contenido", no se pisa: no la fuerces, avisa.

**Imágenes omitidas por diseño** (no son un fallo): `DUPLICATE_OF` (misma imagen que otra del modelo), `PENDING`, `FAILED`,
`requires_manual_download`, y las que no son `THIS_MODEL`.

## Opcional: evaluar Bucket Lock Rules por prefijo de marca

Defensa adicional, **no obligatoria y no sustituye al código**. Solo después de haber verificado la carga de esa marca con
`publish-r2 <marca> --verify-remote` **sin diferencias**, puedes evaluar en Cloudflare las reglas de bloqueo de bucket (Bucket
Lock Rules) para que los objetos bajo el prefijo de esa marca (`<marca>/`) no se puedan borrar ni sobrescribir durante un
periodo. Antes de activar nada, lee en la documentación vigente de Cloudflare cómo funcionan (alcance por prefijo, retención y
si se pueden deshacer), porque aquí no está verificado. Reglas de seguridad para esta decisión:

- Nunca sobre `catalog/latest.json`: es la única clave mutable y se sobrescribe a propósito.
- Nunca sobre el prefijo `usados/`: lo gestiona el administrador del portal y cambia con frecuencia.
- Una marca a la vez, empezando por una pequeña (por ejemplo `oceanic-power/`), y probando que una nueva subida de ese prefijo
  con `publish-r2 --apply` sigue funcionando (no debe pisar nada, así que no debería chocar con el bloqueo).
- Si más adelante una imagen cambia de contenido, genera una clave nueva (el sha256 va en el nombre): el bloqueo no impide
  publicar la versión nueva.

## Al terminar todas las marcas: publicar el export para el portal

El portal importa siempre desde R2 (nunca desde carpetas locales). Con todas las imágenes ya subidas:

```powershell
# Export con las claves reales de r2.json (SIN --preview-keys)
python -m oceanic export-portal --out=..\dist\portal
# Revisa dist\portal\export-report.json: imágenes omitidas, lint_ok false, sin portada, descargas manuales

# Simulación y comprobación de que cada clave de imagen del export existe en R2
python -m oceanic publish-export --out=..\dist\portal
python -m oceanic publish-export --out=..\dist\portal --verify-images

# Subir el catálogo: catalog/{schema_version}/{catalog_hash}/... y, al final, catalog/latest.json
python -m oceanic publish-export --out=..\dist\portal --apply
```

### Versión del codificador en `r2.json`

Los bytes de un WebP dependen de la versión de Pillow y de libwebp, y la clave R2 lleva el hash de esos bytes. Por eso:

- `tools/requirements.txt` fija `pillow==13.0.0` (las demás dependencias siguen con `>=`). Instala siempre con
  `python -m pip install -r requirements.txt`.
- Cada entrada **nueva** de `r2.json` guarda `encoder` (`pillow`, `libwebp`, `quality`, `method`) y, cuando existe, `original_sha256`
  (el hash verificado del original; las copias de la CDN de Axopar no lo tienen). `version` del archivo sigue en 1 y los
  `r2.json` anteriores se leen igual.
- `publish-r2` compara primero con la entrada activa de `r2.json`: si ya registra esa misma clave, la copia cuenta como `ok` y no
  avisa "distinta al inventario", aunque el `sha256` registrado en `images.json` (dato de Nico, que no se toca) sea otro.

Para completar **una sola vez** las entradas ya subidas antes de este cambio (solo agrega los campos que faltan, nunca
sobrescribe, no usa red ni credenciales y no toca `images.json` ni R2):

```powershell
python -m oceanic r2-backfill oceanic-power --pillow=13.0.0 --libwebp=1.6.0            # simulación
python -m oceanic r2-backfill oceanic-power --pillow=13.0.0 --libwebp=1.6.0 --apply    # escribe los r2.json
```

Las versiones se indican a mano a propósito: tienen que ser con las que se generaron esas copias, no las del equipo en el que
se ejecuta el comando. `quality` y `method` salen de `schema/media-rules.json` (75 y 6).

### Historial del puntero y rollback

Antes de sobrescribir `catalog/latest.json`, `publish-export --apply` lo **lee** y, si existe, guarda una copia inmutable en
`catalog/history/<fecha UTC>-<hash>.json` (el hash es el del catálogo al que apuntaba). Si esa copia falla, **no sobrescribe**
el puntero. Para volver a un catálogo ya publicado (necesita las variables `R2_*`, también en simulación, porque lee R2):

```powershell
python -m oceanic publish-export --rollback=<hash>             # simulación: verifica que catalog/<schema_version>/<hash>/index.json existe
python -m oceanic publish-export --rollback=<hash> --apply     # archiva el puntero actual y apunta latest.json a ese catálogo
```

Verifica que el `index.json` de ese catálogo exista y sea coherente (hash, `schema_version`, `preview: false`). Nunca borra:
los catálogos y las copias del historial se conservan.

`publish-export` se niega a subir un export con `preview: true` (el que se genera con `--preview-keys`) o con hashes o
archivos inconsistentes. `catalog/latest.json` es la única clave que se sobrescribe y solo se escribe al final, si todo lo
demás subió sin errores.
