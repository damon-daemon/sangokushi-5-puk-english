"""VRAM from a DuckStation save state (zstd data, 'GPU-VRAM' tag + 1 MB, 1024x512 u16).

  vram.py STATE.sav OUT.png                       whole VRAM as 15-bit colour
  vram.py STATE.sav OUT.png X Y W H BPP CX CY [S] a texture: rect in VRAM halfwords (X, W) and
                                                  rows, BPP 4/8, CLUT at (CX, CY), scale S
  vram.py STATE.sav find FILE                     where FILE's 2 KB pieces sit in VRAM
"""
import struct, sys
from compression import zstd

TAG = b'GPU-VRAM'


def load(path):
    d = open(path, 'rb').read()
    assert d[:5] == b'DUCCW'
    # header u32s from 0xa8: media path len/off, subimage, shot type, w, h, shot off? ...
    h = struct.unpack_from('<12I', d, 0xa8)
    off, size = h[11], h[9]
    u = zstd.decompress(d[off:off + size])
    i = u.index(TAG) + len(TAG)
    return u[i:i + 0x100000]


def px(v):
    return ((v & 31) << 3, ((v >> 5) & 31) << 3, ((v >> 10) & 31) << 3)


def u16(vr, x, y):
    return vr[(y * 1024 + x) * 2] | vr[(y * 1024 + x) * 2 + 1] << 8


def whole(vr):
    from PIL import Image
    im = Image.new('RGB', (1024, 512))
    im.putdata([px(v) for v in struct.unpack('<524288H', vr)])
    return im


def texture(vr, x, y, w, h, bpp, cx, cy):
    from PIL import Image
    per = 16 // bpp
    pal = [px(u16(vr, cx + i, cy)) for i in range(1 << bpp)]
    im = Image.new('RGB', (w * per, h))
    out = []
    for r in range(h):
        for c in range(w):
            v = u16(vr, x + c, y + r)
            for k in range(per):
                out.append(pal[(v >> (k * bpp)) & ((1 << bpp) - 1)])
    im.putdata(out)
    return im


def find(vr, data):
    """VRAM rows are 2048 B: look up each file row-piece of 64 B at any position."""
    rows = [vr[i * 2048:(i + 1) * 2048] for i in range(512)]
    hits = []
    for o in range(0, len(data) - 64, 2048):
        piece = data[o:o + 64]
        if piece.count(piece[:2]) * 2 >= 60:
            continue
        for y, r in enumerate(rows):
            x = r.find(piece)
            if x >= 0 and x % 2 == 0:
                hits.append((o, x // 2, y))
                break
    return hits


def main():
    a = sys.argv[1:]
    vr = load(a[0])
    if a[1] == 'find':
        data = open(a[2], 'rb').read()
        for o, x, y in find(vr, data):
            print('file %#08x -> vram x=%d y=%d' % (o, x, y))
        return
    if len(a) == 2:
        whole(vr).save(a[1])
        return
    x, y, w, h, bpp, cx, cy = map(int, a[2:9])
    s = int(a[9]) if len(a) > 9 else 1
    im = texture(vr, x, y, w, h, bpp, cx, cy)
    if s > 1:
        im = im.resize((im.width * s, im.height * s), 0)
    im.save(a[1])


if __name__ == '__main__':
    main()
