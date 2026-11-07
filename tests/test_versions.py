# -*- coding: utf-8 -*-
"""
**历史版本**：`storygraph.ext.json` 的那几代自动备份（2026-11-07 第十轮）

第九轮给了扩展窗口一条退路（「撤销上次保存」），探针
`.pip-tmp/probe_r10_versions.py` 量到它有两个缺口：

  ① **关窗就没了** —— 材料只在窗口内存里；关掉扩展窗口，"刚导错一份档案"
     就只剩"去资源管理器翻 `.bak`"；
  ② 而同目录的 `.bak` 虽然**真的**是每次保存前的逐字节原话，界面上一处
     都读不到；人真去整份复制回来，**复制不产生备份** —— 刚退掉的那一版
     当场没了（单向门）。

这一轮补的是"磁盘上那几代都能翻、能预览、能放回"，顺带修掉一处：

  · `save()` 从前**内容一模一样也照样写盘**（白烧一代备份、mtime 跳）；
    现在一个字节没变就不碰文件。

判据分五组：备份世代 / 放回 / 不覆盖别人 / 窗口（含**关窗重开**）/ 界面。
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

from storygraph.core import versions as V  # noqa: E402
from storygraph.ext import registry as ER  # noqa: E402
from storygraph.ext import schema as ES  # noqa: E402
from storygraph.ext.schema import parse_extension  # noqa: E402

ALPHA = {'id': 'alpha', 'kind': 'statement', 'label': '甲',
         'fields': [{'key': '方向', 'kind': 'keyword'}]}
BETA = {'id': 'beta', 'kind': 'statement', 'label': '乙'}
GAMMA = {'id': 'gamma', 'kind': 'statement', 'label': '丙'}


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


def _manifest(root) -> Path:
    return Path(root) / ER.MANIFEST


def _bytes(root) -> bytes:
    return _manifest(root).read_bytes()


def _reg(root):
    return ER.ExtensionRegistry.for_project(root)


def _save(root, *raws) -> dict:
    """往项目里加/改几条，然后存盘（每次都会留下一代备份）。"""
    reg = _reg(root)
    for raw in raws:
        reg.upsert(parse_extension(raw))
    return reg.save(root)


def _grow(root) -> list:
    """连存三次（甲 / 甲乙 / 甲乙丙）→ 磁盘上两代备份。"""
    assert _save(root, ALPHA)['ok']
    assert _save(root, BETA)['ok']
    assert _save(root, GAMMA)['ok']
    return V.backups_of(_manifest(root))


# ---------------------------------------------------------------------------
#  一、磁盘上有哪几代（名字、顺序、内容）
# ---------------------------------------------------------------------------

class TestGenerations:
    def test_each_generation_is_the_exact_text_from_before_that_save(self, tmp_path):
        root = _project(tmp_path)
        assert _save(root, ALPHA)['ok']
        first = _bytes(root)                      # 第 1 次保存落盘的那几个字节
        second = _save(root, BETA)
        assert second['ok']
        third = _save(root, GAMMA)
        assert third['ok']

        vers = V.backups_of(_manifest(root))
        assert len(vers) == 2, [v.name for v in vers]
        # 第一次保存时文件还不存在 → 没有"上一版"可备份（不是漏了）
        assert V.read_text_exact(vers[0].path) == third['undo']['before']
        assert V.read_text_exact(vers[1].path) == second['undo']['before']
        assert vers[1].path.read_bytes() == first, '最老那一代不是第 1 次的字节'

    def test_newest_first_and_the_fixed_name_is_the_oldest_generation(self, tmp_path):
        root = _project(tmp_path)
        vers = _grow(root)
        assert [v.kind for v in vers] == [V.KIND_STAMPED, V.KIND_FIXED], \
            [v.name for v in vers]
        assert vers[-1].name == ER.MANIFEST + '.bak'
        # 最老那份 = 只有甲的那一刻
        oldest = V.read_text_exact(vers[-1].path)
        assert [e['id'] for e in json.loads(oldest)['extensions']] == ['alpha']
        newest = V.read_text_exact(vers[0].path)
        assert [e['id'] for e in json.loads(newest)['extensions']] == ['alpha', 'beta']

    def test_a_recreated_fixed_backup_is_newer_than_older_stamped_ones(
            self, tmp_path):
        """
        顺序按 **mtime** 排，不是按名字。

        为什么这条必须单独钉：`.bak` 被人删掉之后再存一次，固定名会被**重新**
        占用 —— 于是"老名字"（固定名）反而是**最新**的一代，而带时间戳那几份
        才是旧的。按名字排会把顺序弄反，界面上第一行就变成"更早的样子"。
        """
        root = _project(tmp_path)
        assert _grow(root)
        _manifest(root).with_name(ER.MANIFEST + '.bak').unlink()
        assert _save(root, {'id': 'delta', 'kind': 'statement', 'label': '丁'})['ok']
        vers = V.backups_of(_manifest(root))
        assert [v.kind for v in vers] == [V.KIND_FIXED, V.KIND_STAMPED], \
            [v.name for v in vers]
        newest = json.loads(V.read_text_exact(vers[0].path))
        assert [e['id'] for e in newest['extensions']] == ['alpha', 'beta', 'gamma']

    def test_a_name_with_brackets_does_not_pick_up_someone_elses_backup(
            self, tmp_path):
        """
        文件名里带 `[` `*` 时，`glob` 会把它当通配符 —— 那会把**别人的**
        文件当成这一份的备份列出来（`x[1].json` 的 `[1]` 正好匹配 `x1.json`）。
        """
        root = tmp_path / 'proj'
        root.mkdir()
        target = root / 'x[1].json'
        target.write_text('{}', encoding='utf-8')
        decoy = root / 'x1.json.bak-20260101-000000-000000-0001'
        decoy.write_text('{"别的东西": 1}', encoding='utf-8')
        assert V.backups_of(target) == [], '把别人的备份当成自己的了'

    def test_limit_and_empty(self, tmp_path):
        root = _project(tmp_path)
        vers = _grow(root)
        assert len(V.backups_of(_manifest(root), limit=1)) == 1
        assert V.backups_of(_manifest(root) / 'nope.json') == []
        fresh = _project(tmp_path / 'other', [ALPHA])
        assert V.backups_of(_manifest(fresh)) == []

    def test_stamp_text_is_a_human_moment(self):
        assert len(V.stamp_text(1794067629.85)) >= 16
        assert '?' not in V.stamp_text(1794067629.85)


# ---------------------------------------------------------------------------
#  二、放回：逐字节、能再放回、一样的就不写
# ---------------------------------------------------------------------------

class TestRestore:
    def test_restoring_a_generation_brings_back_its_exact_bytes(self, tmp_path):
        """CRLF 那种：放回去必须还是 CRLF，不是一个"看起来一样"的 JSON。"""
        root = _project(tmp_path, [ALPHA], crlf=True)
        original = _bytes(root)
        assert b'\r\n' in original
        assert _save(root, BETA)['ok']
        assert b'\r\n' not in _bytes(root)          # 我写下去的是 LF
        old = V.backups_of(_manifest(root))[-1]
        res = V.restore(_manifest(root), old.path)
        assert res['ok'] and res['written'], res
        assert _bytes(root) == original, '放回来的不是那几个字节'

    def test_what_was_there_before_the_restore_becomes_a_new_generation(self, tmp_path):
        """放回之前那份自动进新一代备份 —— 放错了还能再放回（不是单向门）。"""
        root = _project(tmp_path)
        vers = _grow(root)
        before_restore = _bytes(root)
        n = len(vers)
        res = V.restore(_manifest(root), vers[-1].path)   # 退到最老那一版
        assert res['ok'] and res['written'], res
        assert _bytes(root) != before_restore
        after = V.backups_of(_manifest(root))
        assert len(after) == n + 1, [v.name for v in after]
        assert V.read_text_exact(after[0].path).encode('utf-8') == before_restore
        # 再放回去（把刚才那份放回来）—— 退路是双向的
        back = V.restore(_manifest(root), after[0].path)
        assert back['ok'] and back['written'], back
        assert _bytes(root) == before_restore

    def test_a_generation_equal_to_now_writes_nothing(self, tmp_path):
        root = _project(tmp_path)
        vers = _grow(root)
        same = vers[-1]                       # 最老那份
        V.restore(_manifest(root), same.path)  # 先退到它
        n = len(V.backups_of(_manifest(root)))
        again = V.restore(_manifest(root), same.path)   # 再放同一版 = 现在这份
        assert again['ok'] and again['same'] is True, again
        assert again['written'] is False
        assert len(V.backups_of(_manifest(root))) == n, '一模一样还多了一代备份'

    def test_expect_current_can_be_none_meaning_the_file_was_absent(self, tmp_path):
        """`None` 是**合法期望值**（当时没有这份文件），不是"不校验"。"""
        root = _project(tmp_path)
        vers = _grow(root)
        path = _manifest(root)
        path.unlink()
        res = V.restore(path, vers[-1].path, expect_current=None)
        assert res['ok'] and res['written'], res
        assert path.is_file()


# ---------------------------------------------------------------------------
#  三、不许覆盖别人：被别人改过 / 版本没了 / 锁在别人手里
# ---------------------------------------------------------------------------

class TestRefusals:
    def test_refuses_when_the_file_changed_after_we_looked(self, tmp_path):
        root = _project(tmp_path)
        vers = _grow(root)
        looked = V.read_text_exact(_manifest(root))
        path = _manifest(root)
        path.write_text(path.read_text(encoding='utf-8') + '\n', encoding='utf-8')
        now = _bytes(root)
        res = V.restore(path, vers[0].path, expect_current=looked)
        assert res['ok'] is False and res['changed'] is True, res
        assert '又被改过' in res['reason'], res
        assert _bytes(root) == now, '拒绝的时候改了文件'

    def test_refuses_when_that_generation_is_gone(self, tmp_path):
        root = _project(tmp_path)
        vers = _grow(root)
        gone = vers[0].path
        gone.unlink()
        res = V.restore(_manifest(root), gone)
        assert res['ok'] is False and '不在磁盘上' in res['reason'], res

    def test_refuses_when_someone_holds_the_lock(self, tmp_path):
        root = _project(tmp_path)
        vers = _grow(root)
        now = _bytes(root)
        script = _write_child(tmp_path, 'hold.py', HOLD_CHILD)
        child = subprocess.Popen(
            [sys.executable, str(script), str(_manifest(root)), '1.2'],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            encoding='utf-8', env=_child_env())
        try:
            assert child.stdout.readline().strip() == 'held'
            res = V.restore(_manifest(root), vers[0].path, lock_timeout=0.2)
        finally:
            child.communicate(timeout=60)
        assert res['ok'] is False and res['locked'] is True, res
        assert '另一个进程' in res['reason'], res
        assert _bytes(root) == now, '拿不到锁的时候改了文件'


# ---------------------------------------------------------------------------
#  四、真并发：两个进程同刻放回**不同**的版本 → 恰好一个成功
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

RESTORE_CHILD = textwrap.dedent('''
    import json, sys, time
    sys.path.insert(0, {root!r})
    from pathlib import Path
    from storygraph.core import versions as V
    spec = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    start = float(sys.argv[2])
    while time.time() < start:
        time.sleep(0.005)
    res = V.restore(spec['target'], spec['version'],
                    expect_current=spec['expect'], lock_timeout=20.0)
    print(res.get('ok'), res.get('written'), res.get('changed'),
          res.get('locked'), flush=True)
''')


def _child_env() -> dict:
    return dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONPATH=str(ROOT))


def _write_child(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / name
    path.write_text(body.replace('{root!r}', repr(str(ROOT))), encoding='utf-8')
    return path


class TestConcurrency:
    def test_two_processes_restoring_different_versions_exactly_one_wins(
            self, tmp_path):
        """
        两边都以为"现在这份没变过"，各挑一版放回 → **恰好一个成功**。

        比对在锁里，所以必然一个先写、另一个看到"文件已经不是我打开时那份"
        （它绝不可能也成功：那一版和现在这份本来就不一样）。文件最后必然是
        **某一版的完整原话**，不会拼出半份。
        """
        root = _project(tmp_path)
        vers = _grow(root)
        expect = V.read_text_exact(_manifest(root))
        payloads, procs = [], []
        script = _write_child(tmp_path, 'restore.py', RESTORE_CHILD)
        start = time.time() + 1.5
        for i, ver in enumerate(vers):
            spec = tmp_path / f'spec{i}.json'
            spec.write_text(json.dumps({'target': str(_manifest(root)),
                                        'version': str(ver.path),
                                        'expect': expect}, ensure_ascii=False),
                            encoding='utf-8')
            payloads.append(spec)
            procs.append(subprocess.Popen(
                [sys.executable, str(script), str(spec), str(start)],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                encoding='utf-8', env=_child_env()))
        outs = [p.communicate(timeout=90)[0].strip() for p in procs]
        wins = [o for o in outs if o.startswith('True')]
        loses = [o for o in outs if o.startswith('False')]
        assert len(wins) == 1 and len(loses) == 1, outs
        assert wins[0] == 'True True False False', outs
        assert loses[0] == 'False False True False', outs
        final = _bytes(root)
        assert final in [v.path.read_bytes() for v in vers], \
            '最后落盘的不是任何一版的完整原话'


# ---------------------------------------------------------------------------
#  五、`save()` 内容一样时不碰文件（不烧备份、mtime 不跳）
# ---------------------------------------------------------------------------

class TestUnchangedSave:
    def test_an_identical_save_does_not_write_at_all(self, tmp_path, monkeypatch):
        """
        打桩数 `write_text_atomic` 的调用次数：改过的存一次**必须**写，
        一模一样的再存一次**必须**一次都不写（不然白烧一代备份）。
        """
        from storygraph.core import atomicio

        calls = []
        real = atomicio.write_text_atomic

        def counted(*a, **kw):
            calls.append(a[0])
            return real(*a, **kw)

        monkeypatch.setattr(atomicio, 'write_text_atomic', counted)
        root = _project(tmp_path, [ALPHA])
        assert _save(root, BETA)['ok']
        assert len(calls) == 1, calls
        n = len(V.backups_of(_manifest(root)))
        again = _reg(root).save(root)
        assert again['ok'] and again['unchanged'] is True, again
        assert again['undo'] is None
        assert len(calls) == 1, f'内容一样还写了一次盘：{calls}'
        assert len(V.backups_of(_manifest(root))) == n, '内容一样还多了一代备份'

    def test_the_window_says_it_did_not_rewrite(self, qapp, tmp_path):
        from storygraph.ui_qt.dialog_extensions import ExtensionDialog
        root = _project(tmp_path, [ALPHA])
        dlg = ExtensionDialog(None, project_root=root)
        dlg._save()
        dlg._save()                      # 第二次：一模一样
        assert '没有重写' in dlg.status.text(), dlg.status.text()


# ---------------------------------------------------------------------------
#  六、界面：列表、预览、放回
# ---------------------------------------------------------------------------

class TestDialog:
    @staticmethod
    def _dialog(root, qapp):
        from storygraph.ui_qt.dialog_versions import VersionsDialog
        return VersionsDialog(None, _manifest(root),
                              summarize=ER.summarize_manifest)

    def test_it_lists_the_generations_with_a_summary_and_a_preview(
            self, qapp, tmp_path):
        root = _project(tmp_path)
        vers = _grow(root)
        dlg = self._dialog(root, qapp)
        assert dlg.table.rowCount() == len(vers) == 2
        assert dlg.table.item(0, 0).text().strip()          # 时间
        assert '条' in dlg.table.item(0, 2).text()           # 摘要
        assert '现在：3 条' in dlg.status.text(), dlg.status.text()

        dlg.table.selectRow(1)                              # 最老那一版
        assert dlg.picked() is not None
        assert dlg.btn_restore.isEnabled()
        assert json.loads(dlg.preview.toPlainText())['extensions'][0]['id'] \
            == 'alpha'

    def test_clicking_restore_puts_it_back_and_says_it_is_reversible(
            self, qapp, tmp_path):
        root = _project(tmp_path)
        vers = _grow(root)
        original = vers[-1].path.read_bytes()
        dlg = self._dialog(root, qapp)
        got = []
        dlg.restored.connect(got.append)
        dlg.table.selectRow(1)
        dlg.btn_restore.click()                             # **点按钮**
        assert _bytes(root) == original
        assert len(got) == 1 and got[0]['ok'], got
        assert '还能再放回' in dlg.status.text(), dlg.status.text()
        assert dlg.table.rowCount() == 3, '放回之后列表里该多一代'

    def test_it_refuses_when_the_file_changed_while_the_list_was_open(
            self, qapp, tmp_path):
        root = _project(tmp_path)
        vers = _grow(root)
        dlg = self._dialog(root, qapp)                      # 打开时记下"现在那份"
        path = _manifest(root)
        path.write_text(path.read_text(encoding='utf-8') + '\n', encoding='utf-8')
        now = _bytes(root)
        dlg.table.selectRow(0)
        res = dlg.restore_selected()
        assert res['ok'] is False and res['changed'] is True, res
        assert '没放回' in dlg.status.text()
        assert _bytes(root) == now, '拒绝的时候改了文件'

    def test_no_generations_yet_says_so(self, qapp, tmp_path):
        root = _project(tmp_path, [ALPHA])
        dlg = self._dialog(root, qapp)
        assert dlg.table.rowCount() == 0
        assert not dlg.btn_restore.isEnabled()
        assert '还没有备份' in dlg.status.text(), dlg.status.text()


# ---------------------------------------------------------------------------
#  七、窗口：**关掉再打开也能退**（这一轮的标题）
# ---------------------------------------------------------------------------

class TestWindow:
    @staticmethod
    def _archive(tmp_path: Path, ext) -> Path:
        from storygraph.ext import archive as A
        src = tmp_path / 'src'
        (src / 'game').mkdir(parents=True)
        (src / ER.MANIFEST).write_text(
            json.dumps({'schema': ES.EXT_SCHEMA, 'extensions': list(ext)},
                       ensure_ascii=False), encoding='utf-8')
        pkg = tmp_path / 'pkg.sg-ext.json'
        pkg.write_text(A.dump_archive(_reg(src).export_archive(name='包')),
                       encoding='utf-8')
        return pkg

    def test_closing_the_window_does_not_take_the_way_back_with_it(
            self, qapp, tmp_path, monkeypatch):
        """
        第九轮那条退路只在窗口活着的时候有效；这一条是补上的那一半：
        **关掉重开**，历史版本还在，点两下就退回去。
        """
        from storygraph.ui_qt import dialog_extensions as DX

        root = _project(tmp_path, [ALPHA])
        original = _bytes(root)
        pkg = self._archive(tmp_path, [BETA])

        first = DX.ExtensionDialog(None, project_root=root)
        assert first.import_archive(str(pkg))['saved'] is True
        assert _bytes(root) != original
        first.reject()                       # **关窗**：内存里的撤销材料没了

        second = DX.ExtensionDialog(None, project_root=root)
        assert not second.btn_undo.isEnabled(), '关窗之后"撤销上次保存"本该是灰的'

        def fake_exec(self):
            self.table.selectRow(0)          # 最新那一代 = 导入之前的样子
            self.btn_restore.click()
            return 0

        monkeypatch.setattr(DX.VersionsDialog, 'exec', fake_exec)
        second.btn_versions.click()          # **点按钮**

        assert _bytes(root) == original, '关窗重开之后退不回去了'
        assert second.changed is True
        assert second._reg.get('beta') is None, '文件退回去了，内存/树没跟着退'
        assert '已放回' in second.status.text(), second.status.text()

    def test_the_button_opens_this_projects_manifest(self, qapp, tmp_path,
                                                     monkeypatch):
        from storygraph.ui_qt import dialog_extensions as DX
        root = _project(tmp_path, [ALPHA])
        seen = []

        class _Sig:
            def connect(self, *_a):
                pass

        class Rec:
            def __init__(self, parent=None, path=None, **kw):
                seen.append((Path(path), kw.get('summarize')))

            def __getattr__(self, name):
                return _Sig()

            def exec(self):
                return 0

        monkeypatch.setattr(DX, 'VersionsDialog', Rec)
        dlg = DX.ExtensionDialog(None, project_root=root)
        dlg.btn_versions.click()
        assert seen and seen[0][0] == _manifest(root), seen
        assert seen[0][1] is ER.summarize_manifest, '摘要该用扩展清单那一份'

    def test_no_project_says_so_instead_of_opening_a_dialog(self, qapp):
        from storygraph.ui_qt import dialog_extensions as DX
        dlg = DX.ExtensionDialog(None, project_root='')
        assert dlg._show_versions() is None
        assert '先打开项目' in dlg.status.text()
