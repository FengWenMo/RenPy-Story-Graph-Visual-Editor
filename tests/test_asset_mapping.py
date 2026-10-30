"""
tests/test_asset_mapping.py
===========================
**项目级资源映射**（`storygraph.mapping.json`，契约 §1.2 / 规划 §2.6）。

为什么需要它：用户 2026-10-30 拍板"预览语义必须真实 / 映射文件用 JSON /
工具要通用"。映射是**唯一"项目知识"载体** —— 工具在没有映射时按文件夹推断，
映射只做覆盖/纠正；并且**初稿必须从实际目录自动生成**（别的项目开发者
自己跑一遍就能维护自己的映射）。

钉住的判据：

  · 加载 / 坏 JSON / 未知键只警告不失败 / 按 mtime 缓存；
  · `images` 精确覆盖优先于 `rules`，`rules` 按数组顺序先中先得；
  · 占位符 `<tag>` / `<stem>` 替换；
  · `draft_mapping` 生成通用初稿（不写死本项目目录名）、round-trip 能读回、
    文件里没有本机绝对路径；
  · `save_mapping` 原子写 + 旧文件 `.bak`。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from storygraph.assets.asset_identity import (
    known_characters,
    resolve_image,
)
from storygraph.assets.asset_mapping import (
    MAPPING_FILENAME,
    MAPPING_SCHEMA,
    MappingError,
    draft_mapping,
    find_mapping_path,
    load_mapping,
    mapping_cached,
    resolve_with_mapping,
    save_mapping,
    validate_mapping,
)
from storygraph.assets.asset_registry import AssetRegistry

ROOT = Path(__file__).resolve().parents[1]


def _row(aid, path, tag='', attrs='', **extra):
    out = {'ID': aid, 'Path': path, 'Tag': tag, 'Attributes': attrs}
    out.update(extra)
    return out


GOOD = {
    'schema': MAPPING_SCHEMA,
    'name': '测试映射',
    'characters': {'ling': {'display': '凌跃捷', 'aliases': ['凌']}},
    'rules': [
        {'glob': 'game/art/full/<tag>/*.png', 'kind': 'sprite',
         'char': '<tag>', 'attr': '<stem>'},
    ],
    'images': {'13.2': {'kind': 'cg'}},
    'attributes': {'expressions': {'label': '表情'}},
}


# ==================================================================
#  查找 / 加载
# ==================================================================

class TestFindAndLoad:
    def test_find_in_project_root(self, tmp_path):
        target = tmp_path / MAPPING_FILENAME
        target.write_text('{}', encoding='utf-8')
        got = find_mapping_path(str(tmp_path))
        assert got == str(target.resolve())
        assert Path(got).is_absolute()

    def test_find_in_game_dir(self, tmp_path):
        (tmp_path / 'game').mkdir()
        target = tmp_path / 'game' / MAPPING_FILENAME
        target.write_text('{}', encoding='utf-8')
        assert find_mapping_path(str(tmp_path)) == str(target.resolve())

    def test_no_mapping_returns_none(self, tmp_path):
        assert find_mapping_path(str(tmp_path)) is None
        assert find_mapping_path('') is None

    def test_load_good_mapping(self, tmp_path):
        path = tmp_path / MAPPING_FILENAME
        path.write_text(json.dumps(GOOD, ensure_ascii=False), encoding='utf-8')
        data, warnings = load_mapping(str(path))
        assert data == GOOD
        assert warnings == []

    def test_bad_json_raises_mapping_error(self, tmp_path):
        path = tmp_path / MAPPING_FILENAME
        path.write_text('{ broken', encoding='utf-8')
        with pytest.raises(MappingError):
            load_mapping(str(path))

    def test_missing_file_raises_mapping_error(self, tmp_path):
        with pytest.raises(MappingError):
            load_mapping(str(tmp_path / 'nope.json'))

    def test_top_level_not_object_raises(self, tmp_path):
        path = tmp_path / MAPPING_FILENAME
        path.write_text('[1, 2]', encoding='utf-8')
        with pytest.raises(MappingError):
            load_mapping(str(path))

    def test_unknown_keys_warn_but_do_not_fail(self, tmp_path):
        data = dict(GOOD)
        data['future_key'] = {'x': 1}
        data['rules'] = list(GOOD['rules']) + [
            {'glob': 'game/x/*.png', 'kind': 'sprite', 'notes': '手写备注'}]
        path = tmp_path / MAPPING_FILENAME
        path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        got, warnings = load_mapping(str(path))
        assert got == data, '未知键不许被吃掉（人写的信息要留着）'
        text = '\n'.join(warnings)
        assert 'future_key' in text
        assert 'notes' in text

    def test_missing_schema_is_warning(self):
        warnings = validate_mapping({'characters': {}})
        assert any('schema' in w for w in warnings)

    def test_wrong_schema_is_warning(self):
        warnings = validate_mapping({'schema': 'other/9'})
        assert any('schema' in w for w in warnings)

    def test_bad_shift_shapes_warn(self):
        warnings = validate_mapping({
            'schema': MAPPING_SCHEMA,
            'characters': [],
            'rules': {},
            'images': 'x',
            'attributes': [],
        })
        text = '\n'.join(warnings)
        assert 'characters' in text and 'rules' in text
        assert 'images' in text and 'attributes' in text

    def test_absolute_glob_warns(self):
        warnings = validate_mapping({
            'schema': MAPPING_SCHEMA,
            'rules': [{'glob': 'D:/proj/game/x/*.png', 'kind': 'sprite'}],
        })
        assert any('绝对路径' in w for w in warnings)


# ==================================================================
#  缓存（按 mtime + size 失效）
# ==================================================================

class TestMappingCached:
    def test_no_mapping_is_not_an_error(self, tmp_path):
        assert mapping_cached(str(tmp_path)) == ({}, [], '')

    def test_loads_and_reuses(self, tmp_path):
        path = tmp_path / MAPPING_FILENAME
        path.write_text(json.dumps(GOOD, ensure_ascii=False), encoding='utf-8')
        data, warnings, where = mapping_cached(str(tmp_path))
        assert data == GOOD and warnings == [] and where
        # 再问一次（命中缓存）
        assert mapping_cached(str(tmp_path))[0] == GOOD

    def test_broken_mapping_downgrades_to_warning(self, tmp_path):
        path = tmp_path / MAPPING_FILENAME
        path.write_text('{ broken', encoding='utf-8')
        data, warnings, where = mapping_cached(str(tmp_path))
        assert data == {}
        assert warnings and 'JSON' in warnings[0]
        assert where

    def test_mtime_change_refreshes(self, tmp_path):
        path = tmp_path / MAPPING_FILENAME
        path.write_text(json.dumps(GOOD, ensure_ascii=False), encoding='utf-8')
        assert mapping_cached(str(tmp_path))[0]['name'] == '测试映射'
        newer = dict(GOOD)
        newer['name'] = '改过了的映射'
        path.write_text(json.dumps(newer, ensure_ascii=False), encoding='utf-8')
        # 用 utime 保证 mtime 一定变（快速连写在有些文件系统上会撞）
        st = path.stat()
        import os
        os.utime(path, (st.st_atime, st.st_mtime + 5))
        assert mapping_cached(str(tmp_path))[0]['name'] == '改过了的映射'


# ==================================================================
#  解析（映射 → Identity）
# ==================================================================

class TestResolveWithMapping:
    def test_exact_image_by_asset_id(self):
        mapping = {'images': {'refusing': {'kind': 'preview',
                                           'char': 'ling',
                                           'attr': 'refusing'}}}
        got = resolve_with_mapping(mapping, relpath='game/x/refusing.png',
                                   asset_id='refusing')
        assert got is not None
        assert (got.kind, got.tag, got.attrs) == ('preview', 'ling',
                                                  ('refusing',))
        assert got.source == 'mapping'
        assert got.showable is False

    def test_exact_image_by_relpath(self):
        mapping = {'images': {
            'game/images/x.png': {'kind': 'cg'}}}
        got = resolve_with_mapping(mapping, relpath='game/images/x.png',
                                   asset_id='whatever')
        assert (got.kind, got.tag, got.showable) == ('cg', 'cg', True)

    def test_exact_beats_rules(self):
        mapping = {'images': {'game/x/happy.png': {'kind': 'other'}},
                   'rules': [{'glob': 'game/**', 'kind': 'sprite',
                              'char': 'ling'}]}
        got = resolve_with_mapping(mapping, relpath='game/x/happy.png')
        assert got.kind == 'other'

    def test_rules_order(self):
        mapping = {'rules': [
            {'glob': 'game/**', 'kind': 'other'},
            {'glob': 'game/**', 'kind': 'sprite', 'char': 'ling'},
        ]}
        got = resolve_with_mapping(mapping, relpath='game/x.png')
        assert got.kind == 'other'

    def test_rule_glob_star_not_crossing_dirs(self):
        mapping = {'rules': [{'glob': 'game/*.png', 'kind': 'cg'}]}
        assert resolve_with_mapping(mapping, relpath='game/a/b.png') is None
        assert resolve_with_mapping(mapping, relpath='game/a.png') is not None

    def test_rule_double_star_crosses_dirs(self):
        mapping = {'rules': [{'glob': 'game/**/x.png', 'kind': 'cg'}]}
        got = resolve_with_mapping(mapping, relpath='game/a/b/x.png')
        assert got is not None and got.kind == 'cg'

    def test_placeholders_in_glob_and_templates(self):
        mapping = {'rules': [
            {'glob': 'game/art/<tag>/<stem>.png', 'kind': 'sprite',
             'char': '<tag>', 'attr': '<stem>'}]}
        got = resolve_with_mapping(mapping, relpath='game/art/ling/base_happy.png')
        assert (got.tag, got.attrs, got.showable) == ('ling',
                                                      ('base_happy',), True)
        assert got.source == 'mapping-rule'

    def test_backslash_relpath_normalized(self):
        mapping = {'rules': [{'glob': 'game/art/<tag>/*.png',
                              'char': '<tag>', 'kind': 'sprite'}]}
        got = resolve_with_mapping(mapping,
                                   relpath=r'game\art\ling\happy.png')
        assert got is not None and got.tag == 'ling'

    def test_empty_or_none_mapping(self):
        assert resolve_with_mapping(None, relpath='x') is None
        assert resolve_with_mapping({}, relpath='x') is None

    def test_bad_kind_falls_back_to_other(self):
        mapping = {'images': {'x': {'kind': '没这个词'}}}
        got = resolve_with_mapping(mapping, relpath='', asset_id='x')
        assert got.kind == 'other' and got.showable is False

    def test_resolve_image_consults_mapping(self):
        reg = AssetRegistry()
        reg.characters = {'lyj': {'ID': 'lyj', 'Name': '凌', 'ImageTag': 'ling'}}
        row = _row('ling_happy', 'game/images/characters/ling/happy.png',
                   'ling', 'happy')
        mapping = {'images': {'ling_happy': {'kind': 'preview',
                                             'char': 'ling',
                                             'attr': 'happy'}}}
        got = resolve_image(row, known_tags={'ling'},
                            known_chars=known_characters(reg),
                            mapping=mapping)
        assert got.kind == 'preview'


# ==================================================================
#  初稿（从实际目录自动汇总 + round-trip）
# ==================================================================

def _make_foreign_project(tmp_path: Path):
    """
    造一个**布局与真实项目不同**的假项目（通用性判据）：

        <root>/art/full/<tag>/<attr>.png   真立绘
        <root>/art/copies/<tag>/<attr>.png 预览副本（Tag 空）
        <root>/art/avatars/<tag>.png       头像
        <root>/pics/bg/<name>.png          背景
        <root>/pics/cg/<name>.png          CG
    """
    root = tmp_path / 'demo_project'
    files = {
        'art/full/eileen/happy.png': b'full',
        'art/full/eileen/sad.png': b'full',
        'art/full/mary/smile.png': b'full',
        'art/copies/eileen/happy.png': b'copy',
        'art/copies/mary/smile.png': b'copy',
        'art/avatars/eileen.png': b'avatar',
        'pics/bg/room.png': b'bg',
        'pics/bg/roof.jpg': b'bg',
        'pics/cg/event01.png': b'cg',
    }
    for rel, data in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    rows = {
        'eileen_happy': _row('eileen_happy', 'art/full/eileen/happy.png',
                             'eileen', 'happy'),
        'eileen_sad': _row('eileen_sad', 'art/full/eileen/sad.png',
                           'eileen', 'sad'),
        'mary_smile': _row('mary_smile', 'art/full/mary/smile.png',
                           'mary', 'smile'),
        'happy': _row('happy', 'art/copies/eileen/happy.png'),
        'smile': _row('smile', 'art/copies/mary/smile.png'),
        'avatar_eileen': _row('avatar_eileen', 'art/avatars/eileen.png'),
        'bg_room': _row('bg_room', 'pics/bg/room.png', 'bg', 'room'),
        'bg_roof': _row('bg_roof', 'pics/bg/roof.jpg', 'bg', 'roof'),
        'cg_event01': _row('cg_event01', 'pics/cg/event01.png', 'cg',
                           'event01'),
    }
    reg = AssetRegistry()
    reg.images = dict(rows)
    reg.characters = {
        'el': {'ID': 'el', 'Name': 'Eileen', 'ImageTag': 'eileen',
               'Aliases': 'El|小艾'},
        'ma': {'ID': 'ma', 'Name': 'Mary', 'ImageTag': 'mary'},
    }
    return root, rows, reg


class TestDraftMapping:
    def test_draft_from_foreign_layout(self, tmp_path):
        root, rows, reg = _make_foreign_project(tmp_path)
        chars = known_characters(reg)
        draft = draft_mapping(str(root), rows, chars, project_name='Demo')
        assert draft['schema'] == MAPPING_SCHEMA
        assert draft['name'] == 'Demo · 资源映射'
        # characters：显示名/别名/头像（相对路径）
        assert draft['characters']['eileen']['display'] == 'Eileen'
        assert draft['characters']['eileen']['aliases'] == ['El', '小艾']
        assert draft['characters']['eileen']['avatar'] == 'art/avatars/eileen.png'
        # rules：从实际目录汇总（角色目录用**字面量** tag），没有写死任何项目目录名
        globs = [r['glob'] for r in draft['rules']]
        assert 'art/full/eileen/*.png' in globs
        assert 'art/full/mary/*.png' in globs
        assert 'art/copies/eileen/*.png' in globs
        assert 'art/avatars/<stem>.png' in globs
        assert 'pics/bg/*.png' in globs
        assert 'pics/cg/*.png' in globs
        # 头像规则要排在预览/立绘规则前面（先中先得：avatar 目录不能被角色规则吞掉）
        assert globs.index('art/avatars/<stem>.png') < globs.index(
            'art/copies/eileen/*.png')

    def test_draft_roundtrip_through_load(self, tmp_path):
        root, rows, reg = _make_foreign_project(tmp_path)
        draft = draft_mapping(str(root), rows, known_characters(reg))
        # 直接 load_mapping（不落盘也要能读回）
        from storygraph.assets.asset_mapping import validate_mapping
        assert validate_mapping(draft) == []
        path = root / MAPPING_FILENAME
        save_mapping(str(path), draft)
        data, warnings = load_mapping(str(path))
        assert warnings == []
        assert data == draft

    def test_draft_has_no_absolute_paths(self, tmp_path):
        root, rows, reg = _make_foreign_project(tmp_path)
        draft = draft_mapping(str(root), rows, known_characters(reg))
        text = json.dumps(draft, ensure_ascii=False)
        assert str(root) not in text
        assert ':\\' not in text, '映射初稿里不许出现本机绝对路径'
        assert str(Path.home()) not in text

    def test_draft_resolves_correctly(self, tmp_path):
        root, rows, reg = _make_foreign_project(tmp_path)
        draft = draft_mapping(str(root), rows, known_characters(reg))
        sprite = resolve_with_mapping(draft,
                                      relpath='art/full/eileen/happy.png')
        assert (sprite.kind, sprite.tag, sprite.attrs) == ('sprite', 'eileen',
                                                           ('happy',))
        preview = resolve_with_mapping(draft,
                                       relpath='art/copies/eileen/happy.png')
        assert (preview.kind, preview.tag) == ('preview', 'eileen')
        avatar = resolve_with_mapping(draft, relpath='art/avatars/eileen.png')
        assert (avatar.kind, avatar.tag, avatar.showable) == ('avatar',
                                                             'eileen', False)
        bg = resolve_with_mapping(draft, relpath='pics/bg/roof.jpg')
        assert (bg.kind, bg.tag, bg.attrs) == ('bg', 'bg', ('roof',))
        # 规则没覆盖到的其他角色 / 非图像文件不会被张冠李戴
        assert resolve_with_mapping(draft, relpath='art/other/x.png') is None

    def test_draft_does_not_claim_unknown_tag_subdir(self, tmp_path):
        """
        兄弟目录里混着"Tag 不在角色表"的子目录：**不许**被角色目录规则吞掉。

        真机案例：`images/characters/qing/` 的 Tag=`qing` 不在 characters 表里
        （角色表那行 ID 是 `q`）—— 规则只用字面量 tag 目录，不能把它猜成 sprite。
        """
        root = tmp_path / 'mix'
        for rel in ('game/images/characters/eileen/happy.png',
                    'game/images/characters/mystery/normal.png'):
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b'x')
        rows = {
            'eileen_happy': _row('eileen_happy',
                                 'game/images/characters/eileen/happy.png',
                                 'eileen', 'happy'),
            'mystery_normal': _row('mystery_normal',
                                   'game/images/characters/mystery/normal.png',
                                   'mystery', 'normal'),
        }
        reg = AssetRegistry()
        reg.images = dict(rows)
        reg.characters = {'el': {'ID': 'el', 'Name': 'Eileen',
                                 'ImageTag': 'eileen'}}
        draft = draft_mapping(str(root), rows, known_characters(reg))
        assert any(r['glob'].startswith('game/images/characters/eileen')
                   for r in draft['rules'])
        assert resolve_with_mapping(
            draft, relpath='game/images/characters/mystery/normal.png') is None
        assert 'mystery_normal' not in draft['images'], (
            '没有依据的行不许硬编精确覆盖')

    def test_draft_flat_sprite_falls_back_to_exact_images(self, tmp_path):
        """平铺布局（目录里认不出 tag 段）不生成会吞别人的目录规则。"""
        root = tmp_path / 'flat'
        (root / 'pics' / 'flat').mkdir(parents=True)
        rows = {
            'eileen_happy': _row('eileen_happy', 'pics/flat/eileen_happy.png',
                                 'eileen', 'happy'),
            'mary_smile': _row('mary_smile', 'pics/flat/mary_smile.png',
                               'mary', 'smile'),
        }
        reg = AssetRegistry()
        reg.images = dict(rows)
        reg.characters = {
            'el': {'ID': 'el', 'Name': 'Eileen', 'ImageTag': 'eileen'},
            'ma': {'ID': 'ma', 'Name': 'Mary', 'ImageTag': 'mary'},
        }
        draft = draft_mapping(str(root), rows, known_characters(reg))
        assert all('pics/flat/*.png' != r['glob'] for r in draft['rules']), (
            '平铺目录没有 tag 段，规则会张冠李戴 → 必须退回精确覆盖')
        assert draft['images']['eileen_happy']['char'] == 'eileen'
        assert draft['images']['mary_smile']['char'] == 'mary'

    def test_draft_is_generic_no_real_project_literals(self, tmp_path):
        """初稿生成器不许把本项目目录名/项目名写进规则（守卫：改个布局照样成立）。"""
        root, rows, reg = _make_foreign_project(tmp_path)
        draft = draft_mapping(str(root), rows, known_characters(reg))
        text = json.dumps(draft, ensure_ascii=False)
        for forbidden in ('sr_general', 'cc_preview', '千连万理'):
            assert forbidden not in text

    def test_draft_accepts_list_rows_too(self, tmp_path):
        root, rows, reg = _make_foreign_project(tmp_path)
        draft = draft_mapping(str(root), list(rows.values()),
                              known_characters(reg))
        assert [r for r in draft['rules'] if r['kind'] == 'sprite']


# ==================================================================
#  写回（原子 + .bak）
# ==================================================================

class TestSaveMapping:
    def test_creates_file(self, tmp_path):
        path = tmp_path / 'out' / MAPPING_FILENAME
        save_mapping(str(path), GOOD)
        assert path.is_file()
        assert json.loads(path.read_text(encoding='utf-8')) == GOOD

    def test_backs_up_previous_version(self, tmp_path):
        path = tmp_path / MAPPING_FILENAME
        save_mapping(str(path), {'schema': MAPPING_SCHEMA, 'name': 'v1'})
        save_mapping(str(path), {'schema': MAPPING_SCHEMA, 'name': 'v2'})
        backup = path.with_name(path.name + '.bak')
        assert backup.is_file()
        assert json.loads(backup.read_text(encoding='utf-8'))['name'] == 'v1'
        assert json.loads(path.read_text(encoding='utf-8'))['name'] == 'v2'

    def test_no_tmp_left_behind(self, tmp_path):
        path = tmp_path / MAPPING_FILENAME
        save_mapping(str(path), GOOD)
        assert list(tmp_path.glob('*.tmp')) == []

    def test_rejects_non_object(self, tmp_path):
        with pytest.raises(MappingError):
            save_mapping(str(tmp_path / MAPPING_FILENAME), [1, 2])

    def test_writes_utf8_unescaped(self, tmp_path):
        path = tmp_path / MAPPING_FILENAME
        save_mapping(str(path), {'schema': MAPPING_SCHEMA, 'name': '凌跃捷'})
        raw = path.read_text(encoding='utf-8')
        assert '凌跃捷' in raw, '映射是给人看/发群的，不许 unicode 转义成 \\uXXXX'
