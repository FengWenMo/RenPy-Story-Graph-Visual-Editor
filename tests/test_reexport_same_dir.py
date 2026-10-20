"""
tests/test_reexport_same_dir.py
===============================
**"改完再导一次"必须真的把改动写进去。**

用户 2026-10-20 的真机实测（`chapter0_0.rpy`，245 拍）：

    第一遍 `AI真的拥有自我意识吗？` → 改成 `【改过的台词】` → 再导同一目录
    → `story_ir.json` / `UnityDialogueData.json` 里**还是旧句**
    → 而 `IR_CHECK.md` 说"0 错 0 警告"、回执 `ok: True`

根因（`engine_exporters._merge_by_key`）：老条目先占位、同 key 的新条目被
`continue` 跳过；而 `line_id` 是 `label.序号`（`beat_model.assign_identity`），
**改一句台词不会换 id** —— 于是"再导一次"恰好落在"同 key 用旧值"上。
它比报错更坏：整份产物**自洽**（IR 旧、Unity 从 IR 生成也旧、自检对旧数据
也是干净的），没有任何一处会说"你刚才那一改没生效"。

判据（每条都跑真解析，不手搓 IR）：

  · 改一句 → 导两次 → 中立 IR / Unity / CSV 都是新句；
  · 表里删一行 → 导两次 → IR 里那行**没了**（否则 IR 活着、脚本里没了）；
  · 两章导同一目录 → 两章都在（多章累积不许回退）；
  · 同一份导出里 `line_id` 重复 → 最后一条为准；
  · 替换发生在**原位置**（章内顺序不许被打乱）；
  · 回执的 `check.status` 能把"自检没跑成"和"零错误"分开。
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SCRIPT = '''label ch01:
    scene bg classroom
    e "AI真的拥有自我意识吗？"
    e "第二句"
    "旁白一句"
    return
'''


def _beats(tmp_path: Path, src: str = SCRIPT, name: str = 'ch01.rpy'):
    """真的解析一份 `.rpy` → 演出表（带 line_id / content_hash）。"""
    from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
    from storygraph.story.beat_build import beats_from_parse_result

    path = tmp_path / 'game' / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(src, encoding='utf-8')
    pr, _g = IncrementalRenPyAnalyzer().analyze(files=[path], graph=None)
    return beats_from_parse_result(pr)


def _read(path: Path):
    return json.loads(path.read_text(encoding='utf-8'))


def _by_id(rows, key='line_id'):
    return {row[key]: row for row in rows}


class TestEditThenExportAgain:
    def test_edited_line_reaches_ir_and_adapters(self, tmp_path):
        """改一句 → 导两次 → IR / Unity / CSV 都是新句（P0 的正面判据）。"""
        from storygraph.pipeline.engine_exporters import (
            CSV_NAME, NEUTRAL_IR_NAME, UNITY_DATA_NAME, export_ir)

        beats = _beats(tmp_path)
        out = tmp_path / 'out'
        export_ir('unity', beats, out, project='p0')
        edited = next(b for b in beats if b.text == 'AI真的拥有自我意识吗？')
        lid = edited.line_id
        assert _by_id(_read(out / NEUTRAL_IR_NAME)['lines'])[lid]['text'] \
            == 'AI真的拥有自我意识吗？'

        edited.text = '【改过的台词】'
        export_ir('unity', beats, out, project='p0')
        ir = _by_id(_read(out / NEUTRAL_IR_NAME)['lines'])
        unity = _by_id(_read(out / UNITY_DATA_NAME)['lines'], 'lineId')
        assert ir[lid]['text'] == '【改过的台词】', (
            'IR 里还是旧句 —— 同 key 用了旧值（`_merge_by_key` 的老毛病）')
        assert unity[lid]['text'] == '【改过的台词】', 'Unity 产物还是旧句'
        assert len(ir) == len(beats), '行数变了（合并把老条目丢了）'

        export_ir('csv', beats, out, project='p0')
        # Excel 要 BOM 才认 UTF-8（`test_csv_has_utf8_bom_for_excel` 守着），
        # 所以第一列名前面有个 `\ufeff` —— 读的时候先剥掉。
        text = (out / CSV_NAME).read_text(encoding='utf-8').lstrip('\ufeff')
        rows = list(csv.DictReader(text.splitlines()))
        hits = [r for r in rows if r['line_id'] == lid]
        assert hits and hits[0]['text'] == '【改过的台词】', 'CSV 还是旧句'

    def test_replacement_keeps_the_original_position(self, tmp_path):
        """替换是"原地换值"，不是"删掉旧的再追加到末尾"。"""
        from storygraph.pipeline.engine_exporters import (
            NEUTRAL_IR_NAME, export_ir)

        beats = _beats(tmp_path)
        out = tmp_path / 'out'
        export_ir('json', beats, out, project='p0')
        before = [l['line_id'] for l in _read(out / NEUTRAL_IR_NAME)['lines']]
        target = next(b for b in beats if b.text == '第二句')
        target.text = '第二句（改）'
        export_ir('json', beats, out, project='p0')
        after = [l['line_id'] for l in _read(out / NEUTRAL_IR_NAME)['lines']]
        assert after == before, f'章内顺序被打乱：{before} → {after}'

    def test_deleted_line_disappears_from_the_ir(self, tmp_path):
        """
        表里删掉一行 → 再导 → IR 里也必须没有它。

        只按 `line_id` 合并只能"改"不能"删"：删掉的台词会永远留在 IR
        （和目标引擎）里，而编辑器里早就不存在了 —— 这是同一类谎的另一种形态。
        """
        from storygraph.pipeline.engine_exporters import (
            NEUTRAL_IR_NAME, export_ir)

        beats = _beats(tmp_path)
        out = tmp_path / 'out'
        export_ir('json', beats, out, project='p0')
        gone = next(b for b in beats if b.text == '第二句')
        assert gone.line_id in _by_id(_read(out / NEUTRAL_IR_NAME)['lines'])

        kept = [b for b in beats if b is not gone]
        export_ir('json', kept, out, project='p0')
        ir = _read(out / NEUTRAL_IR_NAME)
        ids = _by_id(ir['lines'])
        assert gone.line_id not in ids, '删掉的行还在 IR 里'
        stage_ids = {row['line_id'] for row in ir['stage']}
        assert gone.line_id not in stage_ids, '删掉的行还有舞台快照'
        assert ir['stats']['lines'] == len(kept), (
            f'stats 没跟着删：{ir["stats"]["lines"]} != {len(kept)}')

    def test_two_chapters_still_accumulate(self, tmp_path):
        """多章导同一目录不许回退成"后一章覆盖前一章"（M8 那条老判据）。"""
        from storygraph.pipeline.engine_exporters import (
            NEUTRAL_IR_NAME, export_ir)

        ch1 = _beats(tmp_path, SCRIPT, 'ch01.rpy')
        ch2 = _beats(tmp_path, 'label ch02:\n    e "第二章台词"\n    return\n',
                     'ch02.rpy')
        out = tmp_path / 'out'
        export_ir('json', ch1, out, project='p9')
        export_ir('json', ch2, out, project='p9')
        labels = {l['label'] for l in _read(out / NEUTRAL_IR_NAME)['lines']}
        assert labels == {'ch01', 'ch02'}, f'两章没累积：{labels}'

    def test_reediting_one_chapter_does_not_touch_the_other(self, tmp_path):
        """
        两章都导过之后，只重导第一章：第一章更新，第二章**原样**。

        这是"按 label 划章"那条规则真正的风险面 —— 划错范围就会把别人那一章
        删掉。它和"多章累积"必须同时成立。
        """
        from storygraph.pipeline.engine_exporters import (
            NEUTRAL_IR_NAME, export_ir)

        ch1 = _beats(tmp_path, SCRIPT, 'ch01.rpy')
        ch2 = _beats(tmp_path, 'label ch02:\n    e "第二章台词"\n    return\n',
                     'ch02.rpy')
        out = tmp_path / 'out'
        export_ir('json', ch1, out, project='p9')
        export_ir('json', ch2, out, project='p9')

        edited = next(b for b in ch1 if b.text == 'AI真的拥有自我意识吗？')
        edited.text = '【第一章改过】'
        export_ir('json', ch1, out, project='p9')

        rows = _read(out / NEUTRAL_IR_NAME)['lines']
        by_label = {}
        for row in rows:
            by_label.setdefault(row['label'], []).append(row['text'])
        assert by_label['ch01'][0] == '【第一章改过】'
        assert by_label['ch02'] == ['第二章台词'], '重导第一章把第二章动了'

    def test_duplicate_id_inside_one_export_keeps_the_last(self, tmp_path):
        """同一份导出里 id 撞了 → 以最后一条为准（与"最后一次导出为准"同向）。"""
        from storygraph.pipeline.engine_exporters import _merge_by_key

        merged = _merge_by_key(
            [{'line_id': 'a.0001', 'text': '老'}],
            [{'line_id': 'a.0001', 'text': '新一'},
             {'line_id': 'a.0001', 'text': '新二'}], 'line_id')
        assert [r['text'] for r in merged] == ['新二']

    def test_broken_ir_on_disk_does_not_lose_the_new_export(self, tmp_path):
        """磁盘上那份 IR 坏了 → 不许把新导出一起弄坏（也不能崩）。"""
        from storygraph.pipeline.engine_exporters import (
            NEUTRAL_IR_NAME, export_ir)

        beats = _beats(tmp_path)
        out = tmp_path / 'out'
        out.mkdir()
        (out / NEUTRAL_IR_NAME).write_text('{坏 json', encoding='utf-8')
        export_ir('json', beats, out, project='p0')
        ir = _read(out / NEUTRAL_IR_NAME)
        assert len(ir['lines']) == len(beats)


class TestReceiptTellsTheTruth:
    """回执里的"自检"栏：`ok` / `failed` 不许长得一样（P1）。"""

    def _session(self, tmp_path: Path):
        from storygraph.app.editor import EditorSession

        beats = _beats(tmp_path)
        session = EditorSession(project_root=tmp_path)
        session.beats = beats
        session.script_path = tmp_path / 'game' / 'ch01.rpy'
        return session

    def test_clean_export_reports_status_ok(self, tmp_path):
        res = self._session(tmp_path).export_ir('json', tmp_path / 'out')
        assert res['ok'] is True
        assert res['check']['status'] == 'ok'
        assert res['check']['error_count'] == 0

    def test_broken_check_is_not_reported_as_clean(self, tmp_path, monkeypatch):
        """
        自检自己崩了 → 回执必须说"没跑成"，**不能**报 `error_count: 0`。

        从前 `session_export.export_ir` 外面包着 `try`，异常一吞，
        回执里就是"0 错 0 警告" —— "没跑成"和"干净"长得一模一样。
        """
        import storygraph.pipeline.ir_validate as IRV

        def boom(*_a, **_kw):
            raise RuntimeError('自检内部错误')

        monkeypatch.setattr(IRV, 'summarize', boom)
        res = self._session(tmp_path).export_ir('json', tmp_path / 'out')
        assert res['ok'] is True, '报告失败不该让导出失败'
        assert res['check']['status'] == 'failed'
        assert '自检内部错误' in res['check']['error']
