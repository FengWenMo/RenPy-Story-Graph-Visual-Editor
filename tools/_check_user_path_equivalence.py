#!/usr/bin/env python
"""
验证 `core/user_path.app_data_dir` 与**抽取前**的三份实现逐字等价。

为什么要有这个脚本（而不只是跑 pytest）：这次改动是"把三份相同的路径推导
收成一份"。它的正确性判据不是"测试还绿"（测试大多用 env 覆盖绕过了推导），
而是**在各种环境变量组合下，新旧实现给出同一个字符串**。
所以这里把旧实现原样抄一份当参照，逐组合对比。

用完即弃也行 —— 留着的好处是：以后谁再动 `user_path.py`，可以立刻验一次。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.core import user_path  # noqa: E402


# ---------------------------------------------------------------- 旧实现（参照）
def old_ai_settings() -> Path:
    if sys.platform.startswith('win'):
        base = os.environ.get('LOCALAPPDATA') or ''
        if base:
            return Path(base) / 'StoryGraph' / 'ai.json'
    base = os.environ.get('XDG_STATE_HOME') or ''
    root = Path(base) if base else Path.home() / '.local' / 'state'
    return root / 'storygraph' / 'ai.json'


def old_autosave() -> Path:
    if sys.platform.startswith('win'):
        base = os.environ.get('LOCALAPPDATA') or ''
        if base:
            return Path(base) / 'StoryGraph' / 'autosave'
    base = os.environ.get('XDG_STATE_HOME') or ''
    root = Path(base) if base else Path.home() / '.local' / 'state'
    return root / 'storygraph' / 'autosave'


def old_session_store() -> Path:
    if sys.platform.startswith('win'):
        base = os.environ.get('LOCALAPPDATA') or ''
        if base:
            return Path(base) / 'StoryGraph' / 'session.json'
    base = os.environ.get('XDG_STATE_HOME') or ''
    root = Path(base) if base else Path.home() / '.local' / 'state'
    return root / 'storygraph' / 'session.json'


CASES = [
    {'LOCALAPPDATA': r'C:\Users\t\AppData\Local', 'XDG_STATE_HOME': None},
    {'LOCALAPPDATA': None, 'XDG_STATE_HOME': '/tmp/state'},
    {'LOCALAPPDATA': None, 'XDG_STATE_HOME': None},
    {'LOCALAPPDATA': '', 'XDG_STATE_HOME': ''},          # 空串（不是未设）
    {'LOCALAPPDATA': r'C:\Local', 'XDG_STATE_HOME': '/ignored'},
]

PAIRS = [
    ('ai.json', old_ai_settings, lambda: user_path.app_data_dir('ai.json')),
    ('autosave', old_autosave, lambda: user_path.app_data_dir('autosave')),
    ('session.json', old_session_store,
     lambda: user_path.app_data_dir('session.json')),
]


def main() -> int:
    bad = 0
    saved = {k: os.environ.get(k) for k in ('LOCALAPPDATA', 'XDG_STATE_HOME')}
    try:
        for case in CASES:
            for key, value in case.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            for label, old_fn, new_fn in PAIRS:
                old, new = str(old_fn()), str(new_fn())
                mark = 'OK ' if old == new else '差异'
                if old != new:
                    bad += 1
                print(f'{mark} {label:14s} {case}  →  {new}')
                if old != new:
                    print(f'      旧：{old}')
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    print()
    print('全部一致' if bad == 0 else f'{bad} 处不一致 —— 不许合并')
    return 1 if bad else 0


if __name__ == '__main__':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    raise SystemExit(main())
