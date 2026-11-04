"""Run vwf.blit4 (line-cache blit, no VRAM readback) in unicorn against a Python model of the
old readback blit on a VRAM image: random text runs (shadow pass opaque one row lower, then
the main pass transparent, proportional advances, squeezes, gaps, consecutive lines, cleared
cells + cache drop). Dev only (unicorn in .venv).

usage: .venv/bin/python tools/dev/blittest.py [runs]
"""
import os, random, struct, sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import unicorn
from unicorn import mips_const as M
import iso, kanji, setup, vwf

RET, STACK, SRC = 0x80001100, 0x801ff000, 0x801e0000
VW = 2048                                   # VRAM bytes per row


class Cpu:
    def __init__(self, exe):
        self.u = unicorn.Uc(unicorn.UC_ARCH_MIPS, unicorn.UC_MODE_MIPS32 | unicorn.UC_MODE_LITTLE_ENDIAN)
        self.u.mem_map(0, 0x200000)
        self.u.mem_write(0x10000, bytes(exe[0x800:]))
        self.vram = bytearray(VW * 512)
        self.loads = 0
        self.u.hook_add(unicorn.UC_HOOK_CODE, self._load, begin=vwf.LOADIMAGE, end=vwf.LOADIMAGE)
        self.u.hook_add(unicorn.UC_HOOK_CODE, self._ret, begin=vwf.DRAWSYNC, end=vwf.DRAWSYNC)
        self.u.hook_add(unicorn.UC_HOOK_CODE, self._ret, begin=vwf.STOREIMAGE, end=vwf.STOREIMAGE)
        self.u.hook_add(unicorn.UC_HOOK_CODE, lambda u, a, s, d: u.emu_stop(), begin=RET, end=RET)

    def _ret(self, u, addr, size, data):
        if addr == vwf.STOREIMAGE:
            raise AssertionError('StoreImage called')
        u.reg_write(M.UC_MIPS_REG_V0, 0)
        u.reg_write(M.UC_MIPS_REG_PC, u.reg_read(M.UC_MIPS_REG_RA))

    def _load(self, u, addr, size, data):
        x, y, w, h = struct.unpack('<4h', bytes(u.mem_read(u.reg_read(M.UC_MIPS_REG_A0) & 0x1FFFFFFF, 8)))
        src = u.reg_read(M.UC_MIPS_REG_A1) & 0x1FFFFFFF
        for r in range(h):
            self.vram[(y + r) * VW + x * 2:(y + r) * VW + (x + w) * 2] = bytes(u.mem_read(src + r * w * 2, w * 2))
        self.loads += 1
        u.reg_write(M.UC_MIPS_REG_PC, u.reg_read(M.UC_MIPS_REG_RA))

    def blit(self, fn, px, py, w, h, src, trans):
        u = self.u
        u.mem_write(SRC & 0x1FFFFFFF, src)
        u.mem_write((STACK + 0x10) & 0x1FFFFFFF, struct.pack('<I', SRC))
        for r, v in zip((M.UC_MIPS_REG_A0, M.UC_MIPS_REG_A1, M.UC_MIPS_REG_A2, M.UC_MIPS_REG_A3), (px, py, w, h)):
            u.reg_write(r, v)
        u.reg_write(M.UC_MIPS_REG_SP, STACK)
        u.reg_write(M.UC_MIPS_REG_RA, RET)
        u.emu_start(fn + (0 if trans else 8), RET, count=1_000_000)


def ref_blit(vram, px, py, w, h, src, trans):
    """The old blit: read the covering halfwords, merge (opaque = w columns, transparent = ink), write."""
    for r in range(h):
        for i in range(w):
            k = r * w + i
            n = (src[k >> 1] >> ((k & 1) * 4)) & 0xF
            if n or not trans:
                x = 0x380 * 4 + px + i
                b = (py + r) * VW + (x >> 1)
                sh = (x & 1) * 4
                vram[b] = (vram[b] & ~(0xF << sh)) | (n << sh)


def glyph(w, rows, full):
    """4bpp glyph, pitch w: random ink in rows 3..14 (pixfont) or all rows (full)."""
    px = []
    for r in range(16):
        for i in range(w):
            ink = (full or 3 <= r <= 14) and random.random() < 0.5
            px.append(random.randint(1, 15) if ink else 0)
    out = bytearray((len(px) + 1) // 2)
    for k, n in enumerate(px):
        out[k >> 1] |= n << ((k & 1) * 4)
    return bytes(out)


def clear(cpu, ref, x, y, w, h):
    """ClearImage of a cell rect (px) in both images + rect_hook's cache drop."""
    for r in range(h):
        b = (y + r) * VW + (0x380 * 4 + x) // 2
        cpu.vram[b:b + w // 2] = bytes(w // 2)
        ref[b:b + w // 2] = bytes(w // 2)
    cpu.u.mem_write((cpu.state + vwf.S_CV) & 0x1FFFFFFF, b'\0\0')


def main():
    runs = int(sys.argv[1]) if len(sys.argv) > 1 else 300
    disc = iso.Disc(setup.CLEAN)
    recs = disc.records()
    exe = bytearray(disc.file(iso.EXE_NAME, recs))
    heap = kanji.apply(exe)
    hooks = vwf.apply(exe, heap)
    cpu = Cpu(exe)
    cpu.state = hooks['state']
    blit = hooks['blit']
    ref = bytearray(VW * 512)
    random.seed(1)
    bad = full_bad = 0
    y = 0
    for run in range(runs):
        full = run % 20 == 19
        # a new run: the cursor moved (vwf_cell drops the cache); every 3rd run on the next line
        if run % 3 == 0 or run == 0:
            y = random.randrange(0, 29) * 16
        else:
            y += 16
        x = x0 = random.randrange(0, 30) * 8
        if os.environ.get('CLEANLINE'):                 # a window clear before the run
            clear(cpu, ref, 0, y, 320, 17)
        cpu.u.mem_write((cpu.state + vwf.S_CV) & 0x1FFFFFFF, b'\0\0')
        if random.random() < 0.3:                       # list redraw: the cell was cleared first
            clear(cpu, ref, x, y, 8 * random.randint(1, 8), 16)
        n = random.randint(1, 12)
        for g in range(n):
            w = 16 if random.random() < 0.1 else random.randint(3, 8)
            if x + w > 320:
                break
            main_g = glyph(w, 16, full)
            shadow = glyph(w, 16, full)
            cpu.blit(blit, x, y + 1, w, 16, shadow, False)
            ref_blit(ref, x, y + 1, w, 16, shadow, False)
            cpu.blit(blit, x, y, w, 16, main_g, True)
            ref_blit(ref, x, y, w, 16, main_g, True)
            x += w + random.choice((0, 0, 0, 1, -1, 2))
            x = max(x, 0)
        if cpu.vram != ref:
            where = [(i // VW - y, (i % VW) * 2 - 0x380 * 4) for i in range(len(ref)) if cpu.vram[i] != ref[i]]
            diff = len(where)
            print('   run x0 %d..%d, diffs (row-y, px): %s' % (x0, x, where[:12]))
            if full:
                full_bad += 1
            else:
                bad += 1
                print('run %d (y %d): %d bytes differ' % (run, y, diff))
            ref[:] = cpu.vram                           # go on from the same image
    print('blit: %d / %d runs match (%d LoadImage calls); full-height glyph runs differing: %d (expected: row 16 shadow under the next line)'
          % (runs - bad - full_bad, runs, cpu.loads, full_bad))
    return bad == 0


if __name__ == '__main__':
    sys.exit(0 if main() else 1)
