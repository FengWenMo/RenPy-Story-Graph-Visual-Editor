"""临时探针：读 PE 导入表，列出某个 dll/exe 依赖了哪些 DLL。

为什么不用 dumpbin：交付机上不一定有 VS 工具链；这段只读文件头，
标准库足够。
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path


def imports_of(path) -> list:
    data = Path(path).read_bytes()
    pe = struct.unpack_from('<I', data, 0x3c)[0]
    nsec = struct.unpack_from('<H', data, pe + 6)[0]
    opt_size = struct.unpack_from('<H', data, pe + 20)[0]
    opt = pe + 24
    magic = struct.unpack_from('<H', data, opt)[0]
    dd = opt + (112 if magic == 0x20b else 96)
    imp_rva = struct.unpack_from('<I', data, dd + 8)[0]
    secs = []
    for i in range(nsec):
        o = opt + opt_size - 0 + i * 40
        # 节表紧跟在可选头之后
        o = opt + opt_size + i * 40
        name = data[o:o + 8].rstrip(b'\0').decode('ascii', 'replace')
        vsize, vaddr, rsize, raddr = struct.unpack_from('<IIII', data, o + 8)
        secs.append((name, vaddr, vsize, raddr, rsize))

    def rva2off(rva):
        for _name, vaddr, vsize, raddr, rsize in secs:
            if vaddr <= rva < vaddr + max(vsize, rsize):
                return raddr + (rva - vaddr)
        return None

    out = []
    off = rva2off(imp_rva)
    if off is None:
        return out
    while True:
        ilt, ts, fc, name_rva, iat = struct.unpack_from('<IIIII', data, off)
        if name_rva == 0:
            break
        no = rva2off(name_rva)
        end = data.index(b'\0', no)
        out.append(data[no:end].decode('ascii', 'replace'))
        off += 20
    return out


if __name__ == '__main__':
    for target in sys.argv[1:]:
        names = imports_of(target)
        print(f'== {target} ({len(names)} 个)')
        print('\n'.join('  ' + n for n in sorted(names, key=str.lower)))
