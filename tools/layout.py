"""Small layout fixes for English in fixed screens (word patches, each checked against the clean word).

Officer lists (0x800c7740.., one cell per printf_at after a clear of the cell, vwf one-line clip):
  - the officer-name column (8 cells) centred the name by its Japanese byte count
    (x += (8 - strlen) / 2: a 2-kanji name started 2 cells in) -> left-aligned, so English
    names get the whole 64 px like the header.

Map header box 0x80052ca8(part, city): a 15-cell window, line 1 year (col 0) / month (6) / season
(11), line 2 ruler (0, name centred in 8 bytes by 0x80040b0c) / city (10). Part 0 clears the
window and draws all; parts 1-3 redraw one item over the old text, so a shorter English name
left the tail of the previous one, and a city past the edge wrapped to line 1 ("un4"). Now:
  - month at col 4 ("Month %u": "Month 10" = 45 px), season at col 11 (as JP), city at col 8, ruler name not centred;
  - each item (date line, ruler, city, the blank for no city) first clears its one-line rect
    (CLEAR_RECT), which also gives it the vwf one-line clip (ruler <= 64 px, city <= 112 px).

Duel header 0x800a5380 (15-cell window per side, sprites cut name plates from cols 0-7 / 7-14): the right
name "%8s" at col 7 ended on the window edge, and wrap_fmt breaks 8 px before the edge, so the last word
went to an invisible second line ("Zhao") -> right window 16 cells, and both names get their plate as a
one-line clip rect (names wider than 64 px are squeezed instead of running under "War").

Kana readings (officer record +9, getter 0x80040c58): the officer card (0x8001c154) and both battle
unit cards (0x800847d4, 0x80085558) print the reading "%-10s" above the name -> the format points at
the string's NUL (nothing printed). Message variable type 0xC9 (reading, 0x8004f68c) takes the name
getter 0x800409f4 instead, so namedict turns it into the English name. The sort keys keep the reading.
Portrait name labels 0x8001f1c0 (dialogue/council, a 9x2-cell window under each portrait): the
column was 3 - strlen/2 (left pane) or 5 - strlen/2 (right pane) by the Japanese byte count, so a
2-kanji name in the right pane had 40 px and "Zhang Fei" wrapped to the label's left edge ("Fei
Zhang"). label_hook (at the jal wprintf) keeps the Japanese centre (col * 8 + strlen * 4 px), centres
the measured English on it within the 64 px before the wrap edge, and clears the label row first so
vwf's one-line clip squeezes a longer name instead of wrapping it.
Letters (officer on Training, 0x80032880): the pieces are composed at 0x801bd000, copied to 0x801c1000 and
broken back by 0x80057404(src, dst, 0x20), a Japanese line breaker (a newline every 32 bytes, kinsoku), so
English split mid-word ("mar / tial"). letter_wrap (at that one jal 0x80032a44) expands the names first
(namedict expand into LETTER_TMP, staging area past the 16 KB source) and word-wraps at LETTER_W px; the
scroll pages the lines (9 per page) as before. The pieces end in a newline like the Japanese (tl/msg/26).
"""
import os, sys

sys.path.insert(0, os.path.dirname(__file__))
import asm, vwf
from kanji import put_words, word

PATCHES = [
    (0x800c77d4, 0x00431021, 0x00401021),   # addu v0, v0, v1 -> addu v0, v0, zero (officer name: no centring)
    (0x80040b64, 0x00021042, 0x00001021),   # srl v0, v0, 1 -> move v0, zero (header ruler: no centring)
    (0x80052d98, 0x24040006, 0x24040004),   # header month: col 6 -> 4
    (0x80052e2c, 0x2404000a, 0x24040008),   # header city blank: col 10 -> 8
    (0x80052e60, 0x2404000a, 0x24040008),   # header city: col 10 -> 8
    # duel header windows (0,0 / 0,2, 15 cells): the name plates show cols 0-7 (left) and 7-14 (right); width 16
    # puts the right window's wrap edge on the plate edge (with the clips in HDR_CLEARS)
    (0x800a54e8, 0x2407000f, 0x24070010),
    (0x8001c150, 0x24c623ac, 0x24c623b1),   # officer card reading: fmt "%-10s" -> its NUL
    (0x800847d0, 0x24c62fcc, 0x24c62fd1),   # unit card reading (2): same
    (0x80085554, 0x24c62fcc, 0x24c62fd1),   # unit card reading: same
    (0x8004f68c, 0x0c010316, 0x0c01027d),   # msg variable 0xC9 reading -> jal name getter 0x800409f4
    # ruler-select header 0x800c52fc: city "%4s" (right-aligned) at col 6 + ruler centred at col 15 - strlen/2
    # overlapped for long English ("Hanzhor" + "Zixu Shangren") -> city left-aligned at col 5, ruler at col 12
    (0x800c5344, 0x24a53980, 0x24a5397c),   # city fmt "%4s" -> "%s" (0x8014397c)
    (0x800c531c, 0x24420006, 0x24420005),   # city col 6 -> 5 (Shangyong 54 px ends before col 12)
    (0x800c5364, 0x00021042, 0x00001021),   # ruler: srl v0, v0, 1 -> move v0, zero (no centring)
    (0x800c5368, 0x2403000f, 0x2403000c),   # ruler col 15 -> 12 (box ends ~col 20: <= 66 px inside)
    (0x800c52a0, 0x24050006, 0x24050005),   # its clear CLEAR_RECT(ctx, 6, 0, 14, 2) -> from col 5 (a node without a
    (0x800c52ac, 0x2407000e, 0x2407000f),   # city left the city's first letters showing), width 15
    # command-window header 0x800c4c70: year "%3u" col 0, "Month%2u" col 5, ruler "%8s" right-aligned to col 18:
    # a 70 px ruler ("Zixu Shangren") ran into the month -> month at col 4 (ruler gets 75 px)
    (0x800c4cb0, 0x24420005, 0x24420004),
    # PUK scenario intro 0x8001dd34: strcpy + strcat of two pages (13080 + 2k, 13081 + 2k) into a 0x180-byte
    # stack buffer; the English pairs are 408-479 bytes -> overwrote the saved ra (crash after the intro).
    # Frame 0x1a8 -> 0x2a8: buffer 0x280 bytes.
    (0x8001dd34, 0x27bdfe58, 0x27bdfd58),   # addiu sp, sp, -0x1a8 -> -0x2a8
    (0x8001dd38, 0xafb001a0, 0xafb002a0),   # sw s0, 0x1a0(sp) -> 0x2a0
    (0x8001dd48, 0xafbf01a4, 0xafbf02a4),   # sw ra, 0x1a4(sp) -> 0x2a4
    (0x8001ddd4, 0x8fbf01a4, 0x8fbf02a4),   # lw ra
    (0x8001ddd8, 0x8fb001a0, 0x8fb002a0),   # lw s0
    (0x8001dddc, 0x27bd01a8, 0x27bd02a8),   # addiu sp, sp, 0x1a8 -> 0x2a8
    # officer summary (Register, 0x800247f8): age label "  歳" at col 6 with the number in cells 6-7 -> "yrs"
    # (no padding) at col 8
    (0x8002483c, 0x24040006, 0x24040008),
    # tournament bracket text window (set_window 0x80018f5c: left 0, top 4, w 36): right-column names start at
    # col 28 -> 56 px before the wrap edge ("Huang Zhong" wrapped "Zhong" to the left margin) -> width 38
    (0x800c0b44, 0x24070024, 0x24070026),
    (0x800c0c80, 0x24070024, 0x24070026),
]
HDR_CTX = 0x8014409f
# (jal site, its target, rect x, y, w[, keep]): clear the rect, then call the target (printf_at: return, unless
# keep: call it with the caller's a0-a3)
HDR_CLEARS = [
    (0x80052d70, 0x8005c1f0, 0, 0, 14),       # date line (before the year)
    (0x80052df4, 0x80040b0c, 0, 2, 8),        # ruler
    (0x80052e58, 0x8005dd64, 8, 2, 6),        # city
    (0x80052e38, 0x800c4188, 8, 2, 6),        # no city: printf_at of a blank -> clear only
    # duel header names: rect = the name plate -> vwf one-line clip squeezes names wider than 64 px into it
    (0x800a53e8, 0x800c4188, 0, 0, 8, True),  # left "%-8s" at col 0
    (0x800a5510, 0x800c4188, 7, 0, 8, True),  # right "%8s" at col 7 (namedict right-aligns to col 15)
]


def clear_then(at, x, y, w, target, keep=False):
    """CLEAR_RECT(HDR_CTX, x, y, w, 2), then tail-call target with the caller's a0 (keep: a0-a3)."""
    hi, lo = asm.hi, asm.lo
    return asm.assemble(at, [
        ('addiu', 'sp', 'sp', -0x20),
        ('sw', 'ra', 0x1c, 'sp'),
        ('sw', 'a0', 0x18, 'sp'),
    ] + ([('sw', 'a1', 0x14, 'sp'), ('sw', 'a2', 0x24, 'sp'), ('sw', 'a3', 0x28, 'sp')] if keep else []) + [
        ('addiu', 't0', 'zero', 2),
        ('sw', 't0', 0x10, 'sp'),
        ('lui', 'a0', hi(HDR_CTX)),
        ('addiu', 'a0', 'a0', lo(HDR_CTX)),
        ('addiu', 'a1', 'zero', x),
        ('addiu', 'a2', 'zero', y),
        ('jal', vwf.CLEAR_RECT),
        ('addiu', 'a3', 'zero', w),
        ('lw', 'a0', 0x18, 'sp'),
    ] + ([('lw', 'a1', 0x14, 'sp'), ('lw', 'a2', 0x24, 'sp'), ('lw', 'a3', 0x28, 'sp')] if keep else []) + [
        ('lw', 'ra', 0x1c, 'sp'),
        ('nop',),
    ] + ([('j', target)] if target else [('jr', 'ra')]) + [
        ('addiu', 'sp', 'sp', 0x20),
    ])


LABEL_CALL, WPRINTF = 0x8001f264, 0x80019720
GP_X, GP_Y, GP_LEFT, GP_TOP = 4, 6, 8, 0xa          # cursor col/row, window left/top (cells, gp-relative)

def label_hook(at, meas):
    """label_hook(a0 = ctx, a1 = name): re-centre the cursor on the English width, clear the label
    row (9x2, one-line clip), then tail-call wprintf. meas = namedict's meas_hook (cells + 1)."""
    return asm.assemble(at, [
        ('addiu', 'sp', 'sp', -0x28),
        ('sw', 'ra', 0x24, 'sp'),
        ('sw', 'a0', 0x20, 'sp'),
        ('sw', 'a1', 0x1c, 'sp'),
        ('addu', 't1', 'a1', 'zero'),
        ('addiu', 't0', 'zero', -1),          # strlen
        'len:',
        ('lbu', 't2', 0, 't1'),
        ('addiu', 't1', 't1', 1),
        ('bnez', 't2', 'len'),
        ('addiu', 't0', 't0', 1),
        ('lh', 'v0', GP_X, 'gp'),             # Japanese centre (px from the window left)
        ('lh', 'v1', GP_LEFT, 'gp'),
        ('sll', 't0', 't0', 2),
        ('subu', 'v0', 'v0', 'v1'),
        ('sll', 'v0', 'v0', 3),
        ('addu', 'v0', 'v0', 't0'),
        ('sw', 'v0', 0x18, 'sp'),
        ('jal', meas),                        # v0 = English cells + 1
        ('addiu', 'a0', 'sp', 0x14),
        ('addiu', 'v0', 'v0', -1),
        ('sll', 'v1', 'v0', 2),
        ('lw', 't0', 0x18, 'sp'),
        ('nop',),
        ('subu', 't0', 't0', 'v1'),
        ('addiu', 't0', 't0', 4),
        ('sra', 't0', 't0', 3),               # col = round((centre - width / 2) / 8)
        ('addiu', 't1', 'zero', 8),
        ('subu', 't1', 't1', 'v0'),           # <= 8 - width (inside the 64 px)
        ('slt', 'at', 't1', 't0'),
        ('beqz', 'at', 'cap'),
        ('nop',),
        ('addu', 't0', 't1', 'zero'),
        'cap:',
        ('bgez', 't0', 'pos'),
        ('nop',),
        ('addu', 't0', 'zero', 'zero'),
        'pos:',
        ('sw', 't0', 0x18, 'sp'),
        ('addiu', 't0', 'zero', 2),
        ('sw', 't0', 0x10, 'sp'),
        ('lw', 'a0', 0x20, 'sp'),
        ('addu', 'a1', 'zero', 'zero'),
        ('addu', 'a2', 'zero', 'zero'),
        ('jal', vwf.CLEAR_RECT),
        ('addiu', 'a3', 'zero', 9),
        ('lh', 'v0', GP_LEFT, 'gp'),
        ('lw', 't0', 0x18, 'sp'),
        ('lh', 'v1', GP_TOP, 'gp'),
        ('addu', 'v0', 'v0', 't0'),
        ('sh', 'v0', GP_X, 'gp'),
        ('sh', 'v1', GP_Y, 'gp'),
        ('lw', 'a0', 0x20, 'sp'),
        ('lw', 'a1', 0x1c, 'sp'),
        ('lw', 'ra', 0x24, 'sp'),
        ('nop',),
        ('j', WPRINTF),
        ('addiu', 'sp', 'sp', 0x28),
    ])


CARD_CALL, PRINTF_AT = 0x800be760, 0x800c4188   # tournament card name (0x800be6ac): printf_at(col, 12, name)
# battle top bar (0x800a1540.., window 36 cells): printf_at(col, 0, "%s", name) for both sides, col = 1 / 27 +
# (8 - strlen) / 2; "Yuan Shao" on the right crossed the wrap edge and "Shao" went under the left plate
BAR_CALLS = [0x800a15b8, 0x800a15dc]


def slot_name(at, meas, name_reg, slots, split):
    """In place of printf_at(a0 = col, a1 = row, a2, a3) printing a name (in name_reg) that the game centred by
    Japanese strlen in an 8-cell slot: centre the English on the same point inside slots[0] (col < split) or
    slots[1] (8 cells each), clear the slot row (one-line clip: longer names are squeezed), then printf_at."""
    return asm.assemble(at, [
        ('addiu', 'sp', 'sp', -0x38),
        ('sw', 'ra', 0x34, 'sp'),
        ('sw', 'a1', 0x30, 'sp'),
        ('sw', 'a2', 0x2c, 'sp'),
        ('sw', 'a3', 0x28, 'sp'),
        ('addu', 't1', name_reg, 'zero'),
        ('addiu', 't0', 'zero', -1),          # strlen
        'len:',
        ('lbu', 't2', 0, 't1'),
        ('addiu', 't1', 't1', 1),
        ('bnez', 't2', 'len'),
        ('addiu', 't0', 't0', 1),
        ('sll', 't0', 't0', 2),
        ('sll', 'v0', 'a0', 3),
        ('addu', 'v0', 'v0', 't0'),
        ('sw', 'v0', 0x18, 'sp'),             # Japanese centre (px)
        ('slti', 'at', 'a0', split),
        ('addiu', 'v1', 'zero', slots[1]),
        ('beqz', 'at', 'side'),
        ('nop',),
        ('addiu', 'v1', 'zero', slots[0]),
        'side:',
        ('sw', 'v1', 0x20, 'sp'),             # slot left col
        ('addu', 'a1', name_reg, 'zero'),
        ('jal', meas),                        # v0 = English cells + 1
        ('addiu', 'a0', 'sp', 0x14),
        ('addiu', 'v0', 'v0', -1),
        ('sll', 'v1', 'v0', 2),
        ('lw', 't0', 0x18, 'sp'),
        ('lw', 't2', 0x20, 'sp'),
        ('subu', 't0', 't0', 'v1'),
        ('addiu', 't0', 't0', 4),
        ('sra', 't0', 't0', 3),               # col = round((centre - width / 2) / 8)
        ('addiu', 't1', 't2', 8),
        ('subu', 't1', 't1', 'v0'),           # <= slot + 8 - width
        ('slt', 'at', 't1', 't0'),
        ('beqz', 'at', 'cap'),
        ('nop',),
        ('addu', 't0', 't1', 'zero'),
        'cap:',
        ('slt', 'at', 't0', 't2'),            # >= slot
        ('beqz', 'at', 'pos'),
        ('nop',),
        ('addu', 't0', 't2', 'zero'),
        'pos:',
        ('sw', 't0', 0x1c, 'sp'),
        ('addiu', 't0', 'zero', 2),
        ('sw', 't0', 0x10, 'sp'),
        ('lui', 'a0', asm.hi(HDR_CTX)),
        ('addiu', 'a0', 'a0', asm.lo(HDR_CTX)),
        ('addu', 'a1', 't2', 'zero'),
        ('lw', 'a2', 0x30, 'sp'),
        ('jal', vwf.CLEAR_RECT),
        ('addiu', 'a3', 'zero', 8),
        ('lw', 'a0', 0x1c, 'sp'),
        ('lw', 'a1', 0x30, 'sp'),
        ('lw', 'a2', 0x2c, 'sp'),
        ('lw', 'a3', 0x28, 'sp'),
        ('lw', 'ra', 0x34, 'sp'),
        ('nop',),
        ('j', PRINTF_AT),
        ('addiu', 'sp', 'sp', 0x38),
    ])


LETTER_CALL = 0x80032a44                 # jal 0x80057404 (line breaker) in the letter routine
LETTER_BREAK = 0x80057404
LETTER_TMP = 0x801c5000                  # staging area: source 0x801c1000 + 0x4000; read by nothing else here
LETTER_TMP_CAP = 0x4000
LETTER_W = 240                           # 32 cells = 256 px; wrap_fmt breaks 8 px before the edge


def letter_wrap(at, expand, widths):
    """letter_wrap(a0 = src, a1 = dst): names expanded, then a newline at the last space before LETTER_W px.
    Japanese (2-byte) counts 16 px; escapes 0x1A/0x1B + 1 byte (+ 1 hex digit) are copied, width 0."""
    hi, lo = asm.hi, asm.lo
    return asm.assemble(at, [
        ('addiu', 'sp', 'sp', -32),
        ('sw', 'ra', 16, 'sp'),
        ('sw', 'a1', 20, 'sp'),
        ('lui', 'a1', hi(LETTER_TMP)),
        ('addiu', 'a1', 'a1', lo(LETTER_TMP)),
        ('addiu', 'a2', 'zero', LETTER_TMP_CAP),
        ('jal', expand),
        ('addu', 'a3', 'zero', 'zero'),
        ('lw', 'a1', 20, 'sp'),
        ('lui', 't0', hi(LETTER_TMP)),
        ('addiu', 't0', 't0', lo(LETTER_TMP)),
        ('lui', 't7', hi(widths)),
        ('addiu', 't7', 't7', lo(widths)),
        ('addu', 't5', 'zero', 'zero'),      # x
        ('addu', 't6', 'zero', 'zero'),      # dst of the last space on this line (0: none)
        ('addu', 't3', 'zero', 'zero'),      # x after that space
        'loop:',
        ('lbu', 't2', 0, 't0'),
        ('addiu', 't0', 't0', 1),
        ('beqz', 't2', 'end'),
        ('addiu', 'at', 'zero', 0x0A),
        ('beq', 't2', 'at', 'nl'),
        ('sltiu', 'at', 't2', 0x1A),
        ('bnez', 'at', 'ctl'),
        ('sltiu', 'at', 't2', 0x1C),
        ('bnez', 'at', 'esc'),
        ('sltiu', 'at', 't2', 0x20),
        ('bnez', 'at', 'ctl'),
        ('sltiu', 'at', 't2', 0x7F),
        ('beqz', 'at', 'high'),
        ('addu', 'v0', 't7', 't2'),
        ('lbu', 'v0', -0x20, 'v0'),
        ('sb', 't2', 0, 'a1'),
        ('addiu', 'a1', 'a1', 1),
        ('addu', 't5', 't5', 'v0'),
        ('addiu', 'at', 'zero', 0x20),
        ('bne', 't2', 'at', 'chk'),
        ('nop',),
        ('addiu', 't6', 'a1', -1),
        ('b', 'loop'),
        ('addu', 't3', 't5', 'zero'),
        'high:',                             # 0xA0-0xDF one byte (8 px), else a 2-byte character (16 px)
        ('addiu', 'at', 't2', -0xA0),
        ('sltiu', 'at', 'at', 0x40),
        ('beqz', 'at', 'wide'),
        ('sb', 't2', 0, 'a1'),
        ('addiu', 'a1', 'a1', 1),
        ('b', 'chk'),
        ('addiu', 't5', 't5', 8),
        'wide:',
        ('lbu', 'v1', 0, 't0'),
        ('nop',),
        ('beqz', 'v1', 'end'),
        ('addiu', 'a1', 'a1', 1),
        ('sb', 'v1', 0, 'a1'),
        ('addiu', 'a1', 'a1', 1),
        ('addiu', 't0', 't0', 1),
        ('addiu', 't5', 't5', 16),
        'chk:',
        ('slti', 'at', 't5', LETTER_W + 1),
        ('bnez', 'at', 'loop'),
        ('nop',),
        ('beqz', 't6', 'loop'),
        ('addiu', 'at', 'zero', 0x0A),
        ('sb', 'at', 0, 't6'),
        ('subu', 't5', 't5', 't3'),
        ('b', 'loop'),
        ('addu', 't6', 'zero', 'zero'),
        'nl:',
        ('sb', 't2', 0, 'a1'),
        ('addiu', 'a1', 'a1', 1),
        ('addu', 't5', 'zero', 'zero'),
        ('b', 'loop'),
        ('addu', 't6', 'zero', 'zero'),
        'esc:',                              # 0x1A/0x1B, its byte, and one hex digit if present
        ('sb', 't2', 0, 'a1'),
        ('lbu', 'v1', 0, 't0'),
        ('nop',),
        ('beqz', 'v1', 'end'),
        ('addiu', 'a1', 'a1', 1),
        ('sb', 'v1', 0, 'a1'),
        ('addiu', 'a1', 'a1', 1),
        ('lbu', 'v1', 1, 't0'),
        ('addiu', 't0', 't0', 1),
        ('addiu', 'at', 'v1', -0x30),
        ('sltiu', 'at', 'at', 10),
        ('bnez', 'at', 'hex'),
        ('addiu', 'at', 'v1', -0x41),
        ('sltiu', 'at', 'at', 6),
        ('beqz', 'at', 'loop'),
        ('nop',),
        'hex:',
        ('sb', 'v1', 0, 'a1'),
        ('addiu', 'a1', 'a1', 1),
        ('b', 'loop'),
        ('addiu', 't0', 't0', 1),
        'ctl:',
        ('sb', 't2', 0, 'a1'),
        ('b', 'loop'),
        ('addiu', 'a1', 'a1', 1),
        'end:',
        ('sb', 'zero', 0, 'a1'),
        ('lw', 'ra', 16, 'sp'),
        ('nop',),
        ('jr', 'ra'),
        ('addiu', 'sp', 'sp', 32),
    ])


def apply_labels(exe, heap, meas, expand, widths):
    """After namedict.apply (meas = its meas_hook, expand = its expand, widths = vwf's)."""
    assert word(exe, LABEL_CALL) == 0x0c000000 | WPRINTF >> 2 & 0x3FFFFFF
    vwf.asm_put(exe, LABEL_CALL, [('jal', heap.code(label_hook, meas))])
    # tournament card (window 22 cells, result marks W/L/D at cols 0 and 20, drawn after; game: col 0 / 12 +
    # (8 - n) / 2): slots 1-8 / 11-18 (a name ending on col 20 touched the mark)
    assert word(exe, CARD_CALL) == 0x0c000000 | PRINTF_AT >> 2 & 0x3FFFFFF
    vwf.asm_put(exe, CARD_CALL, [('jal', heap.code(slot_name, meas, 'a2', (1, 11), 10))])
    bar = heap.code(slot_name, meas, 'a3', (1, 27), 14)
    for site in BAR_CALLS:
        assert word(exe, site) == 0x0c000000 | PRINTF_AT >> 2 & 0x3FFFFFF
        vwf.asm_put(exe, site, [('jal', bar)])
    assert word(exe, LETTER_CALL) == 0x0c000000 | LETTER_BREAK >> 2 & 0x3FFFFFF
    vwf.asm_put(exe, LETTER_CALL, [('jal', heap.code(letter_wrap, expand, widths))])


LIST_PRINT, MOR_TYPE = 0x800c79e0, 11     # column types 11 (morale) and 12 (formation)

def form_col(at):
    """Officer lists: the morale and formation columns (types 11-12, s2) print one cell left (formation: at its
    clear rect's start) so "Crescent" fits before the window edge. a0 = col; tail-call printf_at."""
    return asm.assemble(at, [
        ('addiu', 't0', 's2', -MOR_TYPE),
        ('sltiu', 't0', 't0', 2),
        ('beqz', 't0', 'go'),
        ('nop',),
        ('addiu', 'a0', 'a0', -1),
        'go:',
        ('j', 0x800c4188),
        ('nop',),
    ])


def apply(exe, heap):
    assert word(exe, LIST_PRINT) == 0x0c000000 | 0x800c4188 >> 2 & 0x3FFFFFF
    vwf.asm_put(exe, LIST_PRINT, [('jal', heap.code(form_col))])
    for va, old, new in PATCHES:
        assert word(exe, va) == old, hex(va)
        put_words(exe, va, new)
    for site, target, x, y, w, *keep in HDR_CLEARS:
        assert word(exe, site) == 0x0c000000 | target >> 2 & 0x3FFFFFF, hex(site)
        hook = heap.code(clear_then, x, y, w, target if keep or target != 0x800c4188 else None, bool(keep))
        vwf.asm_put(exe, site, [('jal', hook)])
