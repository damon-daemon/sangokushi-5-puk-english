"""Translation files for MESSAGE/MESSAGE.BIN: show, apply, check, stats; used by build.py.

The unit is a segment (msgfile.segments): a chain of Japanese text runs and inline
tokens, between structural codes (conditions, variant ends). The English replaces the
whole segment; the structure around it is kept byte for byte.

tl/msg/BB.txt holds the English (no Japanese) of block BB, one record per segment:

    # comment (a line starting with #; English text never does)
    @<id>            segment 0 of message <id> (= block * 1000 + index)
    @<id>.<n>        segment n
    English text, possibly over several physical lines (joined with spaces).

  \\n          forced line break (the game word-wraps by itself; JP line breaks are dropped)
  \\s          a space (for a space at the start/end of a segment that continues another
              one; plain edge spaces are stripped)
  =           (whole record) keep the Japanese of this segment
  {C26}       insert block-0 word 26 (a pronoun, an honorific, a nickname): may move or
              be dropped (Japanese word endings), not added
  {v01}       print variable 01 (names, numbers): may move, not added
  %s %u %lu   printf fields: same ones in the same order; no other '%'

Translation memory: a segment without English takes the English of a segment with the
same Japanese (explicit English always wins).

usage:
    tl.py show  BB [from] [to] [--todo]   # Japanese segments (ids are local indexes)
    tl.py apply BB batch.txt              # validate a batch, merge it into tl/msg/BB.txt
    tl.py check [BB]                      # validate everything in tl/
    tl.py stats
"""
import os, re, sys

sys.path.insert(0, os.path.dirname(__file__))
import msgfile, pixfont, setup, text

TL_MSG = os.path.join(setup.TL, 'msg')
SEG_MAX = 500                      # bytes per segment: vwf.wrap_fmt copies formats < 512 B
EXPAND_MAX = 254                   # default expand buffer 0x80152e40 (limit 0xFE, ram.md §1)
_TOK = re.compile(r'\{C(\d+)\}|\{v([0-9A-Fa-f]{2})\}')
_FIELD = re.compile(r'%[-0-9]*l?[sdu]')
_REC = re.compile(r'@(\d+)(?:\.(\d+))?\s*$')
# Line estimate (warning only; the window of a message is not known yet). Self-sizing
# windows wrap at vwf.MEASURE_W = 256 px.
WRAP_PX, MIN_LINES = 240, 4
VAR_PX, CALL_PX, FIELD_PX = 64, 40, 24     # guesses for {v..}, {C..}, %s/%u
BIO_BLOCKS, BIO_PX, BIO_LINES = range(1, 11), 256, 5   # officer bio window (printf_at 0x80084fec): an
# error, the 6th line would wrap over the 1st (window 33 x 10 cells at 0x80084f80; verified in DuckStation)
PREWRAP = {52: (208, 165)}                 # block -> (px, first entry): tutorial answers are paged by newlines
# (9 lines per page, Down scrolls; the questions before 52165 are list items)
VAR_B, CALL_B, FIELD_B = 16, 12, 8         # byte guesses for the expanded size
_W = None


# ---------------------------------------------------------------- Japanese side
seg_str = setup.seg_str


def jp_block(blocks, b):
    """{(id, n): (pieces, Japanese string)} of block b."""
    out = {}
    for i, e in enumerate(blocks[b]):
        n = 0
        for kind, v in msgfile.segments(e):
            if kind == 'seg':
                out[(b * 1000 + i, n)] = (v, seg_str(v))
                n += 1
    return out


def load_blocks():
    ext = os.path.join(setup.ROOT, 'extract')
    if not os.path.exists(os.path.join(ext, 'MESSAGE.BIN')):
        import iso
        disc = iso.Disc(setup.CLEAN)
        recs = disc.records()
        return msgfile.parse(disc.file(msgfile.NAME, recs), disc.file(iso.EXE_NAME, recs))
    return msgfile.parse(open(os.path.join(ext, 'MESSAGE.BIN'), 'rb').read(),
                         open(os.path.join(ext, 'SLPS_014.66'), 'rb').read())


# ---------------------------------------------------------------- tl files
def tl_path(b):
    return os.path.join(TL_MSG, '%02d.txt' % b)


def parse_tl(path):
    """-> {(id, n): English or '='}"""
    out, key, buf = {}, None, []

    def flush():
        if key is not None:
            out[key] = ' '.join(x.strip() for x in buf if x.strip())
    if not os.path.exists(path):
        return out
    for ln in open(path, encoding='utf-8'):
        ln = ln.rstrip('\n')
        if ln.startswith('#'):
            continue
        m = _REC.match(ln)
        if m:
            flush()
            key, buf = (int(m.group(1)), int(m.group(2) or 0)), []
            if key in out:
                raise ValueError('%s: @%d.%d twice' % (path, *key))
        elif key is not None:
            buf.append(ln)
        elif ln.strip():
            raise ValueError('%s: text before the first @ line: %r' % (path, ln))
    flush()
    return out


def write_tl(b, recs):
    os.makedirs(TL_MSG, exist_ok=True)
    with open(tl_path(b), 'w', encoding='utf-8') as f:
        for (mid, n) in sorted(recs):
            f.write('@%d%s\n%s\n\n' % (mid, '.%d' % n if n else '', recs[(mid, n)]))


def load_tls(only=None):
    return {b: parse_tl(tl_path(b)) for b in range(msgfile.NBLOCKS) if only is None or b == only}


# ---------------------------------------------------------------- English side
def encode(s):
    return s.replace('\\n', '\n').replace('\\s', ' ').encode('ascii')


def english_pieces(en):
    """English string -> segment pieces (text bytes / token bytes)."""
    out, pos = [], 0
    for m in _TOK.finditer(en):
        if m.start() > pos:
            out.append(('text', encode(en[pos:m.start()])))
        if m.group(1):
            v = int(m.group(1))
            out.append(('tok', bytes([0x01, 0x43, v & 0xFF, v >> 8])))
        else:
            out.append(('tok', bytes([0x02, int(m.group(2), 16), 0xC8])))
        pos = m.end()
    if pos < len(en) or not out:
        out.append(('text', encode(en[pos:])))
    return out


def tokens(s):
    return [m.group() for m in _TOK.finditer(s)]


def check_seg(jp, en):
    """Errors of one English segment against its Japanese string."""
    errs = []
    plain = _TOK.sub('', en)
    bad = sorted(set(c for c in plain.replace('\\n', '').replace('\\s', '') if not (' ' <= c <= '~') or c in '{}\\'))
    if bad:
        errs.append('characters not allowed: %s' % ''.join(bad))
    jf = _FIELD.findall(_TOK.sub('', jp))
    if _FIELD.findall(plain) != jf:
        errs.append('printf fields %s, Japanese has %s' % (_FIELD.findall(plain), jf))
    if '%' in _FIELD.sub('', plain):
        errs.append("bare '%' (write 'percent')")
    jt = tokens(jp)
    for t in set(tokens(en)):
        if en.count(t) > jt.count(t):
            errs.append('%s used %d times, Japanese has %d' % (t, en.count(t), jt.count(t)))
    if not errs:
        n = len(msgfile.seg_bytes(english_pieces(en)))
        if n > SEG_MAX:
            errs.append('%d bytes > %d' % (n, SEG_MAX))
    return errs


def expanded(s, english):
    """Rough expanded size in bytes (Japanese kana/kanji become 2-byte SJIS)."""
    n = len(tokens(s)) and sum(VAR_B if t[1] == 'v' else CALL_B for t in tokens(s))
    n += len(_FIELD.findall(s)) * FIELD_B
    rest = _FIELD.sub('', _TOK.sub('', s))
    if english:
        return n + len(rest.replace('\\n', '\n').replace('\\s', ' '))
    rest = re.sub(r'\{[0-9A-F]{2}\}.', '', rest)                   # ESC K/H
    return n + sum(1 if ' ' <= c <= '~' or c == '\n' else 2 for c in rest)


def est_lines(en, width=WRAP_PX):
    """Lines the in-game word wrap (tools/vwf.py) gives, inserts at guessed widths."""
    global _W
    if _W is None:
        _W = {c: w for c, (_, w) in pixfont.glyphs().items()}
    s = _TOK.sub('\x01', _FIELD.sub('\x02', en)).replace('\\n', '\n')
    size = lambda w: sum(VAR_PX if c == '\x01' else FIELD_PX if c == '\x02' else _W.get(c, 8) for c in w)
    lines = 0
    for para in s.split('\n'):
        lines += 1
        x = 0
        for i, w in enumerate(para.split(' ')):
            add = size(w) + (_W[' '] if i else 0)
            if i and x + add > width:
                lines, x = lines + 1, size(w)
            else:
                x += add
    return lines


def prewrap(en, width):
    """en with a newline at each in-game word-wrap break (est_lines' rule), for windows that page by newlines."""
    global _W
    if _W is None:
        _W = {c: w for c, (_, w) in pixfont.glyphs().items()}
    size = lambda w: sum(VAR_PX if t.group(0).startswith(('{C', '{v')) else FIELD_PX if t.group(0).startswith('%')
                         else _W.get(t.group(0), 8) for t in re.finditer(r'\{[Cv][0-9A-Fa-f]+\}|%[-0-9]*l?[sdu]|.', w))
    out = []
    for para in en.split('\\n'):
        lines, x = [[]], 0
        for i, w in enumerate(para.split(' ')):
            add = size(w) + (_W[' '] if i else 0)
            if i and x + add > width:
                lines.append([w]); x = size(w)
            else:
                lines[-1].append(w); x += add
        out.append('\\n'.join(' '.join(ln) for ln in lines))
    return '\\n'.join(out)


def jp_width(jp):
    """Widest line of a Japanese segment in px (16 per full-width character, 8 ASCII)."""
    s = _TOK.sub('\x01', text.readable(jp))
    return max(sum(VAR_PX if c == '\x01' else 8 if ' ' <= c <= '~' else 16 for c in ln)
               for ln in s.split('\n'))


def bio_errs(b, k, en):
    n = est_lines(en, BIO_PX) if b in BIO_BLOCKS and en != '=' else 0
    return ['@%d.%d: bio needs %d lines at %d px (max %d): shorten' % (*k, n, BIO_PX, BIO_LINES)] \
        if n > BIO_LINES else []


def warn_seg(jp, en):
    out = []
    w = max(WRAP_PX, jp_width(jp))
    n, jl = est_lines(en, w), jp.count('\n') + 1
    if n > max(MIN_LINES, jl):
        out.append('~%d lines at %d px (Japanese %d)' % (n, w, jl))
    e, j = expanded(en, True), expanded(jp, False)
    if e > max(EXPAND_MAX, j):
        out.append('~%d bytes expanded (Japanese ~%d, default buffer %d)' % (e, j, EXPAND_MAX))
    return out


def memory(jps, tls):
    """{Japanese string: English} from every explicit record."""
    tm = {}
    for b, recs in tls.items():
        for k, en in recs.items():
            if en != '=' and k in jps.get(b, {}):
                tm.setdefault(jps[b][k][1], en)
    return tm


def english_entries(blocks, tls=None):
    """Replace the translated segments in blocks (list of entry lists, in place).
    Returns (translated segment count, errors)."""
    tls = tls if tls is not None else load_tls()
    jps = {b: jp_block(blocks, b) for b in tls if tls[b]}
    tm = memory(jps, tls)
    errs, done = [], 0
    for b in range(msgfile.NBLOCKS):
        recs = tls.get(b, {})
        for k in recs:
            if k not in jps.get(b, {}):
                errs.append('@%d.%d: no such segment' % k)
        if not recs and not tm:
            continue
        for i, e in enumerate(blocks[b]):
            items = msgfile.segments(e)
            n, changed = 0, False
            for j, (kind, v) in enumerate(items):
                if kind != 'seg':
                    continue
                key = (b * 1000 + i, n)
                n += 1
                jp = seg_str(v)
                en = recs.get(key)
                if en is None:
                    en = tm.get(jp)
                if en is None or en == '=':
                    continue
                es = check_seg(jp, en)
                if es:
                    errs += ['@%d.%d: %s' % (*key, x) for x in es]
                    continue
                if b in PREWRAP and i >= PREWRAP[b][1]:
                    en = prewrap(en, PREWRAP[b][0])
                items[j] = ('seg', english_pieces(en))
                changed = True
                done += 1
            if changed:
                blocks[b][i] = msgfile.join_segments(items)
    return done, errs


# ---------------------------------------------------------------- commands
def joined(entry):
    """Indexes n of the segments that run straight on into segment n+1 (only a non-variant
    code between them)."""
    out, n, items = set(), 0, msgfile.segments(entry)
    for j, (kind, v) in enumerate(items):
        if kind != 'seg':
            continue
        if (j + 2 < len(items) and items[j + 1][0] == 'code' and items[j + 2][0] == 'seg'
                and not items[j + 1][1].startswith(b'\x05')):
            out.add(n)
        n += 1
    return out


def show(b, a=0, z=None, todo=False):
    blocks = load_blocks()
    jp = jp_block(blocks, b)
    joins = {(b * 1000 + i, n) for i, e in enumerate(blocks[b]) for n in joined(e)}
    tls = load_tls()
    recs = tls[b]
    tm = memory({c: jp if c == b else jp_block(blocks, c) for c in tls if tls[c]}, tls)
    calls = set()
    for (mid, n), (pieces, s) in sorted(jp.items()):
        i = mid % 1000
        if i < a or (z is not None and i > z):
            continue
        if todo and ((mid, n) in recs or s in tm):
            continue
        cont = '  [the next segment follows directly: use \\s for edge spaces]' if (mid, n) in joins else ''
        print('@%d%s\n%s%s\n' % (mid, '.%d' % n if n else '', text.readable(s).replace('\n', '\\n'), cont))
        calls.update(int(m.group(1)) for m in _TOK.finditer(s) if m.group(1))
    if calls:
        print('# calls:')
        j0 = jp_block(blocks, 0)
        for c in sorted(calls):
            segs = [s for (mid, n), (_, s) in sorted(j0.items()) if mid == c]
            print('#   {C%d} = %s' % (c, ' | '.join(text.readable(s).replace('\n', '\\n') for s in segs)[:200]))


def apply(b, batch):
    blocks = load_blocks()
    jp = jp_block(blocks, b)
    new = parse_tl(batch)
    errs = []
    for k, en in new.items():
        if k not in jp:
            errs.append('@%d.%d: no such segment in block %d' % (*k, b))
        elif en != '=':
            errs += ['@%d.%d: %s' % (*k, x) for x in check_seg(jp[k][1], en)] + bio_errs(b, k, en)
    if errs:
        sys.exit('\n'.join(errs) + '\n%d errors, nothing written' % len(errs))
    for k, en in new.items():
        if en != '=':
            for w in warn_seg(jp[k][1], en):
                print('warning @%d.%d: %s' % (*k, w))
    recs = parse_tl(tl_path(b))
    recs.update(new)
    write_tl(b, recs)
    print('block %d: %d records merged (%d in file)' % (b, len(new), len(recs)))


def check(only=None, warn=True):
    blocks = load_blocks()
    tls = load_tls(only)
    done, errs = english_entries(blocks, tls)
    errs += [e for b, recs in tls.items() for k, en in sorted(recs.items()) for e in bio_errs(b, k, en)]
    if warn:
        jps = {b: jp_block(blocks, b) for b in tls if tls[b]}
        for b, recs in tls.items():
            for k, en in sorted(recs.items()):
                if en != '=' and k in jps.get(b, {}):
                    for w in warn_seg(jps[b][k][1], en):
                        print('warning @%d.%d: %s' % (*k, w))
    blocks, _ = msgfile.split_blocks(blocks)
    errs += msgfile.check_sizes(blocks)
    print('\n'.join(errs))
    print('%d segments translated, %d errors' % (done, len(errs)))
    return not errs


def stats():
    blocks = load_blocks()
    tls = load_tls()
    jps = {b: jp_block(blocks, b) for b in range(msgfile.NBLOCKS)}
    tm = memory(jps, tls)
    tot = done = 0
    for b in range(msgfile.NBLOCKS):
        n = len(jps[b])
        d = sum(1 for k, (_, s) in jps[b].items() if k in tls[b] or s in tm)
        tot, done = tot + n, done + d
        if n:
            print('block %02d: %5d / %5d' % (b, d, n))
    print('total   : %5d / %5d (%.1f%%)' % (done, tot, 100.0 * done / max(tot, 1)))


def main():
    a = sys.argv[1:]
    if not a:
        sys.exit(__doc__)
    if a[0] == 'show':
        nums = [int(x) for x in a[2:] if not x.startswith('--')]
        show(int(a[1]), *(nums + [0, None])[:2], todo='--todo' in a)
    elif a[0] == 'apply':
        apply(int(a[1]), a[2])
    elif a[0] == 'check':
        sys.exit(0 if check(int(a[1]) if len(a) > 1 else None) else 1)
    elif a[0] == 'stats':
        stats()
    else:
        sys.exit(__doc__)


if __name__ == '__main__':
    main()
