"""sprites.py STATE.sav [min_w]: textured sprite packets (GP0 0x64-0x67 / 0x7C-0x7F) found in the state's
RAM, after the last draw-mode packet (0xE1 texpage) before each: screen x,y, texpage (VRAM x, depth), UV,
size, CLUT (x, y). Text cells (CLUT 0,481) are left out. Use to learn which VRAM rect + CLUT a baked label uses.
"""
import collections, struct, sys
from compression import zstd

ROOT_EXE = 'extract/SLPS_014.66'


def ram(path):
    d = open(path, 'rb').read()
    h = struct.unpack_from('<12I', d, 0xa8)
    u = zstd.decompress(d[h[11]:h[11] + h[9]])
    exe = open(ROOT_EXE, 'rb').read()
    r = u.find(exe[0x20800:0x20840])
    return u[r - 0x20000:r - 0x20000 + 0x200000]


def sprites(m, min_w=1):
    tp = None
    out = collections.Counter()
    for a in range(0, len(m) - 16, 4):
        w = struct.unpack_from('<4I', m, a)
        op = w[0] >> 24
        if op == 0xE1:
            tp = w[0] & 0x1ff
        elif op in (0x64, 0x65, 0x66, 0x67) and tp is not None:
            x, y = w[1] & 0xffff, w[1] >> 16
            u, v, clut = w[2] & 0xff, (w[2] >> 8) & 0xff, w[2] >> 16
            sw, sh = w[3] & 0xffff, w[3] >> 16
            cx, cy = (clut & 0x3f) * 16, clut >> 6
            if not (x < 1024 and y < 512 and 0 < sw <= 256 and 0 < sh <= 256 and cy < 512) or (cx, cy) == (0, 481):
                continue
            if sw < min_w:
                continue
            depth = (4, 8, 16, 16)[(tp >> 7) & 3]
            out[(x, y, (tp & 15) * 64, (tp >> 4 & 1) * 256, depth, u, v, sw, sh, cx, cy)] += 1
    return out


def main():
    m = ram(sys.argv[1])
    mw = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    print('scr_x scr_y  page_x page_y bpp   u   v   w   h  clut')
    for k in sorted(sprites(m, mw), key=lambda k: (k[1], k[0])):
        print('%5d %5d  %6d %6d %3d %3d %3d %3d %3d  %d,%d' % k)


if __name__ == '__main__':
    main()
