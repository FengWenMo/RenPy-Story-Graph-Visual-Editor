# -*- coding: utf-8 -*-
"""
P0 审计探针 5（第二轮）：真机全量「载入 → 原地导出」字节级往返。

攻击思路：上一轮用合成样本，这轮把**真项目 55 个 .rpy**（含 BOM/CRLF/
Tab/混合缩进/中文 label/自定义语句的全部真实形态）搬进临时工程，逐个：

  阶段一（全部文件，不改一个字节）：load → export 原地
    判据 A：没改任何东西，盘上字节必须一字不动（untouched 或零 diff）
    判据 B：load 出 0 拍的文件要报出来（静默空章节）

  阶段二（抽样 ~12 个，改一拍）：load → 改第一句台词 → export 原地
    判据 C：diff 只能有「生成标记 2 行 + 改写的那 1 行」，其余逐字节相同
    判据 D：导出后重新解析，改动必须真落进去

只读真项目：全部操作在临时目录的副本上进行。
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SRC_ROOT = Path(r'D:\千连万理\RenPyTest\sr_general')
EDIT_TEXT = '审计探针改写的这一句。'

from storygraph.app.editor import EditorSession          # noqa: E402
from storygraph.parsing.discover import discover_scripts  # noqa: E402


def build_temp_project(tmp: Path) -> list:
    """把真项目的 .rpy + 工具清单 + 资产表复制进临时工程（只读真项目）。"""
    dst = tmp / 'game'
    files = []
    for f in discover_scripts(SRC_ROOT):
        rel = f.relative_to(SRC_ROOT)
        t = tmp / rel
        t.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, t)
        files.append(t)
    for extra in ('storygraph.rules.json', 'storygraph.engine_facts.json'):
        s = SRC_ROOT / extra
        if s.is_file():
            shutil.copy2(s, tmp / extra)
    at = SRC_ROOT / 'game' / 'asset_tables'
    if at.is_dir():
        shutil.copytree(at, dst / 'asset_tables')
    return files


def carrier(raw: bytes) -> str:
    tags = []
    if raw.startswith(b'\xef\xbb\xbf'):
        tags.append('BOM')
    if raw.count(b'\r\n'):
        tags.append('CRLF')
    if b'\n' in raw.replace(b'\r\n', b''):
        tags.append('LF')
    if b'\t' in raw:
        tags.append('TAB')
    return '+'.join(tags) or '?'


def main():
    tmp = Path(tempfile.mkdtemp(prefix='sg_p0_real_'))
    files = build_temp_project(tmp)
    print(f'临时工程：{len(files)} 个 .rpy（真项目只读）')

    phase1_bad, phase1_nowrite, phase1_zero = [], [], []
    loaded = {}
    for fp in files:
        before = fp.read_bytes()
        s = EditorSession(project_root=tmp)
        try:
            n = s.load(fp)
        except Exception as e:                    # noqa: BLE001
            phase1_bad.append((fp.name, f'load 抛异常 {type(e).__name__}: {e}'))
            continue
        loaded[fp] = n
        if n == 0:
            phase1_zero.append(fp.name)
            continue
        try:
            res = s.export(fp)
        except Exception as e:                    # noqa: BLE001
            phase1_bad.append((fp.name, f'export 抛异常 {type(e).__name__}: {e}'))
            continue
        after = fp.read_bytes()
        if not res.get('ok'):
            phase1_bad.append((fp.name, f'拒写 codes={res.get("codes")} '
                                       f'{str(res.get("blocking"))[:120]}'))
        elif after != before and not res.get('untouched'):
            phase1_nowrite.append((fp.name, '没改任何东西但盘上字节变了'))
    print('\n=== 阶段一：不改任何东西的原地导出 ===')
    print(f'  有拍可导的文件：{sum(1 for v in loaded.values() if v)}；'
          f'0 拍文件：{len(phase1_zero)} 个 {phase1_zero[:8]}')
    print(f'  拒写/异常：{len(phase1_bad)} 起')
    for name, why in phase1_bad[:15]:
        print(f'    ✗ {name}: {why}')
    print(f'  ★ 零改动却改了字节：{len(phase1_nowrite)} 起')
    for name, why in phase1_nowrite[:15]:
        print(f'    ✗✗ {name}: {why}')

    # ---- 阶段二：抽样改一拍 ----
    def score(fp):
        raw = fp.read_bytes()
        sc = 0
        if raw.startswith(b'\xef\xbb\xbf'):
            sc += 3
        if raw.count(b'\r\n'):
            sc += 2
        if b'\t' in raw:
            sc += 2
        if loaded.get(fp, 0) >= 3:
            sc += 1
        return sc

    cands = [fp for fp in files if loaded.get(fp, 0) > 0]
    cands.sort(key=score, reverse=True)
    sample = cands[:12]

    print(f'\n=== 阶段二：抽样 {len(sample)} 个文件改一拍 ===')
    bad = 0
    for fp in sample:
        before = fp.read_bytes()
        s = EditorSession(project_root=tmp)
        s.load(fp)
        beat = None
        for b in s.beats:
            if str(getattr(b, 'kind', '')) in ('say', 'narration') \
                    and str(getattr(b, 'text', '') or '').strip():
                beat = b
                break
        if beat is None:
            print(f'  – {fp.name}：没有可改的台词拍，跳过')
            continue
        old_text = beat.text
        beat.text = EDIT_TEXT
        try:
            res = s.export(fp)
        except Exception as e:                    # noqa: BLE001
            print(f'  ✗ {fp.name}：export 抛异常 {type(e).__name__}: {e}')
            bad += 1
            continue
        if not res.get('ok'):
            print(f'  ✗ {fp.name}：拒写 codes={res.get("codes")} '
                  f'{str(res.get("blocking"))[:100]}')
            bad += 1
            continue
        after = fp.read_bytes()
        bl = before.splitlines()
        al = after.splitlines()
        changed = [i for i, (x, y) in enumerate(zip(bl, al)) if x != y]
        # 改动必须且只能落在：生成标记（前 2 行）+ 改写的那一行
        allowed = {0, 1}
        ok = True
        detail = ''
        if len(al) != len(bl) + (0 if res.get('untouched') else 2):
            # 行数变化只能是 +2（标记+空行）或 0
            if not (len(al) == len(bl) + 2 or len(al) == len(bl)):
                ok = False
                detail = f'行数 {len(bl)}→{len(al)}'
        stray = [i for i in changed if i not in allowed
                 and EDIT_TEXT.encode('utf-8') not in al[i]]
        if stray:
            ok = False
            detail += f' 意外改动行 {stray[:5]}'
        # 判据 D：重新解析，改动落进去了吗
        s2 = EditorSession(project_root=tmp)
        s2.load(fp)
        landed = any(str(getattr(b, 'text', '') or '') == EDIT_TEXT
                     for b in s2.beats)
        if not landed:
            ok = False
            detail += ' 改动没落进重新解析的结果'
        print(f'  {"✓" if ok else "✗"} {fp.name}（{carrier(before)}，'
              f'{loaded[fp]} 拍）改动行={changed} {detail}')
        if not ok:
            bad += 1
            for i in changed[:6]:
                print(f'      L{i + 1}: {bl[i][:70]!r} -> {al[i][:70]!r}')
    print(f'\n阶段二异常：{bad}/{len(sample)}')
    # 收摊（默认删；要复查现场就 SG_KEEP=1 再跑）—— 不收摊会让
    # `test_disk_hygiene` 挂，也会在 TEMP 里堆出几个 GB
    if os.environ.get('SG_KEEP'):
        print(f'临时工程保留在：{tmp}')
    else:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    main()
