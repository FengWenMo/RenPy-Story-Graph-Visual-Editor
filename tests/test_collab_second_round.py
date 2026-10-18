"""
第二次、第三次协作必须**和第一次一样准**（2026-10-18 现场事故的回归网）
======================================================================

事故（用户实测，逐条复现）：

    库刚 import-ir   每行 payload 中位数 622 字符
    第一次 pull      BasePayload 651 字符，解析正常
    某人 push 一次   book.0045 payload 变成 69,341 字符（FileBlocks 64,000）
    第二次 pull      BasePayload 被写成 32,767 字符（Excel 单元格上限），
                     JSON 在 char 592 处截断
    这时再 push      base = None → 退化两方比较
    冲突返回         {"kind": "revision", "fields": [], "reason": "revision"}

根因不是合并算法（它是对的，第一次实测很漂亮），是**数据放错了地方**：

  · `excel_import` 把整份顶层文本（5 个脚本的 screen/define/init）挂到一拍；
  · `collab_store` 又把它塞进那一行的 payload —— 一行 64KB；
  · 写进 Excel 单元格，必然被 32767 截断。

这个文件守三件事：

  1. `FileBlocks` **不在** payload 里（住 `file_blocks` 表），payload 回到几百字节；
  2. `BasePayload` **永远塞得进一个格子**（长了就存字段摘要，合并语义不变）；
  3. 第 2、3 次协作的结论与第 1 次**逐字段一致** —— 不退化、不报假冲突。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import openpyxl
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.collab import collab_store as cs                       # noqa: E402
from storygraph.story.beat_fields import BEAT_HEADERS                 # noqa: E402
from storygraph.story.beat_model import (CELL_TEXT_LIMIT, Beat,       # noqa: E402
                                         join_file_blocks)
from storygraph.story.excel_export import export_beats_to_excel       # noqa: E402

#: 一段真实的"顶层保真块"：6000 行、约 6 万字符（真机项目就是这么多）
BLOCKS = join_file_blocks([
    ('init python:', '\n'.join(f'    persistent.k{i} = {i}' for i in range(3000))),
    ('define s:', '    Character("s", color="#c8ffc8")'),
    ('screen extra:', '\n'.join(f'    text "line {i}"' for i in range(3000))),
])


def _beats(n=4, label='ch01'):
    out = [Beat(seq=i, label=label, kind='say', speaker='e',
                text=f'第 {i} 句', comment=f'## 备注 {i}',
                line_id=f'{label}.{i:04d}') for i in range(1, n + 1)]
    out[0].file_blocks = BLOCKS
    return out


def _init(tmp_path, beats=None):
    db = tmp_path / 'collab.db'
    cs.init_db(db, users=['alice', 'bob'])
    seed = tmp_path / 'seed.xlsx'
    export_beats_to_excel(beats or _beats(), seed, include_dropdowns=False)
    cs.push(db, seed, actor='alice', force=True)
    return db, seed


def _payload(db, lid) -> dict:
    with cs.connect(db) as conn:
        row = conn.execute('SELECT payload FROM beats WHERE line_id=?',
                           (lid,)).fetchone()
    return json.loads(row['payload'])


def _set_cell(path, lid, field, value):
    """像策划那样改一格（走 openpyxl，不动别的列）。"""
    wb = openpyxl.load_workbook(path)
    try:
        ws = wb['Beats']
        headers = [str(c.value or '') for c in ws[1]]
        id_col = headers.index(BEAT_HEADERS['LineId']) + 1
        col = headers.index(BEAT_HEADERS[field]) + 1
        for r in range(2, ws.max_row + 1):
            if str(ws.cell(row=r, column=id_col).value or '').strip() == lid:
                ws.cell(row=r, column=col, value=value)
                break
        else:
            raise AssertionError(f'表里没有 {lid}')
        wb.save(path)
    finally:
        wb.close()


def _collab_base(path) -> dict:
    wb = openpyxl.load_workbook(path, data_only=True)
    try:
        ws = wb[cs.COLLAB_SHEET]
        headers = [str(c.value or '') for c in ws[1]]
        col = headers.index(cs.BASE_FIELD) + 1
        lid_col = headers.index('LineId') + 1
        out = {}
        for r in range(2, ws.max_row + 1):
            lid = str(ws.cell(row=r, column=lid_col).value or '').strip()
            if lid and lid != cs.PULLED_AT_KEY:
                out[lid] = ws.cell(row=r, column=col).value
        return out
    finally:
        wb.close()


class TestFileBlocksLeaveTheRowPayload:
    """文件级数据不许再混进行级数据（根因）。"""

    def test_payload_has_no_file_blocks_and_stays_small(self, tmp_path):
        db, _ = _init(tmp_path)
        p = _payload(db, 'ch01.0001')
        assert cs._FILE_BLOCKS_KEY not in p, (
            '文件级块又回到行 payload 里了 —— 一行 64KB，BasePayload 必然被截')
        size = len(json.dumps(p, ensure_ascii=False))
        assert size < 5000, f'一行 payload {size} 字符，还是太大了'

    def test_blocks_survive_in_their_own_table(self, tmp_path):
        db, _ = _init(tmp_path)
        with cs.connect(db) as conn:
            got = cs.load_file_blocks(conn)
        assert 'persistent.k2999' in got[cs.LIBRARY_BLOCKS]
        assert 'screen extra' in got[cs.LIBRARY_BLOCKS]

    def test_two_pushes_do_not_duplicate_the_blocks(self, tmp_path):
        """
        同一段顶层文本不许登记两次。

        真机踩到过：Excel 的「文件级保真」表里没有章节信息，读回来只能挂到
        第一张拍；而 pull 出去的拍序与送回来的可能不同 → 同一段被记到两个
        章节名下 → 再渲染就是**两份 init python**（内容是坏的，不是"看着别扭"）。
        """
        db, _ = _init(tmp_path)
        for _ in range(2):
            x = tmp_path / 'round.xlsx'
            cs.pull(db, x, actor='bob')
            cs.push(db, x, actor='bob')
        with cs.connect(db) as conn:
            rows = conn.execute(
                'SELECT label, length(text) AS n FROM file_blocks').fetchall()
        assert len(rows) == 1, [dict(r) for r in rows]
        beats, _ = cs.export_ir_from_db(db)
        joined = '\n'.join(str(b.file_blocks or '') for b in beats)
        assert joined.count('screen extra:') == 1

    def test_pull_puts_them_back_on_the_chapter(self, tmp_path):
        """它们还得**回到拍上**（渲染 / 导出读拍列表），否则只是搬走了。"""
        db, _ = _init(tmp_path)
        out = tmp_path / 'pull.xlsx'
        cs.pull(db, out, actor='bob')
        beats, _ = cs.export_ir_from_db(db)
        assert beats[0].file_blocks == BLOCKS or 'persistent.k0' in beats[0].file_blocks

    def test_render_from_db_has_one_copy(self, tmp_path):
        """不会毁文件：再渲染时 define s / default book 各只出现一次。"""
        db, _ = _init(tmp_path)
        beats, _ = cs.export_ir_from_db(db)
        text = '\n'.join(str(b.file_blocks or '') for b in beats)
        assert text.count('define s:') == 1
        assert text.count('screen extra:') == 1


class TestBaseAlwaysFits:
    def test_every_base_cell_parses_into_a_dict(self, tmp_path):
        db, _ = _init(tmp_path)
        out = tmp_path / 'p1.xlsx'
        cs.pull(db, out, actor='bob')
        bases = _collab_base(out)
        assert bases, '协作信息表里没有 BasePayload'
        for lid, raw in bases.items():
            assert raw is not None and str(raw).strip(), f'{lid} 没有基线'
            assert len(str(raw)) <= CELL_TEXT_LIMIT, (
                f'{lid} 的基线 {len(str(raw))} 字符 —— 会被 Excel 截断')
            got = json.loads(str(raw))          # 截断了就抛
            assert isinstance(got, dict) and got

    def test_a_30k_text_line_still_merges_field_by_field(self, tmp_path):
        """一句 31500 字（JSON 顶到 32KB）：基线放不下原文 → 存字段摘要，**合并照样准**。"""
        beats = _beats()
        beats[1].text = '长' * 31500
        db, _ = _init(tmp_path, beats)

        # 两人**同时**各拉一份（都基于同一版，这才叫"两边都在改"）
        a, b = tmp_path / 'alice.xlsx', tmp_path / 'bob.xlsx'
        cs.pull(db, a, actor='alice')
        cs.pull(db, b, actor='bob')
        bases = _collab_base(b)
        assert len(str(bases['ch01.0002'])) <= CELL_TEXT_LIMIT
        assert cs.BASE_HASHES_KEY in json.loads(str(bases['ch01.0002'])), (
            '31500 字的行应该走摘要基线')

        # alice 改这一句的备注
        _set_cell(a, 'ch01.0002', 'Comment', '## ALICE 改的备注')
        assert cs.push(db, a, actor='alice')['updated'] == ['ch01.0002']

        # bob 改同一句的台词 → 应该自动合并（不同字段），不是冲突
        _set_cell(b, 'ch01.0002', 'Text', 'BOB 改的长台词' * 3)
        res = cs.push(db, b, actor='bob')
        assert not res['conflicts'], res['conflicts']
        assert res['auto_merged_fields'] >= 1, res

        p = _payload(db, 'ch01.0002')
        assert p['Text'] == 'BOB 改的长台词' * 3, '我的台词没写进去'
        assert p['Comment'] == '## ALICE 改的备注', '对方的备注被吃掉了'


class TestSecondAndThirdRoundStayExact:
    """用户的核心指控：第二次起就退化。这条网专门跑第 2、3 次。"""

    def test_round_two_and_three_are_still_field_level(self, tmp_path):
        db, _ = _init(tmp_path)

        # 第 1 轮：两人同时拉 → alice 改台词、bob 改备注
        a, b = tmp_path / 'alice1.xlsx', tmp_path / 'bob1.xlsx'
        cs.pull(db, a, actor='alice')
        cs.pull(db, b, actor='bob')
        _set_cell(a, 'ch01.0003', 'Text', 'ALICE 第一轮')
        r1 = cs.push(db, a, actor='alice')
        assert r1['updated'] == ['ch01.0003'], r1

        _set_cell(b, 'ch01.0003', 'Comment', '## BOB 第一轮')
        r2 = cs.push(db, b, actor='bob')
        assert not r2['degraded'], f'第 2 次 push 退化成两方比较了：{r2}'
        assert not r2['conflicts'], f'第 2 次 push 报假冲突：{r2["conflicts"]}'
        assert r2['auto_merged_fields'] >= 1, r2
        p = _payload(db, 'ch01.0003')
        assert p['Text'] == 'ALICE 第一轮'
        assert p['Comment'] == '## BOB 第一轮'

        # 第 3 轮：再来一次并发（alice 改台词、bob 改说话人）
        a3, b3 = tmp_path / 'alice3.xlsx', tmp_path / 'bob3.xlsx'
        cs.pull(db, a3, actor='alice')
        cs.pull(db, b3, actor='bob')
        _set_cell(a3, 'ch01.0003', 'Text', 'ALICE 第三轮')
        assert cs.push(db, a3, actor='alice')['updated'] == ['ch01.0003']
        _set_cell(b3, 'ch01.0003', 'Speaker', 'n')
        r3 = cs.push(db, b3, actor='bob')
        assert not r3['degraded'], f'第 3 次 push 退化了：{r3}'
        assert not r3['conflicts'], f'第 3 次 push 报假冲突：{r3["conflicts"]}'
        assert r3['auto_merged_fields'] >= 1, r3
        p = _payload(db, 'ch01.0003')
        assert p['Text'] == 'ALICE 第三轮'
        assert p['Speaker'] == 'n'
        assert p['Comment'] == '## BOB 第一轮', '第 2 轮合进来的备注被第 3 轮吃掉了'

    def test_real_conflict_still_names_the_field_on_round_two(self, tmp_path):
        """真冲突（同一格两边各改）在第 2 轮也要**说出字段**，不是裸 revision。"""
        db, _ = _init(tmp_path)
        a, b = tmp_path / 'alice.xlsx', tmp_path / 'bob.xlsx'
        cs.pull(db, a, actor='alice')
        cs.pull(db, b, actor='bob')
        _set_cell(a, 'ch01.0002', 'Text', 'ALICE 的版本')
        assert cs.push(db, a, actor='alice')['updated'] == ['ch01.0002']

        _set_cell(b, 'ch01.0002', 'Text', 'BOB 的版本')
        res = cs.push(db, b, actor='bob')
        assert res['conflicts'], '同一格两边各改，必须报冲突'
        c = res['conflicts'][0]
        assert c['kind'] == 'fields', f'退化成整行冲突了：{c}'
        assert [f['field'] for f in c['fields']] == ['Text']
        assert c['fields'][0]['theirs'] == 'ALICE 的版本'
        assert c['fields'][0]['mine'] == 'BOB 的版本'


class TestMigrationFromSchemaOne:
    """已经在用的库（schema 1）要能自动搬过去，不能让人重导一遍。"""

    def test_old_payload_with_file_blocks_is_migrated(self, tmp_path):
        db = tmp_path / 'old.db'
        cs.init_db(db)
        payload = {'LineId': 'a.0001', 'Seq': 1, 'Label': 'a', 'Text': 'hi',
                   'FileBlocks': BLOCKS}
        with cs.connect(db) as conn:
            conn.execute(
                'INSERT INTO beats(line_id,label,seq,payload,content_hash,'
                'status,revision,updated_by,updated_at) '
                'VALUES(?,?,?,?,?,?,1,?,?)',
                ('a.0001', 'a', 1, json.dumps(payload, ensure_ascii=False),
                 'x', 'draft', 'alice', '2026-10-17T00:00:00'))
            # 模拟"升级前的老库"：把迁移标记抹掉，让它真的跑一次
            conn.execute("DELETE FROM meta WHERE key='file_blocks_split'")
        with cs.connect(db) as conn:
            row = conn.execute('SELECT payload FROM beats').fetchone()
            got = json.loads(row['payload'])
            assert 'FileBlocks' not in got, '老 payload 里的 FileBlocks 没搬走'
            blocks = cs.load_file_blocks(conn)
        assert 'persistent.k2999' in blocks[cs.LIBRARY_BLOCKS]

    def test_migration_runs_once(self, tmp_path):
        db, _ = _init(tmp_path)
        with cs.connect(db) as conn:
            n1 = conn.execute("SELECT COUNT(*) FROM meta WHERE key="
                              "'file_blocks_split'").fetchone()[0]
        assert n1 == 1
