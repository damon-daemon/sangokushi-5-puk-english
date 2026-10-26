"""Package a release: release/<Name>_v<version>.zip with the xdelta patch, a .cue and README.txt.

usage: .venv/bin/python tools/package.py 0.9.0

Run tools/build.py first. The patch turns the clean track 1 into the English track 1;
tracks 2-8 (CD audio) are not changed, so the zip carries a .cue that pairs the patched
track 1 with the player's own tracks 2-8. README.txt is generated from README.md (the
notices, the "What's translated", "Known issues" and "Credits" sections) plus patching
instructions and checksums. No disc data is ever packaged.
"""
import hashlib, os, re, sys, textwrap, zipfile, zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TITLE = 'Sangokushi V with Power-Up Kit - English Translation'
ZIP_NAME = 'Sangokushi_V_PUK_English'
CLEAN = 'sangokushi_5_puk_track1.bin'
AUDIO = ['sangokushi_5_puk_track%d.bin' % n for n in range(2, 9)]
BUILT = os.path.join('build', 'sangokushi_5_puk_en_track1.bin')
PATCH = os.path.join('build', 'sangokushi_5_puk_en.xdelta')
GAME_CODE = 'SLPS-01466'
# Redump file names of the Japanese disc, and the names the patched disc gets
JP_TRACK = 'Sangokushi V with Power-Up Kit (Japan) (Track %d).bin'
JP_TRACK1 = JP_TRACK % 1
# (the version goes in with % version)
EN_TRACK1 = 'Sangokushi V with Power-Up Kit (English v%s) (Track 1).bin'
EN_CUE = 'Sangokushi V with Power-Up Kit (English v%s).cue'


def sums(path):
    """Checksums, read in chunks."""
    md5, sha1, crc, n = hashlib.md5(), hashlib.sha1(), 0, 0
    with open(path, 'rb') as f:
        while chunk := f.read(1 << 24):
            md5.update(chunk); sha1.update(chunk); crc = zlib.crc32(chunk, crc); n += len(chunk)
    return [('Size', '{:,} bytes'.format(n)), ('CRC32', '%08X' % (crc & 0xFFFFFFFF)),
            ('MD5', md5.hexdigest()), ('SHA-1', sha1.hexdigest())]


def section(md, name):
    m = re.search(r'^## %s\n(.*?)(?=^## |\Z)' % re.escape(name), md, re.S | re.M)
    return m.group(1).strip() if m else ''


def plain(text):
    """Markdown -> readable plain text (bold, code, links, quotes)."""
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'(?<!\w)\*(.+?)\*(?!\w)', r'\1', text)
    text = re.sub(r'`([^`]*)`', r'\1', text)
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'\1 (\2)', text)
    text = re.sub(r'^> ?', '', text, flags=re.M)
    return text


def heading(t):
    return '%s\n%s\n' % (t, '-' * len(t))


def cue(en_track1):
    return ('FILE "%s" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n' % en_track1 +
            ''.join('FILE "%s" BINARY\n  TRACK %02d AUDIO\n    INDEX 00 00:00:00\n    INDEX 01 00:02:00\n'
                    % (JP_TRACK % n, n) for n in range(2, 9)))


def main():
    if len(sys.argv) != 2:
        sys.exit('usage: tools/package.py VERSION   (e.g. 0.9.0)')
    version = sys.argv[1]
    clean, built, patch = (os.path.join(ROOT, p) for p in (CLEAN, BUILT, PATCH))
    audio = [os.path.join(ROOT, p) for p in AUDIO]
    for p in [clean, built, patch] + audio:
        if not os.path.exists(p):
            sys.exit('missing %s (run tools/build.py first)' % p)
    if os.path.getmtime(patch) < os.path.getmtime(built):
        sys.exit('patch is older than the built image; re-run tools/build.py')
    md = open(os.path.join(ROOT, 'README.md'), encoding='utf-8').read()
    patch_name = 'sangokushi_5_puk_en_v%s.xdelta' % version
    en_track1, en_cue = EN_TRACK1 % version, EN_CUE % version
    table = lambda rows: '\n'.join('  %-6s %s' % r for r in rows)

    out = [TITLE, 'Version %s' % version, '=' * max(len(TITLE), 20), '']
    for pat in (r'^> \*\*This is a full machine translation.*?(?=\n\n)', r'^> \*\*This is a beta.*?(?=\n\n)'):
        m = re.search(pat, md, re.S | re.M)
        if m:
            out += [textwrap.fill(' '.join(plain(m.group(0)).split()), 78), '']
    out += [heading("What's translated"), plain(section(md, "What's translated")), '']
    out += [heading('How to patch'),
            'You need an unmodified image of the Japanese disc (serial %s), in the' % GAME_CODE,
            'usual eight-track BIN/CUE form (Redump). Only track 1 is patched:', '',
            '  %s' % JP_TRACK1, table(sums(clean)), '',
            'Apply %s to it with an xdelta tool, e.g. Delta Patcher' % patch_name,
            '(https://github.com/marco-calautti/DeltaPatcher), or on the command line:', '',
            '  xdelta3 -d -s "%s" %s "%s"' % (JP_TRACK1, patch_name, en_track1), '',
            'The patched track 1 should match:', '', table(sums(built)), '',
            'Tracks 2-8 (CD audio) are not changed:', '']
    for n, a in enumerate(audio, 2):
        out += ['  %s' % (JP_TRACK % n), table(sums(a)), '']
    out += [
            'Put "%s" (in this zip) in the same folder as' % en_cue,
            'the patched track 1 and the original tracks 2-8, and open the .cue in your emulator.', '']
    out += [heading('Known issues'), plain(section(md, 'Known issues')), '']
    out += [heading('Credits'), plain(section(md, 'Credits')), '']
    readme = '\n'.join(out).replace('\n\n\n', '\n\n')

    os.makedirs(os.path.join(ROOT, 'release'), exist_ok=True)
    zpath = os.path.join(ROOT, 'release', '%s_v%s.zip' % (ZIP_NAME, version))
    with zipfile.ZipFile(zpath, 'w', zipfile.ZIP_DEFLATED) as z:
        z.write(patch, patch_name)
        z.writestr(en_cue, cue(en_track1).replace('\n', '\r\n'))
        z.writestr('README.txt', readme.replace('\n', '\r\n'))
    print('%s (%s, %s, README.txt)' % (zpath, patch_name, en_cue))


if __name__ == '__main__':
    main()
