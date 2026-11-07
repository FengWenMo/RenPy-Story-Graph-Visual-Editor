"""
资源表的两个"工具的锅"（2026-11-04）
====================================

用户（看到体检 232 条问题）："按理说这绝对是工具的锅！！！为什么会导致
这种情况？"

查下来 232 条**全是同一件事**：`Path` 落在 `game/` 之外 ——

| 桶 | 条数 | 是什么 |
|---|---|---|
| `模拟操作/lint_proj/game/images/…` | 231 | 一个**测试副本目录**，被当成资源扫进表 |
| `about_collection_smoke.png` | 1 | 项目根下的散图 |
| 重复 ID / 重复 Path / Path 不存在 / 表里有盘上没 | **0** | —— |

根因：`ensure_asset_tables()` / `_sync_scan_assets()` 调
`scan_assets(root)` 时传的是**项目根**，而资源表这句话的语义是
"**这个项目的 `game/` 里有什么**"。于是 `game/` 之外的任何目录
（测试副本、备份、根目录散图）都会被写进表。

**这确实是工具的锅**：用户从没要求扫 `game/` 之外的东西。

这个文件钉四条：

  1. `_drop_outside_game` —— 建表/对账时丢掉 `game/` 外的行；
  2. `scan_assets` **本身不过滤**（它也被"扫父目录"那种用法调，
     `test_scan_golden` 就是；在那里过滤会把别的项目一起滤掉）；
  3. 体检把"映射里 `char` 填成显示名"数出来（真机 `{"char": "青"}`）；
  4. 合法 tag 不许误报。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PNG = b'\x89PNG\r\n\x1a\n'


class TestDropOutsideGame:
    def _assets(self):
        return {
            'images': [
                {'ID': 'a', 'Path': 'game/images/bg/x.png'},
                {'ID': 'b', 'Path': '模拟操作/lint_proj/game/images/x.png'},
                {'ID': 'c', 'Path': 'about_smoke.png'},
                {'ID': 'd', 'Path': 'game\\images\\bg\\y.png'},   # 反斜杠也要认
            ],
            'audio': {'music': [{'ID': 'm', 'Path': 'game/audio/m.ogg'},
                                {'ID': 'm2', 'Path': '备份/m.ogg'}],
                      'sound': []},
            'voices': [{'ID': 'v', 'Path': 'outside/v.ogg'}],
        }

    def test_drops_rows_outside_game(self, tmp_path):
        from storygraph.assets.asset_registry import _drop_outside_game
        (tmp_path / 'game').mkdir()
        kept, dropped = _drop_outside_game(self._assets(), tmp_path)
        ids = [r['ID'] for r in kept['images']]
        assert ids == ['a', 'd'], ids
        assert dropped == 4                      # b c + m2 + v
        assert [r['ID'] for r in kept['audio']['music']] == ['m']
        assert kept['voices'] == []

    def test_no_game_dir_keeps_everything(self, tmp_path):
        """没有 `game/` 的项目（少见但合法）原样返回 —— 不猜。"""
        from storygraph.assets.asset_registry import _drop_outside_game
        got, dropped = _drop_outside_game(self._assets(), tmp_path)
        assert dropped == 0
        assert len(got['images']) == 4

    def test_keeps_assets_untouched_when_all_inside(self, tmp_path):
        from storygraph.assets.asset_registry import _drop_outside_game
        (tmp_path / 'game').mkdir()
        src = {'images': [{'ID': 'a', 'Path': 'game/images/a.png'}],
               'audio': {'music': [], 'sound': []}, 'voices': []}
        got, dropped = _drop_outside_game(src, tmp_path)
        assert dropped == 0
        assert got['images'] == src['images']


class TestScanAssetsItselfDoesNotFilter:
    """过滤只能在**建表侧**：`scan_assets` 也被"扫父目录"那种用法调。"""

    def test_scanning_a_parent_folder_still_sees_both_projects(self, tmp_path):
        from storygraph.assets.asset_registry_scan import scan_assets
        for name in ('proj_a', 'proj_b'):
            d = tmp_path / name / 'game' / 'images'
            d.mkdir(parents=True)
            (d / 'x.png').write_bytes(PNG)
        got = scan_assets(tmp_path)              # 父目录当扫描根
        paths = sorted(r['Path'] for r in got['images'])
        assert paths == ['proj_a/game/images/x.png', 'proj_b/game/images/x.png']


class TestBuildTableStaysInsideGame:
    def test_ensure_asset_tables_skips_outside(self, tmp_path):
        from storygraph.assets.asset_registry import ensure_asset_tables
        from storygraph.assets.registry_load import load_asset_registry
        d = tmp_path / 'game' / 'images' / 'characters' / 'qing'
        d.mkdir(parents=True)
        (d / 'normal.png').write_bytes(PNG)
        (tmp_path / '模拟操作' / 'game' / 'images').mkdir(parents=True)
        (tmp_path / '模拟操作' / 'game' / 'images' / 'x.png').write_bytes(PNG)
        (tmp_path / 'about_smoke.png').write_bytes(PNG)

        ensure_asset_tables(tmp_path, with_scripts=False)
        reg = load_asset_registry(tmp_path)
        paths = [str(r.get('Path')) for r in (reg.images or {}).values()]
        assert paths, '一张图都没扫到'
        assert all(p.startswith('game/') for p in paths), paths


class TestHealthReportsDisplayNameAsTag:
    """映射里 `char` 填显示名 —— 以前只有卡片提示会提，体检看不见。"""

    def _session(self, tmp_path, mapping):
        import json
        (tmp_path / 'game').mkdir(exist_ok=True)
        if mapping is not None:
            (tmp_path / 'storygraph.mapping.json').write_text(
                json.dumps(mapping, ensure_ascii=False), encoding='utf-8')
        from storygraph.app.editor import EditorSession
        from storygraph.assets import asset_mapping
        asset_mapping._MAPPING_CACHE.clear() \
            if hasattr(asset_mapping, '_MAPPING_CACHE') else None
        s = EditorSession(project_root=str(tmp_path))
        return s

    def _rows(self, session, reg):
        return session._bad_mapping_rows(reg)

    def test_display_name_is_reported(self, tmp_path):
        s = self._session(tmp_path, {'images': {'qing': {'kind': 'sprite',
                                                         'char': '青',
                                                         'attr': 'base'}}})

        class _Reg:
            characters = {}
        rows = self._rows(s, _Reg())
        assert len(rows) == 1
        assert rows[0]['char'] == '青'
        assert '不是合法' in rows[0]['why'] or '找不到图像' in rows[0]['why']

    def test_legal_tag_is_not_reported(self, tmp_path):
        s = self._session(tmp_path, {'images': {'qing': {'kind': 'sprite',
                                                         'char': 'qing',
                                                         'attr': 'base'}}})

        class _Reg:
            characters = {}
        assert self._rows(s, _Reg()) == []

    def test_tag_declared_by_characters_table_is_not_reported(self, tmp_path):
        s = self._session(tmp_path, {'images': {'x': {'char': 'ling'}}})

        class _Reg:
            characters = {'l': {'ID': 'l', 'ImageTag': 'ling'}}
        assert self._rows(s, _Reg()) == []

    def test_no_mapping_file_is_not_an_error(self, tmp_path):
        s = self._session(tmp_path, None)

        class _Reg:
            characters = {}
        assert self._rows(s, _Reg()) == []
