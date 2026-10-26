"""Survey: officer records in SCENARIO/SAN5SN*.DAT (38-byte records: name 9, reading 9, data 20)."""
import sys
REC = 38

def ok(d, r):
    nm, rd = d[r:r+9], d[r+9:r+18]
    n = nm.split(b'\0')[0]; k = rd.split(b'\0')[0]   # name may use gaiji (lead 0xEB..)
    lead = all((0x81 <= n[i] <= 0x9f or 0xe0 <= n[i] <= 0xfc) for i in range(0, len(n), 2))
    return (len(n) % 2 == 0 and 2 <= len(n) <= 8 and lead and nm[len(n):].strip(b'\0') == b''
            and len(k) >= 1 and all(0xa1 <= c <= 0xdf for c in k))

def table(d, seed):
    p = seed
    while ok(d, p - REC): p -= REC
    q = seed
    while ok(d, q + REC): q += REC
    return p, (q - p) // REC + 1

if __name__ == '__main__':
    d = open(sys.argv[1], 'rb').read()
    seed = d.find('劉備'.encode('cp932'))
    p, n = table(d, seed)
    print(hex(p), n, 'records, end', hex(p + n * REC), 'file', hex(len(d)))
