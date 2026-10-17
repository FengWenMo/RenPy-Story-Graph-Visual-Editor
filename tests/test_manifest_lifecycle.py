"""
清单生命周期：**建立之后要能看见、能删、能重置**（用户 2026-10-18）
=====================================================================

用户原话：

> 你给了个rule.json建立，却又不给删除重置，并且用户根本看不到自定义模块
> 究竟定义了个什么。

这个文件守三件事：

  ① `clear_attr_groups` 只清「候选分栏」，`statements` / `host_hooks` /
     用户自己的键**原样保留**；
  ② `reset_declarations` 清 `statements` + `attr_groups`，但**不**顺手删掉
     别人的配置；只剩空段时才删文件；
  ③ 两者都**先备份**；坏 JSON 一律拒绝改动（绝不覆盖用户手写的东西）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.story import statement_registry as SR       # noqa: E402

MANIFEST = SR.MANIFEST_NAMES[0]


def _project(tmp_path, data) -> tuple:
    root = tmp_path / 'proj'
    root.mkdir()
    path = root / MANIFEST
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                    encoding='utf-8')
    return root, path


def _declared() -> dict:
    return {
        'statements': [{'name': 'weather', 'label': '天气',
                        'columns': [{'header': '强度', 'param': 'strength'}]}],
        'attr_groups': [{'name': 'outfit', 'label': '服装',
                         'options': ['泳装', '校服']}],
        'host_hooks': {'trust': True},
        'my_note': '用户自己的键，不许丢',
    }


def _backups(root: Path) -> list:
    return list((root / 'storygraph.rules.backup').glob('*.json'))


class TestClearAttrGroups:
    def test_clears_only_the_groups_and_keeps_everything_else(self, tmp_path):
        root, path = _project(tmp_path, _declared())
        before = path.read_text(encoding='utf-8')
        res = SR.clear_attr_groups(root)
        assert res['ok'] and res['removed'], res
        assert res['count'] == 1
        data = json.loads(path.read_text(encoding='utf-8'))
        assert 'attr_groups' not in data
        assert data['statements'][0]['name'] == 'weather'
        assert data['host_hooks'] == {'trust': True}
        assert data['my_note'] == '用户自己的键，不许丢'
        # 备份 = 改之前的整份文件
        backups = _backups(root)
        assert backups and backups[0].read_text(encoding='utf-8') == before
        assert SR.StatementRegistry.for_project(root).attr_groups == []

    def test_deleting_the_file_only_when_nothing_else_is_left(self, tmp_path):
        root, path = _project(tmp_path, {
            'attr_groups': [{'name': 'outfit', 'label': '服装'}],
        })
        res = SR.clear_attr_groups(root)
        assert res['ok'] and res['deleted_file'], res
        assert not path.exists()
        assert _backups(root), '删文件之前必须先备份'

    def test_nothing_to_clear_is_not_an_error(self, tmp_path):
        root, path = _project(tmp_path, {'statements': []})
        res = SR.clear_attr_groups(root)
        assert res['ok'] and not res['removed']
        assert path.exists()

    def test_bad_json_is_refused_and_left_untouched(self, tmp_path):
        root = tmp_path / 'proj'
        root.mkdir()
        path = root / MANIFEST
        path.write_text('{ 这不是 JSON', encoding='utf-8')
        before = path.read_text(encoding='utf-8')
        res = SR.clear_attr_groups(root)
        assert res['ok'] is False
        assert '不是合法 JSON' in res['reason']
        assert path.read_text(encoding='utf-8') == before


class TestResetDeclarations:
    def test_reset_removes_statements_and_groups_but_keeps_other_config(
            self, tmp_path):
        root, path = _project(tmp_path, _declared())
        res = SR.reset_declarations(root)
        assert res['ok'], res
        assert (res['statements'], res['groups']) == (1, 1)
        data = json.loads(path.read_text(encoding='utf-8'))
        assert 'statements' not in data and 'attr_groups' not in data
        assert data['host_hooks'] == {'trust': True}
        assert data['my_note'] == '用户自己的键，不许丢'
        assert _backups(root)

    def test_reset_deletes_the_file_when_only_declarations_existed(self, tmp_path):
        root, path = _project(tmp_path, {
            'statements': [{'name': 'weather'}],
            'attr_groups': [{'name': 'outfit', 'label': '服装'}],
        })
        res = SR.reset_declarations(root)
        assert res['ok'] and res['deleted_file'], res
        assert not path.exists()
        assert _backups(root)

    def test_nothing_declared_is_not_an_error(self, tmp_path):
        root, path = _project(tmp_path, {'host_hooks': {'trust': True}})
        res = SR.reset_declarations(root)
        assert res['ok'] and not res['deleted_file']
        assert json.loads(path.read_text(encoding='utf-8'))['host_hooks']

    def test_bad_json_is_refused(self, tmp_path):
        root = tmp_path / 'proj'
        root.mkdir()
        (root / MANIFEST).write_text('[]', encoding='utf-8')
        res = SR.reset_declarations(root)
        assert res['ok'] is False
        assert '顶层不是对象' in res['reason']


class TestGameManifestIsWrittenInPlace:
    """清单放在 `game/` 下时，读/写必须**是同一份**（2026-10-18 审计 P1-5）。"""

    def _project(self, tmp_path, data):
        root = tmp_path / 'proj'
        (root / 'game').mkdir(parents=True)
        path = root / 'game' / MANIFEST
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                        encoding='utf-8')
        return root, path

    def test_clear_groups_writes_the_game_copy(self, tmp_path):
        root, path = self._project(tmp_path, _declared())
        res = SR.clear_attr_groups(root)
        assert res['ok'] and res['removed'], res
        data = json.loads(path.read_text(encoding='utf-8'))
        assert 'attr_groups' not in data
        assert data['statements'][0]['name'] == 'weather'
        assert not (root / MANIFEST).exists(), '在根目录又造了一份清单'

    def test_reset_and_remove_use_the_game_copy(self, tmp_path):
        root, path = self._project(tmp_path, _declared())
        assert SR.remove_statement(root, 'weather')['ok']
        data = json.loads(path.read_text(encoding='utf-8'))
        assert data.get('statements') == []
        assert not (root / MANIFEST).exists()

        res = SR.reset_declarations(root)
        assert res['ok'], res
        data = json.loads(path.read_text(encoding='utf-8'))
        assert 'attr_groups' not in data and 'statements' not in data
        assert data['host_hooks'] == {'trust': True}
        assert not (root / MANIFEST).exists()

    def test_declare_updates_the_game_copy(self, tmp_path):
        root, path = self._project(tmp_path, {'statements': []})
        res = SR.declare_statement(root, 'weather', label='天气')
        assert res['ok'], res
        assert json.loads(path.read_text(encoding='utf-8'))['statements']
        assert not (root / MANIFEST).exists()

        res = SR.declare_attr_groups(root, [{'name': 'outfit',
                                            'label': '服装'}])
        assert res['ok'], res
        data = json.loads(path.read_text(encoding='utf-8'))
        assert data['attr_groups'][0]['name'] == 'outfit'
        assert not (root / MANIFEST).exists()


class TestDeclareAttrGroupsStillRefusesEmpty:
    def test_split_flag_is_persisted(self, tmp_path):
        root, path = _project(tmp_path, {'statements': []})
        res = SR.declare_attr_groups(
            root, [{'name': 'pose', 'label': '动作',
                    'options': ['stand', 'wave'], 'split': True}])
        assert res['ok'], res
        entry = json.loads(path.read_text(encoding='utf-8'))['attr_groups'][0]
        assert entry['split'] is True, entry
        assert entry['options'] == ['stand', 'wave']

    def test_empty_list_points_at_the_clear_api(self, tmp_path):
        """旧入口保持原语义（拒绝空列表），把"清空"指向明确的 API。"""
        root, _path = _project(tmp_path, _declared())
        res = SR.declare_attr_groups(root, [])
        assert res['ok'] is False
        assert '清空候选分栏' in res['reason']


class TestReplaceAttrGroups:
    def test_replace_really_removes_the_missing_group(self, tmp_path):
        """
        默认 merge 语义做不到"删一栏"：编辑器里删掉 B、只剩 A 时，
        旧 B 必须从清单消失（否则用户看到"删了还在"）。
        """
        root, path = _project(tmp_path, {
            'statements': [{'name': 'weather'}],
            'attr_groups': [
                {'name': 'outfit', 'label': '服装', 'options': ['泳装']},
                {'name': 'pose', 'label': '动作', 'options': ['站立']},
            ],
            'my_note': '保留',
        })
        res = SR.declare_attr_groups(
            root, [{'name': 'outfit', 'label': '服装', 'options': ['校服']}],
            replace=True)
        assert res['ok'], res
        data = json.loads(path.read_text(encoding='utf-8'))
        assert [g['name'] for g in data['attr_groups']] == ['outfit'], data
        assert data['attr_groups'][0]['options'] == ['校服']
        assert data['statements'][0]['name'] == 'weather'
        assert data['my_note'] == '保留'
        assert _backups(root), '替换（会删数据）必须先备份'

    def test_replace_preserves_user_written_extra_fields(self, tmp_path):
        """replace 只换 name/label/options；同名条目里的手写字段不许丢。"""
        root, path = _project(tmp_path, {
            'attr_groups': [{
                'name': 'outfit', 'label': '服装', 'options': ['泳装'],
                'when': 'clothes2', '备注': '手写的，别删',
            }],
        })
        res = SR.declare_attr_groups(
            root, [{'name': 'outfit', 'label': '泳装', 'options': ['校服']}],
            replace=True)
        assert res['ok'], res
        data = json.loads(path.read_text(encoding='utf-8'))
        entry = data['attr_groups'][0]
        assert entry['label'] == '泳装' and entry['options'] == ['校服']
        assert entry['when'] == 'clothes2', entry
        assert entry['备注'] == '手写的，别删', entry

    def test_replace_with_empty_list_does_not_clear_without_explicit_flag(
            self, tmp_path):
        """
        事故判据（2026-10-18）：主 OK 勾了"把分栏一起写进清单"、而当前没有
        可写分栏时，**不许**把已有分组整段删掉。清空必须显式 `clear=True`。
        """
        root, path = _project(tmp_path, {
            'statements': [{'name': 'weather'}],
            'attr_groups': [{'name': 'outfit', 'label': '服装'}],
        })
        before = path.read_text(encoding='utf-8')
        res = SR.declare_attr_groups(root, [], replace=True)
        assert res['ok'] is False, res
        assert '清空候选分栏' in res['reason']
        assert path.read_text(encoding='utf-8') == before, '空列表把分栏清掉了'

    def test_replace_with_empty_list_clears_only_with_explicit_flag(self, tmp_path):
        root, path = _project(tmp_path, {
            'statements': [{'name': 'weather'}],
            'attr_groups': [{'name': 'outfit', 'label': '服装'}],
        })
        res = SR.declare_attr_groups(root, [], replace=True, clear=True)
        assert res['ok'] and res.get('action') == 'cleared', res
        data = json.loads(path.read_text(encoding='utf-8'))
        assert 'attr_groups' not in data
        assert data['statements'][0]['name'] == 'weather'
        assert _backups(root)
