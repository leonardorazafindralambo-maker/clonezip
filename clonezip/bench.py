"""Banc d'essai : nos clones vs. implémentations de référence (modules bz2 / lzma de Python)."""
import bz2
import lzma
import os
import time

from .bzip2 import bzip2_compress, bzip2_decompress
from .lzma import lzma_compress, lzma_decompress
from .sevenzip import SevenZipWriter, read_archive


def measure(data, name):
    rows = []

    def run(label, comp, decomp):
        t = time.time()
        c = comp(data)
        tc = time.time() - t
        t = time.time()
        ok = decomp(c) == data
        td = time.time() - t
        rows.append((label, len(c), tc, td, ok))

    run("bzip2 (clone)", bzip2_compress, bzip2_decompress)
    run("bzip2 (référence)", bz2.compress, bz2.decompress)
    run("lzma (clone)", lzma_compress, lzma_decompress)
    run("lzma (référence)", lambda d: lzma.compress(d, format=lzma.FORMAT_ALONE),
        lambda c: lzma.decompress(c, format=lzma.FORMAT_ALONE))

    def mk7(d):
        w = SevenZipWriter()
        w.add(name, d)
        return w.tobytes()

    run("7z (clone)", mk7, lambda c: read_archive(c)[0][1])
    return rows


def run_bench(files):
    for path in files:
        data = open(path, "rb").read()
        name = os.path.basename(path)
        print(f"\n=== {name} ({len(data)} octets) ===")
        print(f"{'Algorithme':<20}{'Taille':>10}{'Ratio':>9}{'Compr.':>9}{'Décompr.':>10}  Intègre")
        for label, size, tc, td, ok in measure(data, name):
            print(f"{label:<20}{size:>10}{size / len(data) * 100:>8.1f}%{tc:>8.2f}s{td:>9.2f}s  {'OK' if ok else 'ERREUR'}")
