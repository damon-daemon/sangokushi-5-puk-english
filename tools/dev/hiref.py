"""Strict lui-pair scanner: like mips.py refs, but a lui value dies when its register is
overwritten by any other instruction or at a branch/jump (fewer false positives).
usage: hiref.py lo hi   (prints site -> target, and the access kind)"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
import mips
img = mips.Image('jp')
lo, hi = int(sys.argv[1], 0), int(sys.argv[2], 0)
KIND = {0x09: 'addiu', 0x0D: 'ori', 0x20: 'lb', 0x21: 'lh', 0x23: 'lw', 0x24: 'lbu', 0x25: 'lhu',
        0x28: 'sb', 0x29: 'sh', 0x2B: 'sw', 0x22: 'lwl', 0x26: 'lwr', 0x2A: 'swl', 0x2E: 'swr'}
lui, clear = {}, 0
for va in range(img.base, img.end - 3, 4):
    if clear:
        clear -= 1
        if not clear:
            lui.clear()
    w = img.word(va)
    op, rs, rt, rd, imm = w >> 26, (w >> 21) & 31, (w >> 16) & 31, (w >> 11) & 31, w & 0xFFFF
    if op == 0x0F:
        lui[rt] = imm << 16; continue
    if op in KIND and rs in lui:
        t = (lui[rs] + (imm if op == 0x0D else mips.s16(imm))) & 0xFFFFFFFF
        if lo <= t < hi:
            print('%08x -> %08x %s' % (va, t, KIND[op]))
    # register writes
    if op == 0 and rd: lui.pop(rd, None)
    elif op in (0x08, 0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x0E) or 0x20 <= op <= 0x26: lui.pop(rt, None)
    elif op == 0x03: lui.pop(31, None)
    if op in (1, 2, 3, 4, 5, 6, 7) or (op == 0 and (w & 0x3F) in (8, 9)):
        clear = 2
