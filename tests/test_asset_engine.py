"""
tests/test_asset_engine.py
==========================
AssetEngine 测试（不依赖 UI）。

策略：
  · 尽量不启动后台线程；需要观察线程行为时显式 start/stop
  · 用 asset_env fixture 提供的标准项目布局
  · 变更检测通过直接构造前后两版 state 来驱动 _diff_state
"""

import pytest

from asset_engine import AssetEngine, _apply_rename_patch
from asset_registry import AssetRegistry
from asset_events import AssetEventType, AssetEventBus, ConfirmationGate


@pytest.fixture
def engine(asset_env):
    eng = AssetEngine(
        asset_dir=asset_env['asset_dir'],
        project_root=asset_env['project_root'],
        script_files=[asset_env['game_dir'] / 'script.rpy'],
        watch_source_dirs=False,
        poll_interval=0.05,
    )
    yield eng
    if eng.is_running():
        eng.stop()


# ------------------------------------------------------------------
#  路径工具
# ------------------------------------------------------------------

class TestPathHelpers:
    def test_under_path_true(self, tmp_path):
        parent = tmp_path / 'a'
        child = parent / 'b' / 'c.txt'
        child.parent.mkdir(parents=True)
        child.write_text('x')
        assert AssetEngine.is_under_path(child, parent)

    def test_same_path_counts(self, tmp_path):
        d = tmp_path / 'a'
        d.mkdir()
        assert AssetEngine.is_under_path(d, d)

    def test_outside_path_false(self, tmp_path):
        a = tmp_path / 'a'
        b = tmp_path / 'b'
        a.mkdir()
        b.mkdir()
        assert not AssetEngine.is_under_path(a, b)

    def test_nonexistent_path_still_compares_by_prefix(self, tmp_path):
        """
        不存在的路径也能判定层级：resolve() 不做存在性检查，
        只做字符串前缀比较。这是实际行为，脚本在判断
        "某个待创建文件路径是否归 tl/ 管"时依赖它。
        """
        assert AssetEngine.is_under_path(tmp_path / 'ghost', tmp_path)
        assert not AssetEngine.is_under_path(tmp_path, tmp_path / 'ghost')

    def test_none_input_returns_false(self, tmp_path):
        assert not AssetEngine.is_under_path(None, tmp_path)
        assert not AssetEngine.is_under_path(tmp_path, None)

    def test_string_input(self, tmp_path):
        d = tmp_path / 'a'
        d.mkdir()
        assert AssetEngine.is_under_path(str(d), str(tmp_path))


# ------------------------------------------------------------------
#  生命周期
# ------------------------------------------------------------------

class TestLifecycle:
    def test_initial_load_populates_registry(self, engine):
        engine._initial_load()
        assert engine.registry is not None
        assert 'bg_classroom' in engine.registry.images
        assert 'bgm_theme' in engine.registry.music

    def test_script_characters_synced_into_registry(self, engine):
        engine._initial_load()
        assert 'e' in engine.registry.characters
        assert engine.registry.characters['e']['Name'] == 'Eileen'

    def test_references_rebuilt(self, engine):
        engine._initial_load()
        refs = engine.get_references()
        assert 'bgm_theme' in refs
        assert 'e' in refs       # 角色对话位置

    def test_start_and_stop(self, engine):
        engine.start()
        assert engine.is_running()
        engine.stop()
        assert not engine.is_running()

    def test_start_emits_ready_event(self, asset_env):
        bus = AssetEventBus()
        events = []
        bus.subscribe(None, events.append)
        eng = AssetEngine(
            asset_dir=asset_env['asset_dir'],
            project_root=asset_env['project_root'],
            script_files=[asset_env['game_dir'] / 'script.rpy'],
            event_bus=bus,
            watch_source_dirs=False,
            poll_interval=0.05,
        )
        eng.start()
        eng.stop()
        assert any(e.event_type is AssetEventType.ENGINE_READY for e in events)

    def test_missing_asset_tables_auto_generated(self, tmp_path):
        project = tmp_path / 'proj'
        game = project / 'game' / 'images'
        game.mkdir(parents=True)
        (game / 'pic.png').write_bytes(b'1')
        asset_dir = tmp_path / 'tables'
        eng = AssetEngine(asset_dir=asset_dir, project_root=project,
                          watch_source_dirs=False)
        eng._initial_load()
        assert (asset_dir / 'images.xlsx').exists()
        assert 'pic' in eng.registry.images

    def test_existing_tables_not_overwritten(self, asset_env):
        """已有表且用户改过 Notes → 不能被重新扫描覆盖。"""
        import openpyxl
        f = asset_env['asset_dir'] / 'images.xlsx'
        wb = openpyxl.load_workbook(f)
        ws = wb.active
        ws.cell(row=2, column=6, value='KEEP ME')
        wb.save(f)

        eng = AssetEngine(asset_dir=asset_env['asset_dir'],
                          project_root=asset_env['project_root'],
                          watch_source_dirs=False)
        eng._initial_load()
        row = eng.registry.images.get('bg_classroom') or \
            eng.registry.images.get('bg_street')
        assert row is not None
        assert any(r.get('Notes') == 'KEEP ME'
                   for r in eng.registry.images.values())


# ------------------------------------------------------------------
#  差异检测
# ------------------------------------------------------------------

class _StateFactory:
    @staticmethod
    def make(**cats):
        return cats


class TestDiffState:
    def _diff(self, engine, old, new):
        return engine._diff_state(old, new)

    def test_added(self, engine):
        changes = self._diff(
            engine, {'images': {}}, {'images': {'a': {'Path': 'x.png'}}})
        assert [(c.kind, c.asset_id) for c in changes] == [('added', 'a')]

    def test_removed(self, engine):
        changes = self._diff(
            engine, {'images': {'a': {'Path': 'x.png'}}}, {'images': {}})
        assert [(c.kind, c.asset_id) for c in changes] == [('removed', 'a')]

    def test_renamed_detected_by_path(self, engine):
        changes = self._diff(
            engine,
            {'images': {'old': {'Path': 'x.png'}}},
            {'images': {'new': {'Path': 'x.png'}}})
        renamed = [c for c in changes if c.kind == 'renamed']
        assert renamed and renamed[0].new_id == 'new'

    def test_moved_detected_by_path_change(self, engine):
        changes = self._diff(
            engine,
            {'images': {'a': {'Path': 'x.png'}}},
            {'images': {'a': {'Path': 'y.png'}}})
        assert [(c.kind, c.asset_id) for c in changes] == [('moved', 'a')]

    def test_modified_detected(self, engine):
        changes = self._diff(
            engine,
            {'images': {'a': {'Path': 'x.png', 'Notes': '1'}}},
            {'images': {'a': {'Path': 'x.png', 'Notes': '2'}}})
        assert [(c.kind, c.asset_id) for c in changes] == [('modified', 'a')]

    def test_no_change(self, engine):
        state = {'images': {'a': {'Path': 'x.png'}}}
        assert self._diff(engine, state, dict(state)) == []

    def test_missing_category_tolerated(self, engine):
        assert self._diff(engine, {}, {}) == []


class TestCharacterRenameInference:
    """
    characters 表没有 Path 列，重命名只能靠身份字段推断。

    这是"用户在 Excel 里改角色名 → 脚本自动跟着改"的关键路径，
    之前因为先同步脚本 define 再 diff，改名被彻底掩盖了。
    """

    def test_rename_detected_by_name(self, engine):
        renames = engine._infer_character_renames(
            {'e': {'ID': 'e', 'Name': 'Eileen'}},
            {'eileen': {'ID': 'eileen', 'Name': 'Eileen'}},
        )
        assert renames == {'e': 'eileen'}

    def test_renamed_row_emits_rename_change(self, engine):
        old_state = {'images': {}, 'music': {}, 'sounds': {},
                     'transforms': {},
                     'characters': {'e': {'ID': 'e', 'Name': 'Eileen'}}}
        new_state = dict(old_state)
        new_state['characters'] = {
            'eileen': {'ID': 'eileen', 'Name': 'Eileen'}}
        changes = engine._diff_state(old_state, new_state,
                                     {'e': 'eileen'})
        renamed = [c for c in changes if c.kind == 'renamed']
        assert renamed and renamed[0].new_id == 'eileen'
        # 目标不能同时报成新增
        assert not [c for c in changes if c.kind == 'added']

    def test_conflicting_identity_not_treated_as_rename(self, engine):
        renames = engine._infer_character_renames(
            {'e': {'ID': 'e', 'Name': 'Eileen'}},
            {'m': {'ID': 'm', 'Name': 'Mary'}},
        )
        assert renames == {}

    def test_missing_identity_fields_falls_back_to_removed(self, engine):
        """没有可比字段时不该臆造重命名。"""
        renames = engine._infer_character_renames(
            {'e': {'ID': 'e'}},
            {'x': {'ID': 'x'}},
        )
        assert renames == {}

    def test_one_target_not_claimed_twice(self, engine):
        renames = engine._infer_character_renames(
            {'a': {'ID': 'a', 'Name': 'Same'},
             'b': {'ID': 'b', 'Name': 'Same'}},
            {'combined': {'ID': 'combined', 'Name': 'Same'}},
        )
        # 只剩一个目标，只能被一个来源认领
        assert len(renames) == 1

    def test_edit_distance_picks_closest_name(self, engine):
        assert engine._edit_distance('e', 'ee') == 1
        assert engine._edit_distance('abc', 'abc') == 0
        assert engine._edit_distance('kitten', 'sitting') == 3

    def test_no_change_no_renames(self, engine):
        chars = {'e': {'ID': 'e', 'Name': 'Eileen'}}
        assert engine._infer_character_renames(chars, dict(chars)) == {}

    def test_end_to_end_rename_reaches_script(self, asset_env):
        """
        完整链路：改 Excel → poll → .rpy 里的 define 与对话同步改名。
        """
        from asset_registry import write_dict_to_excel
        asset_dir = asset_env['asset_dir']
        script = asset_env['game_dir'] / 'script.rpy'
        script.write_text(
            'define meiko = Character("Meiko")\n'
            'label start:\n'
            '    meiko "hello"\n'
            '    return\n', encoding='utf-8')

        eng = AssetEngine(asset_dir=asset_dir,
                          project_root=asset_env['project_root'],
                          script_files=[script],
                          watch_source_dirs=False)
        eng._initial_load()
        assert 'meiko' in eng.registry.characters

        write_dict_to_excel(asset_dir / 'characters.xlsx',
                            {'mk': {'ID': 'mk', 'Name': 'Meiko'}},
                            ['ID', 'Name'])
        eng._mark_dirty(eng.TASK_TABLE)
        eng.poll()

        text = script.read_text(encoding='utf-8')
        assert 'define mk = Character("Meiko")' in text
        assert 'mk "hello"' in text
        assert 'mk' in eng.registry.characters


# ------------------------------------------------------------------
#  文件系统分类
# ------------------------------------------------------------------

class TestFsChangeRouting:
    def test_table_change_marked(self, engine, asset_env):
        engine._on_fs_change([(asset_env['asset_dir'] / 'images.xlsx',
                               'modified')])
        assert engine._dirty == {engine.TASK_TABLE}

    def test_script_change_marked(self, engine, asset_env):
        engine._on_fs_change([(asset_env['game_dir'] / 'script.rpy',
                               'modified')])
        assert engine._dirty == {engine.TASK_SCRIPT}

    def test_translation_file_ignored(self, engine, asset_env):
        tl = asset_env['game_dir'] / 'tl' / 'en'
        tl.mkdir(parents=True)
        engine._on_fs_change([(tl / 'x.rpy', 'modified')])
        assert engine._dirty == set()

    def test_source_file_marked(self, engine, asset_env):
        engine.watch_source_dirs = True
        engine._on_fs_change([(asset_env['project_root'] / '1.png', 'added')])
        assert engine._dirty == {engine.TASK_SOURCE}

    def test_xlsx_outside_asset_dir_ignored(self, engine, tmp_path):
        other = tmp_path / 'other.xlsx'
        other.write_bytes(b'1')
        engine._on_fs_change([(other, 'modified')])
        assert engine._dirty == set()


# ------------------------------------------------------------------
#  poll 驱动的处理
# ------------------------------------------------------------------

class TestPoll:
    def test_poll_handles_table_task(self, asset_env):
        bus = AssetEventBus()
        events = []
        bus.subscribe(None, events.append)
        eng = AssetEngine(asset_dir=asset_env['asset_dir'],
                          project_root=asset_env['project_root'],
                          script_files=[asset_env['game_dir'] / 'script.rpy'],
                          event_bus=bus,
                          watch_source_dirs=False)
        eng._initial_load()
        eng._mark_dirty(eng.TASK_TABLE)
        eng.poll()
        assert any(e.event_type is AssetEventType.TABLE_RELOADED
                   for e in events)

    def test_poll_handles_script_task(self, asset_env):
        bus = AssetEventBus()
        events = []
        bus.subscribe(None, events.append)
        eng = AssetEngine(asset_dir=asset_env['asset_dir'],
                          project_root=asset_env['project_root'],
                          script_files=[asset_env['game_dir'] / 'script.rpy'],
                          event_bus=bus,
                          watch_source_dirs=False)
        eng._initial_load()
        eng._mark_dirty(eng.TASK_SCRIPT)
        eng.poll()
        assert any(e.event_type is AssetEventType.SCRIPT_REFERENCES_REBUILT
                   for e in events)

    def test_poll_no_dirty_is_noop(self, engine):
        engine._initial_load()
        engine.poll()      # 不应抛


# ------------------------------------------------------------------
#  查询接口
# ------------------------------------------------------------------

class TestQueries:
    def test_get_all_asset_ids(self, engine):
        engine._initial_load()
        ids = engine.get_all_asset_ids()
        assert {'bg_classroom', 'bgm_theme', 'e'} <= ids

    def test_empty_registry_queries(self):
        eng = AssetEngine(asset_dir='x', project_root='y')
        assert eng.get_registry() is None
        assert eng.get_references() == {}
        assert eng.get_all_asset_ids() == set()
        assert eng.find_broken_references() == []
        assert eng.find_unused_assets() == []
        assert eng.find_undefined_characters() == []
        assert eng.find_unused_characters() == []

    def test_undefined_character_reported(self, engine):
        (engine.script_files[0]).write_text(
            'label a:\n    ghost_var "hi"\n', encoding='utf-8')
        engine._initial_load()
        undefined = engine.find_undefined_characters()
        assert [u['variable'] for u in undefined] == ['ghost_var']

    def test_unused_character_reported(self, engine):
        engine.script_files[0].write_text(
            'define lonely = Character("Lonely")\n'
            'label a:\n    return\n', encoding='utf-8')
        engine._initial_load()
        assert [u['variable'] for u in engine.find_unused_characters()] == ['lonely']

    def test_broken_reference_found(self, engine):
        engine.script_files[0].write_text(
            'label a:\n    show nonexistent_sprite\n', encoding='utf-8')
        engine._initial_load()
        broken = engine.find_broken_references()
        assert [b['asset_id'] for b in broken] == ['nonexistent_sprite']

    def test_unused_asset_found(self, engine):
        engine._initial_load()
        unused = {(u['category'], u['asset_id'])
                  for u in engine.find_unused_assets()}
        assert ('sounds', 'sfx_click') in unused


# ------------------------------------------------------------------
#  重命名应用与确认闸门
# ------------------------------------------------------------------

class TestRenameApplication:
    def test_patch_replaces_word_boundary_only(self, write_rpy):
        fp = write_rpy('show hero\nshow hero_happy\n$ hero = 1\n')
        result = _apply_rename_patch('hero', 'protagonist', [fp],
                                     backup=False)
        text = fp.read_text(encoding='utf-8')
        assert 'protagonist' in text
        assert 'hero_happy' in text          # 不应被部分替换
        assert result['modified'] == [str(fp)]

    def test_dry_run_does_not_write(self, write_rpy):
        """预览模式只能告诉我们哪些文件会变，不落盘。"""
        from asset_extensions import generate_rename_patch
        fp = write_rpy('show hero\n')
        before = fp.read_text(encoding='utf-8')
        result = generate_rename_patch('hero', 'x', [fp], dry_run=True,
                                       backup=False)
        assert fp.read_text(encoding='utf-8') == before
        assert result['modified'] == []
        assert result['matched_files']

    def test_patch_backup_created(self, write_rpy):
        fp = write_rpy('show hero\n')
        _apply_rename_patch('hero', 'x', [fp], backup=True)
        assert fp.with_suffix(fp.suffix + '.bak').exists()

    def test_patch_missing_file_skipped(self, tmp_path):
        result = _apply_rename_patch('a', 'b', [tmp_path / 'nope.rpy'])
        assert result['modified'] == []

    def test_rename_rejected_rolls_back(self, asset_env):
        """用户在闸门里拒绝重命名 → registry 必须恢复旧值。"""
        gate = ConfirmationGate(ask_callback=lambda r: False,
                                rename_threshold=0)
        eng = AssetEngine(asset_dir=asset_env['asset_dir'],
                          project_root=asset_env['project_root'],
                          script_files=[asset_env['game_dir'] / 'script.rpy'],
                          confirmation_gate=gate,
                          watch_source_dirs=False)
        eng._initial_load()

        from asset_engine import _Change
        before = {'Path': 'p.png'}
        ch = _Change('renamed', 'images', 'old_id', before,
                     {'Path': 'p.png'}, new_id='new_id')
        eng.registry.images.pop('old_id', None)
        eng.registry.images['new_id'] = {'Path': 'p.png'}
        eng._prev_state.setdefault('images', {})['new_id'] = {'Path': 'p.png'}

        eng._handle_rename(ch, ['script.rpy:1'])

        assert 'new_id' not in eng.registry.images
        assert eng.registry.images['old_id'] == before

    def test_delete_rejected_rolls_back(self, asset_env):
        gate = ConfirmationGate(ask_callback=lambda r: False)
        eng = AssetEngine(asset_dir=asset_env['asset_dir'],
                          project_root=asset_env['project_root'],
                          script_files=[asset_env['game_dir'] / 'script.rpy'],
                          confirmation_gate=gate,
                          watch_source_dirs=False)
        eng._initial_load()

        from asset_engine import _Change
        before = {'Path': 'p.png'}
        ch = _Change('removed', 'images', 'gone_id', before, {})
        eng._handle_removed(ch, ['script.rpy:1'])
        assert eng.registry.images['gone_id'] == before
