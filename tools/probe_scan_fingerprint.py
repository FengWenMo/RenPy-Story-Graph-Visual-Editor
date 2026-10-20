"""扫描金标：`scan_assets` 的**同输入 → 同输出**（M4 拆它的护栏）。

`scan_assets` 决定"什么是项目资源"，下游（对账/补登/删行/候选表）全建在它上面
—— 拆它时改一条归类规则，等于改整张表。所以先录指纹：

  · 真机项目：`include_ui=True/False`、带/不带 `FileWalkIndex`（嵌套副本排除）四种组合；
  · 每个结果里的**路径统一相对化 + 排序**（否则绝对路径与 set 顺序会让指纹随机漂移）；
  · 附带每类的**条数**（条数变了最容易看出来）。

    python tools/probe_scan_fingerprint.py                  # 打印指纹
    python tools/probe_scan_fingerprint.py --save <file>
    python tools/probe_scan_fingerprint.py --check <file>
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.assets.asset_registry import (FileWalkIndex,  # noqa: E402
                                              scan_assets)

REAL = Path(r'D:\千连万理\RenpyTest\sr_general')


def _rel(p, root: Path) -> str:
    s = str(p)
    try:
        return Path(s).resolve().relative_to(root.resolve()).as_posix()
    except (ValueError, OSError):
        return s.replace('\\', '/')


#: 指纹只认**分类相关**的字段。2026-10-19：从前把整行 `str(dict)` 拿去排序，
#: 于是任何新列（`Group` / `Value`）都会让金标红 —— 那不是"归类变了"，
#: 只是"多了一列"。金标要盯的是"这个文件进没进表、归到哪个类、tag/属性是什么"。
_CORE_FIELDS = ('ID', 'Path', 'Tag', 'Attributes', 'Category', 'Name',
                'Loop', 'Volume')


def _norm_item(item, root: Path) -> str:
    """一行 → 只含核心字段的稳定字符串（路径相对化）。"""
    if not isinstance(item, dict):
        return _rel(item, root)
    payload = {k: item.get(k) for k in _CORE_FIELDS
               if item.get(k) not in (None, '')}
    if payload.get('Path'):
        payload['Path'] = _rel(payload['Path'], root)
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _norm_list(items, root: Path) -> list:
    return sorted(_norm_item(x, root) for x in (items or []))


def _digest(res: dict, root: Path) -> dict:
    audio = res.get('audio') or {}
    return {
        'images': _norm_list(res.get('images'), root),
        'music': _norm_list(audio.get('music'), root),
        'sound': _norm_list(audio.get('sound'), root),
        'voices': _norm_list(res.get('voices'), root),
    }


def fingerprint(root: Path = REAL):
    cases = {}
    for tag, kwargs in (
        ('ui', dict(include_ui=True)),
        ('noui', dict(include_ui=False)),
        ('ui+walk', dict(include_ui=True, walk=FileWalkIndex(root))),
        ('noui+walk', dict(include_ui=False, walk=FileWalkIndex(root))),
    ):
        res = scan_assets(root, **kwargs)
        cases[tag] = _digest(res, root)
    text = json.dumps(cases, ensure_ascii=False, sort_keys=True)
    counts = {k: {kk: len(vv) for kk, vv in v.items()}
              for k, v in cases.items()}
    return hashlib.sha256(text.encode()).hexdigest(), text, counts


def main() -> int:
    root = REAL
    argv = sys.argv[1:]
    if argv and argv[0] not in ('--save', '--check'):
        root = Path(argv[0])
        argv = argv[1:]
    fp, text, counts = fingerprint(root)
    print('条数：', json.dumps(counts, ensure_ascii=False, sort_keys=True))
    print('FINGERPRINT=' + fp)

    if len(argv) == 2 and argv[0] == '--save':
        Path(argv[1]).write_text(fp + '\n' + text + '\n', encoding='utf-8')
        print(f'saved -> {argv[1]}')
    elif len(argv) == 2 and argv[0] == '--check':
        body = Path(argv[1]).read_text(encoding='utf-8').split('\n', 1)
        want = body[0].strip()
        if want != fp:
            print('MISMATCH')
            old = json.loads(body[1]) if len(body) > 1 else {}
            new = json.loads(text)
            for tag in sorted(set(old) | set(new)):
                for kind in ('images', 'music', 'sound', 'voices'):
                    a = (old.get(tag) or {}).get(kind) or []
                    b = (new.get(tag) or {}).get(kind) or []
                    if a != b:
                        print(f'  {tag}.{kind}: {len(a)} -> {len(b)}'
                              f'  少了{sorted(set(a) - set(b))[:5]}'
                              f'  多了{sorted(set(b) - set(a))[:5]}')
            return 1
        print('MATCH')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
