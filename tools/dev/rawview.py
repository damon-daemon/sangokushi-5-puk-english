"""rawview.py FILE OUT.png OFF BPP WIDTH_PX ROWS [CLUT_OFF] [SCALE]: view raw pixel data from a file
(4/8bpp with a CLUT read from the file, 16bpp direct, CLUT_OFF -1 = grey ramp)."""
import struct, sys
from PIL import Image


def px(v):
    return ((v & 31) << 3, ((v >> 5) & 31) << 3, ((v >> 10) & 31) << 3)


def main():
    a = sys.argv[1:]
    d = open(a[0], 'rb').read()
    off, bpp, w, rows = (int(x, 0) for x in a[2:6])
    co = int(a[6], 0) if len(a) > 6 else -1
    s = int(a[7]) if len(a) > 7 else 1
    n = 1 << bpp if bpp < 16 else 0
    pal = ([px(v) for v in struct.unpack_from('<%dH' % n, d, co)] if co >= 0 else
           [(i * 255 // max(n - 1, 1),) * 3 for i in range(n)])
    out = []
    stride = w * bpp // 8
    for r in range(rows):
        row = d[off + r * stride: off + (r + 1) * stride].ljust(stride, b'\0')
        if bpp == 16:
            out += [px(v) for v in struct.unpack('<%dH' % w, row)]
        elif bpp == 8:
            out += [pal[b] for b in row]
        else:
            for b in row:
                out += [pal[b & 15], pal[b >> 4]]
    im = Image.new('RGB', (w, rows)); im.putdata(out)
    if s > 1:
        im = im.resize((w * s, rows * s), 0)
    im.save(a[1])


if __name__ == '__main__':
    main()
