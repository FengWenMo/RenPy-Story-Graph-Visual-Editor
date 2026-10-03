#!/usr/bin/env python
"""
给 `beats_from_statements` 补一层**更细的网**（P3-12，2026-10-05 续十五）。

为什么需要它（§19.5 里写过理由，这里是落地）
--------------------------------------------
`beats_from_statements` 是**解析心脏**：538 行分派循环 + 6 个闭包共享
7 个可变状态（`pending` / `seq` / `current_label` / `pending_has_stage` /
`last_speaker` / `struct_done` / `absorbed`）。拆它必须把这些状态显式化 ——
那是**改结构**，不是搬家。

它现有的安全网只有**往返不变式**（parse → beat → xlsx → beat → compile
→ 再解析，逐行文本多重集相等），而那张网**已经带着 3 条已知丢失**
（见 §十七）。所以"拆坏了"和"本来就有 3 条"在那张网上**分不开**。

这一层网换一个更细的判据：**逐拍逐字段**比对 HEAD 版本。
它不追求"产物对不对"，只问"**结构有没有被改动动过**" ——
`heads` 差异 0 就说明"只搬了位置"。

比对字段：`seq / label / kind / speaker / text / condition / comment /
scene / show / hide / at / with_ / music / sound / voice / raw / code /
stage / stage_order / op_order / src_first / src_last / _menu_member /
code_blocks` —— 覆盖 Beat 上所有会影响产物的东西。

用法：
    python tools/_verify_beats.py          # 与 HEAD 比对，0 差异才算通过
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

#: 会影响产物的 Beat 字段（比对用）。`insert_anchor` 是运行期状态、不入表，
#: 但拼错会让下一次导出认错拍，所以也带上。
FIELDS = (
    'seq', 'label', 'kind', 'speaker', 'text', 'condition', 'comment',
    'scene', 'show', 'hide', 'at', 'with_', 'music', 'sound', 'voice',
    'raw', 'code', 'stage', 'stage_order', 'op_order',
    'src_first', 'src_last', 'insert_anchor', 'code_blocks',
)


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def beat_snap(b) -> dict:
    out = {}
    for f in FIELDS:
        v = getattr(b, f, '<missing>')
        if isinstance(v, (list, tuple)):
            v = [str(x) for x in v]
        elif isinstance(v, set):
            v = sorted(str(x) for x in v)
        elif v is not None and not isinstance(v, (str, int, float, bool)):
            v = str(v)
        out[f] = v
    # 结构位单独看：`hasattr` 为假与值为 False 是**两种不同的意图**
    # （见 `_mark_struct` 的说明：手工构造的 Beat 不该被假的 False 骗到）
    out['_has_menu_member'] = hasattr(b, '_menu_member')
    if hasattr(b, '_menu_member'):
        out['_menu_member'] = bool(b._menu_member)
    return out


def snap_all(beats) -> list:
    return [beat_snap(b) for b in beats]


def main() -> int:
    old_src = subprocess.run(
        ['git', 'show', 'HEAD:storygraph/story/beat_build.py'],
        cwd=ROOT, capture_output=True, text=True, encoding='utf-8').stdout
    tmp = ROOT / 'tools' / '_beat_build_head.py'
    tmp.write_text(old_src, encoding='utf-8')
    try:
        OLD = load(tmp, '_bb_head')
        NEW = load(ROOT / 'storygraph' / 'story' / 'beat_build.py',
                   '_bb_new')

        from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
        from _source_paths import REAL_PROJECT

        game = REAL_PROJECT / 'game'
        files = sorted(game.rglob('*.rpy'))
        print(f'真实项目脚本 {len(files)} 个')

        cases = 0
        bad = 0
        beats_total = 0

        def compare(label, statements, lab, labels):
            nonlocal cases, bad, beats_total
            cases += 1
            # 两边都要**全新的 Statement 副本**：这个函数会往语句上写
            # `_menu_member`（解析器标的位），共用一份会互相干扰。
            import copy
            a = snap_all(OLD.beats_from_statements(
                copy.deepcopy(statements), label=lab, labels=(
                    list(labels) if labels else None)))
            b = snap_all(NEW.beats_from_statements(
                copy.deepcopy(statements), label=lab, labels=(
                    list(labels) if labels else None)))
            beats_total += len(a)
            if a == b:
                return
            bad += 1
            if bad <= 4:
                print(f'** {label} 不一致（旧 {len(a)} 拍 / 新 {len(b)} 拍）')
                for i in range(max(len(a), len(b))):
                    x = a[i] if i < len(a) else None
                    y = b[i] if i < len(b) else None
                    if x == y:
                        continue
                    if x is None or y is None:
                        print(f'   第 {i} 拍：只有一边有')
                        break
                    diff = [k for k in x if x[k] != y[k]]
                    print(f'   第 {i} 拍字段不同: {diff[:6]}')
                    for k in diff[:3]:
                        print(f'      {k}: 旧={x[k]!r} 新={y[k]!r}')
                    break

        # —— ① 逐文件（保留原 label）：最接近真实使用 ——
        for fp in files:
            az = IncrementalRenPyAnalyzer()
            pr, _ = az.analyze(files=[fp], graph=None)
            st = list(pr.statements)
            if not st:
                continue
            # 每条语句的 label（解析器给的）
            labs = [getattr(s, 'label', '') or '' for s in st]
            compare(f'{fp.name} 逐文件', st, '', labs)

        # —— ② 全项目一次性（label 跨文件切换，最容易暴露状态残留）——
        az = IncrementalRenPyAnalyzer()
        pr, _ = az.analyze(files=files, graph=None)
        st = list(pr.statements)
        labs = [getattr(s, 'label', '') or '' for s in st]
        print(f'全项目 {len(st)} 条语句')
        compare('全项目一次性', st, '', labs)
        compare('全项目（不传 labels）', st, 'unused', None)

        # —— ③ 按 label 切片（切断累积状态，制造更多边界）——
        by_label: dict = {}
        for s, lb in zip(st, labs):
            by_label.setdefault(lb, []).append(s)
        for i, (lb, ss) in enumerate(sorted(by_label.items())):
            if i >= 80:
                break
            compare(f'label={lb}', ss, lb, None)

        # —— ④ 单条语句（每条 kind 都过一遍，最细的粒度）——
        for i, s in enumerate(st[:400]):
            compare(f'单条 #{i} {s.kind}', [s], '', None)

        # —— ⑤ 边界 ——
        compare('空输入', [], '', None)
        compare('空输入+labels', [], 'x', [])

        print(f'\n比了 {cases} 例 / {beats_total} 拍，不一致 {bad} 例')
        return 1 if bad else 0
    finally:
        tmp.unlink(missing_ok=True)


if __name__ == '__main__':
    sys.exit(main())
