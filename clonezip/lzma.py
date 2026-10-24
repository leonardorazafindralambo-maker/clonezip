"""Clone de LZMA (format .lzma « alone » réel, lc=3 lp=0 pb=2).

Composants :
  * recherche de répétitions LZ77 par chaînes de hachage (fenêtre jusqu'à 4 Mo) ;
  * codage « range coder » binaire adaptatif (probabilités 11 bits) ;
  * modèle d'état LZMA : littéraux (contexte = octet précédent, « matched literal »),
    correspondances, rep0..rep3, shortrep, longueurs et distances (slots + bits directs + align).
"""
import struct

PROB_INIT = 1024
TOP = 1 << 24
MAX_LEN = 273


# ================================================================ range coder
class RangeEncoder:
    def __init__(self):
        self.low = 0
        self.range = 0xFFFFFFFF
        self.cache = 0
        self.cache_size = 1
        self.out = bytearray()

    def _shift_low(self):
        low = self.low
        if low < 0xFF000000 or low > 0xFFFFFFFF:
            carry = low >> 32
            temp = self.cache
            while True:
                self.out.append((temp + carry) & 0xFF)
                temp = 0xFF
                self.cache_size -= 1
                if self.cache_size == 0:
                    break
            self.cache = (low >> 24) & 0xFF
        self.cache_size += 1
        self.low = (low & 0x00FFFFFF) << 8

    def bit(self, probs, i, b):
        p = probs[i]
        bound = (self.range >> 11) * p
        if b == 0:
            self.range = bound
            probs[i] = p + ((2048 - p) >> 5)
        else:
            self.low += bound
            self.range -= bound
            probs[i] = p - (p >> 5)
        while self.range < TOP:
            self.range = (self.range << 8) & 0xFFFFFFFF
            self._shift_low()

    def direct(self, value, nbits):
        for i in range(nbits - 1, -1, -1):
            self.range >>= 1
            if (value >> i) & 1:
                self.low += self.range
            while self.range < TOP:
                self.range = (self.range << 8) & 0xFFFFFFFF
                self._shift_low()

    def finish(self):
        for _ in range(5):
            self._shift_low()
        return bytes(self.out)


class RangeDecoder:
    def __init__(self, data, pos):
        self.data = bytes(data) + b"\0" * 16
        self.range = 0xFFFFFFFF
        self.code = int.from_bytes(self.data[pos + 1:pos + 5], "big")
        self.pos = pos + 5

    def bit(self, probs, i):
        p = probs[i]
        bound = (self.range >> 11) * p
        if self.code < bound:
            self.range = bound
            probs[i] = p + ((2048 - p) >> 5)
            b = 0
        else:
            self.code -= bound
            self.range -= bound
            probs[i] = p - (p >> 5)
            b = 1
        if self.range < TOP:
            self.range <<= 8
            self.code = ((self.code << 8) | self.data[self.pos]) & 0xFFFFFFFF
            self.pos += 1
        return b

    def direct(self, nbits):
        v = 0
        for _ in range(nbits):
            self.range >>= 1
            if self.code >= self.range:
                self.code -= self.range
                b = 1
            else:
                b = 0
            v = (v << 1) | b
            if self.range < TOP:
                self.range <<= 8
                self.code = ((self.code << 8) | self.data[self.pos]) & 0xFFFFFFFF
                self.pos += 1
        return v


# ================================================================ modèle de probabilités
class LenModel:
    def __init__(self):
        self.choice = [PROB_INIT] * 2
        self.low = [[PROB_INIT] * 8 for _ in range(16)]
        self.mid = [[PROB_INIT] * 8 for _ in range(16)]
        self.high = [PROB_INIT] * 256


class Model:
    def __init__(self, lc, lp, pb):
        self.lc, self.lp, self.pb = lc, lp, pb
        self.literal = [PROB_INIT] * (0x300 << (lc + lp))
        self.is_match = [PROB_INIT] * (12 << 4)
        self.is_rep = [PROB_INIT] * 12
        self.rep_g0 = [PROB_INIT] * 12
        self.rep_g1 = [PROB_INIT] * 12
        self.rep_g2 = [PROB_INIT] * 12
        self.rep0_long = [PROB_INIT] * (12 << 4)
        self.pos_slot = [[PROB_INIT] * 64 for _ in range(4)]
        self.pos_special = [PROB_INIT] * 115
        self.align = [PROB_INIT] * 16
        self.len = LenModel()
        self.rep_len = LenModel()


def _enc_tree(rc, probs, nbits, sym):
    m = 1
    for i in range(nbits - 1, -1, -1):
        b = (sym >> i) & 1
        rc.bit(probs, m, b)
        m = (m << 1) | b


def _enc_rtree(rc, probs, off, nbits, sym):
    m = 1
    for i in range(nbits):
        b = (sym >> i) & 1
        rc.bit(probs, off + m, b)
        m = (m << 1) | b


def _dec_tree(rc, probs, nbits):
    m = 1
    for _ in range(nbits):
        m = (m << 1) | rc.bit(probs, m)
    return m - (1 << nbits)


def _dec_rtree(rc, probs, off, nbits):
    m = 1
    sym = 0
    for i in range(nbits):
        b = rc.bit(probs, off + m)
        m = (m << 1) | b
        sym |= b << i
    return sym


def _enc_len(rc, lm, l, ps):
    l -= 2
    if l < 8:
        rc.bit(lm.choice, 0, 0)
        _enc_tree(rc, lm.low[ps], 3, l)
    elif l < 16:
        rc.bit(lm.choice, 0, 1)
        rc.bit(lm.choice, 1, 0)
        _enc_tree(rc, lm.mid[ps], 3, l - 8)
    else:
        rc.bit(lm.choice, 0, 1)
        rc.bit(lm.choice, 1, 1)
        _enc_tree(rc, lm.high, 8, l - 16)


def _dec_len(rc, lm, ps):
    if rc.bit(lm.choice, 0) == 0:
        return _dec_tree(rc, lm.low[ps], 3) + 2
    if rc.bit(lm.choice, 1) == 0:
        return _dec_tree(rc, lm.mid[ps], 3) + 10
    return _dec_tree(rc, lm.high, 8) + 18


def _enc_dist(rc, m, dist, length):
    ls = min(length - 2, 3)
    if dist < 4:
        slot = dist
    else:
        n = dist.bit_length()
        slot = ((n - 1) << 1) | ((dist >> (n - 2)) & 1)
    _enc_tree(rc, m.pos_slot[ls], 6, slot)
    if slot >= 4:
        fb = (slot >> 1) - 1
        base = (2 | (slot & 1)) << fb
        red = dist - base
        if slot < 14:
            _enc_rtree(rc, m.pos_special, base - slot - 1, fb, red)
        else:
            rc.direct(red >> 4, fb - 4)
            _enc_rtree(rc, m.align, 0, 4, red & 15)


def _dec_dist(rc, m, length):
    ls = min(length - 2, 3)
    slot = _dec_tree(rc, m.pos_slot[ls], 6)
    if slot < 4:
        return slot
    fb = (slot >> 1) - 1
    base = (2 | (slot & 1)) << fb
    if slot < 14:
        return base + _dec_rtree(rc, m.pos_special, base - slot - 1, fb)
    return base + (rc.direct(fb - 4) << 4) + _dec_rtree(rc, m.align, 0, 4)


# ================================================================ recherche de correspondances
def _match_len(data, a, b, maxlen):
    l = 0
    while l < maxlen:
        s = min(32, maxlen - l)
        if data[a + l:a + l + s] == data[b + l:b + l + s]:
            l += s
        else:
            for k in range(s):
                if data[a + l + k] != data[b + l + k]:
                    return l + k
    return l


# ================================================================ compression
def lzma_encode_raw(data: bytes, lc=3, lp=0, pb=2, dict_size=1 << 22, depth=24):
    """Flux LZMA brut (sans en-tête). Utilisé par .lzma et par 7z."""
    n = len(data)
    m = Model(lc, lp, pb)
    rc = RangeEncoder()
    state = 0
    reps = [0, 0, 0, 0]
    head = {}
    prev = [-1] * n
    pbmask = (1 << pb) - 1
    lpmask = (1 << lp) - 1
    pos = 0

    def insert(p):
        if p + 3 <= n:
            key = (data[p] << 16) | (data[p + 1] << 8) | data[p + 2]
            prev[p] = head.get(key, -1)
            head[key] = p

    while pos < n:
        ps = pos & pbmask
        maxlen = min(MAX_LEN, n - pos)

        # 1) meilleure répétition parmi rep0..rep3
        rep_len, rep_idx = 0, 0
        for i in range(4):
            d = reps[i] + 1
            if d <= pos:
                l = _match_len(data, pos - d, pos, maxlen)
                if l > rep_len:
                    rep_len, rep_idx = l, i

        # 2) meilleure correspondance par chaîne de hachage
        main_len, main_dist = 0, 0
        if maxlen >= 3:
            key = (data[pos] << 16) | (data[pos + 1] << 8) | data[pos + 2]
            cand = head.get(key, -1)
            chain = depth
            while cand >= 0 and chain > 0:
                d = pos - cand
                if d > dict_size:
                    break
                if data[cand + main_len] == data[pos + main_len] or main_len == 0:
                    l = _match_len(data, cand, pos, maxlen)
                    if l > main_len:
                        main_len, main_dist = l, d - 1
                        if l == maxlen:
                            break
                cand = prev[cand]
                chain -= 1
            if main_len == 3 and main_dist >= (1 << 14):
                main_len = 0

        if rep_len >= 2 and rep_len + 1 >= main_len:
            # ---- rep match
            rc.bit(m.is_match, (state << 4) + ps, 1)
            rc.bit(m.is_rep, state, 1)
            if rep_idx == 0:
                rc.bit(m.rep_g0, state, 0)
                rc.bit(m.rep0_long, (state << 4) + ps, 1)
            else:
                rc.bit(m.rep_g0, state, 1)
                if rep_idx == 1:
                    rc.bit(m.rep_g1, state, 0)
                else:
                    rc.bit(m.rep_g1, state, 1)
                    rc.bit(m.rep_g2, state, rep_idx - 2)
                d = reps.pop(rep_idx)
                reps.insert(0, d)
            _enc_len(rc, m.rep_len, rep_len, ps)
            state = 8 if state < 7 else 11
            step = rep_len
        elif main_len >= 3:
            # ---- match classique
            rc.bit(m.is_match, (state << 4) + ps, 1)
            rc.bit(m.is_rep, state, 0)
            _enc_len(rc, m.len, main_len, ps)
            _enc_dist(rc, m, main_dist, main_len)
            reps = [main_dist, reps[0], reps[1], reps[2]]
            state = 7 if state < 7 else 10
            step = main_len
        elif pos > reps[0] and data[pos] == data[pos - reps[0] - 1]:
            # ---- short rep (1 octet)
            rc.bit(m.is_match, (state << 4) + ps, 1)
            rc.bit(m.is_rep, state, 1)
            rc.bit(m.rep_g0, state, 0)
            rc.bit(m.rep0_long, (state << 4) + ps, 0)
            state = 9 if state < 7 else 11
            step = 1
        else:
            # ---- littéral
            rc.bit(m.is_match, (state << 4) + ps, 0)
            prev_byte = data[pos - 1] if pos else 0
            off = 0x300 * (((pos & lpmask) << lc) + (prev_byte >> (8 - lc)))
            sym = data[pos]
            s = 1
            if state < 7:
                for i in range(7, -1, -1):
                    b = (sym >> i) & 1
                    rc.bit(m.literal, off + s, b)
                    s = (s << 1) | b
            else:
                mb = data[pos - reps[0] - 1]
                offs = 0x100
                for i in range(7, -1, -1):
                    mb <<= 1
                    mbit = mb & offs
                    b = (sym >> i) & 1
                    rc.bit(m.literal, off + offs + mbit + s, b)
                    s = (s << 1) | b
                    offs &= ~mbit if b == 0 else mbit
            state = 0 if state < 4 else (state - 3 if state < 10 else state - 6)
            step = 1

        for p in range(pos, pos + step):
            insert(p)
        pos += step

    return rc.finish()


def lzma_decode_raw(blob, offset, size, lc=3, lp=0, pb=2):
    m = Model(lc, lp, pb)
    rc = RangeDecoder(blob, offset)
    out = bytearray()
    state = 0
    reps = [0, 0, 0, 0]
    pbmask = (1 << pb) - 1
    lpmask = (1 << lp) - 1
    unknown = size is None or size < 0
    while unknown or len(out) < size:
        pos = len(out)
        ps = pos & pbmask
        if rc.bit(m.is_match, (state << 4) + ps) == 0:
            prev_byte = out[-1] if out else 0
            off = 0x300 * (((pos & lpmask) << lc) + (prev_byte >> (8 - lc)))
            s = 1
            if state < 7:
                while s < 0x100:
                    s = (s << 1) | rc.bit(m.literal, off + s)
            else:
                mb = out[pos - reps[0] - 1]
                offs = 0x100
                while s < 0x100:
                    mb <<= 1
                    mbit = mb & offs
                    b = rc.bit(m.literal, off + offs + mbit + s)
                    s = (s << 1) | b
                    offs &= ~mbit if b == 0 else mbit
            out.append(s & 0xFF)
            state = 0 if state < 4 else (state - 3 if state < 10 else state - 6)
            continue
        if rc.bit(m.is_rep, state) == 0:
            length = _dec_len(rc, m.len, ps)
            dist = _dec_dist(rc, m, length)
            if dist == 0xFFFFFFFF:
                break  # marqueur de fin
            reps = [dist, reps[0], reps[1], reps[2]]
            state = 7 if state < 7 else 10
        else:
            if not out:
                raise ValueError("flux LZMA corrompu")
            if rc.bit(m.rep_g0, state) == 0:
                if rc.bit(m.rep0_long, (state << 4) + ps) == 0:
                    out.append(out[-reps[0] - 1])
                    state = 9 if state < 7 else 11
                    continue
            else:
                if rc.bit(m.rep_g1, state) == 0:
                    d = reps.pop(1)
                elif rc.bit(m.rep_g2, state) == 0:
                    d = reps.pop(2)
                else:
                    d = reps.pop(3)
                reps.insert(0, d)
            length = _dec_len(rc, m.rep_len, ps)
            state = 8 if state < 7 else 11
        dist = reps[0] + 1
        if dist > len(out):
            raise ValueError("distance invalide : flux corrompu")
        if not unknown:
            length = min(length, size - len(out))
        start = len(out) - dist
        if length <= dist:
            out += out[start:start + length]
        else:
            for _ in range(length):
                out.append(out[-dist])
    return bytes(out)


# ================================================================ conteneur .lzma
def props_byte(lc=3, lp=0, pb=2):
    return (pb * 5 + lp) * 9 + lc


def lzma_compress(data: bytes, dict_size: int = 1 << 22, depth: int = 24) -> bytes:
    body = lzma_encode_raw(data, 3, 0, 2, dict_size, depth)
    header = bytes([props_byte()]) + struct.pack("<IQ", dict_size, len(data))
    return header + body


def lzma_decompress(blob: bytes) -> bytes:
    if len(blob) < 13:
        raise ValueError("fichier .lzma trop court")
    props = blob[0]
    if props >= 225:
        raise ValueError("octet de propriétés invalide")
    pb, rest = divmod(props, 45)
    lp, lc = divmod(rest, 9)
    size = struct.unpack("<Q", blob[5:13])[0]
    if size == 0xFFFFFFFFFFFFFFFF:
        size = None
    return lzma_decode_raw(blob, 13, size, lc, lp, pb)
