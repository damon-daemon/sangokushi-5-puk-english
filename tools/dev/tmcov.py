"""Survey: how much of MESSAGE.BIN the DS 3 translation (jp+en pairs) covers."""
import glob, json, re, sys, collections
import os; sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import msgfile, text

DS3 = '/home/leto2/Projects/sangokushi_ds_3_translation/script/'

def norm(s):
    s = re.sub(r'\{[0-9A-Fa-f]{2}\}', '', s)
    return re.sub(r'[\s\n。、！？…・「」]', '', s)

ds = {}
for f in sorted(glob.glob(DS3 + 'msgsec*.json')):
    for x in json.load(open(f)):
        # DS 3 variants are split by {05} codes too: index every piece
        for piece in re.split(r'(?:\{0[0-6]\}|\{[0-9A-F]{2}\}[^{]{0,0})+', x['jp']):
            k = norm(piece)
            if len(k) >= 2: ds.setdefault(k, (f.split('/')[-1], x['id']))
        ds.setdefault(norm(x['jp']), (f.split('/')[-1], x['id']))

d = open('extract/MESSAGE.BIN', 'rb').read(); e = open('extract/SLPS_014.66', 'rb').read()
hit = miss = hitc = missc = 0; perblk = collections.defaultdict(lambda: [0, 0])
for b, ents in enumerate(msgfile.parse(d, e)):
    for en in ents:
        for k, v in msgfile.segments(en):
            if k != 'seg': continue
            s = norm(''.join(text.decode(p) for t, p in v if t == 'text'))
            if not s: continue
            if s in ds: hit += 1; hitc += len(s); perblk[b][0] += 1
            else: miss += 1; missc += len(s); perblk[b][1] += 1
print('segments hit %d miss %d; chars hit %d miss %d (%.0f%%)' % (hit, miss, hitc, missc, 100 * hitc / (hitc + missc)))
print(' '.join('%d:%d/%d' % (b, h, h + m) for b, (h, m) in sorted(perblk.items())))

# fuzzy: 3-gram candidate index, difflib ratio on the best candidates
if '--fuzzy' in sys.argv:
    import difflib
    idx = collections.defaultdict(set); keys = list(ds)
    for i, k in enumerate(keys):
        for j in range(len(k) - 2): idx[k[j:j+3]].add(i)
    bands = collections.Counter(); bc = collections.Counter()
    for b, ents in enumerate(msgfile.parse(d, e)):
        if 1 <= b <= 10: continue
        for en in ents:
            for k, v in msgfile.segments(en):
                if k != 'seg': continue
                s = norm(''.join(text.decode(p) for t, p in v if t == 'text'))
                if not s or s in ds: continue
                cnt = collections.Counter(i for j in range(len(s) - 2) for i in idx.get(s[j:j+3], ()))
                best = max((difflib.SequenceMatcher(None, s, keys[i]).ratio() for i, _ in cnt.most_common(5)), default=0)
                band = '>=90' if best >= .9 else '>=75' if best >= .75 else '>=50' if best >= .5 else '<50'
                bands[band] += 1; bc[band] += len(s)
    print('fuzzy (non-bio misses):', dict(bands), 'chars', dict(bc))
