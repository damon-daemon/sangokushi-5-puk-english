"""Proportional Latin font in the text renderer (notes/ram.md §3, notes/engine.md "VWF").

The game draws text into a 4bpp texture in VRAM (x = 0x380 halfwords on): draw_cell
expands a glyph to two 4bpp buffers (shadow colour, main colour) and blits the shadow
opaque one row lower, then the main colour transparent, at a position in 8-px cells;
the blits (LoadImage, or StoreImage + byte merge + LoadImage) work in whole VRAM
halfwords, i.e. 4-px steps. This patch
  - replaces both blits with blit4 (any pixel x: StoreImage the covering halfwords,
    merge nibble by nibble, LoadImage; opaque = every nibble, transparent = ink only);
    a halfword-aligned opaque blit stays a plain LoadImage;
  - makes draw_cell take a pixel x (its 3 other callers pass cells: scaled by 8 there);
    the direct-to-VRAM mode (ctx mode 0x800: ClearImage of the cell + main pass) becomes
    one opaque blit of the main buffer;
  - replaces the half-width font (0x20-0x5F, no lower case) with ' '..'~' from
    tools/pixfont.py and adds an advance width table;
  - putc's two draw sites (2-byte and single-byte) call vwf_cell: it keeps a pixel x
    next to the game's cell cursor (gp+4): while the cursor still is where vwf_cell left
    it, drawing continues at the pixel x; once anything else moved it (newline, tab,
    window, locate) the pixel x restarts at cursor * 8. After drawing it advances by the
    glyph width (2-byte 16, ASCII its width, other bytes 8) and sets the cursor to the
    next whole cell, so the game's wrap and layout logic keep working in cells;
  - word wrap (wrap_fmt): window text (dialogue, reports) is drawn by the printf-like
    formatter 0x80015008 (format = the expanded message) through wprintf 0x80019720 and
    printf_at 0x800c4188; their formatter call goes through wrap_fmt, which copies the
    format into a buffer and turns a space into a newline when the next word would cross
    the window's right edge ((gp+8 + gp+0xc) cells * 8 px - 8), starting from the vwf pixel x.
    ESC sequences (0x1A/0x1B + letter, + 1 byte after C/B/M) have no width; 2-byte codes
    16 px, other bytes >= 0x7F 8 px. Formats of WRAP_CAP bytes or more are passed as they are;
  - window size (measure): the windows that size themselves to their text call a measure
    0x800575cc(&lines, str) -> longest line in cells (bytes), lines = line count. Its 4 call
    sites call a pixel version instead: the same word wrap at MEASURE_W px from x = 0,
    returns ceil(longest line / 8) + 1 and the wrapped line count. The + 1 cell: putc's own
    cell wrap (cursor = ceil(px / 8), new line when cursor + advance > left + width) needs a
    glyph to start 8 px before the edge, so wrap_fmt wraps at the window edge - 8 px; that
    edge is >= every measured line and <= MEASURE_W, so both break at the same spaces;
  - column tabs (tools/namedict.py expand): TAB 0x1F, TAB_HI + (px >> 5), TAB_LO + (px & 31) ->
    x = max(x + space, px); vwf_cell takes the 3 bytes over 3 calls (state S_TAB, S_TABHI) and draws
    nothing, wrap_fmt and measure count them the same way (measure's px are from x = 0).
"""
import os, struct, sys

sys.path.insert(0, os.path.dirname(__file__))
import asm, pixfont
from kanji import put, put_words, word

ASCII = list(range(0x20, 0x7F))       # 95 codes
LOADIMAGE, STOREIMAGE, DRAWSYNC = 0x800e0fdc, 0x800e103c, 0x800e0d30
BLIT_OPAQUE, BLIT_TRANS = 0x800189f0, 0x80018a48
DRAW_CELL = 0x80019160
FONT_RANGE, FONT_HI, FONT_LO = 0x80018d00, 0x80018d20, 0x80018d24   # half-width expander
DC_SHADOW_X = 0x80019278              # sll a1, s5, 3 / addiu a1, a1, 1
DC_VRAM, DC_VRAM_END = 0x800192bc, 0x8001931c   # mode 0x800: ClearImage + DrawSync
DC_MAIN_X = 0x80019320                # sll a1, s5, 0x13
DC_EXIT = 0x80019350                  # sw zero, 0x14(gp)
CELL_CALLERS = [0x80019138, 0x800294ac, 0x800298c4]   # sll a1, a1, 0x10 -> 0x13 (x8)
PUTC_WIDE, PUTC_WIDE_ADV = 0x800194d4, 0x800194dc   # jal draw_cell; x += 2 (4 words)
PUTC_ONE, PUTC_ONE_ADV = 0x80019624, 0x8001962c     # jal draw_cell; x += 1 (3 words, shared sh)
PUTC_EXIT = 0x8001963c
GP_X, GP_Y, GP_LEFT, GP_TOP, GP_WIDTH = 4, 6, 8, 0xa, 0xc
CLEAR_RECT = 0x80018f94               # (ctx, x, y, w, h) in cells: ClearImage of a text cell
# shared state (s16): vwf_cell col, row, px; clip squeeze; last cleared rect; column tab
S_D, S_LEFT, S_ACC, S_N, S_RX, S_RY, S_RW, S_RH, S_JW = 6, 8, 10, 12, 14, 16, 18, 20, 22
S_TAB, S_TABHI = 24, 26
TAB, TAB_HI, TAB_LO = 0x1F, 0x30, 0x40
FORMAT = 0x80015008                   # formatter(callback, fmt, args)
FORMAT_CALLS = [0x80019744, 0x800c41d0]   # wprintf, printf_at
WRAP_CAP = 768                        # names (tools/namedict.py) make the text longer
MEASURE = 0x800575cc
MEASURE_CALLS = [0x8001e400, 0x80022998, 0x8007a108, 0x800c5b90]
MEASURE_W = 256


def blit4(at, tmp):
    """blit_o / blit_t (a0 = px, a1 = py, a2 = w, a3 = h, 0x10(sp) = src 4bpp, pitch w)."""
    hi, lo = asm.hi, asm.lo
    return asm.assemble(at, [
        ('b', 'common'),                     # blit_t = at
        ('addiu', 't0', 'zero', 1),
        ('addu', 't0', 'zero', 'zero'),      # blit_o = at + 8
        'common:',
        ('addiu', 'sp', 'sp', -0x40),
        ('sw', 'ra', 0x38, 'sp'),
        ('sw', 's0', 0x20, 'sp'),
        ('sw', 's1', 0x24, 'sp'),
        ('sw', 's2', 0x28, 'sp'),
        ('sw', 's3', 0x2c, 'sp'),
        ('sw', 's4', 0x30, 'sp'),
        ('sw', 's5', 0x34, 'sp'),
        ('lw', 's5', 0x50, 'sp'),
        ('addu', 's2', 'a2', 'zero'),
        ('addu', 's3', 'a3', 'zero'),
        ('addu', 's4', 't0', 'zero'),
        ('andi', 's1', 'a0', 3),
        ('subu', 'v0', 'a0', 's1'),
        ('sra', 'v0', 'v0', 2),
        ('addiu', 'v0', 'v0', 0x380),
        ('sh', 'v0', 0x18, 'sp'),
        ('sh', 'a1', 0x1a, 'sp'),
        ('addu', 'v1', 's1', 'a2'),
        ('addiu', 'v1', 'v1', 3),
        ('sra', 'v1', 'v1', 2),
        ('sh', 'v1', 0x1c, 'sp'),
        ('sh', 'a3', 0x1e, 'sp'),
        ('sll', 's0', 'v1', 2),              # covered width in pixels (dst pitch)
        ('andi', 't1', 'a2', 3),
        ('or', 'v0', 's1', 's4'),
        ('or', 'v0', 'v0', 't1'),
        ('bnez', 'v0', 'slow'),
        ('addiu', 'a0', 'sp', 0x18),
        ('jal', LOADIMAGE),
        ('addu', 'a1', 's5', 'zero'),
        ('b', 'done'),
        ('nop',),
        'slow:',
        ('lui', 'a1', hi(tmp)),
        ('jal', STOREIMAGE),
        ('addiu', 'a1', 'a1', lo(tmp)),
        ('jal', DRAWSYNC),
        ('addu', 'a0', 'zero', 'zero'),
        ('blez', 's3', 'load'),
        ('addu', 't5', 'zero', 'zero'),      # src pixel index
        ('blez', 's2', 'load'),
        ('addu', 't6', 's1', 'zero'),        # dst index of the row's first pixel
        ('addu', 't4', 's3', 'zero'),        # rows left
        'row:',
        ('addu', 't7', 'zero', 'zero'),      # column
        'pix:',
        ('srl', 't0', 't5', 1),
        ('addu', 't0', 't0', 's5'),
        ('lbu', 't1', 0, 't0'),
        ('andi', 't2', 't5', 1),
        ('sll', 't2', 't2', 2),
        ('srlv', 't1', 't1', 't2'),
        ('andi', 't1', 't1', 0xf),
        ('bnez', 't1', 'set'),
        ('nop',),
        ('bnez', 's4', 'next'),
        ('nop',),
        'set:',
        ('addu', 't3', 't6', 't7'),
        ('srl', 't0', 't3', 1),
        ('lui', 'at', hi(tmp)),
        ('addu', 't0', 't0', 'at'),
        ('lbu', 't2', lo(tmp), 't0'),
        ('andi', 't3', 't3', 1),
        ('sll', 't3', 't3', 2),
        ('addiu', 't9', 'zero', 0xf),
        ('sllv', 't9', 't9', 't3'),
        ('nor', 't9', 't9', 'zero'),
        ('and', 't2', 't2', 't9'),
        ('sllv', 't1', 't1', 't3'),
        ('or', 't2', 't2', 't1'),
        ('sb', 't2', lo(tmp), 't0'),
        'next:',
        ('addiu', 't5', 't5', 1),
        ('addiu', 't7', 't7', 1),
        ('bne', 't7', 's2', 'pix'),
        ('nop',),
        ('addiu', 't4', 't4', -1),
        ('bnez', 't4', 'row'),
        ('addu', 't6', 't6', 's0'),
        'load:',
        ('addiu', 'a0', 'sp', 0x18),
        ('lui', 'a1', hi(tmp)),
        ('jal', LOADIMAGE),
        ('addiu', 'a1', 'a1', lo(tmp)),
        'done:',
        ('jal', DRAWSYNC),
        ('addu', 'a0', 'zero', 'zero'),
        ('lw', 'ra', 0x38, 'sp'),
        ('lw', 's0', 0x20, 'sp'),
        ('lw', 's1', 0x24, 'sp'),
        ('lw', 's2', 0x28, 'sp'),
        ('lw', 's3', 0x2c, 'sp'),
        ('lw', 's4', 0x30, 'sp'),
        ('lw', 's5', 0x34, 'sp'),
        ('jr', 'ra'),
        ('addiu', 'sp', 'sp', 0x40),
    ])


def vwf_cell(at, state, widths):
    """vwf_cell(a0 = ctx, a1 = cursor col, a2 = row, a3 = code); state = s16 col, row, px."""
    hi, lo = asm.hi, asm.lo
    return asm.assemble(at, [
        ('addiu', 'sp', 'sp', -0x20),
        ('sw', 'ra', 0x18, 'sp'),
        ('sw', 's0', 0x10, 'sp'),
        ('sw', 's1', 0x14, 'sp'),
        ('lui', 't0', hi(state)),
        ('lh', 't1', lo(state), 't0'),
        ('lh', 't2', lo(state + 2), 't0'),
        ('lh', 's1', lo(state + 4), 't0'),
        ('bne', 't1', 'a1', 'reset'),
        ('nop',),
        ('beq', 't2', 'a2', 'keep'),
        ('nop',),
        'reset:',
        ('sll', 's1', 'a1', 3),
        'keep:',
        ('lh', 't3', lo(state + S_TAB), 't0'),
        ('addiu', 'at', 'zero', TAB),
        ('bnez', 't3', 'tabarg'),
        ('nop',),
        ('bne', 'a3', 'at', 'notab'),
        ('addiu', 't3', 'zero', 1),
        ('b', 'nodraw'),
        ('sh', 't3', lo(state + S_TAB), 't0'),
        'tabarg:',                           # 1: high byte; 2: low byte -> x = max(x + space, px)
        ('addiu', 'at', 'zero', 1),
        ('bne', 't3', 'at', 'tab2'),
        ('addiu', 't3', 'zero', 2),
        ('sh', 't3', lo(state + S_TAB), 't0'),
        ('b', 'nodraw'),
        ('sh', 'a3', lo(state + S_TABHI), 't0'),
        'tab2:',
        ('lh', 't4', lo(state + S_TABHI), 't0'),
        ('sh', 'zero', lo(state + S_TAB), 't0'),
        ('addiu', 't4', 't4', -TAB_HI),
        ('sll', 't4', 't4', 5),
        ('addiu', 't5', 'a3', -TAB_LO),
        ('addu', 't4', 't4', 't5'),
        ('addiu', 's1', 's1', pixfont.SPACE_ADV),
        ('slt', 'at', 's1', 't4'),
        ('beqz', 'at', 'nodraw'),
        ('nop',),
        ('b', 'nodraw'),
        ('addu', 's1', 't4', 'zero'),
        'notab:',
        ('sltiu', 'at', 'a3', 0x100),
        ('beqz', 'at', 'wide'),
        ('addiu', 't1', 'a3', -0x20),
        ('sltiu', 'at', 't1', len(ASCII)),
        ('beqz', 'at', 'narrow'),
        ('lui', 't2', hi(widths)),
        ('addu', 't2', 't2', 't1'),
        ('lbu', 's0', lo(widths), 't2'),
        ('b', 'go'),
        ('nop',),
        'wide:',
        ('b', 'go'),
        ('addiu', 's0', 'zero', 16),
        'narrow:',
        ('addiu', 's0', 'zero', 8),
        'go:',                               # clipped cell text (wrap_fmt): 1 px closer at d of N gaps
        ('lui', 't0', hi(state)),
        ('lh', 't1', lo(state + S_LEFT), 't0'),
        ('lh', 't2', lo(state + S_D), 't0'),
        ('blez', 't1', 'draw'),
        ('lh', 't3', lo(state + S_ACC), 't0'),
        ('lh', 't4', lo(state + S_N), 't0'),
        ('addiu', 't1', 't1', -1),
        ('sh', 't1', lo(state + S_LEFT), 't0'),
        ('addu', 't3', 't3', 't2'),
        ('slt', 'at', 't3', 't4'),
        ('bnez', 'at', 'draw'),
        ('sh', 't3', lo(state + S_ACC), 't0'),
        ('subu', 't3', 't3', 't4'),
        ('sh', 't3', lo(state + S_ACC), 't0'),
        ('addiu', 's0', 's0', -1),
        'draw:',
        ('jal', DRAW_CELL),
        ('addu', 'a1', 's1', 'zero'),
        ('addu', 's1', 's1', 's0'),
        'nodraw:',
        ('addiu', 'v0', 's1', 7),
        ('sra', 'v0', 'v0', 3),
        ('sh', 'v0', GP_X, 'gp'),
        ('lui', 't0', hi(state)),
        ('sh', 'v0', lo(state), 't0'),
        ('lh', 'v1', GP_Y, 'gp'),
        ('sh', 's1', lo(state + 4), 't0'),
        ('sh', 'v1', lo(state + 2), 't0'),
        ('lw', 'ra', 0x18, 'sp'),
        ('lw', 's0', 0x10, 'sp'),
        ('lw', 's1', 0x14, 'sp'),
        ('jr', 'ra'),
        ('addiu', 'sp', 'sp', 0x20),
    ])


def wrap_fmt(at, state, widths, buf):
    """In place of jal FORMAT (a0 = callback, a1 = format, a2 = args): a1 = wrapped copy."""
    hi, lo = asm.hi, asm.lo
    return asm.assemble(at, [
        ('addu', 't0', 'a1', 'zero'),
        ('addiu', 't1', 'zero', WRAP_CAP),
        'len:',
        ('lbu', 't2', 0, 't0'),
        ('addiu', 't1', 't1', -1),
        ('beqz', 't2', 'fits'),
        ('addiu', 't0', 't0', 1),
        ('bnez', 't1', 'len'),
        ('nop',),
        ('j', FORMAT),                       # too long: as it is
        ('nop',),
        'fits:',
        ('lui', 't0', hi(state)),
        ('lh', 't1', lo(state), 't0'),
        ('lh', 't2', lo(state + 2), 't0'),
        ('lh', 't3', GP_X, 'gp'),
        ('lh', 't4', GP_Y, 'gp'),
        ('lh', 't5', lo(state + 4), 't0'),   # x (px)
        ('bne', 't1', 't3', 'reset'),
        ('nop',),
        ('beq', 't2', 't4', 'keep'),
        ('nop',),
        'reset:',
        ('sll', 't5', 't3', 3),
        'keep:',
        ('sh', 'zero', lo(state + S_LEFT), 't0'),
        ('lh', 'v0', lo(state + S_RH), 't0'),   # text starting inside the last cleared one-line rect
        ('lh', 'v1', lo(state + S_RY), 't0'),
        ('addiu', 'at', 'v0', -1),
        ('sltiu', 'at', 'at', 2),
        ('beqz', 'at', 'noclip'),
        ('slt', 'at', 't4', 'v1'),
        ('bnez', 'at', 'drop'),
        ('addu', 'v1', 'v1', 'v0'),
        ('slt', 'at', 't4', 'v1'),
        ('beqz', 'at', 'drop'),
        ('nop',),
        ('lh', 'v1', lo(state + S_RX), 't0'),
        ('lh', 'v0', lo(state + S_RW), 't0'),
        ('slt', 'at', 't3', 'v1'),
        ('bnez', 'at', 'drop'),
        ('addu', 'v1', 'v1', 'v0'),
        ('addiu', 'at', 'v1', 2),            # or just past the edge (a clipped text before it)
        ('slt', 'at', 't3', 'at'),
        ('bnez', 'at', 'clip'),
        ('sll', 't6', 'v1', 3),              # clip edge (px)
        'drop:',                             # text elsewhere: forget the rect
        ('b', 'noclip'),
        ('sh', 'zero', lo(state + S_RH), 't0'),
        'clip:',                             # edge = max(rect, x + Japanese width), <= window edge - 8
        ('lh', 'v0', lo(state + S_JW), 't0'),
        ('lh', 'v1', GP_LEFT, 'gp'),
        ('lh', 't3', GP_WIDTH, 'gp'),
        ('sll', 'v0', 'v0', 3),
        ('addu', 'v0', 'v0', 't5'),
        ('slt', 'at', 't6', 'v0'),
        ('beqz', 'at', 'jwok'),
        ('nop',),
        ('addu', 't6', 'v0', 'zero'),
        'jwok:',
        ('addu', 'v1', 'v1', 't3'),
        ('addiu', 'v1', 'v1', -1),
        ('sll', 'v1', 'v1', 3),
        ('slt', 'at', 'v1', 't6'),
        ('bnez', 'at', 'jwcap'),
        ('nop',),
        ('addu', 'v1', 't6', 'zero'),
        'jwcap:',
        ('addu', 't6', 'v1', 'zero'),
        ('lui', 't7', hi(widths)),
        ('addiu', 't7', 't7', lo(widths)),
        ('addu', 't1', 'a1', 'zero'),
        ('addu', 't8', 'zero', 'zero'),      # glyphs
        ('addu', 't9', 't5', 'zero'),        # end x
        ('addu', 't3', 't5', 'zero'),        # t3, t4: end x, glyphs up to the last non-space
        ('addu', 't4', 'zero', 'zero'),
        'cnsp:',
        ('addu', 't3', 't9', 'zero'),
        ('addu', 't4', 't8', 'zero'),
        'cs:',
        ('lbu', 't2', 0, 't1'),
        ('addiu', 't1', 't1', 1),
        ('beqz', 't2', 'cend'),
        ('sltiu', 'at', 't2', 0x20),
        ('bnez', 'at', 'noclip'),            # control bytes, '%': drawn as they are
        ('addiu', 'at', 'zero', 0x25),
        ('beq', 't2', 'at', 'noclip'),
        ('addiu', 't8', 't8', 1),
        ('sltiu', 'at', 't2', 0x7F),
        ('beqz', 'at', 'chigh'),
        ('addu', 'v0', 't7', 't2'),
        ('lbu', 'v0', -0x20, 'v0'),
        ('addiu', 'at', 'zero', 0x20),
        ('bne', 't2', 'at', 'cnsp'),
        ('addu', 't9', 't9', 'v0'),
        ('b', 'cs'),
        ('nop',),
        'chigh:',
        ('sltiu', 'at', 't2', 0xA0),
        ('bnez', 'at', 'clead'),
        ('sltiu', 'at', 't2', 0xE0),
        ('beqz', 'at', 'clead'),
        ('nop',),
        ('b', 'cnsp'),
        ('addiu', 't9', 't9', 8),
        'clead:',
        ('lbu', 'v0', 0, 't1'),
        ('addiu', 't1', 't1', 1),
        ('beqz', 'v0', 'cend'),
        ('nop',),
        ('b', 'cnsp'),
        ('addiu', 't9', 't9', 16),
        'cend:',                             # overflow d = end - edge; N = glyphs - 1 gaps (no trailing spaces)
        ('subu', 'v0', 't3', 't6'),
        ('blez', 'v0', 'noclip'),
        ('addiu', 'v1', 't4', -1),
        ('blez', 'v1', 'cut'),
        ('slt', 'at', 'v1', 'v0'),
        ('bnez', 'at', 'cut'),
        ('nop',),
        'sq:',
        ('sh', 'v0', lo(state + S_D), 't0'),
        ('sh', 'v1', lo(state + S_LEFT), 't0'),
        ('sra', 'at', 'v1', 1),
        ('sh', 'at', lo(state + S_ACC), 't0'),
        ('j', FORMAT),
        ('sh', 'v1', lo(state + S_N), 't0'),
        'cut:',                              # every gap 1 px closer; drop the glyphs past the edge
        ('lui', 't9', hi(buf)),
        ('addiu', 't9', 't9', lo(buf)),
        ('addu', 't1', 'a1', 'zero'),
        ('addu', 't3', 't9', 'zero'),
        ('addu', 't8', 'zero', 'zero'),
        'cc:',
        ('lbu', 't2', 0, 't1'),
        ('nop',),
        ('beqz', 't2', 'cdone'),
        ('sltiu', 'at', 't2', 0x7F),
        ('beqz', 'at', 'cch'),
        ('addiu', 'v1', 'zero', 1),
        ('addu', 'v0', 't7', 't2'),
        ('lbu', 'v0', -0x20, 'v0'),
        ('b', 'cfit'),
        ('nop',),
        'cch:',
        ('sltiu', 'at', 't2', 0xA0),
        ('bnez', 'at', 'ccl'),
        ('sltiu', 'at', 't2', 0xE0),
        ('beqz', 'at', 'ccl'),
        ('nop',),
        ('b', 'cfit'),
        ('addiu', 'v0', 'zero', 8),
        'ccl:',
        ('addiu', 'v1', 'zero', 2),
        ('addiu', 'v0', 'zero', 16),
        'cfit:',                             # ink ends at x + width - 1
        ('addu', 'at', 't5', 'v0'),
        ('addiu', 'at', 'at', -1),
        ('slt', 'at', 't6', 'at'),
        ('bnez', 'at', 'cdone'),
        ('sb', 't2', 0, 't3'),
        ('addiu', 'at', 'zero', 1),
        ('beq', 'v1', 'at', 'c1'),
        ('nop',),
        ('lbu', 't4', 1, 't1'),
        ('nop',),
        ('beqz', 't4', 'cdone'),
        ('sb', 't4', 1, 't3'),
        'c1:',
        ('addu', 't1', 't1', 'v1'),
        ('addu', 't3', 't3', 'v1'),
        ('addiu', 't8', 't8', 1),
        ('addu', 't5', 't5', 'v0'),
        ('b', 'cc'),
        ('addiu', 't5', 't5', -1),
        'cdone:',
        ('sb', 'zero', 0, 't3'),
        ('addiu', 'v1', 't8', -1),
        ('addu', 'a1', 't9', 'zero'),
        ('bgtz', 'v1', 'sq'),
        ('addu', 'v0', 'v1', 'zero'),
        ('j', FORMAT),
        ('nop',),
        'noclip:',
        ('lh', 't6', GP_LEFT, 'gp'),
        ('lh', 't8', GP_WIDTH, 'gp'),
        ('sll', 't6', 't6', 3),              # left margin (px)
        ('sll', 't8', 't8', 3),
        ('addu', 't8', 't8', 't6'),
        ('addiu', 't8', 't8', -8),           # right edge (px) - 8: putc's cell check (see measure)
        ('lui', 't7', hi(widths)),
        ('addiu', 't7', 't7', lo(widths)),
        ('lui', 't9', hi(buf)),
        ('addiu', 't9', 't9', lo(buf)),
        ('addu', 't0', 'a1', 'zero'),        # src
        ('addu', 't1', 't9', 'zero'),        # dst
        'loop:',
        ('lbu', 't2', 0, 't0'),
        ('addiu', 't0', 't0', 1),
        ('sb', 't2', 0, 't1'),
        ('beqz', 't2', 'done'),
        ('addiu', 't1', 't1', 1),
        ('addiu', 'at', 'zero', 0x20),
        ('beq', 't2', 'at', 'space'),
        ('addiu', 'at', 'zero', 0x0A),
        ('beq', 't2', 'at', 'nl'),
        ('addiu', 'at', 'zero', 0x0D),
        ('beq', 't2', 'at', 'nl'),
        ('addiu', 'at', 'zero', TAB),
        ('beq', 't2', 'at', 'tab'),
        ('sltiu', 'at', 't2', 0x1A),
        ('bnez', 'at', 'loop'),              # other control bytes: no width
        ('sltiu', 'at', 't2', 0x1C),
        ('bnez', 'at', 'esc'),
        ('sltiu', 'at', 't2', 0x7F),
        ('beqz', 'at', 'high'),
        ('addu', 'v0', 't7', 't2'),
        ('lbu', 'v0', -0x20, 'v0'),
        ('b', 'loop'),
        ('addu', 't5', 't5', 'v0'),
        'nl:',
        ('b', 'loop'),
        ('addu', 't5', 't6', 'zero'),
        'tab:',                              # column tab: x = max(x + space, px)
        ('lbu', 'v0', 0, 't0'),
        ('addiu', 't0', 't0', 1),
        ('sb', 'v0', 0, 't1'),
        ('beqz', 'v0', 'done'),
        ('addiu', 't1', 't1', 1),
        ('lbu', 'v1', 0, 't0'),
        ('addiu', 't0', 't0', 1),
        ('sb', 'v1', 0, 't1'),
        ('beqz', 'v1', 'done'),
        ('addiu', 't1', 't1', 1),
        ('addiu', 'v0', 'v0', -TAB_HI),
        ('sll', 'v0', 'v0', 5),
        ('addiu', 'v1', 'v1', -TAB_LO),
        ('addu', 'v0', 'v0', 'v1'),
        ('lbu', 'v1', 0, 't7'),
        ('nop',),
        ('addu', 't5', 't5', 'v1'),
        ('slt', 'at', 't5', 'v0'),
        ('beqz', 'at', 'loop'),
        ('nop',),
        ('b', 'loop'),
        ('addu', 't5', 'v0', 'zero'),
        'high:',
        ('sltiu', 'at', 't2', 0xA0),
        ('bnez', 'at', 'lead'),
        ('sltiu', 'at', 't2', 0xE0),
        ('beqz', 'at', 'lead'),
        ('nop',),
        ('b', 'loop'),
        ('addiu', 't5', 't5', 8),
        'lead:',
        ('lbu', 'v0', 0, 't0'),
        ('addiu', 't0', 't0', 1),
        ('sb', 'v0', 0, 't1'),
        ('beqz', 'v0', 'done'),
        ('addiu', 't1', 't1', 1),
        ('b', 'loop'),
        ('addiu', 't5', 't5', 16),
        'esc:',
        ('lbu', 'v0', 0, 't0'),
        ('addiu', 't0', 't0', 1),
        ('sb', 'v0', 0, 't1'),
        ('beqz', 'v0', 'done'),
        ('addiu', 't1', 't1', 1),
        ('addiu', 'at', 'zero', 0x57),       # ESC W: no argument
        ('beq', 'v0', 'at', 'loop'),
        ('nop',),
        ('lbu', 'v0', 0, 't0'),
        ('addiu', 't0', 't0', 1),
        ('sb', 'v0', 0, 't1'),
        ('beqz', 'v0', 'done'),
        ('addiu', 't1', 't1', 1),
        ('b', 'loop'),
        ('nop',),
        'space:',                            # t4 = x + space + next word
        ('lbu', 'v0', 0, 't7'),
        ('addu', 't3', 't0', 'zero'),
        ('addu', 't4', 't5', 'v0'),
        'wl:',
        ('lbu', 'v0', 0, 't3'),
        ('addiu', 't3', 't3', 1),
        ('sltiu', 'at', 'v0', 0x21),
        ('bnez', 'at', 'wend'),
        ('sltiu', 'at', 'v0', 0x7F),
        ('beqz', 'at', 'whigh'),
        ('addu', 'v1', 't7', 'v0'),
        ('lbu', 'v1', -0x20, 'v1'),
        ('b', 'wl'),
        ('addu', 't4', 't4', 'v1'),
        'whigh:',
        ('sltiu', 'at', 'v0', 0xA0),
        ('bnez', 'at', 'wlead'),
        ('sltiu', 'at', 'v0', 0xE0),
        ('beqz', 'at', 'wlead'),
        ('nop',),
        ('b', 'wl'),
        ('addiu', 't4', 't4', 8),
        'wlead:',
        ('addiu', 't3', 't3', 1),
        ('b', 'wl'),
        ('addiu', 't4', 't4', 16),
        'wend:',
        ('addiu', 'at', 'zero', 0x1A),
        ('beq', 'v0', 'at', 'wesc'),
        ('addiu', 'at', 'zero', 0x1B),
        ('beq', 'v0', 'at', 'wesc'),
        ('nop',),
        'wcmp:',
        ('slt', 'at', 't8', 't4'),
        ('beqz', 'at', 'nowrap'),
        ('addiu', 'at', 'zero', 0x0A),
        ('sb', 'at', -1, 't1'),
        ('b', 'loop'),
        ('addu', 't5', 't6', 'zero'),
        'nowrap:',
        ('lbu', 'v0', 0, 't7'),
        ('b', 'loop'),
        ('addu', 't5', 't5', 'v0'),
        'wesc:',
        ('lbu', 'v0', 0, 't3'),
        ('addiu', 't3', 't3', 1),
        ('beqz', 'v0', 'wcmp'),
        ('addiu', 'at', 'zero', 0x57),
        ('beq', 'v0', 'at', 'wl'),
        ('nop',),
        ('b', 'wl'),
        ('addiu', 't3', 't3', 1),
        'done:',
        ('j', FORMAT),
        ('addu', 'a1', 't9', 'zero'),
    ])


def rect_hook(at, state):
    """At CLEAR_RECT(ctx, x, y, w, h on the stack) in cells relative to the window: remember the
    rect in absolute cells (wrap_fmt clips one text starting inside a one-line rect to it)."""
    hi, lo = asm.hi, asm.lo
    return asm.assemble(at, [
        ('lui', 't0', hi(state)),
        ('lhu', 't1', GP_LEFT, 'gp'),
        ('andi', 't2', 'a1', 0xFFFF),
        ('addu', 't1', 't1', 't2'),
        ('sh', 't1', lo(state + S_RX), 't0'),
        ('lhu', 't1', GP_TOP, 'gp'),
        ('andi', 't2', 'a2', 0xFFFF),
        ('addu', 't1', 't1', 't2'),
        ('sh', 't1', lo(state + S_RY), 't0'),
        ('sh', 'a3', lo(state + S_RW), 't0'),
        ('lhu', 't1', 0x10, 'sp'),
        ('addiu', 'sp', 'sp', -0x20),        # the two replaced instructions
        ('andi', 'a3', 'a3', 0xFFFF),
        ('j', CLEAR_RECT + 8),
        ('sh', 't1', lo(state + S_RH), 't0'),
    ])


def measure(at, widths, maxw):
    """measure(a0 = &lines, a1 = str) -> v0 = cells of the longest line (wrapped at maxw px)."""
    hi, lo = asm.hi, asm.lo
    return asm.assemble(at, [
        ('lui', 't7', hi(widths)),
        ('addiu', 't7', 't7', lo(widths)),
        ('addiu', 't8', 'zero', maxw),
        ('addu', 't5', 'zero', 'zero'),      # x
        ('addu', 't6', 'zero', 'zero'),      # longest line
        ('addu', 't9', 'zero', 'zero'),      # line breaks
        'loop:',
        ('lbu', 't2', 0, 'a1'),
        ('addiu', 'a1', 'a1', 1),
        ('beqz', 't2', 'end'),
        ('addiu', 'at', 'zero', 0x20),
        ('beq', 't2', 'at', 'space'),
        ('addiu', 'at', 'zero', 0x0A),
        ('beq', 't2', 'at', 'brk'),
        ('addiu', 'at', 'zero', 0x0D),
        ('beq', 't2', 'at', 'cr'),
        ('addiu', 'at', 'zero', TAB),
        ('beq', 't2', 'at', 'tab'),
        ('sltiu', 'at', 't2', 0x1A),
        ('bnez', 'at', 'loop'),
        ('sltiu', 'at', 't2', 0x1C),
        ('bnez', 'at', 'esc'),
        ('sltiu', 'at', 't2', 0x7F),
        ('beqz', 'at', 'high'),
        ('addu', 'v0', 't7', 't2'),
        ('lbu', 'v0', -0x20, 'v0'),
        ('b', 'loop'),
        ('addu', 't5', 't5', 'v0'),
        'tab:',                              # column tab: x = max(x + space, px)
        ('lbu', 'v0', 0, 'a1'),
        ('nop',),
        ('beqz', 'v0', 'end'),
        ('addiu', 'a1', 'a1', 1),
        ('lbu', 'v1', 0, 'a1'),
        ('nop',),
        ('beqz', 'v1', 'end'),
        ('addiu', 'a1', 'a1', 1),
        ('addiu', 'v0', 'v0', -TAB_HI),
        ('sll', 'v0', 'v0', 5),
        ('addiu', 'v1', 'v1', -TAB_LO),
        ('addu', 'v0', 'v0', 'v1'),
        ('lbu', 'v1', 0, 't7'),
        ('nop',),
        ('addu', 't5', 't5', 'v1'),
        ('slt', 'at', 't5', 'v0'),
        ('beqz', 'at', 'loop'),
        ('nop',),
        ('b', 'loop'),
        ('addu', 't5', 'v0', 'zero'),
        'cr:',                               # CR LF = one line break
        ('lbu', 'v0', 0, 'a1'),
        ('addiu', 'at', 'zero', 0x0A),
        ('bne', 'v0', 'at', 'brk'),
        ('nop',),
        ('addiu', 'a1', 'a1', 1),
        'brk:',
        ('slt', 'at', 't6', 't5'),
        ('beqz', 'at', 'brk2'),
        ('addiu', 't9', 't9', 1),
        ('addu', 't6', 't5', 'zero'),
        'brk2:',
        ('b', 'loop'),
        ('addu', 't5', 'zero', 'zero'),
        'high:',
        ('sltiu', 'at', 't2', 0xA0),
        ('bnez', 'at', 'lead'),
        ('sltiu', 'at', 't2', 0xE0),
        ('beqz', 'at', 'lead'),
        ('nop',),
        ('b', 'loop'),
        ('addiu', 't5', 't5', 8),
        'lead:',
        ('lbu', 'v0', 0, 'a1'),
        ('nop',),
        ('beqz', 'v0', 'end'),
        ('addiu', 'a1', 'a1', 1),
        ('b', 'loop'),
        ('addiu', 't5', 't5', 16),
        'esc:',
        ('lbu', 'v0', 0, 'a1'),
        ('nop',),
        ('beqz', 'v0', 'end'),
        ('addiu', 'a1', 'a1', 1),
        ('addiu', 'at', 'zero', 0x57),
        ('beq', 'v0', 'at', 'loop'),
        ('nop',),
        ('lbu', 'v0', 0, 'a1'),
        ('nop',),
        ('beqz', 'v0', 'end'),
        ('nop',),
        ('b', 'loop'),
        ('addiu', 'a1', 'a1', 1),
        'space:',
        ('lbu', 'v0', 0, 't7'),
        ('addu', 't3', 'a1', 'zero'),
        ('addu', 't4', 't5', 'v0'),
        'wl:',
        ('lbu', 'v0', 0, 't3'),
        ('addiu', 't3', 't3', 1),
        ('sltiu', 'at', 'v0', 0x21),
        ('bnez', 'at', 'wend'),
        ('sltiu', 'at', 'v0', 0x7F),
        ('beqz', 'at', 'whigh'),
        ('addu', 'v1', 't7', 'v0'),
        ('lbu', 'v1', -0x20, 'v1'),
        ('b', 'wl'),
        ('addu', 't4', 't4', 'v1'),
        'whigh:',
        ('sltiu', 'at', 'v0', 0xA0),
        ('bnez', 'at', 'wlead'),
        ('sltiu', 'at', 'v0', 0xE0),
        ('beqz', 'at', 'wlead'),
        ('nop',),
        ('b', 'wl'),
        ('addiu', 't4', 't4', 8),
        'wlead:',
        ('addiu', 't3', 't3', 1),
        ('b', 'wl'),
        ('addiu', 't4', 't4', 16),
        'wend:',
        ('addiu', 'at', 'zero', 0x1A),
        ('beq', 'v0', 'at', 'wesc'),
        ('addiu', 'at', 'zero', 0x1B),
        ('beq', 'v0', 'at', 'wesc'),
        ('nop',),
        'wcmp:',
        ('slt', 'at', 't8', 't4'),
        ('bnez', 'at', 'brk'),               # wrap: the space ends the line
        ('nop',),
        ('lbu', 'v0', 0, 't7'),
        ('b', 'loop'),
        ('addu', 't5', 't5', 'v0'),
        'wesc:',
        ('lbu', 'v0', 0, 't3'),
        ('addiu', 't3', 't3', 1),
        ('beqz', 'v0', 'wcmp'),
        ('addiu', 'at', 'zero', 0x57),
        ('beq', 'v0', 'at', 'wl'),
        ('nop',),
        ('b', 'wl'),
        ('addiu', 't3', 't3', 1),
        'end:',
        ('slt', 'at', 't6', 't5'),
        ('beqz', 'at', 'last'),
        ('nop',),
        ('addu', 't6', 't5', 'zero'),
        'last:',
        ('beqz', 't5', 'out'),
        ('addiu', 'v0', 't6', 15),           # ceil(px / 8) + 1
        ('addiu', 't9', 't9', 1),
        'out:',
        ('sw', 't9', 0, 'a0'),
        ('jr', 'ra'),
        ('sra', 'v0', 'v0', 3),
    ])


def asm_put(exe, va, lines):
    put(exe, va, asm.assemble(va, lines))


def apply(exe, heap):
    """Patch the program (bytearray) in place; heap = kanji.apply's free RAM."""
    assert word(exe, FONT_RANGE) == 0x2c830040 and word(exe, FONT_HI) == 0x3c028011
    assert word(exe, DC_SHADOW_X) == 0x001528c0 and word(exe, DC_MAIN_X) == 0x00152cc0
    assert word(exe, PUTC_WIDE) == 0x0c000000 | DRAW_CELL >> 2 & 0x3FFFFFF and word(exe, PUTC_ONE) == word(exe, PUTC_WIDE)
    for a in CELL_CALLERS:
        assert word(exe, a) == 0x00052c00, hex(a)
    for a in FORMAT_CALLS:
        assert word(exe, a) == 0x0c000000 | FORMAT >> 2 & 0x3FFFFFF, hex(a)
    for a in MEASURE_CALLS:
        assert word(exe, a) == 0x0c000000 | MEASURE >> 2 & 0x3FFFFFF, hex(a)
    g = pixfont.glyphs()
    font = heap.put(b''.join(g[chr(c)][0] for c in ASCII))
    widths = heap.put(bytes(g[chr(c)][1] for c in ASCII))
    tmp = heap.alloc(256)
    state = heap.put(struct.pack('<14h', -1, -1, *[0] * 12))
    assert asm.hi(state) == asm.hi(state + S_TABHI)
    blit = heap.code(blit4, tmp)
    cell = heap.code(vwf_cell, state, widths)
    wbuf = heap.alloc(WRAP_CAP)
    wrap = heap.code(wrap_fmt, state, widths, wbuf)
    meas = heap.code(measure, widths, MEASURE_W)
    # half-width font: ' '..'~'
    put_words(exe, FONT_RANGE, 0x2c830000 | len(ASCII))
    put_words(exe, FONT_HI, 0x3c020000 | asm.hi(font), 0x24420000 | asm.lo(font) & 0xFFFF)
    # pixel-precise blits
    asm_put(exe, BLIT_OPAQUE, [('j', blit + 8), ('nop',)])
    asm_put(exe, BLIT_TRANS, [('j', blit), ('nop',)])
    # draw_cell: a1 = pixel x (shadow straight below, as the original's x / 4 truncation drew it)
    asm_put(exe, DC_SHADOW_X, [('addu', 'a1', 's5', 'zero'), ('nop',)])
    asm_put(exe, DC_MAIN_X, [('sll', 'a1', 's5', 0x10)])
    vram = asm.assemble(DC_VRAM, [
        ('addu', 'a0', 's5', 'zero'),
        ('sll', 'a1', 's6', 3),
        ('sll', 'a2', 's0', 3),
        ('sll', 'a3', 's1', 3),
        ('addiu', 'v0', 'sp', 0x20),
        ('jal', blit + 8),
        ('sw', 'v0', 0x10, 'sp'),
        ('j', DC_EXIT),
        ('nop',),
    ])
    put(exe, DC_VRAM, vram + bytes(DC_VRAM_END - DC_VRAM - len(vram)))
    for a in CELL_CALLERS:
        put_words(exe, a, 0x00052cc0)                 # sll a1, a1, 0x13
    # putc
    asm_put(exe, PUTC_WIDE, [('jal', cell)])
    put(exe, PUTC_WIDE_ADV, bytes(16))
    asm_put(exe, PUTC_ONE, [('jal', cell)])
    asm_put(exe, PUTC_ONE_ADV, [('j', PUTC_EXIT), ('nop',), ('nop',)])
    for a in FORMAT_CALLS:
        asm_put(exe, a, [('jal', wrap)])
    assert word(exe, CLEAR_RECT) == 0x27bdffe0 and word(exe, CLEAR_RECT + 4) == 0x30e7ffff
    asm_put(exe, CLEAR_RECT, [('j', heap.code(rect_hook, state)), ('nop',)])
    for a in MEASURE_CALLS:
        asm_put(exe, a, [('jal', meas)])
    return {'cell': cell, 'wrap': wrap, 'measure': meas, 'widths': widths, 'state': state}
