"""
模板资产路径不许被当成"文件不存在"（2026-10-18，build P2）
==========================================================

真机 `sr_general` 的资源表里有 5 行**模板路径**：

    cc_preview/lan/{image}.png
    cc_preview/ling/{image}.png
    …

文件名是运行时拼出来的，所以它们**永远**解析不到真实文件。可所有"文件在
不在"的判据都拿 `Path` 去 `exists()`，同一个事实被判成四种错：

  · 构建报告 **E302「源文件不存在」**（用户看到的那条）；
  · 资源体检把行列进"表里有、磁盘上没有"；
  · 资源同步的"自动删"**把行删掉**；
  · 行被标成 `unusable`（选资产时看不见）。

判据只放一处（`asset_registry.is_template_path`），四处共用。占位行
（`is_placeholder_row`）早就豁免了，模板行是同一枚硬币的另一面。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.assets.asset_registry import (AssetRegistry,      # noqa: E402
                                              _mark_unusable,
                                              _missing_paths,
                                              is_missing_allowed,
                                              is_template_path)
from storygraph.pipeline.validate import validate_level3          # noqa: E402
from storygraph.story.beat_model import Beat                      # noqa: E402


class TestThePredicate:
    @pytest.mark.parametrize('path,want', [
        ('cc_preview/lan/{image}.png', True),
        ('images/{name}/x.png', True),
        ('images/bg/bg1.1.png', False),
        ('images/{2,3}/x.png', False),      # 正则量词，不是模板
        ('', False),
        (None, False),
    ])
    def test_template_paths(self, path, want):
        assert is_template_path(path) is want

    def test_placeholder_row_and_template_row_are_both_allowed(self):
        assert is_missing_allowed({'Path': 'a/{image}.png'})
        assert is_missing_allowed({'Path': 'a.png', 'Notes': '占位（还没出图）'})
        assert not is_missing_allowed({'Path': 'a.png'})


def _registry(*paths):
    reg = AssetRegistry()
    reg.images = {f'img{i}': {'ID': f'img{i}', 'Path': p}
                  for i, p in enumerate(paths)}
    return reg


class TestValidateDoesNotReportThem:
    def test_template_path_is_not_e302(self, tmp_path):
        reg = _registry('cc_preview/lan/{image}.png',
                        'cc_preview/ling/{image}.png')
        issues = validate_level3([], reg, project_root=tmp_path)
        assert not [i for i in issues if i.code == 'E302'], \
            [i.message for i in issues if i.code == 'E302']

    def test_a_real_missing_file_is_still_e302(self, tmp_path):
        """豁免不能开太大：真丢了的文件必须照旧报。"""
        reg = _registry('cc_preview/lan/{image}.png', 'images/nope.png')
        issues = validate_level3([], reg, project_root=tmp_path)
        e302 = [i for i in issues if i.code == 'E302']
        assert e302 and 'images/nope.png' in e302[0].message, e302

    def test_an_existing_file_is_not_reported(self, tmp_path):
        (tmp_path / 'images').mkdir()
        (tmp_path / 'images' / 'ok.png').write_bytes(b'x')
        reg = _registry('images/ok.png')
        issues = validate_level3([], reg, project_root=tmp_path)
        assert not [i for i in issues if i.code == 'E302']


class TestSyncDoesNotDeleteThem:
    def test_missing_paths_skips_templates(self, tmp_path):
        rows = {'a': {'ID': 'a', 'Path': 'cc_preview/lan/{image}.png'},
                'b': {'ID': 'b', 'Path': 'images/gone.png'}}
        got = _missing_paths(rows, set(), 'images', None, tmp_path)
        assert got == ['images/gone.png'], got

    def test_rows_are_not_marked_unusable(self, tmp_path):
        reg = _registry('cc_preview/lan/{image}.png')
        _mark_unusable(reg, tmp_path)
        assert not (reg.unusable.get('images') or {}), reg.unusable
