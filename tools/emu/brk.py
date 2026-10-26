"""brk.py ADDR [keys...]: break at ADDR (hex) while pressing keys (hpress.sh); print a0-a3, ra, pc.
Run from the project root with the headless emulator running."""
import subprocess, sys, threading
sys.path.insert(0, 'tools/emu'); import gdbmem
addr = int(sys.argv[1], 16); keys = sys.argv[2:]
g = gdbmem.G()
if keys:
    threading.Timer(0.5, lambda: subprocess.run(['tools/emu/hpress.sh', '/dev/null'] + keys)).start()
try:
    pkt, r = gdbmem.wait_break(g, addr, timeout=20)
    print(pkt, ' '.join('%s=%08x' % (k, r[k]) for k in ('r4', 'r5', 'r6', 'r7', 'r31', 'pc')))
except Exception as e:
    print('no hit', e)
g.resume()
