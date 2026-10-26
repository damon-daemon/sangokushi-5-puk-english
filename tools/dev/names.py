"""Look up English names in the DS 3 tables (officers, items, titles, cities, stages).

usage: tools/dev/names.py find <japanese> [...]     # exact match first, then substring hits
Cities are DS 3's short slot forms (may be cut, e.g. "Xiangp"): use the full Pinyin in text.
"""
import json, os, sys

DS3 = os.path.expanduser('~/Projects/sangokushi_ds_3_translation/script/')
KINDS = ('officers', 'items', 'sp', 'cities', 'stages')


def table():
    return [(k, x['jp'], x['en']) for k in KINDS
            for x in json.load(open(DS3 + 'names_%s.json' % k, encoding='utf-8')) if x.get('en')]


def main():
    a = sys.argv[1:]
    if len(a) < 2 or a[0] != 'find':
        sys.exit(__doc__)
    t = table()
    for q in a[1:]:
        hits = [x for x in t if x[1] == q] or [x for x in t if q in x[1]][:10]
        print('%s: %s' % (q, ' | '.join('%s (%s)' % (e, k) for k, j, e in hits) if hits else 'not found'))


if __name__ == '__main__':
    main()
