"""
tests/test_collab_merge.py
==========================
C（用户 2026-10-19）：push 的冲突判据从"revision 变了"改成**字段级三方比较**。

用户原话：

> 现在的 push 是整行级判断：库里的 revision 变了就拒绝。这会产生大量假冲突
> ——A 改了台词、B 改了同一行的音乐，现在会报"这一行冲突"，其实两个改动
> 根本不打架。

这个文件按**场景**钉判据（不是按函数）：

① 两人改同一行的**不同字段** → 静默合并（自动合并要报"合了 N 行/M 处"）；
② 两人改**同一字段且不同** → 冲突，且带着 `(field, base, mine, theirs)`；
③ 只有一方改 → 不算冲突、也不产生新 revision；
④ 我调顺序（Seq）、TA 改内容 → 自动合并（Seq 我的、内容 TA 的）；
⑤ 冲突界面的三种落地：用我的 / 用 TA 的 / 两句都留（新行插在原行后面）；
⑥ 部分提交：`only=[...]` 只写选中的行，其余（含删除扫描）不碰；
⑦ 结构冲突：我删的那句 TA 改过 → 保留 TA 的并说"你删过这句"；
⑧ 没有 base（老表 / 隐藏表被另存丢掉）→ 降级两方比较，并**明说**不能自动合。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from storygraph.story.beat_model import Beat, assign_identity


# ==================================================================
#  造现场：库里三句，alice 和 bob 各 pull 一份
# ==================================================================

def _write_beats(db_path, texts=('第一句', '第二句', '第三句')):
    from storygraph.collab import collab_store as cs
    from storygraph.story.excel_export import export_beats_to_excel
    beats = [Beat(seq=i + 1, label='ch01', speaker='e', text=t)
             for i, t in enumerate(texts)]
    assign_identity(beats)
    return beats


def _seed(tmp_path):
    """建库 + seed（force 整表写一次），返回 (db, 一个临时表路径)。"""
    from storygraph.collab import collab_store as cs
    from storygraph.story.excel_export import export_beats_to_excel

    db = tmp_path / 'collab.sqlite3'
    cs.init_db(db, users=('alice', 'bob'))
    beats = _write_beats(db)
    seed = tmp_path / 'seed.xlsx'
    export_beats_to_excel(beats, seed)
    cs.push(db, seed, actor='alice', force=True)
    return db


def _pull(db, path, actor):
    from storygraph.collab import collab_store as cs
    cs.pull(db, path, actor=actor)
    return Path(path)


def _sheet_payload(sheet, line_id) -> dict:
    """本地表里那一行的 payload（用协作库同一段编解码器）。"""
    from storygraph.collab.collab_store import _beat_to_payload
    from storygraph.story.excel_import import import_beats_from_excel
    for b in import_beats_from_excel(sheet):
        if b.line_id == line_id:
            return _beat_to_payload(b)
    raise AssertionError(f'{line_id} 不在表里')


def _set_cell(sheet, line_id, field, value):
    """改本地表的一格（模拟文案在 Excel 里改了这一列）。"""
    import openpyxl
    from storygraph.story.beat_fields import BEAT_HEADERS
    from storygraph.story.script_table import BEAT_FIELDS

    header = str(BEAT_HEADERS.get(field, field))
    wb = openpyxl.load_workbook(sheet)
    try:
        ws = wb['Beats']
        headers = [str(c.value or '').strip() for c in ws[1]]
        col = headers.index(header) + 1
        id_col = headers.index(str(BEAT_HEADERS.get('LineId', 'LineId'))) + 1
        for r in range(2, ws.max_row + 1):
            if str(ws.cell(row=r, column=id_col).value or '').strip() == line_id:
                ws.cell(row=r, column=col, value=value)
                break
        else:
            raise AssertionError(f'{line_id} 不在 {sheet} 的 Beats 表里')
        wb.save(sheet)
    finally:
        wb.close()


def _delete_row(sheet, line_id):
    import openpyxl
    from storygraph.story.beat_fields import BEAT_HEADERS

    wb = openpyxl.load_workbook(sheet)
    try:
        ws = wb['Beats']
        headers = [str(c.value or '').strip() for c in ws[1]]
        id_col = headers.index(str(BEAT_HEADERS.get('LineId', 'LineId'))) + 1
        for r in range(2, ws.max_row + 1):
            if str(ws.cell(row=r, column=id_col).value or '').strip() == line_id:
                ws.delete_rows(r)
                wb.save(sheet)
                return
        raise AssertionError(f'{line_id} 不在表里')
    finally:
        wb.close()


def _db_payload(db, line_id) -> dict:
    from storygraph.collab import collab_store as cs
    with cs.connect(db) as conn:
        row = conn.execute('SELECT payload FROM beats WHERE line_id=?',
                           (line_id,)).fetchone()
    return json.loads(row['payload']) if row else {}


def _row(db, line_id):
    from storygraph.collab import collab_store as cs
    with cs.connect(db) as conn:
        return conn.execute('SELECT * FROM beats WHERE line_id=?',
                            (line_id,)).fetchone()


def _append_row(sheet, line_id, seq, text, label='ch01'):
    """在本地表末尾追加一行（模拟用户在 Excel 里新插了一句）。"""
    import openpyxl
    from storygraph.story.beat_fields import BEAT_HEADERS

    wb = openpyxl.load_workbook(sheet)
    try:
        ws = wb['Beats']
        headers = [str(c.value or '').strip() for c in ws[1]]
        row = [''] * len(headers)

        def put(field, value):
            row[headers.index(str(BEAT_HEADERS.get(field, field)))] = value

        put('LineId', line_id)
        put('Label', label)
        put('Seq', seq)
        put('Text', text)
        ws.append(row)
        wb.save(sheet)
    finally:
        wb.close()


@pytest.fixture
def world(tmp_path):
    """
    一个"两人并行"的世界：

      alice 的表 = 拉取后由她改；bob 的表 = 拉取后由他改并先 push。
    """
    from storygraph.collab import collab_store as cs

    db = _seed(tmp_path)
    alice = _pull(db, tmp_path / 'alice.xlsx', 'alice')
    bob = _pull(db, tmp_path / 'bob.xlsx', 'bob')

    class W:
        pass

    w = W()
    w.db, w.alice, w.bob, w.cs = db, alice, bob, cs
    w.set_alice = lambda lid, f, v: _set_cell(alice, lid, f, v)
    w.set_bob = lambda lid, f, v: _set_cell(bob, lid, f, v)
    w.push_bob = lambda **kw: cs.push(db, bob, actor='bob', **kw)
    w.push_alice = lambda **kw: cs.push(db, alice, actor='alice', **kw)
    return w


# ==================================================================
#  ① 不同字段 → 静默合并
# ==================================================================

class TestDifferentFieldsMergeSilently:
    def test_text_mine_music_theirs(self, world):
        world.set_bob('ch01.0002', 'BGM', 'bgm_new.ogg')
        assert not world.push_bob()['conflicts']

        world.set_alice('ch01.0002', 'Text', '我改的台词')
        res = world.push_alice()

        assert not res['conflicts'], res['conflicts']
        assert 'ch01.0002' in res['merged'], res
        payload = _db_payload(world.db, 'ch01.0002')
        assert payload['Text'] == '我改的台词'
        # 音乐那格会被表里的写法归一（`[音乐] xxx`），只认"TA 的值到了"
        assert 'bgm_new.ogg' in payload['BGM'], payload['BGM']

    def test_the_tool_says_what_it_did(self, world):
        from storygraph.collab.collab_store import auto_merge_summary

        world.set_bob('ch01.0002', 'BGM', 'bgm_new.ogg')
        world.push_bob()
        world.set_alice('ch01.0002', 'Text', '我改的台词')
        res = world.push_alice()

        text = auto_merge_summary(res)
        assert '自动合并了 1 行' in text, text
        assert res['auto_merged_fields'] >= 1

    def test_both_sides_to_the_same_value_is_not_a_conflict(self, world):
        world.set_bob('ch01.0002', 'Text', '我们写成了同一句')
        world.push_bob()
        world.set_alice('ch01.0002', 'Text', '我们写成了同一句')
        res = world.push_alice()
        assert not res['conflicts'], res['conflicts']


# ==================================================================
#  ② 同一字段 → 冲突（带三方值）
# ==================================================================

class TestSameFieldConflicts:
    def test_both_texts_are_returned_with_the_base(self, world):
        world.set_bob('ch01.0002', 'Text', 'TA 写的')
        world.push_bob()
        world.set_alice('ch01.0002', 'Text', '我写的')
        res = world.push_alice()

        assert len(res['conflicts']) == 1, res['conflicts']
        c = res['conflicts'][0]
        assert c['kind'] == 'fields'
        assert c['line_id'] == 'ch01.0002'
        fields = {f['field']: f for f in c['fields']}
        assert fields['Text']['mine'] == '我写的'
        assert fields['Text']['theirs'] == 'TA 写的'
        assert fields['Text']['base'] == '第二句'

    def test_nothing_is_written_for_a_conflicted_line(self, world):
        world.set_bob('ch01.0002', 'Text', 'TA 写的')
        world.push_bob()
        before = _row(world.db, 'ch01.0002')['revision']
        world.set_alice('ch01.0002', 'Text', '我写的')
        world.push_alice()
        after = _row(world.db, 'ch01.0002')['revision']
        assert after == before, '冲突的行不许偷偷写进去'
        assert _db_payload(world.db, 'ch01.0002')['Text'] == 'TA 写的'

    def test_only_the_edit_side_is_blamed(self, world):
        """TA 改了台词、我改了音乐 → 冲突里只有台词，音乐已经合进去了。"""
        world.set_bob('ch01.0002', 'Text', 'TA 写的')
        world.push_bob()
        world.set_alice('ch01.0002', 'Text', '我写的')
        world.set_alice('ch01.0002', 'BGM', 'bgm_mine.ogg')
        res = world.push_alice()
        c = res['conflicts'][0]
        assert [f['field'] for f in c['fields']] == ['Text']


# ==================================================================
#  ③ 只有一方改 → 不算冲突、也不写
# ==================================================================

class TestOneSideOnly:
    def test_nothing_changed_on_my_side_is_unchanged(self, world):
        world.set_bob('ch01.0002', 'Text', 'TA 改的')
        world.push_bob()
        res = world.push_alice()
        assert not res['conflicts']
        assert 'ch01.0002' in res['unchanged'], res

    def test_the_revision_does_not_move_for_unchanged_rows(self, world):
        world.set_bob('ch01.0002', 'Text', 'TA 改的')
        world.push_bob()
        before = _row(world.db, 'ch01.0002')['revision']
        world.push_alice()
        assert _row(world.db, 'ch01.0002')['revision'] == before


# ==================================================================
#  ④ 顺序 + 内容 → 自动合并
# ==================================================================

class TestSeqAndContent:
    def test_seq_mine_content_theirs(self, world):
        world.set_bob('ch01.0002', 'Text', 'TA 改的内容')
        world.push_bob()
        # alice 只调顺序（Seq 9），没动内容
        world.set_alice('ch01.0002', 'Seq', 9)
        res = world.push_alice()
        assert not res['conflicts'], res['conflicts']
        got = _row(world.db, 'ch01.0002')
        assert int(got['seq']) == 9
        assert json.loads(got['payload'])['Text'] == 'TA 改的内容'


# ==================================================================
#  ⑤ 冲突界面：三种落地
# ==================================================================

class TestResolutions:
    def test_pick_theirs(self, world):
        world.set_bob('ch01.0002', 'Text', 'TA 写的')
        world.push_bob()
        world.set_alice('ch01.0002', 'Text', '我写的')
        world.push_alice()

        res = world.cs.resolve_conflicts(
            world.db, world.alice, 'alice',
            [{'line_id': 'ch01.0002', 'kind': 'fields',
              'values': {'Text': 'theirs'}}])
        assert res['applied'] == ['ch01.0002'], res
        assert _db_payload(world.db, 'ch01.0002')['Text'] == 'TA 写的'
        # 本地表也跟上（否则下一次 push 又报同一条）
        assert _sheet_payload(world.alice, 'ch01.0002')['Text'] == 'TA 写的'
        again = world.push_alice()
        assert not again['conflicts'], again['conflicts']

    def test_pick_mine(self, world):
        world.set_bob('ch01.0002', 'Text', 'TA 写的')
        world.push_bob()
        world.set_alice('ch01.0002', 'Text', '我写的')
        world.push_alice()
        world.cs.resolve_conflicts(
            world.db, world.alice, 'alice',
            [{'line_id': 'ch01.0002', 'kind': 'fields',
              'values': {'Text': 'mine'}}])
        assert _db_payload(world.db, 'ch01.0002')['Text'] == '我写的'
        assert _sheet_payload(world.alice, 'ch01.0002')['Text'] == '我写的'

    def test_custom_text(self, world):
        world.set_bob('ch01.0002', 'Text', 'TA 写的')
        world.push_bob()
        world.set_alice('ch01.0002', 'Text', '我写的')
        world.push_alice()
        world.cs.resolve_conflicts(
            world.db, world.alice, 'alice',
            [{'line_id': 'ch01.0002', 'kind': 'fields',
              'values': {'Text': '我自己拼的一版'}}])
        assert _db_payload(world.db, 'ch01.0002')['Text'] == '我自己拼的一版'

    def test_keep_both_makes_a_second_line(self, world):
        world.set_bob('ch01.0002', 'Text', 'TA 的那句')
        world.push_bob()
        world.set_alice('ch01.0002', 'Text', '我的那句')
        world.push_alice()

        res = world.cs.resolve_conflicts(
            world.db, world.alice, 'alice',
            [{'line_id': 'ch01.0002', 'kind': 'keep_both',
              'order': 'mine_first'}])
        assert res['both_kept'], res
        dup = res['both_kept'][0]['dup_line_id']
        assert _db_payload(world.db, 'ch01.0002')['Text'] == '我的那句'
        assert _db_payload(world.db, dup)['Text'] == 'TA 的那句'
        assert int(_row(world.db, dup)['seq']) == 3          # 原 seq 2 + 1
        # 本地表里也要看得到这两句
        assert _sheet_payload(world.alice, dup)['Text'] == 'TA 的那句'
        # 后面那一行的序号顺延了（不许两行撞在同一个位置）
        assert int(_row(world.db, 'ch01.0003')['seq']) >= 4

    def test_unresolved_field_is_refused(self, world):
        world.set_bob('ch01.0002', 'Text', 'TA 写的')
        world.push_bob()
        world.set_alice('ch01.0002', 'Text', '我写的')
        world.push_alice()
        res = world.cs.resolve_conflicts(
            world.db, world.alice, 'alice',
            [{'line_id': 'ch01.0002', 'kind': 'fields', 'values': {}}])
        assert res['applied'] == []
        assert res['errors'] and '还没决定' in res['errors'][0]['error']


# ==================================================================
#  ⑥ 部分提交
# ==================================================================

class TestPartialCommit:
    def test_only_pushes_the_selected_rows(self, world):
        world.set_bob('ch01.0002', 'Text', 'TA 写的')
        world.push_bob()
        world.set_alice('ch01.0002', 'Text', '我写的')       # 会冲突
        world.set_alice('ch01.0001', 'Text', '干净的改动')     # 不冲突
        world.push_alice(only=['ch01.0001'])
        assert _db_payload(world.db, 'ch01.0001')['Text'] == '干净的改动'
        assert _db_payload(world.db, 'ch01.0002')['Text'] == 'TA 写的'

    def test_partial_commit_does_not_sweep_deletions(self, world):
        _delete_row(world.alice, 'ch01.0003')
        world.push_alice(only=['ch01.0001'])
        assert _row(world.db, 'ch01.0003')['deleted'] == 0, \
            '部分提交不许把没选中的行当删除扫掉'


# ==================================================================
#  ⑦ 结构：我删的那句 TA 改过
# ==================================================================

class TestStructural:
    def test_a_line_i_deleted_but_they_edited_is_kept(self, world):
        world.set_bob('ch01.0002', 'Text', 'TA 在这句上改了字')
        world.push_bob()
        _delete_row(world.alice, 'ch01.0002')
        res = world.push_alice()
        assert res['kept'], res
        assert res['kept'][0]['line_id'] == 'ch01.0002'
        assert '你删过' in res['kept'][0]['hint']
        assert _row(world.db, 'ch01.0002')['deleted'] == 0
        assert _db_payload(world.db, 'ch01.0002')['Text'] == 'TA 在这句上改了字'

    def test_a_stale_local_sheet_does_not_resurrect(self, world):
        """我删了一行 → 库里软删除。我手上那份旧表再推 → 不复活。"""
        import shutil
        stale = world.alice.with_name('alice_stale.xlsx')
        shutil.copy(world.alice, stale)      # 删除**之前**的那份旧表
        _delete_row(world.alice, 'ch01.0003')
        world.push_alice()
        assert _row(world.db, 'ch01.0003')['deleted'] == 1
        res = world.cs.push(world.db, stale, actor='alice')
        assert 'ch01.0003' in res['skipped_stale'], res
        assert _row(world.db, 'ch01.0003')['deleted'] == 1

    def test_two_inserts_at_the_same_slot(self, world):
        # bob 和 alice 都在 seq=10 插了一行（各自 pull 之后才插的）——
        # 库不认识的"同位置"就是这种：两个新行撞在同一个 Seq。
        _append_row(world.bob, 'ch01.0100', 10, 'bob 新插的')
        assert not world.push_bob()['conflicts']

        _append_row(world.alice, 'ch01.0101', 10, 'alice 新插的')
        res = world.push_alice()
        kinds = [c['kind'] for c in res['conflicts']]
        assert 'insert_same_slot' in kinds, res['conflicts']
        c = [x for x in res['conflicts'] if x['kind'] == 'insert_same_slot'][0]
        assert c['mine']['Text'] == 'alice 新插的'
        assert c['theirs']['Text'] == 'bob 新插的'
        assert c['theirs_line_id'] == 'ch01.0100'

    def test_keep_both_puts_my_insert_next_to_theirs(self, world):
        _append_row(world.bob, 'ch01.0100', 10, 'bob 新插的')
        world.push_bob()
        _append_row(world.alice, 'ch01.0101', 10, 'alice 新插的')
        res = world.push_alice()
        c = [x for x in res['conflicts'] if x['kind'] == 'insert_same_slot'][0]

        out = world.cs.resolve_conflicts(
            world.db, world.alice, 'alice',
            [{'line_id': 'ch01.0101', 'kind': 'insert_both',
              'order': 'theirs_first', 'theirs_line_id': c['theirs_line_id']}])
        assert out['applied'] == ['ch01.0101'], out
        assert int(_row(world.db, 'ch01.0101')['seq']) == 11
        assert _db_payload(world.db, 'ch01.0101')['Text'] == 'alice 新插的'
        assert int(_row(world.db, 'ch01.0100')['seq']) == 10
        # 本地表也更新到 11，下一次 push 不会再撞同一个位置
        assert _sheet_payload(world.alice, 'ch01.0101')['Seq'] in (11, '11')
        assert not world.push_alice()['conflicts']


# ==================================================================
#  ⑧ 没有 base → 降级两方比较，并且说清楚
# ==================================================================

class TestNoBaseDegrades:
    def test_an_old_sheet_without_base_is_reported_as_such(self, world):
        """模拟旧版 Excel 另存：协作信息表还在，但没 BasePayload 列。"""
        import openpyxl

        wb = openpyxl.load_workbook(world.alice)
        ws = wb['协作信息']
        col = [str(c.value or '') for c in ws[1]].index('BasePayload') + 1
        ws.delete_cols(col)
        wb.save(world.alice)

        world.set_bob('ch01.0002', 'Text', 'TA 改的')
        world.push_bob()
        world.set_alice('ch01.0002', 'Text', '我改了')
        res = world.push_alice()
        assert res['conflicts'], res
        c = res['conflicts'][0]
        assert c['reason'] == 'no_baseline'
        assert '只能比谁新' in c['hint'], c['hint']

    def test_pull_writes_the_base_payload(self, world):
        from storygraph.collab.collab_store import read_collab_sheet

        got = read_collab_sheet(world.alice)
        assert got['ch01.0001']['base'], 'pull 必须把每一行的内容写进 base'
        assert got['ch01.0001']['base']['Text'] == '第一句'
