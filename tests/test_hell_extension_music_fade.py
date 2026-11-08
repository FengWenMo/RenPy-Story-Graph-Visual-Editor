# -*- coding: utf-8 -*-
"""
地狱测试：**音乐自动 fade in / fade out**，全程只在工具里操作（2026-11-08）。

用户原话：

> 自己针对扩展模块再写一个地狱测试……考虑假设这样的情景：播放音乐要自动
> +fade in 1.0，停止音乐要自动+fade out 1.0，若是没有改代码！就从工具中
> 操作……如何做到？建一个模拟项目，进行模拟操作，导出落地，做不到，就说明
> 你的工具仍有问题，要继续修！

这一组**不碰任何一行剧本代码**：

  ① 建一个假项目（一句台词、一条 `play music`、一条 `stop music`、
     还有一条**作者自己已经写了 `fadein 1.0`** 的）；
  ② 打开扩展窗口 → ＋新建 → 种类选「批处理规则」→ 勾「允许把它批处理到
     整章」→ 做什么选「给匹配的行追加」→ 正则 `^\\s*play music\\b`、
     追加 ` fadein 1.0` → 保存；`stop music` / ` fadeout 1.0` 同理；
  ③ 用编辑器导出这一章；
  ④ 断言产物：两条 `play music` 各带且**只带一个** `fadein 1.0`
     （已手写的那条**不许变成两个**）、`stop music fadeout 1.0`；
  ⑤ 再导一次：逐字节不变（幂等）。

判据全部走**真按钮/真表单**（`_new_point` / `cb_op` / `ed_match` / `_save`），
不是自己拼 JSON —— 用户要的是"从工具中操作"。
"""
from __future__ import annotations

import json

_SRC = ('label c1:\n'
        '    play music "bgm/a.mp3"\n'
        '    "一"\n'
        '    stop music\n'
        '    "二"\n'
        '    play music "bgm/bgm02.mp3" fadein 1.0\n'
        '    "三"\n'
        '    return\n')


def _project(tmp_path):
    root = tmp_path / 'music'
    ch = root / 'game' / 'c1.rpy'
    ch.parent.mkdir(parents=True)
    ch.write_text(_SRC, encoding='utf-8')
    return root, ch


def _declare(dlg, *, ident, label, pattern, append):
    """等价于：点＋新建、填表、保存（全部走界面上的控件）。"""
    dlg._new_point()
    dlg.ed_id.setText(ident)
    dlg.ed_label.setText(label)
    dlg.cb_kind.setCurrentIndex(dlg.cb_kind.findData('batch'))
    dlg.ck_batch.setChecked(True)
    dlg.cb_op.setCurrentIndex(dlg.cb_op.findData('append'))
    assert dlg._match_row.isVisibleTo(dlg), '追加模式要露出「匹配哪一行」'
    dlg.ed_match.setText(pattern)
    dlg.ed_text.setPlainText(append)
    assert dlg._save(), dlg.status.text()


def _declare_both(dlg):
    _declare(dlg, ident='music.fadein', label='音乐淡入',
             pattern=r'^\s*play music\b', append=' fadein 1.0')
    _declare(dlg, ident='music.fadeout', label='音乐淡出',
             pattern=r'^\s*stop music\b', append=' fadeout 1.0')


def _export(root, ch):
    from storygraph.app.editor import EditorSession
    s = EditorSession(project_root=root)
    s.load(str(ch))
    return s.export(str(ch))


class TestMusicFadeWithoutTouchingCode:
    def test_the_dialog_really_saved_two_append_rules(self, qapp, tmp_path):
        from storygraph.ui_qt.dialog_extensions import ExtensionDialog
        root, _ch = _project(tmp_path)
        dlg = ExtensionDialog(None, project_root=str(root))
        try:
            _declare_both(dlg)
        finally:
            dlg.close()
        data = json.loads((root / 'storygraph.ext.json').read_text('utf-8'))
        got = {e['id']: e['batch'] for e in data['extensions']}
        assert got['music.fadein']['replace']['append'] == ' fadein 1.0', got
        assert got['music.fadeout']['replace']['append'] == ' fadeout 1.0', got
        assert got['music.fadein']['replace']['match']['pattern'] \
            == r'^\s*play music\b', got

    def test_export_lands_the_fades(self, qapp, tmp_path):
        from storygraph.ui_qt.dialog_extensions import ExtensionDialog
        root, ch = _project(tmp_path)
        dlg = ExtensionDialog(None, project_root=str(root))
        try:
            _declare_both(dlg)
        finally:
            dlg.close()
        res = _export(root, ch)
        assert res['ok'], res
        text = ch.read_text(encoding='utf-8')
        assert 'play music "bgm/a.mp3" fadein 1.0' in text, text
        assert 'stop music fadeout 1.0' in text, text
        # 作者自己写过 fadein 的那一条：**不许变成两个 fadein**
        line = next(x for x in text.splitlines() if 'bgm02' in x)
        assert line.count('fadein') == 1, line

    def test_the_export_says_what_it_did(self, qapp, tmp_path):
        from storygraph.ui_qt.dialog_extensions import ExtensionDialog
        root, ch = _project(tmp_path)
        dlg = ExtensionDialog(None, project_root=str(root))
        try:
            _declare_both(dlg)
        finally:
            dlg.close()
        res = _export(root, ch)
        said = str(res.get('note') or '') + ' '.join(res.get('warnings') or [])
        assert '扩展批处理' in said and '2 行' in said, res

    def test_a_second_export_changes_nothing(self, qapp, tmp_path):
        from storygraph.ui_qt.dialog_extensions import ExtensionDialog
        root, ch = _project(tmp_path)
        dlg = ExtensionDialog(None, project_root=str(root))
        try:
            _declare_both(dlg)
        finally:
            dlg.close()
        _export(root, ch)
        once = ch.read_text(encoding='utf-8')
        _export(root, ch)
        assert ch.read_text(encoding='utf-8') == once

    def test_the_rule_survives_a_reopen_of_the_dialog(self, qapp, tmp_path):
        from storygraph.ui_qt.dialog_extensions import ExtensionDialog
        root, _ch = _project(tmp_path)
        dlg = ExtensionDialog(None, project_root=str(root))
        try:
            _declare_both(dlg)
        finally:
            dlg.close()
        dlg = ExtensionDialog(None, project_root=str(root),
                              focus_id='music.fadein')
        try:
            assert str(dlg.cb_op.currentData()) == 'append'
            assert dlg.ed_match.text() == r'^\s*play music\b'
            assert dlg.ed_text.toPlainText() == ' fadein 1.0'
        finally:
            dlg.close()
