# -*- coding: utf-8 -*-
"""
**把声明带走**：`ext/archive.py` 的判据（2026-11-07 第七轮）

用户的原话（设计文档 §15.10 第 1 条）：

> 平台要能跨项目用，现在只能手工抄 `ext.json`。

所以这里钉四件事：

  ① 导出的档案只装**形状真的住在文件里**的声明（跳过内置 / 只带开关的空壳），
     而且**不带本机路径**；
  ② 导回去能一条不差地装进另一个项目，再 `save()` 落盘、重装还一样；
  ③ 同名但形状不同 → **不覆盖**，把冲突列出来（`replace` / `keep` 才动手）；
  ④ 档案坏一处 → 整份不导入（"要么全对、要么完全不动"）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.ext import archive as A  # noqa: E402
from storygraph.ext.registry import MANIFEST, ExtensionRegistry  # noqa: E402
from storygraph.ext.schema import EXT_SCHEMA  # noqa: E402


def _write_ext(root: Path, extensions) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / 'game').mkdir(exist_ok=True)
    path = root / MANIFEST
    path.write_text(json.dumps({'schema': EXT_SCHEMA, 'extensions': extensions},
                               ensure_ascii=False), encoding='utf-8')
    return path


def _wipe(ext):
    return {'id': 'my_wipe', 'kind': 'call', 'label': '擦除',
            'fields': [{'key': '方向', 'kind': 'choice',
                        'choices': ['left', 'right'], 'position': 0},
                       {'key': '秒数', 'kind': 'number', 'position': 1}]}


def _cue(ext):
    return {'id': 'mycue', 'kind': 'statement', 'label': '我的写法',
            'fields': [{'key': '强度', 'kind': 'number',
                        'placeholder': '1~10'}]}


class TestExport:
    def test_only_portable_sources_are_carried(self, tmp_path):
        root = tmp_path / 'p'
        _write_ext(root, [_wipe(root), _cue(root)])
        reg = ExtensionRegistry.for_project(root)
        # 只带开关的记录（`override`）与内置都不是"声明"
        reg.set_enabled('renpy.transition', False)
        arch = reg.export_archive(name='包')
        ids = [e['id'] for e in arch['extensions']]
        assert ids == ['my_wipe', 'mycue'], ids
        assert arch['schema'] == A.ARCHIVE_SCHEMA
        assert arch['name'] == '包'
        assert arch['tool'].startswith('storygraph')

    def test_skipped_ones_are_told_not_silently_dropped(self, tmp_path):
        root = tmp_path / 'p'
        _write_ext(root, [_cue(root)])
        reg = ExtensionRegistry.for_project(root)
        reg.set_enabled('renpy.transition', False)          # 变成 override 记录
        skipped = {s['id']: s['why'] for s in
                   A.describe_skipped(reg.all())}
        assert 'renpy.transition' in skipped
        assert '内置' in skipped['renpy.transition'] or \
               '别处' in skipped['renpy.transition']

    def test_no_absolute_paths_in_the_archive(self, tmp_path):
        root = tmp_path / 'p'
        _write_ext(root, [_cue(root)])
        reg = ExtensionRegistry.for_project(root)
        text = json.dumps(reg.export_archive(name='x'), ensure_ascii=False)
        assert str(tmp_path) not in text
        assert 'C:\\' not in text and 'D:\\' not in text

    def test_plugin_declarations_are_portable(self, tmp_path):
        root = tmp_path / 'p'
        (root / 'game').mkdir(parents=True)
        (root / 'extensions').mkdir()
        (root / 'extensions' / 'zap.json').write_text(json.dumps({
            'extensions': [_cue(root)]}, ensure_ascii=False), encoding='utf-8')
        reg = ExtensionRegistry.for_project(root)
        ids = [e['id'] for e in reg.export_archive()['extensions']]
        assert ids == ['mycue'], ids


class TestImport:
    def test_round_trip_into_another_project(self, tmp_path):
        src = tmp_path / 'src'
        _write_ext(src, [_wipe(src), _cue(src)])
        arch = ExtensionRegistry.for_project(src).export_archive(name='包')

        dst = tmp_path / 'dst'
        (dst / 'game').mkdir(parents=True)
        reg = ExtensionRegistry.for_project(dst)
        res = reg.import_archive(arch)
        assert res['ok'] and res['added'] == ['my_wipe', 'mycue'], res

        saved = reg.save()
        assert saved['ok'], saved
        again = ExtensionRegistry.for_project(dst)
        assert again.get('my_wipe').label == '擦除'
        assert [f.key for f in again.get('my_wipe').field_specs()] == ['方向', '秒数']
        # 提示也跟着走（第七轮新串起来的那条链）
        assert again.get('mycue').field_specs()[0].placeholder == '1~10'

    def test_same_id_different_shape_is_a_conflict_and_nothing_is_written(
            self, tmp_path):
        src = tmp_path / 'src'
        _write_ext(src, [_cue(src)])
        arch = ExtensionRegistry.for_project(src).export_archive()

        dst = tmp_path / 'dst'
        other = dict(_cue(dst))
        other['label'] = '项目里原来那条'
        _write_ext(dst, [other])
        reg = ExtensionRegistry.for_project(dst)
        res = reg.import_archive(arch)                     # 默认 abort
        assert not res['ok'] and res['conflict']
        assert res['conflicts'] == ['mycue']
        assert reg.get('mycue').label == '项目里原来那条'    # 内存里没动

    def test_replace_wins_and_keep_keeps(self, tmp_path):
        src = tmp_path / 'src'
        _write_ext(src, [_cue(src)])
        arch = ExtensionRegistry.for_project(src).export_archive()

        dst = tmp_path / 'dst'
        other = dict(_cue(dst))
        other['label'] = '旧'
        _write_ext(dst, [other])
        reg = ExtensionRegistry.for_project(dst)
        res = reg.import_archive(arch, on_conflict='replace')
        assert res['ok'] and res['replaced'] == ['mycue']
        assert reg.get('mycue').label == '我的写法'

        dst2 = tmp_path / 'dst2'
        _write_ext(dst2, [dict(_cue(dst2), label='旧')])
        reg2 = ExtensionRegistry.for_project(dst2)
        res2 = reg2.import_archive(arch, on_conflict='keep')
        assert res2['ok'] and res2['same'] == [] and res2['replaced'] == []
        assert reg2.get('mycue').label == '旧'

    def test_identical_entry_is_a_noop(self, tmp_path):
        src = tmp_path / 'src'
        _write_ext(src, [_cue(src)])
        arch = ExtensionRegistry.for_project(src).export_archive()
        dst = tmp_path / 'dst'
        _write_ext(dst, [_cue(dst)])
        reg = ExtensionRegistry.for_project(dst)
        res = reg.import_archive(arch)
        assert res['ok'] and res['same'] == ['mycue'] and res['added'] == []

    def test_bad_archive_imports_nothing(self, tmp_path):
        dst = tmp_path / 'dst'
        (dst / 'game').mkdir(parents=True)
        reg = ExtensionRegistry.for_project(dst)
        bad = {'schema': A.ARCHIVE_SCHEMA, 'extensions': [
            {'id': 'ok_one', 'kind': 'statement'},
            {'kind': 'statement'},                          # 没有 id
            {'id': 'ok_one', 'kind': 'statement'},          # 重复 id
            {'id': 'weird', 'kind': 'nonsense'},            # 不认识的 kind
        ]}
        res = reg.import_archive(bad)
        assert not res['ok']
        assert len(res['problems']) == 3, res['problems']
        assert reg.get('ok_one') is None                    # 一条都没进

    def test_unknown_archive_schema_is_refused(self, tmp_path):
        reg = ExtensionRegistry.for_project(tmp_path / 'dst')
        res = reg.import_archive({'schema': 'future/9', 'extensions': []})
        assert not res['ok'] and '不认识' in res['problems'][0]

    def test_broken_json_text_is_refused(self, tmp_path):
        reg = ExtensionRegistry.for_project(tmp_path / 'dst')
        res = reg.import_archive('{not json')
        assert not res['ok'] and 'JSON' in res['problems'][0]


class TestFileHelpers:
    def test_dump_and_read_round_trip(self, tmp_path):
        src = tmp_path / 'src'
        _write_ext(src, [_cue(src)])
        arch = ExtensionRegistry.for_project(src).export_archive(name='n')
        path = tmp_path / 'pkg.sg-ext.json'
        path.write_text(A.dump_archive(arch), encoding='utf-8')
        incoming, meta, problems = A.read_file(path)
        assert problems == []
        assert meta['name'] == 'n'
        assert [e['id'] for e in incoming] == ['mycue']

    def test_half_written_file_is_reported(self, tmp_path):
        path = tmp_path / 'x.sg-ext.json'
        path.write_text('{"schema": ', encoding='utf-8')
        incoming, meta, problems = A.read_file(path)
        assert incoming == [] and problems


# ---------------------------------------------------------------------------
#  窗口：两个按钮（导出 / 导入）真的落到文件和项目里
# ---------------------------------------------------------------------------
#
# ⚠ 第七轮这一节判的是"`export_archive()` / `import_archive()` 干活对不对"，
#   **没有一条真的去点按钮** —— 而按钮连的是 `self._export_archive()`
#   （多一个下划线，类里不存在）。于是这一节全绿、用户点下去毫无反应。
#   第九轮补上真点击（下面两条），源码层面另有 `test_dead_slots.py` 守着。

class TestWindow:
    @staticmethod
    def _dialog(root):
        from storygraph.ui_qt.dialog_extensions import ExtensionDialog
        return ExtensionDialog(None, project_root=root)

    @staticmethod
    def _button(dlg, text):
        from PySide6.QtWidgets import QPushButton
        for btn in dlg.findChildren(QPushButton):
            if btn.text() == text:
                return btn
        raise AssertionError(f'窗口上没有「{text}」按钮：'
                             f'{[b.text() for b in dlg.findChildren(QPushButton)]}')

    def test_export_button_really_writes_the_file(
            self, qapp, tmp_path, monkeypatch):
        """**点按钮**（不是调方法）：第九轮之前这里是死按钮。"""
        root = tmp_path / 'p'
        _write_ext(root, [_cue(root)])
        dlg = self._dialog(root)
        out = tmp_path / 'pkg.sg-ext.json'
        monkeypatch.setattr(dlg, '_ask_export_path', lambda: str(out))
        btn = self._button(dlg, '导出…')
        assert btn.isEnabled()
        btn.click()                     # 槽里抛异常只会打到 stderr
        assert out.is_file(), '「导出…」点下去什么都没发生'
        assert '已导出 1 条' in dlg.status.text()
        assert [e['id'] for e in
                json.loads(out.read_text(encoding='utf-8'))['extensions']] \
            == ['mycue']

    def test_import_button_really_lands_in_the_project(
            self, qapp, tmp_path, monkeypatch):
        """**点按钮**：档案要真的进到项目的 `storygraph.ext.json` 里。"""
        src = tmp_path / 'src'
        _write_ext(src, [_wipe(src)])
        arch = ExtensionRegistry.for_project(src).export_archive(name='包')
        pkg = tmp_path / 'pkg.sg-ext.json'
        pkg.write_text(A.dump_archive(arch), encoding='utf-8')

        dst = tmp_path / 'dst'
        (dst / 'game').mkdir(parents=True)
        dlg = self._dialog(dst)
        monkeypatch.setattr(dlg, '_ask_import_path', lambda: str(pkg))
        btn = self._button(dlg, '导入…')
        btn.click()
        on_disk = json.loads((dst / MANIFEST).read_text(encoding='utf-8'))
        assert [e['id'] for e in on_disk['extensions']] == ['my_wipe']
        assert '导入：新增 1 条' in dlg.status.text()

    def test_export_writes_the_file_and_says_what_it_left_out(
            self, qapp, tmp_path, monkeypatch):
        root = tmp_path / 'p'
        _write_ext(root, [_cue(root)])
        dlg = self._dialog(root)
        dlg._reg.set_enabled('renpy.transition', False)     # 变成 override
        out = tmp_path / 'pkg.sg-ext.json'
        monkeypatch.setattr(dlg, '_ask_export_path', lambda: str(out))

        res = dlg.export_archive()
        assert res['ok'] and out.is_file(), res
        data = json.loads(out.read_text(encoding='utf-8'))
        assert [e['id'] for e in data['extensions']] == ['mycue']
        assert '已导出 1 条' in dlg.status.text()
        assert '没带' in dlg.status.text()                  # 跳过的要说出来

    def test_export_with_nothing_portable_says_so(self, qapp, tmp_path):
        root = tmp_path / 'p'
        (root / 'game').mkdir(parents=True)
        dlg = self._dialog(root)
        res = dlg.export_archive()
        assert not res['ok'] and '没有可带走' in dlg.status.text()

    def test_import_lands_in_the_project_file(self, qapp, tmp_path):
        src = tmp_path / 'src'
        _write_ext(src, [_wipe(src)])
        arch = ExtensionRegistry.for_project(src).export_archive(name='包')
        pkg = tmp_path / 'pkg.sg-ext.json'
        pkg.write_text(A.dump_archive(arch), encoding='utf-8')

        dst = tmp_path / 'dst'
        (dst / 'game').mkdir(parents=True)
        dlg = self._dialog(dst)
        res = dlg.import_archive(str(pkg))
        assert res['ok'] and res['saved'], res
        on_disk = json.loads((dst / MANIFEST).read_text(encoding='utf-8'))
        assert [e['id'] for e in on_disk['extensions']] == ['my_wipe']
        assert '导入：新增 1 条' in dlg.status.text()
        # 树的缓存也要照到新条目
        assert ExtensionRegistry.for_project(dst).get('my_wipe') is not None

    def test_import_of_a_broken_file_changes_nothing(self, qapp, tmp_path,
                                                     monkeypatch):
        dst = tmp_path / 'dst'
        (dst / 'game').mkdir(parents=True)
        dlg = self._dialog(dst)
        warned = []
        monkeypatch.setattr(dlg, '_warn',
                            lambda title, text: warned.append((title, text)))
        pkg = tmp_path / 'bad.sg-ext.json'
        pkg.write_text('{"schema": "storygraph.ext.archive/1", '
                       '"extensions": [{"kind": "statement"}]}', encoding='utf-8')
        res = dlg.import_archive(str(pkg))
        assert not res['ok']
        assert warned and '没有导入' in warned[0][1]
        assert not (dst / MANIFEST).exists()                # 一个文件都没建
        assert '没导入' in dlg.status.text()
