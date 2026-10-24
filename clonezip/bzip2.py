"""Clone de bzip2 (format .bz2 réel).

Pipeline de compression, identique à bzip2 :
    RLE1  ->  BWT (Burrows-Wheeler)  ->  MTF + RLE2 (RUNA/RUNB)  ->  Huffman

* RLE1 : les séquences de 4 à 259 octets identiques sont codées « 4 octets + compteur ».
* BWT  : tri des rotations du bloc (doublage de préfixes, vectorisé avec numpy).
* MTF  : « move-to-front » ; les zéros consécutifs sont codés en base 2 bijective
         avec les symboles RUNA / RUNB.
* Huffman canonique (longueur max 17 bits), 2 à 6 tables optimisées en 4 itérations,
  un sélecteur (codé en MTF) par groupe de 50 symboles.
"""
import heapq
from itertools import groupby

import numpy as np

BLOCK_MAGIC = 0x314159265359
END_MAGIC = 0x177245385090
GROUP_SIZE = 50
MAX_CODE_LEN = 17

# ---------------------------------------------------------------- CRC32 (MSB first)
_CRC_TABLE = []
for _i in range(256):
    _c = _i << 24
    for _ in range(8):
        _c = ((_c << 1) ^ 0x04C11DB7) if _c & 0x80000000 else (_c << 1)
        _c &= 0xFFFFFFFF
    _CRC_TABLE.append(_c)


def bz_crc(data) -> int:
    crc = 0xFFFFFFFF
    t = _CRC_TABLE
    for b in data:
        crc = ((crc << 8) & 0xFFFFFFFF) ^ t[(crc >> 24) ^ b]
    return crc ^ 0xFFFFFFFF


# ---------------------------------------------------------------- bits
class BitWriter:
    def __init__(self):
        self.buf = bytearray()
        self.acc = 0
        self.n = 0

    def write(self, value, nbits):
        self.acc = (self.acc << nbits) | (value & ((1 << nbits) - 1))
        self.n += nbits
        while self.n >= 8:
            self.n -= 8
            self.buf.append((self.acc >> self.n) & 0xFF)
        self.acc &= (1 << self.n) - 1

    def flush(self):
        if self.n:
            self.write(0, 8 - self.n)


class BitReader:
    def __init__(self, data):
        self.data = bytes(data) + b"\0" * 8
        self.pos = 0  # en bits

    def peek(self, n):
        byte = self.pos >> 3
        chunk = int.from_bytes(self.data[byte:byte + 4], "big")
        return (chunk >> (32 - (self.pos & 7) - n)) & ((1 << n) - 1)

    def skip(self, n):
        self.pos += n

    def read(self, n):
        v = 0
        while n > 24:
            v = (v << 24) | self.peek(24)
            self.pos += 24
            n -= 24
        v = (v << n) | self.peek(n)
        self.pos += n
        return v

    def bit(self):
        v = (self.data[self.pos >> 3] >> (7 - (self.pos & 7))) & 1
        self.pos += 1
        return v

    def align(self):
        self.pos = (self.pos + 7) & ~7

    def eof(self):
        return (self.pos >> 3) >= len(self.data) - 8


# ---------------------------------------------------------------- étapes de compression
def rle1(data: bytes) -> bytes:
    out = bytearray()
    for b, g in groupby(data):
        r = len(list(g))
        while r > 0:
            k = min(r, 259)
            if k >= 4:
                out += bytes([b]) * 4 + bytes([k - 4])
            else:
                out += bytes([b]) * k
            r -= k
    return bytes(out)


def bwt(block: bytes):
    """Retourne (dernière colonne, index de la rotation d'origine)."""
    n = len(block)
    a = np.frombuffer(block, dtype=np.uint8).astype(np.int64)
    rank = a.copy()
    k = 1
    while True:
        key = rank * (n + 256) + np.roll(rank, -k)
        sa = np.argsort(key, kind="stable")
        sk = key[sa]
        diff = np.concatenate(([0], (sk[1:] != sk[:-1]).astype(np.int64)))
        rank = np.empty(n, dtype=np.int64)
        rank[sa] = np.cumsum(diff)
        if rank.max() == n - 1 or 2 * k >= n:
            break
        k *= 2
    last = a[(sa - 1) % n]
    orig_ptr = int(np.nonzero(sa == 0)[0][0])
    return last.astype(np.uint8).tobytes(), orig_ptr


def mtf_rle2(last: bytes, used):
    """MTF + codage des zéros (RUNA=0, RUNB=1). Retourne la liste de symboles (EOB inclus)."""
    index = {b: i for i, b in enumerate(used)}
    mtf = list(range(len(used)))
    out = []
    zrun = 0

    def flush(r):
        while r > 0:
            if r & 1:
                out.append(0)
                r = (r - 1) >> 1
            else:
                out.append(1)
                r = (r - 2) >> 1

    for b in last:
        v = index[b]
        pos = mtf.index(v)
        if pos == 0:
            zrun += 1
            continue
        flush(zrun)
        zrun = 0
        mtf.pop(pos)
        mtf.insert(0, v)
        out.append(pos + 1)
    flush(zrun)
    out.append(len(used) + 1)  # EOB
    return out


def huffman_lengths(freqs, maxlen=MAX_CODE_LEN):
    f = [max(x, 1) for x in freqs]
    n = len(f)
    while True:
        heap = [(w, i) for i, w in enumerate(f)]
        heapq.heapify(heap)
        parent = [-1] * (2 * n)
        nxt = n
        while len(heap) > 1:
            w1, a = heapq.heappop(heap)
            w2, b = heapq.heappop(heap)
            parent[a] = parent[b] = nxt
            heapq.heappush(heap, (w1 + w2, nxt))
            nxt += 1
        depth = [0] * nxt
        for node in range(nxt - 2, -1, -1):
            depth[node] = depth[parent[node]] + 1
        lens = depth[:n]
        if max(lens) <= maxlen:
            return lens
        f = [(x >> 1) + 1 for x in f]


def canonical_codes(lens):
    codes = [0] * len(lens)
    code = 0
    for l in range(min(lens), max(lens) + 1):
        for s, sl in enumerate(lens):
            if sl == l:
                codes[s] = code
                code += 1
        code <<= 1
    return codes


def _optimise_tables(syms, alpha, n_groups):
    """Comme bzip2 : plusieurs tables de Huffman, une par groupe de 50 symboles (4 itérations)."""
    freq = [0] * alpha
    for x in syms:
        freq[x] += 1
    # répartition initiale de l'alphabet en n_groups plages de fréquence cumulée comparable
    lens = []
    rem, gs = len(syms), 0
    for t in range(n_groups, 0, -1):
        target = rem // t
        ge, acc = gs - 1, 0
        while acc < target and ge < alpha - 1:
            ge += 1
            acc += freq[ge]
        lens.append([0 if gs <= v <= ge else 15 for v in range(alpha)])
        rem -= acc
        gs = ge + 1
    arr = np.array(syms, dtype=np.int64)
    n_sel = (len(arr) + GROUP_SIZE - 1) // GROUP_SIZE
    padded = np.zeros(n_sel * GROUP_SIZE, dtype=np.int64)
    padded[:len(arr)] = arr
    valid = np.zeros(n_sel * GROUP_SIZE, dtype=bool)
    valid[:len(arr)] = True
    sel = np.zeros(n_sel, dtype=np.int64)
    for _ in range(4):
        cost = np.stack([(np.array(l)[padded] * valid).reshape(n_sel, GROUP_SIZE).sum(1) for l in lens], axis=1)
        sel = cost.argmin(axis=1)
        new_lens = []
        for t in range(n_groups):
            mask = np.repeat(sel == t, GROUP_SIZE) & valid
            f = np.bincount(padded[mask], minlength=alpha).tolist()
            new_lens.append(huffman_lengths(f))
        lens = new_lens
    return lens, sel.tolist()


def _encode_block(bw: BitWriter, raw: bytes, level_limit: int):
    crc = bz_crc(raw)
    data = rle1(raw)
    assert len(data) <= level_limit
    last, orig_ptr = bwt(data)
    used = sorted(set(last))
    syms = mtf_rle2(last, used)
    alpha = len(used) + 2

    n_groups = 2 if len(syms) < 200 else 3 if len(syms) < 600 else 4 if len(syms) < 1200 else 5 if len(syms) < 2400 else 6
    tables, selectors = _optimise_tables(syms, alpha, n_groups)
    codes_t = [canonical_codes(l) for l in tables]

    bw.write(BLOCK_MAGIC, 48)
    bw.write(crc, 32)
    bw.write(0, 1)  # non randomisé
    bw.write(orig_ptr, 24)
    # table des symboles utilisés
    in_use16 = 0
    for b in used:
        in_use16 |= 1 << (15 - (b >> 4))
    bw.write(in_use16, 16)
    for g in range(16):
        if in_use16 & (1 << (15 - g)):
            m = 0
            for b in used:
                if b >> 4 == g:
                    m |= 1 << (15 - (b & 15))
            bw.write(m, 16)
    # sélecteurs (MTF + unaire) et tables
    bw.write(n_groups, 3)
    bw.write(len(selectors), 15)
    order = list(range(n_groups))
    for sel in selectors:
        j = order.index(sel)
        order.pop(j)
        order.insert(0, sel)
        for _ in range(j):
            bw.write(1, 1)
        bw.write(0, 1)
    for lens in tables:
        cur = lens[0]
        bw.write(cur, 5)
        for l in lens:
            while cur < l:
                bw.write(0b10, 2)
                cur += 1
            while cur > l:
                bw.write(0b11, 2)
                cur -= 1
            bw.write(0, 1)
    for gi, sel in enumerate(selectors):
        lens, codes = tables[sel], codes_t[sel]
        for s in syms[gi * GROUP_SIZE:(gi + 1) * GROUP_SIZE]:
            bw.write(codes[s], lens[s])
    return crc


def bzip2_compress(data: bytes, level: int = 9) -> bytes:
    if not 1 <= level <= 9:
        raise ValueError("niveau 1..9")
    bw = BitWriter()
    for ch in b"BZh" + str(level).encode():
        bw.write(ch, 8)
    limit = level * 100000 - 19
    chunk = int(limit / 1.25)
    combined = 0
    for start in range(0, len(data), chunk):
        crc = _encode_block(bw, data[start:start + chunk], limit)
        combined = (((combined << 1) | (combined >> 31)) & 0xFFFFFFFF) ^ crc
    bw.write(END_MAGIC, 48)
    bw.write(combined, 32)
    bw.flush()
    return bytes(bw.buf)


# ---------------------------------------------------------------- décompression
def _build_table(lens):
    maxlen = max(lens)
    codes = canonical_codes(lens)
    table = [0] * (1 << maxlen)
    for s, (c, l) in enumerate(zip(codes, lens)):
        lo = c << (maxlen - l)
        hi = (c + 1) << (maxlen - l)
        table[lo:hi] = [(s << 5) | l] * (hi - lo)
    return table, maxlen


def _decode_block(br: BitReader):
    stored_crc = br.read(32)
    if br.read(1):
        raise ValueError("blocs randomisés non supportés")
    orig_ptr = br.read(24)
    in_use16 = br.read(16)
    used = []
    for g in range(16):
        if in_use16 & (1 << (15 - g)):
            m = br.read(16)
            for j in range(16):
                if m & (1 << (15 - j)):
                    used.append(g * 16 + j)
    alpha = len(used) + 2
    n_groups = br.read(3)
    n_sel = br.read(15)
    mtf_sel = list(range(n_groups))
    selectors = []
    for _ in range(n_sel):
        j = 0
        while br.bit():
            j += 1
        v = mtf_sel.pop(j)
        mtf_sel.insert(0, v)
        selectors.append(v)
    tables = []
    for _ in range(n_groups):
        cur = br.read(5)
        lens = []
        for _ in range(alpha):
            while br.bit():
                cur += -1 if br.bit() else 1
            lens.append(cur)
        tables.append(_build_table(lens))

    eob = len(used) + 1
    mtf = list(range(len(used)))
    out = bytearray()
    run = 0
    nrun = 1
    gcount = 0
    sel_i = 0
    while True:
        if gcount == 0:
            table, maxlen = tables[selectors[sel_i]]
            sel_i += 1
            gcount = GROUP_SIZE
        gcount -= 1
        v = table[br.peek(maxlen)]
        br.skip(v & 31)
        sym = v >> 5
        if sym <= 1:
            run += nrun << sym  # RUNA: +N, RUNB: +2N
            nrun <<= 1
            continue
        if run:
            out += bytes([used[mtf[0]]]) * run
            run = 0
            nrun = 1
        if sym == eob:
            break
        x = mtf.pop(sym - 1)
        mtf.insert(0, x)
        out.append(used[x])

    last = np.frombuffer(bytes(out), dtype=np.uint8)
    n = len(last)
    nxt = np.argsort(last, kind="stable").tolist()
    lst = bytes(out)
    res = bytearray(n)
    p = nxt[orig_ptr]
    for i in range(n):
        res[i] = lst[p]
        p = nxt[p]
    # RLE1 inverse
    final = bytearray()
    i = 0
    cnt = 0
    prev = -1
    m = len(res)
    while i < m:
        b = res[i]
        i += 1
        if cnt == 4:
            final += bytes([prev]) * b
            cnt = 0
            prev = -1
            continue
        final.append(b)
        if b == prev:
            cnt += 1
        else:
            prev = b
            cnt = 1
    if bz_crc(final) != stored_crc:
        raise ValueError("CRC de bloc invalide")
    return bytes(final), stored_crc


def bzip2_decompress(blob: bytes) -> bytes:
    br = BitReader(blob)
    result = bytearray()
    first = True
    while True:
        if not first and (br.eof() or br.peek(24) != 0x425A68):
            break
        first = False
        if br.read(24) != 0x425A68:
            raise ValueError("signature 'BZh' absente")
        level = br.read(8) - 0x30
        if not 1 <= level <= 9:
            raise ValueError("niveau invalide")
        combined = 0
        while True:
            magic = br.read(48)
            if magic == END_MAGIC:
                if br.read(32) != combined:
                    raise ValueError("CRC global invalide")
                br.align()
                break
            if magic != BLOCK_MAGIC:
                raise ValueError("signature de bloc invalide")
            block, crc = _decode_block(br)
            combined = (((combined << 1) | (combined >> 31)) & 0xFFFFFFFF) ^ crc
            result += block
    return bytes(result)
