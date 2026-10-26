"""strtest.py [@ADDR:REG] N TEXT [keys...]: overwrite the strings of the next N print_string calls
(or calls of ADDR, string pointer in register REG, e.g. @800c4188:4 = printf format) with TEXT
(test of the renderer with any text) while pressing keys (hpress.sh). TEXT (Shift-JIS, NUL-terminated;
'\\n' = newline) is written to unused heap RAM (SCRATCH, inside the freed font area) and the
string register is pointed at it. Run from the project root with the emulator running."""
import subprocess, sys, threading
sys.path.insert(0, 'tools/emu'); import gdbmem
PRINT_STRING = 0x80019658
SCRATCH = 0x80108000              # end of the freed font area (kanji.Heap: unused so far)
at, reg = PRINT_STRING, 5
if sys.argv[1].startswith('@'):
    a, b = sys.argv.pop(1)[1:].split(':'); at, reg = int(a, 16), int(b)
n, text, keys = int(sys.argv[1]), sys.argv[2].replace('\\n', '\n').encode('cp932') + b'\0', sys.argv[3:]
g = gdbmem.G()
if keys:
    threading.Timer(0.5, lambda: subprocess.run(['tools/emu/hpress.sh', '/dev/null'] + keys)).start()
for _ in range(n):
    pkt, r = gdbmem.wait_break(g, at, timeout=20)
    a1 = r['r%d' % reg]
    old = g.read(a1, 64).split(b'\0')[0]
    print('%08x ra=%08x %r' % (a1, r['r31'], old.decode('cp932', 'replace')))
    print(g.cmd('M%x,%x:%s' % (SCRATCH, len(text), text.hex())), g.cmd('P%x=%s' % (reg, SCRATCH.to_bytes(4, 'little').hex())))
g.resume()
