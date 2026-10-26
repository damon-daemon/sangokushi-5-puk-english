"""Names: the record tables keep their Japanese, the renderer shows English (tools/namedict.py).

Every other text is English by now, so whatever Japanese still reaches the screen comes from
a record table: officer names, cities, title prefixes, items, formations. The fields are
4-13 bytes (officers 9, cities 5), too small for English, so the records stay Japanese and a
Japanese -> English dictionary in free RAM replaces the names when they are drawn.

Tables (all found by scanning, see KINDS):
  officers    38-byte records (name 9, kana reading 9, data 20): SCENARIO/SAN5SN1..12.DAT
              (main table 0x56D4 + a second one after it) and copies in the program
  cities      program 0x801230a8, 32-byte records (name 5, reading 7); scenario 0xB3C (32 B)
  titles      program 0x801099b0, 8-byte records (name 5): prefixes, "将軍" is a progstr
  items       program 0x80109b68, 32-byte records (3 B, name 13, reading 16)
  formations  program 0x8010a7e8, 10-byte records (name 5)

tl/names/<kind>.txt: `id<TAB>English` (# comments); id = officer number (u16 at +30 of the
record, 0-499, the same in every table) or the record index of the other tables. No Japanese
in tl/: script/names/<kind>.txt (export) shows it; gaiji (EB9F-EBFC) are written {gXX} there.

  names.py export        script/names/<kind>.txt (japanese, reading, where; English if known)
  names.py check         validate tl/names against the disc (missing, unknown, conflicts)
  names.py pairs         print the japanese -> English pairs
  names.py find TEXT...  pairs whose Japanese or English contains TEXT
"""
import os, re, sys

sys.path.insert(0, os.path.dirname(__file__))
import iso, setup

PROG_BASE = 0x80010000 - 0x800
SCEN = ['SCENARIO/SAN5SN%d.DAT' % i for i in range(1, 13)]
OFF_REC = 38
CITIES = (0x801230a8, 32, 5, 47)                # address, stride, name field[, count]
TITLES = (0x801099b0, 8, 5)
ITEMS = (0x80109b6b, 32, 13)
FORMATIONS = (0x8010a7e8, 10, 5)
KINDS = ('officers', 'cities', 'titles', 'items', 'formations')
TL_NAMES = os.path.join(setup.TL, 'names')


def is_lead(c):
    return 0x81 <= c <= 0x9F or 0xE0 <= c <= 0xFC


def key(b):
    """Name bytes -> editable key ({gXX} for gaiji); None if not 2-byte characters only."""
    out = []
    for i in range(0, len(b), 2):
        p = b[i:i + 2]
        if len(p) < 2 or not is_lead(p[0]) or p[1] < 0x40:
            return None
        if p[0] == 0xEB:
            out.append('{g%02X}' % p[1])
            continue
        try:
            out.append(p.decode('cp932'))
        except UnicodeDecodeError:
            return None
    return ''.join(out)


def unkey(s):
    """Editable key -> name bytes."""
    out = bytearray()
    for m in re.finditer(r'\{g([0-9A-Fa-f]{2})\}|.', s):
        out += bytes([0xEB, int(m.group(1), 16)]) if m.group(1) else m.group().encode('cp932')
    return bytes(out)


def kana(b):
    return b.split(b'\0')[0].decode('cp932', 'replace')


def officer_ok(d, r):
    if r < 0 or r + OFF_REC > len(d):
        return False
    nm, rd = d[r:r + 9], d[r + 9:r + 18]
    n, k = nm.split(b'\0')[0], rd.split(b'\0')[0]
    return (2 <= len(n) <= 8 and key(n) is not None and nm[len(n):].strip(b'\0') == b''
            and len(k) >= 1 and all(0xA1 <= c <= 0xDF for c in k))


def officer_tables(d):
    """[(offset, count)] of runs of >= 4 officer records."""
    out, i = [], 0
    while i < len(d) - OFF_REC:
        if officer_ok(d, i) and officer_ok(d, i + OFF_REC):
            j = i
            while officer_ok(d, j + OFF_REC):
                j += OFF_REC
            n = (j - i) // OFF_REC + 1
            if n >= 4:
                out.append((i, n))
                i = j + OFF_REC
                continue
        i += 1
    return out


def fixed_table(d, base, addr, stride, field, count=None):
    """Records with a 2-byte-character name at the start of each field, until the first miss."""
    out, a = [], addr
    while True:
        f = d[a - base:a - base + field]
        n = f.split(b'\0')[0]
        if not n or key(n) is None or len(out) == count:
            return out
        out.append((a, n, kana(d[a - base + field:a - base + stride])))
        a += stride


def collect(disc, recs=None):
    """{kind: {id: {'jp': name bytes, 'kana': reading, 'at': [places]}}}; officers by officer
    number (u16 at +30 of the record, 0-499: the same in every table), the rest by index."""
    recs = recs or disc.records()
    prog = disc.file(iso.EXE_NAME, recs)
    out = {k: {} for k in KINDS}

    def add(kind, i, n, rd, where):
        e = out[kind].setdefault(i, {'jp': n, 'kana': rd, 'at': []})
        if e['jp'] != n:
            raise ValueError('%s %d: %s at %s, %s at %s' % (kind, i, key(e['jp']), e['at'][0], key(n), where))
        e['kana'] = e['kana'] or rd
        e['at'].append(where)

    for tag, d in [(name[-5:-4] if name[-6] == 'N' else name[-6:-4], disc.file(name, recs)) for name in SCEN] + \
            [('PROG', prog)]:
        for off, n in officer_tables(d):
            for i in range(n):
                r = off + i * OFF_REC
                add('officers', d[r + 30] | d[r + 31] << 8, d[r:r + 9].split(b'\0')[0], kana(d[r + 9:r + 18]),
                    '%s:%x' % (tag, r + (PROG_BASE if tag == 'PROG' else 0)))
    for kind, t in (('cities', CITIES), ('titles', TITLES), ('items', ITEMS), ('formations', FORMATIONS)):
        for i, (a, n, rd) in enumerate(fixed_table(prog, PROG_BASE, *t)):
            add(kind, i, n, rd if kind in ('cities', 'items') else '', 'PROG:%x' % a)
    return out


def load(kind):
    """[(line number, id, english)] of tl/names/<kind>.txt"""
    path = os.path.join(TL_NAMES, kind + '.txt')
    if not os.path.exists(path):
        return []
    rows = []
    for n, line in enumerate(open(path, encoding='utf-8'), 1):
        line = line.rstrip('\n')
        if not line.strip() or line.startswith('#'):
            continue
        i, _, e = line.partition('\t')
        rows.append((n, int(i) if i.strip().isdigit() else i, e.strip()))
    return rows


GENERAL = '将軍'.encode('cp932')       # PROG:80142100, kept Japanese (tl/progstr '-')


def pairs(disc, recs=None, quiet=True):
    """{japanese bytes: english bytes}; raises ValueError on errors."""
    names = collect(disc, recs)
    errs, out, src = [], {}, {}
    for kind in KINDS:
        have = set()
        for n, i, e in load(kind):
            where = '%s.txt:%d' % (kind, n)
            if i not in names[kind]:
                errs.append('%s: no %s %r' % (where, kind[:-1], i))
                continue
            if i in have:
                errs.append('%s: %s %d twice' % (where, kind[:-1], i))
            if not e or not e.isascii() or '%' in e or '\t' in e:
                errs.append('%s: bad English %r' % (where, e))
                continue
            b = names[kind][i]['jp']
            if b in out and out[b] != e.encode():
                errs.append('%s: %s = %r, but %s says %r' % (where, key(b), e, src[b], out[b].decode()))
            out[b], src[b] = e.encode(), where
            have.add(i)
        miss = sorted(set(names[kind]) - have)
        if miss:
            errs.append('%s: %d without English: %s' % (kind, len(miss), ' '.join(map(str, miss[:20]))))
    if errs:
        raise ValueError('\n'.join(errs))
    # a title is prefix + GENERAL in one string (0x80044950..): match the whole title so the
    # Japanese width (8 bytes, the layout's column) stays; the English holds "Gen" where it fits
    for n, i, e in load('titles'):
        out[names['titles'][i]['jp'] + GENERAL] = e.encode()
    out[GENERAL] = b'Gen'
    return out


def export(disc):
    names = collect(disc)
    en = {k: {i: e for _, i, e in load(k)} for k in KINDS}
    d = os.path.join(setup.SCRIPT, 'names')
    os.makedirs(d, exist_ok=True)
    for kind in KINDS:
        with open(os.path.join(d, kind + '.txt'), 'w', encoding='utf-8') as f:
            f.write('# id\tjapanese\treading\tEnglish\twhere\n')
            for i, e in sorted(names[kind].items()):
                f.write('%d\t%s\t%s\t%s\t%s\n' % (i, key(e['jp']), e['kana'], en[kind].get(i, ''),
                                                  ' '.join(e['at'][:6])))
        print('%-10s %d' % (kind, len(names[kind])))


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'check'
    disc = iso.Disc(setup.CLEAN)
    if cmd == 'export':
        export(disc)
    elif cmd == 'check':
        try:
            p = pairs(disc, quiet=False)
        except ValueError as e:
            sys.exit(str(e))
        print('%d name pairs OK' % len(p))
    elif cmd in ('pairs', 'find'):
        try:
            p = pairs(disc)
        except ValueError as e:
            sys.exit(str(e))
        q = sys.argv[2:]
        for j, e in sorted((key(j), e.decode()) for j, e in p.items()):
            if cmd == 'pairs' or any(x in j or x.lower() in e.lower() for x in q):
                print('%s\t%s' % (j, e))
    else:
        sys.exit(__doc__)


if __name__ == '__main__':
    main()
