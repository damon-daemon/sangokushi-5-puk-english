"""Point a save state taken on the clean disc at the current build.

DuckStation loads a state with the disc recorded in the state, not the one on the
command line, so a clean-disc state keeps reading the clean disc. This writes a copy of
the state whose disc is TEST_CUE (build/s5_en_test.cue: the build's .cue under a name as
long as the clean one, so the header's path is replaced in place), and writes that .cue.
Then: headless.sh build/s5_en_test.cue OUT_STATE, and inject.py (program + MESSAGE.BIN
position). Files read at boot that moved in the build, other than MESSAGE.BIN, are not fixed.

usage: .venv/bin/python tools/emu/restate.py STATE.sav [OUT.sav]   (OUT: build/states/en_<name>)
"""
import os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CLEAN_CUE = os.path.join(ROOT, 'sangokushi_5_puk.cue')
BUILT_CUE = os.path.join(ROOT, 'build', 'sangokushi_5_puk_en.cue')
TEST_CUE = os.path.join(ROOT, 'build', 's5_en_test.cue')
assert len(TEST_CUE) == len(CLEAN_CUE)


def main():
    src = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(
        os.path.dirname(src), 'en_' + os.path.basename(src))
    d = open(src, 'rb').read()
    old = CLEAN_CUE.encode()
    i = d.find(old, 0, 4096)
    if i < 0:
        sys.exit('%s: clean disc path not in the header' % src)
    open(out, 'wb').write(d[:i] + TEST_CUE.encode() + d[i + len(old):])
    with open(BUILT_CUE) as f, open(TEST_CUE, 'w') as g:
        g.write(f.read())
    print('%s -> %s (disc %s)' % (src, out, os.path.relpath(TEST_CUE, ROOT)))


if __name__ == '__main__':
    main()
