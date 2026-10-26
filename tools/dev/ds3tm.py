"""Import the DS 3 English (our own earlier work) as tl/msg records: translation memory.

DS 3 (~/Projects/sangokushi_ds_3_translation/script/msgsecNN.json) is a port of this game
with the same message texts but its own bytecode (no 01 22 text runs; {02}{XX}ﾈ names,
{01}C<x>{00} block-0 inserts, other {XX} codes structural with short ASCII operands).
Each DS 3 entry is cut at its structural codes; the en string has the same codes in the same
order, so its pieces line up with the jp pieces. A S5 segment whose normalised Japanese
equals a DS 3 jp piece takes that piece's English, tokens renamed positionally
({02}{XX}ﾈ -> {vYY}, {01}C..{00} -> {C<id>} as in the S5 segment).

usage: tools/dev/ds3tm.py [--write]     # stats; --write merges exact hits into tl/msg
       (records already in tl/msg are kept; list of imported keys -> notes/ds3tm.json)
"""
import collections, glob, json, os, re, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import msgfile, setup, tl

DS3 = os.path.expanduser('~/Projects/sangokushi_ds_3_translation/script/')
VAR = re.compile(r'\{02\}\{([0-9A-F]{2})\}[a-z]?ﾈ')
SPEECH = re.compile(r'\{01\}C(\{[0-9A-F]{2}\}|[^{])\{00\}')
GAIJI = re.compile(r'\{F[A-C]\}(?:\{[0-9A-F]{2}\}|[^{])')
DROP = re.compile(r'\{1B\}C\d|\{1B\}[KH]')
ARG = r'(?:\{[0-9A-F]{2}\}|[ -~])'
# a structural code with its operands (as DS 3 tl.py codes_outside_inline / header)
STRUCT = re.compile(r'\{01\}[A-Z]%s?%s?|\{0[24679]\}%s?%s?|\{[0-9A-F]{2}\}' % ((ARG,) * 4))


PH = r'[\U000F0000-\U000FFFFD]'     # placeholders of inline tokens


def inline_sub(s):
    """DS 3 string -> (string with inline tokens as placeholders, [(kind, value)])."""
    toks = []

    def inline(m):
        if m.re is VAR:
            toks.append(('v', m.group(1)))
        else:
            g = m.group(1)
            toks.append(('C', int(g[1:3], 16) if g.startswith('{') else ord(g)))
        return chr(0xF0000 + len(toks) - 1)
    s = GAIJI.sub('\ue0ff', DROP.sub('', s))
    return SPEECH.sub(inline, VAR.sub(inline, s)), toks


def pieces(s, toks, chunks):
    """Cut s at the structural chunks (in order); None if one is missing."""
    out, pos = [], 0
    for c in chunks:
        i = s.find(c, pos)
        if i < 0:
            c = STRUCT.match(c).group()[:4]           # the bare code; operands differ
            i = s.find(c, pos)
            if i < 0:
                return None
        out.append(s[pos:i])
        pos = i + len(c)
    out.append(s[pos:])
    return [(p, [toks[ord(ch) - 0xF0000] for ch in p if ord(ch) >= 0xF0000]) for p in out]


def ds3_pairs(jp, en):
    """[(jp piece, jp tokens, en piece, en tokens)] or None."""
    js, jt = inline_sub(jp)
    es, et = inline_sub(en)
    chunks = STRUCT.findall(js)
    a, b = pieces(js, jt, chunks), pieces(es, et, chunks)
    if a is None or b is None:
        return None
    return [(x[0], x[1], y[0], y[1]) for x, y in zip(a, b)]


_PUNCT = re.compile(r'[\s\n。、！？…・「」　]')


def norm(s):
    """Japanese text only (tokens, codes, punctuation, spaces removed)."""
    s = re.sub(PH, '', s)
    s = re.sub(r'\{C\d+\}|\{v[0-9A-F]{2}\}|\{1B\}.|\{[0-9A-F]{2}\}', '', s)
    return _PUNCT.sub('', s)


def has_jp(s):
    return any(0x7F < ord(c) < 0xE000 or 0xF900 <= ord(c) < 0xF0000 for c in s)


def en_clean(p):
    return re.sub(r' {2,}', ' ', p.replace('\n', ' ')).strip()


def memory():
    """{normalised jp piece: (en piece with placeholders, jp tokens, source)}; conflicts dropped."""
    tm, bad = {}, set()
    for f in sorted(glob.glob(DS3 + 'msgsec*.json')):
        sec = os.path.basename(f)[:-5]
        for x in json.load(open(f, encoding='utf-8')):
            if not x.get('en'):
                continue
            pairs = ds3_pairs(x['jp'], x['en'])
            for jt, jtok, et, etok in pairs or ():
                k = norm(jt)
                if len(k) < 2 or not has_jp(jt) or has_jp(et):
                    continue
                if sorted(etok, key=str) and not set(map(str, etok)) <= set(map(str, jtok)):
                    continue
                v = (en_clean(marked(et, etok_all(x['en']))), jtok, '%s:%d' % (sec, x['id']))
                if k in tm and tm[k][0] != v[0]:
                    bad.add(k)
                tm.setdefault(k, v)
    return tm, bad


def etok_all(en):
    return inline_sub(en)[1]


def marked(p, toks):
    """Placeholders -> \x00<kind><value>\x00 (independent of the entry's token order)."""
    return re.sub(PH, lambda m: '\x00%s%s\x00' % toks[ord(m.group()) - 0xF0000], p)


def to_s5(en, jtok, s5jp):
    """Rename the DS 3 tokens to the S5 segment's (matched in order by kind; S5 {C..}
    without a DS 3 partner are dropped word endings); None if they do not line up."""
    s5tok, ren, i = tl.tokens(s5jp), {}, 0
    for kind, val in jtok:
        while i < len(s5tok) and s5tok[i][1] != kind:
            if s5tok[i][1] == 'v':
                return None
            i += 1
        if i == len(s5tok):
            return None
        if ren.setdefault('%s%s' % (kind, val), s5tok[i]) != s5tok[i]:
            return None
        i += 1
    if any(t[1] == 'v' for t in s5tok[i:]):
        return None
    fail = []

    def rename(m):
        if m.group(1) not in ren:
            fail.append(m.group(1))
        return ren.get(m.group(1), '')
    out = re.sub('\x00([^\x00]*)\x00', rename, en)
    if fail or any(ord(c) > 0x7E for c in out):
        return None
    return out.strip()


def main():
    tm, bad = memory()
    blocks = tl.load_blocks()
    tls = tl.load_tls()
    hits = collections.defaultdict(dict)
    n = miss = rej = skip = 0
    for b in range(msgfile.NBLOCKS):
        if 1 <= b <= 10:                        # bios: DS 3 rewrote them
            continue
        for k, (_, s) in tl.jp_block(blocks, b).items():
            key = norm(s)
            if not key:
                continue
            n += 1
            if key not in tm:
                miss += 1
                continue
            if key in bad:
                skip += 1
            en = to_s5(tm[key][0], tm[key][1], s)
            if en is None or tl.check_seg(s, en):
                rej += 1
                continue
            hits[b][k] = (en, tm[key][2])
    got = sum(len(h) for h in hits.values())
    print('segments %d: DS 3 hits %d (rejected %d: tokens/fields; %d with conflicting DS 3 English), miss %d'
          % (n, got, rej, skip, miss))
    if '--write' in sys.argv:
        log = {}
        for b, h in hits.items():
            recs = tls[b]
            new = {k: en for k, (en, _) in h.items() if k not in recs}
            recs.update(new)
            tl.write_tl(b, recs)
            log.update({'%d.%d' % k: h[k][1] for k in new})
        with open(os.path.join(setup.ROOT, 'notes', 'ds3tm.json'), 'w') as f:
            json.dump(log, f, indent=0, sort_keys=True)
        print('written: %d records' % len(log))


if __name__ == '__main__':
    main()
