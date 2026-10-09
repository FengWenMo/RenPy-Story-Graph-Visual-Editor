"""纯文字导入金标：`convert_text` 的**同输入 → 同产物**（S1/S2 的护栏）。

为什么单靠"往返逐字节"不够：那是"产物 == 我们自己的渲染器" —— 它**测不出
"映射规则变了但产物仍然自洽"**（例如 `JC` 从 `jc` 改成别的变量：往返照样一致，
可是剧情里说话人换了人）。所以金标要钉住**产物本身**：

  · 真机 5 章定稿（1372 行）各自：`convert_file` 的**产物 sha256** + 行型统计 +
    未映射清单；
  · 只存**逐文件哈希**（几十行），报差异时能定位到"哪个文件变了"。

    python tools/probe_txt_import_fingerprint.py [--save|--check <file>]
"""
from __future__ import annotations

import glob
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.authoring.txt_import import (CharacterMap,  # noqa: E402
                                             convert_file)
from storygraph.pipeline.build import load_asset_registry   # noqa: E402

TXT_DIR = Path(r'D:\千连万理\txt--renpy脚本转换工具')
REAL = Path(r'D:\千连万理\RenpyTest\sr_general')


def fingerprint(txt_dir: Path = TXT_DIR):
    chars = CharacterMap((load_asset_registry(REAL) or {}).characters) \
        if REAL.is_dir() else None
    cases = {}
    for f in sorted(glob.glob(str(txt_dir / '*-定稿.txt'))):
        r = convert_file(f, characters=chars)
        cases[Path(f).name] = {
            'label': r.label,
            'sha': hashlib.sha256(r.rpy_text.encode()).hexdigest(),
            'counts': r.counts,
            'unmapped': r.unmapped,
            'warnings': r.warnings,
            'dropped': r.dropped,
        }
    text = json.dumps(cases, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(text.encode()).hexdigest(), text, cases


def main() -> int:
    txt_dir = TXT_DIR
    argv = sys.argv[1:]
    if argv and argv[0] not in ('--save', '--check'):
        txt_dir = Path(argv[0])
        argv = argv[1:]
    fp, text, cases = fingerprint(txt_dir)
    print('文件 %d 个；未映射角色：%s' % (
        len(cases),
        json.dumps({k: v['unmapped'] for k, v in cases.items()
                    if v['unmapped']}, ensure_ascii=False)))
    print('FINGERPRINT=' + fp)

    if len(argv) == 2 and argv[0] == '--save':
        Path(argv[1]).write_text(fp + '\n' + text + '\n', encoding='utf-8')
        print(f'saved -> {argv[1]}')
    elif len(argv) == 2 and argv[0] == '--check':
        body = Path(argv[1]).read_text(encoding='utf-8').split('\n', 1)
        want = body[0].strip()
        if want != fp:
            old = json.loads(body[1]) if len(body) > 1 else {}
            print('MISMATCH')
            for name in sorted(set(old) | set(cases)):
                if old.get(name) != cases.get(name):
                    a, b = old.get(name) or {}, cases.get(name) or {}
                    print(f'  {name}:')
                    for k in sorted(set(a) | set(b)):
                        if a.get(k) != b.get(k):
                            print(f'     {k}: {a.get(k)} -> {b.get(k)}')
            return 1
        print('MATCH')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
