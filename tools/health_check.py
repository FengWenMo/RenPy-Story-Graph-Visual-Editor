#!/usr/bin/env python
"""
通用体检：一次跑完全部**静态**健康指标，输出一张表。
====================================================

为什么要有这个入口
------------------
体检脚本以前是**一次性的**（`tools/_complexity.py` / `_audit_silent_except.py`
/ `_hang_check.py` …，名字都带 `_` 前缀、docstring 里写着"临时"）。
每轮重构各写一个，跑完就忘，于是**同一件事被重写了五遍**，而且
没有任何一次留下基线 —— "比上次好了还是坏了"全靠回忆。

这个文件是**收敛后的入口**：只用标准库，跑完 10 秒内出表，
每次重构前后各跑一次就能看出趋势。

它**不**覆盖的东西（那些是动态检查，另有入口）：
  · 往返保真      → `tools/audit_roundtrip.py`（真实项目，慢）
  · 刁钻/变态测试 → `tools/torture_test.py` / `brutal_test.py`
  · 行为契约      → `pytest`（尤其是 `test_roundtrip_invariants.py`）

用法
----
    D:/Python/python.exe tools/health_check.py            # 出表
    D:/Python/python.exe tools/health_check.py --top 20   # 表更长
    D:/Python/python.exe tools/health_check.py --json     # 机器读

只读，不写盘。退出码 0 = 没有"该修"项，1 = 有。
"""
from __future__ import annotations

import ast
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / 'storygraph'
TESTS = ROOT / 'tests'

def _layering_contract():
    """
    分层契约**从测试里读**，绝不在这里抄一份。

    为什么（这是**实测踩出来的**）：第一版我手抄了 `LAZY_ALLOWED`，
    漏掉 `('assets', 'story')` 这一条 —— 于是把一个**已登记、有正当理由**
    的延迟 import 报成了违规。守卫绿着，我的体检却在喊狼来了。

    这正是 `tests/test_editor_session_mixin_contract.py` 里
    "字段清单不许有两份定义"那一条在防的事，我自己踩了一遍。
    所以：抄不得，读。
    """
    if str(TESTS) not in sys.path:
        sys.path.insert(0, str(TESTS))
    from test_package_layering import (LAYER_INDEX, SIDE_PACKAGES,
                                       LAZY_ALLOWED)
    return LAYER_INDEX, SIDE_PACKAGES, set(LAZY_ALLOWED)

#: 分支节点（mccabe 口径：每加一个分支 +1）
BRANCH = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try,
          ast.ExceptHandler, ast.With, ast.AsyncWith, ast.BoolOp,
          ast.IfExp, ast.comprehension)

#: 判定阈值（超了就是"该修"，退出码会变 1）
THRESHOLD = {'cc': 50, 'module_lines': 800, 'class_methods': 40}


def _py_files(root: Path):
    return sorted(p for p in root.rglob('*.py')
                  if '__pycache__' not in p.parts)


def _cc(node) -> int:
    n = 1
    for sub in ast.walk(node):
        if isinstance(sub, BRANCH):
            n += max(0, len(sub.values) - 1) if isinstance(sub, ast.BoolOp) else 1
    return n


def _pkg_of(path: Path) -> str:
    """`storygraph/story/patcher.py` → `story`；顶层 .py → ''。"""
    try:
        rel = path.relative_to(PKG)
    except ValueError:
        return ''
    return rel.parts[0] if len(rel.parts) > 1 else ''


# ---------------------------------------------------------------- 各项体检

def scale() -> dict:
    src = _py_files(PKG)
    tst = _py_files(TESTS)
    lines = sum(len(p.read_text(encoding='utf-8').splitlines()) for p in src)
    tlines = sum(len(p.read_text(encoding='utf-8').splitlines()) for p in tst)
    return {'源码文件': len(src), '源码行数': lines,
            '测试文件': len(tst), '测试行数': tlines}


def complexity() -> list:
    out = []
    for p in _py_files(PKG):
        try:
            tree = ast.parse(p.read_text(encoding='utf-8'))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                v = _cc(node)
                if v >= 30:
                    out.append((v, node.name, str(p.relative_to(ROOT))))
    return sorted(out, reverse=True)


def god_objects() -> tuple:
    """(模块行数 top, 类方法数 top)。方法数只数**自己定义**的。"""
    mods, classes = [], []
    for p in _py_files(PKG):
        text = p.read_text(encoding='utf-8')
        mods.append((len(text.splitlines()), str(p.relative_to(ROOT))))
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                n = sum(1 for i in node.body
                        if isinstance(i, (ast.FunctionDef, ast.AsyncFunctionDef)))
                classes.append((n, node.name, str(p.relative_to(ROOT))))
    return sorted(mods, reverse=True), sorted(classes, reverse=True)


def smells() -> dict:
    ble = silent = silent_pass = 0
    for p in _py_files(PKG):
        text = p.read_text(encoding='utf-8')
        ble += text.count('noqa: BLE001')
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler):
                silent += 1
                body = [b for b in node.body if not isinstance(b, ast.Pass)]
                if not body and node.body:
                    silent_pass += 1
    return {'noqa: BLE001': ble, 'except 处理器': silent,
            '真·什么都不说(pass)': silent_pass}


def dependencies() -> tuple:
    """(顶层反向边, 未登记的反向延迟 import)。分层契约从测试里读。"""
    layer_index, side_packages, lazy_allowed = _layering_contract()
    top_rev, lazy_rev = [], []
    for p in _py_files(PKG):
        src = _pkg_of(p)
        if not src:
            continue
        try:
            tree = ast.parse(p.read_text(encoding='utf-8'))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            # `import a.b` 与 `from a.b import c` 的节点字段不一样
            if isinstance(node, ast.ImportFrom):
                mod = node.module or ''
            elif node.names:
                mod = node.names[0].name
            else:
                mod = ''
            if not mod.startswith('storygraph.'):
                continue
            tgt = mod.split('.')[1] if len(mod.split('.')) > 1 else ''
            if not tgt or tgt == src:
                continue
            allowed = side_packages.get(src)
            if allowed is not None:
                if tgt not in allowed:
                    top_rev.append((src, tgt, str(p.name)))
                continue
            si, ti = layer_index.get(src), layer_index.get(tgt)
            if si is None or ti is None:
                continue
            # **反向** = 来源比目标更底层（`core` 去 import `ui_qt`）。
            # 顺向（`ui_qt` 取 `core`）是正常写法，不报。
            # 判据与 `tests/test_package_layering.py` 的 `ti > si` 同源。
            top_level = any(node is i for i in tree.body)
            if ti > si:
                (top_rev if top_level else lazy_rev).append((src, tgt))
    lazy_bad = sorted({e for e in lazy_rev if e not in lazy_allowed})
    return sorted(set(top_rev)), lazy_bad


def dead_code() -> list:
    """
    疑似死代码：模块级 def/class，**全项目没有任何别的文件提到它**。

    低置信度（同文件内使用不算、动态取用不算），所以只当提示，不作判定。
    """
    defined = {}                       # name -> [(file, is_exported)]
    for p in _py_files(PKG):
        try:
            tree = ast.parse(p.read_text(encoding='utf-8'))
        except SyntaxError:
            continue
        exported = set()
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(
                    getattr(t, 'id', '') == '__all__' for t in node.targets):
                try:
                    exported = set(ast.literal_eval(node.value))
                except ValueError:
                    exported = set()
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef)):
                defined.setdefault(node.name, []).append(
                    (str(p.relative_to(ROOT)), node.name in exported))

    # 按**标识符**计数（不是子串，`Node` 不会算进 `AudioNode`）。
    # 分两处统计，因为"只有测试在用"和"压根没人用"是**两件事**：
    # 前者可能是"为将来准备的功能"，后者才是真死代码。
    import re

    in_src, in_all = Counter(), Counter()
    for p in _py_files(PKG):
        text = p.read_text(encoding='utf-8')
        for name in defined:
            if len(name) >= 4:
                in_src[name] += len(re.findall(rf'\b{re.escape(name)}\b', text))
    for p in _py_files(TESTS):
        text = p.read_text(encoding='utf-8')
        for name in defined:
            if len(name) >= 4:
                in_all[name] += len(re.findall(rf'\b{re.escape(name)}\b', text))
    dead, test_only = [], []
    for name, where in defined.items():
        if name.startswith('__') or len(name) < 4:
            continue
        if any(x for _, x in where):
            continue                                   # __all__ 导出过
        src_n = in_src[name]
        all_n = src_n + in_all[name]
        if all_n <= 1:
            dead.append((name, where[0][0]))           # 连测试都没碰过
        elif src_n <= 1:
            test_only.append((name, where[0][0]))      # 只有测试在用
    return sorted(dead), sorted(test_only)


# ---------------------------------------------------------------- 出表

def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    top = 10
    if '--top' in argv:
        top = int(argv[argv.index('--top') + 1])
    as_json = '--json' in argv

    s = scale()
    cxs = complexity()
    mods, classes = god_objects()
    sm = smells()
    top_rev, lazy_bad = dependencies()
    dead, test_only = dead_code()

    bad = []
    if cxs and cxs[0][0] > THRESHOLD['cc']:
        bad.append(f"复杂度头部 {cxs[0][0]} > {THRESHOLD['cc']}")
    if mods and mods[0][0] > THRESHOLD['module_lines']:
        bad.append(f"最大模块 {mods[0][0]} 行 > {THRESHOLD['module_lines']}")
    if classes and classes[0][0] > THRESHOLD['class_methods']:
        bad.append(f"最大类 {classes[0][0]} 方法 > {THRESHOLD['class_methods']}")
    if sm['真·什么都不说(pass)']:
        bad.append(f"静默 except {sm['真·什么都不说(pass)']} 处")
    if top_rev:
        bad.append(f'顶层反向依赖 {len(top_rev)} 条')
    if lazy_bad:
        bad.append(f'未登记的反向延迟 import {lazy_bad}')

    if as_json:
        print(json.dumps({'scale': s, 'complexity': cxs[:top],
                          'modules': mods[:top], 'classes': classes[:top],
                          'smells': sm, 'top_reverse': top_rev,
                          'lazy_unregistered': lazy_bad,
                          'dead_code': dead[:top], 'verdict': bad},
                         ensure_ascii=False, indent=2))
        return 1 if bad else 0

    print('=' * 62)
    print('通用体检（静态）')
    print('=' * 62)
    print(f"规模    源码 {s['源码文件']} 文件 / {s['源码行数']} 行；"
          f"测试 {s['测试文件']} 文件 / {s['测试行数']} 行")

    print(f"\n-- 复杂度头部（>=30）top {top} " + '-' * 24)
    for v, name, where in cxs[:top]:
        flag = ' ←该拆' if v > THRESHOLD['cc'] else ''
        print(f'  {v:4d}  {name}()  {where}{flag}')

    print(f'\n-- 最长模块 top 5 ' + '-' * 30)
    for n, where in mods[:5]:
        flag = ' ←该拆' if n > THRESHOLD['module_lines'] else ''
        print(f'  {n:5d} 行  {where}{flag}')

    print(f'\n-- 最重类 top 5 ' + '-' * 32)
    for n, name, where in classes[:5]:
        flag = ' ←上帝对象' if n > THRESHOLD['class_methods'] else ''
        print(f'  {n:3d} 方法  {name}  {where}{flag}')

    print('\n-- 坏味道 ' + '-' * 41)
    for k, v in sm.items():
        print(f'  {k}: {v}')

    print('\n-- 依赖方向 ' + '-' * 39)
    print(f'  顶层反向边: {top_rev or "无"}')
    print(f'  未登记的反向延迟 import: {lazy_bad or "无"}')

    print('\n-- 死代码：连测试都没引用过 ' + '-' * 17)
    for name, where in dead[:top]:
        print(f'  {name}  {where}')
    if not dead:
        print('  无')
    print(f'-- 死代码：只有测试在用 ' + '-' * 23)
    for name, where in test_only[:top]:
        print(f'  {name}  {where}')
    if not test_only:
        print('  无')

    print('\n' + '=' * 62)
    if bad:
        print('判定：有该修的项')
        for b in bad:
            print(f'  · {b}')
    else:
        print('判定：静态指标全部在阈值内')
    print('=' * 62)
    return 1 if bad else 0


if __name__ == '__main__':
    raise SystemExit(main())
