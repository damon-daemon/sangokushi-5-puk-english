"""Rebuild track 1 of the disc with the translation and produce a patch.

Output: build/sangokushi_5_puk_en_track1.bin, copies of the audio tracks 2-8 and a .cue
next to them (boots in an emulator), and build/sangokushi_5_puk_en.xdelta (clean track 1
-> English track 1; the audio tracks are not changed).

usage: .venv/bin/python tools/build.py [clean_track1.bin] [out_track1.bin]
"""
import os, shutil, subprocess, sys

sys.path.insert(0, os.path.dirname(__file__))
import gfx, iso, kanji, layout, msgfile, namedict, nameentry, names, progstr, setup, tl, vwf

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'build', 'sangokushi_5_puk_en_track1.bin')
FIXED = (iso.EXE_NAME,)                  # must keep their place


def build_files(disc, recs):
    """{iso path: bytes} of every rebuilt file."""
    exe = bytearray(disc.file(iso.EXE_NAME, recs))
    msg = disc.file(msgfile.NAME, recs)
    blocks = msgfile.parse(msg, exe)
    done, errs = tl.english_entries(blocks)
    if errs:
        sys.exit('tl/msg:\n' + '\n'.join(errs))
    print('messages: %d segments in English' % done)
    blocks, splits = msgfile.split_blocks(blocks)
    bad = msgfile.check_sizes(blocks)
    if bad:
        sys.exit('MESSAGE.BIN:\n' + '\n'.join(bad))
    data, offs = msgfile.build(blocks, msgfile.raw_blocks(msg, exe), msgfile.BLOCK_MAX)
    heap = kanji.apply(exe)
    hooks = vwf.apply(exe, heap)
    layout.apply(exe, heap)
    print('name entry: %d bytes of code' % nameentry.apply(exe, heap))
    try:
        pairs = names.pairs(disc, recs)
    except ValueError as e:
        sys.exit('tl/names:\n%s' % e)
    d, lab = namedict.apply(exe, heap, pairs, hooks)
    layout.apply_labels(exe, heap, lab['meas_hook'], lab['expand'], hooks['widths'])
    print('names: %d in the dictionary' % len(d.ent))
    clean = {'PROG': disc.file(iso.EXE_NAME, recs)}
    files = {'PROG': exe}
    n, moved = progstr.apply(files, clean, heap)
    print('program strings: %d in English (%d moved to free RAM)' % (n, moved))
    tab = msgfile.relayout(exe, heap, offs, splits)
    print('messages: %d blocks, table %08x%s' % (len(blocks), tab, ''.join(
        ', block %d cut at %d -> %d' % s for s in splits)))
    used, total = heap.used()
    print('free RAM (font area): %d of %d bytes used' % (used, total))
    try:
        art = gfx.apply(lambda p: disc.file(p, recs))
    except ValueError as e:
        sys.exit('tl/gfx: %s' % e)
    print('graphics: %s' % ', '.join(sorted(art)))
    return {iso.EXE_NAME: bytes(exe), msgfile.NAME: data, **art}


def write(clean, out, files):
    os.makedirs(os.path.dirname(out), exist_ok=True)
    moved = iso.write_image(clean, out, files)
    print('rebuilt %s%s' % (out, ' (moved: %s)' % ', '.join(moved) if moved else ''))
    if set(moved) & set(FIXED):
        sys.exit('%s moved on the disc' % ', '.join(set(moved) & set(FIXED)))
    iso.verify(out, files)
    audio = []
    for t, src in enumerate(setup.AUDIO, 2):
        dst = out.replace('_track1.bin', '_track%d.bin' % t)
        if not os.path.exists(dst) or os.path.getsize(dst) != os.path.getsize(src):
            shutil.copyfile(src, dst)
        audio.append(dst)
    iso.write_cue(out.replace('_track1.bin', '.cue'), out, audio)
    patch = os.path.join(os.path.dirname(out), 'sangokushi_5_puk_en.xdelta')
    try:
        subprocess.run(['xdelta3', '-e', '-f', '-9', '-S', 'djw', '-B', str(1 << 28), '-s', clean, out, patch], check=True)
        print('patch %s (%d bytes)' % (patch, os.path.getsize(patch)))
    except FileNotFoundError:
        print('xdelta3 not installed; skipped patch')
    return moved


def main():
    clean = sys.argv[1] if len(sys.argv) > 1 else setup.CLEAN
    out = sys.argv[2] if len(sys.argv) > 2 else OUT
    disc = iso.Disc(clean)
    files = build_files(disc, disc.records())
    write(clean, out, files)


if __name__ == '__main__':
    main()
