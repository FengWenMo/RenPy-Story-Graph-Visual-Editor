"""
MCP 的**读**工具：章节 / 拍 / 资产 / 图像解析（2026-11-03）
==========================================================

外部 AI 审查的原话："好久之前做的，现在加了很多新功能却没同步。"

确实：`mcp_surface` 原来只有语句解析层那批（未知写法 / 引擎事实 /
声明），而工具这几年长出来的三块 —— **章节与拍**、**资源表与图像身份**、
**剧本表与补丁** —— 在 MCP 里一个都没有。别家的 agent 连"这个项目有几章、
`show qing base` 到底合不合法"都问不出来。

这个文件钉四件事：

  1. 四个读工具都出现在 `tools/list` 里，且 `inputSchema` 是合法对象；
  2. 走**协议层**（`handle` → `tools/call`）能调通，不是只有函数能跑；
  3. 没有项目根 / 不是工程根时给**结论**（`ok=False` + `reason`），绝不抛；
  4. 两个"看起来对其实错"的判据：
     · 图像名 → 资产 ID 必须走 `layeredimage` 反查（`base` → `normal.png`），
       不能拼 `qing_base`；
     · `showable` 必须取 `resolve_pick.ok`（映射把 `char` 填成显示名时，
       `identity.showable` 仍是 True —— 报 True 会让 AI 写出引擎报错的 `show`）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from storygraph import mcp_surface as M                     # noqa: E402
from storygraph.mcp import server as S                     # noqa: E402

NEW_TOOLS = ('chapters', 'beats', 'assets', 'image_lookup')


class TestTheyAreAdvertised:
    def test_every_new_tool_is_in_tools_list(self):
        names = {t['name'] for t in S.tools_list()}
        for short in NEW_TOOLS:
            assert f'storygraph_{short}' in names, f'{short} 没出现在 tools/list'

    def test_schema_is_a_real_object_schema(self):
        for tool in S.tools_list():
            schema = tool.get('inputSchema') or {}
            assert schema.get('type') == 'object', tool['name']
            assert isinstance(schema.get('properties'), dict), tool['name']

    def test_each_has_a_capability_entry_with_limits(self):
        caps = {t['name']: t for t in M.capabilities()['tools']}
        for short in NEW_TOOLS:
            meta = caps.get(f'storygraph_{short}')
            assert meta, f'{short} 没进 capabilities()'
            assert meta.get('kind') == 'read'
            assert meta.get('known_limits'), f'{short} 没写已知边界'

    def test_project_root_is_injected(self):
        """非 `capabilities` 的工具都要带 `project_root`（否则模型不知道要传）。"""
        for tool in S.tools_list():
            if tool['name'] == 'storygraph_capabilities':
                continue
            assert 'project_root' in tool['inputSchema']['properties'], \
                tool['name']


class TestTheProtocolCanCallThem:
    @pytest.mark.parametrize('short', NEW_TOOLS)
    def test_tools_call_without_project_returns_a_conclusion(self, short):
        """没有项目根 → 一条**能读懂**的结论，而不是异常。"""
        args = {'name': 'x'} if short == 'image_lookup' else {}
        reply = S.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                          'params': {'name': f'storygraph_{short}',
                                     'arguments': args}})
        assert 'error' not in reply, reply
        text = reply['result']['content'][0]['text']
        got = json.loads(text)
        assert got['ok'] is False
        assert got['reason'], '拒绝时必须有 reason'

    def test_not_a_project_fails_fast(self, tmp_path):
        """空目录不是工程根：要**立刻**说清，而不是在后面报怪错。"""
        for short in NEW_TOOLS:
            fn = getattr(M, short)
            kw = {'name': 'x'} if short == 'image_lookup' else {}
            got = fn(str(tmp_path), **kw)
            assert got['ok'] is False
            assert 'game' in got['reason']


class TestImageNameMapsToAssetViaTheDeclaration:
    """图像名 → 资产 ID：**不能拼名字**（`qing base` ≠ `qing_base`）。"""

    IMAGES = {
        'qing_normal': {'Tag': 'qing', 'Attributes': 'normal'},
        'qing_speechless': {'Tag': 'qing', 'Attributes': 'speechless'},
    }
    #: `definitions.rpy` 里的声明：`attribute base: "normal"` /
    #: `attribute angry: 'speechless'`
    WORDS = {'qing': {'normal': 'base', 'speechless': 'angry'}}

    def test_declared_attribute_resolves_to_the_file_name(self):
        assert M._image_id_for_spec(self.IMAGES, 'qing base', self.WORDS) \
            == 'qing_normal'

    def test_second_attribute_too(self):
        assert M._image_id_for_spec(self.IMAGES, 'qing angry', self.WORDS) \
            == 'qing_speechless'

    def test_file_name_also_works(self):
        """没声明时退回文件名（老项目没有 `layeredimage`）。"""
        assert M._image_id_for_spec(self.IMAGES, 'qing normal', {}) \
            == 'qing_normal'

    def test_unknown_attribute_is_not_guessed(self):
        assert M._image_id_for_spec(self.IMAGES, 'qing nosuch', self.WORDS) == ''

    def test_single_word_is_not_an_image_name(self):
        assert M._image_id_for_spec(self.IMAGES, 'qing', self.WORDS) == ''


class TestShowableAsksTheRealQuestion:
    """`showable` 取 `resolve_pick.ok`，不是 `identity.showable`。

    真机事故：映射里写 `{"images": {"qing": {"char": "青"}}}` —— `identity`
    说"这是 sprite、能 show"，而 `resolve_pick` 会因为「青」不是项目认的
    tag 而拒绝。只报前者，外部 AI 就会写出 `show 青 base`（引擎报错）。
    """

    def test_mapping_typo_makes_showable_false(self, monkeypatch, tmp_path):
        (tmp_path / 'game').mkdir()
        row = {'ID': 'qing', 'Tag': 'ui', 'Attributes': 'qing',
               'Path': 'game/phone_msg/qing.png', 'Category': 'ui'}

        class _Reg:
            images = {'qing': row}
            characters = {'l': {'ID': 'l', 'ImageTag': 'lan'}}
            transforms = {}
            project_root = str(tmp_path)

        monkeypatch.setattr(
            'storygraph.assets.registry_load.load_asset_registry',
            lambda *_a, **_k: _Reg())
        monkeypatch.setattr(
            'storygraph.app.picker.pick_context',
            lambda *_a, **_k: {'known_tags': frozenset({'qing', 'lan'}),
                               'known_chars': {}, 'mapping': {
                                   'images': {'qing': {'kind': 'sprite',
                                                       'char': '青',
                                                       'attr': 'base'}}},
                               'all_rows': {'qing': row},
                               'sprite_words': {}})
        got = M.assets(str(tmp_path), query='qing')
        assert got['ok'] is True
        assert got['count'] == 1
        assert got['assets'][0]['showable'] is False, \
            '映射填成显示名时仍然报 showable=True —— 会误导外部 AI'
        assert got['assets'][0]['spec'] == ''


class TestNoTableIsNotNoAssets:
    def test_missing_table_says_so(self, monkeypatch, tmp_path):
        (tmp_path / 'game').mkdir()
        monkeypatch.setattr(
            'storygraph.assets.registry_load.load_asset_registry',
            lambda *_a, **_k: None)
        got = M.assets(str(tmp_path))
        assert got['ok'] is False
        assert '不等于' in got['reason'], '必须说清"读不出来 ≠ 没有资产"'
