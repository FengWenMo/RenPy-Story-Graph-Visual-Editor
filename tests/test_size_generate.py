"""
tests/test_size_generate.py
===========================
**尺寸面板 →「生成 sg_*」的端到端**（2026-10-31，护栏 ① ② 的落点）。

纯函数那三条护栏在 `tests/test_sg_transforms.py`；"行里该列什么"在
`tests/test_stage_size_rows.py`。这里只钉**窗口层**那一步：

  · 确认 → 真的写 `game/sg_transforms.rpy` + 真的改到那一拍（缓存也刷了）；
  · 取消 → 文件与拍**一个字节都不动**；
  · 文件被手改过 → 条目置灰、硬发请求也不写，控制台说清原因；
  · 确认框里有**两件事**（脚本 diff + 文件 diff），不是只写脚本。

⚠ 项目是 `conftest.ui_window` 现场生成的那个（1×1 的 PNG、1920×1080）：
一张 1×1 的图「铺满」= 960×960 的框，项目里没有等价 transform → 正好走生成。
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
pytest.importorskip('PySide6')

from PySide6.QtWidgets import QMessageBox                     # noqa: E402

from storygraph.app.stage_render import available              # noqa: E402
from storygraph.ui_qt import card_actions as CA               # noqa: E402
from storygraph.ui_qt import main_window as MW                # noqa: E402

pytestmark = pytest.mark.skipif(not available(), reason='没装 Pillow')


def _yes(*_a, **_k):
    return QMessageBox.StandardButton.Yes


def _no(*_a, **_k):
    return QMessageBox.StandardButton.No


def _scene_beat_index(win) -> int:
    for i, b in enumerate(win._session.beats):
        if str(getattr(b, 'scene', '') or '').strip():
            return i
    raise AssertionError('这一章没有 scene，测不了尺寸面板')


def _rows(win):
    return win._panel.size_panel().rows()


def _item_index(row, key):
    for i, item in enumerate(row['items']):
        if item['key'] == key:
            return i
    raise AssertionError(f'没有 {key}：{[i["key"] for i in row["items"]]}')


def _click(win, key, index=0):
    """在 `index` 行的下拉里选中预设 `key` 并按下「改」。"""
    panel = win._panel.size_panel()
    slot = panel.slots()[index]
    slot['combo'].setCurrentIndex(_item_index(panel.rows()[index], key))
    assert slot['button'].isEnabled(), panel.rows()[index]['items']
    slot['button'].click()


def _sg_file(win):
    return win._project_root / 'game' / 'sg_transforms.rpy'


class TestGenerateFromCard:
    def test_confirm_writes_the_file_and_edits_the_beat(self, ui_window,
                                                        monkeypatch):
        win = ui_window
        monkeypatch.setattr(CA.QMessageBox, 'question', staticmethod(_yes))
        i = _scene_beat_index(win)
        win._show_index(i)
        beat = win._session.beats[i]
        assert not str(getattr(beat, 'scene_at', '') or '')
        cover = _rows(win)[0]['items'][_item_index(_rows(win)[0], 'cover')]
        assert cover['generate'] is True and cover['at'] == 'sg_fit_cover'
        _click(win, 'cover')
        assert str(win._session.beats[i].scene_at) == 'sg_fit_cover'
        text = _sg_file(win).read_text(encoding='utf-8')
        assert 'transform sg_fit_cover:' in text and 'fit "cover"' in text
        # 缓存刷了：这一行现在认得出自己就是那条生成的定义
        row = _rows(win)[0]
        assert row['at'] == 'sg_fit_cover'
        cover = row['items'][_item_index(row, 'cover')]
        assert cover['current'] is True and cover['ok'] is False

    def test_the_confirm_box_shows_both_changes(self, ui_window, monkeypatch):
        win = ui_window
        seen = {}

        def _capture(_parent, _title, text, *_a, **_k):
            seen['text'] = text
            return QMessageBox.StandardButton.Yes

        monkeypatch.setattr(CA.QMessageBox, 'question',
                            staticmethod(_capture))
        win._show_index(_scene_beat_index(win))
        _click(win, 'cover')
        text = seen.get('text') or ''
        assert 'sg_fit_cover' in text
        assert 'game/sg_transforms.rpy' in text        # 要动的项目文件
        assert '+ transform sg_fit_cover:' in text    # 文件 diff
        assert 'scene_at' in text                      # 脚本 diff

    def test_cancel_touches_nothing(self, ui_window, monkeypatch):
        win = ui_window
        monkeypatch.setattr(CA.QMessageBox, 'question', staticmethod(_no))
        i = _scene_beat_index(win)
        win._show_index(i)
        _click(win, 'cover')
        assert not str(getattr(win._session.beats[i], 'scene_at', '') or '')
        assert not _sg_file(win).exists()

    def test_a_hand_edited_file_is_refused_out_loud(self, ui_window,
                                                    monkeypatch):
        """文件被手改过 → 条目置灰；就算硬发请求也不写、不改、说清原因。"""
        win = ui_window
        monkeypatch.setattr(CA.QMessageBox, 'question', staticmethod(_yes))
        sg = _sg_file(win)
        sg.write_text('# 我自己写的\ntransform mine:\n    zoom 2\n',
                      encoding='utf-8')
        win._show_index(_scene_beat_index(win))
        row = _rows(win)[0]
        cover = row['items'][_item_index(row, 'cover')]
        assert cover['ok'] is False and cover['generate'] is False
        assert '不是工具生成的' in cover['why']
        before = str(win._session.beats[_scene_beat_index(win)].scene_at or '')
        plan = MW.apply_size_from_card(win, {
            'row': row['index'], 'key': 'cover', 'at': 'sg_fit_cover',
            'kind': 'scene', 'tag': '', 'spec': 'bg classroom', 'source': 0,
            'generate': True, 'natural': (1, 1)})
        assert plan is None
        assert '生成 transform 没做' in win._console.toPlainText()
        assert str(win._session.beats[_scene_beat_index(win)].scene_at or '') \
            == before
        assert sg.read_text(encoding='utf-8') == \
            '# 我自己写的\ntransform mine:\n    zoom 2\n'

    def test_a_generate_request_that_dedups_uses_the_existing_name(
            self, ui_window, monkeypatch):
        """护栏 ② 的窗口层那一半：查重命中 → 不写文件，直接用现成的名字。

        构造：项目里有一条 `transform bgfit: fit "cover"`（人写的，几何上就是
        「铺满」），而面板那条请求里 `at` 是个还不存在的 `sg_fit_cover`
        （模拟"面板那一刻还没看到 bgfit"）。写盘前再查一遍重就该选 `bgfit`。
        """
        win = ui_window
        monkeypatch.setattr(CA.QMessageBox, 'question', staticmethod(_yes))
        (win._project_root / 'game' / 'mine.rpy').write_text(
            'transform bgfit:\n    fit "cover"\n', encoding='utf-8')
        win._session.refresh_transform_index()
        i = _scene_beat_index(win)
        win._show_index(i)
        plan = MW.apply_size_from_card(win, {
            'row': 0, 'key': 'cover', 'at': 'sg_fit_cover', 'kind': 'scene',
            'tag': '', 'spec': 'bg classroom', 'source': 0,
            'generate': True, 'natural': (1, 1)})
        assert plan is not None and plan.ok
        assert str(win._session.beats[i].scene_at) == 'bgfit'
        assert not _sg_file(win).exists(), '查重命中却还是写了文件'


# ==================================================================
#  ③ 打开项目时点名
# ==================================================================

def _fake_win(root):
    """只带 `_log` / `_status` / `_project_root` 的假窗口（点名这件事不碰控件）。"""

    class _Win:
        def __init__(self):
            self._project_root = root
            self.logs = []
            self.status = []

        def _log(self, text):
            self.logs.append(str(text))

        def _status(self, text):
            self.status.append(str(text))

    return _Win()


class TestPointItOutOnOpen:
    def test_a_missing_reference_is_named_when_the_project_opens(self, tmp_path):
        from storygraph.ui_qt.window_graph_ops import \
            report_missing_sg_transforms

        root = tmp_path / 'proj'
        (root / 'game').mkdir(parents=True)
        (root / 'game' / 'a.rpy').write_text(
            'label a:\n    scene bg room at sg_fit_cover\n', encoding='utf-8')
        win = _fake_win(root)
        missing = report_missing_sg_transforms(win)
        assert [m['name'] for m in missing] == ['sg_fit_cover']
        text = '\n'.join(win.logs)
        assert 'sg_fit_cover' in text and 'game/a.rpy:2' in text
        assert 'NameError' in text
        assert win.status and 'sg_*' in win.status[0]

    def test_a_clean_project_says_nothing(self, tmp_path):
        from storygraph.ui_qt.window_graph_ops import \
            report_missing_sg_transforms

        root = tmp_path / 'proj'
        (root / 'game').mkdir(parents=True)
        (root / 'game' / 'a.rpy').write_text(
            'transform sg_fit_cover:\n    fit "cover"\n'
            'label a:\n    scene bg room at sg_fit_cover\n', encoding='utf-8')
        win = _fake_win(root)
        assert report_missing_sg_transforms(win) == []
        assert win.logs == [] and win.status == []

    def test_no_project_is_not_an_error(self):
        from storygraph.ui_qt.window_graph_ops import \
            report_missing_sg_transforms

        win = _fake_win(None)
        assert report_missing_sg_transforms(win) == []
        assert win.logs == []
