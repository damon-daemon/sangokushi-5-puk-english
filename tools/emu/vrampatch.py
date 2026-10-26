"""vrampatch.py IN.sav OUT.sav [FILE ...]: copy a DuckStation state with the build's GRAPH
files written into its VRAM (tools/gfx.py FILES map: raw VRAM rects, loaded once at boot),
so redrawn graphics can be checked from a clean-disc state without booting the build.
FILE = GRAPH names (default: every file in gfx.FILES), read from the built track 1. Name
only the files resident on the state's screen: SMENU's rect is reused by the map in-game
(writing it there paints menu pieces over the map). Combine with inject.py for the program side.
"""
import os, struct, sys
from compression import zstd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import gfx, iso, vram

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BUILT = os.path.join(ROOT, 'build', 'sangokushi_5_puk_en_track1.bin')


def main():
    a = sys.argv[1:]
    src, out, names = a[0], a[1], a[2:] or list(gfx.FILES)
    d = bytearray(open(src, 'rb').read())
    h = list(struct.unpack_from('<12I', d, 0xa8))
    off, size = h[11], h[9]
    assert off + size == len(d), 'state data is not last'
    u = bytearray(zstd.decompress(d[off:off + size]))
    base = u.index(vram.TAG) + len(vram.TAG)
    disc = iso.Disc(BUILT)
    recs = disc.records()
    for n in names:
        data = disc.file('GRAPH/%s.BIN' % n, recs)
        for o, x, y, w, hh in gfx.FILES[n]:
            y %= 1000           # virtual y of an alternate file for the same spot (gfx.FILES)
            for r in range(hh):
                p = base + ((y + r) * 1024 + x) * 2
                u[p:p + w * 2] = data[o + r * w * 2:o + (r + 1) * w * 2]
    z = zstd.compress(bytes(u))
    h[9] = len(z)
    struct.pack_into('<12I', d, 0xa8, *h)
    open(out, 'wb').write(bytes(d[:off]) + z)
    print(out, ', '.join(names))


if __name__ == '__main__':
    main()
