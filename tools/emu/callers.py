"""callers.py [@TRIGGER] ADDR SECONDS [keys...]: (after the first call of TRIGGER, if given) count the distinct return addresses of calls to ADDR (hex)
for SECONDS while pressing keys (hpress.sh); prints ra, count and the first hit's a0-a3 and the
string at a1 (if it points to RAM). Breakpoint set once, always cleared at the end.
Run from the project root with the headless emulator running."""
import collections, socket, subprocess, sys, threading, time
sys.path.insert(0, 'tools/emu'); import gdbmem
trig = int(sys.argv.pop(1)[1:], 16) if sys.argv[1].startswith('@') else None
addr, secs, keys = int(sys.argv[1], 16), float(sys.argv[2]), sys.argv[3:]
g = gdbmem.G()
g.cmd('z0,%x,4' % addr)
if keys:
    threading.Timer(0.5, lambda: subprocess.run(['tools/emu/hpress.sh', '/dev/null'] + keys)).start()
if trig is not None:
    pkt, r = gdbmem.wait_break(g, trig, timeout=30)
    print('trigger ra=%08x a0=%08x' % (r['r31'], r['r4']))
seen, first = collections.Counter(), {}
g.cmd('Z0,%x,4' % addr)
end = time.time() + secs
try:
    while time.time() < end:
        g.s.settimeout(max(0.5, end - time.time()))
        g.s.sendall(b'$c#63')
        try:
            g._recv()
        except socket.timeout:
            g.s.sendall(b'\x03'); g._recv(); break        # interrupt: CPU halted again
        r = gdbmem.regs(g)
        k = r['r31']; seen[k] += 1
        if k not in first:
            s = b''
            if 0x80000000 <= r['r5'] < 0x80200000:
                s = g.read(r['r5'], 80).split(b'\0')[0]
            first[k] = (r['r4'], r['r5'], r['r6'], r['r7'], s.decode('cp932', 'replace'))
finally:
    g.s.settimeout(5)
    g.cmd('z0,%x,4' % addr)
    g.resume()
for k, c in seen.most_common():
    a = first[k]
    print('ra=%08x x%-4d a0-a3=%08x %08x %08x %08x %r' % (k, c, *a[:4], a[4]))
