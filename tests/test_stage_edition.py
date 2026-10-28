"""
tests/test_stage_edition.py
===========================
**演出专用版**的"随包礼物"判据（`storygraph.app.stage_edition`）：

  · 真实项目的 `storygraph.rules.json`（show 的 角色/服装/表情 三列）与
    `layout.json`（43 章摆好的版式）**真的在仓库里**、格式对、内容不是占位；
  · `apply_presets` 只补缺：项目已有就一个字节不动、补完是原子写、
    dry-run 零写入、没有预设时明确返回 `no-preset`（不补空文件）；
  · `find_received_project` 认得"项目放旁边 / 解压进项目里 / 只套一层"，
    有歧义时**不猜**（返回 None 让用户自己选）；
  · 自动套用/自动打开真的接在 `open_project_path` / `main_qt` 上
    （静态扫源码：接线掉了一条，这里先红）。

为什么这批判据要存在
--------------------
演出专用版唯一多出来的行为是"**自动写用户项目**"（补两个配置文件）和
"**自动换项目**"。这两件事在标准版里都是禁用行为，一旦哪天条件写错
（比如 `stage_edition()` 变成恒真），标准版用户的项目就会莫名其妙多出文件。
所以这里把"只在演出专用版里、只补缺、绝不覆盖"钉死。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.app import engine_preview as EP      # noqa: E402
from storygraph.app import stage_edition as SE      # noqa: E402


# ---------------------------------------------------------------------------
#  仓库里的真实预设
# ---------------------------------------------------------------------------

class TestRealPresets:

    def test_preset_dir_points_at_repo_presets_in_source_checkout(self):
        d = SE.preset_dir()
        assert d is not None
        assert d == ROOT / 'storygraph' / 'presets'

    def test_rules_preset_has_the_show_columns(self):
        """真实 rules：`show` 的 角色 / 服装 / 表情 三列候选（用户配的那份）。"""
        path = SE.preset_file(SE.RULES_NAME)
        assert path is not None, '仓库里没有 storygraph/presets/storygraph.rules.json'
        data = json.loads(path.read_text(encoding='utf-8'))
        statements = data.get('statements') or []
        assert statements, 'rules 预设里一条语句都没有'
        show = next((s for s in statements if s.get('name') == 'show'), None)
        assert show is not None, '真实预设里没有 show 的声明'
        names = [c.get('name') for c in show.get('columns') or []]
        assert '角色' in names
        for extra in ('服装', '表情'):
            assert extra in names, f'show 少了「{extra}」列：{names}'
        # 候选不是空的（这份是"真项目导出来的"，不是占位模板）
        assert len(show['columns'][0].get('choices') or []) > 5

    def test_layout_preset_is_the_real_chapter_map(self):
        path = SE.preset_file(SE.LAYOUT_NAME)
        assert path is not None, '仓库里没有 storygraph/presets/layout.json'
        data = json.loads(path.read_text(encoding='utf-8'))
        nodes = data.get('nodes') or {}
        assert len(nodes) >= 40, f'版式只有 {len(nodes)} 个节点，不像真实项目'
        # 键是 `game/chapter/xxx.rpy::label` —— 跨机器稳定，不靠 node id
        for key in list(nodes)[:5]:
            assert '::' in str(key), f'版式键格式不对：{key}'
        for key, node in list(nodes.items())[:10]:
            assert isinstance(node.get('x'), (int, float)), key
            assert isinstance(node.get('y'), (int, float)), key

    def test_presets_are_readable_json(self):
        for name in (SE.RULES_NAME, SE.LAYOUT_NAME):
            path = SE.preset_file(name)
            assert path is not None
            json.loads(path.read_text(encoding='utf-8'))    # 坏 JSON 直接抛


# ---------------------------------------------------------------------------
#  apply_presets
# ---------------------------------------------------------------------------

@pytest.fixture
def bare_project(tmp_path) -> Path:
    root = tmp_path / 'Game'
    (root / 'game').mkdir(parents=True)
    (root / 'game' / 'ch1.rpy').write_text('label ch1:\n    return\n',
                                           encoding='utf-8')
    return root


class TestApplyPresets:

    def test_copies_both_missing_files(self, bare_project):
        res = SE.apply_presets(bare_project)
        assert res['rules'] == 'copied'
        assert res['layout'] == 'copied'
        assert len(res['errors']) == 0
        rules = bare_project / 'storygraph.rules.json'
        layout = bare_project / '.storygraph' / 'layout.json'
        assert rules.read_bytes() == SE.preset_file(SE.RULES_NAME).read_bytes()
        assert layout.read_bytes() == SE.preset_file(SE.LAYOUT_NAME).read_bytes()

    def test_second_apply_changes_nothing(self, bare_project):
        SE.apply_presets(bare_project)
        rules = bare_project / 'storygraph.rules.json'
        layout = bare_project / '.storygraph' / 'layout.json'
        before = (rules.stat().st_mtime_ns, layout.stat().st_mtime_ns,
                  rules.read_bytes(), layout.read_bytes())
        res = SE.apply_presets(bare_project)
        assert res['rules'] == 'exists'
        assert res['layout'] == 'exists'
        after = (rules.stat().st_mtime_ns, layout.stat().st_mtime_ns,
                 rules.read_bytes(), layout.read_bytes())
        assert before == after

    def test_existing_files_are_never_overwritten(self, bare_project):
        custom = bare_project / 'storygraph.rules.json'
        custom.write_text('{"statements": []}', encoding='utf-8')
        old_layout = bare_project / 'storygraph_layout.json'   # 旧路径也算已有
        old_layout.write_text('{"nodes": {}}', encoding='utf-8')
        res = SE.apply_presets(bare_project)
        assert res['rules'] == 'exists'
        assert res['layout'] == 'exists'
        assert custom.read_text(encoding='utf-8') == '{"statements": []}'
        assert not (bare_project / '.storygraph').exists(), \
            '布局已有（旧路径）时不该再写一份新的'

    def test_rules_in_game_dir_counts_as_existing(self, bare_project):
        (bare_project / 'game' / 'storygraph.rules.json').write_text(
            '{"statements": []}', encoding='utf-8')
        res = SE.apply_presets(bare_project)
        assert res['rules'] == 'exists'
        assert not (bare_project / 'storygraph.rules.json').exists()

    def test_dry_run_writes_nothing(self, bare_project):
        res = SE.apply_presets(bare_project, dry_run=True)
        assert res['rules'] == 'copied' and res['layout'] == 'copied'
        assert not (bare_project / 'storygraph.rules.json').exists()
        assert not (bare_project / '.storygraph').exists()

    def test_missing_preset_dir_is_reported_not_guessed(
            self, bare_project, monkeypatch):
        monkeypatch.setattr(SE, 'preset_dir', lambda: None)
        res = SE.apply_presets(bare_project)
        assert res['rules'] == 'no-preset'
        assert res['layout'] == 'no-preset'
        assert not (bare_project / 'storygraph.rules.json').exists()

    def test_missing_project_is_reported(self, tmp_path):
        res = SE.apply_presets(tmp_path / 'nope')
        assert res['errors']


# ---------------------------------------------------------------------------
#  find_received_project
# ---------------------------------------------------------------------------

def _pkg_with_project(pkg: Path) -> Path:
    pkg.mkdir(parents=True, exist_ok=True)
    game = pkg / 'game'
    game.mkdir()
    (game / 'ch1.rpy').write_text('label ch1:\n    return\n', encoding='utf-8')
    return pkg


class TestFindReceivedProject:

    def test_project_folder_named_项目(self, tmp_path, monkeypatch):
        pkg = tmp_path / 'pkg'
        _pkg_with_project(pkg / '项目')
        monkeypatch.setattr(SE, 'app_dir', lambda: pkg)
        assert SE.find_received_project() == pkg / '项目'

    def test_package_inside_project_root(self, tmp_path, monkeypatch):
        """解压进项目文件夹：包目录的上一级就是项目根。"""
        project = _pkg_with_project(tmp_path / 'Game')
        pkg = project / 'StoryGraph-Stage'
        pkg.mkdir()
        monkeypatch.setattr(SE, 'app_dir', lambda: pkg)
        assert SE.find_received_project() == project.resolve()

    def test_extra_nested_folder_is_unwrapped(self, tmp_path, monkeypatch):
        """`项目/项目/game/` 这种"多套了一层"很常见。"""
        pkg = tmp_path / 'pkg'
        (_pkg_with_project(pkg / '项目' / 'Game'))
        monkeypatch.setattr(SE, 'app_dir', lambda: pkg)
        assert SE.find_received_project() == pkg / '项目' / 'Game'

    def test_ambiguous_folders_are_not_guessed(self, tmp_path, monkeypatch):
        pkg = tmp_path / 'pkg'
        _pkg_with_project(pkg / '项目' / 'A')
        _pkg_with_project(pkg / '项目' / 'B')
        monkeypatch.setattr(SE, 'app_dir', lambda: pkg)
        assert SE.find_received_project() is None

    def test_no_project_returns_none(self, tmp_path, monkeypatch):
        pkg = tmp_path / 'pkg'
        pkg.mkdir()
        monkeypatch.setattr(SE, 'app_dir', lambda: pkg)
        assert SE.find_received_project() is None

    def test_unique_sibling_project_found_in_frozen_build(
            self, tmp_path, monkeypatch):
        """打包版：旁边唯一一个带 game/ 的目录就是随包项目。"""
        pkg = tmp_path / 'pkg'
        pkg.mkdir()
        _pkg_with_project(pkg / 'sr_general')
        monkeypatch.setattr(SE, 'app_dir', lambda: pkg)
        monkeypatch.setattr(sys, 'frozen', True, raising=False)
        assert SE.find_received_project() == pkg / 'sr_general'

    def test_package_demo_is_not_a_received_project(
            self, tmp_path, monkeypatch):
        """
        包里自带的 `demo/`（示例工程）**不是**收到的项目。

        不排掉它的话，用户没放 `项目/` 时工具会自动打开 demo、还会把预设
        补进 demo 里 —— 第一次用的人会以为"我的项目怎么变成这样了"。
        """
        pkg = tmp_path / 'pkg'
        pkg.mkdir()
        _pkg_with_project(pkg / 'demo')
        _pkg_with_project(pkg / 'renpy_sdk' / 'tmp' / 'whatever')  # 历史垃圾
        monkeypatch.setattr(SE, 'app_dir', lambda: pkg)
        monkeypatch.setattr(sys, 'frozen', True, raising=False)
        assert SE.find_received_project() is None

    def test_real_project_next_to_demo_still_wins(
            self, tmp_path, monkeypatch):
        pkg = tmp_path / 'pkg'
        pkg.mkdir()
        _pkg_with_project(pkg / 'demo')
        _pkg_with_project(pkg / 'sr_general')
        monkeypatch.setattr(SE, 'app_dir', lambda: pkg)
        monkeypatch.setattr(sys, 'frozen', True, raising=False)
        assert SE.find_received_project() == pkg / 'sr_general'

    def test_development_checkout_does_not_mistake_demo_for_project(
            self, tmp_path, monkeypatch):
        """源码运行时仓库根下有 `demo/game/`，不能被当成"收到的项目"。"""
        repo = tmp_path / 'repo'
        repo.mkdir()
        _pkg_with_project(repo / 'demo')
        monkeypatch.setattr(SE, 'app_dir', lambda: repo)
        assert SE.find_received_project() is None


# ---------------------------------------------------------------------------
#  接线（静态扫源码）
# ---------------------------------------------------------------------------

class TestWiring:

    def test_open_project_applies_presets_only_in_stage_edition(self):
        src = (ROOT / 'storygraph' / 'ui_qt' / 'window_graph_ops.py').read_text(
            encoding='utf-8')
        assert 'apply_presets' in src
        assert 'stage_edition()' in src
        # 条件必须在调 apply_presets 之前（标准版不许写项目）。
        # 用调用点而不是单词位置：上面注释里先出现了 "apply_presets"。
        assert src.index('_EP.stage_edition()') < \
            src.index('apply_presets(root)')

    def test_main_qt_auto_opens_received_project(self):
        src = (ROOT / 'storygraph' / 'main_qt.py').read_text(encoding='utf-8')
        assert 'find_received_project' in src
        assert 'apply_presets' in src
        assert 'stage_edition' in src

    def test_main_window_has_engine_actions(self):
        src = (ROOT / 'storygraph' / 'ui_qt' / 'main_window.py').read_text(
            encoding='utf-8')
        assert 'engine_preview_chapter' in src
        assert 'engine_preview_from_beat' in src
        assert 'engine_preview_selected' in src
        assert 'shutdown_engine_preview' in src

    def test_stage_edition_requires_marker_or_env(self, tmp_path, monkeypatch):
        monkeypatch.delenv('STORYGRAPH_STAGE_EDITION', raising=False)
        monkeypatch.setattr(EP, 'app_dir', lambda: tmp_path)
        assert EP.stage_edition() is False
