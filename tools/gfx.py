"""Redraw the Japanese text baked into the GRAPH/*.BIN images.

The GRAPH files are raw VRAM rectangles (no TIM headers): FILES maps each file's pieces
to their VRAM rect, so an image is addressed like the sprites that draw it
(tools/emu/sprites.py: texture page, u/v, size, CLUT). Lettering code from the S6
gfx.py (Soutenroku/Taiko 5 port): stroke glyphs of tools/latinfont.py (original
drawings) rendered with antialiasing and mapped to the nearest colour of the image's own
CLUT, or the pixel glyphs of tools/tinyfont.py for tiny labels. Palettes, sizes and file
lengths never change.

  plate(): light text with a dark shadow/outline on a textured plate (menu bars, tabs):
    the old lettering is erased by inpainting from the plate around it, then the English
    is drawn in the original's fill and shadow colours.
  clear(): text on transparency.  pixel(): tinyfont lettering.

tl/gfx.txt: "NAME.spot<TAB>English" ('|' = line break, '^' = date column); NAME is a
key of ITEMS. A spot may cover several boxes (the same label in each button state).
"""
import collections, os, random, struct, sys
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(__file__))
import latinfont, pixfont, tinyfont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TL = os.path.join(ROOT, 'tl', 'gfx.txt')
SS = 4

def colour(c):
    r, g, b = (c & 31) * 255 // 31, (c >> 5 & 31) * 255 // 31, (c >> 10 & 31) * 255 // 31
    return (r, g, b, 0 if c == 0 else 0x80)

# ---------------------------------------------------------------- raw VRAM files

# disc file (GRAPH/NAME.BIN) -> pieces (file offset, VRAM x (halfwords), y, w (halfwords), h)
FILES = {
    'SMENU': [(0x1000, 320, 0, 128, 512), (0x21000, 896, 256, 128, 256)],
    'CELL': [(0, 640, 0, 256, 256)],
    # 48x24 button strips loaded over the bottom of CMDWIN's rect (listed first: locate()
    # takes the first match): CMDWIN3 = 条件 戦績, CMDWIN2 = blank plates + 状況 (same VRAM
    # spot, so given a virtual y + 1000 here)
    'CMDWIN3': [(0, 768, 472, 96, 24)],
    'CMDWIN2': [(0, 768, 1472, 88, 24)],
    'CMDWIN': [(0, 640, 256, 256, 256)],
    # duel (一騎討ち) page loaded over SMENU's rect (320,0): virtual y + 2000; CLUTs at +0xd800..
    'HEXIKKI': [(0, 320, 2000, 128, 256)],
    # duel tournament page (0x800bfbc4): +0 3 CLUTs -> (320,248), +0x800 -> (320,0) 256x104 8bpp: virtual y + 3000
    'IKKIMODE': [(0x800, 320, 3000, 128, 104)],
    # title screen (0x80063350..): +0x1800 -> (320,256) 320x240 16bpp, over SMENU's rect: virtual y + 4000
    'OPENING': [(0x1800, 320, 4256, 320, 240)],
    # ruler-select page: +0 -> (512,256) 256x256 8bpp (the rest of the file goes elsewhere)
    'SMAP': [(0, 512, 256, 128, 256)],
    # battle page: +0 -> (512,0) 256x256 8bpp (digits, icons, panel labels; CLUTs inside); +0x10000 elsewhere
    'HEX': [(0, 512, 0, 128, 256)],
}

def locate(x, y):
    """VRAM halfword (x, y) -> (file, byte offset) of the piece holding it."""
    for f, pieces in FILES.items():
        for off, vx, vy, w, h in pieces:
            if vx <= x < vx + w and vy <= y < vy + h:
                return f, off + ((y - vy) * w + x - vx) * 2
    raise ValueError('VRAM %d,%d is in no known file' % (x, y))

class Texture:
    """A w x h pixel rect at texture page (px, py) + (u, v), bpp 4/8, read from the
    edited file buffers; clut = VRAM (x, y) or ('FILE', byte offset)."""
    def __init__(self, bufs, px, py, u, v, w, h, bpp, clut):
        self.bufs, self.bpp, self.w, self.h = bufs, bpp, w, h
        if bpp == 16:
            # direct colour: the palette is the rect's own colours, grown by Canvas.index
            self.x0, self.y0 = px + u, py + v
            self.raw_pal, self.rows, seen = [], [], {}
            for r in range(h):
                f, o = locate(self.x0, self.y0 + r)
                row = []
                for c in struct.unpack_from('<%dH' % w, bufs[f], o):
                    if c not in seen:
                        seen[c] = len(self.raw_pal); self.raw_pal.append(c)
                    row.append(seen[c])
                self.rows.append((f, o, row))
            self.pal = [colour(c) for c in self.raw_pal]
            return
        per = 16 // bpp
        assert u % per == 0 and w % per == 0
        self.x0, self.y0 = px + u // per, py + v
        cf, co = clut if isinstance(clut[0], str) else locate(*clut)
        self.raw_pal = struct.unpack_from('<%dH' % (1 << bpp), bufs[cf], co)
        self.pal = [colour(c) for c in self.raw_pal]
        self.rows = []
        for r in range(h):
            f, o = locate(self.x0, self.y0 + r)
            raw = bufs[f][o:o + w * bpp // 8]
            self.rows.append((f, o, list(raw) if bpp == 8 else [q for b in raw for q in (b & 15, b >> 4)]))

    def add(self, rgb, alpha):
        """16 bpp: palette index of an exact colour (added if new); alpha 0 = 0x0000."""
        c = 0 if not alpha else (int(rgb[0]) >> 3 | int(rgb[1]) >> 3 << 5 | int(rgb[2]) >> 3 << 10) or 0x8000
        if c not in self.raw_pal:
            self.raw_pal.append(c); self.pal.append(colour(c))
        return self.raw_pal.index(c)

    def get(self, x, y):
        return self.pal[self.rows[y][2][x]]

    def idx(self, x, y):
        return self.rows[y][2][x]

    def put(self, x, y, i):
        assert 0 <= i < len(self.pal)
        self.rows[y][2][x] = i

    def store(self):
        for f, o, p in self.rows:
            if self.bpp == 16:
                out = struct.pack('<%dH' % len(p), *(self.raw_pal[i] for i in p))
                self.bufs[f][o:o + len(out)] = out
                continue
            out = bytes(p) if self.bpp == 8 else bytes(p[k] | p[k + 1] << 4 for k in range(0, len(p), 2))
            self.bufs[f][o:o + len(out)] = out

    def image(self):
        im = Image.new('RGBA', (self.w, self.h))
        im.putdata([c[:3] + (255 if c[3] else 0,) for c in (self.pal[i] for _, _, p in self.rows for i in p)])
        return im

def _pixfont():
    """The game-text font (tools/pixfont.py) in tinyfont form: rows from the cap top (9 rows
    to the baseline, 3 descender rows)."""
    out = {c: rows for c, (w, rows) in pixfont._parse().items()}
    out[' '] = ['.' * (pixfont.SPACE_ADV - pixfont.SPACING)]
    return out

PIX = _pixfont()
CAPS = {'mid': 7, 'small': 5, 'pix': 9}

def luma(c):
    return (c[0] * 299 + c[1] * 587 + c[2] * 114) // 1000

class Canvas:
    """Palette helpers around a Texture."""
    def __init__(self, tex):
        self.t = tex
        self.w, self.h, self.pal = tex.w, tex.h, tex.pal
        self.cache = {}
    def get(self, x, y):
        return self.t.get(x, y)

    def idx(self, x, y):
        return self.t.idx(x, y)

    def put(self, x, y, i):
        self.t.put(x, y, i)

    def index(self, rgb, alpha=0x80):
        """Nearest palette entry to an RGB colour (alpha 0 = the transparent colour)."""
        key = tuple(int(round(v)) for v in rgb[:3]) + (int(round(alpha)),)
        if self.t.bpp == 16:
            return self.t.add(key[:3], key[3])
        if key not in self.cache:
            self.cache[key] = min(range(len(self.pal)), key=lambda i: sum((a - b) ** 2 for a, b in zip(self.pal[i][:3], key[:3]))
                                  + 6 * (self.pal[i][3] - key[3]) ** 2)
        return self.cache[key]

# ---------------------------------------------------------------- lettering

def text_masks(text, cap, widths, condense=1.0, spacing=None, descender=None):
    """Coverage images (PIL 'L', SS times the final size) of text drawn with each
    stroke width in widths (px, final scale): wider strokes give the outline layers.
    Returns (images, (w, h), (left, top) of the cap box) at final scale."""
    stroke = widths[0]
    s = (cap - stroke) / latinfont.C
    spacing = stroke * 0.45 if spacing is None else spacing
    pad = max(widths) / 2 + 1
    xs, x = [], pad
    for c in text:
        w, _ = latinfont.GLYPHS[c]
        xs.append(x)
        x += (w * s * condense + stroke + spacing) if c != ' ' else cap * 0.32
    width = x - spacing + pad
    if descender is None:
        descender = any(c in 'gjpqy,;' for c in text)
    height = cap + stroke + 2 * pad + (3 * s if descender else 0)
    base = pad + cap - stroke / 2
    out = []
    for sw in widths:
        im = Image.new('L', (int(width * SS) + 1, int(height * SS) + 1), 0)
        dr = ImageDraw.Draw(im)
        r = sw * SS / 2
        for c, x0 in zip(text, xs):
            for st in latinfont.GLYPHS[c][1]:
                pts = [((x0 + stroke / 2 + px * s * condense) * SS, (base - py * s) * SS) for px, py in st]
                for a, b in zip(pts, pts[1:]):
                    dr.line([a, b], fill=255, width=max(1, round(sw * SS)))
                for px, py in pts:
                    dr.ellipse((px - r, py - r, px + r, py + r), fill=255)
        out.append(im)
    return out, (width, height), (pad, pad)

def fit(lines, cap, box_w, box_h, widths_of, min_cap=5.0, min_condense=0.7):
    """Shrink (condense first, then cap) until every line fits box_w and all lines box_h.
    -> (cap, condense, [(masks, (w, h), (px, py))])"""
    condense = 1.0
    while True:
        r = [text_masks(l, cap, widths_of(cap), condense, descender=False) for l in lines]
        lh = cap + widths_of(cap)[-1]
        tw = max(m[1][0] for m in r) - 2 * (max(widths_of(cap)) / 2 + 1)
        if (tw <= box_w and lh * len(lines) + (len(lines) - 1) * 1 <= box_h + 1) or cap <= min_cap:
            if tw > box_w + 0.5 or lh * len(lines) > box_h + 3:
                raise ValueError('%r does not fit %dx%d' % ('|'.join(lines), box_w, box_h))
            return cap, condense, r
        if condense > min_condense and lh * len(lines) + (len(lines) - 1) * 1 <= box_h + 1:
            condense *= 0.96            # too wide: condense first
        else:
            cap *= 0.95

# ---------------------------------------------------------------- plates

def plate_style(cv, box, mode='light', grow=2, sens=(24, 0.4), amin=0x40, th=None):
    """-> (text mask set, fill rgb, outline rgb, text alpha) for light text with a dark
    shadow (mode 'light') or dark text on a light plate ('dark'). th = fixed luma threshold
    of the lettering (when it covers too much of the box for the median to be the plate)."""
    x0, y0, x1, y1 = box
    px = [(x, y) for y in range(y0, y1 + 1) for x in range(x0, x1 + 1)]
    L = {p: luma(cv.get(*p)) for p in px}
    A = {p: cv.get(*p)[3] for p in px}
    vals = sorted(L[p] for p in px if A[p] > amin)
    med = vals[len(vals) // 2]
    spread = max(6, vals[len(vals) * 2 // 3] - vals[len(vals) // 3])
    if th is not None:
        core = {p for p in px if (L[p] >= th if mode == 'light' else L[p] <= th) and A[p] > amin}
        med = sorted(L[p] for p in px if p not in core)[(len(px) - len(core)) // 2]
    elif mode == 'light':
        hi = vals[int(len(vals) * 0.97)]
        th = med + max(sens[0], (hi - med) * sens[1])
        core = {p for p in px if L[p] >= th and A[p] > amin}
    else:
        lo = vals[int(len(vals) * 0.03)]
        th = med - max(sens[0], (med - lo) * sens[1])
        core = {p for p in px if L[p] <= th and A[p] > amin}
    if not core:
        raise ValueError('no lettering found in %r' % (box,))
    ring = collections.Counter()
    for (x, y) in core:
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                q = (x + dx, y + dy)
                if q in L and q not in core and ((L[q] < med) if mode == 'light' else (L[q] > med)):
                    ring[q] += 1
    fillc = sorted((cv.get(*p) for p in core), key=luma)
    fill = fillc[int(len(fillc) * 0.8)] if mode == 'light' else fillc[len(fillc) // 5]
    outc = sorted((cv.get(*p) for p in ring), key=luma)
    outline = (outc[len(outc) // 10] if mode == 'light' else outc[len(outc) * 3 // 4]) if outc else None
    mask = set(core) | set(ring)
    for _ in range(grow):
        mask |= {(x + dx, y + dy) for (x, y) in mask for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                 if (x + dx, y + dy) in L and abs(L[x + dx, y + dy] - med) > spread}
    mask |= {(x + dx, y + dy) for (x, y) in mask for dx in (-1, 0, 1) for dy in (-1, 0, 1) if (x + dx, y + dy) in L}
    return mask, fill[:3], outline[:3] if outline else None, fill[3]

def erase(cv, box, mask, seed=0, sample=False):
    """Inpaint masked pixels from the outside in (onion peel): each pass fills the pixels
    with the most known neighbours by copying a random known pixel within 2 px, so the
    background's local colours and grain continue under the text. sample: copy random
    unmasked pixels from anywhere in the box instead (plain noisy stone under big lettering)."""
    x0, y0, x1, y1 = box
    rnd = random.Random(seed)
    todo = {p for p in mask if x0 <= p[0] <= x1 and y0 <= p[1] <= y1}
    if sample:
        src = [cv.idx(x, y) for y in range(y0, y1 + 1) for x in range(x0, x1 + 1) if (x, y) not in todo]
        for p in sorted(todo):
            cv.put(p[0], p[1], rnd.choice(src))
        return
    near = [(dx, dy) for dx in range(-2, 3) for dy in range(-2, 3) if dx or dy]
    def known(x, y):
        return [(x + dx, y + dy) for dx, dy in near
                if x0 <= x + dx <= x1 and y0 <= y + dy <= y1 and (x + dx, y + dy) not in todo]
    while todo:
        cand = {p: known(*p) for p in sorted(todo)}
        best = max(len(v) for v in cand.values())
        if best == 0:
            break
        need = max(1, min(best, 8))
        layer = [p for p, v in cand.items() if len(v) >= need]
        for p in layer:
            cv.put(p[0], p[1], cv.idx(*rnd.choice(cand[p])))
        todo -= set(layer)

def draw(cv, box, text, fill, outline, alpha=0x80, cap=None, align='center', bold=0.14, lw=1.0,
         vshift=0, hshift=0, shadow=None, min_cap=5.0, sharp=0.0):
    """English (lines split on '|') in box, fill + outline colours. shadow=(dx, dy): the
    outline colour is a drop shadow of the letters instead of a ring round them. sharp
    (0-1) pushes antialiased edges towards ink or background (crisper small text)."""
    x0, y0, x1, y1 = box
    lines = text.split('|')
    bw, bh = x1 - x0 + 1, y1 - y0 + 1
    ring = outline is not None and not shadow
    widths_of = lambda c: [max(1.0, c * bold)] + ([max(1.0, c * bold) + 2 * lw] if ring else [])
    cap = cap or min(bh / len(lines) * 0.62, 22)
    cap = min(cap, bh / len(lines) * 0.75)
    sx, sy = shadow or (0, 0)
    cap, condense, rs = fit(lines, cap, bw - 2 - sx, bh - sy, widths_of, min_cap)
    lh = cap + widths_of(cap)[-1] + (1 if len(lines) > 1 else 0)
    total = lh * len(lines) - (1 if len(lines) > 1 else 0)
    top = y0 + (bh - sy - total) / 2 + vshift
    def cover(v):
        if sharp:
            v = min(1.0, max(0.0, (v - 0.5) * (1 + 3 * sharp) + 0.5))
        return v
    for k, (masks, (tw, th), (px, py)) in enumerate(rs):
        size = (int(tw) + 1, int(th) + 1)
        layers = [m.resize(size, Image.BOX) for m in masks]
        inkw = tw - 2 * px
        ox = {'center': x0 + (bw - sx - inkw) / 2, 'left': x0 + 1, 'right': x1 - sx - inkw}[align] - px + hshift
        oy = top + k * lh - py + (widths_of(cap)[-1] - widths_of(cap)[0]) / 2
        ox, oy = int(round(ox)), int(round(oy))
        get = lambda l, x, y: cover(l.getpixel((x, y)) / 255) if 0 <= x < size[0] and 0 <= y < size[1] else 0
        for y in range(-1, size[1] + sy + 1):
            for x in range(-1, size[0] + sx + 1):
                tx, ty = ox + x, oy + y
                if not (x0 <= tx <= x1 and y0 <= ty <= y1):
                    continue
                c = get(layers[0], x, y)
                if shadow:
                    e = get(layers[0], x - sx, y - sy) if outline else 0
                else:
                    e = get(layers[1], x, y) if ring else 0
                if c <= 0.04 and e <= 0.08:
                    continue
                base = cv.get(tx, ty)
                col = base[:3]
                if e > 0:
                    col = tuple(p + (q - p) * min(1, e * 1.3) for p, q in zip(col, outline))
                if c > 0:
                    col = tuple(p + (q - p) * min(1, c * 1.15) for p, q in zip(col, fill))
                a = base[3] + (max(alpha, base[3]) - base[3]) * max(c, e)
                cv.put(tx, ty, cv.index(col, a))

def clear(cv, box, text, dark=False, amin=0x60, outline=True, colours=None, **kw):
    """Text on transparency: fill/outline = bright/dark opaque colours of the box (or
    colours=(fill, outline) rgb), which is cleared to the transparent colour and redrawn."""
    x0, y0, x1, y1 = box
    inside = [(x, y) for y in range(y0, y1 + 1) for x in range(x0, x1 + 1)]
    if colours:
        fill, line = colours
    else:
        opq = sorted((cv.get(x, y) for x, y in inside if cv.get(x, y)[3] >= amin), key=luma)
        fill, line = opq[len(opq) * 19 // 20], opq[len(opq) // 10]
        if dark:
            fill, line = opq[len(opq) // 20], opq[len(opq) * 9 // 10]
    t = min(range(len(cv.pal)), key=lambda i: cv.pal[i][3])
    if cv.pal[t][3]:
        t = cv.index((0, 0, 0))         # no transparent colour (opaque full-screen image): black
    for x, y in inside:
        cv.put(x, y, t)
    if text:
        draw(cv, box, text, fill[:3], line[:3] if outline else None, 0x80, **kw)

def plate(cv, text, box=None, mode='light', erase_box=None, style=None, colours=None, sample=False, **kw):
    """colours = (fill rgb, outline rgb): override the measured ones (e.g. a tile whose
    lettering is too thin to measure well)."""
    box = box or (1, 1, cv.w - 2, cv.h - 2)
    if mode == 'clear':
        return clear(cv, box, text, colours=colours, **kw)
    boxes = erase_box if isinstance(erase_box, list) else [erase_box or box]
    mask, fill, outline, alpha = plate_style(cv, boxes[0], mode, **(style or {}))
    for b in boxes[1:]:
        mask |= plate_style(cv, b, mode, **(style or {}))[0]
    if colours:
        fill, outline = colours
    for b in boxes:
        erase(cv, b, mask, sample=sample)
    if text and '^' in text:
        # date column: left part in the first col px, the rest left-aligned after it
        col = kw.pop('col', 50)
        left, right = text.split('^')
        kw['align'] = 'left'
        draw(cv, (box[0] + 2, box[1], box[0] + col - 1, box[3]), left, fill, outline, alpha, **kw)
        draw(cv, (box[0] + col + 2, box[1], box[2], box[3]), right, fill, outline, alpha, **kw)
    elif text:
        kw.pop('col', None)
        draw(cv, box, text, fill, outline, alpha, **kw)

# ---------------------------------------------------------------- pixel icons

def pixel(cv, text, box, mode='dark', font=None, ink=None, shadow=None, grow=0, vshift=0, hshift=0, also=(), th=None, grey=False, outline=None, flat=None):
    """Tiny icon lettering (tinyfont): the old glyph = pixels of the ink palette index (the
    darkest colour in the box, or the brightest for mode 'light'; ink= forces it) plus those
    of the shadow index; they are inpainted, then text is drawn in the same index, centred.
    shadow = (palette index, dx, dy) drop shadow. font: 'mid' / 'small' (default: mid when
    it fits). also = more palette indexes to erase; th = erase every pixel darker (mode
    'dark') or brighter than this luma; grey = erase every grey pixel (lettering and its
    outline on a coloured plate); outline = palette index of a 1 px ring round the text;
    flat = fill the whole box with this palette index instead of inpainting."""
    x0, y0, x1, y1 = box
    px = [(x, y) for y in range(y0, y1 + 1) for x in range(x0, x1 + 1)]
    if ink is None:
        used = collections.Counter(cv.idx(*p) for p in px)
        key = lambda i: luma(cv.pal[i])
        ink = (min if mode == 'dark' else max)((i for i in used if cv.pal[i][3]), key=key)
    kill = {ink} | ({shadow[0]} if shadow else set()) | set(also)
    mask = {p for p in px if cv.idx(*p) in kill}
    if grey:
        mask |= {p for p in px if max(cv.get(*p)[:3]) - min(cv.get(*p)[:3]) < 24}
    if th is not None:
        mask |= {p for p in px if (luma(cv.get(*p)) <= th if mode == 'dark' else luma(cv.get(*p)) >= th)}
    for _ in range(grow):
        mask |= {(x + dx, y + dy) for (x, y) in mask for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                 if x0 <= x + dx <= x1 and y0 <= y + dy <= y1}
    if isinstance(flat, tuple):
        flat = cv.index(flat[:3], *flat[3:])
    if flat == 'rows':
        # striped plate: every row takes its most common index
        m = None
        for y in range(y0, y1 + 1):
            c = collections.Counter(cv.idx(x, y) for x in range(x0, x1 + 1) if cv.idx(x, y) not in kill)
            m = c.most_common(1)[0][0] if c else m
            for x in range(x0, x1 + 1):
                cv.put(x, y, m)
    elif flat is not None:
        for p in px:
            cv.put(p[0], p[1], flat)
    else:
        erase(cv, box, mask)
    if not text:
        return
    lines = text.split('|')
    bw, bh = x1 - x0 + 1, y1 - y0 + 1
    sx, sy = (shadow[1], shadow[2]) if shadow else (0, 0)
    if outline is not None:
        bw, bh, x0, y0 = bw - 2, bh - 2, x0 + 1, y0 + 1
    fonts = {'mid': tinyfont.MID, 'small': tinyfont.SMALL, 'pix': PIX}
    for name in ([font] if isinstance(font, str) else (font or ['mid', 'small'])):
        f = fonts[name]
        if any(c not in f for l in lines for c in l):
            continue
        rs = [tinyfont.render(l, f) for l in lines]
        # lowercase descenders hang below the cap height (7): count them only when present
        hs = [h for _, h, _ in rs]
        total = sum(hs) + len(lines) - 1
        if max(w for w, _, _ in rs) + sx <= bw and total + sy <= bh + {'mid': 2, 'pix': 3}.get(name, 0):
            break
    else:
        raise ValueError('%r does not fit %dx%d' % (text, bw, bh))
    cap = CAPS[name]
    lh = cap + 1
    top = y0 + (bh - sy - (lh * len(lines) - 1)) // 2 + vshift
    for k, (w, h, ink_px) in enumerate(rs):
        ox = x0 + (bw - sx - w + 1) // 2 + hshift
        oy = top + k * lh
        if outline is not None:
            ring = {(x + dx, y + dy) for (x, y) in ink_px for dx in (-1, 0, 1) for dy in (-1, 0, 1)}
            for (x, y) in ring:
                tx, ty = ox + x, oy + y
                if box[0] <= tx <= x1 and box[1] <= ty <= y1:
                    cv.put(tx, ty, outline)
        if shadow:
            for (x, y) in ink_px:
                tx, ty = ox + x + sx, oy + y + sy
                if x0 <= tx <= x1 and y0 <= ty <= y1:
                    cv.put(tx, ty, shadow[0])
        for (x, y) in ink_px:
            tx, ty = ox + x, oy + y
            if x0 <= tx <= x1 and y0 <= ty <= y1 + 1:
                cv.put(tx, ty, ink)

def icons(n, w=16, h=16, inset=2, **kw):
    """A strip of n icons of w x h (frame inset px)."""
    return [dict(kw, kind='pixel', box=(k * w + inset, inset, k * w + w - 1 - inset, h - 1 - inset)) for k in range(n)]

# ---------------------------------------------------------------- engraved banners

def engrave(cv, text, cap=20, bold=0.18, bevel=1.6, rim=1.0, edge=1.0, colours=None, min_cap=10):
    """Big lettering on transparency in the HEX/SSLG.S6 style (crisp, no antialiasing): dark
    stroke, light bevel (bright up-left, grey down-right), green rim, black edge. The whole
    image is cleared first. colours = (core, bevel light, bevel dark, rim, edge) rgb."""
    core_c, hi_c, lo_c, rim_c, edge_c = colours or ((8, 8, 8), (230, 230, 230), (164, 172, 156), (82, 115, 82), (8, 8, 8))
    t = min(range(len(cv.pal)), key=lambda i: cv.pal[i][3])
    for y in range(cv.h):
        for x in range(cv.w):
            cv.put(x, y, t)
    condense = 1.0
    while True:
        sw = max(2.0, cap * bold)
        widths = [sw, sw + 2 * bevel, sw + 2 * (bevel + rim), sw + 2 * (bevel + rim + edge)]
        masks, (tw, th), _ = text_masks(text, cap, widths, condense, descender=False)
        if tw <= cv.w - 2 or cap <= min_cap:
            break
        if condense > 0.8:
            condense *= 0.97
        else:
            cap *= 0.96
    if tw > cv.w:
        raise ValueError('%r: %d px wide, image %d' % (text, tw, cv.w))
    size = (int(tw) + 1, int(th) + 1)
    layers = [m.resize(size, Image.BOX) for m in masks]
    on = [[[layers[k].getpixel((x, y)) >= 128 for x in range(size[0])] for y in range(size[1])] for k in range(4)]
    ox, oy = (cv.w - size[0]) // 2, (cv.h - size[1]) // 2
    idx = [cv.index(c) for c in (core_c, hi_c, lo_c, rim_c, edge_c)]
    for y in range(size[1]):
        for x in range(size[0]):
            tx, ty = ox + x, oy + y
            if not (0 <= tx < cv.w and 0 <= ty < cv.h):
                continue
            if on[0][y][x]:
                i = idx[0]
            elif on[1][y][x]:
                # bevel: lit when the stroke lies down-right of it
                lit = any(on[0][y + d][x + d] for d in (1, 2) if y + d < size[1] and x + d < size[0])
                i = idx[1] if lit else idx[2]
            elif on[2][y][x]:
                i = idx[3]
            elif on[3][y][x]:
                i = idx[4]
            else:
                continue
            cv.put(tx, ty, i)


# ---------------------------------------------------------------- items

# an image = (texture page x, y, u, v, w, h, bpp, clut); a box = dict(img=..., box=..., plate options)
SMENU_CLUT = ('SMENU', 0)
MENU = {'mode': 'dark', 'shadow': (1, 1), 'cap': 11, 'bold': 0.12, 'sharp': 0.5, 'style': {'grow': 1}}

def bars(img, n, w=208, h=20, left=4, **kw):
    """n stacked 208x20 stone bars (main menu, scenario list): box per bar."""
    return [dict(MENU, img=img, box=(left, r * h + 3, w - 5, r * h + h - 4),
                 erase_box=(3, r * h + 2, w - 4, r * h + h - 3), **kw) for r in range(n)]

def states(*lists):
    """Zip box lists of the same labels in different button states: spot k = all k-th boxes."""
    return [list(b) for b in zip(*lists)]

CMD_CLUT = (640, 498)
def cmd_tabs():
    """Command window tabs (CMDWIN, 32 px each, normal + pressed side by side): top row
    君主 家臣 移動 戦争 機能 (32x16, pressed = 1 px lower), row 2 内政 人事 計略 外交 軍事
    (32x32 plates, pressed = 1 px right and lower)."""
    top = (640, 256, 176, 0, 320, 16, 8, CMD_CLUT)
    low = (640, 256, 176, 16, 320, 32, 8, CMD_CLUT)
    lab = dict(kind='pixel', mode='light', font='mid', ink=6, shadow=(10, 1, 1))
    flat = lambda img, box, i: dict(kind='pixel', img=img, box=box, flat=i, text='')
    tops = [sum(([flat(top, (x + 1, dy, x + 28, dy), 14), flat(top, (x + 1, dy + 1, x + 1, dy + 12), 14), flat(top, (x + 29, dy, x + 29, dy + 13), 13),
                  flat(top, (x + 1, dy + 13, x + 28, dy + 13), 13),
                  dict(lab, img=top, box=(x + 2, dy + 1, x + 28, dy + 12), flat=15)]
                 for x, dy in ((k * 64, 0), (k * 64 + 32, 1))), []) for k in range(5)]
    lows = [sum(([flat(low, (x + c, y, x + c, y + 17), i) for c, i in ((0, 10), (1, 15), (2, 8), (3, 15), (29, 11), (30, 11))]
                 + [dict(lab, img=low, box=(x + 4, y, x + 28, y + 17), flat=13)]
                 for x, y in ((k * 64, 6), (k * 64 + 33, 7))), []) for k in range(5)]
    return tops + lows

CELL_TAB = (640, 0, 64, 72, 432, 48, 8, (640, 239))
def cell_tabs():
    """Officer/city panel buttons (CELL, 48x24 plates, normal + lit side by side): row 1
    人物 アイテム 武将 次都市 斬首, row 2 外交 目標 登用 解放 斬首 (斬首 = last plate of both rows)."""
    def box(r, c):
        x, y = c * 48, r * 24
        return dict(TAB, img=CELL_TAB, box=(x + 3, y + 3, x + 44, y + 20))
    spots = [[box(0, 2 * k), box(0, 2 * k + 1)] for k in range(4)] + [[box(0, 8), box(1, 8)]]
    return spots + [[box(1, 2 * k), box(1, 2 * k + 1)] for k in range(4)]
CELL_STAT = (640, 0, 128, 136, 384, 32, 8, (640, 247))
def cell_stats():
    """Stat labels (CELL, 16 px high plates on 2 rows; CLUT 640,247 brown / 245 green share
    the indexes). Ink 1 + red shadow 8 = white labels, ink 10 = green-ink striped plates."""
    white = dict(kind='pixel', ink=1, shadow=(8, 1, 1), flat='rows', font=['pix', 'mid', 'small'])
    bare = dict(white, shadow=None, also=(8,))
    plain = dict(kind='pixel', ink=10, flat='rows', font=['pix', 'mid', 'small'])
    row1 = [(0, 32, white), (32, 32, white), (64, 32, plain), (96, 40, dict(plain, ink=11)), (136, 24, plain),
            (160, 56, plain), (216, 56, plain), (272, 32, plain), (304, 48, plain), (352, 32, plain)]
    row2 = [(k * 16, 16, white) for k in range(9)] + [(160, 16, dict(plain, ink=8)), (176, 16, plain), (192, 16, plain),
            (208, 16, bare), (224, 16, bare), (240, 16, bare), (288, 56, plain)]
    return [dict(o, img=CELL_STAT, box=(x + 1, r * 16 + 1, x + w - 2, r * 16 + 14))
            for r, row in enumerate((row1, row2)) for x, w, o in row]

# striped plates, white (1) + grey (2) lettering
CELL_HEAD = (640, 0, 296, 200, 176, 24, 8, (640, 247))
TITLE = (320, 4256, 0, 0, 320, 240, 16, None)
DUEL = (320, 2000, 0, 112, 224, 64, 8, ('HEXIKKI', 0xe200))
TAB = {'mode': 'light', 'cap': 10, 'bold': 0.14, 'sharp': 0.5, 'style': {'grow': 1}}

ITEMS = {
    # main menu: 6 buttons, normal (y 0) and selected (y 120) = the same pixels area, other look
    'SMENU.menu': states(bars((320, 0, 0, 0, 208, 120, 8, SMENU_CLUT), 6),
                         bars((320, 0, 0, 120, 208, 120, 8, SMENU_CLUT), 6)),
    # scenario list: 11 titles, normal (320,256) and selected (896,256)
    'SMENU.scen': states(bars((320, 256, 0, 0, 208, 220, 8, SMENU_CLUT), 11, left=12, col=62),
                         bars((896, 256, 0, 0, 208, 220, 8, SMENU_CLUT), 11, left=12, col=62)),
    # command window tabs: spots 0-4 top row, 5-9 second row
    'CMDWIN.tabs': cmd_tabs(),
    # command window header icons 16x16: 0 金 (gold) 1 米 (food)
    'CMDWIN.icons': [[dict(kind='pixel', img=(640, 256, 496, v, 16, 16, 8, (640, 239)), box=(1, 1, 14, 14), ink=3,
                           shadow=(11, 1, 1), flat=6, font='pix'),
                      # 金 touches the tile's left border: give those pixels back to the border
                      dict(kind='pixel', img=(640, 256, 496, v, 16, 16, 8, (640, 239)), box=(0, 5, 0, 6), flat=8, text='')][:2 if v == 0 else 1]
                     for v in (0, 16)],
    # officer/city panel buttons: 0 人物 1 アイテム 2 武将 3 次都市 4 斬首 5 外交 6 目標 7 登用 8 解放
    'CELL.tabs': cell_tabs(),
    # 48x24 buttons in CELL.tabs style (normal + lit): CMDWIN3 0 条件 1 戦績, CMDWIN2 0 状況
    'CMDWIN3.btns': [[dict(TAB, img=(768, 472, 0, 0, 192, 24, 8, (640, 239)), box=(x + 3, 3, x + 44, 20)) for x in (k * 96, k * 96 + 48)]
                     for k in range(2)],
    'CMDWIN2.btns': [[dict(TAB, img=(768, 1472, 80, 0, 96, 24, 8, (640, 239)), box=(x + 3, 3, x + 44, 20)) for x in (0, 48)]],
    # stat labels: row 1 0 勇名 1 経験 2 武将 3 武将数 4 防 5 兵士数 6 人口 7 民忠 8 治水度 9 在野;
    # row 2 10-18 武 知 政 魅 忠 齢 兵 練 士, 19 兵 20 農 21 商 22 平 23 山 24 水 25 予備兵
    'CELL.stats': cell_stats(),
    # officer page headers (88x24): 0 使用可能陣形 1 特殊能力
    'CELL.heads': [dict(kind='pixel', img=CELL_HEAD, box=(x + 1, 5, x + 86, 18), ink=1, also=(2,), flat='rows', font='pix')
                   for x in (0, 88)],
    # city panel button 商人 (40x24, one state)
    # duel captions (HEXIKKI page at 320,0; 32 px brush glyphs on transparency, CLUT 0,499):
    # 0 勝負！ 1 快勝！ 2 対 (row v 112), 3 勝利！ 4 辛勝… (row v 144)
    'HEXIKKI.duel': [dict(mode='clear', img=DUEL, box=(x, y + 1, x + w - 1, y + 30), cap=18, bold=0.2, lw=1.4)
                     for x, y, w in ((0, 0, 96), (96, 0, 96), (192, 0, 32), (0, 32, 96), (96, 32, 96))],
    # duel tournament captions (red on transparency, CLUT IKKIMODE +0): 0 優勝 (32x16) 1 団体優勝 (64x16)
    'IKKIMODE.champ': [dict(kind='pixel', img=(320, 3000, x, 88, w, 16, 8, ('IKKIMODE', 0)), box=(0, 0, w - 1, 15), ink=78,
                            shadow=(19, 1, 1), flat=0, font=['pix', 'mid', 'small']) for x, w in ((120, 32), (152, 64))],
    # title logo: パワーアップキット + zigzag underline (16bpp, black = 0x0000) -> English over a straight bar
    'OPENING.logo': [[dict(kind='pixel', img=TITLE, box=(96, 189, 226, 218), flat=(0, 0, 0, 0), ink=0, text=''),
                      dict(mode='clear', img=TITLE, box=(100, 190, 222, 211), cap=15, bold=0.2, lw=1.2,
                           colours=((168, 8, 248), (40, 0, 80))),
                      dict(kind='pixel', img=TITLE, box=(100, 214, 222, 215), flat=(152, 0, 248), ink=0, text='')]],
    # ruler select 決定 button (48x24, normal + lit side by side, lit = 1 px lower; CLUT 640,496)
    'SMAP.ok': [[dict(kind='pixel', img=(512, 256, 0, 152, 96, 24, 8, (640, 496)), box=(x + 5, 5, x + 42, 17 + dy), ink=8,
                      shadow=(4, 1, 1), flat='rows', font=['pix', 'mid']) for x, dy in ((0, 0), (48, 1))]],
    # battle status panel labels (HEX page 576,0, CLUT HEX +0xec00): 0 部隊 (32x16) 1 兵 2 米 (16x16)
    'HEX.panel': [dict(kind='pixel', img=(576, 0, u, v, w, 16, 8, ('HEX', 0xec00)), box=(1, 2, w - 2, 13), ink=15,
                       shadow=(9, 1, 1), also=(0,), flat='rows', font=['pix', 'mid', 'small']) for u, v, w in ((32, 32, 32), (80, 0, 16), (96, 0, 16))],
    'CELL.merchant': [dict(kind='pixel', img=(640, 0, 256, 200, 40, 24, 8, (640, 249)), box=(4, 6, 35, 17), ink=8, flat=15, font=['pix', 'mid', 'small'])],
}

def load_tl():
    """-> {name: {spot: text}}"""
    out = collections.defaultdict(dict)
    if os.path.exists(TL):
        for line in open(TL, encoding='ascii').read().splitlines():
            if line.strip() and not line.startswith('#'):
                k, _, v = line.partition('\t')
                name, _, spot = k.rpartition('.')
                if name not in ITEMS:
                    raise ValueError('tl/gfx.txt: unknown image %r' % name)
                out[name][int(spot)] = v
    return out

def redraw(bufs, name, spots):
    """Draw one item's English into bufs ({file: bytearray}) -> the edited textures."""
    boxes = ITEMS[name]
    texs = {}
    for spot in sorted(spots):
        if spot >= len(boxes):
            raise ValueError('no spot %d' % spot)
        for o in (boxes[spot] if isinstance(boxes[spot], list) else [boxes[spot]]):
            o = dict(o)
            img = o.pop('img')
            if img not in texs:
                texs[img] = Canvas(Texture(bufs, *img))
            cv = texs[img]
            kind = o.pop('kind', 'plate')
            text = o.pop('text', spots[spot])
            if kind == 'plate':
                plate(cv, text, **o)
            elif kind == 'pixel':
                pixel(cv, text, **o)
            elif kind == 'engrave':
                engrave(cv, text, **o)
            else:
                raise ValueError(kind)
    for cv in texs.values():
        cv.t.store()
    return texs

def apply(file_fn):
    """-> {disc path: new bytes}; file_fn(path) = clean file bytes (path like 'GRAPH/SMENU.BIN')."""
    clean = {f: file_fn('GRAPH/%s.BIN' % f) for f in FILES}
    bufs = {f: bytearray(d) for f, d in clean.items()}
    for name, spots in sorted(load_tl().items()):
        try:
            redraw(bufs, name, spots)
        except ValueError as err:
            raise ValueError('gfx %s: %s' % (name, err))
    return {'GRAPH/%s.BIN' % f: bytes(b) for f, b in bufs.items() if b != clean[f]}

def preview(out, prefixes=None, scale=2, root=os.path.join(ROOT, 'extract', 'GRAPH')):
    """Before/after sheet of the tl/gfx.txt items (dev; Koei data, keep out of git)."""
    clean = {f: bytearray(open(os.path.join(root, f + '.BIN'), 'rb').read()) for f in FILES}
    cells = []
    for name, spots in sorted(load_tl().items()):
        if prefixes and not any(name.startswith(p) for p in prefixes):
            continue
        bufs = {f: bytearray(b) for f, b in clean.items()}
        try:
            texs = redraw(bufs, name, spots)
        except ValueError as err:
            print('%s: %s' % (name, err))
            continue
        for img, cv in texs.items():
            a = Texture(clean, *img)
            pair = [x.image().resize((x.w * scale, x.h * scale), Image.NEAREST) for x in (a, cv.t)]
            cells.append(('%s %s' % (name, img[:4]), pair))
    W = max([1200] + [a.width * 2 + 8 for _, (a, b) in cells])
    x = y = rowh = 0
    pos = []
    for lab, (a, b) in cells:
        cw = a.width + b.width + 8
        if x + cw > W:
            x, y, rowh = 0, y + rowh + 16, 0
        pos.append((x, y))
        x += cw + 10
        rowh = max(rowh, a.height)
    sheet = Image.new('RGBA', (W, y + rowh + 16), (60, 0, 60, 255))
    dr = ImageDraw.Draw(sheet)
    for (lab, (a, b)), (px, py) in zip(cells, pos):
        dr.text((px, py), lab, fill=(255, 255, 0, 255))
        sheet.alpha_composite(a, (px, py + 12))
        sheet.alpha_composite(b, (px + a.width + 8, py + 12))
    sheet.save(out)
    print(out, len(cells))

if __name__ == '__main__':
    # gfx.py preview out.png [name prefixes...] [--scale N]
    a = sys.argv[3:]
    sc = 2
    if '--scale' in a:
        k = a.index('--scale'); sc = int(a[k + 1]); del a[k:k + 2]
    preview(sys.argv[2], a, sc)
