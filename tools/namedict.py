"""Japanese -> English name dictionary in free RAM and the hooks that use it (notes/engine.md "Names").

All other text is English by now, so any 2-byte Shift-JIS that reaches a printer is a name from a
record table (tools/names.py: officers, cities, title prefixes, items, formations). The records stay
Japanese; the text is expanded right before it is drawn or measured:

  expand(a0 = src, a1 = dst, a2 = dst size, a3 = column base) -> v0 = end of dst (NUL), v1 = source
      bytes read: at every lead byte the longest dictionary key is looked up and replaced by its
      English, no hit -> the 2 bytes are copied; ESC sequences (0x1A/0x1B + letter + 1 byte unless
      'W') are copied as they are, so an argument byte is never taken for a lead byte.
  Columns: padded formats (%-8s, %8s, %5d, ...) line up at 8 px a source byte. A name hit followed
      by >= 2 spaces and more text on the line, spaces before digits (>= 2, or 1 at the line start before a lone
      number, or 1 on a line that has a tab already), or >= 2 spaces before more text on a line with a tab; no ESC on the line (one space is prose) -> the spaces become TAB 0x1F,
      0x30 + (px >> 5), 0x40 + (px & 31) (never '%', a space or a lead byte: the text is a format
      again); digits after it are right-aligned on their Japanese end column; px = column
      base + 8 x (source bytes since the line start), base = a3 & 0xFFFF on the first line (where the
      text starts, startx), a3 >> 16 on later lines (window left); measure passes 0. vwf_cell,
      wrap_fmt and measure draw/count a tab as x = max(x + space, px).
      Right-aligned names (%8s: >= 2 spaces, then a name hit): the tab puts the English so that it
      ends on the name's Japanese end column (vwf widths), but never left of the first space.
  formatter path (wprintf 0x80019744, printf_at 0x800c41d0, vwf.FORMAT_CALLS): names_fmt formats
      the whole text first (the game's formatter 0x80015008 with store_cb writing into BUF_F, '%'
      doubled because the result is used as a format again), expands it into BUF_X and jumps to
      vwf's wrap_fmt with that (no conversions left) -> %s names are expanded and wrapped too.
  print_string 0x80019658 (ctx, str, max bytes): ps_hook expands str into BUF_P before the loop;
      the byte limit grows by what the expansion added (<= 0x7fff).
  measure (vwf.MEASURE_CALLS): meas_hook expands into BUF_M, then vwf's pixel measure.

Dictionary (pack()):
  IDX:  u32 n, then n x (u16 first character (lead << 8 | trail), u16 offset into BLOB), sorted
  BLOB: per first character, entries longest key first: u8 0x20 | length of the rest of the key
        (< 32), the rest of the key, the English, NUL; a 0 byte ends the group.
"""
import os, struct, sys

sys.path.insert(0, os.path.dirname(__file__))
import asm, vwf
from asm import hi, lo
from kanji import put, word

F_ANY = 0x20
CAP = 1024                               # expanded text (formatted text <= BUF_F)
FMT_CAP = 768
PS_LOOP = 0x8001967c                     # print_string: lbu a1, 0(s1) / nop, s1 = string, a2 = limit
TAB, TAB_HI, TAB_LO = vwf.TAB, vwf.TAB_HI, vwf.TAB_LO
DIGIT_ADV = vwf.pixfont.DIGIT_W + vwf.pixfont.SPACING


def vwf_widths():
    g = vwf.pixfont.glyphs()
    return bytes(g[chr(c)][1] for c in vwf.ASCII)


def is_lead(c):
    return 0x81 <= c <= 0x9F or 0xE0 <= c <= 0xFC


class Dict:
    def __init__(self, pairs, widths=None):
        """pairs {jp bytes: english bytes}; widths = vwf advance per ASCII 0x20..0x7E"""
        self.ent = dict(pairs)
        self.widths = widths if widths is not None else vwf_widths()

    def lookup(self, s, i):
        for n in range(len(s) - i, 1, -1):
            e = self.ent.get(s[i:i + n])
            if e is not None:
                return e, n
        return None

    def expand(self, s, cap=CAP, base=0):
        """Model of the MIPS expand (s without its NUL; base = expand's a3)."""
        out, i = bytearray(), 0
        lim = cap - 1
        start, later, esc, tabbed = 0, False, False, False

        def tab(j):
            """spaces up to s[j] -> column tab (True), or False when there is no room"""
            nonlocal tabbed
            if len(out) + 3 > lim:
                return False
            px = ((base >> 16) if later else (base & 0xFFFF)) + (j - start) * 8
            k = j
            while k < len(s) and 0x30 <= s[k] <= 0x39:
                k += 1
            px = min(px + (k - j) * (8 - DIGIT_ADV), 511)    # digits: right-aligned on the end column
            out.extend([TAB, TAB_HI + (px >> 5), TAB_LO + (px & 31)])
            tabbed = True
            return True

        def lone(j):
            """digits from s[j] to the end of the text or line (a list cell number)"""
            while j < len(s) and 0x30 <= s[j] <= 0x39:
                j += 1
            return j == len(s) or s[j] == 0x0A

        def rtab(i, j):
            """spaces s[i:j] before a name hit -> tab that right-aligns its English (True)"""
            nonlocal tabbed
            hit = self.lookup(s, j) if j < len(s) and is_lead(s[j]) else None
            if not hit or len(out) + 3 > lim:
                return False
            e, n = hit
            b = (base >> 16) if later else (base & 0xFFFF)
            px = b + (j + n - start) * 8 - sum(self.widths[c - 0x20] for c in e if 0x20 <= c < 0x7F)
            px = min(max(px, b + (i - start) * 8), 511)
            out.extend([TAB, TAB_HI + (px >> 5), TAB_LO + (px & 31)])
            tabbed = True
            return True

        while i < len(s) and len(out) < lim:
            c = s[i]
            if c in (0x1A, 0x1B):
                esc = True
                n = 2 if i + 1 < len(s) and s[i + 1] == 0x57 else 3
                for b in s[i:i + n]:
                    if len(out) < lim:
                        out.append(b)
                i += n
                continue
            if not is_lead(c):
                if c == 0x20 and not esc:
                    j = i
                    while j < len(s) and s[j] == 0x20:
                        j += 1
                    dig = j < len(s) and 0x30 <= s[j] <= 0x39
                    more = j < len(s) and s[j] not in (0x0A, 0x0D)
                    if not dig and j - i >= 2 and rtab(i, j) or \
                            (dig and (j - i >= 2 or tabbed or i == start and lone(j)) or more and j - i >= 2 and tabbed) and tab(j):
                        i = j
                        continue
                out.append(c)
                i += 1
                if c == 0x0A:
                    start, later, esc, tabbed = i, True, False, False
                continue
            hit = self.lookup(s, i)
            if hit:
                e, n = hit
                full = True
                for b in e:
                    if len(out) >= lim:
                        full = False
                        break
                    out.append(b)
                i += n
                if full and not esc and i < len(s) and s[i] == 0x20:
                    j = i
                    while j < len(s) and s[j] == 0x20:
                        j += 1
                    if j - i >= 2 and j < len(s) and s[j] not in (0x0A, 0x0D) and (rtab(i, j) or tab(j)):
                        i = j
                continue
            out.append(c)
            if i + 1 < len(s):
                if len(out) < lim:
                    out.append(s[i + 1])
            i += 2
        return bytes(out)


def pack(d):
    """(idx bytes, blob bytes)"""
    groups = {}
    for k, e in d.ent.items():
        if len(k) < 2 or len(k) - 2 >= 32 or len(k) % 2:
            raise ValueError('bad name key %r' % k)
        groups.setdefault(k[:2], []).append((k, e))
    blob, idx = bytearray(), []
    for first in sorted(groups):
        idx.append((first[0] << 8 | first[1], len(blob)))
        for k, e in sorted(groups[first], key=lambda x: -len(x[0])):
            blob += bytes([F_ANY | (len(k) - 2)]) + k[2:] + e + b'\0'
        blob.append(0)
    if len(blob) > 0xFFFF:
        raise ValueError('name dictionary too large')
    head = struct.pack('<I', len(idx)) + b''.join(struct.pack('<HH', c, o) for c, o in idx)
    return head, bytes(blob)


def lookup_lines(idx, blob):
    """lookup(a0 = text at a lead byte) -> v0 English (0 = none), v1 key length. Leaf, t0-t9."""
    return [
        'lookup:',
        ('lbu', 't0', 0, 'a0'),
        ('lbu', 't1', 1, 'a0'),
        ('sll', 't0', 't0', 8),
        ('or', 't0', 't0', 't1'),
        ('lui', 't2', hi(idx)),
        ('addiu', 't2', 't2', lo(idx)),
        ('lw', 't3', 0, 't2'),                # hi = n
        ('addiu', 't2', 't2', 4),
        ('addu', 't4', 'zero', 'zero'),       # lo
        'bs:',
        ('sltu', 'at', 't4', 't3'),
        ('beqz', 'at', 'none'),
        ('addu', 't5', 't4', 't3'),
        ('srl', 't5', 't5', 1),
        ('sll', 't6', 't5', 2),
        ('addu', 't6', 't2', 't6'),
        ('lhu', 't7', 0, 't6'),
        ('nop',),
        ('beq', 't7', 't0', 'found'),
        ('sltu', 'at', 't7', 't0'),
        ('beqz', 'at', 'lower'),
        ('nop',),
        ('b', 'bs'),
        ('addiu', 't4', 't5', 1),
        'lower:',
        ('b', 'bs'),
        ('addu', 't3', 't5', 'zero'),
        'found:',
        ('lhu', 't6', 2, 't6'),
        ('lui', 't7', hi(blob)),
        ('addiu', 't7', 't7', lo(blob)),
        ('addu', 't6', 't7', 't6'),
        'ent:',
        ('lbu', 't7', 0, 't6'),
        ('nop',),
        ('beqz', 't7', 'none'),
        ('addiu', 't6', 't6', 1),
        ('andi', 't8', 't7', 0x1F),
        ('addu', 't9', 'zero', 'zero'),
        'cmp:',
        ('beq', 't9', 't8', 'match'),
        ('addu', 'at', 'a0', 't9'),
        ('lbu', 'v0', 2, 'at'),
        ('addu', 'at', 't6', 't9'),
        ('lbu', 'v1', 0, 'at'),
        ('addiu', 't9', 't9', 1),
        ('bne', 'v0', 'v1', 'skip'),
        ('nop',),
        ('b', 'cmp'),
        ('nop',),
        'match:',
        ('addu', 'v0', 't6', 't8'),
        ('jr', 'ra'),
        ('addiu', 'v1', 't8', 2),
        'skip:',
        ('addu', 't6', 't6', 't8'),
        'sk2:',
        ('lbu', 'at', 0, 't6'),
        ('addiu', 't6', 't6', 1),
        ('bnez', 'at', 'sk2'),
        ('nop',),
        ('b', 'ent'),
        ('nop',),
        'none:',
        ('jr', 'ra'),
        ('addu', 'v0', 'zero', 'zero'),
    ]


def expand_lines(state, widths):
    """expand(a0 = src, a1 = dst, a2 = dst size, a3 = column base) -> v0 = end of dst (at the NUL),
    v1 = source read. s0 src, s1 dst, s2 last usable dst byte (room for the NUL), s3 source line
    start, s4 flags (1: ESC on this line, 2: not the first line, 4: a tab on this line)."""
    return [
        'expand:',
        ('addiu', 'sp', 'sp', -56),
        ('sw', 'ra', 16, 'sp'),
        ('sw', 's0', 20, 'sp'),
        ('sw', 's1', 24, 'sp'),
        ('sw', 's2', 28, 'sp'),
        ('sw', 's3', 32, 'sp'),
        ('sw', 's4', 36, 'sp'),
        ('sw', 'a0', 40, 'sp'),
        ('sw', 'a3', 44, 'sp'),
        ('addu', 's0', 'a0', 'zero'),
        ('addu', 's1', 'a1', 'zero'),
        ('addu', 's2', 'a1', 'a2'),
        ('addiu', 's2', 's2', -1),
        ('addu', 's3', 'a0', 'zero'),
        ('addu', 's4', 'zero', 'zero'),
        'exp:',
        ('lbu', 't0', 0, 's0'),
        ('sltu', 'at', 's1', 's2'),
        ('beqz', 't0', 'expdone'),
        ('nop',),
        ('beqz', 'at', 'expdone'),
        ('addiu', 't1', 't0', -0x81),
        ('sltiu', 'at', 't1', 0x1F),
        ('bnez', 'at', 'lead'),
        ('addiu', 't1', 't0', -0xE0),
        ('sltiu', 'at', 't1', 0x1D),
        ('bnez', 'at', 'lead'),
        ('addiu', 't1', 't0', -0x1A),
        ('sltiu', 'at', 't1', 2),
        ('bnez', 'at', 'esc'),
        ('nop',),
        ('addiu', 'at', 'zero', 0x20),
        ('beq', 't0', 'at', 'spc'),
        ('nop',),
        'single:',
        ('sb', 't0', 0, 's1'),
        ('addiu', 's0', 's0', 1),
        ('addiu', 'at', 'zero', 0x0A),
        ('bne', 't0', 'at', 'exp'),
        ('addiu', 's1', 's1', 1),
        ('addu', 's3', 's0', 'zero'),         # new line: columns count from here
        ('b', 'exp'),
        ('addiu', 's4', 'zero', 2),
        'spc:',                               # spaces before digits -> column tab
        ('andi', 't2', 's4', 1),
        ('bnez', 't2', 'single'),
        ('addu', 't1', 's0', 'zero'),
        'ssp:',
        ('addiu', 't1', 't1', 1),
        ('lbu', 't3', 0, 't1'),
        ('nop',),
        ('beq', 't3', 'at', 'ssp'),
        ('addiu', 't2', 't3', -0x30),
        ('sltiu', 't2', 't2', 10),
        ('beqz', 't2', 'snodig'),
        ('subu', 't2', 't1', 's0'),
        ('sltiu', 't2', 't2', 2),
        ('beqz', 't2', 'temit'),              # >= 2 spaces before digits
        ('andi', 't2', 's4', 4),
        ('bnez', 't2', 'temit'),              # one, on a line with columns
        ('nop',),
        ('bne', 's0', 's3', 'single'),        # one, at the line start before a lone number (list cell
        ('addu', 't5', 't1', 'zero'),         # " 100" under "  98"; not " 1/ 2")
        'lsd:',
        ('lbu', 't3', 0, 't5'),
        ('nop',),
        ('addiu', 't6', 't3', -0x30),
        ('sltiu', 't6', 't6', 10),
        ('bnez', 't6', 'lsd'),
        ('addiu', 't5', 't5', 1),
        ('beqz', 't3', 'temit'),
        ('addiu', 't6', 't3', -0x0A),
        ('beqz', 't6', 'temit'),
        ('nop',),
        ('b', 'single'),
        ('nop',),
        'snodig:',                            # >= 2 spaces before more text, on a line with columns
        ('sltiu', 't2', 't2', 2),
        ('bnez', 't2', 'single'),
        ('sw', 'zero', 52, 'sp'),             # ... or before a name: right-aligned (miss -> the rules below)
        ('addiu', 't4', 't3', -0x81),
        ('sltiu', 'at', 't4', 0x1F),
        ('bnez', 'at', 'sname'),
        ('addiu', 't4', 't3', -0xE0),
        ('sltiu', 'at', 't4', 0x1D),
        ('bnez', 'at', 'sname'),
        ('nop',),
        'snold:',
        ('andi', 't2', 's4', 4),
        ('beqz', 't2', 'single'),
        ('nop',),
        ('beqz', 't3', 'single'),
        ('addiu', 't2', 'zero', 0x0A),
        ('beq', 't3', 't2', 'single'),
        ('addiu', 't2', 'zero', 0x0D),
        ('beq', 't3', 't2', 'single'),
        ('nop',),
        ('b', 'temit'),
        ('nop',),
        'plain:',
        ('sb', 't0', 0, 's1'),
        ('addiu', 's0', 's0', 1),
        ('b', 'exp'),
        ('addiu', 's1', 's1', 1),
        'esc:',                               # ESC, letter, argument unless 'W': copied
        ('ori', 's4', 's4', 1),
        ('sb', 't0', 0, 's1'),
        ('addiu', 's1', 's1', 1),
        ('addiu', 's0', 's0', 1),
        ('lbu', 't0', 0, 's0'),               # letter
        ('sltu', 'at', 's1', 's2'),
        ('beqz', 't0', 'exp'),
        ('nop',),
        ('beqz', 'at', 'expdone'),
        ('nop',),
        ('sb', 't0', 0, 's1'),
        ('addiu', 's1', 's1', 1),
        ('addiu', 'at', 'zero', 0x57),
        ('beq', 't0', 'at', 'exp'),
        ('addiu', 's0', 's0', 1),
        ('lbu', 't0', 0, 's0'),               # argument
        ('sltu', 'at', 's1', 's2'),
        ('beqz', 't0', 'exp'),
        ('nop',),
        ('beqz', 'at', 'expdone'),
        ('nop',),
        ('b', 'plain'),
        ('nop',),
        'lead:',
        ('jal', 'lookup'),
        ('addu', 'a0', 's0', 'zero'),
        ('beqz', 'v0', 'raw2'),
        ('nop',),
        ('addu', 's0', 's0', 'v1'),
        'copy:',
        ('lbu', 't0', 0, 'v0'),
        ('addiu', 'v0', 'v0', 1),
        ('beqz', 't0', 'tabchk'),
        ('sltu', 'at', 's1', 's2'),
        ('beqz', 'at', 'expdone'),
        ('nop',),
        ('sb', 't0', 0, 's1'),
        ('b', 'copy'),
        ('addiu', 's1', 's1', 1),
        'tabchk:',                            # name, spaces, more on the line -> column tab
        ('andi', 'at', 's4', 1),
        ('bnez', 'at', 'exp'),
        ('lbu', 't0', 0, 's0'),
        ('addiu', 'at', 'zero', 0x20),
        ('bne', 't0', 'at', 'exp'),
        ('addu', 't1', 's0', 'zero'),
        'tsp:',
        ('addiu', 't1', 't1', 1),
        ('lbu', 't0', 0, 't1'),
        ('nop',),
        ('beq', 't0', 'at', 'tsp'),
        ('subu', 't2', 't1', 's0'),
        ('sltiu', 't2', 't2', 2),             # one space: prose
        ('bnez', 't2', 'exp'),
        ('nop',),
        ('beqz', 't0', 'exp'),
        ('addiu', 'at', 'zero', 0x0A),
        ('beq', 't0', 'at', 'exp'),
        ('addiu', 'at', 'zero', 0x0D),
        ('beq', 't0', 'at', 'exp'),
        ('addiu', 't4', 'zero', 1),           # a name after the spaces: right-aligned (miss -> temit)
        ('sw', 't4', 52, 'sp'),
        ('addiu', 't4', 't0', -0x81),
        ('sltiu', 'at', 't4', 0x1F),
        ('bnez', 'at', 'sname'),
        ('addiu', 't4', 't0', -0xE0),
        ('sltiu', 'at', 't4', 0x1D),
        ('bnez', 'at', 'sname'),
        ('nop',),
        'temit:',                             # t1 = source after the spaces
        ('addiu', 't2', 's1', 3),
        ('sltu', 'at', 's2', 't2'),           # room for 3 bytes before the NUL
        ('beqz', 'at', 'troom'),
        ('sltu', 'at', 's1', 's2'),
        ('beqz', 'at', 'expdone'),
        ('addiu', 't0', 'zero', 0x20),
        ('b', 'single'),                      # no room: the space as it is
        ('nop',),
        'troom:',
        ('subu', 't2', 't1', 's3'),
        ('sll', 't2', 't2', 3),
        ('lw', 't3', 44, 'sp'),
        ('andi', 'at', 's4', 2),
        ('beqz', 'at', 'tfirst'),
        ('andi', 't4', 't3', 0xFFFF),
        ('srl', 't4', 't3', 16),
        'tfirst:',
        ('addu', 't2', 't2', 't4'),
        ('addu', 't5', 't1', 'zero'),
        ('lbu', 't3', 0, 't5'),
        ('nop',),
        'tdig:',                              # digits: right-aligned on the end column
        ('addiu', 't6', 't3', -0x30),
        ('sltiu', 't6', 't6', 10),
        ('beqz', 't6', 'tdend'),
        ('nop',),
        ('addiu', 't2', 't2', 8 - DIGIT_ADV),
        ('addiu', 't5', 't5', 1),
        ('lbu', 't3', 0, 't5'),
        ('b', 'tdig'),
        ('nop',),
        'tdend:',
        ('sltiu', 'at', 't2', 512),
        ('bnez', 'at', 'tput'),
        ('nop',),
        ('addiu', 't2', 'zero', 511),
        'tput:',
        ('addiu', 't0', 'zero', TAB),
        ('sb', 't0', 0, 's1'),
        ('srl', 't0', 't2', 5),
        ('addiu', 't0', 't0', TAB_HI),
        ('sb', 't0', 1, 's1'),
        ('andi', 't0', 't2', 31),
        ('addiu', 't0', 't0', TAB_LO),
        ('sb', 't0', 2, 's1'),
        ('addiu', 's1', 's1', 3),
        ('ori', 's4', 's4', 4),
        ('b', 'exp'),
        ('addu', 's0', 't1', 'zero'),
        'sname:',                             # s0 first space, t1 name: tab so its English ends on
        ('sw', 't1', 48, 'sp'),               # the Japanese end column (>= the first space's column)
        ('jal', 'lookup'),
        ('addu', 'a0', 't1', 'zero'),
        ('lw', 't1', 48, 'sp'),
        ('beqz', 'v0', 'snmiss'),
        ('addiu', 't2', 's1', 3),
        ('sltu', 'at', 's2', 't2'),
        ('bnez', 'at', 'snmiss'),
        ('addu', 't2', 't1', 'v1'),
        ('subu', 't2', 't2', 's3'),
        ('sll', 't2', 't2', 3),
        ('lw', 't3', 44, 'sp'),
        ('andi', 'at', 's4', 2),
        ('beqz', 'at', 'snfirst'),
        ('andi', 't4', 't3', 0xFFFF),
        ('srl', 't4', 't3', 16),
        'snfirst:',
        ('addu', 't2', 't2', 't4'),           # end column
        ('subu', 't5', 's0', 's3'),
        ('sll', 't5', 't5', 3),
        ('addu', 't5', 't5', 't4'),           # first space's column
        ('lui', 't7', hi(widths)),
        ('addiu', 't7', 't7', lo(widths)),
        'snw:',
        ('lbu', 't3', 0, 'v0'),
        ('addiu', 'v0', 'v0', 1),
        ('beqz', 't3', 'snwend'),
        ('addiu', 't3', 't3', -0x20),
        ('sltiu', 'at', 't3', 0x5F),
        ('beqz', 'at', 'snw'),
        ('addu', 't3', 't7', 't3'),
        ('lbu', 't3', 0, 't3'),
        ('b', 'snw'),
        ('subu', 't2', 't2', 't3'),
        'snwend:',
        ('slt', 'at', 't2', 't5'),
        ('beqz', 'at', 'tdend'),
        ('nop',),
        ('b', 'tdend'),
        ('addu', 't2', 't5', 'zero'),
        'snmiss:',
        ('lw', 't2', 52, 'sp'),
        ('lbu', 't3', 0, 't1'),
        ('bnez', 't2', 'temit'),
        ('addiu', 't0', 'zero', 0x20),
        ('b', 'snold'),
        ('nop',),
        'raw2:',
        ('lbu', 't0', 0, 's0'),
        ('lbu', 't1', 1, 's0'),
        ('sb', 't0', 0, 's1'),
        ('addiu', 's1', 's1', 1),
        ('beqz', 't1', 'expdone'),
        ('addiu', 's0', 's0', 1),
        ('sltu', 'at', 's1', 's2'),
        ('beqz', 'at', 'expdone'),
        ('nop',),
        ('sb', 't1', 0, 's1'),
        ('addiu', 's1', 's1', 1),
        ('b', 'exp'),
        ('addiu', 's0', 's0', 1),
        'expdone:',
        ('sb', 'zero', 0, 's1'),
        ('addu', 'v0', 's1', 'zero'),
        ('lw', 't0', 40, 'sp'),
        ('lw', 'ra', 16, 'sp'),
        ('subu', 'v1', 's0', 't0'),
        ('lw', 's0', 20, 'sp'),
        ('lw', 's1', 24, 'sp'),
        ('lw', 's2', 28, 'sp'),
        ('lw', 's3', 32, 'sp'),
        ('lw', 's4', 36, 'sp'),
        ('jr', 'ra'),
        ('addiu', 'sp', 'sp', 56),

        'startx:',                            # a3 = column base: window left px << 16 | text start px
        ('lui', 't0', hi(state)),             # (vwf's pixel x while the cursor is where vwf left it)
        ('lh', 't1', lo(state), 't0'),
        ('lh', 't2', lo(state + 2), 't0'),
        ('lh', 't3', vwf.GP_X, 'gp'),
        ('lh', 't4', vwf.GP_Y, 'gp'),
        ('lh', 't5', lo(state + 4), 't0'),
        ('bne', 't1', 't3', 'sxreset'),
        ('nop',),
        ('beq', 't2', 't4', 'sxkeep'),
        ('nop',),
        'sxreset:',
        ('sll', 't5', 't3', 3),
        'sxkeep:',
        ('lh', 't6', vwf.GP_LEFT, 'gp'),
        ('andi', 't5', 't5', 0xFFFF),
        ('sll', 't6', 't6', 19),
        ('jr', 'ra'),
        ('or', 'a3', 't5', 't6'),
    ]


def code_lines(idx, blob, ptr, buf_f, buf_x, buf_p, buf_m, wrap, meas, state, widths, store_cb):
    return lookup_lines(idx, blob) + expand_lines(state, widths) + [
        'store_cb:',                          # formatter callback (a0 = character): into BUF_F
        ('lui', 't0', hi(ptr)),
        ('lw', 't1', lo(ptr), 't0'),
        ('lui', 't2', hi(buf_f + FMT_CAP - 3)),
        ('addiu', 't2', 't2', lo(buf_f + FMT_CAP - 3)),
        ('sltu', 'at', 't1', 't2'),
        ('beqz', 'at', 'stret'),
        ('andi', 'a0', 'a0', 0xFF),
        ('sb', 'a0', 0, 't1'),
        ('addiu', 't1', 't1', 1),
        ('addiu', 'at', 'zero', 0x25),        # '%' -> "%%" (the result is a format again)
        ('bne', 'a0', 'at', 'stput'),
        ('nop',),
        ('sb', 'a0', 0, 't1'),
        ('addiu', 't1', 't1', 1),
        'stput:',
        ('sw', 't1', lo(ptr), 't0'),
        'stret:',
        ('jr', 'ra'),
        ('nop',),

        'names_fmt:',                         # in place of jal FORMAT (a0 callback, a1 format, a2 args)
        ('addiu', 'sp', 'sp', -24),
        ('sw', 'ra', 16, 'sp'),
        ('sw', 'a0', 20, 'sp'),
        ('lui', 't0', hi(ptr)),
        ('lui', 't1', hi(buf_f)),
        ('addiu', 't1', 't1', lo(buf_f)),
        ('sw', 't1', lo(ptr), 't0'),
        ('lui', 'a0', hi(store_cb)),
        ('jal', vwf.FORMAT),
        ('addiu', 'a0', 'a0', lo(store_cb)),
        ('lui', 't0', hi(ptr)),
        ('lw', 't1', lo(ptr), 't0'),
        ('lui', 'a0', hi(buf_f)),
        ('sb', 'zero', 0, 't1'),
        ('addiu', 'a0', 'a0', lo(buf_f)),
        ('subu', 't2', 't1', 'a0'),           # Japanese width in bytes (vwf's one-line clip)
        ('lui', 't3', hi(state + vwf.S_JW)),
        ('sh', 't2', lo(state + vwf.S_JW), 't3'),
        ('jal', 'startx'),                    # t0-t6, a3 only
        ('nop',),
        ('lui', 'a1', hi(buf_x)),
        ('addiu', 'a1', 'a1', lo(buf_x)),
        ('jal', 'expand'),
        ('addiu', 'a2', 'zero', CAP),
        ('lw', 'a0', 20, 'sp'),
        ('lw', 'ra', 16, 'sp'),
        ('lui', 'a1', hi(buf_x)),
        ('addiu', 'a1', 'a1', lo(buf_x)),
        ('j', wrap),
        ('addiu', 'sp', 'sp', 24),

        'ps_hook:',                           # print_string, before its loop: s1 = string, a2 = limit
        ('addiu', 'sp', 'sp', -24),
        ('sw', 'ra', 16, 'sp'),
        ('sw', 'a2', 20, 'sp'),
        ('jal', 'startx'),
        ('addu', 'a0', 's1', 'zero'),
        ('lui', 'a1', hi(buf_p)),
        ('addiu', 'a1', 'a1', lo(buf_p)),
        ('jal', 'expand'),
        ('addiu', 'a2', 'zero', CAP),
        ('lui', 's1', hi(buf_p)),
        ('addiu', 's1', 's1', lo(buf_p)),
        ('subu', 'v0', 'v0', 's1'),           # bytes out - bytes in = growth
        ('subu', 'v0', 'v0', 'v1'),
        ('lw', 'a2', 20, 'sp'),
        ('nop',),
        ('sll', 'a2', 'a2', 16),
        ('blez', 'v0', 'psgo'),
        ('sra', 'a2', 'a2', 16),
        ('addu', 'a2', 'a2', 'v0'),
        ('slti', 'at', 'a2', 0x7fff),
        ('bnez', 'at', 'psgo'),
        ('nop',),
        ('addiu', 'a2', 'zero', 0x7fff),
        'psgo:',
        ('lw', 'ra', 16, 'sp'),
        ('lbu', 'a1', 0, 's1'),
        ('jr', 'ra'),
        ('addiu', 'sp', 'sp', 24),

        'meas_hook:',                         # measure(a0 = &lines, a1 = str)
        ('addiu', 'sp', 'sp', -24),
        ('sw', 'ra', 16, 'sp'),
        ('sw', 'a0', 20, 'sp'),
        ('addu', 'a0', 'a1', 'zero'),
        ('addu', 'a3', 'zero', 'zero'),       # measure: columns from x = 0
        ('lui', 'a1', hi(buf_m)),
        ('addiu', 'a1', 'a1', lo(buf_m)),
        ('jal', 'expand'),
        ('addiu', 'a2', 'zero', CAP),
        ('lw', 'a0', 20, 'sp'),
        ('lw', 'ra', 16, 'sp'),
        ('lui', 'a1', hi(buf_m)),
        ('addiu', 'a1', 'a1', lo(buf_m)),
        ('j', meas),
        ('addiu', 'sp', 'sp', 24),
    ]


def code(at, *args):
    """Machine code at `at`; the length does not depend on the address (heap.code)."""
    lab = asm.labels(at, code_lines(*args, at))
    return asm.assemble(at, code_lines(*args, lab['store_cb']))


def labels(at, *args):
    return asm.labels(at, code_lines(*args, at))


def apply(exe, heap, pairs, hooks):
    """pairs {jp: en}; hooks = vwf.apply's result. Returns the Dict."""
    for a in vwf.FORMAT_CALLS + vwf.MEASURE_CALLS:
        assert word(exe, a) >> 26 == 3, hex(a)
    assert word(exe, PS_LOOP) == 0x92250000 and word(exe, PS_LOOP + 4) == 0
    d = Dict(pairs, vwf_widths())
    head, blob = pack(d)
    idx = heap.put(head)
    blob_a = heap.put(blob)
    ptr = heap.alloc(4)
    buf_f = heap.alloc(FMT_CAP)
    buf_x, buf_p, buf_m = heap.alloc(CAP), heap.alloc(CAP), heap.alloc(CAP)
    args = (idx, blob_a, ptr, buf_f, buf_x, buf_p, buf_m, hooks['wrap'], hooks['measure'], hooks['state'],
            hooks['widths'])
    at = heap.code(code, *args)
    lab = labels(at, *args)
    for a in vwf.FORMAT_CALLS:
        put(exe, a, asm.assemble(a, [('jal', lab['names_fmt'])]))
    for a in vwf.MEASURE_CALLS:
        put(exe, a, asm.assemble(a, [('jal', lab['meas_hook'])]))
    put(exe, PS_LOOP, asm.assemble(PS_LOOP, [('jal', lab['ps_hook'])]))
    return d, lab
