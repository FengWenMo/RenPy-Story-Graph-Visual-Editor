"""
tests/test_asset_identity.py
============================
**图像资产身份解析**（契约 §1.1）。

用户 2026-10-30 的 P0 原话：

> 立绘怎么一堆单独的分类，什么 relaxing，resigned —— 这不都是凌的表情吗
> 只有文件名是角色名的可以匹配；直接放角色那个头像图就能动

根因：没有"这张图是谁"的唯一真源。这里钉住**判定顺序**与**不猜**：

  ① 映射精确覆盖 → ② 项目自己声明过的名字 → ③ 映射规则
  → ④ 资源表 Tag 列（仅已知角色 tag 算立绘）
  → ⑤ 路径目录段命中已知角色 tag → ⑥ 文件 stem 恰等于 tag → ⑦ unknown。

（顺序的唯一真源是 `core.authority.IDENTITY_ORDER`；2026-10-31 之前 ①② 是
一个整体、且排在声明之前，glob 规则因此能改掉项目声明 —— 见
`TestDeclaredBeatsGlobRules`。）

以及**通用副本检测**（同 tag+同 stem 另有一行 Tag 非空 → 预览图）、
**avatar 只认目录段**、`find_real_sprite` 预览/头像 → 真立绘。

全部用**合成行**，不依赖任何真实项目；另有一组"无项目专有常量"守卫。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from storygraph.assets.asset_identity import (
    Identity,
    asset_id_of,
    engine_fact_tags,
    find_real_sprite,
    group_key_for_identity,
    known_characters,
    known_tags_of,
    kind_from_tag_word,
    normalize_relpath,
    project_context,
    resolve_image,
    resolve_images,
    spec_for_identity,
)
from storygraph.assets.asset_registry import AssetRegistry

REPO = Path(__file__).resolve().parents[1]


def _reg(**characters) -> AssetRegistry:
    reg = AssetRegistry()
    reg.characters = dict(characters)
    return reg


REG = _reg(
    lyj={'ID': 'lyj', 'Name': '「凌跃捷」', 'ImageTag': 'ling',
         'Aliases': 'LTJ|凌'},
    l={'ID': 'l', 'Name': '「蓝」', 'ImageTag': 'lan'},
    mary={'ID': 'mary', 'Name': '玛丽', 'ImageTag': 'mary'},
    xqlz={'ID': 'xqlz', 'Name': '星桥理子', 'ImageTag': 'xing'},
)
CHARS = known_characters(REG)
TAGS = set(CHARS)


def row(aid: str, path: str, tag: str = '', attrs: str = '', **extra) -> dict:
    out = {'ID': aid, 'Path': path, 'Tag': tag, 'Attributes': attrs}
    out.update(extra)
    return out


def ident(rowobj, rows=None, *, mapping=None, relpath=''):
    return resolve_image(rowobj, relpath=relpath, known_tags=TAGS,
                         known_chars=CHARS, mapping=mapping,
                         rows=rows if rows is not None else [rowobj])


# ==================================================================
#  路径归一
# ==================================================================

class TestNormalizeRelpath:
    """`Path` 在真实项目里有四种写法，映射 glob 是项目相对的 → 必须先归一。"""

    def test_backslashes(self):
        assert normalize_relpath(r'game\images\x.png') == 'game/images/x.png'

    def test_project_root_prefix(self, tmp_path):
        proj = tmp_path / 'proj'
        (proj / 'game').mkdir(parents=True)
        abspath = proj / 'game' / 'images' / 'x.png'
        assert normalize_relpath(str(abspath), str(proj)) == 'game/images/x.png'

    def test_project_dir_name_prefix(self, tmp_path):
        proj = tmp_path / 'proj'
        proj.mkdir()
        assert normalize_relpath('proj/game/images/x.png',
                                 str(proj)) == 'game/images/x.png'

    def test_empty(self):
        assert normalize_relpath('') == ''
        assert normalize_relpath(None) == ''

    def test_leading_dot_and_parent(self):
        assert normalize_relpath('./game/x.png') == 'game/x.png'
        assert normalize_relpath('../game/x.png') == 'game/x.png'


# ==================================================================
#  ③ 资源表 Tag 列
# ==================================================================

class TestTagColumn:
    def test_known_character_tag_is_sprite(self):
        got = ident(row('ling_happy', 'game/images/characters/ling/happy.png',
                        'ling', 'happy'))
        assert got.kind == 'sprite'
        assert got.tag == 'ling'
        assert got.attrs == ('happy',)
        assert got.display == '凌跃捷', '显示名要去掉「」这类引号'
        assert got.source == 'tag-column'
        assert got.showable is True
        assert spec_for_identity(got) == 'ling happy'

    def test_multi_attr_kept(self):
        got = ident(row('ling_x', 'game/images/characters/ling/x.png',
                        'ling', 'base_arms_akimbo sigh'))
        assert got.attrs == ('base_arms_akimbo', 'sigh')

    @pytest.mark.parametrize('tag,kind,group', [
        ('bg', 'bg', '背景'),
        ('cg', 'cg', 'CG'),
        ('ui', 'ui', '界面/其他图层'),
    ])
    def test_non_actor_tag_layer(self, tag, kind, group):
        got = ident(row(f'{tag}_x', f'game/images/{tag}/x.png', tag, 'x'))
        assert (got.kind, got.tag, got.showable) == (kind, tag, True)
        assert group_key_for_identity(got) == group

    def test_unknown_tag_is_other_not_sprite(self):
        """Tag 写了但不是已知角色 tag → `other`（**不猜**是哪个角色）。"""
        got = ident(row('nbg_line', 'game/images/nbg/_line.png', 'nbg', '_line'))
        assert got.kind == 'other'
        assert got.tag == ''
        assert got.showable is False
        assert group_key_for_identity(got) == '其他图片'


# ==================================================================
#  ④ 路径目录段 / ⑤ 文件名 stem
# ==================================================================

class TestFolderInference:
    def test_tag_dir_is_sprite(self):
        got = ident(row('happy', 'game/images/characters/ling/happy.png'))
        assert got.kind == 'sprite'
        assert got.tag == 'ling'
        assert got.attrs == ('happy',)
        assert got.source == 'folder'
        assert got.showable is True
        assert spec_for_identity(got) == 'ling happy'

    def test_tag_prefix_stripped_from_stem(self):
        """`.../ling/ling_happy.png` → 属性 `happy`（否则会写出 `ling ling_happy`）。"""
        got = ident(row('ling_happy',
                        'game/images/characters/ling/ling_happy.png'))
        assert got.attrs == ('happy',)

    def test_underscore_attr_not_eaten(self):
        """`_hd_cut` / `_ss_cut` 这类下划线开头的属性原样保留（不猜）。"""
        got = ident(row('xing_hd',
                        'game/images/characters/xing/_hd_cut.png'))
        assert got.attrs == ('_hd_cut',)
        assert spec_for_identity(got) == 'xing _hd_cut'

    def test_stem_equals_tag(self):
        got = ident(row('ling', 'game/images/ling.png'))
        assert (got.kind, got.tag, got.source) == ('sprite', 'ling', 'stem')

    def test_case_insensitive_match_reports_known_spelling(self):
        got = ident(row('Ling_Happy',
                        'game/images/characters/LING/happy.png'))
        assert got.tag == 'ling', '应按已知 tag 的原写法回报'

    def test_unknown_path_stays_unknown(self):
        """一点角色线索都没有 → unknown，**不猜**（这是非法 show 的源头）。"""
        got = ident(row('about_collection_smoke', 'about_collection_smoke.png',
                        '', 'about_collection_smoke'))
        assert got.kind == 'unknown'
        assert got.tag == ''
        assert got.showable is False
        assert got.source == 'unknown'
        assert group_key_for_identity(got) == '未识别（不猜）'

    def test_weak_bg_folder_is_bg_but_not_showable(self):
        """没 Tag 也没映射：能看出是背景目录 → 归背景组，但**说不出 show 名**。"""
        got = ident(row('room', 'game/images/bg/room.png'))
        assert got.kind == 'bg'
        assert got.tag == ''
        assert got.showable is False


# ==================================================================
#  ⓪ 项目自己声明过的图像名（ATL / 脚本合并行）—— 不许把能用的 show 封掉
# ==================================================================

class TestDeclaredImages:
    """
    `image logo blink:`（贴图写在 ATL 块里、`Path` 为空）在引擎里是**真图像**。

    用户 2026-10-30 的映射要治理的是"猜"，不是"项目自己声明过的" ——
    这类行必须 `showable=True`（否则原本能从选择器加的图会被封掉）。
    """

    def test_atl_row_is_showable(self):
        got = ident(row('logo_blink', '', 'logo', 'blink', _atl=True))
        assert got.showable is True
        assert got.tag == 'logo'
        assert spec_for_identity(got) == 'logo blink'

    def test_atl_row_single_word_name(self):
        got = ident(row('dororo_displeased', '', 'dororo_displeased', '',
                        _atl=True))
        assert got.showable is True
        assert spec_for_identity(got) == 'dororo_displeased'

    def test_atl_row_character_is_sprite(self):
        got = ident(row('ling_secret', '', 'ling', 'secret', _atl=True))
        assert got.kind == 'sprite' and got.showable is True
        assert got.display == '凌跃捷'

    def test_old_table_atl_marker_in_notes(self):
        """老表没有 `_atl`：`Path` 空 + `Notes` 里那句标记，同样算声明过。"""
        got = ident(row('logo_blink', '', 'logo', 'blink',
                        Notes='定义.rpy:12；ATL 定义（贴图写在块里，看源文件）'))
        assert got.showable is True

    def test_script_merged_row_is_showable(self):
        got = ident(row('custom_fx', 'Solid("#000")', 'custom_fx', '',
                        _from_script=True))
        assert got.showable is True
        assert spec_for_identity(got) == 'custom_fx'

    def test_plain_scan_row_without_evidence_still_rejected(self):
        """反例：普通扫描行（无声明证据、Tag 又不认识）仍然不许放行。"""
        got = ident(row('nbg_line', 'game/images/nbg/_line.png', 'nbg', '_line'))
        assert got.showable is False
        assert got.kind == 'other'


# ==================================================================
#  通用副本检测（不认项目名）
# ==================================================================

class TestPreviewCopyDetection:
    def _rows(self):
        real = row('ling_happy', 'game/images/characters/ling/happy.png',
                   'ling', 'happy')
        copy = row('happy', 'game/art/copies/ling/happy.png')
        return [real, copy]

    def test_same_tag_same_stem_is_preview(self):
        rows = self._rows()
        got = ident(rows[1], rows)
        assert got.kind == 'preview'
        assert got.tag == 'ling'
        assert got.showable is False
        assert group_key_for_identity(got) == '角色预览图（不能直接上舞台）'

    def test_preview_word_in_dir_is_preview_even_without_copy(self):
        """目录名里有 preview/thumb 这类**通用词**也算副本（不写死项目目录名）。"""
        rows = [row('lonely', 'game/art/previews/ling/lonely.png')]
        got = ident(rows[0], rows)
        assert got.kind == 'preview'
        assert got.tag == 'ling'
        assert find_real_sprite(got, rows) is None

    def test_thumbnails_dir_is_preview(self):
        rows = [row('happy', 'game/art/thumbnails/ling/happy.png')]
        assert ident(rows[0], rows).kind == 'preview'

    def test_plain_folder_without_copy_is_sprite(self):
        rows = [row('happy', 'game/art/full/ling/happy.png')]
        assert ident(rows[0], rows).kind == 'sprite'

    def test_copy_detection_uses_attr_words_too(self):
        """真行 Path 的 stem 带角色前缀（`ling_happy.png`）也能被对上。"""
        real = row('ling_happy', 'game/images/characters/ling/ling_happy.png',
                   'ling', 'ling_happy')
        copy = row('happy', 'game/cc_preview/ling/happy.png')
        assert ident(copy, [real, copy]).kind == 'preview'
        assert find_real_sprite(ident(copy, [real, copy]),
                                [real, copy]) is not None


# ==================================================================
#  avatar（只认映射或目录段；宁可 unknown）
# ==================================================================

class TestAvatar:
    def test_avatar_dir(self):
        got = ident(row('avatar_ling', 'game/art/avatar/ling.png'))
        assert got.kind == 'avatar'
        assert got.tag == 'ling'
        assert got.showable is False
        assert group_key_for_identity(got) == '角色头像（不能直接上舞台）'

    def test_avatar_plural_dir(self):
        got = ident(row('avatar_lan', 'game/art/avatars/lan.png'))
        assert (got.kind, got.tag) == ('avatar', 'lan')

    def test_avatar_dir_unknown_tag_left_empty(self):
        got = ident(row('who', 'game/art/avatar/who.png'))
        assert got.kind == 'avatar'
        assert got.tag == ''

    def test_avatar_tag_column(self):
        """资源表 Tag 写着 `avatar`、Attributes 写着角色 tag。"""
        got = ident(row('characters_ling',
                        'game/images/characters/avatar/ling.png',
                        'avatar', 'ling'))
        assert got.kind == 'avatar'
        assert got.tag == 'ling'
        assert got.source == 'tag-column'


# ==================================================================
#  ① / ② 映射优先
# ==================================================================

class TestMappingPriority:
    def test_exact_by_asset_id_overrides_tag_column(self):
        mapping = {'images': {'ling_happy': {'kind': 'preview',
                                             'char': 'ling',
                                             'attr': 'happy'}}}
        got = ident(row('ling_happy',
                        'game/images/characters/ling/happy.png',
                        'ling', 'happy'), mapping=mapping)
        assert got.kind == 'preview'
        assert got.source == 'mapping'

    def test_exact_by_relpath(self):
        mapping = {'images': {
            'game/images/characters/ling/happy.png':
                {'kind': 'sprite', 'char': 'ling', 'attr': 'happy'}}}
        got = ident(row('whatever', 'game/images/characters/ling/happy.png'),
                    mapping=mapping)
        assert (got.kind, got.tag, got.attrs) == ('sprite', 'ling', ('happy',))

    def test_rules_are_ordered_first_match_wins(self):
        mapping = {'rules': [
            {'glob': 'game/**', 'kind': 'other'},
            {'glob': 'game/images/characters/<tag>/*.png', 'kind': 'sprite',
             'char': '<tag>', 'attr': '<stem>'},
        ]}
        got = ident(row('happy', 'game/images/characters/ling/happy.png'),
                    mapping=mapping)
        assert got.kind == 'other', '第一条命中就该停（顺序是契约）'

    def test_rule_placeholders(self):
        mapping = {'rules': [
            {'glob': 'game/art/<tag>/<stem>.png', 'kind': 'sprite',
             'char': '<tag>', 'attr': '<stem>'},
        ]}
        got = ident(row('happy', 'game/art/ling/happy.png'), mapping=mapping)
        assert got.source == 'mapping-rule'
        assert (got.kind, got.tag, got.attrs) == ('sprite', 'ling', ('happy',))

    def test_bg_rule_derives_stem_attr(self):
        mapping = {'rules': [{'glob': 'game/images/bg/*.png', 'kind': 'bg'}]}
        got = ident(row('bg_room', 'game/images/bg/room.png'), mapping=mapping)
        assert got.kind == 'bg'
        assert got.attrs == ('room',)
        assert spec_for_identity(got) == 'bg room'

    def test_empty_mapping_does_not_block_inference(self):
        got = ident(row('happy', 'game/images/characters/ling/happy.png'),
                    mapping={})
        assert got.kind == 'sprite'


class TestDeclaredBeatsGlobRules:
    """
    2026-10-31（真源与优先级 §P0-1）：**glob 规则只补空缺，不许改项目声明**。

    旧代码把"精确覆盖 + 规则"当成一个整体排在声明之前，于是：

      · 一条 `game/**` 的 glob（"这一类大概都这样"）就能把项目自己写的
        `layeredimage` 名字改掉；
      · 而同一函数的注释还写着"映射要治理的是『猜』，不是『项目自己声明过的』"
        —— 注释在第 553 行、映射在第 541 行，**代码与意图相反**，两边都不报。

    现在的顺序：精确覆盖（点名的这一张）→ 项目声明 → glob 规则。
    """

    def _declared(self, **extra):
        return row('ling_happy', 'game/images/characters/ling/happy.png',
                   'ling', 'happy', _layered=True, **extra)

    def test_a_glob_rule_cannot_rename_a_declared_image(self):
        mapping = {'rules': [{'glob': 'game/**', 'kind': 'preview'}]}
        got = ident(self._declared(), mapping=mapping)
        assert got.kind == 'sprite', '声明过的图像名被一条 glob 规则改掉了'
        assert got.source == 'declared'
        assert got.showable is True

    def test_an_exact_override_still_wins(self):
        """人**点名**这一张（`images` 表）：可以改，这是最高级别的人为意图。"""
        mapping = {'images': {'ling_happy': {'kind': 'preview',
                                             'char': 'ling',
                                             'attr': 'happy'}}}
        got = ident(self._declared(), mapping=mapping)
        assert got.kind == 'preview' and got.source == 'mapping'

    def test_a_rule_still_fills_a_row_nobody_declared(self):
        """规则不是废物：没人声明过的行，它照旧给结论。"""
        mapping = {'rules': [{'glob': 'game/**', 'kind': 'bg'}]}
        got = ident(row('room', 'game/images/bg/room.png'), mapping=mapping)
        assert got.kind == 'bg' and got.source == 'mapping-rule'


# ==================================================================
#  find_real_sprite：预览图/头像 → 真立绘
# ==================================================================

class TestFindRealSprite:
    def _rows(self):
        return [
            row('ling_happy', 'game/images/characters/ling/happy.png',
                'ling', 'happy'),
            row('ling_sad', 'game/images/characters/ling/sad.png',
                'ling', 'sad'),
            row('happy', 'game/cc_preview/ling/happy.png'),
        ]

    def test_prefers_matching_attr(self):
        rows = self._rows()
        got = find_real_sprite(ident(rows[2], rows), rows)
        assert got is not None and got['ID'] == 'ling_happy'

    def test_prefers_larger_pixels_when_dimensions_given(self):
        """属性都对得上时，像素更大的那张才是"真立绘"（8K vs 缩略图）。"""
        rows = [
            row('ling_close_happy_a', 'game/images/characters/ling/ch_a.png',
                'ling', 'close_happy'),
            row('ling_close_happy_b', 'game/images/characters/ling/ch_b.png',
                'ling', 'close_happy'),
        ]
        want = Identity(kind='preview', tag='ling', attrs=('close_happy',))
        dims = {'ling_close_happy_a': (100, 100),
                'ling_close_happy_b': (4000, 8000)}
        got = find_real_sprite(want, rows, dimensions=dims)
        assert got is not None and got['ID'] == 'ling_close_happy_b'

    def test_avatar_reuses_any_sprite_of_the_tag(self):
        rows = [
            row('ling_happy', 'game/images/characters/ling/happy.png',
                'ling', 'happy'),
            row('avatar_ling', 'game/art/avatar/ling.png'),
        ]
        got = find_real_sprite(ident(rows[1], rows), rows)
        assert got is not None and got['ID'] == 'ling_happy'

    def test_no_candidate_returns_none(self):
        rows = [row('happy', 'game/art/previews/ling/happy.png')]
        assert find_real_sprite(ident(rows[0], rows), rows) is None

    def test_sprite_identity_is_not_remapped(self):
        one = row('ling_happy', 'game/images/characters/ling/happy.png',
                  'ling', 'happy')
        assert find_real_sprite(ident(one, [one]), [one]) is None


# ==================================================================
#  spec / 分组名
# ==================================================================

class TestSpecAndGroupKey:
    def test_spec_tag_plus_attrs(self):
        got = Identity(kind='sprite', tag='ling', attrs=('base', 'happy'),
                       showable=True)
        assert spec_for_identity(got) == 'ling base happy'

    def test_spec_does_not_repeat_tag(self):
        got = Identity(kind='sprite', tag='ling', attrs=('ling', 'happy'))
        assert spec_for_identity(got) == 'ling happy'

    def test_spec_no_tag_falls_back_to_asset_id(self):
        got = Identity(kind='unknown')
        assert spec_for_identity(got, {'ID': 'refusing'}) == 'refusing'

    def test_group_key_sprite_uses_display(self):
        got = Identity(kind='sprite', tag='ling', display='凌跃捷')
        assert group_key_for_identity(got) == '立绘：凌跃捷（ling）'

    def test_group_key_sprite_falls_back_to_tag(self):
        assert group_key_for_identity(
            Identity(kind='sprite', tag='ling')) == '立绘：ling'

    def test_asset_id_of(self):
        assert asset_id_of({'ID': 'x'}) == 'x'
        assert asset_id_of({}) == ''
        assert asset_id_of(None) == ''


# ==================================================================
#  项目上下文（已知 tag 三路合并）
# ==================================================================

class TestProjectContext:
    def test_chars_and_mapping_merge(self, tmp_path):
        import json
        proj = tmp_path / 'proj'
        proj.mkdir()
        (proj / 'storygraph.mapping.json').write_text(json.dumps({
            'schema': 'storygraph.mapping/1',
            'characters': {'dororo': {'display': '多罗罗'}},
        }), encoding='utf-8')
        reg = _reg(lyj={'ID': 'lyj', 'Name': '凌', 'ImageTag': 'ling'})
        reg.images = {'x': row('x', 'game/art/full/ling/happy.png')}
        ctx = project_context(reg, str(proj))
        assert 'ling' in ctx['known_tags'] and 'dororo' in ctx['known_tags']
        assert ctx['known_chars']['ling']['display'] == '凌'
        assert ctx['mapping']['characters']['dororo']['display'] == '多罗罗'
        assert ctx['warnings'] == ()
        assert ctx['copy_index'] == {}

    def test_engine_fact_tags(self, tmp_path):
        import json
        proj = tmp_path / 'proj'
        proj.mkdir()
        (proj / 'storygraph.engine_facts.json').write_text(json.dumps({
            'schema': 1,
            'engine': {},
            'fingerprint': {},
            'collection': {'parse_clean': True, 'probe_complete': True,
                           'lint_exit': 0},
            'lint': {'parse_errors': [], 'semantic_errors': [],
                     'program_errors': []},
            'facts': {
                'tags': {'value': ['ling', 'bg'], 'source': 'probe'},
                'attributes': {'value': {
                    'ling': [{'group': 'expressions', 'attribute': 'happy'}],
                    'bg': [],
                }, 'source': 'probe'},
            },
            'gaps': [],
            'derived': {'overridden_builtins': [], 'undeclared_heads': [],
                        'scan_disclaimer': 'x'},
            'conflicts': [],
        }), encoding='utf-8')
        assert engine_fact_tags(str(proj)) == {'ling'}, 'bg 没有属性，不算角色'

    def test_no_facts_file_is_empty(self, tmp_path):
        assert engine_fact_tags(str(tmp_path)) == set()

    def test_known_characters_merges_duplicate_tags(self):
        reg = _reg(a={'ID': 'a', 'Name': '', 'ImageTag': 'ling'},
                   b={'ID': 'b', 'Name': '凌', 'ImageTag': 'ling'})
        chars = known_characters(reg)
        assert chars['ling']['display'] == '凌'

    def test_non_actor_tag_never_a_character(self):
        reg = _reg(c={'ID': 'c', 'Name': '背景', 'ImageTag': 'bg'})
        assert known_characters(reg) == {}
        assert known_tags_of(reg) == set()

    def test_resolve_images_batch_matches_single(self):
        rows = {
            'ling_happy': row('ling_happy',
                              'game/images/characters/ling/happy.png',
                              'ling', 'happy'),
            'happy': row('happy', 'game/cc_preview/ling/happy.png'),
        }
        batch = resolve_images(rows, known_tags=TAGS, known_chars=CHARS)
        one = ident(rows['happy'], list(rows.values()))
        assert batch['happy'] == one


# ==================================================================
#  通用性守卫：代码里不许出现项目专有常量当逻辑分支
# ==================================================================

def _code_strings(path: Path) -> list:
    """模块里**参与代码**的字符串常量（排除 docstring）。"""
    tree = ast.parse(path.read_text(encoding='utf-8'))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef,
                             ast.AsyncFunctionDef, ast.ClassDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc:
                docstrings.add(doc)
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value not in docstrings:
                out.append(node.value)
    return out


class TestNoProjectSpecificConstants:
    """
    **通用性守卫**：身份解析不许靠项目专有常量（`sr_general` / `cc_preview` /
    `ling` …）当逻辑分支。允许出现在 docstring 的抗例里，但**不许当代码字符串**。
    """

    MODULES = ('storygraph/assets/asset_identity.py',
               'storygraph/assets/asset_mapping.py',
               'storygraph/app/picker.py')

    def test_no_project_literals_in_code(self):
        forbidden = ('sr_general', 'cc_preview', '千连万理',
                     'refusing', 'avatar_ling')
        for rel in self.MODULES:
            strings = _code_strings(REPO / rel)
            for word in forbidden:
                bad = [s for s in strings if word in s.lower()]
                assert not bad, f'{rel} 的代码字符串里出现了项目专有词 {word!r}: {bad[:3]}'

    def test_identity_modules_do_not_import_ui(self):
        for rel in ('storygraph/assets/asset_identity.py',
                    'storygraph/assets/asset_mapping.py'):
            src = (REPO / rel).read_text(encoding='utf-8')
            assert 'ui_qt' not in src, f'{rel} 不许 import UI 层（契约 §1.1）'
            assert 'PySide6' not in src

    def test_kind_vocabulary_is_shared_not_local(self):
        """bg/cg/ui 的判定词表来自 `core.vocabulary`（唯一真源），不是就地一份。"""
        assert kind_from_tag_word('bg') == 'bg'
        assert kind_from_tag_word('cg') == 'cg'
        assert kind_from_tag_word('ui') == 'ui'
        assert kind_from_tag_word('overlay') == 'ui'
        assert kind_from_tag_word('ling') == ''
