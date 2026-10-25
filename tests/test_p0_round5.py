"""
tests/test_p0_round5.py
=======================
《不安全因素》**第二轮**审计（`docs/P0审计_不安全因素_第二轮_2026-10-25.md`）
的判据 —— 每一条都对着审计里那个**真实跑出来的输出**：

| 审计条目 | 从前（实测） | 本文件锁住的判据 |
|---|---|---|
| P0-3 改名补丁 TOCTOU | plan→apply 之间文件被改，旧全文**静默盖回去** | 拒写 + 说出是哪个文件 |
| P1-4 改名过度匹配 | `$ bg = 1` / `# bg 注释` / 字符串里的名字一起改 | 只改资产引用位；被拒的要**报出来** |
| P1-5 混合换行导出 | 4 行 CRLF 静默变 LF | 没碰过的行**换行符原样** |
| P1-6 资产表误删行 | 绝对路径 / `tmp/` 下**真实文件**的行被判缺失删掉 | 文件在就不删；判不了也只报不删 |
| P2-1 写失败仍报 ok | `ok=True` 而文件没写 | `ok` 如实；`failed` 说清原因 |
| P2-2 读不了的文件静默跳过 | 改名静默不完整 | `skipped_files` 列出来 |
| P2-3 新章节 label 不校验 | 非法 label 直接写进 `.rpy` | 拒写并出声 |
| P2-4 单代备份 | 第二次同步盖掉第一代备份 | 上一代归档成 `*.bak-<时间戳>.xlsx` |
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.assets.asset_extensions import (                          # noqa: E402
    apply_rename_patch, plan_rename_patch, rollback_rename_patch,
)

# ===========================================================================
#  P0-3 改名补丁 TOCTOU
# ===========================================================================

SRC = ('label ch01:\n'
       '    scene bg classroom\n'
       '    show bg\n'
       '    $ bg = 1\n'
       '    # bg 这是注释\n'
       '    play music "bg 音乐.mp3"\n'
       '    "台词"\n'
       '    return\n')


def _script(tmp_path, text=SRC, name='ch01.rpy') -> Path:
    game = tmp_path / 'game'
    game.mkdir(parents=True, exist_ok=True)
    fp = game / name
    fp.write_text(text, encoding='utf-8', newline='')
    return fp


class TestRenameToctou:
    """计划之后文件被改过 → **一个字节都不许写**。"""

    def test_concurrent_edit_is_not_overwritten(self, tmp_path):
        fp = _script(tmp_path)
        plan = plan_rename_patch('bg', 'bg_new', [fp])
        assert plan['files'], '前提：这一份确实会被改'
        # 用户在看预览的几分钟里，程序员在文件里加了一句
        concurrently = SRC.replace('    "台词"', '    "程序员刚加的一句"')
        fp.write_text(concurrently, encoding='utf-8', newline='')

        res = apply_rename_patch(plan, confirm=True, backup=True)

        after = fp.read_text(encoding='utf-8')
        assert after == concurrently, '旧全文把并发写进去的内容盖回去了'
        assert 'bg_new' not in after, '过期计划居然写盘了'
        assert res['ok'] is False
        assert res['modified'] == []
        assert res['changed_since_plan'] == [str(fp)], res
        assert '被改过' in res['reason'], res['reason']
        # 拒写就不该留备份（什么都没写，没必要让人以为动过）
        assert res['backup_files'] == []

    def test_clean_files_still_go_through(self, tmp_path):
        """一个文件过期、另一个干净：干净的照写，过期的拒写。"""
        stale = _script(tmp_path, name='a.rpy')
        clean = _script(tmp_path, name='b.rpy')
        plan = plan_rename_patch('bg', 'bg_new', [stale, clean])
        assert len(plan['files']) == 2
        stale.write_text(SRC + '# 程序员加的一行\n', encoding='utf-8',
                         newline='')

        res = apply_rename_patch(plan, confirm=True, backup=True)

        assert res['modified'] == [str(clean)]
        assert res['changed_since_plan'] == [str(stale)]
        assert 'bg_new' in clean.read_text(encoding='utf-8')
        assert '# 程序员加的一行' in stale.read_text(encoding='utf-8')
        assert res['ok'] is False
        # 写成功的那一份仍然可回滚
        rb = rollback_rename_patch(res)
        assert rb['restored'] == [str(clean)]
        assert clean.read_text(encoding='utf-8') == SRC

    def test_backup_failure_blocks_the_write(self, tmp_path, monkeypatch):
        """备份做不成 → 也不写（`.bak` 是"可回滚"的另一半）。"""
        from storygraph.assets import asset_extensions

        fp = _script(tmp_path)
        plan = plan_rename_patch('bg', 'bg_new', [fp])

        def boom(*_a, **_k):
            raise OSError('磁盘满')

        monkeypatch.setattr(asset_extensions.shutil, 'copy2', boom)
        res = apply_rename_patch(plan, confirm=True, backup=True)

        assert res['ok'] is False
        assert res['modified'] == []
        assert res['failed'] and '备份' in res['failed'][0]['reason']
        assert fp.read_text(encoding='utf-8') == SRC


# ===========================================================================
#  P1-4 改名只改"资产引用位"
# ===========================================================================

class TestRenameScope:
    def test_code_comment_and_string_are_not_touched(self, tmp_path):
        fp = _script(tmp_path)
        plan = plan_rename_patch('bg', 'bg_new', [fp])
        res = apply_rename_patch(plan, confirm=True, backup=False)
        assert res['ok'] is True

        lines = fp.read_text(encoding='utf-8').splitlines()
        assert lines[1] == '    scene bg_new classroom', lines[1]
        assert lines[2] == '    show bg_new', lines[2]
        assert lines[3] == '    $ bg = 1', lines[3]
        assert lines[4] == '    # bg 这是注释', lines[4]
        assert lines[5] == '    play music "bg 音乐.mp3"', lines[5]
        # 改了几行 = 预览说的行数（预览与落盘一致）
        assert plan['total_lines'] == 2

    def test_rejected_hits_are_reported_not_swallowed(self, tmp_path):
        fp = _script(tmp_path)
        plan = plan_rename_patch('bg', 'bg_new', [fp])
        assert plan['rejected_total'] == 3, plan['rejected']
        reasons = [why for _no, _line, why in plan['rejected'][0]['hits']]
        assert any('`$`' in r for r in reasons), reasons
        assert any('注释' in r for r in reasons), reasons
        assert any('引号里' in r for r in reasons), reasons
        # 行号指向的正是那三行
        nums = [no for no, _l, _w in plan['rejected'][0]['hits']]
        assert nums == [4, 5, 6], nums

    def test_audio_path_inside_quotes_is_still_renamed(self, tmp_path):
        """`play music "bgm_main.ogg"` 里的**整段路径**是资产引用，要改。"""
        fp = _script(tmp_path, 'label a:\n    play music "bgm_main.ogg"\n')
        plan = plan_rename_patch('bgm_main', 'bgm_theme', [fp])
        assert plan['total_lines'] == 1, plan['rejected']
        apply_rename_patch(plan, confirm=True, backup=False)
        assert 'bgm_theme.ogg' in fp.read_text(encoding='utf-8')

    def test_character_rename_still_reaches_define_and_say(self, tmp_path):
        """反例守卫：角色改名必须同时改 `define` 与台词前的说话人。"""
        text = ('define meiko = Character("Meiko")\n'
                'label start:\n    meiko "hello"\n    return\n')
        fp = _script(tmp_path, text)
        plan = plan_rename_patch('meiko', 'mk', [fp])
        apply_rename_patch(plan, confirm=True, backup=False)
        after = fp.read_text(encoding='utf-8')
        assert 'define mk = Character("Meiko")' in after
        assert 'mk "hello"' in after

    def test_python_block_body_is_left_alone(self, tmp_path):
        text = ('init python:\n'
                '    bg = 1\n'
                '    register(bg)\n'
                'label a:\n'
                '    show bg\n')
        fp = _script(tmp_path, text)
        plan = plan_rename_patch('bg', 'bg_new', [fp])
        apply_rename_patch(plan, confirm=True, backup=False)
        lines = fp.read_text(encoding='utf-8').splitlines()
        assert lines[1] == '    bg = 1', lines[1]
        assert lines[2] == '    register(bg)', lines[2]
        assert lines[4] == '    show bg_new', lines[4]

    def test_label_lines_are_not_renamed(self, tmp_path):
        """资产 id 恰好和 label 同名：改 label 就是换跳转目标。"""
        text = ('label bg:\n'
                '    show bg\n'
                '    jump bg\n')
        fp = _script(tmp_path, text)
        plan = plan_rename_patch('bg', 'bg_new', [fp])
        apply_rename_patch(plan, confirm=True, backup=False)
        lines = fp.read_text(encoding='utf-8').splitlines()
        assert lines[0] == 'label bg:', lines[0]
        assert lines[1] == '    show bg_new', lines[1]
        assert lines[2] == '    jump bg', lines[2]


# ===========================================================================
#  P1-5 混合换行文件：没碰过的行换行符原样
# ===========================================================================

def _split_eols(data: bytes):
    """按字节拆 (行内容, 行结束符) —— 保留每行自己的换行符。"""
    rows, cur, i = [], bytearray(), 0
    while i < len(data):
        b = data[i:i + 1]
        cur += b
        if b == b'\n':
            rows.append((bytes(cur[:-1]), b'\n'))
            cur = bytearray()
        elif b == b'\r':
            if data[i + 1:i + 2] == b'\n':
                rows.append((bytes(cur[:-1]), b'\r\n'))
                cur = bytearray()
                i += 1
            else:
                rows.append((bytes(cur[:-1]), b'\r'))
                cur = bytearray()
        i += 1
    if cur:
        rows.append((bytes(cur), b''))
    return rows


class TestMixedNewlines:
    def test_apply_restores_each_lines_own_eol(self, tmp_path):
        from storygraph.story.source_style import detect

        fp = tmp_path / 'a.rpy'
        fp.write_bytes(b'label a:\r\n    "x"\n    return\n')
        style = detect(fp)
        out = style.apply('label a:\n    "y"\n    return\n', source=fp)
        assert out == 'label a:\r\n    "y"\n    return\n'

    def test_uniform_file_is_unchanged_by_the_new_path(self, tmp_path):
        from storygraph.story.source_style import detect

        fp = tmp_path / 'b.rpy'
        fp.write_bytes(b'label a:\r\n    "x"\r\n    return\r\n')
        style = detect(fp)
        out = style.apply('label a:\n    "y"\n    return\n', source=fp)
        assert out == 'label a:\r\n    "y"\r\n    return\r\n'

    def test_inplace_export_keeps_untouched_lines(self, tmp_path):
        """审计的原样复跑：改第 6 行，前 4 行 CRLF 必须还是 CRLF。"""
        from storygraph.app.editor import EditorSession

        lines = ['label ch01:', '    scene bg classroom',
                 '    show ling happy at left', '    ling "第一句"',
                 '    "旁白一句"', '    ling "第三句"',
                 '    "第四句"', '    return']
        raw = bytearray()
        for i, ln in enumerate(lines):
            raw += ln.encode('utf-8')
            raw += b'\r\n' if i < 4 else b'\n'
        game = tmp_path / 'game'
        game.mkdir(parents=True)
        fp = game / 'ch01.rpy'
        fp.write_bytes(bytes(raw))
        before = _split_eols(fp.read_bytes())

        session = EditorSession(project_root=tmp_path)
        session.load(fp)
        for beat in session.beats:
            if '第三句' in str(getattr(beat, 'text', '') or ''):
                beat.text = '第三句改过了'
        res = session.export(fp)
        assert res.get('ok'), res

        after = _split_eols(fp.read_bytes())
        for content, eol in before:
            if '第三句' in content.decode('utf-8'):
                continue                        # 用户改的那一行本来就该变
            hit = [e for c, e in after if c == content]
            assert hit, f'第 {content!r} 行不见了'
            assert eol in hit, f'{content!r} 的换行符被改：{eol!r} → {hit[0]!r}'


# ===========================================================================
#  P1-6 资产表：文件在就不许删；判不了也只报不删
# ===========================================================================

def _xlsx_with_paths(path: Path, rows):
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(['ID', 'Path'])
    for aid, rel in rows:
        ws.append([aid, rel])
    wb.save(path)


class TestAssetTablePrune:
    def _walk(self, root):
        from storygraph.assets.asset_registry import FileWalkIndex
        return FileWalkIndex(root)

    def test_absolute_path_to_a_real_file_is_kept(self, tmp_path):
        from storygraph.assets.asset_registry import (_missing_paths,
                                                      _row_file_exists,
                                                      _walk_covers)

        img = tmp_path / 'game' / 'images' / 'a.png'
        img.parent.mkdir(parents=True)
        img.write_bytes(b'x')
        rel = str(img).replace('\\', '/')
        rows = {'a': {'ID': 'a', 'Path': rel}}
        walk = self._walk(tmp_path)
        # 前提：索引**管不着**绝对路径（那正是从前误删的原因）
        assert _walk_covers(rel, walk) is False
        assert _missing_paths(rows, set(), 'images', walk, tmp_path) == []
        assert _row_file_exists('image', rows['a'], tmp_path, walk=walk) is True

    def test_file_under_skipped_dir_is_kept(self, tmp_path):
        from storygraph.assets.asset_registry import _missing_paths

        hidden = tmp_path / 'game' / 'tmp' / 'hidden.png'
        hidden.parent.mkdir(parents=True)
        hidden.write_bytes(b'x')
        rows = {'b': {'ID': 'b', 'Path': 'game/tmp/hidden.png'}}
        walk = self._walk(tmp_path)
        assert _missing_paths(rows, set(), 'images', walk, tmp_path) == []

    def test_really_missing_row_is_still_reported(self, tmp_path):
        from storygraph.assets.asset_registry import _missing_paths

        (tmp_path / 'game').mkdir()
        rows = {'c': {'ID': 'c', 'Path': 'game/images/gone.png'}}
        walk = self._walk(tmp_path)
        got = _missing_paths(rows, set(), 'images', walk, tmp_path)
        assert got == ['game/images/gone.png'], got

    def test_unjudged_row_is_reported_but_not_deleted(self, tmp_path):
        from storygraph.assets.asset_registry import (AssetRegistry,
                                                      _missing_paths,
                                                      _sync_prune_stage,
                                                      _unjudged_paths)

        root = tmp_path
        (root / 'game').mkdir()
        table_dir = root / 'game' / 'asset_tables'
        table_dir.mkdir()
        gone_abs = str((root / 'elsewhere' / 'nope.png')).replace('\\', '/')
        _xlsx_with_paths(table_dir / 'images.xlsx',
                         [('x', gone_abs), ('keep', 'game/images/gone.png')])
        reg = AssetRegistry()
        reg.images = {'x': {'ID': 'x', 'Path': gone_abs},
                      'keep': {'ID': 'keep', 'Path': 'game/images/gone.png'}}
        walk = self._walk(root)
        # 判不了的那一行只进 `_unjudged_paths`，不进 `_missing_paths`
        assert _unjudged_paths(reg.images, set(), 'images', walk,
                               root) == [gone_abs]
        assert 'game/images/gone.png' in _missing_paths(
            reg.images, set(), 'images', walk, root)

        _removed, _index, _added, unjudged = _sync_prune_stage(
            root, table_dir, reg, walk)

        import openpyxl
        ws = openpyxl.load_workbook(table_dir / 'images.xlsx').active
        left = [r[1] for r in ws.iter_rows(min_row=2, values_only=True)]
        assert gone_abs in left, '判不了的行被删了（那是用户手写的元数据）'
        assert 'game/images/gone.png' not in left, '真缺失的行该删'
        assert unjudged.get('images') == 1, unjudged

    def test_backup_keeps_the_previous_generation(self, tmp_path):
        from storygraph.assets.asset_registry import _backup_table

        table = tmp_path / 'images.xlsx'
        table.write_bytes(b'v1')
        _backup_table(table)
        table.write_bytes(b'v2')
        _backup_table(table)

        assert (tmp_path / 'images.bak.xlsx').read_bytes() == b'v2'
        archived = sorted(tmp_path.glob('images.bak-*.xlsx'))
        assert archived, '上一代备份被固定名覆盖了'
        assert archived[0].read_bytes() == b'v1'


# ===========================================================================
#  P2-2 读不了的脚本：改名不许静默不完整
# ===========================================================================

def test_unreadable_script_is_listed_not_silently_skipped(tmp_path):
    game = tmp_path / 'game'
    game.mkdir()
    bad = game / 'gbk.rpy'
    bad.write_bytes('label a:\n    # 中文注释（GBK 存的）\n    show bg\n'
                    .encode('gbk'))
    good = game / 'ok.rpy'
    good.write_text('label b:\n    show bg\n', encoding='utf-8')

    plan = plan_rename_patch('bg', 'bg_new', [bad, good])
    assert plan['matched_files'] == [str(good)]
    assert [s['path'] for s in plan['skipped_files']] == [str(bad)]
    assert '读不了' in plan['skipped_files'][0]['reason']


# ===========================================================================
#  P2-3 新建章节：label 先校验
# ===========================================================================

class _View:
    def __init__(self):
        self.msgs = []

    def _status(self, msg):
        self.msgs.append(str(msg))


class TestNewChapterLabel:
    @pytest.mark.parametrize('bad', ['1abc', 'a-b', 'a b', '坏名字'])
    def test_invalid_label_is_refused(self, tmp_path, bad):
        import os

        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        from storygraph.ui_qt.window_nodes import _write_new_chapter

        target = tmp_path / 'game' / 'x.rpy'
        view = _View()
        assert _write_new_chapter(view, str(target), bad) == ''
        assert not target.exists(), f'非法 label {bad!r} 居然写出了文件'
        assert view.msgs and 'label' in view.msgs[0]

    def test_valid_label_still_writes(self, tmp_path):
        import os

        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        from storygraph.ui_qt.window_nodes import _write_new_chapter

        target = tmp_path / 'game' / 'ok.rpy'
        assert _write_new_chapter(_View(), str(target), 'chapter1_2') \
            == str(target)
        assert 'label chapter1_2:' in target.read_text(encoding='utf-8')

    def test_non_utf8_file_is_refused_byte_for_byte(self, tmp_path):
        """P1-A（第三轮审计）：坏字节会被 `errors='replace'` 换成 U+FFFD 写回。"""
        import os

        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        from storygraph.ui_qt.window_nodes import _write_new_chapter

        target = tmp_path / 'game' / 'legacy.rpy'
        target.parent.mkdir(parents=True)
        # 0xE9 是孤立的 Latin-1 字节（不是合法 UTF-8 序列）
        target.write_bytes(b'label a:\r\n    "caf\xe9"\r\n    return\r\n')
        before = target.read_bytes()

        view = _View()
        assert _write_new_chapter(view, str(target), 'ch02') == '', \
            '非 UTF-8 文件居然被写了'
        assert target.read_bytes() == before, '字节被改了（U+FFFD 腐蚀）'
        assert b'\xef\xbf\xbd' not in target.read_bytes(), '出现 U+FFFD'
        assert view.msgs and 'UTF-8' in view.msgs[0], view.msgs


# ===========================================================================
#  P1-5b 删源行（与 control_route 同级的原地改写点）也要逐行还原换行
# ===========================================================================

class TestSourceLineDeleteKeepsEol:
    def test_mixed_eol_survives_deleting_a_beat(self, tmp_path):
        from storygraph.app.editor import EditorSession
        from storygraph.app.source_line_delete import (
            apply_source_line_delete, plan_source_line_delete)

        lines = ['label ch01:', '    e "一"', '    jump target_a',
                 '    e "二"', '    return', 'label target_a:',
                 '    return']
        raw = bytearray()
        for i, ln in enumerate(lines):
            raw += ln.encode('utf-8')
            raw += b'\r\n' if i < 4 else b'\n'
        game = tmp_path / 'game'
        game.mkdir()
        fp = game / 'ch01.rpy'
        fp.write_bytes(bytes(raw))

        session = EditorSession(project_root=tmp_path)
        session.load(fp)
        beat = next(b for b in session.beats
                    if str(getattr(b, 'raw', '') or '').startswith('jump '))

        plan = plan_source_line_delete(tmp_path, fp, beat.line_id)
        assert plan.ok, plan.blocking
        res = apply_source_line_delete(plan)
        assert res['ok'], res

        after = _split_eols(fp.read_bytes())
        # 删掉的是那一行 jump；其余每行**内容和换行符**都原样：
        # 原文前 4 行是 CRLF（label / e"一" / jump / e"二"），后 3 行是 LF。
        kept = list(after)
        for want in [(b'label ch01:', b'\r\n'),
                     ('    e "一"'.encode(), b'\r\n'),
                     ('    e "二"'.encode(), b'\r\n'),
                     (b'    return', b'\n'), (b'label target_a:', b'\n')]:
            assert want in kept, f'{want[0]!r} 的那一行/换行被改了：{kept}'
        assert b'jump target_a' not in fp.read_bytes()

