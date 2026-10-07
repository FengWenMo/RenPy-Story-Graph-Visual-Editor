"""
tests/test_ui_cards.py
======================
立绘卡改版的回归：**按角色聚合**、缩略图缓存键、设计 token、
Stage 列里的立绘、裸词位置、字数提示、翻页纯导航。

2026-10-01：tkinter 前端已删除。
  · 纯逻辑（聚合 / 缓存键 / token / Stage 行 / 裸词位置）保持原样，
    只把 import 指向 `storygraph.app`；
  · 原来测 tkinter `ThumbLoader` 桩行为的一组删掉了 —— Qt 版
    `ui_qt.thumb_loader` 用的是 `QTimer` + `'_shutting_down'` 标志，
    根本没有"没有事件循环就偷偷建线程"这个坑，对应测试由
    `tests/test_ui_qt.py` 里的 Qt 用例覆盖；
  · 窗口相关的（翻页纯导航、字数）改成跑**真 Qt 窗口**。
"""

import pytest

from pathlib import Path

from storygraph.story.beat_model import Beat
from storygraph.story.beat_render import make_symbols
from storygraph.ui_qt import tokens as T
from storygraph.app.beats import (beat_sprite_rows, group_sprites_by_tag,
                                  lookup_image_id, stage_sprite_rows,
                                  strip_stage_show_lines)
from storygraph.app.thumbs import cache_key


def _rows(show):
    return beat_sprite_rows(
        Beat(seq=1, label='a', text='喂', show=show), make_symbols(None))


# ==================================================================
#  一、按角色聚合：表情变化不该显示成"多个人"
# ==================================================================

class TestGroupSpritesByTag:
    """
    用户 2026-09-29：
    > 表情变化被当作多个角色立绘处理，外观虽正确但不直观

    Ren'Py 里 `show ling happy` 后再 `show ling sad` 是**换属性**
    （后者替换前者，场上还是一个人），所以展示层要聚合成一张卡。
    """

    def test_same_tag_two_expressions_become_one_card(self):
        rows = _rows('ling happy; ling sad')
        cards = group_sprites_by_tag(rows)
        assert len(cards) == 1, '同一角色换表情必须是**一张卡**'
        card = cards[0]
        assert card['tag'] == 'ling'
        assert card['count'] == 2
        # 用 == 而不是 is：聚合函数会**拷贝**条目（防御性），只保证内容一致
        assert card['active'] == rows[-1], '生效的应是最后一条（RenPy 的替换语义）'
        assert card['active']['image'] == 'ling sad'

    def test_extras_are_preserved(self):
        """聚合是**显示层**的事：其它条目必须留着，写回时不能丢。"""
        rows = _rows('ling happy; ling sad')
        card = group_sprites_by_tag(rows)[0]
        assert [r['image'] for r in card['extras']] == ['ling happy']

    def test_different_tags_stay_separate(self):
        cards = group_sprites_by_tag(_rows('ling happy; ye smile'))
        assert [c['tag'] for c in cards] == ['ling', 'ye']
        assert all(c['count'] == 1 for c in cards)

    def test_multi_position_is_flagged(self):
        """
        同 tag 多条**且位置不同**不是"两个人"，是数据可疑
        （Ren'Py 不会让同 tag 有两个实例）→ 要给警告，而不是静默摊开。
        """
        cards = group_sprites_by_tag(_rows('ling happy@left; ling sad@right'))
        assert cards[0]['multi_position'] is True

    def test_same_position_not_flagged(self):
        cards = group_sprites_by_tag(_rows('ling happy@left; ling sad@left'))
        assert cards[0]['multi_position'] is False

    def test_first_index_keeps_original_order(self):
        cards = group_sprites_by_tag(_rows('ye smile; ling happy; ling sad'))
        assert [c['tag'] for c in cards] == ['ye', 'ling']
        assert cards[1]['first_index'] == 1

    def test_empty_is_empty(self):
        assert group_sprites_by_tag([]) == []
        assert group_sprites_by_tag(None) == []


# ==================================================================
#  二、缩略图缓存键：素材换了必须失效
# ==================================================================

class TestCacheKey:
    """
    缓存键的内核现在住在 `storygraph.app.thumbs`（框架无关）。

    为什么搬家：它原先在 `storygraph/ui/thumb_loader.py`，
    而 **Qt 的缩略图加载器也要用它** —— 于是 Qt 前端
    `from storygraph.ui.thumb_loader import cache_key`，
    等于"Qt 依赖 tkinter"。搬到 app 层才把这个反向依赖摘掉。
    """

    def test_same_input_is_stable(self):
        assert (cache_key('x.png', (88, 118), (1, 2))
                == cache_key('x.png', (88, 118), (1, 2)))

    def test_size_change_differs(self):
        """同图不同目标尺寸 → 不同缓存（卡片档 vs 图库档）。"""
        assert (cache_key('x.png', (88, 118), (1, 2))
                != cache_key('x.png', (64, 86), (1, 2)))

    def test_mtime_change_invalidates(self):
        """素材被替换后必须失效——否则用户看到的是旧图。"""
        assert (cache_key('x.png', (88, 118), (1, 2))
                != cache_key('x.png', (88, 118), (9, 2)))

    def test_size_in_bytes_change_invalidates(self):
        assert (cache_key('x.png', (88, 118), (1, 2))
                != cache_key('x.png', (88, 118), (1, 99)))

    def test_real_file_uses_stat(self, tmp_path):
        """不给 stamp 时自己去 stat（真实调用路径）。"""
        f = tmp_path / 'x.png'
        f.write_bytes(b'12345')
        k1 = cache_key(f, (88, 118))
        f.write_bytes(b'1234567890')
        assert cache_key(f, (88, 118)) != k1, '文件换了键必须跟着换'

    def test_missing_file_is_safe(self, tmp_path):
        """文件不存在也不能抛（界面要能降级成占位）。"""
        assert cache_key(tmp_path / 'nope.png', (88, 118))


# ==================================================================
#  三、设计 token：完整性 + 可审计
# ==================================================================

class TestTokens:
    REQUIRED = ('bg.canvas', 'bg.surface', 'bg.card', 'bg.cardHover',
                'border.default', 'border.strong', 'brand.primary',
                'brand.onPrimary', 'brand.subtle', 'text.primary',
                'text.secondary', 'text.tertiary', 'state.error',
                'state.warning', 'state.info', 'toast.bg', 'toast.fg')

    def test_light_and_dark_have_same_keys(self):
        assert set(T.LIGHT) == set(T.DARK), '两套主题的 token 必须一一对应'

    def test_required_keys_present(self):
        missing = [k for k in self.REQUIRED if k not in T.LIGHT]
        assert not missing, f'缺 token：{missing}'

    def test_unknown_key_fails_loudly(self):
        """取不到的 token 要显眼失败（洋红），不能悄悄给个"看起来正常"的颜色。"""
        assert T.t('不存在的.key') == '#FF00FF'

    def test_card_geometry_is_self_consistent(self):
        """卡片尺寸必须自洽：内边距 + 图 + 名字条 + 徽标行 ≈ 卡片高。"""
        g = T.CARD
        total = (g['pad'] + g['thumbH'] + T.SPACE['xs'] + g['nameH'] + 2
                 + g['chipRowH'] + g['pad'])
        assert abs(total - g['h']) <= 6, f'卡片高度不自洽：{total} vs {g["h"]}'

    def test_compact_card_is_smaller(self):
        assert T.CARD_COMPACT['w'] < T.CARD['w']
        assert T.CARD_COMPACT['h'] < T.CARD['h']

    def test_min_font_size_is_8pt(self):
        """字号下限 8pt（≈11px）：旧代码里的 7pt（≈9px）已取消。"""
        for name, spec in T.FONTS.items():
            assert spec[1] >= 8, f'{name} 是 {spec[1]}pt，小于 8pt 下限'

    def test_tooltip_contrast_is_high(self):
        """Tooltip 深底白字，对比度必须远超 4.5:1（它是深色浮层）。"""
        assert T.LIGHT['toast.bg'] != T.LIGHT['toast.fg']


# ==================================================================
#  四、Stage 列里的立绘（老写法）：用户报"立绘没显示"的那条路
# ==================================================================

class TestStageSpriteRows:
    """
    用户 2026-09-30：
    > 已经导入了的立绘没有显示！！！（确认台上确实有人）

    现场是：立绘写在 **Stage 列**（`[显示] 凌跃捷 开心 左`），
    而立绘区只读 `show` 列 → 台上有人、界面空白。
    """

    def test_reads_show_ops(self):
        rows = stage_sprite_rows('[显示] 凌跃捷 开心 左\n[转场] dissolve')
        assert len(rows) == 1
        row = rows[0]
        assert row['tag'] == '凌跃捷'
        assert row['expression'] == '开心', '裸词位置"左"不能被当成表情'
        assert row['position'] == '左', '裸词位置必须被认出来（否则迁移会丢信息）'
        assert row['from_stage'] is True

    def test_at_form_still_works(self):
        rows = stage_sprite_rows('[显示] 叶怀瑾 hmph at i11')
        assert rows and rows[0]['position'] == 'i11'

    def test_multiple_ops_keep_order(self):
        rows = stage_sprite_rows('[显示] a 开心 左; [显示] b 微笑 右')
        assert [r['tag'] for r in rows] == ['a', 'b']

    def test_only_show_ops_are_taken(self):
        """隐藏 / 转场 / 停顿都不是立绘，不该变成卡片。"""
        assert stage_sprite_rows('[转场] dissolve') == []
        assert stage_sprite_rows('[隐藏] 叶怀瑾') == []
        assert stage_sprite_rows('') == []
        assert stage_sprite_rows(None) == []

    def test_strip_keeps_non_show_lines(self):
        out = strip_stage_show_lines(
            '[显示] a 开心\n[转场] dissolve\n[停顿] 1.0')
        assert '[显示]' not in out, '迁移后 Stage 不该再有立绘指令'
        assert '[转场] dissolve' in out
        assert '[停顿] 1.0' in out


# ==================================================================
#  五、裸词位置：`凌跃捷 开心 左` 里的"左"是位置，不是表情
# ==================================================================

class TestSpriteRowsBarePosition:
    """
    2026-09-30 实测的**静默丢数据**形态：

    show 里写裸词位置（`凌跃捷 开心 左`，DSL 允许）时，如果不识别为位置，
    chip 上会显示"开心 左"；用户一点切换表情，"左"就被当成表情替换掉
    —— 位置**无声无息地没了**。结构化列要求 `@位置`，这里统一归一。
    """

    def _row(self, show):
        return beat_sprite_rows(
            Beat(seq=1, label='a', text='喂', show=show),
            make_symbols(None))[0]

    def test_bare_position_is_split_out(self):
        row = self._row('凌跃捷 开心 左')
        # 2026-10-05 用户定调（"工具应是通用的"）：内置词表只是**输入别名**，
        # 项目没声明过就显示真实名字 —— 这三条钉的是**拆词**语义
        # （位置不能被当成表情），名字用真实值。
        assert row['expression'] == '开心', '裸词位置不能被当成表情'
        assert row['position'] == '左', '裸词位置必须被识别出来'
        assert row['image'] == '凌跃捷 开心', 'image 不该再含位置（chip 要分开显示）'

    def test_at_form_is_unchanged(self):
        row = self._row('ling happy@left')
        assert row['expression'] == 'happy'
        assert row['position'] == 'left'

    def test_non_position_tail_stays(self):
        """不是位置词的尾巴不能被当成位置削掉。"""
        row = self._row('ling happy mask')
        assert row['position'] == ''


# ==================================================================
#  六、台词字数统计（超长台词是演出现场真会踩的坑）
# ==================================================================

class TestCharCount:
    """
    超长台词在**游戏里**会溢出对话框，而编辑器里换行好看 ——
    这是"作者看不见的坑"，必须给数字与警示。

    tkinter 版有这一项，搬 Qt 时漏了；这里用真 Qt 卡片测。
    """

    def test_short_text(self, qapp, ui_window):
        ui_window._show_index(0)
        ui_window._panel.set_text('短句。')
        ui_window._panel.update_char_count()
        assert ui_window._panel._char_label.text() == '3 字'

    def test_long_text_warns(self, qapp, ui_window):
        ui_window._show_index(0)
        ui_window._panel.set_text('长' * 250)
        ui_window._panel.update_char_count()
        txt = ui_window._panel._char_label.text()
        assert '250 字' in txt
        assert '超过' in txt, '超长要明说，不能只给个数字'

    def test_empty_clears_counter(self, qapp, ui_window):
        ui_window._show_index(0)
        ui_window._panel.set_text('')
        ui_window._panel.update_char_count()
        assert ui_window._panel._char_label.text() == ''

    def test_whitespace_not_counted(self, qapp, ui_window):
        ui_window._show_index(0)
        ui_window._panel.set_text('a b\nc\nd')
        ui_window._panel.update_char_count()
        assert ui_window._panel._char_label.text() == '4 字'


# ==================================================================
#  六·五、立绘缩略图：CG 兜底 / 加载中的可见反馈 / 异步结果不串
# ==================================================================

class TestSpriteThumbPath:
    """
    用户 2026-10-03：

    > 为何卡片界面有立绘时，图片一开始没有显示？为何立绘加载得有点久，
    > 而 cg 很难出现？

    实测 `sr_general`：`cg03.png` **在磁盘上**（`game/images/cg/cg03.png`），
    但 2828 条图片记录里**一条 cg 都没有**。旧代码
    `return self._on_resolve_path(spec) or None` 一拿到 None 就直接放弃，
    永远走不到"按目录约定猜"那一步 —— 所以立绘（在表里）正常、CG 全灭。
    """

    @staticmethod
    def _card(spec, resolver=None, tmp_path=None):
        from storygraph.ui_qt.sprite_grid import SpriteCardWidget
        return SpriteCardWidget({'image': spec, 'expression': '',
                                 'position': '', 'at': '', 'speaking': False,
                                 'order_index': 0, 'extra_rows': []},
                                on_resolve_path=resolver)

    def test_falls_back_to_convention_when_resolver_says_none(self, qapp,
                                                             tmp_path):
        game = tmp_path / 'game' / 'images' / 'cg'
        game.mkdir(parents=True)
        real = game / 'cg03.png'
        real.write_bytes(b'\x89PNG\r\n\x1a\n')
        card = self._card('cg cg03', resolver=lambda spec: None)
        card._loader = None
        # 注入一个假的 loader：只记录请求到的路径
        seen = {}

        class _L:
            project_root = tmp_path

            def request(self, slot, path, apply, max_size=None,
                        placeholder=None):
                seen['path'] = path
        card._loader = _L()
        card._load_thumb('cg cg03')
        assert seen.get('path'), 'CG 没能通过目录约定找到文件'
        assert Path(seen['path']).name == 'cg03.png', seen

    def test_resolver_wins_when_it_finds_something(self, qapp):
        card = self._card('ling oh', resolver=lambda spec: 'D:/x/ling_oh.png')
        assert card._thumb_path('ling oh') == 'D:/x/ling_oh.png'

    def test_loading_state_is_visible_not_blank(self, qapp, tmp_path):
        """解码要时间（立绘 1500×2200）—— 期间必须**看得见"在加载"**。"""
        card = self._card('ling oh', resolver=lambda spec: 'D:/x/ling_oh.png')

        class _L:
            project_root = None

            def request(self, slot, path, apply, max_size=None,
                        placeholder=None):
                pass
        card._loader = _L()
        card._load_thumb('ling oh')
        assert '…' in card._thumb.text(), f'没有加载提示：{card._thumb.text()!r}'
        assert '正在加载' in card._thumb.toolTip()

    def test_placeholder_never_says_the_image_is_broken(self, qapp):
        """
        骨架占位（loader 起解码时那一下）**不许**写成"读不出来"。

        用户 2026-10-05："卡牌界面图片加载慢" —— 真实立绘是 4320×7680 /
        9.4 MB，解一张 350~600 ms。以前骨架走的是 `apply(None)`，而那张卡的
        `apply(None)` 的含义是"**这张图读不出来（可能损坏或格式不支持）**"：
        图还在解，卡片上就已经在报损坏了。
        """
        card = self._card('ling oh', resolver=lambda spec: 'D:/x/ling_oh.png')

        class _L:
            project_root = None
            placeholder = None
            apply = None

            def request(self, slot, path, apply, max_size=None,
                        placeholder=None):
                self.placeholder, self.apply = placeholder, apply
        card._loader = _L()
        card._load_thumb('ling oh')
        assert card._loader.placeholder is not None, '没有给 loader 骨架回调'
        card._loader.placeholder()
        assert '正在加载' in card._thumb.toolTip(), card._thumb.toolTip()
        assert '读不出来' not in card._thumb.toolTip(), \
            '图还在解，卡片上就写着"读不出来"'
        card._loader.apply(None, 'D:/x/ling_oh.png')     # 这次是真的失败
        assert '读不出来' in card._thumb.toolTip(), \
            '真解不出来时反而没说原因'

    def test_missing_image_says_why_instead_of_pretending(self, qapp):
        card = self._card('nobody x', resolver=lambda spec: None)
        card._loader = type('L', (), {'project_root': None,
                                      'request': lambda *a, **k: None})()
        card._load_thumb('nobody x')
        assert '找不到' in card._thumb.toolTip(), card._thumb.toolTip()


class TestPositionChip:
    """
    用户 2026-10-03：

    > 我说的位置不显示，是这个立绘下面的位置没有显示……这是bug，
    > 明明说了在 i11……

    真相：`at` 在 Ren'Py 里是**持续状态** —— 这一拍只写
    `show ye base_handdown care`（没有 `at`），`i11` 是更早那一拍
    `show ye care at i11` 留下的。舞台快照（累积推导）知道它现在在 `i11`，
    而卡片只读"这一拍自己写的"，于是显示成 `—`。
    """

    @staticmethod
    def _card(item):
        from storygraph.ui_qt.sprite_grid import SpriteCardWidget
        return SpriteCardWidget(item, on_resolve_path=lambda s: None)

    def test_inherited_position_is_shown_with_a_marker(self, qapp):
        card = self._card({'image': 'ye base_handdown care', 'expression': '',
                           'position': '', 'at': '', 'inherited_at': 'i11',
                           'effective_at': 'i11'})
        card._loader = None
        assert 'i11' in card._pos_btn.text(), card._pos_btn.text()
        assert '继承' in card._pos_btn.text()
        assert '固定' in card._pos_btn.toolTip()

    def test_own_position_has_no_marker(self, qapp):
        card = self._card({'image': 'ye happy', 'expression': '',
                           'position': 'f11', 'at': 'f11',
                           'inherited_at': '', 'effective_at': 'f11'})
        card._loader = None
        assert card._pos_btn.text() == '位置：f11'

    def test_no_position_anywhere_shows_dash(self, qapp):
        card = self._card({'image': 'ye happy', 'expression': '',
                           'position': '', 'at': '', 'inherited_at': '',
                           'effective_at': ''})
        card._loader = None
        assert card._pos_btn.text() == '位置：—'

    def test_clicking_an_inherited_position_fixes_it_to_this_beat(self, qapp):
        """
        点一下继承来的位置 = **把它固定到这一拍**（值不变），
        而不是硬跳到候选表第一项把位置改掉。
        """
        card = self._card({'image': 'ye base_handdown care', 'expression': '',
                           'position': '', 'at': '', 'inherited_at': 'i11',
                           'effective_at': 'i11'})
        card._loader = None
        card._symbols = None                  # 没有候选表（自定义 transform）
        card._pos_btn.click()
        assert card.item['position'] == 'i11', card.item

    def test_session_exposes_effective_and_inherited_at(self, write_rpy):
        """会话层要把"当前生效"与"继承来的"算出来给前端。"""
        from storygraph.app.editor import EditorSession

        src = ('label ch:\n'
               '    show ye care at i11\n'
               '    "一"\n'
               '    show ye base_handdown care\n'
               '    "二"\n'
               '    return\n')
        s = EditorSession()
        s.load(write_rpy(src, 'ch.rpy'))
        first = s.cards(0)[0]
        assert first['effective_at'] == 'i11' and first['inherited_at'] == ''
        second = s.cards(1)[0]
        assert second['position'] == '' and second['inherited_at'] == 'i11', second


class TestThumbLoaderBehaviour:
    """异步加载器的两条硬要求：**过期结果必须丢**、**用共享内核解码**。"""

    def test_stale_result_is_dropped(self, qapp, monkeypatch):
        """
        同一张卡连续两次请求（换表情）时，先回来的旧结果**不许**顶掉新的。

        旧实现不看 key：旧结果会 `_pending.pop(slot)` 拿到**新请求的回调**
        并把旧图缓存到**新键**上 —— 表现就是"换了表情/点了 CG 之后图不出来"。
        """
        from PySide6.QtGui import QImage
        from PySide6.QtWidgets import QWidget

        from storygraph.ui_qt import thumb_loader as TL

        jobs = []
        monkeypatch.setattr(TL.QThreadPool, 'globalInstance',
                            staticmethod(lambda: type('P', (), {
                                'start': staticmethod(
                                    lambda job: jobs.append(job))})()))
        loader = TL.ThumbLoader(QWidget(), max_size=(16, 16))
        applied = []
        loader.request('slot', 'a.png', lambda px, p: applied.append(('a', px)))
        old_key, old_job = jobs[-1].key, jobs[-1]
        # 同一张卡又改了（换表情）：新的想头排在旧的后面
        loader.request('slot', 'b.png', lambda px, p: applied.append(('b', px)))
        assert len(jobs) == 1, '旧任务还在跑，不该同时排两个'
        img = QImage(4, 4, QImage.Format.Format_RGBA8888)
        loader.image_ready.emit('slot', old_key, img, 'a.png')
        assert [px for _, px in applied if px is not None] == [], \
            f'过期结果被应用了：{applied}'
        assert loader._cache, '过期结果本身是一张真图，应该进缓存而不是丢掉'
        # 旧任务跑完 → 接着解当前想要的那张
        assert len(jobs) == 2, '旧任务结束后没有接着解最新的那张'
        loader.image_ready.emit('slot', jobs[-1].key, img, 'b.png')
        assert applied and applied[-1][0] == 'b' and applied[-1][1] is not None
        assert old_job.key != jobs[-1].key

    def test_uses_the_shared_decode_kernel(self, qapp, monkeypatch):
        """
        解码必须走 `app.thumbs.decode_sync`（解码器级降采样 + 磁盘缓存）。

        旧实现自己 `QImageReader.read()` 整图再缩：立绘 1500×2200、CG 更大，
        冷缓存下慢得肉眼可见（用户："立绘加载得有点久"）。
        """
        from PySide6.QtGui import QImage

        from storygraph.ui_qt import thumb_loader as TL

        calls = []

        class _FakePil:
            mode = 'RGBA'
            width, height = 8, 6

            def tobytes(self, *a):
                return bytes(8 * 6 * 4)

        def fake_decode(path, max_size, project_root=None):
            calls.append((path, tuple(max_size)))
            return _FakePil()

        monkeypatch.setattr(TL, 'decode_sync', fake_decode)
        img = TL._decode('some/spec', (16, 16), None)
        assert calls, '没有走 app.thumbs.decode_sync'
        assert isinstance(img, QImage) and img.width() == 8

    def test_duplicate_in_flight_request_is_not_queued_twice(self, qapp,
                                                             monkeypatch):
        """同一个 slot 反复请求（翻页顺带刷新）不该排出重复的解码任务。"""
        from PySide6.QtWidgets import QWidget

        from storygraph.ui_qt import thumb_loader as TL

        started = []
        monkeypatch.setattr(TL.QThreadPool, 'globalInstance',
                            staticmethod(lambda: type('P', (), {
                                'start': staticmethod(
                                    lambda job: started.append(job))})()))
        loader = TL.ThumbLoader(QWidget(), max_size=(16, 16))
        loader.request('slot', 'a.png', lambda *a: None)
        loader.request('slot', 'a.png', lambda *a: None)   # 同一个 key 再要一次
        loader._pump()
        assert len(started) == 1, f'同一个 key 排了重复的解码任务：{len(started)}'

    def test_memory_cache_key_uses_resolved_path(self, qapp, tmp_path,
                                                monkeypatch):
        """内存缓存的键要用**解析后的真实文件**（spec 上 stat 不到 mtime）。"""
        from PySide6.QtWidgets import QWidget

        from storygraph.ui_qt import thumb_loader as TL

        real = tmp_path / 'real.png'
        real.write_bytes(b'x')
        monkeypatch.setattr(TL, 'resolve_thumb_source',
                            lambda path, root: str(real))
        loader = TL.ThumbLoader(QWidget(), max_size=(16, 16))
        seen = {}
        monkeypatch.setattr(TL, 'cache_key',
                            lambda path, size: seen.setdefault('path', path))
        loader.request('slot', 'cg cg03', lambda *a: None)
        assert seen.get('path') == str(real), seen


class TestThumbLoadingIsFast:
    """
    用户 2026-10-05：

    > 现在先优化之前提到的……卡牌界面图片加载慢的问题
    > ……这个选择器内图片的加载还是不够迅速，优先加载目前在窗口内的图片！

    真实项目的立绘是 **4320×7680 / 9.4 MB** 的 PNG：冷解码 350~600 ms
    （磁盘缓存命中后 ~1 ms）。所以这里钉几件事：

      · 请求**立刻开工**（不再有"每个 slot 先白等 120ms 防抖"）；
      · 骨架占位走独立回调，**不许**把"图坏了"提前写到卡片上；
      · 预取（相邻拍的图）能真的填进缓存，之后请求 0 延迟命中；
      · **优先级**：窗口内可见的排最前，预取永远排最后（不许挡路）。
    """

    @staticmethod
    def _fake_pool(monkeypatch, TL, started):
        """把全局线程池换成"只记下发了哪些任务"的假货。"""
        monkeypatch.setattr(TL.QThreadPool, 'globalInstance',
                            staticmethod(lambda: type('P', (), {
                                'start': staticmethod(
                                    lambda job: started.append(job))})()))

    def test_request_starts_immediately(self, qapp, monkeypatch):
        """请求立刻开工（以前每个 slot 要先白等一个防抖周期）。"""
        from PySide6.QtWidgets import QWidget

        from storygraph.ui_qt import thumb_loader as TL

        started = []
        self._fake_pool(monkeypatch, TL, started)
        loader = TL.ThumbLoader(QWidget(), max_size=(16, 16))
        loader.request('slot', 'a.png', lambda *a: None)
        assert len(started) == 1, '请求没有立刻开工'

    def test_repeat_request_while_decoding_is_merged(self, qapp, monkeypatch):
        """同一个 slot 在解的过程中又被改 → 只更新想头，不重复排队（chip 连点）。"""
        from PySide6.QtWidgets import QWidget

        from storygraph.ui_qt import thumb_loader as TL

        started = []
        self._fake_pool(monkeypatch, TL, started)
        loader = TL.ThumbLoader(QWidget(), max_size=(16, 16))
        loader.request('slot', 'a.png', lambda *a: None)
        loader.request('slot', 'b.png', lambda *a: None)   # 立刻又改一次
        assert len(started) == 1, f'第二次请求直接开工了：{len(started)} 个任务'

    def test_in_flight_count_is_bounded(self, qapp, monkeypatch):
        """
        同时在解的张数有上限 —— 池子里**只有当下最想要的几张**。

        不限流的话，`QThreadPool` 先进先出：滚过去的几百个任务会挡在
        "现在看得见的那几张"前面（用户："优先加载目前在窗口内的图片"）。
        """
        from PySide6.QtWidgets import QWidget

        from storygraph.ui_qt import thumb_loader as TL

        started = []
        self._fake_pool(monkeypatch, TL, started)
        loader = TL.ThumbLoader(QWidget(), max_size=(16, 16), max_in_flight=3)
        for i in range(20):
            loader.request(f'slot{i}', f'img{i}.png', lambda *a: None)
        assert len(started) == 3, f'没有限流，一次排了 {len(started)} 个任务'
        assert len(loader.queued()) == 17, '其余应该在排队'

    def test_focus_puts_visible_ones_first(self, qapp, monkeypatch):
        """
        `focus()` 把**窗口内可见的**提到最前，滚过去的降回普通。

        这就是"优先加载目前在窗口内的图片"的全部机制：排队顺序由我们定，
        不由 `QThreadPool` 的先进先出定。
        """
        from PySide6.QtGui import QImage
        from PySide6.QtWidgets import QWidget

        from storygraph.ui_qt import thumb_loader as TL

        started = []
        self._fake_pool(monkeypatch, TL, started)
        loader = TL.ThumbLoader(QWidget(), max_size=(16, 16), max_in_flight=1)
        for i in range(6):
            loader.request(f'slot{i}', f'img{i}.png', lambda *a: None)
        # 先要的 0..4 在排队，1 号正在解；现在用户滚到了 4/5 那一屏
        loader.focus(['slot4', 'slot5'])
        order = loader.queued()
        assert order[:2] == ['slot4', 'slot5'], \
            f'窗口内的没有排到最前：{order}'
        # 正在解的那张跑完 → 下一个必须是可见项，不是"先要的 0 号"
        job = started[0]
        img = QImage(4, 4, QImage.Format.Format_RGBA8888)
        loader.image_ready.emit(job.slot, job.key, img, 'x.png')
        assert started[-1].slot in ('slot4', 'slot5'), \
            f'下一个解的不是窗口内的：{started[-1].slot}'

    def test_prefetch_never_jumps_the_queue(self, qapp, monkeypatch):
        """预取永远排最后 —— 它不许挡着真正要显示的那张。"""
        from PySide6.QtWidgets import QWidget

        from storygraph.ui_qt import thumb_loader as TL

        started = []
        self._fake_pool(monkeypatch, TL, started)
        loader = TL.ThumbLoader(QWidget(), max_size=(16, 16), max_in_flight=1)
        loader.prefetch('future.png')                 # 先来的预取（正在解）
        loader.request('card', 'now.png', lambda *a: None)    # 后到的正式请求
        loader.request('card2', 'now2.png', lambda *a: None)
        assert len(started) == 1, '限流没生效'
        assert loader.queued() == ['card', 'card2'], \
            f'正式请求没有排在预取前面：{loader.queued()}'

        # 预取（正在解的那个）跑完 → 接着解正式请求，预取不插队
        job = started[0]
        from PySide6.QtGui import QImage
        loader.image_ready.emit(job.slot, job.key,
                                QImage(4, 4, QImage.Format.Format_RGBA8888),
                                'future.png')
        assert started[-1].slot == 'card', f'下一个解的是：{started[-1].slot}'

    def test_placeholder_is_called_instead_of_apply_none(self, qapp,
                                                        monkeypatch):
        """
        给了 `placeholder` 就不许再用 `apply(None)` 占位。

        立绘卡的 `apply(None)` 含义是"**这张图读不出来**"：拿它当骨架，
        图还在解的时候卡片上就已经写着"可能损坏"了。
        """
        from PySide6.QtWidgets import QWidget

        from storygraph.ui_qt import thumb_loader as TL

        self._fake_pool(monkeypatch, TL, [])
        loader = TL.ThumbLoader(QWidget(), max_size=(16, 16))
        skeleton, applied = [], []
        loader.request('slot', 'a.png', lambda px, p: applied.append(px),
                       placeholder=lambda: skeleton.append(1))
        assert skeleton == [1], '骨架回调没被调用'
        assert applied == [], f'占位时调用了 apply(None)：{applied}'

    def test_apply_is_not_called_for_pure_prefetch(self, qapp):
        """预取是"只要缓存"：连 apply=None 也不许炸。"""
        from PySide6.QtWidgets import QWidget

        from storygraph.ui_qt.thumb_loader import ThumbLoader

        loader = ThumbLoader(QWidget(), max_size=(16, 16))
        loader._shutting_down = True           # 走"直接放弃"那条路，够验签名
        loader.prefetch('a.png')
        assert loader._want == {}

    def test_prefetch_fills_the_cache_so_the_next_request_is_instant(self,
                                                                    qapp,
                                                                    tmp_path,
                                                                    monkeypatch):
        """
        预取之后，真正的请求必须**同步**拿到图（不再排解码任务）。

        这就是"翻到下一拍不空等"的全部机制：预取先把图解进内存缓存。
        """
        from PySide6.QtGui import QImage
        from PySide6.QtWidgets import QWidget

        from storygraph.ui_qt import thumb_loader as TL

        loader = TL.ThumbLoader(QWidget(), max_size=(16, 16))
        jobs = []
        self._fake_pool(monkeypatch, TL, jobs)
        loader.prefetch('lan happy')
        assert len(jobs) == 1, '预取没排任务'
        slot = jobs[0].slot
        img = QImage(4, 4, QImage.Format.Format_RGBA8888)
        loader.image_ready.emit(slot, jobs[0].key, img, 'lan happy')
        assert loader._cache, '预取结果没有进缓存'

        applied = []
        loader.request('card-1', 'lan happy', lambda px, p: applied.append(px))
        assert applied and applied[-1] is not None, '预取之后还要现解一遍'
        assert len(jobs) == 1, '预取命中了却还是排了新任务'

    def test_prefetch_skips_what_is_already_cached_or_in_flight(self, qapp,
                                                               monkeypatch):
        from PySide6.QtWidgets import QWidget

        from storygraph.ui_qt import thumb_loader as TL

        loader = TL.ThumbLoader(QWidget(), max_size=(16, 16))
        jobs = []
        self._fake_pool(monkeypatch, TL, jobs)
        loader.prefetch('lan happy')
        loader.prefetch('lan happy')           # 正在解 → 不重复排
        assert len(jobs) == 1, f'预取重复排了任务：{len(jobs)}'

    def test_clear_pending_drops_queued_but_keeps_running(self, qapp,
                                                          monkeypatch):
        """换候选列表 / 换帧时：丢掉还没开始的，正在解的让它跑完（结果进缓存）。"""
        from PySide6.QtWidgets import QWidget

        from storygraph.ui_qt import thumb_loader as TL

        started = []
        self._fake_pool(monkeypatch, TL, started)
        loader = TL.ThumbLoader(QWidget(), max_size=(16, 16), max_in_flight=1)
        loader.request('running', 'a.png', lambda *a: None)
        loader.request('queued', 'b.png', lambda *a: None)
        loader.clear_pending()
        assert 'running' in loader._running, '正在解的被丢掉了（白白浪费）'
        assert loader.queued() == [], f'排队里还有：{loader.queued()}'

    def test_clear_pending_can_keep_what_the_caller_still_wants(self, qapp,
                                                               monkeypatch):
        """
        `keep(slot)`：只丢**调用方说是旧的**那些。

        演出预览换一帧时要丢掉别的帧的想头，但**预取**（下一拍的图）必须留着 ——
        否则每翻一页都把刚预热的丢掉，预取等于白做（用户 2026-10-05 说的
        "蓝色框一闪而过"就又回来了）。
        """
        from PySide6.QtWidgets import QWidget

        from storygraph.ui_qt import thumb_loader as TL

        self._fake_pool(monkeypatch, TL, [])
        loader = TL.ThumbLoader(QWidget(), max_size=(16, 16), max_in_flight=1)
        loader.request('running', 'a.png', lambda *a: None, priority=100)
        loader.request(('bg', 7), 'b.png', lambda *a: None)      # 别的帧
        loader.request(('bg', 8), 'c.png', lambda *a: None)      # 这一帧
        loader.prefetch('d.png')                                 # 预取
        loader.clear_pending(keep=lambda s: s != ('bg', 7))
        left = loader.queued()
        assert ('bg', 7) not in left, f'别的帧的想头没丢掉：{left}'
        assert ('bg', 8) in left, f'这一帧的想头被误丢了：{left}'
        assert any(isinstance(s, tuple) and s and s[0] == 'prefetch'
                   for s in left), f'预取被丢了（白预热）：{left}'

    def test_pump_fills_from_cache_when_another_slot_already_decoded_it(
            self, qapp, monkeypatch):
        """
        **同一张图**在排队期间被别的 slot 解好了 → 直接回填，不再解第二遍。

        演出预览里最明显：预取和当前帧要的是同一张图，不查缓存就每张都解两遍
        （白等一个解码时间，正好是"占位框多闪一会儿"）。
        """
        from PySide6.QtGui import QImage
        from PySide6.QtWidgets import QWidget

        from storygraph.ui_qt import thumb_loader as TL

        loader = TL.ThumbLoader(QWidget(), max_size=(16, 16), max_in_flight=1)
        jobs = []
        self._fake_pool(monkeypatch, TL, jobs)
        loader.prefetch('lan happy')            # 占住唯一的线程
        assert len(jobs) == 1
        applied = []
        loader.request('card-1', 'lan happy', lambda px, p: applied.append(px))
        # 预取的结果回来了（按它自己的 key 进缓存）
        img = QImage(4, 4, QImage.Format.Format_RGBA8888)
        loader.image_ready.emit(jobs[0].slot, jobs[0].key, img, 'lan happy')
        assert len(jobs) == 1, '缓存里已经有了还排新任务（同一张图解了两遍）'
        assert applied and applied[-1] is not None, '缓存回填没走到回调'


# ==================================================================
#  七、立绘解析不能跨角色串图
# ==================================================================

class TestLookupImageId:
    """
    2026-09-30 用户实测的事故：
    > 这其实是角色 **lan** 的立绘，不是 ling oh！

    真实资源表里 `lan` 和 `ling` **都有 `oh` 这个表情**，而 `ID='oh'`
    那一条属于 `lan`。旧实现遇到 `ling oh` 会退化成"拿最后一个词去查"，
    于是显示成 lan 的图 —— **张冠李戴**。
    """

    IMAGES = {
        'oh': {'Tag': 'lan', 'Attributes': 'oh'},
        '_oh': {'Tag': 'ling', 'Attributes': 'oh'},
        'disappointed': {'Tag': 'ling', 'Attributes': 'disappointed'},
        'ling': {'Tag': 'avatar', 'Attributes': 'ling'},
    }

    def test_same_attr_name_does_not_cross_characters(self):
        assert lookup_image_id(self.IMAGES, 'ling', ['oh']) == '_oh'
        assert lookup_image_id(self.IMAGES, 'lan', ['oh']) == 'oh'

    def test_unknown_tag_returns_nothing(self):
        """tag 对不上时**宁可没图**，也不许命中别人的属性同名图。"""
        assert lookup_image_id(self.IMAGES, 'nobody', ['oh']) == ''

    def test_name_form_still_works(self):
        assert lookup_image_id(self.IMAGES, 'ling', ['disappointed']) \
            == 'disappointed'

    def test_empty_inputs_are_safe(self):
        assert lookup_image_id({}, 'ling', ['oh']) == ''
        assert lookup_image_id(self.IMAGES, '', ['oh']) == ''


# ==================================================================
#  八、翻页是**纯导航**（不改任何一拍的数据）
# ==================================================================

class TestPagingIsPureNavigation:
    """
    用户 2026-09-30 拍板：

    > 回车不要自动填说话人！！！回车是下一张卡片的意思！！！

    在此之前 `next_beat` 会跑一段"参与角色轮转"，把说话人**静默写进下一拍**
    （真实 chapter1_3：第 45 拍本来就写着 `lyj`，界面显示成 `dhy`）。
    现在翻页只提交**当前这一拍**的编辑，别的拍一个字符都不许动。
    """

    def test_rotation_helper_is_gone(self, qapp, ui_window):
        """轮转自动填充必须**整个删除**，不是"加个守卫"——
        只要它还在，就有第二次被接到导航路径上的机会。"""
        assert not hasattr(ui_window, '_advance_speaker')
        assert not hasattr(ui_window, '_active_rotation')

    def test_next_beat_touches_nothing_but_the_index(self, qapp, ui_window):
        ui_window._show_index(0)
        before = [(b.speaker, b.text, b.kind) for b in ui_window.beats]
        ui_window.next_beat()
        after = [(b.speaker, b.text, b.kind) for b in ui_window.beats]
        assert before == after, '翻页不许改任何一拍的数据'
        assert ui_window._loaded_idx == 1

    def test_prev_beat_touches_nothing(self, qapp, ui_window):
        ui_window._show_index(2)
        before = [(b.speaker, b.text) for b in ui_window.beats]
        ui_window.prev_beat()
        assert before == [(b.speaker, b.text) for b in ui_window.beats]
        assert ui_window._loaded_idx == 1

    def test_first_and_last_are_stops(self, qapp, ui_window):
        """两端到头就不动（**而且 `_loaded_idx` 不许变成 -1**）。"""
        ui_window._show_index(0)
        ui_window.prev_beat()
        assert ui_window._loaded_idx == 0
        last = len(ui_window.beats) - 1
        ui_window._show_index(last)
        ui_window.next_beat()
        assert ui_window._loaded_idx == last, '最后一拍之后不该越界'

    def test_fill_speaker_from_participant_only_touches_current_beat(
            self, qapp, ui_window):
        """
        便利性用**显式动作**保留：双击参与角色 → 填进**眼前这一拍**。
        别的拍照样不许动。
        """
        ui_window._show_index(0)
        target = ui_window._who.item(0)
        if target is None:
            pytest.skip('这个夹具项目没有参与角色')
        before = [b.speaker for b in ui_window.beats]
        ui_window._fill_speaker(target)
        assert ui_window.beats[0].speaker == target.text()
        assert [b.speaker for b in ui_window.beats[1:]] == before[1:]
