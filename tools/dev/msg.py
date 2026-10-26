"""Survey helper: MESSAGE.BIN blocks/entries -> readable text."""
import struct, sys

EXE_TABLE = 0x1000a0   # file offset in SLPS_014.66 of the u32 block-offset table (57 blocks)
NBLOCKS = 57

def blocks(d, e):
    offs = list(struct.unpack_from('<%dI' % NBLOCKS, e, EXE_TABLE))
    return offs

def entries(d, off):
    n = struct.unpack_from('<H', d, off)[0]          # u16 count, then n u16 offsets (block-relative)
    return list(struct.unpack_from('<%dH' % n, d, off + 2))

def readable(b):
    out = []; i = 0
    while i < len(b):
        c = b[i]
        if (0x81 <= c <= 0x9f or 0xe0 <= c <= 0xfc) and i + 1 < len(b):
            try: out.append(b[i:i+2].decode('cp932'))
            except UnicodeDecodeError: out.append('<%02x%02x>' % (c, b[i+1]))
            i += 2; continue
        if 0xa1 <= c <= 0xdf: out.append(bytes([c]).decode('cp932'))
        elif 0x20 <= c < 0x7f: out.append(chr(c))
        elif c == 0x0a: out.append('\\n')
        else: out.append('<%02x>' % c)
        i += 1
    return ''.join(out)

if __name__ == '__main__':
    d = open(sys.argv[1], 'rb').read(); e = open(sys.argv[2], 'rb').read()
    bo = blocks(d, e) + [len(d)]
    sel = [int(x) for x in sys.argv[3:]] or range(NBLOCKS)
    for bi in sel:
        base = bo[bi]; o = entries(d, base)
        for k, s in enumerate(o):
            end = o[k+1] if k + 1 < len(o) else None
            raw = d[base+s: base+end] if end else d[base+s: base+s+0x200]
            print('%02d.%03d %s' % (bi, k, readable(raw)))
