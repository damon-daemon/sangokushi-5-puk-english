"""Cut the untranslated message segments into worker chunks with DS 3 fuzzy suggestions.

Each chunk file notes/chunks/cNN.txt holds ~SIZE todo segments in tl.py show format, plus
'# ds3 NN%: English  <=  Japanese' lines for the closest DS 3 pieces (>= MIN similarity),
so a worker can reuse DS 3 wording where the PS1 text only differs a little.
Bios (blocks 1-10) are left out; --bios writes them to notes/chunks/bNN.txt instead (blocks 1-5
and 6-10), each with its officer's name (scenario records) and no DS 3 suggestions (DS 3 has
different bios).

usage: tools/dev/chunks.py [SIZE] [--blocks A-B]     # default 500, blocks 11-56
       tools/dev/chunks.py --bios
"""
import collections, difflib, glob, json, os, re, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ds3tm, msgfile, setup, text, tl

MIN = 0.75
OUT = os.path.join(setup.ROOT, 'notes', 'chunks')


def ds3_pieces():
    """[(normalised jp, readable jp, en)] of every DS 3 piece with English."""
    out, seen = [], set()
    for f in sorted(glob.glob(ds3tm.DS3 + 'msgsec*.json')):
        for x in json.load(open(f, encoding='utf-8')):
            if not x.get('en'):
                continue
            etoks, jtoks = ds3tm.etok_all(x["en"]), ds3tm.inline_sub(x["jp"])[1]
            for jt, jtok, et, etok in ds3tm.ds3_pairs(x['jp'], x['en']) or ():
                k = ds3tm.norm(jt)
                if len(k) < 4 or not ds3tm.has_jp(jt) or ds3tm.has_jp(et) or k in seen:
                    continue
                seen.add(k)
                show = lambda s, t: re.sub(ds3tm.PH, lambda m: '{%s%s}' % t[ord(m.group()) - 0xF0000], s)
                jr, er = ds3tm.en_clean(show(jt, jtoks)), ds3tm.en_clean(show(et, etoks))
                m = re.match(r'\d+(?=\D)', jr)      # operand digits of the code before the piece
                if m and er.startswith(m.group()):
                    jr, er = jr[m.end():], er[m.end():]
                out.append((k, jr, er))
    return out


def bigrams(s):
    return {s[i:i + 2] for i in range(len(s) - 1)}


class Index:
    def __init__(self, pieces):
        self.p = pieces
        self.ix = collections.defaultdict(list)
        for i, (k, _, _) in enumerate(pieces):
            for g in bigrams(k):
                self.ix[g].append(i)

    def best(self, key, n=2):
        bg = bigrams(key)
        cnt = collections.Counter(i for g in bg for i in self.ix.get(g, ()))
        res = []
        for i, c in cnt.most_common(40):
            k = self.p[i][0]
            if 2.0 * c / (len(bg) + len(bigrams(k))) < MIN - 0.15:
                break
            r = difflib.SequenceMatcher(None, key, k, autojunk=False).ratio()
            if r >= MIN:
                res.append((r, i))
        return [(r, self.p[i]) for r, i in sorted(res, reverse=True)[:n]]


def bio_names():
    """{bio number n (id = 1000 * (n // 50 + 1) + n % 50): Japanese name} from every scenario."""
    import scen
    out = {}
    for f in sorted(glob.glob(os.path.join(setup.ROOT, 'extract', 'SAN5SN*.DAT'))):
        d = open(f, 'rb').read()
        p, n = scen.table(d, d.find('劉備'.encode('cp932')))
        for r in range(p, p + n * scen.REC, scen.REC):
            num = int.from_bytes(d[r + 30:r + 32], 'little')
            nm, rd = (text.readable(text.decode(re.sub(rb'\xEB.', '□'.encode('cp932'),   # gaiji
                                                       d[r + o:r + o + 9].split(b'\0')[0], flags=re.S)))
                      for o in (0, 9))
            out.setdefault(num, (nm, rd))
    return out


def bios():
    import names
    blocks, tls = tl.load_blocks(), tl.load_tls()
    nm, en = bio_names(), {}
    for k, j, e in names.table():
        if k == 'officers':
            en.setdefault(j, e)
    os.makedirs(OUT, exist_ok=True)
    for ci, (lo, hi) in enumerate(((1, 5), (6, 10))):
        lines, cnt = [], 0
        for b in range(lo, hi + 1):
            for k, (_, s) in sorted(tl.jp_block(blocks, b).items()):
                if k in tls[b]:
                    continue
                n = (b - 1) * 50 + k[0] % 1000
                j, rd = nm.get(n, ('', ''))
                who = ('%s (%s)' % (en[j], j) if j in en else
                       '%s, read %s (identify from the bio)' % (j, rd) if j else 'not in any scenario')
                lines += ['@%d%s' % (k[0], '.%d' % k[1] if k[1] else ''), '# officer: ' + who,
                          text.readable(s).replace('\n', ''), '']
                cnt += 1
        with open(os.path.join(OUT, 'b%02d.txt' % ci), 'w', encoding='utf-8') as f:
            f.write('# bio chunk b%02d: %d bios, blocks %02d-%02d\n\n' % (ci, cnt, lo, hi) + '\n'.join(lines))
        print('b%02d: %d bios, blocks %d-%d' % (ci, cnt, lo, hi))


def main():
    a = sys.argv[1:]
    if '--bios' in a:
        return bios()
    size = int(next((x for x in a if x.isdigit()), 500))
    lo, hi = 11, msgfile.NBLOCKS - 1
    if '--blocks' in a:
        lo, hi = map(int, a[a.index('--blocks') + 1].split('-'))
    blocks = tl.load_blocks()
    tls = tl.load_tls()
    jps = {b: tl.jp_block(blocks, b) for b in range(msgfile.NBLOCKS)}
    joins = {(b * 1000 + i, n) for b in range(lo, hi + 1) for i, e in enumerate(blocks[b]) for n in tl.joined(e)}
    tm = tl.memory(jps, tls)
    idx = Index(ds3_pieces())
    todo = []
    for b in range(lo, hi + 1):
        seen = set()
        for k, (_, s) in sorted(jps[b].items()):
            if k in tls[b] or s in tm or s in seen:
                continue
            seen.add(s)                       # same text twice in a block: tl.py's memory fills the copy
            todo.append((b, k, s))
    os.makedirs(OUT, exist_ok=True)
    for f in glob.glob(os.path.join(OUT, 'c*.txt')):
        os.remove(f)
    chunks, cur = [], []
    for t in todo:                            # cut at block boundaries when near the size
        if cur and (len(cur) >= size or (len(cur) >= size * 0.8 and t[0] != cur[-1][0])):
            chunks.append(cur)
            cur = []
        cur.append(t)
    if cur:
        chunks.append(cur)
    sug = 0
    for ci, ch in enumerate(chunks):
        lines = []
        for b, (mid, n), s in ch:
            lines.append('@%d%s' % (mid, '.%d' % n if n else ''))
            lines.append(text.readable(s).replace('\n', '\\n') + (
                '  [the next segment follows directly]' if (mid, n) in joins else ''))
            for r, (_, jr, en) in idx.best(ds3tm.norm(s)):
                lines.append('# ds3 %d%%: %s  <=  %s' % (r * 100, en, jr.replace('\n', '\\n')))
                sug += 1
            lines.append('')
        bl = sorted({b for b, _, _ in ch})
        head = '# chunk c%02d: %d segments, block%s %s\n\n' % (
            ci, len(ch), 's' if len(bl) > 1 else '', ', '.join('%02d' % b for b in bl))
        with open(os.path.join(OUT, 'c%02d.txt' % ci), 'w', encoding='utf-8') as f:
            f.write(head + '\n'.join(lines))
        print('c%02d: %4d segments, blocks %s' % (ci, len(ch), ','.join(map(str, bl))))
    print('%d todo segments in %d chunks, %d DS 3 suggestions' % (len(todo), len(chunks), sug))


if __name__ == '__main__':
    main()
