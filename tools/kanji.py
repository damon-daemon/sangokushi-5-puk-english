"""Shrink the program's 16x16 kanji font and free its RAM (notes/ram.md §3, notes/engine.md "Font").

The program carries 2378 full-width glyphs (1bpp 16x16, 32 B each, at GLYPHS) with a
sorted u16 Shift-JIS code list (CODES), 81 KB in all, and no BIOS-font path. The
English build keeps only
  - the glyphs the BIOS kanji ROM does not have (it holds only 0x8140-0x84BE and the
    level-1 kanji 0x889F-0x9872): NEC specials 0x87xx, level-2 kanji and the gaiji
    0xEB9F- (rare name kanji), copied from the original set (untranslated text);
  - full-width digits, Latin letters and ．’－ (drawn from tools/pixfont.py, centred in the
    16-px cell) and the full-width space (index 0 = the "not found" glyph): the officer
    name entry (0x8002927c / 0x800292c8) lists the code list's entries in a code range
    as its character grid;
and the glyph lookup in expand_kanji (0x80018c1c) falls back to the BIOS ROM font
(Krom2RawAdd, B0:51, 16x15) for every other kanji/kana (untranslated Japanese). The
rest of the area is returned as free RAM for the English code and data (Heap).
"""
import os, struct, sys

sys.path.insert(0, os.path.dirname(__file__))
import asm, pixfont

EXE_BASE = 0x80010000 - 0x800
CODES, GLYPHS, OLD_N = 0x800f55a0, 0x800f6834, 0x94a
AREA_END = GLYPHS + 32 * OLD_N              # 0x80109174 (small kana font follows)
SEARCH = 0x80018ba8                          # binary search(code) -> index
SEARCH_N, SEARCH_HI, SEARCH_LO = 0x80018bac, 0x80018bb0, 0x80018bb4
SEARCH_MISS = 0x80018c10                     # loop delay slot: move a1, zero (= miss result)
EXPAND_LOOKUP, EXPAND_LOOKUP_END = 0x80018c4c, 0x80018c68   # jal search .. glyph address in t0
# name entry: linear scan (bound OLD_N) and page list (bound OLD_N - 2)
NAME_HI, NAME_LO, NAME_N = 0x8002927c, 0x80029280, 0x80029298
PAGE_HI, PAGE_LO, PAGE_N = 0x80029300, 0x80029304, 0x80029330

SYMBOLS = {0x8144: '.', 0x8166: "'", 0x817C: '-'}       # name entry Sym grid (nameentry.py)
LATIN = ([0x8140] + list(SYMBOLS) + list(range(0x824F, 0x8259)) + list(range(0x8260, 0x827A))
         + list(range(0x8281, 0x829B)))


def keep_original(code):
    return not (0x8140 <= code <= 0x84BE or 0x889F <= code <= 0x9872)


def latin_glyph(code):
    if code == 0x8140:
        return bytes(32)
    ch = SYMBOLS.get(code) or chr(code - 0x824F + ord('0')) if code < 0x8260 else chr(code - 0x8260 + ord('A')) if code < 0x8281 else chr(code - 0x8281 + ord('a'))
    rows, adv = pixfont.glyphs()[ch]
    sh = max(0, (16 - (adv - pixfont.SPACING)) // 2)
    out = bytearray()
    for r in rows:
        v = r << (8 - sh)
        out += bytes([v >> 8 & 0xFF, v & 0xFF])
    return bytes(out)


def glyph_ptr(at, glyphs, tmp):
    """glyph_ptr(a0 = code) -> v0 = address of a 32-byte 1bpp 16x16 glyph: the kept set,
    else the BIOS ROM glyph (16x15) copied into tmp with a blank last row, else glyph 0."""
    hi, lo = asm.hi, asm.lo
    return asm.assemble(at, [
        ('addiu', 'sp', 'sp', -0x18),
        ('sw', 'ra', 0x10, 'sp'),
        ('jal', SEARCH),
        ('sw', 'a0', 0x14, 'sp'),
        ('lw', 'a0', 0x14, 'sp'),
        ('bltz', 'v0', 'bios'),
        ('sll', 'v1', 'v0', 5),
        ('lui', 'v0', hi(glyphs)),
        ('addiu', 'v0', 'v0', lo(glyphs)),
        ('b', 'out'),
        ('addu', 'v0', 'v0', 'v1'),
        'bios:',
        ('addiu', 't2', 'zero', 0xB0),
        ('jalr', 't2'),
        ('addiu', 't1', 'zero', 0x51),       # Krom2RawAdd(a0) -> v0 (-1 = none)
        ('addiu', 'at', 'zero', -1),
        ('bne', 'v0', 'at', 'copy'),
        ('lui', 't0', hi(tmp)),
        ('lui', 'v0', hi(glyphs)),
        ('b', 'out'),
        ('addiu', 'v0', 'v0', lo(glyphs)),
        'copy:',
        ('addiu', 't0', 't0', lo(tmp)),
        ('addiu', 't3', 't0', 29),
        'loop:',
        ('lbu', 't1', 0, 'v0'),
        ('addiu', 'v0', 'v0', 1),
        ('sb', 't1', 0, 't0'),
        ('bne', 't0', 't3', 'loop'),
        ('addiu', 't0', 't0', 1),
        ('sb', 'zero', 1, 't3'),
        ('sb', 'zero', 2, 't3'),
        ('lui', 'v0', hi(tmp)),
        ('addiu', 'v0', 'v0', lo(tmp)),
        'out:',
        ('lw', 'ra', 0x10, 'sp'),
        ('addiu', 'sp', 'sp', 0x18),
        ('jr', 'ra'),
        ('nop',),
    ])


class Heap:
    """Free RAM in the program image: alloc() hands out addresses, put() writes."""
    def __init__(self, exe, lo, hi):
        self.exe, self.lo, self.hi, self.top, self.end = exe, lo, hi, lo, hi

    def alloc_top(self, size, align=4):
        """Space at the end of the free RAM: its address does not move when the code grows."""
        a = (self.hi - size) & -align
        if a < self.top:
            raise ValueError('free RAM exhausted (%d bytes over)' % (self.top - a))
        self.hi = a
        return a

    def alloc(self, size, align=4):
        self.top += -self.top % align
        a = self.top
        self.top += size
        if self.top > self.hi:
            raise ValueError('free RAM exhausted (%d bytes over)' % (self.top - self.hi))
        return a

    def put(self, data, align=4):
        a = self.alloc(len(data), align)
        self.exe[a - EXE_BASE:a - EXE_BASE + len(data)] = data
        return a

    def code(self, fn, *args):
        """Place fn(address, *args) -> machine code (its length must not depend on the address)."""
        n = len(fn(self.top + -self.top % 4, *args))
        a = self.alloc(n)
        put(self.exe, a, fn(a, *args))
        return a

    def used(self):
        return self.top - self.lo + self.end - self.hi, self.end - self.lo


def word(exe, va):
    return struct.unpack_from('<I', exe, va - EXE_BASE)[0]


def put(exe, va, data):
    exe[va - EXE_BASE:va - EXE_BASE + len(data)] = data


def put_words(exe, va, *ws):
    put(exe, va, struct.pack('<%dI' % len(ws), *ws))


def apply(exe):
    """Patch the program (bytearray); returns a Heap over the freed area."""
    assert word(exe, SEARCH_N) == 0x24060000 | OLD_N and word(exe, SEARCH_HI) == 0x3c08800f
    assert word(exe, SEARCH_LO) == 0x250855a0 and word(exe, SEARCH_MISS) == 0x00002821
    assert word(exe, EXPAND_LOOKUP) == 0x0c000000 | SEARCH >> 2 & 0x3FFFFFF
    assert word(exe, NAME_N) == 0x2c620000 | OLD_N and word(exe, PAGE_N) == 0x2ca20000 | OLD_N - 2
    codes = struct.unpack_from('<%dH' % OLD_N, exe, CODES - EXE_BASE)
    old = bytes(exe[GLYPHS - EXE_BASE:AREA_END - EXE_BASE])
    new = {c: old[32 * i:32 * i + 32] for i, c in enumerate(codes) if keep_original(c)}
    new.update({c: latin_glyph(c) for c in LATIN})
    order = sorted(new)
    n = len(order)
    exe[CODES - EXE_BASE:AREA_END - EXE_BASE] = bytes(AREA_END - CODES)
    heap = Heap(exe, CODES, AREA_END)
    glyphs = heap.put(b''.join(new[c] for c in order))
    # the search reads codes[n] too (inclusive bound): 0xFFFF sentinel
    tab = heap.put(struct.pack('<%dH' % (n + 1), *(order + [0xFFFF])))
    tmp = heap.alloc(32)
    hi, lo = asm.hi(tab), asm.lo(tab) & 0xFFFF
    put_words(exe, SEARCH_N, 0x24060000 | n, 0x3c080000 | hi, 0x25080000 | lo)
    put_words(exe, SEARCH_MISS, 0x2405ffff)                      # addiu a1, zero, -1
    put_words(exe, NAME_HI, 0x3c050000 | hi, 0x24a50000 | lo)
    put_words(exe, NAME_N, 0x2c620000 | n)
    put_words(exe, PAGE_HI, 0x3c030000 | hi, 0x24630000 | lo)
    put_words(exe, PAGE_N, 0x2ca20000 | n - 2)
    fn = heap.code(glyph_ptr, glyphs, tmp)
    code = asm.assemble(EXPAND_LOOKUP, [('jal', fn), ('andi', 'a0', 'a0', 0xffff), ('addu', 't0', 'v0', 'zero')])
    put(exe, EXPAND_LOOKUP, code + bytes(EXPAND_LOOKUP_END - EXPAND_LOOKUP - len(code)))
    return heap
