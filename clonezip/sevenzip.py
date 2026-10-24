"""Clone de 7-Zip : écriture / lecture d'archives .7z (format réel, codeur LZMA).

Structure d'un .7z :
    [SignatureHeader 32 o] [flux compressés] [en-tête (Header)]
Le Header décrit : PackInfo (taille des flux), UnpackInfo (dossiers/codeurs),
SubStreamsInfo (fichiers dans un dossier « solide », CRC32) et FilesInfo (noms...).

Modes : solide (tous les fichiers dans un seul flux LZMA, défaut de 7-Zip, meilleur taux)
        ou non solide (un flux par fichier).
"""
import os
import struct
import zlib

from .lzma import lzma_decode_raw, lzma_encode_raw, props_byte

SIGNATURE = b"7z\xbc\xaf\x27\x1c"

K_END, K_HEADER, K_ARCHIVE_PROPS, K_ADD_STREAMS, K_MAIN_STREAMS = 0x00, 0x01, 0x02, 0x03, 0x04
K_FILES_INFO, K_PACK_INFO, K_UNPACK_INFO, K_SUBSTREAMS = 0x05, 0x06, 0x07, 0x08
K_SIZE, K_CRC, K_FOLDER, K_CODERS_UNPACK_SIZE, K_NUM_UNPACK_STREAM = 0x09, 0x0A, 0x0B, 0x0C, 0x0D
K_EMPTY_STREAM, K_EMPTY_FILE, K_NAMES = 0x0E, 0x0F, 0x11
K_ENCODED_HEADER = 0x17

LZMA_ID = b"\x03\x01\x01"


# ---------------------------------------------------------------- nombres 7z
def write_number(v: int) -> bytes:
    first, mask, i = 0, 0x80, 0
    while i < 8:
        if v < (1 << (7 * (i + 1))):
            first |= v >> (8 * i)
            break
        first |= mask
        mask >>= 1
        i += 1
    out = bytes([first])
    for _ in range(i):
        out += bytes([v & 0xFF])
        v >>= 8
    return out


class Reader:
    def __init__(self, data):
        self.d = data
        self.p = 0

    def byte(self):
        b = self.d[self.p]
        self.p += 1
        return b

    def read(self, n):
        b = self.d[self.p:self.p + n]
        if len(b) != n:
            raise ValueError("en-tête 7z tronqué")
        self.p += n
        return b

    def number(self):
        first = self.byte()
        mask = 0x80
        value = 0
        for i in range(8):
            if first & mask == 0:
                return value + ((first & (mask - 1)) << (8 * i))
            value |= self.byte() << (8 * i)
            mask >>= 1
        return value

    def bits(self, n):
        out = []
        b = 0
        mask = 0
        for _ in range(n):
            if mask == 0:
                b = self.byte()
                mask = 0x80
            out.append(bool(b & mask))
            mask >>= 1
        return out

    def digests(self, n):
        all_def = self.byte()
        defined = [True] * n if all_def else self.bits(n)
        return [struct.unpack("<I", self.read(4))[0] if d else None for d in defined]


def pack_bits(bits):
    out = bytearray()
    for i in range(0, len(bits), 8):
        b = 0
        for j, v in enumerate(bits[i:i + 8]):
            if v:
                b |= 0x80 >> j
        out.append(b)
    return bytes(out)


def _crc(b):
    return zlib.crc32(b) & 0xFFFFFFFF


# ---------------------------------------------------------------- écriture
class SevenZipWriter:
    def __init__(self, solid=True, dict_size=1 << 22):
        self.solid = solid
        self.dict_size = dict_size
        self.files = []  # (nom, données)

    def add(self, name: str, data: bytes):
        self.files.append((name.replace(os.sep, "/"), data))

    def add_path(self, path):
        if os.path.isdir(path):
            base = os.path.dirname(os.path.abspath(path.rstrip("/\\")))
            for root, _, names in os.walk(path):
                for n in sorted(names):
                    full = os.path.join(root, n)
                    self.add(os.path.relpath(full, base), open(full, "rb").read())
        else:
            self.add(os.path.basename(path), open(path, "rb").read())

    def tobytes(self) -> bytes:
        nonempty = [(n, d) for n, d in self.files if len(d) > 0]
        groups = [nonempty] if (self.solid and nonempty) else [[f] for f in nonempty]
        packed, folders = [], []
        for g in groups:
            raw = b"".join(d for _, d in g)
            packed.append(lzma_encode_raw(raw, 3, 0, 2, self.dict_size))
            folders.append((len(raw), [(len(d), _crc(d)) for _, d in g]))

        h = bytearray([K_HEADER])
        if folders:
            h += bytes([K_MAIN_STREAMS])
            h += bytes([K_PACK_INFO]) + write_number(0) + write_number(len(packed))
            h += bytes([K_SIZE]) + b"".join(write_number(len(p)) for p in packed) + bytes([K_END])
            h += bytes([K_UNPACK_INFO, K_FOLDER]) + write_number(len(folders)) + b"\x00"
            props = bytes([props_byte()]) + struct.pack("<I", self.dict_size)
            for _ in folders:
                h += write_number(1)  # nombre de codeurs
                h += bytes([len(LZMA_ID) | 0x20]) + LZMA_ID + write_number(len(props)) + props
            h += bytes([K_CODERS_UNPACK_SIZE]) + b"".join(write_number(f[0]) for f in folders)
            h += bytes([K_END])
            h += bytes([K_SUBSTREAMS, K_NUM_UNPACK_STREAM])
            h += b"".join(write_number(len(f[1])) for f in folders)
            sizes = b""
            for _, subs in folders:
                for s, _c in subs[:-1]:
                    sizes += write_number(s)
            if sizes:
                h += bytes([K_SIZE]) + sizes
            h += bytes([K_CRC, 1]) + b"".join(struct.pack("<I", c) for _, subs in folders for _s, c in subs)
            h += bytes([K_END, K_END])
        # --- FilesInfo
        h += bytes([K_FILES_INFO]) + write_number(len(self.files))
        empties = [len(d) == 0 for _, d in self.files]
        if any(empties):
            v = pack_bits(empties)
            h += bytes([K_EMPTY_STREAM]) + write_number(len(v)) + v
            v = pack_bits([True] * sum(empties))  # ce sont des fichiers, pas des dossiers
            h += bytes([K_EMPTY_FILE]) + write_number(len(v)) + v
        names = b"".join(n.encode("utf-16-le") + b"\x00\x00" for n, _ in self.files)
        h += bytes([K_NAMES]) + write_number(len(names) + 1) + b"\x00" + names
        h += bytes([K_END, K_END])

        body = b"".join(packed)
        header = bytes(h)
        start = struct.pack("<QQI", len(body), len(header), _crc(header))
        sig = SIGNATURE + b"\x00\x04" + struct.pack("<I", _crc(start)) + start
        return sig + body + header

    def write(self, path):
        with open(path, "wb") as f:
            f.write(self.tobytes())


def create_archive(path, inputs, solid=True):
    w = SevenZipWriter(solid=solid)
    for p in inputs:
        w.add_path(p)
    w.write(path)
    return w


# ---------------------------------------------------------------- lecture
class Folder:
    def __init__(self):
        self.coder_id = b""
        self.props = b""
        self.unpack_size = 0
        self.num_substreams = 1
        self.substream_sizes = []
        self.substream_crcs = []


def _read_streams_info(r: Reader):
    pack_pos, pack_sizes, folders = 0, [], []
    while True:
        t = r.byte()
        if t == K_END:
            break
        if t == K_PACK_INFO:
            pack_pos = r.number()
            n = r.number()
            while True:
                tt = r.byte()
                if tt == K_END:
                    break
                if tt == K_SIZE:
                    pack_sizes = [r.number() for _ in range(n)]
                elif tt == K_CRC:
                    r.digests(n)
                else:
                    raise ValueError("propriété PackInfo inconnue")
        elif t == K_UNPACK_INFO:
            if r.byte() != K_FOLDER:
                raise ValueError("Folder attendu")
            nf = r.number()
            if r.byte():
                raise ValueError("données externes non supportées")
            for _ in range(nf):
                f = Folder()
                ncod = r.number()
                if ncod != 1:
                    raise ValueError("seuls les dossiers à un codeur (LZMA) sont supportés")
                flag = r.byte()
                f.coder_id = r.read(flag & 0x0F)
                if flag & 0x10:
                    raise ValueError("codeur complexe non supporté")
                if flag & 0x20:
                    f.props = r.read(r.number())
                folders.append(f)
            if r.byte() != K_CODERS_UNPACK_SIZE:
                raise ValueError("CodersUnpackSize attendu")
            for f in folders:
                f.unpack_size = r.number()
            while True:
                tt = r.byte()
                if tt == K_END:
                    break
                if tt == K_CRC:
                    r.digests(nf)
        elif t == K_SUBSTREAMS:
            for f in folders:
                f.num_substreams = 1
            while True:
                tt = r.byte()
                if tt == K_END:
                    break
                if tt == K_NUM_UNPACK_STREAM:
                    for f in folders:
                        f.num_substreams = r.number()
                elif tt == K_SIZE:
                    for f in folders:
                        sizes = [r.number() for _ in range(max(f.num_substreams - 1, 0))]
                        if f.num_substreams:
                            sizes.append(f.unpack_size - sum(sizes))
                        f.substream_sizes = sizes
                elif tt == K_CRC:
                    total = sum(f.num_substreams for f in folders)
                    crcs = r.digests(total)
                    i = 0
                    for f in folders:
                        f.substream_crcs = crcs[i:i + f.num_substreams]
                        i += f.num_substreams
                else:
                    raise ValueError("propriété SubStreams inconnue")
        else:
            raise ValueError(f"propriété 0x{t:02x} inconnue")
    for f in folders:
        if not f.substream_sizes and f.num_substreams == 1:
            f.substream_sizes = [f.unpack_size]
    return pack_pos, pack_sizes, folders


def _decode_folders(blob, base, pack_pos, pack_sizes, folders):
    outs = []
    off = base + pack_pos
    for f, ps in zip(folders, pack_sizes):
        if f.coder_id != LZMA_ID:
            raise ValueError("seul le codeur LZMA est supporté")
        p = f.props[0]
        pb, rest = divmod(p, 45)
        lp, lc = divmod(rest, 9)
        outs.append(lzma_decode_raw(blob[off:off + ps], 0, f.unpack_size, lc, lp, pb))
        off += ps
    return outs


def _parse(blob: bytes):
    if blob[:6] != SIGNATURE:
        raise ValueError("ce n'est pas une archive 7z")
    next_off, next_size, next_crc = struct.unpack("<QQI", blob[12:32])
    if _crc(blob[12:32]) != struct.unpack("<I", blob[8:12])[0]:
        raise ValueError("CRC de l'en-tête de départ invalide")
    if next_size == 0:
        return [], []
    hdr = blob[32 + next_off:32 + next_off + next_size]
    if _crc(hdr) != next_crc:
        raise ValueError("CRC de l'en-tête invalide")
    r = Reader(hdr)
    t = r.byte()
    while t == K_ENCODED_HEADER:  # en-tête compressé (cas des archives créées par 7-Zip)
        pp, ps, fo = _read_streams_info(r)
        hdr = _decode_folders(blob, 32, pp, ps, fo)[0]
        r = Reader(hdr)
        t = r.byte()
    if t != K_HEADER:
        raise ValueError("en-tête 7z invalide")
    folders, pack_pos, pack_sizes = [], 0, []
    files = []
    while True:
        t = r.byte()
        if t == K_END:
            break
        if t == K_ARCHIVE_PROPS:
            while r.byte() != K_END:
                r.read(r.number())
        elif t == K_MAIN_STREAMS:
            pack_pos, pack_sizes, folders = _read_streams_info(r)
        elif t == K_FILES_INFO:
            nfiles = r.number()
            empty_stream = [False] * nfiles
            empty_file = []
            names = []
            while True:
                pt = r.byte()
                if pt == K_END:
                    break
                size = r.number()
                end = r.p + size
                if pt == K_EMPTY_STREAM:
                    empty_stream = r.bits(nfiles)
                elif pt == K_EMPTY_FILE:
                    empty_file = r.bits(sum(empty_stream))
                elif pt == K_NAMES:
                    r.byte()  # external
                    raw = r.d[r.p:end]
                    names = raw.decode("utf-16-le").split("\x00")[:nfiles]
                r.p = end
            files = (nfiles, empty_stream, empty_file, names)
        else:
            raise ValueError(f"propriété d'en-tête 0x{t:02x} inconnue")
    if not files:
        return [], []
    nfiles, empty_stream, empty_file, names = files
    entries = []
    ei = 0
    si = 0
    for i in range(nfiles):
        if empty_stream[i]:
            is_dir = not (empty_file[ei] if ei < len(empty_file) else False)
            ei += 1
            entries.append({"name": names[i], "size": 0, "dir": is_dir, "stream": None, "crc": None})
        else:
            entries.append({"name": names[i], "dir": False, "stream": si, "crc": None})
            si += 1
    # associer sous-flux -> (dossier, offset)
    subs = []
    for fi, f in enumerate(folders):
        off = 0
        for k in range(f.num_substreams):
            subs.append((fi, off, f.substream_sizes[k], f.substream_crcs[k] if f.substream_crcs else None))
            off += f.substream_sizes[k]
    for e in entries:
        if e["stream"] is not None:
            fi, off, size, crc = subs[e["stream"]]
            e.update(folder=fi, offset=off, size=size, crc=crc)
    return entries, (pack_pos, pack_sizes, folders)


def list_archive(path):
    blob = open(path, "rb").read()
    entries, _ = _parse(blob)
    return [(e["name"], e["size"], e["dir"]) for e in entries]


def read_archive(blob: bytes):
    """Retourne la liste [(nom, données)] en vérifiant les CRC."""
    entries, info = _parse(blob)
    result = []
    if not entries:
        return result
    outs = _decode_folders(blob, 32, *info) if info[2] else []
    for e in entries:
        if e["dir"]:
            result.append((e["name"], None))
        elif e["stream"] is None:
            result.append((e["name"], b""))
        else:
            data = outs[e["folder"]][e["offset"]:e["offset"] + e["size"]]
            if e["crc"] is not None and _crc(data) != e["crc"]:
                raise ValueError(f"CRC invalide pour {e['name']}")
            result.append((e["name"], data))
    return result


def extract_archive(path, dest="."):
    blob = open(path, "rb").read()
    out = []
    for name, data in read_archive(blob):
        target = os.path.normpath(os.path.join(dest, name))
        if not os.path.abspath(target).startswith(os.path.abspath(dest)):
            raise ValueError(f"chemin dangereux ignoré : {name}")
        if data is None:
            os.makedirs(target, exist_ok=True)
            continue
        os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
        with open(target, "wb") as f:
            f.write(data)
        out.append(target)
    return out
