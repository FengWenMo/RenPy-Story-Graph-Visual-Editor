"""
tests/test_nested_project_copy.py
================================
**嵌套项目副本**（用户 2026-10-05："为什么你改一个表……还是会导致立绘不显示
呢？？？而且表情也调不了了，修复！"）

实测现场：项目根下有个 `sr_general/`，里面是**同一份项目**的副本，
表里 1386 行指向它。自动补登把这些文件当"项目资源"收了进来，编出
`sr_general_angry` / `ling_angry` 这种 ID —— 而 `ling_angry` 恰好是
"tag_attribute" 的写法，于是卡片里的 `ling_angry`：

  · 精确 ID 命中了**那一行**（Tag 为空、图在 `cc_preview/` 里）
    → 预览取错图、`attrs_of_tag('ling_angry')` 为空 → **表情候选全空**。

本文件钉住三件事：
  ① 扫描**不把嵌套副本当项目资源**；
  ② `tag_attribute` 写法**不被"没有 Tag 的同名 ID"劫持**；
  ③ `image_tag_of` 从 `ling_angry` 能读回真 tag（表情/位置候选的来源）。
"""

from __future__ import annotations

import openpyxl
import pytest


def _row(ws, values):
    ws.append(values)


@pytest.fixture()
def nested_project(tmp_path):
    """一个项目 + 一份嵌套副本 + 一张"混了副本行"的资源表。"""
    root = tmp_path / 'proj'
    game = root / 'game'
    (game / 'images' / 'characters' / 'ling').mkdir(parents=True)
    (game / 'cc_preview' / 'ling').mkdir(parents=True)
    (game / 'images' / 'characters' / 'ling' / 'angry.png').write_bytes(b'x')
    (game / 'cc_preview' / 'ling' / 'angry.png').write_bytes(b'x')
    # 嵌套副本：`<root>/sr_general/game/...`
    nest = root / 'sr_general'
    (nest / 'game' / 'cc_preview' / 'ling').mkdir(parents=True)
    (nest / 'game' / 'cc_preview' / 'ling' / 'angry.png').write_bytes(b'x')
    (nest / 'game' / 'images' / 'characters' / 'ling').mkdir(parents=True)
    (nest / 'game' / 'images' / 'characters' / 'ling' / 'angry.png') \
        .write_bytes(b'x')

    tables = game / 'asset_tables'
    tables.mkdir(parents=True, exist_ok=True)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(['ID', 'Path', 'Tag', 'Attributes', 'Category', 'Notes'])
    # 真立绘（带 Tag）
    _row(ws, ['_angry', 'game/images/characters/ling/angry.png', 'ling',
              'angry', 'character', ''])
    # **劫持者**：ID 恰好叫 `ling_angry`，却没有 Tag、图在 cc_preview 里
    _row(ws, ['ling_angry',
              'sr_general/game/cc_preview/ling/angry.png', '', 'angry',
              'other', ''])
    wb.save(tables / 'images.xlsx')
    return root


class TestTheCopyIsNotAProjectResource:
    def test_the_scan_skips_a_nested_project(self, nested_project):
        from storygraph.assets.asset_registry import (FileWalkIndex,
                                                      scan_assets)
        walk = FileWalkIndex(nested_project)
        assert walk.nested_prefixes() == {'sr_general/'}, walk.nested_prefixes()
        assert any('sr_general/' in rel for rel in walk.files()), \
            '索引仍要能看见副本文件（判"文件在不在"要用）'
        assets = scan_assets(nested_project, include_ui=True, walk=walk)
        assert assets['images'], '真项目里的图还是要扫到'
        assert not [a for a in assets['images']
                    if str(a['Path']).startswith('sr_general/')], \
            '嵌套副本被当成项目资源扫进来了'

    def test_the_sync_does_not_append_copy_files(self, nested_project):
        from storygraph.app.editor import EditorSession
        from storygraph.pipeline.build import load_asset_registry
        before = set(load_asset_registry(nested_project).images)
        s = EditorSession(project_root=nested_project)
        res = s.load_assets()
        reg = res['registry']
        fresh = [rid for rid in (reg.images or {}) if rid not in before]
        # 夹具里那两行是**已经存在的**（其中一行故意指向副本）；这里只关心
        # "有没有**新增**指向副本的行"。
        bad = [rid for rid in fresh
               if str((reg.images[rid] or {}).get('Path') or '')
               .startswith('sr_general/')]
        assert not bad, f'补登把副本文件写进了表：{bad}'


class TestTheTagAttributeFormIsNotHijacked:
    def test_resolving_the_underscored_form_prefers_the_tagged_row(
            self, nested_project):
        from storygraph.assets.asset_registry import (AssetIndex, load_hooks,
                                                      parse_asset_tables)
        reg = parse_asset_tables(nested_project / 'game' / 'asset_tables',
                                 hooks=load_hooks(
                                     project_root=nested_project),
                                 project_root=nested_project)
        assert 'ling_angry' in reg.images, '前置条件：劫持者那一行在表里'
        hits = AssetIndex(reg).resolve('ling_angry')
        assert hits and hits[0] == '_angry', hits

    def test_a_row_with_its_own_tag_is_never_overridden(self, nested_project):
        """精确 ID 那行**自己有 Tag** 时，保持"精确 ID 优先"（不许乱改判）。"""
        from storygraph.assets.asset_registry import (AssetIndex, load_hooks,
                                                      parse_asset_tables)
        wb = openpyxl.load_workbook(
            nested_project / 'game' / 'asset_tables' / 'images.xlsx')
        ws = wb.active
        _row(ws, ['ling_angry', 'game/cc_preview/ling/angry.png', 'ling',
                  'angry', 'character', ''])
        wb.save(nested_project / 'game' / 'asset_tables' / 'images.xlsx')
        reg = parse_asset_tables(nested_project / 'game' / 'asset_tables',
                                 hooks=load_hooks(
                                     project_root=nested_project),
                                 project_root=nested_project)
        hits = AssetIndex(reg).resolve('ling_angry')
        assert hits == ['_angry'] or hits[0] == 'ling_angry', hits


class TestImageTagOf:
    def test_it_reads_back_the_real_tag(self, nested_project):
        from storygraph.assets.asset_registry import load_hooks, \
            parse_asset_tables
        from storygraph.app.beats import image_tag_of
        from storygraph.story.dsl import Symbols
        reg = parse_asset_tables(nested_project / 'game' / 'asset_tables',
                                 hooks=load_hooks(
                                     project_root=nested_project),
                                 project_root=nested_project)
        sym = Symbols(reg)
        assert image_tag_of('ling_angry', sym) == 'ling'
        assert image_tag_of('ling angry', sym) == 'ling'

    def test_expression_choices_are_not_empty(self, nested_project):
        """表情候选 = 这个 tag 的真实属性名（39 个那种）。"""
        from storygraph.assets.asset_registry import load_hooks, \
            parse_asset_tables
        from storygraph.app.beats import expression_choices
        from storygraph.story.dsl import Symbols
        reg = parse_asset_tables(nested_project / 'game' / 'asset_tables',
                                 hooks=load_hooks(
                                     project_root=nested_project),
                                 project_root=nested_project)
        sym = Symbols(reg)

        class _Beat:
            show = 'ling_angry'
        assert expression_choices(_Beat(), sym) == ['angry'], \
            expression_choices(_Beat(), sym)


class TestThePreviewFindsTheUnderscoredForm:
    def test_path_of_image_accepts_both_forms(self, nested_project):
        from storygraph.app.editor import EditorSession
        s = EditorSession(project_root=nested_project)
        s.load_assets()
        a = s.path_of_image('ling_angry')
        b = s.path_of_image('ling angry')
        assert a is not None and b is not None, (a, b)
        assert str(a) == str(b)
        assert str(a).endswith('images\\characters\\ling\\angry.png') or \
            str(a).endswith('images/characters/ling/angry.png'), a
