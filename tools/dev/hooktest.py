"""Run the namedict MIPS (expand, names_fmt through the game's own formatter, print_string) in
unicorn and compare with the Python model in tools/namedict.py. Dev only (unicorn in .venv).

unicorn does not model the R3000 load delay slot: asm.check_delay covers that.

usage: .venv/bin/python tools/dev/hooktest.py [n random strings]
"""
import os, random, struct, sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import unicorn
from unicorn import mips_const as M
import iso, kanji, names, namedict, setup, vwf

GP = 0x80142054
PUTC = 0x80019380
STUB = 0x80001000          # stands for the formatter callback (wprintf's putc)
RET = 0x80001100           # return address: stops the run
STACK = 0x801ff000
DATA = 0x801e0000          # test strings / args
import pixfont
WIDTHS = {ord(c): w for c, (g, w) in pixfont.glyphs().items()}


class Cpu:
    def __init__(self, exe):
        self.u = unicorn.Uc(unicorn.UC_ARCH_MIPS, unicorn.UC_MODE_MIPS32 | unicorn.UC_MODE_LITTLE_ENDIAN)
        self.u.mem_map(0, 0x200000)
        self.u.mem_write(0x10000, bytes(exe[0x800:]))
        self.drawn = bytearray()
        for a in (STUB, PUTC):
            self.u.hook_add(unicorn.UC_HOOK_CODE, self._stub, begin=a, end=a)
        self.u.hook_add(unicorn.UC_HOOK_CODE, lambda u, a, s, d: u.emu_stop(), begin=RET, end=RET)

    def _stub(self, u, addr, size, data):
        r = M.UC_MIPS_REG_A0 if addr == STUB else M.UC_MIPS_REG_A1
        self.drawn.append(u.reg_read(r) & 0xFF)
        u.reg_write(M.UC_MIPS_REG_PC, u.reg_read(M.UC_MIPS_REG_RA))

    def call(self, fn, *a):
        u = self.u
        for r, v in zip((M.UC_MIPS_REG_A0, M.UC_MIPS_REG_A1, M.UC_MIPS_REG_A2, M.UC_MIPS_REG_A3), a):
            u.reg_write(r, v)
        u.reg_write(M.UC_MIPS_REG_SP, STACK)
        u.reg_write(M.UC_MIPS_REG_GP, GP)
        u.reg_write(M.UC_MIPS_REG_RA, RET)
        self.drawn = bytearray()
        u.emu_start(fn, RET, count=5_000_000)
        return u.reg_read(M.UC_MIPS_REG_V0), u.reg_read(M.UC_MIPS_REG_V1)

    def put(self, a, b):
        self.u.mem_write(a & 0x1FFFFFFF, b)

    def cstr(self, a, n=2048):
        b = bytes(self.u.mem_read(a & 0x1FFFFFFF, n))
        return b[:b.index(0)]


def setup_exe():
    disc = iso.Disc(setup.CLEAN)
    recs = disc.records()
    pairs = names.pairs(disc, recs)
    exe = bytearray(disc.file(iso.EXE_NAME, recs))
    heap = kanji.apply(exe)
    hooks = vwf.apply(exe, heap)
    d, lab = namedict.apply(exe, heap, pairs, hooks)
    lab.update(vwf_wrap=hooks['wrap'], vwf_state=hooks['state'])
    return exe, d, lab, pairs


def clip_model(text, x, edge):
    """wrap_fmt's one-line-rect clip: (drawn bytes, squeeze d, gaps N); ASCII only"""
    w = [WIDTHS[c] for c in text]
    core = text.rstrip(b' ')
    d, n = x + sum(w[:len(core)]) - edge, len(core) - 1
    if d <= 0:
        return text, 0, 0
    if 0 < n and d <= n:
        return text, d, n
    m = 0
    for c in w:
        if x + c - 1 > edge:
            break
        x += c - 1; m += 1
    return text[:m], max(m - 1, 0), max(m - 1, 0)


def clip_tests(cpu, lab):
    st = lab['vwf_state']
    # (text, start col, rect width, Japanese bytes); rect at col 16, window 38 cells
    cases = [(b'Luoyang', 16, 5, 4), (b'Punish Rebels', 18, 8, 4), (b'Punish Rebels', 20, 8, 4), (b'Gen', 24, 8, 4),
             (b'Zhuge Liang', 10, 9, 4), (b'Show Might', 22, 8, 4), (b'X', 18, 1, 2), (b'Liu Bei', 10, 9, 4),
             (b'Punish Rebels', 17, 5, 8), (b'Gen  ', 17, 5, 5), (b'Luoyang  ', 16, 5, 5)]
    bad = 0
    for text, col, w, jb in cases:
        x0 = 16
        cpu.put(GP + 4, struct.pack('<6h', col, 5, 0, 0, 38, 20))
        cpu.put(st, struct.pack('<12h', -1, -1, 0, 0, 0, 0, 0, x0, 5, w, 2, jb))
        cpu.put(DATA, text + b'\0')
        cpu.call(lab['vwf_wrap'], STUB, DATA, 0)
        got = (bytes(cpu.drawn),) + struct.unpack('<hhh', bytes(cpu.u.mem_read((st + 6) & 0x1FFFFFFF, 6)))[0:3:2]
        got = (got[0], got[1], struct.unpack('<h', bytes(cpu.u.mem_read((st + 12) & 0x1FFFFFFF, 2)))[0])
        if not x0 <= col <= x0 + w + 1:
            want = (text, 0, 0)
        else:
            want = clip_model(text, col * 8, min(max((x0 + w) * 8, col * 8 + jb * 8), 37 * 8))
        ok = got == want
        bad += not ok
        print('clip %-16r col %d w %d jp %d: %r%s' % (text, col, w, jb, got, '' if ok else '  != %r' % (want,)))
    print('clip: %d / %d match' % (len(cases) - bad, len(cases)))


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 300
    exe, d, lab, pairs = setup_exe()
    cpu = Cpu(exe)
    clip_tests(cpu, lab)
    keys = list(pairs)
    rnd = random.Random(5)
    junk = [b'\x88\xa0', b'\x82\xa0', b'\xb1', b' ', b'abc', b'12', b'\x1bC\x83', b'\x1aW', b'\x1bB\x02',
            b'\n', b'%', b'\xeb\xa0', b'   ', b'  12', b' 7']
    bad = tabs = 0
    out = 0x801f0000
    for t in range(n):
        s = b''.join(rnd.choice(keys) if rnd.random() < .5 else rnd.choice(junk) for _ in range(rnd.randint(0, 12)))
        cap = rnd.choice([namedict.CAP, 16, 40])
        base = rnd.choice([0, 16 << 16 | 40, 8 << 16 | 300])
        cpu.put(DATA, s + b'\0')
        v0, v1 = cpu.call(lab['expand'], DATA, out, cap, base)
        got, want = cpu.cstr(out), d.expand(s, cap, base)
        tabs += namedict.TAB in want
        if got != want or v0 != out + len(got):
            bad += 1
            if bad < 6:
                print('expand %r cap %d\n  mips  %r\n  model %r' % (s, cap, got, want))
    print('expand: %d / %d match (%d with column tabs)' % (n - bad, n, tabs))
    # right-aligned names (%8s): spaces then a name, at line start and after a name
    two = [k for k in keys if len(k) == 4][:3]
    for s in (b'    ' + two[0], two[1] + b'  ' + b'    ' + two[2], b'  ' + two[0] + b'  12', b'\x1bC\x02    ' + two[0]):
        cpu.put(DATA, s + b'\0')
        cpu.call(lab['expand'], DATA, out, namedict.CAP, 30 << 16 | 240)
        got, want = cpu.cstr(out), d.expand(s, namedict.CAP, 30 << 16 | 240)
        print('right-aligned %r -> %r %s' % (s, want, 'OK' if got == want else '!= mips %r' % got))
    # names_fmt through the real formatter: "%s attacked %s at %d%%!" with two names
    a, b = keys[0], keys[5]
    cpu.put(DATA, b'%s attacked %s (%d%%)!\0')
    cpu.put(DATA + 0x100, a + b'\0')
    cpu.put(DATA + 0x140, b + b'\0')
    cpu.put(DATA + 0x200, struct.pack('<3I', DATA + 0x100, DATA + 0x140, 42))
    cpu.put(GP + 4, struct.pack('<6h', 0, 0, 0, 0, 200, 10))       # x, y, left, top, width, height
    cpu.call(lab['names_fmt'], STUB, DATA, DATA + 0x200)
    want = b'%s attacked %s (42%%)!' % (pairs[a], pairs[b])
    want = want.replace(b'%%', b'%')
    print('names_fmt: %r %s' % (bytes(cpu.drawn), 'OK' if bytes(cpu.drawn) == want else '!= %r' % want))
    # column: "%-8s  War%4u" at col 3 of a window at col 2 -> tab to 3 * 8 + 10 * 8 px
    two = next(k for k in keys if len(k) == 4)
    cpu.put(DATA, b'%-8s  War%4u\0')
    cpu.put(DATA + 0x100, two + b'\0')
    cpu.put(DATA + 0x200, struct.pack('<2I', DATA + 0x100, 85))
    cpu.put(GP + 4, struct.pack('<6h', 3, 0, 2, 0, 30, 10))
    cpu.call(lab['names_fmt'], STUB, DATA, DATA + 0x200)
    px = 3 * 8 + 10 * 8
    px2 = 3 * 8 + 17 * 8 - 2 * namedict.DIGIT_ADV                    # "  85" right-aligned on byte 17
    want = (pairs[two] + bytes([namedict.TAB, namedict.TAB_HI + (px >> 5), namedict.TAB_LO + (px & 31)]) + b'War'
            + bytes([namedict.TAB, namedict.TAB_HI + (px2 >> 5), namedict.TAB_LO + (px2 & 31)]) + b'85')
    print('names_fmt column: %r %s' % (bytes(cpu.drawn), 'OK' if bytes(cpu.drawn) == want else '!= %r' % want))
    # print_string(ctx, str, limit): limit grows with the names
    for limit in (4, 100):
        s = a + b' & ' + b
        cpu.put(DATA, s + b'\0')
        cpu.call(0x80019658, 0x8014409f, DATA, limit)
        want = d.expand(s)[:limit + len(d.expand(s)) - len(s)]
        print('print_string limit %d: %r %s' % (limit, bytes(cpu.drawn), 'OK' if bytes(cpu.drawn) == want else '!= %r' % want))


if __name__ == '__main__':
    main()
