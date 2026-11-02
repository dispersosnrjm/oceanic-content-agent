"""Oceanic Content Engine CLI.

    cd tools
    python -m oceanic extract axopar <raw.html> <url> <out.json>
    python -m oceanic render     <model_dir>   # tabla + specifications.md + validación
    python -m oceanic media      <model_dir>   # descarga imágenes y documentos
    python -m oceanic readiness  <model_dir>   # CONTENT_STATUS
    python -m oceanic check      [biblioteca]  # render + readiness de todos los modelos
    python -m oceanic build      axopar <extract.json>...   # genera paquetes desde extracts
    python -m oceanic media      <model_dir> [--cdn]        # --cdn: copia web desde el CDN (rápido)
    python -m oceanic zip        [--name=X] <dir> [<dir>...] # ZIP en dist/ para subir a Drive
"""

from __future__ import annotations

import json
import sys
import zipfile
from datetime import date
from pathlib import Path

from . import media, readiness, specs

ROOT = Path(__file__).resolve().parents[2]


def _models(base: Path):
    return sorted(p.parent.parent for p in base.glob("*/*/00_MODELO/00_MODELO.md"))


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 1
    cmd, args = argv[0], argv[1:]
    if cmd == "extract":
        # --accessed=YYYY-MM-DD: date the raw HTML was captured (re-extracting an old capture keeps its date)
        accessed = next((a.split("=", 1)[1] for a in args if a.startswith("--accessed=")), date.today().isoformat())
        brand, raw, url, out = [a for a in args if not a.startswith("--accessed=")]
        adapter = __import__(f"oceanic.adapters.{brand}", fromlist=["extract"])
        data = adapter.extract(Path(raw).read_text(), url, accessed)
        Path(out).write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n")
        print(f"{len(data['images'])} imágenes, {len(data['videos'])} videos -> {out}")
        return 0
    if cmd == "render":
        errors = specs.render(Path(args[0]))
        for e in errors:
            print("ERROR", e)
        return 1 if errors else 0
    if cmd == "media":
        model = Path(args[0])
        if "--restore" in args:
            return media.restore_main(args, ROOT)
        local = next((a.split("=", 1)[1] for a in args if a.startswith("--from-dir=")), None)
        print("imágenes:", media.download_images(model, f"dir:{local}" if local else "cdn" if "--cdn" in args else "original"))
        print("documentos:", media.download_documents(model))
        readiness.write(model)
        return 0
    if cmd == "publish-r2":
        from . import r2
        return r2.main_cli(args, ROOT)
    if cmd == "usados-fotos":
        from . import usados_media
        return usados_media.main_download(args, ROOT)
    if cmd == "publish-usados":
        from . import usados_media
        return usados_media.main_publish(args, ROOT)
    if cmd == "backup-photos":
        from . import backup
        return backup.main_backup(args, ROOT)
    if cmd == "restore-photos":
        from . import backup
        return backup.main_restore(args, ROOT)
    if cmd == "r2-backfill":
        from . import r2
        return r2.main_backfill(args, ROOT)
    if cmd == "publish-export":
        from . import catalog
        return catalog.main_cli(args, ROOT)
    if cmd == "export-portal":
        from . import exporter
        return exporter.main_cli(args, ROOT)
    if cmd == "optimize":
        for model in sorted(p.parent.parent for p in Path(args[0]).rglob("00_MODELO/00_MODELO.md")):
            st = media.optimize_images(model)
            print(f"{model.name}: {st['optimized']} optimizadas, "
                  f"{st['bytes_before'] / 1048576:.1f} → {st['bytes_after'] / 1048576:.1f} MB")
        return 0
    if cmd == "readiness":
        result = readiness.write(Path(args[0]))
        print(result["CONTENT_STATUS"], f"{result['completeness_pct']}%")
        for k, v in result["items"].items():
            print(f"  {v['status']:8} {v['group']:10} {k:16} {v['detail']}")
        return 0
    if cmd == "check":
        base = Path(args[0]) if args else ROOT / "biblioteca"
        failed = 0
        for model in _models(base):
            errors = specs.render(model)
            result = readiness.write(model)
            print(f"{result['CONTENT_STATUS']:6} {model.relative_to(base)}")
            for e in errors:
                print("   ERROR", e)
            failed += bool(errors)
        return 1 if failed else 0
    if cmd == "build":
        brand, extracts = args[0], args[1:]
        builder = __import__(f"oceanic.builders.{brand}", fromlist=["build"])
        drafts = ROOT / "drafts" / brand
        if hasattr(builder, "prepare"):  # cross-model checks (media shared between model pages)
            builder.prepare([json.loads(Path(p).read_text()) for p in extracts])
        for path in extracts:
            ext = json.loads(Path(path).read_text())
            slug = Path(path).stem
            try:
                model = builder.build(slug, ext, drafts)
            except SystemExit as exc:
                print(exc)
                continue
            errors = specs.render(model)
            result = readiness.write(model)
            print(f"{result['CONTENT_STATUS']:6} {model.name}" + "".join(f"\n   ERROR {e}" for e in errors))
        return 0
    if cmd == "zip":
        label = next((a.split("=", 1)[1] for a in args if a.startswith("--name=")), None)
        dirs = [Path(a).resolve() for a in args if not a.startswith("--name=")]
        dist = ROOT / "dist"
        dist.mkdir(exist_ok=True)
        base = ROOT / "biblioteca"
        name = label or "__".join(d.name for d in dirs[:2]) + ("__etc" if len(dirs) > 2 else "")
        out = dist / f"oceanic-biblioteca__{name}__{date.today().isoformat()}.zip"
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
            for d in dirs:
                for f in sorted(d.rglob("*")):
                    if f.is_file():
                        # WebP is already compressed.
                        mode = zipfile.ZIP_STORED if f.suffix == ".webp" else zipfile.ZIP_DEFLATED
                        zf.write(f, f.relative_to(base), compress_type=mode)
        print(f"{out} ({out.stat().st_size / 1048576:.1f} MB)")
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
