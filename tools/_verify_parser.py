#!/usr/bin/env python
"""
验证 `_parse_statement` 拆分**逐节点等价**（P3-12 第五步）。

从 git 取 HEAD 版本的 `renpy_parser.py`，与工作区版本对**同一批真实脚本**
跑解析，比较 AST 节点的类型 + 关键字段 + errors/warnings/labels。

为什么要比节点：`_parse_statement` 的活儿就是"认得出是哪种语句、字段对不对"。
数量一样而类型/字段错了照样是坏的 —— 而"数量一样"恰好最容易骗过人。

⚠ 旧版**按文件路径**加载（两个模块不能同名，会互相覆盖）；它 import 的
其它 `storygraph.*` 都指向工作区那份 —— 这一步只换 `renpy_parser` 一个模块。
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def sig(node) -> tuple:
    """AST 节点的可比签名：类型 + 关键字段（跳过位置元信息）。"""
    if node is None:
        return ('None',)
    d = {}
    for k, v in vars(node).items():
        if k in ('line_number', 'filename'):
            continue
        if isinstance(v, (list, tuple)):
            v = tuple(sig(x) if hasattr(x, '__dict__') else str(x) for x in v)
        elif hasattr(v, '__dict__'):
            v = sig(v)
        elif isinstance(v, dict):
            v = tuple(sorted(
                (str(a),
                 str(sig(b) if hasattr(b, '__dict__') else b))
                for a, b in v.items()))
        elif isinstance(v, set):
            v = tuple(sorted(str(x) for x in v))
        else:
            v = str(v)
        d[k] = v
    return (type(node).__name__, tuple(sorted(d.items())))


def main() -> int:
    old_src = subprocess.run(
        ['git', 'show', 'HEAD:storygraph/parsing/renpy_parser.py'],
        cwd=ROOT, capture_output=True, text=True, encoding='utf-8').stdout
    tmp = ROOT / 'tools' / '_renpy_parser_head.py'
    tmp.write_text(old_src, encoding='utf-8')
    try:
        OLD = load(tmp, '_rpp_head')
        NEW = load(ROOT / 'storygraph' / 'parsing' / 'renpy_parser.py',
                   'storygraph.parsing.renpy_parser')

        from _source_paths import REAL_PROJECT
        files = sorted((REAL_PROJECT / 'game').rglob('*.rpy'))
        print(f'真实项目脚本 {len(files)} 个')

        cases = bad = nodes = 0
        for fp in files:
            pr_old, _ = OLD.IncrementalRenPyAnalyzer().analyze(files=[fp],
                                                              graph=None)
            pr_new, _ = NEW.IncrementalRenPyAnalyzer().analyze(files=[fp],
                                                               graph=None)
            cases += 1
            a = [sig(x) for x in pr_old.statements]
            b = [sig(x) for x in pr_new.statements]
            nodes += len(a)
            if (a == b
                    and list(pr_old.errors) == list(pr_new.errors)
                    and list(pr_old.warnings) == list(pr_new.warnings)
                    and list(pr_old.labels) == list(pr_new.labels)):
                continue
            bad += 1
            if bad <= 3:
                print(f'** {fp.name} 不一致（旧 {len(a)} / 新 {len(b)} 条）')
                for i in range(max(len(a), len(b))):
                    x = a[i] if i < len(a) else None
                    y = b[i] if i < len(b) else None
                    if x != y:
                        print(f'   第 {i} 条 旧={str(x)[:110]}')
                        print(f'            新={str(y)[:110]}')
                        break
                if list(pr_old.errors) != list(pr_new.errors):
                    print('   errors:', pr_old.errors[:2], '≠',
                          pr_new.errors[:2])
                if list(pr_old.warnings) != list(pr_new.warnings):
                    print('   warnings:', pr_old.warnings[:2], '≠',
                          pr_new.warnings[:2])

        print(f'\n比了 {cases} 个文件 / {nodes} 条语句，不一致 {bad} 个文件')
        return 1 if bad else 0
    finally:
        tmp.unlink(missing_ok=True)


if __name__ == '__main__':
    sys.exit(main())
