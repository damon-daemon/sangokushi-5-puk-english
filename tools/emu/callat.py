"""callat.py FUNC A0 [A1] -- [keys...]: at the next msg_entry call (0x8004f108) first call FUNC(A0, A1)
(hex FUNC, int args) through a stub in scratch RAM, then continue into msg_entry. E.g. the PUK scenario
intro: callat.py 8001dd34 9 -- X. Run from the project root with the emulator running."""
import subprocess, sys, threading
sys.path.insert(0, 'tools'); sys.path.insert(0, 'tools/emu')
import asm, gdbmem
MSG_ENTRY, STUB = 0x8004f108, 0x80108800
args = sys.argv[1:sys.argv.index('--')] if '--' in sys.argv else sys.argv[1:]
keys = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
func, a = int(args[0], 16), [int(x) for x in args[1:]] + [0, 0]
code = asm.assemble(STUB, [
    ('addiu', 'sp', 'sp', -0x20), ('sw', 'ra', 0x10, 'sp'), ('sw', 'a0', 0x14, 'sp'), ('sw', 'a1', 0x18, 'sp'),
    ('lui', 'a0', asm.hi(a[0])), ('addiu', 'a0', 'a0', asm.lo(a[0])),
    ('lui', 'a1', asm.hi(a[1])), ('jal', func), ('addiu', 'a1', 'a1', asm.lo(a[1])),
    ('lw', 'ra', 0x10, 'sp'), ('lw', 'a0', 0x14, 'sp'), ('lw', 'a1', 0x18, 'sp'),
    ('j', MSG_ENTRY), ('addiu', 'sp', 'sp', 0x20)])
g = gdbmem.G()
if keys:
    threading.Timer(0.5, lambda: subprocess.run(['tools/emu/hpress.sh', '/dev/null'] + keys)).start()
try:
    pkt, r = gdbmem.wait_break(g, MSG_ENTRY, timeout=20)
except TimeoutError:
    print('no msg_entry call'); g.s.sendall(b'\x03'); g._recv(); g.cmd('z0,%x,4' % MSG_ENTRY); g.resume(); sys.exit(1)
print(g.cmd('M%x,%x:%s' % (STUB, len(code), code.hex())))
print('id %d ra=%08x' % (r['r4'], r['r31']), g.cmd('P25=%s' % STUB.to_bytes(4, 'little').hex()))
g.resume()
