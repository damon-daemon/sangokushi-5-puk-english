"""Create the working folder script/ from your own disc image.

script/ holds the Japanese text extracted from the disc next to the English, so it is
generated on each machine and never distributed. Run this once after cloning (and
again after editing tl/), then tools/build.py.

usage: .venv/bin/python tools/setup.py [track1.bin]
"""
import hashlib, json, os, sys

sys.path.insert(0, os.path.dirname(__file__))
import iso, msgfile, text

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, 'script')
TL = os.path.join(ROOT, 'tl')
CLEAN = os.path.join(ROOT, 'sangokushi_5_puk_track1.bin')
CLEAN_SHA1 = '74744d56f1f5ac9fe80696a052df1776b026e97e'
# CD audio tracks 2-8: copied next to the rebuilt track 1, never patched
AUDIO = [os.path.join(ROOT, 'sangokushi_5_puk_track%d.bin' % t) for t in range(2, 9)]
AUDIO_SHA1 = ['5c27e181b1670af0e8c55b11eb596722c9282617', 'b4c00b36c0598582c9b014c150979f9cf2beff02',
              '0c4f6f633911f26b97cd1353891c2b09edd38f6e', '079395163385f9296e8a558dca39c54e2a1869ce',
              '2cc0319c67056601be8416b56b04dcec0b0584df', 'cc9c7980fc0fdeef22b763470d360b88c68a1cdc',
              'e777b203b09da5ade6e752fc01bb0c02b5ae14c0']


def sha1(path):
    h = hashlib.sha1()
    with open(path, 'rb') as f:
        while chunk := f.read(1 << 24):
            h.update(chunk)
    return h.hexdigest()


def check_clean(path, want=CLEAN_SHA1):
    digest = sha1(path)
    if digest != want:
        sys.exit('%s: SHA-1 %s does not match the clean dump (%s)' % (path, digest, want))


def tok_str(b):
    """Inline token of a segment: {C<id>} insert block-0 entry, {vXX} print variable XX."""
    if b[0] == 0x01:
        return '{C%d}' % (b[2] | b[3] << 8)
    return '{v%02X}' % b[1]


def seg_str(pieces):
    return ''.join(text.decode(v) if k == 'text' else tok_str(v) for k, v in pieces)


def msg_json(b):
    return os.path.join(SCRIPT, 'msg', 'block%02d.json' % b)


def export_messages(disc, recs):
    """script/msg/blockNN.json = [{'id', 'jp': [segments]}] (entries with text only)."""
    os.makedirs(os.path.join(SCRIPT, 'msg'), exist_ok=True)
    blocks = msgfile.parse(disc.file(msgfile.NAME, recs), disc.file(iso.EXE_NAME, recs))
    total = 0
    for b, ents in enumerate(blocks):
        rows = []
        for i, e in enumerate(ents):
            segs = [seg_str(v) for k, v in msgfile.segments(e) if k == 'seg']
            if segs:
                rows.append({'id': b * 1000 + i, 'jp': segs})
                total += len(segs)
        with open(msg_json(b), 'w', encoding='utf-8') as f:
            json.dump(rows, f, ensure_ascii=False, indent=0)
    return total


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else CLEAN
    check_clean(path)
    if path == CLEAN:
        for p, want in zip(AUDIO, AUDIO_SHA1):
            check_clean(p, want)
    disc = iso.Disc(path)
    recs = disc.records()
    os.makedirs(SCRIPT, exist_ok=True)
    print('messages: %d segments' % export_messages(disc, recs))
    print('script/ ready; now run tools/build.py')


if __name__ == '__main__':
    main()
