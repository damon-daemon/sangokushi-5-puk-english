"""Rough constant-argument tracker: for each jal to <target>, emulate lui/ori/addiu/lw-from-abs
in the 24 instructions before it (+ delay slot) and print a0..a3 when constant.
usage: callargs.py target"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
import mips
img = mips.Image('jp')
tgt = int(sys.argv[1], 0)
for c in mips.jals(img, tgt):
    reg = {}
    for va in list(range(c - 4 * 24, c, 4)) + [c + 4]:
        w = img.word(va)
        op, rs, rt, rd, imm = w >> 26, (w >> 21) & 31, (w >> 16) & 31, (w >> 11) & 31, w & 0xFFFF
        if op in (2, 3) or (op == 0 and (w & 0x3F) in (8, 9)) or (op in (4, 5, 6, 7, 1)):
            if va != c + 4 and va < c - 4:
                reg = {}
            continue
        base = 0 if rs == 0 else reg.get(rs)
        if op == 0x0F: reg[rt] = imm << 16
        elif op == 0x0D: reg[rt] = base | imm if isinstance(base, int) else None
        elif op == 0x09: reg[rt] = (base + mips.s16(imm)) & 0xFFFFFFFF if isinstance(base, int) else None
        elif op == 0x23: reg[rt] = ('[%08x]' % ((reg[rs] + mips.s16(imm)) & 0xFFFFFFFF)) if isinstance(reg.get(rs), int) else None
        elif op == 0 and (w & 0x3F) in (0x21, 0x25) and rt == 0: reg[rd] = reg.get(rs) if rs else 0
        elif op == 0 and (w & 0x3F) == 0x21: reg[rd] = None
        elif op in (0x08, 0x0C, 0x0A, 0x0B) or (op == 0 and rd): 
            r = rt if op else rd
            reg[r] = None
    f = lambda v: ('%08x' % v if isinstance(v, int) else v) if v is not None else '?'
    print('%08x  a0=%s a1=%s a2=%s a3=%s' % (c, *(f(reg.get(r)) for r in (4, 5, 6, 7))))
