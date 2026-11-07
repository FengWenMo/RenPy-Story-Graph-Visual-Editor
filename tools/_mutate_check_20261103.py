# -*- coding: utf-8 -*-
"""
变异验证：**把修复改回去，判据必须变红**（2026-11-03）
====================================================

地狱测试方法论第四节：一条从没红过的测试等于没有测试。
这一轮改了四处，每处都要单独变异（否则另一半能悄悄退化而测试全绿）。

跑法：
    D:\\Python\\python.exe tools/_mutate_check_20261103.py

每行输出：变异名 / 是否红 / 红在哪条判据。
任何一条**没红**都算失败 —— 说明那条判据钉不住这个缺陷。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable

#: (名字, 文件, 原文, 改成, 应该红的判据文件)
MUTATIONS = [
    (
        'M1 去掉「项目声明优先」层（提示只看角色表）',
        'storygraph/ui_qt/sprite_grid.py',
        "        declared = _declared_attr_hint(tag, want, symbols)\n"
        "        if declared is not None:\n"
        "            return declared\n",
        "        declared = None\n",
        'tests/test_spec_self_consistency.py::TestWarningOrder::test_declaration_wins_and_shuts_up',
    ),
    (
        'M2 去掉「生成器自校验」（映射填显示名照样产出）',
        'storygraph/app/picker.py',
        "            bad = spec_tag_problem(spec, known_tags)\n"
        "            if bad:\n"
        "                return PickResult(identity=ident, ok=False, message=bad)\n",
        "",
        'tests/test_spec_self_consistency.py::TestPickerRefusesItEndToEnd::test_display_name_mapping_is_refused_with_a_reason',
    ),
    (
        'M3 去掉「名字不许拼两遍」',
        'storygraph/assets/asset_identity.py',
        "    stem = _stem_of(str((row or {}).get('Path') or ''))\n"
        "    if stem and tag and stem.lower().startswith(tag.lower() + '_'):\n"
        "        words = [str(w).strip() for w in (identity.attrs or ()) if str(w).strip()]\n"
        "        if len(words) == 1 and words[0].lower() == stem.lower():\n"
        "            return stem\n",
        "",
        'tests/test_spec_self_consistency.py::TestNameIsNotDoubled::test_stem_already_carries_the_tag',
    ),
    (
        'M4 去掉「@ 后缀不是属性」',
        'storygraph/ui_qt/sprite_grid.py',
        "    want = parts[1].split('@')[0].strip() if len(parts) > 1 else ''",
        "    want = parts[1] if len(parts) > 1 else ''",
        'tests/test_spec_self_consistency.py::TestWarningOrder::test_at_suffix_is_not_an_attribute',
    ),
    (
        'M6 参数齐了也重建上下文（平方级回来）',
        'storygraph/app/picker.py',
        "    if not need:\n        return None",
        "    if False:\n        return None",
        'tests/test_picker_context_reuse.py::TestContextIsNotRebuilt::test_complete_arguments_skip_the_context',
    ),
    (
        'M7 有 context 却因为没 registry 就忽略它',
        'storygraph/app/picker.py',
        "    if context is not None:\n        ctx = context",
        "    if registry is not None and context is not None:\n        ctx = context",
        'tests/test_picker_context_reuse.py::TestResultIsTheSameEitherWay::test_context_vs_no_context_agree',
    ),
    (
        'M5 去掉「大小写/下划线给正确写法」',
        'storygraph/ui_qt/sprite_grid.py',
        "        spell = _spelling_hint(tag, symbols)\n"
        "        if spell:\n"
        "            return spell\n",
        "",
        'tests/test_spec_self_consistency.py::TestWarningOrder::test_uppercase_gets_the_right_spelling',
    ),
]


def run_one(name: str, rel: str, old: str, new: str, expect: str) -> bool:
    path = ROOT / rel
    original = path.read_text(encoding='utf-8')
    if old not in original:
        print(f'  ! {name}\n      变异点找不到（代码已经变了？）—— 这条没验成')
        return False
    try:
        path.write_text(original.replace(old, new, 1), encoding='utf-8')
        proc = subprocess.run(
            [PY, '-m', 'pytest', expect, '-q', '--no-header',
             '-p', 'no:cacheprovider'],
            cwd=str(ROOT), capture_output=True, text=True, timeout=180)
        went_red = proc.returncode != 0
        if went_red:
            tail = [ln for ln in proc.stdout.splitlines()
                    if 'failed' in ln or 'error' in ln][-1:] or ['']
            print(f'  ✓ {name}\n      变异后变红（{tail[0].strip()[:70]}）')
        else:
            print(f'  ✗ {name}\n      变异后**仍然全绿** —— 这条判据钉不住它！')
        return went_red
    finally:
        path.write_text(original, encoding='utf-8')


def main() -> int:
    print('=' * 66)
    print('变异验证：改回旧实现，判据必须变红')
    print('=' * 66)
    bad = 0
    for name, rel, old, new, expect in MUTATIONS:
        try:
            if not run_one(name, rel, old, new, expect):
                bad += 1
        except Exception as exc:                  # noqa: BLE001
            print(f'  ! {name} 抛异常：{type(exc).__name__}: {exc}')
            bad += 1
    print('\n' + '=' * 66)
    if bad:
        print(f'RESULT: FAIL —— {bad}/{len(MUTATIONS)} 条变异没被抓住')
        return 1
    print(f'RESULT: PASS —— {len(MUTATIONS)}/{len(MUTATIONS)} 条变异都被判据抓住')
    return 0


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    raise SystemExit(main())
