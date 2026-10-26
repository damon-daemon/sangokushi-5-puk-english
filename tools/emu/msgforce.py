"""msgforce.py ID [keys...]: at the next msg_entry call (0x8004f108, id in a0) show message ID instead,
while pressing keys (hpress.sh). Run from the project root with the emulator running; then screenshot."""
import subprocess, sys, threading
sys.path.insert(0, 'tools/emu'); import gdbmem
MSG_ENTRY = 0x8004f108
mid, keys = int(sys.argv[1]), sys.argv[2:]
g = gdbmem.G()
if keys:
    threading.Timer(0.5, lambda: subprocess.run(['tools/emu/hpress.sh', '/dev/null'] + keys)).start()
try:
    pkt, r = gdbmem.wait_break(g, MSG_ENTRY, timeout=20)
    print('id %d ra=%08x -> %d' % (r['r4'], r['r31'], mid), g.cmd('P4=%s' % mid.to_bytes(4, 'little').hex()))
except TimeoutError:
    print('no msg_entry call'); g.s.sendall(b'\x03'); g._recv(); g.cmd('z0,%x,4' % MSG_ENTRY)
g.resume()
