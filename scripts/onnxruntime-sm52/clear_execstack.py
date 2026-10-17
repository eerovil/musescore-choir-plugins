"""Clear the executable-stack flag in 64-bit ELF libraries.

onnxruntime's Linux build marks its Python extension as wanting an executable
stack, and newer glibc refuses to load such a library ("cannot enable
executable stack as shared object requires"). Nothing in it needs one, so the
PF_X bit of its PT_GNU_STACK header is cleared in place. A path that does not
exist is skipped, so an unmatched shell glob is harmless.
"""

import os
import struct
import sys

PT_GNU_STACK = 0x6474E551
PF_X = 1


def clear(path: str) -> bool:
    with open(path, "r+b") as f:
        header = f.read(64)
        if header[:4] != b"\x7fELF" or header[4] != 2:
            return False
        (phoff,) = struct.unpack_from("<Q", header, 0x20)
        phentsize, phnum = struct.unpack_from("<HH", header, 0x36)
        for i in range(phnum):
            offset = phoff + i * phentsize
            f.seek(offset)
            ptype, flags = struct.unpack("<II", f.read(8))
            if ptype == PT_GNU_STACK and flags & PF_X:
                f.seek(offset + 4)
                f.write(struct.pack("<I", flags & ~PF_X))
                return True
    return False


if __name__ == "__main__":
    for path in sys.argv[1:]:
        if os.path.isfile(path) and clear(path):
            print(f"cleared the executable-stack flag: {path}")
