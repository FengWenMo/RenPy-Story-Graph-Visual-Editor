"""
tests/test_scale_scenario.py
============================
模拟真实规模的项目（几百张图 + 一个章节），验证一次"重新扫描"的
**实际副作用**：弹了几次窗、报了多少误报、诊断刷了几遍。

这些数字就是用户在 UI 上感受到的东西，单测覆盖不到。
"""

import pytest

from storygraph.assets.asset_registry import scan_assets, write_asset_excel
from storygraph.assets.asset_engine import AssetEngine
from storygraph.assets.asset_events import AssetEventBus, AssetEventType, ConfirmationGate


BG_COUNT = 200
USED_COUNT = 50


@pytest.fixture
def big_project(tmp_path):
    """
    200 张背景图 + 一个引用其中 50 张的章节。

    脚本里写的是 Ren'Py 的 `show bg 001`（tag + attribute），
    资产表里存的是文件名 `bg_001`——这正是误报的根源。
    """
    project = tmp_path / 'proj'
    game = project / 'game'
    bg_dir = game / 'images' / 'bg'
    audio = game / 'audio'
    asset_dir = tmp_path / 'asset_tables'

    bg_dir.mkdir(parents=True)
    audio.mkdir(parents=True)
    asset_dir.mkdir(parents=True)

    for i in range(BG_COUNT):
        (bg_dir / f'bg_{i:03d}.png').write_bytes(b'1')
    (audio / 'bgm_theme.ogg').write_bytes(b'1')

    lines = ['define e = Character("Eileen")', '', 'label ch01:']
    for i in range(USED_COUNT):
        lines.append(f'    show bg {i:03d}')
        lines.append(f'    e "第 {i} 句"')
    lines.append('    play music bgm_theme')
    lines.append('    return')
    (game / 'script.rpy').write_text('\n'.join(lines) + '\n',
                                     encoding='utf-8')

    write_asset_excel(scan_assets(project, include_ui=False), asset_dir)
    return {
        'project_root': project,
        'asset_dir': asset_dir,
        'script': game / 'script.rpy',
    }


@pytest.fixture
def engine_and_events(big_project):
    asked = []
    bus = AssetEventBus()
    events = []
    bus.subscribe(None, events.append)

    gate = ConfirmationGate(
        ask_callback=lambda r: asked.append(r) or True,
        rename_threshold=5,
    )
    eng = AssetEngine(
        asset_dir=big_project['asset_dir'],
        project_root=big_project['project_root'],
        script_files=[big_project['script']],
        event_bus=bus,
        confirmation_gate=gate,
        watch_source_dirs=False,
        poll_interval=0.05,
    )
    return eng, events, asked


class TestScanIsQuiet:
    def test_initial_scan_never_pops_a_dialog(self, engine_and_events):
        """扫几百张图，一个确认框都不该弹。"""
        eng, _events, asked = engine_and_events
        eng._initial_load()
        assert asked == []

    def test_rescan_never_pops_a_dialog(self, engine_and_events):
        """反复重新扫描也不弹窗——用户就是在这一步被淹的。"""
        eng, _events, asked = engine_and_events
        eng._initial_load()
        for _ in range(3):
            eng._mark_dirty(eng.TASK_TABLE)
            eng._mark_dirty(eng.TASK_SCRIPT)
            eng.poll()
        assert asked == []

    def test_referenced_images_are_recognized(self, engine_and_events):
        """
        `show bg 001` 必须认出资产表里的 `bg_001`，
        否则整库图片都会被判成"未使用"。
        """
        eng, _events, _asked = engine_and_events
        eng._initial_load()
        refs = eng.get_references()
        for i in range(USED_COUNT):
            assert f'bg_{i:03d}' in refs, f'bg_{i:03d} 未被识别为已引用'

    def test_unused_count_is_realistic(self, engine_and_events):
        """200 张里用了 50 张，未使用应约 150，而不是接近 200。"""
        eng, _events, _asked = engine_and_events
        eng._initial_load()
        unused = eng.find_unused_assets()
        unused_images = [u for u in unused if u['category'] == 'images']
        assert len(unused_images) <= BG_COUNT - USED_COUNT + 2

    def test_no_false_broken_references(self, engine_and_events):
        eng, _events, _asked = engine_and_events
        eng._initial_load()
        assert eng.find_broken_references() == []

    def test_builtin_transition_not_reported(self, big_project):
        """`with dissolve` 是引擎自带的，不能算断裂引用。"""
        script = big_project['script']
        script.write_text(
            'label a:\n    scene bg 000 with dissolve\n    return\n',
            encoding='utf-8')
        eng = AssetEngine(asset_dir=big_project['asset_dir'],
                          project_root=big_project['project_root'],
                          script_files=[script],
                          watch_source_dirs=False)
        eng._initial_load()
        assert eng.find_broken_references() == []


class TestDiagnosticsAreNotSpammy:
    def test_diagnostics_emitted_once_per_change(self, engine_and_events):
        """同样的诊断不能每次 poll 都刷一遍。"""
        eng, events, _asked = engine_and_events
        eng._initial_load()
        events.clear()

        for _ in range(5):
            eng._mark_dirty(eng.TASK_SCRIPT)
            eng._mark_dirty(eng.TASK_TABLE)
            eng.poll()

        unused_events = [e for e in events
                         if e.event_type == AssetEventType.UNUSED_ASSETS_FOUND]
        assert len(unused_events) <= 1

    def test_identical_rescan_emits_nothing_new(self, engine_and_events):
        """内容没变，就不该再打扰用户。"""
        eng, events, _asked = engine_and_events
        eng._initial_load()
        eng._mark_dirty(eng.TASK_SCRIPT)
        eng.poll()
        events.clear()

        eng._mark_dirty(eng.TASK_SCRIPT)
        eng.poll()
        diagnostic_types = (AssetEventType.UNUSED_ASSETS_FOUND,
                            AssetEventType.BROKEN_REFERENCES_FOUND)
        assert not [e for e in events if e.event_type in diagnostic_types]


class TestScriptTableAtScale:
    def test_chapter_roundtrip(self, big_project, tmp_path):
        """整章摊成表格再生成脚本，句子数不能变。"""
        from storygraph.story.script_table import (
            open_rpy_as_table, import_beats_from_excel,
            render_beats_to_rpy,
        )
        table = open_rpy_as_table(big_project['script'],
                                  tmp_path / 'ch01.xlsx')
        beats = import_beats_from_excel(table)
        # 50 句台词 + 末尾那拍"只有音乐、没有台词"的指令（不能丢）
        assert len(beats) == USED_COUNT + 1

        text = render_beats_to_rpy(beats)
        for i in range(USED_COUNT):
            # 作者写法 `show bg 001` 与下划线形态 bg_001 在 Ren'Py 里等价；
            # 本用例没给资源表，按约定退回下划线形态，所以两种都算"存活"。
            assert f'show bg {i:03d}' in text or \
                f'show bg_{i:03d}' in text
        assert 'play music bgm_theme' in text

    def test_generated_script_is_reparsable(self, big_project, tmp_path):
        from storygraph.parsing.renpy_parser import RenPyParser
        from storygraph.story.script_table import (
            open_rpy_as_table, import_beats_from_excel, write_beats_to_rpy,
        )
        table = open_rpy_as_table(big_project['script'],
                                  tmp_path / 'ch01.xlsx')
        out = write_beats_to_rpy(import_beats_from_excel(table),
                                 tmp_path / 'ch01.generated.rpy')
        parser = RenPyParser()
        parser.parse(out)
        assert not parser.errors, parser.errors
