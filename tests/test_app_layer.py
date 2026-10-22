"""
tests/test_app_layer.py
=======================
**清理 tkinter 时新搬进 `storygraph.app` 的那几块**的回归。

2026-10-01 删除 `storygraph/ui/`（tkinter）时，有三块代码必须活下来，
不能跟着一起消失 —— 它们本来就不属于"界面"，只是当年放错了地方：

  · `app.thumbs`       缩略图内核（缓存键 + 后台解码）
  · `app.asset_panel`  资产面板数据源（树 / 详情 / 搜索 / 统计 / 角色视角）
  · `app.tables`       剧本表（Excel）双向工作流 ← **功能最多的一块**

这个文件就是那三块的回归网。特别是 `app.tables`：它承接了 tkinter
`GraphView` 里 141 行的 `export_rpy_from_table`，那条路上有两个真实事故
（产物写回 game/ 抢 label、全量重生成把注释冲掉），必须钉住。
"""

import os
from pathlib import Path

import pytest


# ==================================================================
#  一、app.thumbs：缓存键 + 解码
# ==================================================================

class TestThumbs:
    def test_cache_key_shape(self):
        from storygraph.app.thumbs import cache_key
        k = cache_key('a.png', (88, 118), (11, 22))
        assert 'a.png' in k and '11' in k and '22' in k and '88x118' in k

    def test_resolve_thumb_source_is_re_exported(self):
        """`resolve_thumb_source` 只转发 story 层实现，不在 app 里重写。"""
        import storygraph.app.thumbs as thumbs
        from storygraph.story.excel_export import resolve_thumb_source
        assert thumbs.resolve_thumb_source is resolve_thumb_source

    def test_decode_sync_returns_pil_image(self, tmp_path):
        pil = pytest.importorskip('PIL.Image')
        from storygraph.app.thumbs import decode_sync
        img = pil.new('RGB', (40, 30), (10, 20, 30))
        f = tmp_path / 'x.png'
        img.save(f)
        out = decode_sync(f, (20, 20))
        assert out is not None and out.size[0] <= 20 and out.size[1] <= 20

    def test_decode_sync_missing_file(self, tmp_path):
        from storygraph.app.thumbs import decode_sync
        assert decode_sync(tmp_path / 'nope.png', (20, 20)) is None

    def test_decode_sync_garbage(self, tmp_path):
        from storygraph.app.thumbs import decode_sync
        f = tmp_path / 'bad.png'
        f.write_bytes(b'not an image')
        assert decode_sync(f, (20, 20)) is None

    # ---- 冷解码的"先粗后精"（用户 2026-10-05："卡牌界面图片加载慢"）----

    def test_downscale_is_rgba_and_keeps_the_aspect_ratio(self, tmp_path):
        """
        大图降采样后必须是 RGBA 且**比例不变、不超目标**。

        真实项目的立绘是 4320×7680 / 9.4 MB 的 PNG：这里用一张大图钉住
        `_downscale` 的契约（reduce 粗缩 + thumbnail 收边那两步不能改语义）。
        """
        pil = pytest.importorskip('PIL.Image')
        from storygraph.app.thumbs import _downscale

        src = pil.new('RGB', (1200, 800), (200, 30, 40))
        out = _downscale(src, (120, 90))
        assert out.mode == 'RGBA', out.mode
        assert out.size[0] <= 120 and out.size[1] <= 90, out.size
        assert abs(out.size[0] / out.size[1] - 1200 / 800) < 0.05, out.size

    def test_downscale_leaves_small_images_alone(self):
        """比目标还小的图不许被放大（放大只会糊）。"""
        pil = pytest.importorskip('PIL.Image')
        from storygraph.app.thumbs import _downscale

        src = pil.new('RGBA', (30, 20), (1, 2, 3, 255))
        out = _downscale(src, (200, 200))
        assert out.size == (30, 20), out.size

    def test_downscale_without_a_size_just_converts(self):
        pil = pytest.importorskip('PIL.Image')
        from storygraph.app.thumbs import _downscale

        out = _downscale(pil.new('P', (10, 10)), None)
        assert out.mode == 'RGBA' and out.size == (10, 10)

    def test_downscale_does_not_mutate_a_redraw_of_the_same_size(self):
        """已经是 RGBA 且尺寸合适时不许改动图像（也不许报错）。"""
        pil = pytest.importorskip('PIL.Image')
        from storygraph.app.thumbs import _downscale

        src = pil.new('RGBA', (16, 16), (9, 9, 9, 255))
        out = _downscale(src, (64, 64))
        assert out.size == src.size and out.mode == 'RGBA'

    def test_decode_sync_still_finds_a_big_png_and_caches_it(self, tmp_path,
                                                            monkeypatch):
        """
        大图的冷解码结果要落**磁盘缓存**，第二次直接命中（否则每次翻拍都重解）。
        """
        pil = pytest.importorskip('PIL.Image')
        from storygraph.app import thumbs

        cache = tmp_path / 'cache'
        cache.mkdir()
        monkeypatch.setattr(thumbs, 'thumb_cache_dir', lambda: cache)
        f = tmp_path / 'big.png'
        pil.new('RGB', (600, 900), (8, 8, 8)).save(f)

        first = thumbs.decode_sync(f, (60, 90), tmp_path)
        assert first is not None and first.size[0] <= 60
        # ⚠ 缓存是**两级分片**的（`<ab>/<cd>/<digest>.png`，见
        # `excel_export.thumb_cache_file`）：单目录堆到 14 万条之后
        # 每次查找都在巨大索引里翻。所以这里必须 rglob。
        assert list(cache.rglob('*.png')), '没有写磁盘缓存'

        calls = {'n': 0}
        real_open = pil.open

        def counting_open(*a, **k):
            calls['n'] += 1
            return real_open(*a, **k)

        monkeypatch.setattr(pil, 'open', counting_open)
        second = thumbs.decode_sync(f, (60, 90), tmp_path)
        assert second is not None and second.size == first.size
        assert calls['n'] == 1, '第二次没有走磁盘缓存'


# ==================================================================
#  二、app.asset_panel：数据源（纯查询，无 UI）
# ==================================================================

class _Engine:
    def __init__(self, reg=None, root=None):
        self._reg = reg
        self.project_root = root

    def get_registry(self):
        return self._reg


def _registry():
    from storygraph.assets.asset_registry import AssetRegistry
    reg = AssetRegistry()
    reg.images = {
        'bg_classroom': {'ID': 'bg_classroom', 'Path': 'images/bg/classroom.png',
                         'Tag': 'bg', 'Category': 'background'},
        'ling_happy': {'ID': 'ling_happy', 'Path': 'images/ling/happy.png',
                       'Tag': 'ling', 'Category': 'character',
                       'Attributes': 'happy'},
    }
    reg.transforms = {'i11': {'ID': 'i11', 'ATL': 'xpos 0.1'}}
    reg.characters = {'e': {'ID': 'e', 'Name': 'Eileen',
                            '_dialogue_count': 7}}
    reg.references = {'bg_classroom': [('a.rpy', 3)]}
    return reg


class TestAssetPanelData:
    def test_tree_groups_by_category_and_tag(self):
        from storygraph.app.asset_panel import AssetPanelData
        tree = AssetPanelData(_Engine(_registry())).get_tree('category')
        assert tree['background']['bg'] == ['bg_classroom']
        assert tree['character']['ling'] == ['ling_happy']

    def test_tree_group_by_folder_uses_posix_separators(self):
        """
        Windows 上 `PurePosixPath` 才不会生成反斜杠分组 key ——
        反斜杠 key 会让下拉里的分组名看起来像乱码。
        """
        from storygraph.app.asset_panel import AssetPanelData
        tree = AssetPanelData(_Engine(_registry())).get_tree('folder')
        for group in tree:
            assert '\\' not in group, f'分组 key 里有反斜杠：{group!r}'

    def test_tree_without_registry_is_empty(self):
        from storygraph.app.asset_panel import AssetPanelData
        assert AssetPanelData(_Engine(None)).get_tree() == {}

    def test_detail_includes_references(self):
        from storygraph.app.asset_panel import AssetPanelData
        d = AssetPanelData(_Engine(_registry())).get_detail('bg_classroom')
        assert d['category'] == 'images'
        assert d['references'] == [('a.rpy', 3)]

    def test_detail_unknown_returns_none(self):
        from storygraph.app.asset_panel import AssetPanelData
        assert AssetPanelData(_Engine(_registry())).get_detail('zzz') is None

    def test_search_matches_id_and_path(self):
        from storygraph.app.asset_panel import AssetPanelData
        p = AssetPanelData(_Engine(_registry()))
        assert ('images', 'ling_happy') in p.search('happy')
        assert ('images', 'bg_classroom') in p.search('classroom')
        assert p.search('nothing-here') == []

    def test_search_respects_limit(self):
        from storygraph.app.asset_panel import AssetPanelData
        assert len(AssetPanelData(_Engine(_registry())).search('i', limit=1)) == 1

    def test_statistics_counts_and_references(self):
        from storygraph.app.asset_panel import AssetPanelData
        stats = AssetPanelData(_Engine(_registry())).get_statistics()
        assert stats['images'] == 2
        assert stats['characters'] == 1
        assert stats['referenced'] == 1
        # 5 个 ID（2 图 + 1 变换 + 1 角色 …）里只有 1 个被引用
        total = sum(stats[n] for n in AssetPanelData.REGISTRY_NAMES)
        assert stats['unused'] == total - stats['referenced']
        assert stats['broken_refs'] == 0

    def test_statistics_without_registry_is_empty_dict(self):
        from storygraph.app.asset_panel import AssetPanelData
        assert AssetPanelData(_Engine(None)).get_statistics() == {}

    def test_category_breakdown(self):
        from storygraph.app.asset_panel import AssetPanelData
        b = AssetPanelData(_Engine(_registry())).get_category_breakdown()
        assert b == {'background': 1, 'character': 1}

    def test_group_names_are_sorted(self):
        from storygraph.app.asset_panel import AssetPanelData
        names = AssetPanelData(_Engine(_registry())).get_all_group_names()
        assert names == sorted(names)

    def test_optional_engine_capabilities_do_not_raise(self):
        """
        引擎可能没有 `linker` / `find_unused_assets` —— 这些是**可选能力**，
        缺了只该少几个统计数字，不该抛（面板一进来就崩）。
        """
        from storygraph.app.asset_panel import AssetPanelData
        p = AssetPanelData(_Engine(_registry()))
        assert p.get_character_ranking() == []
        assert p.get_broken_references() == []
        assert p.get_unused_assets() == []
        assert p.get_undefined_characters() == []
        assert p.get_unused_characters() == []


# ==================================================================
#  三、app.tables：剧本表双向工作流（重点）
# ==================================================================

@pytest.fixture
def table_env(tmp_path):
    """
    一个最小可往返的项目：`game/ch01.rpy` + 同名演出表 + 资源表。

    返回 dict(root, script, table)。
    """
    root = tmp_path / 'proj'
    game = root / 'game'
    game.mkdir(parents=True)
    script = game / 'ch01.rpy'
    script.write_text(
        'define e = Character("Eileen")\n'
        'label ch01:\n'
        '    scene bg classroom\n'
        '    e "第一句"\n'
        '    e "第二句"\n'
        '    return\n',
        encoding='utf-8')
    return {'root': root, 'script': script}


class TestGenerateTable:
    def test_generates_a_workbook_next_to_the_script(self, table_env):
        from storygraph.app import tables
        res = tables.generate_table(table_env['script'],
                                    project_root=table_env['root'])
        assert res['ok'], res['error']
        assert res['table_path'].exists()
        assert res['table_path'].suffix == '.xlsx'
        # 同名（`ch01.rpy` → `ch01.xlsx`）
        assert res['table_path'].stem == 'ch01'

    def test_missing_script_is_reported_not_raised(self, tmp_path):
        from storygraph.app import tables
        res = tables.generate_table(tmp_path / 'ghost.rpy')
        assert res['ok'] is False and '找不到' in res['error']

    def test_does_not_open_excel_by_itself(self, table_env, monkeypatch):
        """
        `generate_table` **不打开 Excel** —— 那是前端的事。
        无头环境/测试不该被外部程序打断（旧 tkinter 版是方法内部直接调的）。
        """
        import storygraph.graph.editor_utils as eu
        calls = []
        monkeypatch.setattr(eu, 'open_with_system_default',
                            lambda p: calls.append(p) or True)
        from storygraph.app import tables
        tables.generate_table(table_env['script'],
                              project_root=table_env['root'])
        assert calls == []


class TestExportTable:
    def _make_table(self, env, monkeypatch, text=None):
        """生成演出表；可选：改一句台词，模拟策划在 Excel 里编辑。"""
        from storygraph.app import tables
        res = tables.generate_table(env['script'], project_root=env['root'])
        assert res['ok'], res['error']
        return res['table_path']

    def test_roundtrip_or_patch_succeeds(self, table_env):
        """生成表 → 立刻写回：必须成功，且产物内容包含那两句台词。"""
        from storygraph.app import tables
        table = self._make_table(table_env, None)
        res = tables.export_table(table, registry=None,
                                  project_root=table_env['root'])
        assert not res['error'], res['error']
        assert res['beats'] >= 2
        assert res['blocking'] == [], res['blocking']
        done = tables.apply_export(res, project_root=table_env['root'])
        assert done['ok'], done.get('error')
        text = Path(done['out_path']).read_text(encoding='utf-8')
        assert '第一句' in text and '第二句' in text

    def test_output_never_lands_in_game_dir(self, table_env):
        """
        **产物不许写回 `game/`**（真实事故）：Ren'Py 把 `game/` 下所有
        `.rpy` 当同一个脚本，产物和原文会抢同一批 label。
        """
        from storygraph.app import tables
        table = self._make_table(table_env, None)
        res = tables.export_table(table, project_root=table_env['root'])
        out = Path(res['out_path'])
        assert 'build' in out.parts, f'产物没进 build/：{out}'
        assert 'game' not in out.parts, f'产物落进 game/ 了：{out}'

    def test_label_collision_blocks_and_keeps_a_review_copy(self, table_env):
        """
        落进 `game/` 且与原文抢 label → **拒写**，但另存一份
        `.blocked.rpy` 供人对比（只报错不给人看产物 = 让人猜哪里错了）。
        """
        from storygraph.app import tables
        table = self._make_table(table_env, None)
        clash = table_env['root'] / 'game' / 'ch01.generated.rpy'
        res = tables.export_table(table, project_root=table_env['root'],
                                  out_path=clash)
        assert res['blocking'], '与原文抢 label 竟然没被拦下'
        assert not clash.exists(), '被拦下却还是写了目标文件！'
        assert res['review_path'] is not None and \
            Path(res['review_path']).exists(), '被拦下却没留对比副本'

    def test_empty_table_is_reported(self, tmp_path):
        from storygraph.app import tables
        empty = tmp_path / 'empty.xlsx'
        try:
            import openpyxl
            openpyxl.Workbook().save(empty)
        except Exception:                     # noqa: BLE001
            pytest.skip('装不出空工作簿')
        res = tables.export_table(empty)
        assert res['ok'] is False and res['error']

    def test_unreadable_table_is_reported_not_raised(self, tmp_path):
        from storygraph.app import tables
        bad = tmp_path / 'bad.xlsx'
        bad.write_bytes(b'not a workbook')
        res = tables.export_table(bad)
        assert res['ok'] is False
        assert '读取剧本表失败' in res['error']

    def test_apply_export_is_a_separate_step(self, table_env):
        """
        「先算、再问、最后写」必须真的分得开：`export_table` 之后、
        `apply_export` 之前，**目标文件不存在**。
        """
        from storygraph.app import tables
        table = self._make_table(table_env, None)
        res = tables.export_table(table, project_root=table_env['root'])
        assert not Path(res['out_path']).exists(), \
            'export_table 自己就落盘了 —— 那"问过用户再写"就做不到了'
        tables.apply_export(res, project_root=table_env['root'])
        assert Path(res['out_path']).exists()

    def test_source_rpy_is_used_for_incremental_patch(self, table_env):
        """给了底稿 .rpy 就走增量补丁（保住原文的注释与未改行）。"""
        from storygraph.app import tables
        table = self._make_table(table_env, None)
        res = tables.export_table(table, project_root=table_env['root'],
                                  source_rpy=table_env['script'])
        assert '增量补丁' in res['note'] or '全量重生成' in res['note']
        assert res['note'], '没给出"用了增量还是全量"的说明'


class TestTableMtimeMemory:
    def test_first_sight_records_and_returns_false(self, tmp_path):
        from storygraph.app import tables
        f = tmp_path / 'a.xlsx'
        f.write_bytes(b'x')
        store = {}
        assert tables.table_changed_since(store, f) is False
        assert str(f.resolve()) in store

    def test_remember_and_detect(self, tmp_path):
        from storygraph.app import tables
        f = tmp_path / 'a.xlsx'
        f.write_bytes(b'x')
        store = {}
        tables.remember_table_mtime(store, f)
        assert tables.table_changed_since(store, f) is False
        st = f.stat()
        os.utime(f, (st.st_atime, st.st_mtime + 10))
        assert tables.table_changed_since(store, f) is True

    def test_missing_file_is_safe(self, tmp_path):
        from storygraph.app import tables
        assert tables.table_changed_since({}, tmp_path / 'nope.xlsx') is False


class TestCharacterTable:
    def test_exports_characters_xlsx(self, tmp_path):
        from storygraph.app import tables
        reg = _registry()
        asset_dir = tmp_path / 'asset_tables'
        asset_dir.mkdir()
        res = tables.export_character_table(reg, asset_dir)
        assert res['ok'], res['error']
        assert res['count'] == 1
        assert (asset_dir / 'characters.xlsx').exists()

    def test_no_characters_is_reported(self, tmp_path):
        from storygraph.app import tables
        from storygraph.assets.asset_registry import AssetRegistry
        res = tables.export_character_table(AssetRegistry(), tmp_path)
        assert res['ok'] is False and '没解析到' in res['error']

    def test_missing_asset_dir_is_reported(self):
        from storygraph.app import tables
        res = tables.export_character_table(_registry(), None)
        assert res['ok'] is False and '资产表目录' in res['error']

    def test_fields_are_the_documented_contract(self):
        from storygraph.app import tables
        assert tables.CHARACTER_TABLE_FIELDS[0] == 'ID'
        assert 'ImageTag' in tables.CHARACTER_TABLE_FIELDS


# ==================================================================
#  四、保存 / 撤销（卡片上那两个按钮的语义层）
# ==================================================================

class TestSaveAndUndo:
    @pytest.fixture
    def session(self):
        from storygraph.app.editor import EditorSession
        from storygraph.story.beat_model import Beat
        from storygraph.story.beat_render import make_symbols
        s = EditorSession(registry_provider=lambda: None)
        s.symbols = make_symbols(None)
        s.beats = [
            Beat(seq=1, label='c1', text='第一拍', speaker='e',
                 show='ling oh', scene='bg bg1'),
            Beat(seq=2, label='c1', text='第二拍', speaker='e'),
        ]
        return s

    def test_snapshot_and_restore_round_trip(self, session):
        snap = session.snapshot(0)
        session.beats[0].text = '改坏了'
        session.beats[0].show = ''
        session.restore(snap)
        assert session.beats[0].text == '第一拍'
        assert session.beats[0].show == 'ling oh'

    def test_snapshot_excludes_source_line_numbers(self, session):
        """
        撤销一次字段编辑**不该**改动"这一拍在源文件的哪几行" ——
        那是增量导出的定位信息，不是内容。
        """
        session.beats[0].src_first = 10
        session.beats[0].src_last = 14
        snap = session.snapshot(0)
        assert 'src_first' not in snap.data and 'src_last' not in snap.data

    def test_snapshot_keeps_code_interleaving(self, session):
        """`ordered`（raw/code 的交错顺序）必须进快照，否则撤销会排乱代码。"""
        session.beats[0].ordered = [('code', '$ f()'), ('raw', 'pause')]
        snap = session.snapshot(0)
        session.beats[0].ordered = []
        session.restore(snap)
        assert session.beats[0].ordered == [('code', '$ f()'), ('raw', 'pause')]

    def test_save_without_change_does_not_enable_undo(self, session):
        fields = dict(session.fields(0))
        res = session.save(0, fields, cards=session.cards(0),
                           advanced_open=True,
                           stage_loaded=fields['Stage'])
        assert res['changed'] is False
        assert session.has_undo(0) is False

    def test_save_with_change_enables_undo(self, session):
        res = session.save(0, {'Speaker': 'e', 'Text': '改了'},
                           cards=session.cards(0), advanced_open=False)
        assert res['changed'] is True
        assert session.has_undo(0) is True

    def test_undo_restores_previous_content(self, session):
        session.mark_loaded(0)
        session.save(0, {'Speaker': 'e', 'Text': '第一次'}, cards=None,
                     advanced_open=False)
        session.save(0, {'Speaker': 'e', 'Text': '第二次'}, cards=None,
                     advanced_open=False)
        session.undo()
        assert session.beats[0].text == '第一次', '应回到"上一次保存"'
        assert session.has_undo(0) is False, '单级撤销：撤到底就该没有'

    def test_undo_without_history_returns_none(self, session):
        assert session.undo(0) is None

    def test_undo_out_of_range_is_safe(self, session):
        assert session.undo(99) is None
        assert session.restore(None) is None

    def test_undo_only_touches_one_beat(self, session):
        session.save(0, {'Speaker': 'e', 'Text': 'A'}, cards=None,
                     advanced_open=False)
        session.save(1, {'Speaker': 'e', 'Text': 'B'}, cards=None,
                     advanced_open=False)
        second_before = session.beats[1].text
        session.undo(0)
        assert session.beats[1].text == second_before, '撤销动了别的拍'

    def test_load_clears_undo_history(self, session, tmp_path):
        """
        换章之后旧撤销记录必须失效 —— 否则"撤销"会把上一章的快照
        摊到这一章同拍号的 Beat 上（跨文件写错数据）。
        """
        session.save(0, {'Speaker': 'e', 'Text': 'X'}, cards=None,
                     advanced_open=False)
        assert session.has_undo(0)
        script = tmp_path / 'ch.rpy'
        script.write_text('label a:\n    e "hi"\n    return\n',
                          encoding='utf-8')
        session.load(script)
        assert session.has_undo(0) is False

    def test_forget_undo_is_idempotent(self, session):
        session.forget_undo()
        session.forget_undo()
        assert session.has_undo(0) is False

    def test_fingerprint_ignores_nothing_important(self, session):
        """
        指纹必须对**所有**进渲染的字段敏感 —— 漏一个就会出现
        "改了却说没改"（撤销按钮不亮，用户白改）。
        """
        snap = session.snapshot(0)
        base = snap.fingerprint()
        from storygraph.app.editor import _SNAPSHOT_FIELDS
        checked = 0
        for name in _SNAPSHOT_FIELDS:
            if name == 'ordered':
                continue
            cur = snap.data.get(name)
            if isinstance(cur, str):
                snap.data[name] = cur + 'X'
                assert snap.fingerprint() != base, f'{name} 不进指纹'
                snap.data[name] = cur
                checked += 1
        assert checked > 20, f'参与比对的字段太少（{checked}），快照可能漏字段'

    def test_mark_loaded_ignores_out_of_range(self, session):
        session.mark_loaded(99)
        assert session.loaded_index is None


# ==================================================================
#  四、结构分析：三种"走不到"必须分清楚
# ==================================================================

class TestGraphAnalysisClassification:
    """
    `EditorSession.analyze_graph_problems` —— 静态分析的**结论分类**。

    为什么要有这一层（真实项目实测）：`sr_general` 有 51 个块 / 40 条边，
    入口是权威的 `start`，分析层把其余 40 个块全判成"不可达"。
    可它们绝大多数是各章节文件自己的 label，靠菜单 / `call screen` /
    外部跳转进入 —— 解析器只认字面 jump/call/menu，所以图上 40/51 全红。
    **全红等于没标**，用户会直接忽略这个功能。

    于是分成三类，而且必须**画回图上**（前端只认这几组 id）：
      · stranded  从任何入口都到不了（关在没有入口的环里）→ 标红
      · dead_end  所有出口分支条件都恒假（下落风险）    → 标红
      · independent 从本入口走不到、但别的入口可达      → 灰虚线，不算问题
    """

    @staticmethod
    def _session():
        from storygraph.app.editor import EditorSession
        return EditorSession()

    def test_isolated_cycle_is_stranded(self):
        from storygraph.graph.models import Graph

        g = Graph()
        g.add_node('start', 0, 0)
        b = g.add_node('loop_b', 0, 0)
        c = g.add_node('loop_c', 0, 0)
        g.add_edge(b, c)
        g.add_edge(c, b)
        res = self._session().analyze_graph_problems(g)
        assert res['ok']
        assert set(res['stranded_ids']) == {b, c}
        assert set(res['problem_ids']) == {b, c}
        assert res['counts']['stranded'] == 2

    def test_reachable_from_another_root_is_not_a_problem(self):
        """从 start 走不到、但从别的入口可达 → 独立入口，**不许**标红。"""
        from storygraph.graph.models import Graph

        g = Graph()
        g.add_node('start', 0, 0)
        x = g.add_node('menu_root', 0, 0)
        y = g.add_node('chapter_x', 0, 0)
        g.add_edge(x, y)
        res = self._session().analyze_graph_problems(g)
        assert res['stranded_ids'] == [], '从别的入口可达的块被当成断了'
        assert res['problem_ids'] == []
        assert set(res['independent_ids']) == {x, y}
        assert any('独立入口' in n for n in res['notes'])

    def test_impossible_branch_is_a_dead_end(self):
        """所有出口条件恒假 = 死胡同（下落风险），要标出来。"""
        from storygraph.graph.models import Graph

        g = Graph()
        a = g.add_node('start', 0, 0)
        b = g.add_node('never', 0, 0)
        g.add_edge(a, b, condition='love >= 5')   # 入口处 love 恒为 0
        res = self._session().analyze_graph_problems(g)
        assert a in res['dead_end_ids'], res['warnings']
        assert a in res['problem_ids']

    def test_summary_counts_are_consistent_with_the_id_sets(self):
        from storygraph.graph.models import Graph

        g = Graph()
        g.add_node('start', 0, 0)
        x = g.add_node('menu_root', 0, 0)
        b = g.add_node('loop_b', 0, 0)
        c = g.add_node('loop_c', 0, 0)
        g.add_edge(x, c)
        g.add_edge(b, c)
        g.add_edge(c, b)
        res = self._session().analyze_graph_problems(g)
        assert res['counts']['stranded'] == len(res['stranded_ids'])
        assert res['counts']['independent'] == len(res['independent_ids'])
        assert '真断了' in res['summary']
        assert x not in res['problem_ids']

    def test_empty_graph_is_a_blank_result_not_a_crash(self):
        res = self._session().analyze_graph_problems(None)
        assert res['ok'] is False
        assert res['problem_ids'] == []
        assert res['text']

    def test_text_keeps_the_detailed_report(self):
        """详细文本（「变量分析」用）仍然逐节点列出状态。"""
        from storygraph.graph.models import Graph

        g = Graph()
        g.add_node('start', 0, 0)
        res = self._session().analyze_graph_problems(g)
        assert '节点: start' in res['text']
        assert '变量区间分析结果' in res['text']


class TestChapterImportScope:
    """
    「剧情图里只有用户导入的章节」这条语义的**会话层**判据。

    用户 2026-10-03：
    > 我没有叫你所有的文件全部导进剧情图内！……所谓的导入项目只是为了表格的
    > xlsx 的资源管理系统！不是把整个项目都排在剧情图上面！！！

    所以：`import_chapters` 只吃"用户选中的文件"，`load_assets` 只吃资源表
    而且**签名里根本没有 graph**（想灌也灌不进去）。
    """

    @staticmethod
    def _session(root=None):
        from storygraph.app.editor import EditorSession
        return EditorSession(project_root=root)

    def test_import_takes_only_the_selected_files(self, write_rpy):
        a = write_rpy('label ch01:\n    "一"\n    jump ch02\n\n'
                      'label ch02:\n    "二"\n    return\n', 'ch01.rpy')
        write_rpy('label other:\n    "别的"\n    return\n', 'other.rpy')
        res = self._session().import_chapters([a])
        labels = {n.label_name for n in res['graph'].nodes.values()}
        assert labels == {'ch01', 'ch02'}, labels
        assert 'other' not in labels, '没选中的文件也进了图'

    def test_import_accumulates_across_batches(self, write_rpy):
        a = write_rpy('label ch01:\n    "一"\n    return\n', 'a.rpy')
        b = write_rpy('label ch02:\n    "二"\n    return\n', 'b.rpy')
        s = self._session()
        res = s.import_chapters([a])
        graph = res['graph']
        res2 = s.import_chapters([b], graph)
        labels = {n.label_name for n in res2['graph'].nodes.values()}
        assert labels == {'ch01', 'ch02'}, labels
        assert res2['added_nodes'] == 1

    def test_reimport_does_not_duplicate(self, write_rpy):
        a = write_rpy('label ch01:\n    "一"\n    return\n', 'a.rpy')
        s = self._session()
        graph = s.import_chapters([a])['graph']
        res = s.import_chapters([a], graph)
        labels = [n.label_name for n in res['graph'].nodes.values()]
        assert labels.count('ch01') == 1
        assert res['added_nodes'] == 0

    def test_import_reports_counts_and_files(self, write_rpy):
        a = write_rpy('label ch01:\n    "一"\n    jump ch02\n\n'
                      'label ch02:\n    return\n', 'ch01.rpy')
        res = self._session().import_chapters([a])
        assert res['ok'] and res['added_nodes'] == 2
        assert res['added_edges'] >= 1, 'jump 连线没进来'
        assert res['files'] == [str(a)]
        assert res['nodes'] == 2 and res['edges'] == res['added_edges']

    def test_import_ignores_non_rpy_and_missing_paths(self, write_rpy, tmp_path):
        txt = tmp_path / 'notes.txt'
        txt.write_text('label x:\n    return\n', encoding='utf-8')
        res = self._session().import_chapters([txt, tmp_path / 'nope.rpy'])
        assert res['ok'] is False and res['added_nodes'] == 0
        assert '没有选中' in res['error']

    def test_graph_files_reflects_what_is_imported(self, write_rpy):
        a = write_rpy('label ch01:\n    return\n', 'a.rpy')
        b = write_rpy('label ch02:\n    return\n', 'b.rpy')
        s = self._session()
        graph = s.import_chapters([a, b])['graph']
        assert [Path(f).name for f in s.graph_files(graph)] == ['a.rpy', 'b.rpy']

    def test_drop_chapters_removes_nodes_and_their_edges(self, write_rpy):
        a = write_rpy('label ch01:\n    "一"\n    jump ch02\n\n'
                      'label ch02:\n    "二"\n    return\n', 'a.rpy')
        b = write_rpy('label other:\n    "别的"\n    return\n', 'b.rpy')
        s = self._session()
        graph = s.import_chapters([a, b])['graph']
        before_edges = len(graph.edges)
        res = s.drop_chapters(graph, [a])
        labels = {n.label_name for n in graph.nodes.values()}
        assert labels == {'other'}, labels
        assert res['removed_nodes'] == 2
        assert len(graph.edges) < before_edges, '移除后留下了悬空连线'

    def test_load_assets_reads_the_registry_and_leaves_editing_alone(self,
                                                                     ui_project):
        s = self._session(ui_project)
        res = s.load_assets()
        assert res['ok'], res
        assert sum(res['counts'].values()) > 0, res['counts']
        assert s.registry() is not None
        assert s.symbols is not None, '符号表（下拉候选）没跟着刷新'
        assert s.beats == [], 'load_assets 不该载入任何一章'

    def test_load_assets_without_a_directory_says_so(self):
        res = self._session().load_assets()
        assert res['ok'] is False and res['error']

    def test_import_merges_script_characters_into_the_registry(self, ui_project):
        """
        导入章节时顺手把 `define x = Character(...)` 合进资源表 ——
        说话人候选就是这么来的（这一步以前挂在"重新扫描项目"上，
        而那个动作现在只管资源表，不能再顺手碰图）。
        """
        s = self._session(ui_project)
        assert s.load_assets()['ok']
        before = set(s.characters())
        fp = ui_project / 'game' / 'script' / 'newchar.rpy'
        fp.write_text('define zz = Character("Zed")\n\n'
                      'label zz_ch:\n    "hi"\n    return\n', encoding='utf-8')
        s.import_chapters([fp])
        after = set(s.characters())
        assert after - before, f'导入后没多出说话人候选（{before}）'
        assert any('zz' in c for c in after - before), after - before


class TestStageDigestAndMissingChapters:
    """
    用户 2026-10-03 的两条：

    > 我连线呢！？？？tkinter那一版都有连线，你这个为何显示不出来？
    > 为何不写明现在的舞台情况是什么样的，旧版明明都有……
    """

    @staticmethod
    def _session(root=None):
        from storygraph.app.editor import EditorSession
        return EditorSession(project_root=root)

    # ---- 舞台情况（旧版预览面板的 stage_digest，卡片搬 Qt 时没接）----

    def test_stage_digest_mentions_cg_layer(self, write_rpy):
        a = write_rpy('label ch:\n    scene bg room\n    show cg cg03 at bgfit\n'
                      '    "看这张图"\n    return\n', 'ch.rpy')
        s = self._session()
        s.load(a)
        text = s.stage_digest_text(0)
        assert '背景：bg room' in text, text
        assert 'CG/图层' in text and 'cg cg03' in text, \
            f'舞台说明里看不出屏幕上有 CG：{text}'

    def test_stage_digest_shows_changes_and_music(self, write_rpy):
        a = write_rpy('label ch:\n    play music bgm01\n    "音乐起"\n'
                      '    return\n', 'ch.rpy')
        s = self._session()
        s.load(a)
        text = s.stage_digest_text(0)
        assert '本拍变化' in text and 'bgm01' in text, text

    def test_stage_digest_empty_session_is_safe(self):
        assert self._session().stage_digest_text(0) == ''

    def test_inherited_hint_is_reachable(self, write_rpy):
        a = write_rpy('label ch:\n    scene bg room\n    "一"\n    "二"\n'
                      '    return\n', 'ch.rpy')
        s = self._session()
        s.load(a)
        assert '背景 bg room' in s.inherited_hint(1), s.inherited_hint(1)

    # ---- "我连线呢？"：图上没有线时要说清还缺哪个文件 ----

    def test_missing_chapters_found_by_reference_direction(self, write_rpy):
        """
        导入的章节**自己不含跳转**时，串起它们的是**另一个文件**。

        真实项目就是这个形状：`chapter1_1/1_2/1_3.rpy` 各自只有一个 label +
        `return`，`chapter_controller.rpy` 里 `call chapter1_1` / `jump …`。
        只查"声明方向"会什么都提示不出来（这正是上一版的盲点）。
        """
        ch = write_rpy('label chapter1_1:\n    "一"\n    return\n',
                       'chapter1_1.rpy')
        write_rpy('label chapter1_1_control:\n'
                  '    call chapter1_1\n'
                  '    return\n', 'chapter_controller.rpy')
        s = self._session(ch.parent)
        graph = s.import_chapters([ch])['graph']
        info = s.missing_chapter_files(graph)
        names = [Path(f).name for f in info['files']]
        assert 'chapter_controller.rpy' in names, \
            f'没提示出"串起这一章的文件"：{info}'

    def test_missing_chapters_found_by_declaration_direction(self, write_rpy):
        """图上有占位块（跳到未导入的 label）→ 提示声明它的文件。"""
        a = write_rpy('label ch:\n    jump elsewhere\n    return\n', 'a.rpy')
        b = write_rpy('label elsewhere:\n    "在别处"\n    return\n', 'b.rpy')
        s = self._session(a.parent)
        graph = s.import_chapters([a])['graph']
        placeholders = [n.label_name for n in graph.nodes.values()
                        if not n.file_path]
        assert placeholders == ['elsewhere'], placeholders
        info = s.missing_chapter_files(graph)
        assert 'b.rpy' in [Path(f).name for f in info['files']], info

    def test_missing_chapters_unknown_targets_are_reported(self, write_rpy):
        a = write_rpy('label ch:\n    jump typo_lable\n    return\n', 'a.rpy')
        s = self._session(a.parent)
        graph = s.import_chapters([a])['graph']
        info = s.missing_chapter_files(graph)
        assert info['files'] == []
        assert info['unknown'] == ['typo_lable'], info

    def test_importing_the_suggested_files_produces_the_edges(self, write_rpy):
        """补全之后**连线必须真的出现**（这才是用户要的结果）。"""
        ch = write_rpy('label chapter1_1:\n    "一"\n    return\n', 'c1.rpy')
        write_rpy('label chapter1_1_control:\n'
                  '    call chapter1_1\n    return\n', 'controller.rpy')
        s = self._session(ch.parent)
        graph = s.import_chapters([ch])['graph']
        assert len(graph.edges) == 0
        info = s.missing_chapter_files(graph)
        res = s.import_chapters(info['files'], graph)
        assert len(res['graph'].edges) >= 1, '补全后连线还是没出现'


class TestLayoutJsonAndControlFlowExport:
    """
    用户 2026-10-04：

    > 我的旧版本有导出控制流图.rpy文件，label末尾加_controller的那个……
    > 有保存布局.json！现在这个新版本呢？为何模块编辑器的功能缺失那么多？？？

    后端一直都在（`pipeline.renpy_exporter.export_to_renpy`、
    `graph.commands.LoadLayoutCommand`），缺的只是 UI 入口 —— 而布局
    **文件格式必须与旧版一致**，否则用户以前存的手排版式全作废。
    """

    def _graph(self, n=3):
        from storygraph.graph.models import Graph
        g = Graph()
        ids = [g.add_node(f'ch{i}', 100.0 * i, 50.0 * i,
                          operations=['love += 1'] if i == 0 else None,
                          file_path=f'/p/ch{i}.rpy', line_number=i + 1)
               for i in range(n)]
        g.add_edge(ids[0], ids[1], etype='call', condition='love >= 2')
        g.add_edge(ids[1], ids[2], etype='jump', condition='',
                   option_text='去屋顶')
        return g, ids

    # ---- 保存布局 ----

    def test_save_layout_uses_the_old_format(self, tmp_path):
        from storygraph.app.editor import EditorSession
        g, ids = self._graph()
        target = tmp_path / 'layout.json'
        res = EditorSession().save_layout(g, target)
        assert res['ok'] and res['nodes'] == 3 and res['edges'] == 2
        import json
        data = json.loads(target.read_text(encoding='utf-8'))
        # **逐字段**与旧版 tkinter `graph_view.save_layout` 一致
        assert sorted(data.keys()) == ['edges', 'next_ids', 'nodes']
        n0 = data['nodes'][str(ids[0])]
        # 迁移（2026-10-05）：多了 `statements`（结构化真源，含出处/条件），
        # 旧版的七个键一个不少 —— 老读者只多一个它不认识的键，其余照旧。
        assert sorted(n0.keys()) == ['file_path', 'is_current', 'label',
                                     'line_number', 'operations',
                                     'statements', 'x', 'y']
        assert n0['label'] == 'ch0' and n0['operations'] == ['love += 1']
        assert n0['statements'][0]['kind'] == 'python'
        assert n0['statements'][0]['payload']['code'] == 'love += 1'
        e0 = data['edges'][next(iter(data['edges']))]
        # 旧版的五个键一个不少（老文件照样能读）；唯一新增的是 rom_clause
        # —— call X from Y 的存档点标识，不存它存布局→读布局就会丢。
        assert {'condition', 'from', 'option_text', 'to', 'type'} <= set(e0)
        # 旧版的五个键一个不少；**新增过两个**，都有必须存下来的理由：
        #   · `from_clause` —— `call X from Y` 的存档点标识（2026-10-05）；
        #   · `origin`      —— 这条边来自源文件（source）还是用户画的草图
        #                      （manual）（2026-10-22 第二阶段）；
        #   · `line_id`     —— 这条边对应哪一拍（同 label 里多次 call 同一目标
        #                      靠它对位，同上）。
        # 后两者不存会怎样：一存一读，手工边全变成 `source`、`line_id` 全空，
        # 下一次"按 flow 拍重建 source 边"就会把用户画的线删掉、把重复的
        # call 合成一条 —— 工具吃掉用户的动作，比双真源更糟。
        assert set(e0) - {'condition', 'from', 'option_text', 'to', 'type'} \
            == {'from_clause', 'origin', 'line_id'}
        assert data['next_ids'] == {'node': g.next_node_id,
                                    'edge': g.next_edge_id}

    def test_old_layout_file_still_loads(self, tmp_path):
        """**旧版存过的文件必须还能读**（含我们现在不用的 is_current）。"""
        import json
        from storygraph.app.editor import EditorSession
        old = {
            "nodes": {"3": {"label": "a", "x": 12, "y": 34,
                            "is_current": True, "operations": ["x = 1"],
                            "file_path": "/p/a.rpy", "line_number": 7},
                      "9": {"label": "b", "x": 56, "y": 78,
                            "is_current": False, "operations": [],
                            "file_path": None, "line_number": None}},
            "edges": {"2": {"from": 3, "to": 9, "type": "jump",
                            "condition": "flag", "option_text": ""}},
            "next_ids": {"node": 10, "edge": 3},
        }
        p = tmp_path / 'old.json'
        p.write_text(json.dumps(old), encoding='utf-8')
        res = EditorSession().load_layout(p)
        assert res['ok'], res
        g = res['graph']
        assert set(g.nodes) == {3, 9} and set(g.edges) == {2}
        assert g.nodes[3].label_name == 'a' and g.nodes[3].x == 12
        assert g.nodes[3].operations == ['x = 1']
        assert g.nodes[9].file_path is None
        assert g.edges[2].condition == 'flag'
        assert g.next_node_id == 10 and g.next_edge_id == 3

    def test_layout_round_trip_keeps_positions(self, tmp_path):
        from storygraph.app.editor import EditorSession
        s = EditorSession()
        g, ids = self._graph()
        g.nodes[ids[1]].x, g.nodes[ids[1]].y = 777.0, 888.0
        target = tmp_path / 'rt.json'
        assert s.save_layout(g, target)['ok']
        back = s.load_layout(target)
        assert back['ok']
        assert back['graph'].nodes[ids[1]].x == 777.0
        assert back['graph'].nodes[ids[1]].y == 888.0
        # 第二条边才是带菜单项的那条（第一条是纯 call）
        menu_eid = [eid for eid, e in g.edges.items() if e.option_text][0]
        assert back['graph'].edges[menu_eid].option_text == '去屋顶'

    def test_bad_layout_files_are_reported_not_crashed(self, tmp_path):
        from storygraph.app.editor import EditorSession
        bad = tmp_path / 'bad.json'
        bad.write_text('{"hello": 1}', encoding='utf-8')
        res = EditorSession().load_layout(bad)
        assert res['ok'] is False and 'nodes' in res['error']
        missing = tmp_path / 'nope.json'
        assert EditorSession().load_layout(missing)['ok'] is False

    # ---- 导出控制流 ----

    def test_export_control_flow_makes_control_labels(self, tmp_path):
        from storygraph.app.editor import EditorSession
        g, ids = self._graph()
        out = tmp_path / 'flow.rpy'
        res = EditorSession().export_control_flow(g, out)
        assert res['ok'], res
        text = out.read_text(encoding='utf-8')
        for label in ('ch0_control', 'ch1_control', 'ch2_control'):
            assert f'label {label}:' in text, text
        # 每个控制块都要 call 回原 label（旧版生成规则）
        assert 'call ch0' in text and 'call ch1' in text
        # 菜单选项与条件都要落到控制流里
        assert '"去屋顶"' in text
        assert 'if love >= 2' in text
        assert res['count'] == 3 and 'ch0_control' in res['labels']

    def test_export_control_flow_refuses_empty_graph(self, tmp_path):
        from storygraph.app.editor import EditorSession
        from storygraph.graph.models import Graph
        res = EditorSession().export_control_flow(Graph(), tmp_path / 'x.rpy')
        assert res['ok'] is False and '空' in res['error']


class TestReachableFromAllRoots:
    """`graph.analysis.reachable_from_all_roots` —— 区分两种"走不到"的判据。"""

    def test_cycle_without_entry_is_not_reachable(self):
        from storygraph.graph.analysis import reachable_from_all_roots
        from storygraph.graph.models import Graph

        g = Graph()
        a = g.add_node('start', 0, 0)
        b = g.add_node('b', 0, 0)
        c = g.add_node('c', 0, 0)
        g.add_edge(b, c)
        g.add_edge(c, b)
        assert reachable_from_all_roots(g) == {a}

    def test_all_roots_are_included(self):
        from storygraph.graph.analysis import reachable_from_all_roots
        from storygraph.graph.models import Graph

        g = Graph()
        a = g.add_node('a', 0, 0)
        b = g.add_node('b', 0, 0)
        c = g.add_node('c', 0, 0)
        g.add_edge(a, b)
        assert reachable_from_all_roots(g) == {a, b, c}

    def test_graph_without_roots_claims_nothing(self):
        """整张图都在环里：没有"入口"可言，不能据此断言谁走不到。"""
        from storygraph.graph.analysis import reachable_from_all_roots
        from storygraph.graph.models import Graph

        g = Graph()
        a = g.add_node('a', 0, 0)
        b = g.add_node('b', 0, 0)
        g.add_edge(a, b)
        g.add_edge(b, a)
        assert reachable_from_all_roots(g) == {a, b}