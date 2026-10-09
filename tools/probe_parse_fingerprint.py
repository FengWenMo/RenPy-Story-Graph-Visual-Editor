"""解析金标：`ParseResult` 的**同输入 → 同结果**（M4 拆 `renpy_parser._walk` 的护栏）。

`_walk` 是"源码 → ParseResult"的分发中枢（角色/台词/label/transform/image/
菜单/label 集合/翻译单元…）。拆它时漏掉任何一类，**下游全都会少东西**，
而且多半是静默的 —— 所以金标要**覆盖整份 ParseResult**：

  · 真机 53 个 `.rpy` 各自 `analyze()` → `ParseResult`；
  · 用一个**通用递归归一化**（dataclass→dict、dict 按 key 排序、set 排序、
    Path 相对化）把它压成可比较的 JSON，再算 sha256；
  · 报差异时能指出是**哪个文件**。

    python tools/probe_parse_fingerprint.py
    python tools/probe_parse_fingerprint.py --save <file>
    python tools/probe_parse_fingerprint.py --check <file>
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.parsing.discover import discover_scripts          # noqa: E402
from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer  # noqa: E402

REAL = Path(r'D:\千连万理\RenpyTest\sr_general')


def norm(obj, base: Path | None = None):
    """通用归一化：dataclass / dict / set / Path 都压成**稳定可比**的形状。"""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: norm(getattr(obj, f.name), base)
                for f in dataclasses.fields(obj)}
    if isinstance(obj, dict):
        return {str(k): norm(v, base)
                for k, v in sorted(obj.items(), key=lambda kv: str(kv[0]))}
    if isinstance(obj, (set, frozenset)):
        return sorted((norm(x, base) for x in obj), key=repr)
    if isinstance(obj, (list, tuple)):
        return [norm(x, base) for x in obj]
    if isinstance(obj, Path):
        s = str(obj).replace('\\', '/')
        return s.replace(str(base).replace('\\', '/'), '<ROOT>') if base else s
    if isinstance(obj, str) and base is not None:
        return obj.replace(str(base), '<ROOT>').replace(
            str(base).replace('\\', '/'), '<ROOT>')
    if isinstance(obj, (int, float, bool)) or obj is None:
        return obj
    return repr(obj)


def fingerprint(root: Path = REAL, limit: int | None = None):
    az = IncrementalRenPyAnalyzer()
    per_file = {}
    files = sorted(discover_scripts(root))
    if limit:
        files = files[:limit]
    for f in files:
        pr, _ = az.analyze(files=[f], graph=None)
        blob = json.dumps(norm(pr, root), ensure_ascii=False, sort_keys=True)
        per_file[Path(f).name] = hashlib.sha256(blob.encode()).hexdigest()
    h = hashlib.sha256(
        '\n'.join(f'{k}\n{v}' for k, v in sorted(per_file.items())).encode())
    return h.hexdigest(), per_file


def main() -> int:
    argv = sys.argv[1:]
    root = REAL
    if argv and argv[0] not in ('--save', '--check'):
        root = Path(argv[0])
        argv = argv[1:]
    fp, per_file = fingerprint(root)
    print(f'文件 {len(per_file)} 个')
    print('FINGERPRINT=' + fp)

    if len(argv) == 2 and argv[0] == '--save':
        Path(argv[1]).write_text(
            fp + '\n' + '\n'.join(f'{k} {v}'
                                  for k, v in sorted(per_file.items())) + '\n',
            encoding='utf-8')
        print(f'saved -> {argv[1]}')
    elif len(argv) == 2 and argv[0] == '--check':
        lines = Path(argv[1]).read_text(encoding='utf-8').split()
        want = lines[0] if lines else ''
        want_files = dict(zip(lines[1::2], lines[2::2]))
        if want != fp:
            print('MISMATCH')
            for k, v in sorted(per_file.items()):
                if want_files.get(k) != v:
                    print(f'  解析结果变了：{k}')
            return 1
        print('MATCH')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
