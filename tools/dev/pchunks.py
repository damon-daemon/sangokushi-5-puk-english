"""Cut the untranslated program strings (tools/progstr.py) into worker chunks notes/chunks/pNN.txt.

Lines in address order (neighbours are context: menus and messages sit together):
  KEY<TAB>room<TAB>F|M<TAB>jp px<TAB>Japanese          (\\n = line break)
  # ds3 NN%: English  <=  Japanese                    closest DS 3 pieces (strings of 4+ characters)
room = bytes free in place; F = must fit room; M = may be longer (moved to free RAM).
jp px = width of the widest Japanese line (16 per full-width character, 8 per half-width).

usage: tools/dev/pchunks.py [N]      # N chunks (default 2)
"""
import glob, os, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import chunks, ds3tm, progstr

OUT = chunks.OUT


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    tl = progstr.load_tl()
    rs = [r for r in progstr.rows() if r['ui']]
    res = progstr.resolved(tl, rs)
    todo = [r for r in rs if r['key'] not in tl and r['key'] not in res]
    idx = chunks.Index(chunks.ds3_pieces())
    for f in glob.glob(os.path.join(OUT, 'p*.txt')):
        os.remove(f)
    size = -(-len(todo) // n)
    sug = 0
    for ci in range(n):
        ch = todo[ci * size:(ci + 1) * size]
        lines = ['# progstr chunk p%02d: %d strings, %s .. %s' % (ci, len(ch), ch[0]['key'], ch[-1]['key']), '']
        for r in ch:
            jp = r['jp'].replace('\n', '\\n')
            lines.append('%s\t%d\t%s\t%d\t%s' % (r['key'], r['room'], 'F' if r['block'] else 'M',
                                                  progstr.px(r['jp'], True), jp))
            k = ds3tm.norm(r['jp'])
            if len(k) >= 4:
                for q, (_, jr, en) in chunks.Index.best(idx, k, 1):
                    lines.append('# ds3 %d%%: %s  <=  %s' % (q * 100, en, jr.replace('\n', '\\n')))
                    sug += 1
        with open(os.path.join(OUT, 'p%02d.txt' % ci), 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines) + '\n')
        print('p%02d: %d strings %s .. %s' % (ci, len(ch), ch[0]['key'], ch[-1]['key']))
    print('%d todo strings, %d DS 3 suggestions' % (len(todo), sug))


if __name__ == '__main__':
    main()
