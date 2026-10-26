"""Latin letters in the officer name entry (notes/nameentry.md, notes/engine.md "Name entry").

Main menu -> Register officer data -> Register: one name field (12 bytes, 8 on the a0 = 1
path) and a half-width kana reading. The grids list the font code list's entries in a
code range per mode (kanji.py keeps the full-width Latin, digits and ．’－ for this), so:

  - mode 44 (button かな -> "A-Z") lists Ａ-Ｚ, mode 45 (カナ -> "a-z") ａ-ｚ; their
    expected counts follow (the page count is (count - 2) / 50); Sym keeps its range
    and shows the kept 　．’－ (count 4); the labels are progstr strings (tl/progstr);
  - append (0x8002ac60) goes through nm_append: Latin letters, digits and 　．’－ are
    stored as one ASCII byte (12 letters fit), other cells keep their 2 bytes but only
    when both fit (the game checked len <= max - 1, enough only while lengths were even);
  - backspace (0x8002ac2c) cuts the last character, 1 or 2 bytes, instead of 2.
The reading field has its own loop (1 byte per kana, buttons disabled): not touched.
"""
import os, sys

sys.path.insert(0, os.path.dirname(__file__))
import asm, kanji

RANGE_LO, RANGE_HI, COUNT = 0x8010df10, 0x8010dfd8, 0x8010de48     # u32 [50] per mode
GRIDS = {44: (0x829f, 0x82f1, 81, 0x8260, 0x8279),                 # mode: old lo, hi, n, new lo, hi
         45: (0x8340, 0x8396, 84, 0x8281, 0x829a)}
SYM, SYM_LO, SYM_HI, SYM_N = 47, 0x8140, 0x81ac, 30
APPEND, BS, LOOP = 0x8002ac60, 0x8002ac2c, 0x8002aaa0
ORIG = {APPEND: 0x8f82067c, APPEND + 4: 0x8f8306a8, BS: 0x1232ff9c, BS + 4: 0x2442fffe}
GP_MAX, GP_LEN = 0x67c, 0x6a8
ONE_BYTE = {0x8140: 0x20, **{c: ord(ch) for c, ch in kanji.SYMBOLS.items()}}
RUNS = [(0x8260, 26, 'A'), (0x8281, 26, 'a'), (0x824f, 10, '0')]


def routines():
    """nm_append: s0 = cell code, s1 = buf + len, s2 = buf; nm_bs: s1, s2 (len > 0).
    Both end at the loop head; v*, a*, t*, at are free there."""
    L = [
        'append:',
        ('lw', 't0', GP_MAX, 'gp'),
        ('lw', 't1', GP_LEN, 'gp'),
        ('andi', 't2', 's0', 0xffff),
    ]
    for code, n, ch in RUNS:
        L += [('ori', 'at', 'zero', code), ('subu', 't3', 't2', 'at'), ('sltiu', 'at', 't3', n),
              ('bnez', 'at', 'one'), ('addiu', 't4', 't3', ord(ch))]
    for code, ch in ONE_BYTE.items():
        L += [('ori', 'at', 'zero', code), ('beq', 't2', 'at', 'one'), ('addiu', 't4', 'zero', ch)]
    L += [
        ('addiu', 't3', 't1', 2),             # 2-byte character: both bytes must fit
        ('slt', 'at', 't0', 't3'),
        ('bnez', 'at', 'back'),
        ('srl', 't4', 't2', 8),
        ('sb', 't4', 0, 's1'),
        ('sb', 't2', 1, 's1'),
        ('addiu', 's1', 's1', 2),
        ('b', 'store'),
        ('addiu', 't1', 't1', 2),
        'one:',
        ('slt', 'at', 't1', 't0'),
        ('beqz', 'at', 'back'),
        ('nop',),
        ('sb', 't4', 0, 's1'),
        ('addiu', 's1', 's1', 1),
        ('addiu', 't1', 't1', 1),
        'store:',
        ('sw', 't1', GP_LEN, 'gp'),
        'back:',
        ('j', LOOP),
        ('nop',),

        'bs:',                                # t2 = start of the last character
        ('lw', 't0', GP_LEN, 'gp'),
        ('addu', 't1', 'zero', 'zero'),
        ('addu', 't2', 'zero', 'zero'),
        'bl:',
        ('slt', 'at', 't1', 't0'),
        ('beqz', 'at', 'cut'),
        ('addu', 't3', 's2', 't1'),
        ('lbu', 't3', 0, 't3'),
        ('addu', 't2', 't1', 'zero'),
        ('sltiu', 'at', 't3', 0x80),
        ('beqz', 'at', 'two'),
        ('nop',),
        ('b', 'bl'),
        ('addiu', 't1', 't1', 1),
        'two:',
        ('b', 'bl'),
        ('addiu', 't1', 't1', 2),
        'cut:',                               # bytes after len are already 0
        ('addu', 's1', 's2', 't2'),
        ('sb', 'zero', 0, 's1'),
        ('sb', 'zero', 1, 's1'),
        ('j', LOOP),
        ('sw', 't2', GP_LEN, 'gp'),
    ]
    return L


def apply(exe, heap):
    """Patch the program (bytearray); code into heap. Returns bytes of RAM used."""
    for va, w in ORIG.items():
        assert kanji.word(exe, va) == w, hex(va)
    for mode, (lo, hi, n, nlo, nhi) in GRIDS.items():
        assert (kanji.word(exe, RANGE_LO + 4 * mode), kanji.word(exe, RANGE_HI + 4 * mode),
                kanji.word(exe, COUNT + 4 * mode)) == (lo, hi, n), mode
        kanji.put_words(exe, RANGE_LO + 4 * mode, nlo)
        kanji.put_words(exe, RANGE_HI + 4 * mode, nhi)
        kanji.put_words(exe, COUNT + 4 * mode, nhi - nlo + 1)
    assert kanji.word(exe, COUNT + 4 * SYM) == SYM_N
    kanji.put_words(exe, COUNT + 4 * SYM, sum(SYM_LO <= c <= SYM_HI for c in kanji.LATIN))
    L = routines()
    asm.check_delay(L)
    at = heap.code(asm.assemble, L)
    lab = asm.labels(at, L)
    kanji.put(exe, APPEND, asm.assemble(APPEND, [('j', lab['append'])]))   # delay: lw v1, len (kept)
    kanji.put(exe, BS, asm.assemble(BS, [('j', lab['bs']), ('nop',)]))
    return len(asm.assemble(at, L))
