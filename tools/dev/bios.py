"""List BIOS call stubs (li t2,A0/B0/C0; jr t2; li t1,n) in SLPS_014.66 and their callers."""
import sys, struct, os
sys.path.insert(0, os.path.dirname(__file__))
import mips
img = mips.Image('jp')
for va in range(img.base, img.end - 12, 4):
    w0, w1, w2 = img.word(va), img.word(va + 4), img.word(va + 8)
    if (w0 & 0xFFFFFF00) == 0x240A0000 and (w0 & 0xFF) in (0xA0, 0xB0, 0xC0) and w1 == 0x01400008 \
            and (w2 & 0xFFFF0000) == 0x24090000:
        print('%08x  %02X:%02X  callers %s' % (va, w0 & 0xFF, w2 & 0xFFFF,
              ' '.join('%08x' % c for c in mips.jals(img, va))))
