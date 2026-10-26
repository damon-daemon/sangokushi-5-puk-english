"""lettertest.py TEXTFILE [keys...]: show TEXTFILE (UTF-8/ASCII, real newlines) through the letter path —
the call at 0x80032a44 (line breaker 0x80057404, or our letter_wrap in a build) then letter display 0x8001ea20(dst) — by diverting the next
msg_entry call through a stub in scratch RAM that calls both and then continues into msg_entry.
Run from the project root with the emulator running (e.g. on the command window, keys reaching a message)."""
import subprocess, sys, threading
sys.path.insert(0, 'tools'); sys.path.insert(0, 'tools/emu')
import asm, gdbmem
MSG_ENTRY, BREAK, SHOW = 0x8004f108, 0x80057404, 0x8001ea20
STUB, SRC, DST = 0x80108800, 0x801c1000, 0x801c4000
text = open(sys.argv[1], encoding='utf-8').read().rstrip('\n').encode('cp932') + b'\0'
keys = sys.argv[2:]
def stub():
    return asm.assemble(STUB, [
    ('addiu', 'sp', 'sp', -0x20), ('sw', 'ra', 0x10, 'sp'), ('sw', 'a0', 0x14, 'sp'),
    ('lui', 'a0', asm.hi(SRC)), ('addiu', 'a0', 'a0', asm.lo(SRC)),
    ('lui', 'a1', asm.hi(DST)), ('addiu', 'a1', 'a1', asm.lo(DST)),
    ('jal', BREAK), ('addiu', 'a2', 'zero', 0x20),
    ('lui', 'a0', asm.hi(DST)), ('jal', SHOW), ('addiu', 'a0', 'a0', asm.lo(DST)),
    ('lw', 'ra', 0x10, 'sp'), ('lw', 'a0', 0x14, 'sp'), ('j', MSG_ENTRY), ('addiu', 'sp', 'sp', 0x20)])
code = None
g = gdbmem.G()
w = int.from_bytes(bytes.fromhex(g.cmd('m80032a44,4')), 'little')
BREAK = 0x80000000 | (w & 0x3FFFFFF) << 2
print('breaker %08x' % BREAK)
if keys:
    threading.Timer(0.5, lambda: subprocess.run(['tools/emu/hpress.sh', '/dev/null'] + keys)).start()
try:
    pkt, r = gdbmem.wait_break(g, MSG_ENTRY, timeout=20)
except TimeoutError:
    print('no msg_entry call'); g.s.sendall(b'\x03'); g._recv(); g.cmd('z0,%x,4' % MSG_ENTRY); g.resume(); sys.exit(1)
code = stub()
print(g.cmd('M%x,%x:%s' % (SRC, len(text), text.hex())), g.cmd('M%x,%x:%s' % (STUB, len(code), code.hex())))
# the stub re-enters msg_entry: hit once more there would re-divert, so only the pc changes here
print('id %d ra=%08x' % (r['r4'], r['r31']), g.cmd('P25=%s' % STUB.to_bytes(4, 'little').hex()))
g.resume()
