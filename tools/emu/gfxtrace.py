"""gfxtrace.py SECONDS [keys...]: log cd_read and LoadImage calls while pressing keys (hpress.sh).

cd_read(lba, off, dst, size) 0x800c5a70 -> file name from the ISO listing (clean disc LBAs);
LoadImage(rect*, src) 0x800e0fdc -> VRAM rect. One line per call, appended to build/gfxtrace.log.
Run from the project root with the headless emulator running (clean disc for file names).
"""
import os, re, struct, subprocess, sys, threading, time
sys.path.insert(0, 'tools/emu'); import gdbmem

CD_READ, LOAD_IMAGE = 0x800c5a70, 0x800e0fdc
LOG = 'build/gfxtrace.log'


def files():
    out = []
    for line in open('notes/jp_files.txt', encoding='utf-8'):
        m = re.match(r'\s*(\d+)\s+([\d,]+)\s+(\S+)', line)
        if m:
            out.append((int(m.group(1)), int(m.group(2).replace(',', '')), m.group(3)))
    return out


def name(fl, lba):
    for l, size, n in fl:
        if l <= lba < l + (size + 2047) // 2048:
            return '%s+%#x' % (n, (lba - l) * 2048)
    return 'lba %d' % lba


def main():
    secs = float(sys.argv[1]); keys = sys.argv[2:]
    fl = files()
    g = gdbmem.G()
    for a in (CD_READ, LOAD_IMAGE):
        g.cmd('Z0,%x,4' % a)
    if keys:
        threading.Timer(0.5, lambda: subprocess.run(['tools/emu/hpress.sh', '/dev/null'] + keys)).start()
    end = time.time() + secs
    log = open(LOG, 'a')
    g.s.settimeout(1)
    while time.time() < end:
        g.s.sendall(b'$c#63')
        try:
            g._recv()
        except Exception:
            # still running: halt to keep the protocol in step
            g.s.sendall(b'\x03')
            g.s.settimeout(5); g._recv(); g.s.settimeout(1)
            continue
        r = gdbmem.regs(g)
        if r['pc'] == CD_READ:
            off = r['r5']
            s = 'cd_read %s off=%#x dst=%08x size=%#x' % (name(fl, r['r4'] + off // 2048), off % 2048, r['r6'], r['r7'])
        elif r['pc'] == LOAD_IMAGE:
            x, y, w, h = struct.unpack('<4h', g.read(r['r4'], 8))
            if r['r31'] in (0x800639d0, 0x80018a30, 0x80018b00):  # FMV frames, font cells
                continue
            s = 'LoadImage x=%d y=%d w=%d h=%d src=%08x ra=%08x' % (x, y, w, h, r['r5'], r['r31'])
        else:
            continue
        print(s); log.write(s + '\n'); log.flush()
    for a in (CD_READ, LOAD_IMAGE):
        g.cmd('z0,%x,4' % a)
    g.resume()


if __name__ == '__main__':
    main()
