# clonezip — clones de bzip2, lzma et 7-zip en Python

Projet d'examen *Codage et traitement du signal* : clone fonctionnel des utilitaires de compression
**bzip2**, **lzma** et **7-zip**, écrits de zéro (seule dépendance : `numpy`, utilisé pour accélérer le tri de la BWT).

Les formats de fichiers sont les **formats réels** :

| Clone | Format | Compatible avec |
|-------|--------|-----------------|
| `bzip2` | `.bz2` | binaire `bzip2`, module Python `bz2` (dans les deux sens) |
| `lzma`  | `.lzma` (« LZMA alone ») | binaire `xz --format=lzma`, module Python `lzma` (dans les deux sens) |
| `7z`    | `.7z` (codeur LZMA, solide ou non) | relu par notre propre lecteur (CRC vérifiés) ; format conforme à la spécification 7z |

## Installation

```bash
git clone <url-du-depot> && cd clonezip
pip install -r requirements.txt      # numpy (+ reportlab, matplotlib, pillow pour le rapport)
```

## Utilisation

```bash
python -m clonezip bzip2 fichier.txt            # -> fichier.txt.bz2  (-k garde l'original, -l 1..9 : taille de bloc)
python -m clonezip bzip2 -d fichier.txt.bz2
python -m clonezip lzma  fichier.wav            # -> fichier.wav.lzma
python -m clonezip lzma  -d fichier.wav.lzma
python -m clonezip 7z a archive.7z dossier/ photo.bmp     # créer  (--no-solid : un flux par fichier)
python -m clonezip 7z l archive.7z                        # lister
python -m clonezip 7z x archive.7z -o sortie/             # extraire
python -m clonezip 7z t archive.7z                        # tester les CRC
python -m clonezip bench fichier1 fichier2 ...            # comparer clones / outils de référence
```

Utilisable aussi comme bibliothèque :

```python
from clonezip import bzip2_compress, bzip2_decompress, lzma_compress, lzma_decompress
assert lzma_decompress(lzma_compress(b"bonjour" * 100)) == b"bonjour" * 100
```

## Reproduire la démonstration (4 types de fichiers)

```bash
python scripts/generate_samples.py samples   # texte, audio WAV, image BMP, vidéo Y4M (+ MP4 si ffmpeg)
python scripts/make_report.py                # exécute tout et produit rapport_compression.pdf
python -m unittest discover -s tests -v      # 14 tests (dont interopérabilité avec bzip2 / xz)
```

## Fonctionnement

### bzip2 (`clonezip/bzip2.py`)
1. **RLE1** : suites de 4 à 259 octets identiques → 4 octets + compteur.
2. **BWT** : tri des rotations du bloc (doublage de préfixes) ; on garde la dernière colonne et l'index d'origine.
3. **MTF + RLE2** : *move-to-front*, puis les séquences de zéros sont codées avec RUNA/RUNB (base 2 bijective).
4. **Huffman canonique** : 2 à 6 tables optimisées en 4 itérations, un sélecteur par groupe de 50 symboles.
5. CRC32 par bloc et CRC combiné de fin de flux.

### lzma (`clonezip/lzma.py`)
1. **LZ77** par chaînes de hachage (fenêtre 4 Mo, longueurs 2–273), avec réutilisation des 4 dernières distances (rep0–rep3) et *shortrep*.
2. **Machine à 12 états** et modèles de probabilités adaptatifs (11 bits) : littéraux contextuels (octet précédent), *matched literals*,
   longueurs (bas/milieu/haut), distances (slot + bits directs + *align*).
3. **Range coder** binaire (codeur d'intervalles) avec propagation de retenue.

### 7-zip (`clonezip/sevenzip.py`)
En-tête de signature `7z¼¯'\x1c` + flux LZMA + en-tête (PackInfo, UnpackInfo/Folder, SubStreamsInfo, FilesInfo, noms UTF-16, CRC32).
Mode **solide** (défaut, meilleur taux) ou non solide. La lecture comprend aussi les en-têtes compressés (`kEncodedHeader`).

## Limites
* Python pur : nettement plus lent que les outils officiels (C) ; prévu pour des fichiers de quelques Mo.
* LZMA : analyse gloutonne (l'encodeur officiel fait un *parsing* optimal) → fichiers 0 à ~30 % plus gros.
* 7z : uniquement le codeur LZMA (pas de BCJ, PPMd, AES, multi-volumes) ; l'archive créée n'a pas pu être testée avec le binaire `7z` officiel dans mon environnement de développement.
* bzip2 : blocs « randomisés » (format obsolète) non supportés.
