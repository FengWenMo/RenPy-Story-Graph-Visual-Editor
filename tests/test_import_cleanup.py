"""
纯文字导入 · **残留清理** 判据（2026-10-26）
==============================================

用户实机原话：

> 我一个个删 vscode 里面的 .bak 和 blocked 里面的文件老狼狈了……

`storygraph/authoring/import_cleanup.py` 是"扫描 + 安全删除"的纯逻辑部分
（界面另一个人做，只调这几个函数）。这里钉住的是**判据和红线**，不是文案：

  ① 四类残留都扫得到：`.rpy.bak` / `.rpy.bak-<时间戳>` / `.blocked.rpy`
     / 历史遗留的 `.bak.rpy`；
  ② ⚠ **`characters.xlsx.bak`（资源表备份）绝不许列出来** —— 列出来用户
     会顺手全选删掉，而那是他唯一的表备份；别的 `.bak` 也不许被卷进来；
  ③ **只认 `<项目根>/game/` 与 `<项目根>/build/review/`**，项目外/别处同名
     文件一律不列（用户会全选删）；
  ④ 没有项目 / 没有 game/ → `[]`，不抛异常（界面打开项目时顺手调它）；
  ⑤ `delete` 前**再查一次**命名与目录：调用方塞任意路径进来也不会被删，
     而会进 `failed` 并带中文原因；
  ⑥ 单个文件删不掉（被占用/权限/中间被删）不影响其它，也不许假装成功；
  ⑦ 结果按路径排序（确定性）；`summarize` / `human_size` 给界面用的数字
     与显示串必须自洽。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.authoring import import_cleanup as IC  # noqa: E402


# ==================== 夹具 ====================

def _project(tmp_path: Path) -> Path:
    """最小项目：只需要 `game/`（本模块的判据不读角色表/剧情图）。"""
    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True)
    return root


def _touch(path: Path, data: bytes = b'x') -> Path:
    """造一个文件（自动建父目录），返回路径。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _kinds(files) -> dict:
    """`{文件名: kind}` —— 断言时少写一点。"""
    return {j.path.name: j.kind for j in files}


# ==================== ① 四类残留都扫得到 ====================

def test_scan_finds_all_four_kinds(tmp_path):
    """`game/` 递归 + `build/review/` 里各放一份，四类必须全认出来。"""
    root = _project(tmp_path)
    _touch(root / 'game' / 'chapter' / 'x.rpy.bak')
    _touch(root / 'game' / 'chapter' / 'x.rpy.bak-20261026-210509-123456')
    _touch(root / 'build' / 'review' / 'x.blocked.rpy')
    _touch(root / 'game' / 'x.blocked.rpy')          # 历史遗留：曾经写进 game/
    _touch(root / 'game' / 'chapter' / 'old.bak.rpy')

    got = _kinds(IC.scan_import_junk(root))
    assert got == {
        'x.rpy.bak': IC.KIND_BAK,
        'x.rpy.bak-20261026-210509-123456': IC.KIND_BAK_STAMPED,
        'x.blocked.rpy': IC.KIND_BLOCKED,       # 同名文件在 game/ 与 build/review/
        'old.bak.rpy': IC.KIND_BAK_RPY,         # 两处都算 junk（dict 合并成一条）
    }, got
    # game/ 与 build/review/ 的那两个 blocked 是不同路径，必须都在结果里
    blocked = [j for j in IC.scan_import_junk(root) if j.kind == IC.KIND_BLOCKED]
    assert len(blocked) == 2, [str(j.path) for j in blocked]


# ==================== ② 资源表备份绝不许列出来 ====================

def test_the_resource_table_backup_and_unrelated_baks_are_never_listed(tmp_path):
    """
    ⚠ 红线：`characters.xlsx.bak` 是用户**唯一**的表备份。列出来 = 用户
    顺手全选删掉 = 丢数据。`*.txt.bak` / `random.bak` 同理不归这里管。

    判据必须是"完整后缀"，不能是 `*.bak` 宽匹配 —— 这条测试就是来钉它的。
    """
    root = _project(tmp_path)
    precious = _touch(root / 'game' / 'asset_tables' / 'characters.xlsx.bak',
                      b'PK\x03\x04 fake xlsx')
    _touch(root / 'game' / 'notes' / '其他.txt.bak')
    _touch(root / 'game' / 'random.bak')
    junk = _touch(root / 'game' / 'chapter1.rpy.bak')

    files = IC.scan_import_junk(root)
    assert [j.path for j in files] == [junk]
    assert precious not in [j.path for j in files]
    assert all('xlsx' not in j.path.name for j in files)


# ==================== ③ 只在 game/ 与 build/review/ 里工作 ====================

def test_other_projects_and_loose_files_are_not_listed(tmp_path):
    """项目外另一个 `game/` 里的 `.rpy.bak` 不是这个项目的残留，不许列。"""
    root = _project(tmp_path)
    mine = _touch(root / 'game' / 'mine.rpy.bak')
    _touch(tmp_path / 'other_project' / 'game' / 'theirs.rpy.bak')
    _touch(tmp_path / 'loose.rpy.bak')               # 项目根外面、也不是 game/

    assert [j.path for j in IC.scan_import_junk(root)] == [mine]


def test_build_review_only_contributes_blocked_copies(tmp_path):
    """
    `build/review/` 里只认 `.blocked.rpy`（那是门禁的落点）；`.rpy.bak`
    不在这个目录的管辖范围 —— 与 `scan` 的"两个目录、四类命名"一致。
    """
    root = _project(tmp_path)
    _touch(root / 'build' / 'review' / 'x.blocked.rpy')
    _touch(root / 'build' / 'review' / 'x.rpy.bak')

    assert _kinds(IC.scan_import_junk(root)) == {'x.blocked.rpy': IC.KIND_BLOCKED}


def test_scan_ignores_directories_that_look_like_junk(tmp_path):
    """名字像残留的**目录**不列、也不删（`is_file()` 判据）。"""
    root = _project(tmp_path)
    dir_junk = root / 'game' / 'fake.rpy.bak'
    dir_junk.mkdir()

    assert IC.scan_import_junk(root) == []


# ==================== ④ 没有项目 / 没有 game 目录 → [] ====================

def test_missing_project_or_missing_game_dir_returns_empty(tmp_path):
    """界面打开项目时会顺手调一次：没有项目时**不许抛异常**。"""
    assert IC.scan_import_junk(None) == []
    assert IC.scan_import_junk('') == []
    assert IC.scan_import_junk(tmp_path / 'does_not_exist') == []
    assert IC.scan_import_junk(_touch(tmp_path / 'not_a_project.txt')) == []

    # 有 build/review/ 但没有 game/ → 不是这个工具认的项目，宁可少报
    only_build = tmp_path / 'only_build'
    _touch(only_build / 'build' / 'review' / 'x.blocked.rpy')
    assert IC.scan_import_junk(only_build) == []


# ==================== ⑤ 排序 / 字段（确定性，便于界面与测试） ====================

def test_scan_is_sorted_and_reports_size_and_mtime(tmp_path):
    root = _project(tmp_path)
    a = _touch(root / 'game' / 'a.rpy.bak', b'1')
    _touch(root / 'game' / 'c.rpy.bak', b'333')
    _touch(root / 'game' / 'sub' / 'b.rpy.bak', b'22')

    files = IC.scan_import_junk(root)
    paths = [j.path for j in files]
    assert paths == sorted(paths, key=str), '扫描结果不是按路径排序的'

    by_name = {j.path.name: j for j in files}
    assert by_name['a.rpy.bak'].size == 1
    assert by_name['b.rpy.bak'].size == 2
    assert by_name['c.rpy.bak'].size == 3
    assert by_name['a.rpy.bak'].mtime == pytest.approx(
        a.stat().st_mtime), 'mtime 没带上（界面显示/排序要用）'


def test_summarize_counts_totals_and_groups_by_kind(tmp_path):
    root = _project(tmp_path)
    _touch(root / 'game' / 'a.rpy.bak', b'1' * 10)
    _touch(root / 'game' / 'b.rpy.bak', b'2' * 20)
    _touch(root / 'game' / 'old.bak.rpy', b'3' * 30)
    _touch(root / 'build' / 'review' / 'x.blocked.rpy', b'4' * 40)

    files = IC.scan_import_junk(root)
    summary = IC.summarize(files)
    assert summary['total'] == 4
    assert summary['bytes'] == 100
    assert summary['human_bytes'] == IC.human_size(100) == '100 B'
    # 四类键都在（界面按固定顺序渲染，缺键会漏一行）
    assert summary['counts'] == {
        IC.KIND_BAK: 2,
        IC.KIND_BAK_STAMPED: 0,
        IC.KIND_BLOCKED: 1,
        IC.KIND_BAK_RPY: 1,
    }
    assert set(summary['by_kind']) == set(IC.ALL_KINDS)
    assert [j.path.name for j in summary['by_kind'][IC.KIND_BAK]] == \
        ['a.rpy.bak', 'b.rpy.bak']
    assert summary['by_kind'][IC.KIND_BLOCKED][0].path.name == 'x.blocked.rpy'

    # 空输入也不许炸（界面可能先拿空列表摆一次）
    empty = IC.summarize([])
    assert empty['total'] == 0 and empty['bytes'] == 0
    assert empty['human_bytes'] == '0 B'
    assert empty['counts'] == {kind: 0 for kind in IC.ALL_KINDS}


@pytest.mark.parametrize('size, want', [
    (0, '0 B'),
    (1, '1 B'),
    (1023, '1023 B'),
    (1024, '1.0 KB'),
    (12595, '12.3 KB'),                 # 12.3 × 1024
    (1024 * 1024 - 1, '1.0 MB'),        # 1023.99 KB 不许显示成 "1024.0 KB"
    (1024 * 1024, '1.0 MB'),
    (int(1.2 * 1024 * 1024), '1.2 MB'),
    (1024 ** 3, '1.0 GB'),
])
def test_human_size_boundaries(size, want):
    """`human_size` 的单位阈值与"1024.0 不许露出来"的进位规则。"""
    assert IC.human_size(size) == want


# ==================== ⑥ 删除：只删 junk，别的一个都不动 ====================

def test_delete_only_removes_junk_and_keeps_everything_else(tmp_path):
    root = _project(tmp_path)
    junk_paths = [
        _touch(root / 'game' / 'chapter1.rpy.bak'),
        _touch(root / 'game' / 'chapter1.rpy.bak-20261026-210509-123456'),
        _touch(root / 'build' / 'review' / 'chapter1.blocked.rpy'),
        _touch(root / 'game' / 'chapter' / 'old.bak.rpy'),
    ]
    precious = _touch(root / 'game' / 'asset_tables' / 'characters.xlsx.bak',
                      b'PK')
    keep = _touch(root / 'game' / 'chapter1.rpy',
                  b'label chapter1:\n    "hi"\n')

    files = IC.scan_import_junk(root)
    assert len(files) == len(junk_paths)
    deleted, failed = IC.delete_import_junk(files)

    assert failed == []
    assert [str(p) for p in deleted] == sorted(str(p) for p in junk_paths)
    assert all(not p.exists() for p in junk_paths), '有 junk 没被删掉'
    assert precious.exists(), '资源表备份被删了 —— 用户唯一的表备份没了'
    assert keep.exists(), '正常 .rpy 被删了'
    assert IC.scan_import_junk(root) == []


# ==================== ⑦ 删除前安全网：不信任调用方 ====================

def test_delete_refuses_paths_outside_the_allowed_dirs(tmp_path):
    """
    调用方塞一条"不在允许目录里"的假 `JunkFile`（或裸路径）进来 →
    **不删**、进 `failed` 并给中文原因。删文件不可恢复，宁可不删。
    """
    outside = _touch(tmp_path / 'elsewhere' / 'evil.rpy.bak')
    loose = _touch(tmp_path / 'loose' / 'x.blocked.rpy')
    fake = IC.JunkFile(path=outside, kind=IC.KIND_BAK, size=1, mtime=0.0)

    # 乱序传入 → failed 也要按路径排序
    deleted, failed = IC.delete_import_junk([loose, fake])

    assert deleted == []
    assert outside.exists() and loose.exists(), '允许目录外的文件被删了'
    assert [str(p) for p, _ in failed] == sorted([str(outside), str(loose)])
    assert all('不在 <项目根>/game' in why for _p, why in failed), failed


def test_delete_refuses_the_resource_table_backup_even_when_handed_in(tmp_path):
    """就算调用方把它当 junk 递进来，`characters.xlsx.bak` 也绝不许删。"""
    root = _project(tmp_path)
    precious = _touch(root / 'game' / 'asset_tables' / 'characters.xlsx.bak',
                      b'PK')
    fake = IC.JunkFile(path=precious, kind=IC.KIND_BAK, size=2, mtime=0.0)

    deleted, failed = IC.delete_import_junk([fake])

    assert deleted == []
    assert precious.exists(), '资源表备份被删了 —— 用户唯一的表备份没了'
    assert len(failed) == 1 and failed[0][0] == precious
    assert '不是四类导入残留' in failed[0][1], failed[0][1]


def test_a_file_that_vanished_after_scan_is_reported_not_silently_ok(tmp_path):
    """扫描之后文件被删了 → `unlink(missing_ok=False)` 必须报出来，不许假装成功。"""
    root = _project(tmp_path)
    gone = _touch(root / 'game' / 'gone.rpy.bak')
    still = _touch(root / 'game' / 'still.rpy.bak')
    files = IC.scan_import_junk(root)
    gone.unlink()

    deleted, failed = IC.delete_import_junk(files)

    assert [p.name for p in deleted] == ['still.rpy.bak']
    assert [p.name for p, _ in failed] == ['gone.rpy.bak']
    assert '不是普通文件' in failed[0][1], failed[0][1]


def test_one_delete_failure_does_not_stop_the_others(tmp_path, monkeypatch):
    """被占用/权限失败只影响它自己：其它照删，失败的带原因进 `failed`。"""
    root = _project(tmp_path)
    busy = _touch(root / 'game' / 'busy.rpy.bak')
    ok = _touch(root / 'game' / 'ok.rpy.bak')
    files = IC.scan_import_junk(root)
    real_unlink = Path.unlink

    def fake_unlink(self, *args, **kwargs):
        if Path(self) == busy:
            raise PermissionError('文件被别的程序占用')
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, 'unlink', fake_unlink)
    deleted, failed = IC.delete_import_junk(files)

    assert deleted == [ok]
    assert [p for p, _ in failed] == [busy]
    assert '删除失败' in failed[0][1] and '占用' in failed[0][1], failed[0][1]
    assert busy.exists() and not ok.exists()


def test_delete_refuses_symlinks(tmp_path, monkeypatch):
    """
    符号链接不许删（`is_file()` 会跟着目标骗人；`unlink` 删的是链接本身）。
    扫描也不该把它列出来 —— 列了用户就会以为清掉了，目标其实还在。
    """
    root = _project(tmp_path)
    real = _touch(root / 'game' / 'real.rpy.bak')
    link = root / 'game' / 'link.rpy.bak'
    try:
        link.symlink_to(real)
    except (OSError, NotImplementedError):
        # Windows 没开 Developer Mode 时建不了符号链接 —— 用桩钉同一条判据，
        # 保证这条安全网在任何机器上都真的被跑过一次。
        link.write_bytes(b'same content')
        monkeypatch.setattr(Path, 'is_symlink', lambda self: Path(self) == link)

    scanned = IC.scan_import_junk(root)
    assert all(j.path != link for j in scanned), '符号链接被列进结果了'

    fake = IC.JunkFile(path=link, kind=IC.KIND_BAK, size=1, mtime=0.0)
    deleted, failed = IC.delete_import_junk([fake])

    assert deleted == []
    assert [p for p, _ in failed] == [link]
    assert '符号链接' in failed[0][1], failed[0][1]
    assert real.exists(), '链接指向的目标被误删了'
