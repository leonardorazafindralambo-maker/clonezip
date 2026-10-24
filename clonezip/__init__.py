"""clonezip : clones pédagogiques de bzip2, lzma et 7-zip, écrits en Python.

Les formats produits sont les formats réels :
  * .bz2  -> lisible par le vrai `bzip2` et par le module `bz2` de Python
  * .lzma -> lisible par `xz --format=lzma` et par le module `lzma` de Python
  * .7z   -> archive 7-Zip (codeur LZMA, un ou plusieurs fichiers)
"""
from .bzip2 import bzip2_compress, bzip2_decompress
from .lzma import lzma_compress, lzma_decompress
from .sevenzip import SevenZipWriter, create_archive, extract_archive, list_archive

__all__ = [
    "bzip2_compress", "bzip2_decompress",
    "lzma_compress", "lzma_decompress",
    "SevenZipWriter", "create_archive", "extract_archive", "list_archive",
]
__version__ = "1.0.0"
