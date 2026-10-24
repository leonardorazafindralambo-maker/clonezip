"""Génère 4 fichiers d'exemple : texte, audio (WAV), image (BMP), vidéo (Y4M brut + MP4)."""
import math
import os
import random
import struct
import subprocess
import sys
import wave

out = sys.argv[1] if len(sys.argv) > 1 else "samples"
os.makedirs(out, exist_ok=True)
random.seed(42)

# ---- TEXTE : texte français pseudo-naturel (~200 Ko)
mots = ("le la les un une des de du et ou mais donc car dans sur sous avec sans pour par compression "
        "algorithme signal codage fichier donnée information entropie huffman transformée bloc octet "
        "fréquence symbole probabilité dictionnaire répétition distance longueur arbre table lecture "
        "écriture mémoire temps taux gain qualité perte analyse méthode résultat exemple système").split()
phrases = []
while sum(map(len, phrases)) < 200_000:
    n = random.randint(6, 16)
    p = " ".join(random.choice(mots) for _ in range(n))
    phrases.append(p.capitalize() + ".\n")
open(f"{out}/texte.txt", "w", encoding="utf-8").write("".join(phrases))

# ---- AUDIO : WAV 16 bits mono 22,05 kHz, 6 s (notes + léger bruit)
sr, dur = 22050, 6
with wave.open(f"{out}/audio.wav", "wb") as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
    frames = bytearray()
    notes = [261.6, 329.6, 392.0, 523.3]
    for i in range(sr * dur):
        f = notes[(i // (sr // 2)) % 4]
        v = 0.5 * math.sin(2 * math.pi * f * i / sr) + 0.2 * math.sin(2 * math.pi * 2 * f * i / sr)
        v += random.uniform(-0.01, 0.01)
        frames += struct.pack("<h", int(v * 20000))
    w.writeframes(bytes(frames))

# ---- IMAGE : BMP 24 bits 320x240 (dégradés + formes)
W, H = 320, 240
rows = []
for y in range(H):
    row = bytearray()
    for x in range(W):
        r, g, b = x * 255 // W, y * 255 // H, 128
        if (x - 160) ** 2 + (y - 120) ** 2 < 70 ** 2:
            r, g, b = 240, 200, 40
        if 40 < x < 110 and 150 < y < 210:
            r, g, b = 30, 60, 200
        row += bytes((b, g, r))
    rows.append(bytes(row))
size = 54 + W * 3 * H
hdr = b"BM" + struct.pack("<IHHI", size, 0, 0, 54) + struct.pack("<IiiHHIIiiII", 40, W, H, 1, 24, 0, W * 3 * H, 2835, 2835, 0, 0)
open(f"{out}/image.bmp", "wb").write(hdr + b"".join(reversed(rows)))

# ---- VIDÉO : Y4M brut (YUV 4:2:0) 160x120, 40 images, carré en mouvement
vw, vh, nf = 160, 120, 40
with open(f"{out}/video.y4m", "wb") as f:
    f.write(f"YUV4MPEG2 W{vw} H{vh} F20:1 Ip A1:1 C420jpeg\n".encode())
    for k in range(nf):
        y = bytearray(vw * vh)
        for j in range(vh):
            for i in range(vw):
                y[j * vw + i] = 60 + (i * 100 // vw)
        x0 = 5 + k * 3
        for j in range(40, 80):
            for i in range(x0, min(x0 + 30, vw)):
                y[j * vw + i] = 230
        f.write(b"FRAME\n" + bytes(y) + bytes([128]) * (vw * vh // 4) + bytes([128 + k % 8]) * (vw * vh // 4))

# ---- VIDÉO : MP4 (H.264) déjà compressé, si ffmpeg est dispo (cas réel)
try:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=duration=4:size=320x240:rate=15",
                    "-pix_fmt", "yuv420p", f"{out}/video.mp4"], check=True)
except Exception:
    pass
print({n: os.path.getsize(f"{out}/{n}") for n in sorted(os.listdir(out))})
