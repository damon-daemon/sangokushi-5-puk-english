"""Put a new build's program into a running (state-loaded) headless DuckStation instead of booting.

A save state holds the RAM of the build it was taken on, program included. This halts the
CPU (GDB connect; reconnected until pc and ra are outside the text renderer and the freed
font area, so none of our hook code is running), then writes through the GDB server, where
RAM differs from the new build:
  - the freed font area (static at run time) in full,
  - every other program byte the build changes (code patches, tables), as whole words,
and continues. S5 has no overlays. When the emulator runs the build (a state made with
restate.py), the MESSAGE.BIN position read at boot is set to the build's and the loaded
block is dropped (the next message reloads it), and block 0 (loaded once at boot, the block-0 words)
is rewritten from the build's MESSAGE.BIN.

usage: .venv/bin/python tools/emu/inject.py [built_track1.bin]
"""
import os, sys, time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.dirname(__file__))
import iso, kanji, msgfile, setup
import gdbmem, ram as ramlib

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BUILT = os.path.join(ROOT, 'build', 'sangokushi_5_puk_en_track1.bin')
FONT = (kanji.CODES, kanji.AREA_END)
BUSY = [(0x80018860, 0x80019720), FONT]       # blits .. print_string; heap code
BASE = 0x80010000
MSG_LBA = 0x80142b78                         # MESSAGE.BIN LBA (CdSearchFile at boot)
MSG_BLOCK = 0x801429f0                       # gp+0x99c: block in the current-block buffer
LOG = os.path.join(ROOT, 'build', 'dsh', 'headless.log')


def on_build():
    """True when the running disc is not the clean one (the last media line of the log)."""
    media = [ln for ln in open(LOG, errors='replace') if 'media' in ln or 'Loading CD image' in ln]
    return bool(media) and os.path.basename(setup.CLEAN).replace('_track1.bin', '.cue') not in media[-1]


def ranges_diff(a, b, base):
    """[(va, va_end)] where a and b differ (runs merged when < 16 bytes apart)."""
    out, i, n = [], 0, min(len(a), len(b))
    while i < n:
        if a[i] != b[i]:
            j = i
            while j < n and (a[j] != b[j] or any(a[k] != b[k] for k in range(j, min(j + 16, n)))):
                j += 1
            out.append((base + i, base + j))
            i = j
        else:
            i += 1
    return out


def plan(mem, exe_c, exe_n):
    """[(va, bytes)] to write. mem = 2 MB RAM, exe_* = program images from 0x80010000."""
    want = {}
    for va in range(*FONT):
        want[va] = exe_n[va - BASE]
    for lo, hi in ranges_diff(exe_c, exe_n, BASE):
        for va in range(lo & ~3, (hi + 3) & ~3):
            want[va] = exe_n[va - BASE]
    out, run = [], None                         # runs where RAM differs, gaps < 32 bytes merged
    for va in sorted(want):
        if mem[va & 0x1FFFFF] == want[va]:
            continue
        if run and va - run[1] < 32 and all(v in want for v in range(run[1], va)):
            run[1] = va + 1
        else:
            run = [va, va + 1]
            out.append(run)
    # whole aligned words (DuckStation's GDB server does not answer unaligned writes)
    return [(lo & ~3, bytes(want.get(v, mem[v & 0x1FFFFF]) for v in range(lo & ~3, (hi + 3) & ~3)))
            for lo, hi in out]


def main():
    built = sys.argv[1] if len(sys.argv) > 1 else BUILT
    exe_c = iso.read_file(setup.CLEAN, iso.EXE_NAME)[0x800:]
    exe_n = iso.read_file(built, iso.EXE_NAME)[0x800:]
    for tries in range(50):                      # connecting halts the CPU: retry until it is
        g = gdbmem.G()                           # outside our hooks and the text renderer
        g.s.settimeout(5)
        g.cmd('?')
        r = gdbmem.regs(g)
        if not any(lo <= r[k] < hi for k in ('pc', 'r31') for lo, hi in BUSY):
            break
        g.resume()
        time.sleep(0.05)
    else:
        sys.exit('CPU always busy in the text code; try again')
    where = 'pc %08x ra %08x' % (r['pc'], r['r31'])
    writes = plan(ramlib.shm(), exe_c, exe_n)
    if on_build():
        lba = iso.Disc(built).records()['MESSAGE/MESSAGE.BIN'].lba
        block0 = iso.read_file(built, msgfile.NAME)[:msgfile.BLOCK0_MAX]
        writes += [(MSG_LBA, lba.to_bytes(4, 'little')), (MSG_BLOCK, b'\xff' * 4),
                   (msgfile.ARENA, block0)]
    else:
        print('emulator runs the clean disc: MESSAGE.BIN not switched (see restate.py)')
    total = 0
    for va, b in writes:
        for k in range(0, len(b), 0x400):
            part = b[k:k + 0x400]
            r = g.cmd('M%x,%x:%s' % (va + k, len(part), part.hex()))
            if r != 'OK':
                sys.exit('write %08x failed: %r' % (va + k, r))
        total += len(b)
    g.resume()
    print('halted at %s; wrote %d bytes in %d runs' % (where, total, len(writes)))


if __name__ == '__main__':
    main()
