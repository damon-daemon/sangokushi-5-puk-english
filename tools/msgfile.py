"""MESSAGE/MESSAGE.BIN: blocks of message entries, and the block table in the program.

File = 57 blocks, each starting on a 2 KB boundary. The program holds the byte offset of
every block (u32, 2 KB aligned) at TABLE, with no end marker (the last block runs to the
end of the file). Block = u16 n, n u16 offsets (from the block start), then the entries.
Message id = block * 1000 + index. The clean disc pads every block with a copy of its own
start (Koei's tool wrote the block buffer twice), so the last entry ends where the block
header repeats; build() keeps the clean padding of unchanged blocks so they stay
byte-identical. Block 52 fills its 0xB000 bytes and its last entry is cut off on the disc.

Grammar = the S6 / Soutenroku bytecode (S6 notes/msg_vars.md): text runs are
`01 22 <bytes>` up to the next byte 00-06; `01 43 u16` (insert a block 0 entry) and
`01 4A u16` (message reference) take a 2-byte operand; `05 05 04 <cond>` if,
`05 05 05 05 05 04 <cond>` else-if, `05 05 05` end of a variant. split() cuts an entry into
('code', bytes) and ('text', bytes) parts; b''.join of the parts is the entry again.
"""
import os, re, struct, sys

sys.path.insert(0, os.path.dirname(__file__))
import asm

NAME = 'MESSAGE/MESSAGE.BIN'
TABLE = 0x8010F8A0                 # va of the u32 block table in SLPS_014.66
EXE_BASE = 0x80010000 - 0x800      # va - file offset
NBLOCKS = 57
ALIGN = 2048


def table(exe):
    return list(struct.unpack_from('<%dI' % NBLOCKS, exe, TABLE - EXE_BASE))


def set_table(exe, offsets):
    assert len(offsets) == NBLOCKS and all(o % ALIGN == 0 for o in offsets)
    struct.pack_into('<%dI' % NBLOCKS, exe, TABLE - EXE_BASE, *offsets)


def data_end(data):
    """End of the real data of a padded clean block (where its header repeats)."""
    n = struct.unpack_from('<H', data, 0)[0]
    last = struct.unpack_from('<H', data, 2 * n)[0]
    h = data.find(data[:8], last)
    if h < 0:
        return len(data)
    while data[h - 1] == 0:
        h -= 1
    return h


def parse_block(data):
    """Block bytes (with padding) -> list of entry bytes."""
    n = struct.unpack_from('<H', data, 0)[0]
    offs = struct.unpack_from('<%dH' % n, data, 2)
    end = data_end(data)
    return [data[offs[i]:offs[i + 1] if i + 1 < n else end] for i in range(n)]


def build_block(ents):
    """Entries -> block bytes without padding (header layout as on the disc)."""
    n = len(ents)
    out = bytearray(struct.pack('<H', n) + bytes(2 * n))
    for i, e in enumerate(ents):
        if len(out) > 0xFFFF:
            raise ValueError('block offset %d > 64 KB' % len(out))
        struct.pack_into('<H', out, 2 + 2 * i, len(out))
        out += e
    return bytes(out)


def bounds(data, exe):
    t = table(exe)
    return t + [len(data)]


def parse(data, exe):
    t = bounds(data, exe)
    return [parse_block(data[t[b]:t[b + 1]]) for b in range(NBLOCKS)]


def build(blocks, tail=None, pad=0):
    """-> (file bytes, offset table). tail: raw_blocks() of the clean file; an unchanged
    block is written as on the clean disc (with its self-copy padding). pad: the file
    is zero-filled to at least the last block + pad (every load reads a fixed size)."""
    out, offs = bytearray(), []
    for b, ents in enumerate(blocks):
        offs.append(len(out))
        blk = build_block(ents)
        if tail and b in tail and tail[b][0] == blk:
            blk = tail[b][1]
        out += blk
        out += bytes(-len(out) % ALIGN)
    out += bytes(max(0, offs[-1] + pad - len(out)))
    return bytes(out), offs


def raw_blocks(data, exe):
    """{block: (rebuilt-without-padding bytes, clean padded bytes)} for byte-identical rebuilds."""
    t = bounds(data, exe)
    out = {}
    for b in range(NBLOCKS):
        clean = data[t[b]:t[b + 1]]
        out[b] = (build_block(parse_block(clean)), clean)
    return out


# ---------------------------------------------------------------- loader patch
# Arena 0x80170000: block 0 at +0, the current block at +0x15A0, every load reads 0xB000
# bytes (22 sectors) whatever the block size (notes/ram.md §1). English block 0 outgrows
# 0x15A0, so relayout() moves the split to +0x1DA0 and reads 0xA800 (21 sectors: whole
# sectors, the current block still ends at 0x8017C5A0). Blocks larger than BLOCK_MAX (only
# block 52, the tutorial) are cut: split_blocks() moves the tail entries to extra blocks
# 57.., a hook in msg_entry sends their ids there, and the block table moves to free RAM.
ARENA = 0x80170000
BLOCK0_MAX = 0x1DA0                # block 0 buffer (clean 0x15A0)
BLOCK_MAX = 0xA800                 # current-block buffer and read size (clean 0xB000)
MSG_ENTRY_HOOK = 0x8004f188        # lw v0,0x99c(gp); mfhi a1; srl s0,a1,6 (s0 = id / 1000)
# (va, clean word, patched word or None) of the loader / msg_entry instructions
RELAYOUT = [
    (0x8004f0c8, 0x30c615a0, 0x30c61da0),   # andi a2,a2,0x15a0: current block offset
    (0x8004f0e4, 0x3c018011, None),          # lui at,0x8011      table (set in relayout)
    (0x8004f0ec, 0x8c25f8a0, None),          # lw a1,-0x760(at)
    (0x8004f0f4, 0x3407b000, 0x3407a800),   # ori a3,zero,0xb000: read size
    (0x8004f188, 0x8f82099c, None),          # hook (set in relayout)
    (0x8004f18c, 0x00002810, None),
    (0x8004f190, 0x00058182, None),
    (0x8004f1c4, 0x904215a1, 0x90421da1),   # current block: count, offsets, position base
    (0x8004f1cc, 0x906315a0, 0x90631da0),
    (0x8004f1ec, 0x902315a3, 0x90231da3),
    (0x8004f1f8, 0x902215a2, 0x90221da2),
    (0x8004f204, 0x246315a0, 0x24631da0),
]


def refs(entry):
    """Message ids of the `01 4A` references in an entry."""
    return [struct.unpack_from('<H', v, j + 2)[0] for k, v in split(entry) if k == 'code'
            for j in range(len(v) - 3) if v[j:j + 2] == b'\x01\x4A']


def split_blocks(blocks, limit=BLOCK_MAX):
    """Cut blocks (other than 0) larger than limit: the tail entries become a new block at
    the end (cut again if needed). -> (blocks, [(block, first moved index, new block)]).
    A reference pops the block number without reloading the buffer, so no `01 4A` may
    point across a cut (the clean script has no cross-block references at all)."""
    blocks, splits = list(blocks), []
    b = 1
    while b < len(blocks):
        ents = blocks[b]
        if len(build_block(ents)) > limit:
            k = len(ents)
            while k > 1 and len(build_block(ents[:k])) > limit:
                k -= 1
            nb = len(blocks)
            blocks[b], tail = ents[:k], ents[k:]
            blocks.append(tail)
            splits.append((b, k, nb))
            for part, lo, hi in ((ents[:k], 0, k), (tail, k, len(ents))):
                for i, e in enumerate(part, lo):
                    for t in refs(e):
                        if t // 1000 == b and not lo <= t % 1000 < hi:
                            raise ValueError('message %d refers to %d across a block cut' % (b * 1000 + i, t))
        b += 1
    return blocks, splits


def split_hook(at, splits):
    """msg_entry after `id * 0x10624dd3` (a1 = hi): s0 = block, s1 = id; ids moved by a
    split get block nb and id nb*1000 + (index - k). Ends with v0 = gp+0x99c (loaded block)."""
    lines = [('srl', 's0', 'a1', 6)]
    for n, (b, k, nb) in enumerate(splits):
        first = b * 1000 + k
        assert first <= 0xFFFF and nb < 65
        lines += [
            ('addiu', 't0', 'zero', b),
            ('bne', 's0', 't0', 'n%d' % n),
            ('ori', 't1', 'zero', first),
            ('sltu', 't1', 's1', 't1'),
            ('bnez', 't1', 'n%d' % n),
            ('nop',),
            ('addiu', 's1', 's1', nb * 1000 - first),
            ('addiu', 's0', 'zero', nb),
            'n%d:' % n,
        ]
    lines += [('jr', 'ra'), ('lw', 'v0', 0x99c, 'gp')]
    return asm.assemble(at, lines)


def relayout(exe, heap, offs, splits):
    """Patch the loader for BLOCK0_MAX / BLOCK_MAX, the block table offs (put in free RAM)
    and the split hook."""
    def w(va):
        return struct.unpack_from('<I', exe, va - EXE_BASE)[0]

    def put(va, word):
        struct.pack_into('<I', exe, va - EXE_BASE, word)

    for va, old, new in RELAYOUT:
        assert w(va) == old, 'msg loader %08x' % va
        if new is not None:
            put(va, new)
    tab = heap.put(struct.pack('<%dI' % len(offs), *offs))
    put(0x8004f0e4, 0x3c010000 | asm.hi(tab))
    put(0x8004f0ec, 0x8c250000 | asm.lo(tab) & 0xFFFF)
    hook = heap.code(split_hook, splits)
    exe[MSG_ENTRY_HOOK - EXE_BASE:MSG_ENTRY_HOOK - EXE_BASE + 12] = asm.assemble(MSG_ENTRY_HOOK, [
        ('jal', hook),
        ('mfhi', 'a1'),
        ('nop',),
    ])
    return tab


def check_sizes(blocks):
    """Errors for blocks that do not fit the relaid-out buffers."""
    bad = []
    for b, ents in enumerate(blocks):
        size, limit = len(build_block(ents)), BLOCK0_MAX if b == 0 else BLOCK_MAX
        if size > limit:
            bad.append('block %d: %d bytes > %d' % (b, size, limit))
    return bad


def split(entry):
    parts, i, n = [], 0, len(entry)
    code = bytearray()
    while i < n:
        c = entry[i]
        if c == 0x01 and i + 1 < n:
            op = entry[i + 1]
            if op == 0x22:
                j = i + 2
                while j < n and entry[j] > 0x06:
                    j += 1
                code += entry[i:i + 2]
                parts.append(('code', bytes(code)))
                parts.append(('text', entry[i + 2:j]))
                code = bytearray()
                i = j
                continue
            if op in (0x43, 0x4A):
                code += entry[i:i + 4]
                i += 4
                continue
        code.append(c)
        i += 1
    if code:
        parts.append(('code', bytes(code)))
    return parts


def texts(entry):
    return [p for k, p in split(entry) if k == 'text']


# ---------------------------------------------------------------- segments
# A segment is a chain of text runs joined by inline codes only: a bare `01 22` (the
# Japanese starts a new run for every line), `01 43 a b` (insert a block-0 word) and
# `02 v C8` (print variable v: a name; S6 used 65/66). Everything else (05 ends, 04..06
# conditions, other commands) is structure and stays byte for byte. A segment is the
# unit translated as a whole: English text and the inline tokens in any order. Tokens
# at the start of the code after a segment belong to it (droppable word endings).
TOKEN = re.compile(rb'\x01\x43..|\x02.\xC8', re.S)
_LEAD = re.compile(rb'(?:\x01\x43..|\x02.\xC8)*', re.S)
_TAIL = re.compile(rb'(?:\x01\x43..|\x02.\xC8)*\x01\x22', re.S)


def _tail(code):
    """Start of the inline tail of a code part (tokens + the final 01 22), or None."""
    if not code.endswith(b'\x01\x22'):
        return None
    for i in range(len(code) - 1):
        if _TAIL.fullmatch(code, i):
            return i
    return None


def segments(entry):
    """-> list of ('code', bytes) and ('seg', [('text', bytes) | ('tok', bytes)])."""
    out, cur = [], None
    for kind, v in split(entry):
        if kind == 'text':
            if cur is None:
                cur = []
                out.append(('seg', cur))
            cur.append(('text', v))
            continue
        lead = _LEAD.match(v).end() if cur is not None else 0
        if lead and lead < len(v) and _tail(v) != 0:
            # tokens right after a segment (word endings before a variant end) join it
            cur += [('tok', m.group()) for m in TOKEN.finditer(v, 0, lead)]
            v = v[lead:]
        i = _tail(v)
        if i is None:
            out.append(('code', v))
            cur = None
            continue
        if i > 0 or cur is None:
            if i > 0:
                out.append(('code', v[:i]))
            cur = []
            out.append(('seg', cur))
        cur += [('tok', m.group()) for m in TOKEN.finditer(v, i, len(v) - 2)]
    return out


def seg_bytes(pieces):
    """Segment pieces -> bytes (every text piece starts a run with 01 22)."""
    out = bytearray()
    for kind, v in pieces:
        out += v if kind == 'tok' else b'\x01\x22' + v
    return bytes(out)


def join_segments(items):
    return b''.join(v if kind == 'code' else seg_bytes(v) for kind, v in items)
