"""Describe 金标：`describe_statements` 的**同输入 → 同结果**（M4 拆它的护栏）。

它是"IDE 提示 / 文档 / 能力覆盖"那一面的数据源：少报一条声明、把 builtin 记成
未知，都不会让人当场看见 —— 所以金标覆盖**两种生产调用形状**：

  ① CLI 那条  ：`describe_statements(project_root=…)`（`cli.py describe`）
  ② build 那条：`describe_statements(project_root=…, statements=解析出来的语句)`
     —— 带 counts 的那条路（`pipeline/build.py` 里用来报"猜出来的结构"）

指纹 = 返回 dict 归一化后（`project` 换成 `<ROOT>`）的 JSON。

    python tools/probe_describe_fingerprint.py [--save|--check <file>]
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.parsing.discover import discover_scripts          # noqa: E402
from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer  # noqa: E402
from storygraph.story.statement_registry import describe_statements  # noqa: E402

REAL = Path(r'D:\千连万理\RenpyTest\sr_general')

#: 合成项目：**有声明**（真机没有）—— 才压得到 declared / builtin / shape-guess 三条路
RULES = {
    'statements': [{
        'name': 'mycue', 'label': '闪电提示', 'block': True,
        'columns': [
            {'name': 'intensity', 'label': '强度', 'choices': ['1', '2', '3']},
            {'name': 'target', 'label': '目标'},
        ],
    }],
}
SCRIPT = (
    'label a:\n'
    '    "一句台词"\n'
    '    # 一条注释\n'
    '    mycue "闪电" intensity=3 target=ling:\n'
    '        target = ling\n'
    '    with dissolve\n'
    '    l oh "3"\n'
    '    while x < 3:\n'
    '        "循环里"\n'
    '    return\n'
    'label b:\n'
    '    mycue "又一下" intensity=1:\n'
    '        pass\n'
    '    magiccue "没声明过的"\n'
    '    camera bg:\n'
    '        xpos 1\n'
)


def _synthetic(tmp: Path) -> Path:
    root = tmp / 'proj'
    (root / 'game').mkdir(parents=True, exist_ok=True)
    (root / 'game' / 'ch.rpy').write_text(SCRIPT, encoding='utf-8')
    (root / 'storygraph.rules.json').write_text(
        json.dumps(RULES, ensure_ascii=False), encoding='utf-8')
    return root


def _all_statements(root: Path):
    az = IncrementalRenPyAnalyzer()
    out = []
    for f in sorted(discover_scripts(root)):
        pr, _ = az.analyze(files=[f], graph=None)
        out.extend(getattr(pr, 'statements', None) or [])
    return out


def fingerprint(root: Path = REAL):
    import tempfile
    from storygraph.story.statement_registry import clear_cache

    cases = {
        'cli': describe_statements(project_root=root),
        'build': describe_statements(project_root=root,
                                     statements=_all_statements(root)),
    }
    # 合成项目（有声明）：清缓存后再跑，避免用到真机那条路的缓存
    with tempfile.TemporaryDirectory(prefix='describe_fp_') as td:
        proj = _synthetic(Path(td))
        clear_cache()
        cases['synthetic'] = describe_statements(project_root=proj)
        clear_cache()
        cases['synthetic_build'] = describe_statements(
            project_root=proj, statements=_all_statements(proj))
    clear_cache()
    for c in cases.values():
        c['project'] = '<ROOT>'            # 绝对路径不参与指纹
    text = json.dumps(cases, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(text.encode()).hexdigest(), text, cases


def main() -> int:
    root = REAL
    argv = sys.argv[1:]
    if argv and argv[0] not in ('--save', '--check'):
        root = Path(argv[0])
        argv = argv[1:]
    fp, text, cases = fingerprint(root)
    print('summary:',
          json.dumps({k: v['summary'] for k, v in cases.items()},
                     ensure_ascii=False, sort_keys=True))
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
            for tag in sorted(set(old) | set(cases)):
                a, b = old.get(tag) or {}, cases.get(tag) or {}
                if a != b:
                    print(f'  [{tag}] summary 旧 {a.get("summary")}')
                    print(f'  [{tag}] summary 新 {b.get("summary")}')
                    ra = {r['name']: r for r in a.get('statements') or []}
                    rb = {r['name']: r for r in b.get('statements') or []}
                    for n in sorted(set(ra) | set(rb)):
                        if ra.get(n) != rb.get(n):
                            print(f'    {n}:\n      旧 {ra.get(n)}'
                                  f'\n      新 {rb.get(n)}')
            return 1
        print('MATCH')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
