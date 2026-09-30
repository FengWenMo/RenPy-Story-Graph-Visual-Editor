"""
tests/test_ui_cards.py
======================
立绘卡改版（2026-09-29）的回归：**按角色聚合**、缩略图缓存键、设计 token。

这里全部是**纯逻辑**，不需要实例化 tkinter —— 无头环境（CI）也跑得动。
界面本身的观感与手感由人工验收（见 docs/卡牌编辑器界面设计规格_v3.md §9）。
"""

from storygraph.story.beat_model import Beat
from storygraph.story.beat_render import make_symbols
from storygraph.ui import tokens as T
from storygraph.ui.beat_editor import (beat_sprite_rows, group_sprites_by_tag,
                                       lookup_image_id, stage_sprite_rows,
                                       strip_stage_show_lines)
from storygraph.ui.thumb_loader import cache_key


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
#  四、缩略图加载器：没有事件循环就别加载
# ==================================================================

class TestThumbLoaderGuard:
    """
    这条守卫修过一次真实的"测试卡死"：

    测试环境的 tkinter 是桩，widget 没有 `after`（没有事件循环）。
    第一版代码在 `after` 失败时会**退化成直接在线程池里解码**——
    于是每个实例化面板的用例都建线程 + 解码真实大图（真实项目 2902 张），
    全量跑跑到 41% 就被拖到超时（SIGTERM）。
    正确行为：**没有事件循环就完全不加载**，占位文字代替。
    """

    class _NoEvents:
        """像测试桩：什么都没有。"""

    class _WithEvents:
        def __init__(self):
            self.calls = []

        def after(self, delay, fn):
            self.calls.append(delay)
            return len(self.calls)

        def after_cancel(self, _id):
            pass

    def test_no_event_loop_does_not_load(self):
        from storygraph.ui.thumb_loader import ThumbLoader

        loader = ThumbLoader(self._NoEvents(), max_size=(10, 10))
        got = []
        loader.request('slot', 'whatever.png',
                       lambda photo, path: got.append(photo))
        assert got == [], '没有事件循环时不该加载（更不能偷偷建线程）'
        assert loader.cache_size == 0
        loader.shutdown()

    def test_empty_path_short_circuits(self):
        from storygraph.ui.thumb_loader import ThumbLoader

        loader = ThumbLoader(self._WithEvents(), max_size=(10, 10))
        got = []
        loader.request('slot', '', lambda photo, path: got.append(photo))
        assert got == [None], '空路径应立刻回调（显示占位），不排队等待'
        loader.shutdown()

    def test_request_is_debounced(self):
        from storygraph.ui.thumb_loader import ThumbLoader

        widget = self._WithEvents()
        loader = ThumbLoader(widget, max_size=(10, 10), debounce_ms=77)
        loader.request('slot', 'x.png', lambda photo, path: None)
        assert widget.calls == [77], '请求应当被防抖延迟合并，而不是立刻解码'
        loader.shutdown()

    def test_forget_cancels_pending(self):
        from storygraph.ui.thumb_loader import ThumbLoader

        loader = ThumbLoader(self._WithEvents(), max_size=(10, 10))
        loader.request('slot', 'x.png', lambda photo, path: None)
        loader.forget('slot')
        assert 'slot' not in loader._pending
        loader.shutdown()

    def test_accepts_path_object(self):
        """
        **回归**：路径可能是 `pathlib.Path`，不是字符串。

        2026-09-30 真实事故：`asset_preview_path()` 返回 `Path`，
        而加载器写了 `path.strip()` → `AttributeError` →
        卡片构造崩 → `load_beat` 崩 → **用户看到"立绘全没了"**。
        假数据（返回空字符串）正好绕过这条路径，所以必须用 Path 测。
        """
        from pathlib import Path

        from storygraph.ui.thumb_loader import ThumbLoader

        widget = self._WithEvents()
        loader = ThumbLoader(widget, max_size=(10, 10))
        # 关键：**不抛异常**（第一版这里直接 AttributeError，把面板刷新带崩）。
        # 假 widget 只记录排程、不执行回调（那正是防抖，符合预期）。
        loader.request('slot', Path('a') / 'b.png', lambda photo, path: None)
        assert widget.calls, 'Path 入参应被正常排程，而不是崩掉'
        loader.shutdown()


# ==================================================================
#  五、Stage 列里的立绘（老写法）：用户报"立绘没显示"的那条路
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
#  六、裸词位置：`凌跃捷 开心 左` 里的"左"是位置，不是表情
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
        assert row['expression'] == '开心', '裸词位置不能被当成表情'
        assert row['position'] == '左', '裸词位置必须被识别出来'
        assert row['image'] == '凌跃捷 开心', 'image 不该再含位置（chip 要分开显示）'

    def test_at_form_is_unchanged(self):
        row = self._row('ling happy@left')
        assert row['expression'] == '开心'
        assert row['position'] == '左'

    def test_non_position_tail_stays(self):
        """不是位置词的尾巴不能被当成位置削掉。"""
        row = self._row('ling happy mask')
        assert row['position'] == ''


# ==================================================================
#  七、台词字数统计（超长台词是演出现场真会踩的坑）
# ==================================================================

class _SpyVar:
    def __init__(self, value=''):
        self.value = value

    def set(self, value):
        self.value = value

    def get(self):
        return self.value


class TestCharCount:
    def _panel(self, text):
        from storygraph.ui.beat_editor import BeatEditorPanel

        panel = BeatEditorPanel.__new__(BeatEditorPanel)
        panel._vars = {'Text': _SpyVar(text)}
        panel._multi = set()          # 让 _wget 走 StringVar 分支
        panel._widgets = {}
        panel._char_count_var = _SpyVar()
        panel._char_count_label = None
        return panel

    def test_short_text(self):
        panel = self._panel('短句。')
        panel._update_char_count()
        assert panel._char_count_var.value == '3 字'

    def test_long_text_warns(self):
        panel = self._panel('长' * 250)
        panel._update_char_count()
        assert '250 字' in panel._char_count_var.value
        assert '超过' in panel._char_count_var.value, '超长要明说，不能只给个数字'

    def test_empty_clears_counter(self):
        panel = self._panel('')
        panel._update_char_count()
        assert panel._char_count_var.value == ''

    def test_whitespace_not_counted(self):
        panel = self._panel('a b\nc\nd')
        panel._update_char_count()
        assert panel._char_count_var.value == '4 字'


# ==================================================================
#  八、立绘解析不能跨角色串图
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
#  九、翻页是**纯导航**（不改任何一拍的数据）
# ==================================================================

class TestPagingIsPureNavigation:
    """
    用户 2026-09-30 拍板：

    > 回车不要自动填说话人！！！回车是下一张卡片的意思！！！

    在此之前 `next_beat` 会跑一段"参与角色轮转"，把说话人**静默写进下一拍**
    （真实 chapter1_3：第 45 拍本来就写着 `lyj`，界面显示成 `dhy`）。
    现在翻页只提交**当前这一拍**的编辑，别的拍一个字符都不许动。
    """

    def _win(self, speakers):
        from storygraph.ui.beat_editor import ScriptTableEditorWindow

        win = ScriptTableEditorWindow.__new__(ScriptTableEditorWindow)
        win.beats = [Beat(seq=i + 1, label='a', text='x', speaker=sp)
                     for i, sp in enumerate(speakers)]
        win._loaded_idx = 0
        # 只测"导航"这件事本身：把"提交当前拍"与"渲染"换成记账
        win.apply_current_beat = lambda: None
        win._show_index = lambda i: setattr(win, '_loaded_idx', i)
        return win

    def test_rotation_helper_is_gone(self):
        """轮转自动填充必须**整个删除**，不是"加个守卫"——
        只要它还在，就有第二次被接到导航路径上的机会。"""
        from storygraph.ui.beat_editor import ScriptTableEditorWindow
        assert not hasattr(ScriptTableEditorWindow, '_advance_speaker')

    def test_next_beat_touches_nothing_but_the_index(self):
        win = self._win(['dhy', '', 'lyj'])
        before = [(b.speaker, b.text, b.kind) for b in win.beats]
        win.next_beat()
        after = [(b.speaker, b.text, b.kind) for b in win.beats]
        assert before == after, '翻页不许改任何一拍的数据'
        assert win._loaded_idx == 1

    def test_prev_beat_touches_nothing(self):
        win = self._win(['dhy', '', 'lyj'])
        win._loaded_idx = 2
        before = [(b.speaker, b.text) for b in win.beats]
        win.prev_beat()
        assert before == [(b.speaker, b.text) for b in win.beats]
        assert win._loaded_idx == 1

    def test_first_and_last_are_stops(self):
        win = self._win(['dhy'])
        win.prev_beat()
        assert win._loaded_idx == 0
        win.next_beat()
        assert win._loaded_idx == 0, '已经是最后一拍就不动'

    def test_fill_speaker_from_participant_only_touches_current_beat(self):
        """
        便利性用**显式动作**保留：双击参与角色 → 填进**眼前这一拍**。
        别的拍照样不许动。
        """
        from storygraph.ui.beat_editor import ScriptTableEditorWindow

        win = self._win(['', '', ''])
        win._highlight_speaker = lambda *a, **k: None
        win._set_note = lambda *a, **k: None
        win._panel = None
        win.fill_speaker_from_participant('lyj')
        assert win.beats[0].speaker == 'lyj'
        assert win.beats[1].speaker == ''
        assert win.beats[2].speaker == ''
