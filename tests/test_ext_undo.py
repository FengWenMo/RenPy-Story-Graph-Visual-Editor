# -*- coding: utf-8 -*-
"""
**撤销刚才那次保存**：`storygraph.ext.json` 的退路（2026-11-07 第九轮）

用户 2026-11-07 的话（设计文档 §16.8 第 4 条）：

> 导入 + 保存之后没有 `undo`（要退就靠 `save()` 的 `.bak`）。

`.bak` 是**文件级**退路：要人自己进目录、认出哪一份、再手工覆盖回去。
一份档案导错，动的是几十条声明 —— 那是"整份文件换了个样子"，
不是"某一条改错了"，所以这里做的是**整份恢复**：

    `save()` → 顺带把"保存之前的原话"交出来（`undo`）
    `restore_manifest(undo)` → 锁里比对：磁盘上还是**我写下去的那份**才放回去

三条底线，每条都有判据：

  ① **逐字节**：CRLF 文件恢复回去还是 CRLF（不是"内容差不多"）；
  ② **不覆盖别人**：保存之后文件又被谁改过 / 删了 → 一个字都不写，如实说；
  ③ **也拿锁**：撤销与"别人的保存"撞在一起时排队，不许读→写敞着。

另外 `before=None`（保存前本来没有这份文件）→ 撤销 = **把它删掉**，
与第八轮"清单空了就删文件"同一条口径：不留空清单当遗迹。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.ext import registry as ER  # noqa: E402
from storygraph.ext import schema as ES  # noqa: E402

ALPHA = {'id': 'alpha', 'kind': 'statement', 'label': '甲',
         'fields': [{'key': '方向', 'kind': 'keyword'}]}
BETA = {'id': 'beta', 'kind': 'statement', 'label': '乙',
        'fields': [{'key': '秒数', 'kind': 'number'}]}


def _project(tmp_path: Path, ext=None, *, crlf: bool = False) -> Path:
    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True)
    if ext is not None:
        text = json.dumps({'schema': ES.EXT_SCHEMA, 'extensions': list(ext)},
                          ensure_ascii=False, indent=2)
        if crlf:
            text = text.replace('\n', '\r\n')
        (root / ER.MANIFEST).write_bytes(text.encode('utf-8'))
    return root


def _bytes(root) -> bytes:
    return (Path(root) / ER.MANIFEST).read_bytes()


def _reg(root):
    return ER.ExtensionRegistry.for_project(root)


def _save_one(root, raw) -> dict:
    """往项目里塞一条再存盘（返回 `save()` 的结果）。"""
    reg = _reg(root)
    reg.upsert(ES.parse_extension(raw))
    return reg.save(root)


# ---------------------------------------------------------------------------
#  一、`save()` 交出来的撤销材料
# ---------------------------------------------------------------------------

class TestUndoMaterial:
    def test_save_hands_back_the_text_that_was_there_before(self, tmp_path):
        root = _project(tmp_path, [ALPHA])
        before = _bytes(root)
        res = _save_one(root, BETA)
        assert res['ok'], res
        undo = res['undo']
        assert undo['before'].encode('utf-8') == before
        assert undo['after'] == _bytes(root).decode('utf-8')   # 就是我写下去的那份
        assert Path(undo['path']) == root / ER.MANIFEST

    def test_before_is_none_when_the_file_did_not_exist(self, tmp_path):
        root = _project(tmp_path)
        res = _save_one(root, ALPHA)
        assert res['ok'], res
        assert res['undo']['before'] is None

    def test_an_unchanged_save_has_nothing_to_undo(self, tmp_path):
        """连点两次保存：第二次一个字节都没改 → 没有可撤销的。"""
        root = _project(tmp_path)
        assert _save_one(root, ALPHA)['ok']
        again = _reg(root).save(root)
        assert again['ok'] and again['undo'] is None, again

    def test_the_second_save_undoes_back_to_the_first(self, tmp_path):
        root = _project(tmp_path, [ALPHA])
        assert _save_one(root, BETA)['ok']
        after_first = _bytes(root)
        reg = _reg(root)
        reg.get('alpha').label = '甲改'          # 再改一条
        second = reg.save(root)
        assert second['ok'], second
        assert second['undo']['before'].encode('utf-8') == after_first


# ---------------------------------------------------------------------------
#  二、恢复：逐字节、删文件、拒绝覆盖别人
# ---------------------------------------------------------------------------

class TestRestore:
    def test_restore_brings_back_the_exact_bytes(self, tmp_path):
        """
        CRLF 的文件恢复回去还是 CRLF。

        （用 `read_text()` 读、`write_text_atomic()` 写会悄悄换成 LF ——
        用户拿 `git diff` 一眼就看见满屏改动，那不叫"恢复原样"。）
        """
        root = _project(tmp_path, [ALPHA], crlf=True)
        original = _bytes(root)
        assert b'\r\n' in original
        res = _save_one(root, BETA)
        assert res['ok'] and res['undo']['before'] is not None
        assert b'\r\n' not in _bytes(root)        # 我写下去的是 LF
        back = ER.restore_manifest(res['undo'])
        assert back['ok'], back
        assert _bytes(root) == original, '恢复出来的不是原来那几个字节'

    def test_restore_deletes_a_file_that_did_not_exist_before(self, tmp_path):
        root = _project(tmp_path)
        res = _save_one(root, ALPHA)
        assert res['ok'] and (root / ER.MANIFEST).is_file()
        back = ER.restore_manifest(res['undo'])
        assert back['ok'] and back['deleted'] is True, back
        assert not (root / ER.MANIFEST).exists(), '撤销之后不该留一份空清单'

    def test_restore_refuses_when_someone_changed_the_file_afterwards(
            self, tmp_path):
        root = _project(tmp_path, [ALPHA])
        res = _save_one(root, BETA)
        assert res['ok']
        # 别人（另一个窗口 / 另一个进程）在这之后又改了一次
        later = _save_one(root, {'id': 'gamma', 'kind': 'statement',
                                 'label': '丙'})
        assert later['ok']
        now = _bytes(root)
        back = ER.restore_manifest(res['undo'])
        assert back['ok'] is False and back['changed'] is True, back
        assert '被改过' in back['reason'], back
        assert _bytes(root) == now, '拒绝撤销的时候改了文件'

    def test_restore_refuses_when_the_file_was_deleted(self, tmp_path):
        """别人把文件删了（他的决定）→ 不许拿旧文本把它变回来。"""
        root = _project(tmp_path, [ALPHA])
        res = _save_one(root, BETA)
        assert res['ok']
        (root / ER.MANIFEST).unlink()
        back = ER.restore_manifest(res['undo'])
        assert back['ok'] is False and back['changed'] is True, back
        assert not (root / ER.MANIFEST).exists()

    def test_restore_without_material_says_so(self):
        assert ER.restore_manifest(None)['ok'] is False
        assert ER.restore_manifest({})['ok'] is False
        assert '没有可撤销' in ER.restore_manifest(None)['reason']


# ---------------------------------------------------------------------------
#  三、撤销也在锁里（真子进程）
# ---------------------------------------------------------------------------

HOLD_CHILD = textwrap.dedent('''
    import sys, time
    sys.path.insert(0, {root!r})
    from pathlib import Path
    from storygraph.core.filelock import file_lock
    with file_lock(Path(sys.argv[1]), timeout=15):
        print('held', flush=True)
        time.sleep(float(sys.argv[2]))
''')

UNDO_CHILD = textwrap.dedent('''
    import json, sys, time
    sys.path.insert(0, {root!r})
    from pathlib import Path
    from storygraph.ext.registry import restore_manifest
    undo = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    start = float(sys.argv[2])
    while time.time() < start:
        time.sleep(0.005)
    res = restore_manifest(undo, lock_timeout=20.0)
    print(res.get('ok'), res.get('deleted'), res.get('changed'), res.get('locked'),
          flush=True)
''')


def _child_env() -> dict:
    return dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONPATH=str(ROOT))


def _write_child(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / name
    path.write_text(body.replace('{root!r}', repr(str(ROOT))), encoding='utf-8')
    return path


class TestRestoreRespectsTheLock:
    def test_restore_waits_and_refuses_instead_of_forcing_it(self, tmp_path):
        """别人正握着锁 → 到点就**拒绝**（如实说"稍后再试"），不是硬写。"""
        root = _project(tmp_path, [ALPHA])
        res = _save_one(root, BETA)
        assert res['ok']
        now = _bytes(root)
        script = _write_child(tmp_path, 'hold.py', HOLD_CHILD)
        child = subprocess.Popen(
            [sys.executable, str(script), str(root / ER.MANIFEST), '1.2'],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            encoding='utf-8', env=_child_env())
        try:
            assert child.stdout.readline().strip() == 'held'
            back = ER.restore_manifest(res['undo'], lock_timeout=0.2)
        finally:
            child.communicate(timeout=60)
        assert back['ok'] is False and back['locked'] is True, back
        assert '另一个进程' in back['reason'], back
        assert _bytes(root) == now, '拿不到锁的时候改了文件'

    def test_two_processes_undoing_the_same_save_exactly_one_wins(
            self, tmp_path):
        """
        两个进程同刻撤销同一份材料 → **恰好一个成功**。

        另一个必然看到"文件已经不是我写下去的那份"（它看到的是 before）——
        比对在锁里，所以不会出现两个都成功、也不会两个都失败。
        """
        root = _project(tmp_path, [ALPHA])
        res = _save_one(root, BETA)
        assert res['ok']
        original = res['undo']['before'].encode('utf-8')
        payload = tmp_path / 'undo.json'
        payload.write_text(json.dumps(res['undo'], ensure_ascii=False),
                           encoding='utf-8')
        script = _write_child(tmp_path, 'undo.py', UNDO_CHILD)
        start = time.time() + 1.5
        procs = [subprocess.Popen(
            [sys.executable, str(script), str(payload), str(start)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            encoding='utf-8', env=_child_env()) for _ in range(2)]
        outs = [p.communicate(timeout=90)[0].strip() for p in procs]
        wins = [o for o in outs if o.startswith('True')]
        loses = [o for o in outs if o.startswith('False')]
        assert len(wins) == 1 and len(loses) == 1, outs
        assert wins[0] == 'True False False False', outs   # ok / 没删文件
        assert loses[0] == 'False False True False', outs  # "文件已经被改过"
        assert _bytes(root) == original, '撤销之后文件不是原来那份'


# ---------------------------------------------------------------------------
#  四、窗口上那个按钮（用户真正会按的东西）
# ---------------------------------------------------------------------------

class TestWindow:
    @staticmethod
    def _dialog(root):
        from storygraph.ui_qt.dialog_extensions import ExtensionDialog
        return ExtensionDialog(None, project_root=root)

    @staticmethod
    def _archive(tmp_path: Path, ext) -> Path:
        from storygraph.ext import archive as A
        src = tmp_path / 'src'
        (src / 'game').mkdir(parents=True)
        (src / ER.MANIFEST).write_text(
            json.dumps({'schema': ES.EXT_SCHEMA, 'extensions': list(ext)},
                       ensure_ascii=False), encoding='utf-8')
        arch = _reg(src).export_archive(name='包')
        pkg = tmp_path / 'pkg.sg-ext.json'
        pkg.write_text(A.dump_archive(arch), encoding='utf-8')
        return pkg

    def test_undo_button_is_dead_until_a_save_happens(self, qapp, tmp_path):
        dlg = self._dialog(_project(tmp_path, [ALPHA]))
        assert not dlg.btn_undo.isEnabled()
        assert dlg._undo_save()['ok'] is False
        assert '没有可撤销' in dlg.status.text()

    def test_import_then_the_button_puts_the_file_back(self, qapp, tmp_path):
        root = _project(tmp_path, [ALPHA])
        original = _bytes(root)
        pkg = self._archive(tmp_path, [BETA])
        dlg = self._dialog(root)
        assert dlg.import_archive(str(pkg))['saved'] is True
        assert '撤销上次保存' in dlg.status.text()      # 退路要在明面上
        assert dlg.btn_undo.isEnabled()
        assert _bytes(root) != original

        dlg.btn_undo.click()                            # **点按钮**
        assert _bytes(root) == original, '撤销之后文件不是导入前那一份'
        assert '已撤销上次保存' in dlg.status.text()
        assert not dlg.btn_undo.isEnabled()
        assert dlg._reg.get('beta') is None              # 树/内存也回到了没导入

    def test_undo_after_someone_else_edited_refuses_and_greys_out(
            self, qapp, tmp_path):
        root = _project(tmp_path, [ALPHA])
        pkg = self._archive(tmp_path, [BETA])
        dlg = self._dialog(root)
        assert dlg.import_archive(str(pkg))['saved'] is True
        path = root / ER.MANIFEST
        path.write_text(path.read_text(encoding='utf-8') + '\n',
                        encoding='utf-8')                # 别人又动了一下
        now = _bytes(root)
        dlg.btn_undo.click()
        assert _bytes(root) == now, '拒绝撤销的时候改了文件'
        assert '没撤销' in dlg.status.text()
        assert not dlg.btn_undo.isEnabled(), (
            '文件已经被别人改过 —— 这份材料再也不会对上，按钮该灰掉')
