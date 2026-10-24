import bz2
import lzma
import os
import random
import shutil
import subprocess
import tempfile
import unittest

from clonezip import (SevenZipWriter, bzip2_compress, bzip2_decompress,
                      lzma_compress, lzma_decompress)
from clonezip.sevenzip import read_archive

random.seed(1)
CASES = {
    "vide": b"",
    "1 octet": b"a",
    "run court": b"aaaa",
    "run long (>259)": b"z" * 1000,
    "texte": b"abracadabra " * 500,
    "aleatoire": os.urandom(20000),
    "tous octets": bytes(range(256)) * 40,
    "mixte": b"x" * 300 + os.urandom(300) + b"abcabcabc" * 100 + b"\x00" * 500,
    "binaire biaise": bytes(random.choice([0, 0, 0, 1, 255]) for _ in range(30000)),
}


class TestBzip2(unittest.TestCase):
    def test_roundtrip(self):
        for name, d in CASES.items():
            with self.subTest(name):
                self.assertEqual(bzip2_decompress(bzip2_compress(d)), d)

    def test_lisible_par_bz2_officiel(self):
        for name, d in CASES.items():
            with self.subTest(name):
                self.assertEqual(bz2.decompress(bzip2_compress(d)), d)

    def test_lit_les_fichiers_du_bz2_officiel(self):
        for name, d in CASES.items():
            with self.subTest(name):
                self.assertEqual(bzip2_decompress(bz2.compress(d)), d)

    def test_plusieurs_blocs(self):
        d = os.urandom(150000) + b"abc" * 100000
        c = bzip2_compress(d, level=1)  # blocs de 100 Ko
        self.assertEqual(bz2.decompress(c), d)
        self.assertEqual(bzip2_decompress(c), d)

    def test_corruption_detectee(self):
        c = bytearray(bzip2_compress(b"hello world" * 100))
        c[len(c) // 2] ^= 0xFF
        with self.assertRaises(Exception):
            bzip2_decompress(bytes(c))

    @unittest.skipUnless(shutil.which("bzip2"), "bzip2 absent")
    def test_binaire_bzip2(self):
        d = CASES["mixte"]
        r = subprocess.run(["bzip2", "-dc"], input=bzip2_compress(d), capture_output=True)
        self.assertEqual(r.stdout, d)


class TestLzma(unittest.TestCase):
    def test_roundtrip(self):
        for name, d in CASES.items():
            with self.subTest(name):
                self.assertEqual(lzma_decompress(lzma_compress(d)), d)

    def test_lisible_par_lzma_officiel(self):
        for name, d in CASES.items():
            with self.subTest(name):
                self.assertEqual(lzma.decompress(lzma_compress(d), format=lzma.FORMAT_ALONE), d)

    def test_lit_les_fichiers_du_lzma_officiel(self):
        for name, d in CASES.items():
            with self.subTest(name):
                self.assertEqual(lzma_decompress(lzma.compress(d, format=lzma.FORMAT_ALONE)), d)

    @unittest.skipUnless(shutil.which("xz"), "xz absent")
    def test_binaire_xz(self):
        d = CASES["mixte"]
        r = subprocess.run(["xz", "--format=lzma", "-dc"], input=lzma_compress(d), capture_output=True)
        self.assertEqual(r.stdout, d)


class Test7z(unittest.TestCase):
    FILES = [("a.txt", b"hello " * 400), ("sub/b.bin", os.urandom(700)), ("vide.txt", b""), ("c.txt", b"xyz" * 300)]

    def test_solide_et_non_solide(self):
        for solid in (True, False):
            with self.subTest(solid=solid):
                w = SevenZipWriter(solid=solid)
                for n, d in self.FILES:
                    w.add(n, d)
                self.assertEqual(read_archive(w.tobytes()), self.FILES)

    def test_archive_vide(self):
        self.assertEqual(read_archive(SevenZipWriter().tobytes()), [])

    def test_crc_corrompu(self):
        w = SevenZipWriter()
        w.add("a", b"hello " * 400)
        blob = bytearray(w.tobytes())
        blob[40] ^= 0xFF  # dans le flux compressé
        with self.assertRaises(Exception):
            read_archive(bytes(blob))

    def test_cli_extraction(self):
        from clonezip.cli import main
        with tempfile.TemporaryDirectory() as t:
            src = os.path.join(t, "doc.txt")
            open(src, "wb").write(b"bonjour " * 1000)
            arc = os.path.join(t, "x.7z")
            main(["7z", "a", arc, src])
            out = os.path.join(t, "out")
            main(["7z", "x", arc, "-o", out])
            self.assertEqual(open(os.path.join(out, "doc.txt"), "rb").read(), b"bonjour " * 1000)


if __name__ == "__main__":
    unittest.main()
