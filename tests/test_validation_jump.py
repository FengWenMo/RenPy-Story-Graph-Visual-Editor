"""
校验清单的"点得到那一拍"（2026-10-31，UX 建议 ③）。

判据只有三条，全部围绕"**不许给死按钮**"：

  ① 有坐标的条目 → 双击发出信号，且字段完整；
  ② **没有坐标的条目 → 双击什么都不发**（显示"无位置"，不给入口）；
  ③ 坐标只认两种形态（`line_id` / `label.seq`），**认不出的不猜、不跳**。

为什么要这么较真：从一个"点了不动"的按钮里，用户学不到任何东西 ——
它比没有按钮更消耗信任。所以"能不能跳"必须在**显示入口之前**就判掉。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.ui_qt.validation_list import (ValidationListDialog,
                                              issue_fields)


# ---------------------------------------------------------------------------
#  ① 字段归一
# ---------------------------------------------------------------------------

def test_issue_fields_两种形态都取得到():
    """`validate_all` 返回对象，老调用方传 dict —— 两边都得能取。"""
    class Issue:
        level, code, severity = 'error', 'E305', 'error'
        message, where, hint = '引用了不存在的图', 'chapter1.0042', '改图名'

    a = issue_fields(Issue())
    b = issue_fields({'level': 'error', 'code': 'E305', 'severity': 'error',
                      'message': '引用了不存在的图', 'where': 'chapter1.0042',
                      'hint': '改图名'})
    assert a == b, (a, b)
    assert a['where'] == 'chapter1.0042'


def test_issue_fields_缺字段给空串不是_None():
    """缺字段不能是 `None` —— 下游 `str(x)` 会得到 `'None'` 塞进界面。"""
    got = issue_fields({})
    assert all(isinstance(v, str) for v in got.values()), got
    assert got['where'] == ''


# ---------------------------------------------------------------------------
#  ② 对话框：有位置发信号，没位置不发（核心判据）
# ---------------------------------------------------------------------------

def _dialog(errors, warnings=()):
    return ValidationListDialog(None, errors=list(errors),
                                warnings=list(warnings))


def test_有位置的条目双击发信号(qapp):
    dlg = _dialog([{'level': 'error', 'code': 'E305', 'message': 'x',
                    'where': 'chapter1.0042'}])
    got = []
    dlg.issue_activated.connect(got.append)
    item = dlg._tree.topLevelItem(0).child(0)
    dlg._on_double(item, 0)
    assert got and got[0]['where'] == 'chapter1.0042', got


def test_没有位置的条目双击不跳(qapp):
    """**这条是"不许给死按钮"的判据。**"""
    dlg = _dialog([{'level': 'error', 'code': 'E999', 'message': '没坐标'}])
    got = []
    dlg.issue_activated.connect(got.append)
    item = dlg._tree.topLevelItem(0).child(0)
    dlg._on_double(item, 0)
    assert not got, '没有 where 的条目不该发出跳转信号'
    assert '无位置' in item.text(1), item.text(1)
    assert '跳不过去' in dlg._status.text()


def test_双击分组标题行不跳(qapp):
    dlg = _dialog([{'level': 'error', 'message': 'x', 'where': 'c.0001'}])
    got = []
    dlg.issue_activated.connect(got.append)
    top = dlg._tree.topLevelItem(0)
    dlg._on_double(top, 0)
    assert not got, '标题行不是条目'


def test_空结果说明白而不是空白一片(qapp):
    dlg = _dialog([])
    assert '干净' in dlg._status.text(), dlg._status.text()


# ---------------------------------------------------------------------------
#  ③ 定位解析：认得出才跳（用鸭子类型的假窗口，不建真窗口）
# ---------------------------------------------------------------------------

class _Beat:
    def __init__(self, label, seq, line_id=''):
        self.label, self.seq, self.line_id = label, seq, line_id


class _FakeWin:
    """只实现 `_locate_issue` 用到的那几个东西。"""

    def __init__(self, beats):
        self._session = type('S', (), {'beats': beats})()
        self.jumped = []
        self.status = []
        self.logs = []
        self.show_beat = self._show_beat

    def _show_beat(self, idx):
        if 0 <= idx < len(self._session.beats):
            self.jumped.append(idx)
            return True
        return False

    def _status(self, text):
        self.status.append(text)

    def _log(self, text):
        self.logs.append(text)


def _locate(beats, where):
    from storygraph.ui_qt.window_graph_ops import GraphOpsMixin
    win = _FakeWin(beats)
    GraphOpsMixin._locate_issue(win, {'where': where})
    return win


BEATS = [_Beat('chapter1', 1), _Beat('chapter1', 2),
         _Beat('chapter2', 1, line_id='L-abc')]


def test_label_seq_定位到那一拍():
    win = _locate(BEATS, 'chapter1.0002')
    assert win.jumped == [1], win.jumped


def test_line_id_优先于_label_seq():
    win = _locate(BEATS, 'L-abc')
    assert win.jumped == [2], win.jumped


def test_认不出的坐标不跳也不猜():
    """`label` 里没有这段、或者 seq 不是数字 —— **一个都不许跳**。"""
    for where in ('chapter9.0001', 'chapter1.abc', 'chapter1', '', '   '):
        win = _locate(BEATS, where)
        assert win.jumped == [], f'{where!r} 不该跳，实际跳了 {win.jumped}'


def test_跳不过去要说出来():
    win = _locate(BEATS, 'chapter9.0001')
    assert any('跳不过去' in s for s in win.logs), win.logs


def test_越界的拍号不假跳():
    """`show_beat` 返回 False 时，不能记"已定位"。"""
    win = _FakeWin(BEATS)
    win.show_beat = lambda idx: False
    from storygraph.ui_qt.window_graph_ops import GraphOpsMixin
    GraphOpsMixin._locate_issue(win, {'where': 'chapter1.0002'})
    assert not win.jumped
    assert not any('已定位' in s for s in win.status), win.status


if __name__ == '__main__':                      # 本机没 pytest 时的兜底
    import inspect
    import traceback

    _qapp = None
    try:
        from PySide6.QtWidgets import QApplication
        _qapp = QApplication.instance() or QApplication([])
    except Exception:                            # noqa: BLE001
        print('没有 PySide6 → 跳过 Qt 用例')

    funcs = [(n, f) for n, f in sorted(globals().items())
             if n.startswith('test_') and callable(f)]
    failed = 0
    for name, fn in funcs:
        try:
            if 'qapp' in inspect.signature(fn).parameters:
                if _qapp is None:
                    print(f'  skip {name}')
                    continue
                fn(_qapp)
            else:
                fn()
            print(f'  OK   {name}')
        except Exception:                        # noqa: BLE001
            failed += 1
            print(f'  FAIL {name}')
            traceback.print_exc()
    print(f'\n{len(funcs) - failed}/{len(funcs)} 通过')
    raise SystemExit(1 if failed else 0)
