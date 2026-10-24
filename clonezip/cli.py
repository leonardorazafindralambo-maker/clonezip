"""Interface en ligne de commande : python -m clonezip {bzip2,lzma,7z,bench} ..."""
import argparse
import os
import sys
import time

from . import bzip2 as _bz
from . import lzma as _lz
from . import sevenzip as _sz


def _human(n):
    for u in ("o", "Ko", "Mo", "Go"):
        if n < 1024 or u == "Go":
            return f"{n:.0f} {u}" if u == "o" else f"{n:.2f} {u}"
        n /= 1024


def _report(verb, src, dst, a, b, dt):
    if verb.startswith("décompr"):
        print(f"{verb:<12} {src} -> {dst} : {_human(a)} -> {_human(b)}  [{dt:.2f} s]")
        return
    ratio = (b / a * 100) if a else 0
    print(f"{verb:<12} {src} -> {dst} : {_human(a)} -> {_human(b)}  "
          f"({ratio:.1f} % de l'original, gain {100 - ratio:.1f} %)  [{dt:.2f} s]")


def _single(args, comp, decomp, ext, name):
    for path in args.files:
        data = open(path, "rb").read()
        t = time.time()
        if args.decompress:
            if not path.endswith(ext):
                print(f"{name}: {path} n'a pas l'extension {ext}", file=sys.stderr)
                return 1
            out = decomp(data)
            dst = path[:-len(ext)]
            verb = "décompressé"
        else:
            out = comp(data)
            dst = path + ext
            verb = "compressé"
        dt = time.time() - t
        if os.path.exists(dst) and not args.force:
            print(f"{name}: {dst} existe déjà (utilisez -f)", file=sys.stderr)
            return 1
        if args.stdout:
            sys.stdout.buffer.write(out)
        else:
            open(dst, "wb").write(out)
            _report(verb, path, dst, len(data), len(out), dt)
            if not args.keep:
                os.remove(path)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="clonezip", description="Clones de bzip2, lzma et 7-zip")
    sub = ap.add_subparsers(dest="cmd", required=True)

    for name in ("bzip2", "lzma"):
        p = sub.add_parser(name, help=f"clone de {name}")
        p.add_argument("-d", "--decompress", action="store_true")
        p.add_argument("-k", "--keep", action="store_true", help="garder le fichier source")
        p.add_argument("-f", "--force", action="store_true")
        p.add_argument("-c", "--stdout", action="store_true")
        if name == "bzip2":
            p.add_argument("-l", "--level", type=int, default=9, choices=range(1, 10))
        p.add_argument("files", nargs="+")

    p7 = sub.add_parser("7z", help="clone de 7-zip (a = ajouter, x = extraire, l = lister, t = tester)")
    p7.add_argument("action", choices=["a", "x", "l", "t"])
    p7.add_argument("archive")
    p7.add_argument("files", nargs="*")
    p7.add_argument("-o", "--output", default=".")
    p7.add_argument("--no-solid", action="store_true")

    pb = sub.add_parser("bench", help="compare les 3 algorithmes (et les outils de référence) sur des fichiers")
    pb.add_argument("files", nargs="+")

    args = ap.parse_args(argv)
    if args.cmd == "bzip2":
        return _single(args, lambda d: _bz.bzip2_compress(d, args.level), _bz.bzip2_decompress, ".bz2", "bzip2")
    if args.cmd == "lzma":
        return _single(args, _lz.lzma_compress, _lz.lzma_decompress, ".lzma", "lzma")
    if args.cmd == "7z":
        if args.action == "a":
            t = time.time()
            w = _sz.create_archive(args.archive, args.files, solid=not args.no_solid)
            raw = sum(len(d) for _, d in w.files)
            _report("archivé", f"{len(w.files)} fichier(s)", args.archive, raw, os.path.getsize(args.archive), time.time() - t)
        elif args.action == "l":
            print(f"{'Taille':>12}  Nom")
            for name, size, is_dir in _sz.list_archive(args.archive):
                print(f"{size:>12}  {name}{'/' if is_dir else ''}")
        elif args.action == "x":
            for f in _sz.extract_archive(args.archive, args.output):
                print("extrait :", f)
        elif args.action == "t":
            n = len(_sz.read_archive(open(args.archive, "rb").read()))
            print(f"OK : {n} entrée(s), tous les CRC sont valides.")
        return 0
    if args.cmd == "bench":
        from .bench import run_bench
        run_bench(args.files)
        return 0


if __name__ == "__main__":
    sys.exit(main())
