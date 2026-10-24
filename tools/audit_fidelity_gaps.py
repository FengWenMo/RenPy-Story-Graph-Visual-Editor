# -*- coding: utf-8 -*-
"""
审计 · **卡牌/模块编辑器会改坏用户脚本的四处**（2026-10-24）

这不是常规测试，是**复现脚本**：每条用例都对着一个真实缺陷，
现在全部复现出来（红了才说明缺陷在）。修好后应当全绿 ——
届时应把它转成 `tests/test_*.py` 里的正式判据。

跑法（必须用带 openpyxl 的解释器）：

    D:\\Python\\python.exe tools\\audit_fidelity_gaps.py

判据（每条都是"用户什么都没写错，产物却坏了"）：

  · G1 同拍「普通 show + 带子句的 show」→ 子句那条**整条消失**
        （`show bg with dissolve` 里的 with 还会挪到别人头上）
  · G2 `show expression 表达式` → 导出后**丢了 `expression` 关键字**
        （产物变成 `show holo("x")`，引擎会当图像名解析 → 直接报错）
  · G3 说话人不是 ASCII 标识符（中文/日文角色名）→ **每导出一次多一行**旧台词
  · G4 首次导出（有改动）会往文件头插 2 行生成标记 —— 字节侵入，不是错误但要知道

每条都走**真实链路**：`EditorSession.load → apply(卡片) → export(原地)`。
"""
from __future__ import annotations

import difflib
import io
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ('utf-8', 'utf8'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from storygraph.app.editor import EditorSession       # noqa: E402


def _project(src: str) -> tuple:
    tmp = Path(tempfile.mkdtemp(prefix='sg_audit_'))
    game = tmp / 'game'
    game.mkdir(parents=True)
    path = game / 'script.rpy'
    path.write_bytes(src.encode('utf-8'))
    return tmp, path


def _read(path: Path) -> str:
    return open(path, encoding='utf-8', newline='').read()


def _diff(before: str, after: str) -> str:
    return ''.join(difflib.unified_diff(before.splitlines(True),
                                        after.splitlines(True),
                                        '原文', '导出后', n=1))


def _submit_all(s: EditorSession, change_index: int | None = None,
                new_text: str = '') -> None:
    """把每一拍都"打开卡片 → 提交"一遍（翻页/保存走的就是这条路）。"""
    for i in range(len(s.beats)):
        f = s.fields(i)
        if i == change_index:
            f['Text'] = new_text
        s.apply(i, f, cards=s.cards(i), advanced_open=True,
                stage_loaded=f.get('Stage'))


# ----------------------------------------------------------------------
# G1：同一拍里「普通 show + 带子句 show」
# ----------------------------------------------------------------------
def g1_clause_show():
    src = ('label start:\n'
           '    show e happy\n'
           '    show bg library with dissolve\n'
           '    "台词"\n'
           '    return\n')
    tmp, path = _project(src)
    s = EditorSession(project_root=tmp)
    s.load(path)
    print('G1 载入：beat.show = %r' % s.beats[0].show)
    print('G1 卡片：%r' % [c.get('image') for c in s.cards(0)])
    _submit_all(s)
    print('G1 提交后：beat.show = %r' % s.beats[0].show)
    s.export(path)
    after = _read(path)
    ok = 'show bg library with dissolve' in after
    print('G1 结论：%s' % ('✅ 保住了' if ok else '❌ **with 那条没了**'))
    print(_diff(src, after))


# ----------------------------------------------------------------------
# G2：show expression
# ----------------------------------------------------------------------
def g2_show_expression():
    src = ('label start:\n'
           '    show expression holo("lan base") at i11\n'
           '    show e happy\n'
           '    "台词"\n'
           '    return\n')
    tmp, path = _project(src)
    s = EditorSession(project_root=tmp)
    s.load(path)
    _submit_all(s)
    print('G2 提交后：beat.show = %r' % s.beats[0].show)
    s.export(path)
    after = _read(path)
    ok = 'show expression holo("lan base") at i11' in after
    print('G2 结论：%s' % ('✅ 保住了' if ok else '❌ **expression 关键字丢了**'))
    print(_diff(src, after))


# ----------------------------------------------------------------------
# G3：非 ASCII 说话人（中文/日文角色名）
# ----------------------------------------------------------------------
def g3_nonascii_speaker():
    src = ('label start:\n'
           '    凌跃捷 "第一句"\n'
           '    凌跃捷 "第二句"\n'
           '    return\n')
    tmp, path = _project(src)
    s = EditorSession(project_root=tmp)
    s.load(path)
    n0 = len(s.beats)
    _submit_all(s, change_index=0, new_text='第一句（改）')
    s.export(path)
    n1 = len(s.beats)
    after = _read(path)
    lines = [ln for ln in after.splitlines()
             if ln.strip() and not ln.startswith('#')]
    dup = len([ln for ln in lines if '第一句' in ln]) - 1
    print('G3 拍数 %d → %d | 产物里「第一句」出现 %d 次'
          % (n0, n1, dup + 1))
    print('G3 结论：%s' % ('✅ 没重复' if dup == 0
                          else '❌ **多出 %d 行旧台词**（每导一次还会再长）' % dup))
    print(_diff(src, after))


# ----------------------------------------------------------------------
# G4：首次导出的 2 行生成标记
# ----------------------------------------------------------------------
def g4_marker():
    src = 'label start:\n    e "one"\n    return\n'
    tmp, path = _project(src)
    s = EditorSession(project_root=tmp)
    s.load(path)
    _submit_all(s, change_index=0, new_text='one!')
    s.export(path)
    after = _read(path)
    added = [ln for ln in after.splitlines() if ln.startswith('#')]
    print('G4 文件头新增 %d 行：%r' % (len(added), added))


if __name__ == '__main__':
    for fn in (g1_clause_show, g2_show_expression, g3_nonascii_speaker, g4_marker):
        print('\n' + '=' * 72)
        try:
            fn()
        except Exception as exc:                     # noqa: BLE001
            print('%s 跑挂了：%r' % (fn.__name__, exc))
