#!/usr/bin/env python
"""
交付就绪检查：**单人在真实项目上走一遍完整旅程**。

为什么用 subprocess 调 CLI 而不是直接调 API：交付给别人用的是命令，
不是函数。API 绿而命令挂是最常见的"以为能交付"。

范围（按用户要求）：**只 Ren'Py 引擎、单人工具、不含协作**。
所以只跑 validate / build / table / compile 四条，`collab` 不碰。

2026-10-05：**三条边界**都拓展过一轮（见文件末尾的"边界现状"输出）——

  ① `build` 在真实项目上"必然失败" → 根因是门禁**逐行编译** `python:` 块体
     的假阳性（10 条 PYTHON_SYNTAX_ERROR），已修 → 现在 build 出得来产物；
  ② CLI 只能全量（没有源 .rpy → 永远重生成）→ 新增 `--source`，
     命令行也能**增量**改已有章节，第三类内容保住；
     补丁没配上又确实要丢顶层语句时 → `OPAQUE_LOST` **拒写**（不静默降级）；
  ③ 顶层 `init python:` 整块静默消失 → 现在随表走隐藏列 `FileBlocks`，
     渲染时回到文件级（真实项目 `whackamole.rpy` 2 个块 / 56 行保住）。

所以本文件除了"跑通"，还要把这三条**量出来**：增量那一步会报
"只改了几行"，保真那一步会报"顶层语句还在不在"。

只读真实项目，所有产物写临时目录。用法：
    D:/Python/python.exe tools/journey_check.py
"""
from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REAL = Path(r'D:\千连万理\RenPyTest\sr_general')
PY = sys.executable

#: 顶层（label 之外）的语句 —— 全量重生成一行都留不下
TOP_LEVEL_RE = re.compile(r'^(?:init(?:\s+\d+)?\s+)?python(?:\s+early)?\s*:'
                          r'|^(?:default|define|screen|style|image|transform'
                          r'|layeredimage)\s', re.M)

#: (步骤名, 参数列表)
STEPS: list = []


def run(args: list, cwd=None) -> tuple:
    p = subprocess.run([PY, '-m', 'storygraph.cli', *args],
                       cwd=str(cwd or ROOT), capture_output=True,
                       text=True, encoding='utf-8', errors='replace')
    return p.returncode, (p.stdout or '') + (p.stderr or '')


def check(name: str, args: list, must_exist: Path = None) -> bool:
    code, out = run(args)
    tail = [l for l in out.strip().splitlines() if l.strip()]
    ok = code == 0
    if must_exist is not None and not must_exist.exists():
        ok = False
        tail.append(f'!! 产物没生成：{must_exist}')
    print(f'  [{"PASS" if ok else "FAIL"}] {name}  (exit={code})')
    for l in tail[:4]:
        print(f'         {l[:110]}')
    if not ok and tail:
        for l in tail[4:8]:
            print(f'         {l[:110]}')
    return ok


def _demo_with_top_level(files: list) -> Path:
    """
    找一个**既含顶层语句、又有 label（= 有拍）**的样本。

    为什么要两个条件都满足：边界②③恰恰只在"顶层定义 + 演出流共存"的
    文件上才看得出来 ——
      · 只有顶层定义（`AAA示例.rpy`）：patcher 无拍可增量，走不到边界②；
      · 只有演出（`chapter*.rpy`）：没有顶层定义可丢，走不到边界③。

    选**最小**的那个：跑得快，且出问题时人眼能看完整个文件。
    """
    cands = []
    for p in files:
        try:
            text = p.read_text(encoding='utf-8')
        except OSError:
            continue
        if not TOP_LEVEL_RE.search(text) or 'label ' not in text:
            continue
        cands.append((len(text.splitlines()), str(p), p))
    if cands:
        return min(cands)[2]
    for p in files:
        try:
            if TOP_LEVEL_RE.search(p.read_text(encoding='utf-8')):
                return p
        except OSError:
            continue
    return files[0]


def _top_level_lines(text: str) -> list:
    """顶层（无缩进、非注释、非 label）的语句行。"""
    out = []
    for ln in text.splitlines():
        if not ln.strip() or ln[:1] in (' ', '\t') or ln.lstrip().startswith('#'):
            continue
        if ln.startswith('label '):
            continue
        out.append(ln.strip())
    return out


def main() -> int:
    if not REAL.exists():
        print(f'真实项目不在：{REAL}（换机则跳过）')
        return 0

    chapters = sorted((REAL / 'game' / 'chapter').glob('*.rpy'))
    minigames = sorted((REAL / 'game').rglob('*.rpy'))
    if not chapters:
        print('没找到章节 .rpy')
        return 0
    src = chapters[0]
    demo = _demo_with_top_level(minigames)      # 边界②③的样本
    print(f'真实项目：{REAL}')
    print(f'样本章节：{src.name}（{len(src.read_text(encoding="utf-8").splitlines())} 行）')
    print(f'顶层语句样本：{demo.relative_to(REAL)}'
          f'（{len(_top_level_lines(demo.read_text(encoding="utf-8")))} 处顶层语句）\n')

    results = {}
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        xl = tmp / f'{src.stem}.xlsx'
        back = tmp / f'{src.stem}.back.rpy'
        compiled = tmp / f'{src.stem}.compiled.rpy'
        outdir = tmp / 'build'

        # —— 边界②/③的样本表与产物 ——
        dxl = tmp / f'{demo.stem}.xlsx'
        dback = tmp / f'{demo.stem}.inc.rpy'
        dplain = tmp / f'{demo.stem}.full.rpy'

        print('-- 1. 导入：.rpy → 演出表 ' + '-' * 28)
        results['rpy2xlsx'] = check(
            'table rpy2xlsx', ['table', 'rpy2xlsx', str(src),
                               '--out', str(xl), '--project', str(REAL)], xl)

        print('\n-- 2. 导出：演出表 → .rpy ' + '-' * 28)
        results['xlsx2rpy'] = check(
            'table xlsx2rpy', ['table', 'xlsx2rpy', str(xl),
                               '--out', str(back), '--project', str(REAL)], back)

        print('\n-- 2b. 边界②：增量导出（--source）' + '-' * 22)
        results['inc_import'] = check(
            f'table rpy2xlsx（{demo.name}）',
            ['table', 'rpy2xlsx', str(demo), '--out', str(dxl),
             '--project', str(REAL)], dxl)
        if dxl.exists():
            results['incremental'] = check(
                'table xlsx2rpy --source',
                ['table', 'xlsx2rpy', str(dxl), '--source', str(demo),
                 '--out', str(dback), '--project', str(REAL)], dback)
            # 全量那条路（不给 --source）：会不会丢顶层语句？
            results['full_no_source'] = check(
                'table xlsx2rpy（不给 --source）',
                ['table', 'xlsx2rpy', str(dxl),
                 '--out', str(dplain), '--project', str(REAL)], dplain)

        print('\n-- 3. 编译：演出表 → .rpy（缺资源写 TODO）' + '-' * 16)
        results['compile'] = check(
            'compile', ['compile', str(xl), '--out', str(compiled),
                        '--project', str(REAL)], compiled)

        # 注意：`validate` / `build` 的 project 是**位置参数**，不是 `--project`
        # （第一版我写成了 `--project`，两条命令直接退出码 2 —— 用错的是我）
        print('\n-- 4. 校验：三级校验（不写盘）' + '-' * 25)
        results['validate'] = check(
            'validate', ['validate', str(REAL)])

        print('\n-- 5. 边界①：构建（校验 + 导出产物）' + '-' * 17)
        results['build'] = check(
            'build', ['build', str(REAL), '--out', str(outdir)])
        gen = outdir / 'story.generated.rpy'
        print(f'  [{"PASS" if gen.exists() else "FAIL"}] '
              f'产物真的写出来了：{gen.name}')
        results['build_wrote'] = gen.exists()

        # 产物本身能不能被引擎解析（跑了半天导出个不能用的脚本 = 白搭）
        print('\n-- 6. 产物可解析性 ' + '-' * 34)
        sys.path.insert(0, str(ROOT))
        from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer

        for label, p in (('xlsx2rpy 产物', back), ('compile 产物', compiled),
                         ('build 产物', gen)):
            if not p.exists():
                print(f'  [SKIP] {label} 没生成')
                continue
            try:
                a = IncrementalRenPyAnalyzer()
                pr, _ = a.analyze(files=[p], graph=None)
                n = len(getattr(pr, 'statements', []) or [])
                print(f'  [PASS] {label} 可解析，{n} 条语句')
                results[f'parse:{label}'] = n > 0
            except Exception as e:                     # noqa: BLE001
                print(f'  [FAIL] {label} 解析失败：{e}')
                results[f'parse:{label}'] = False

        # 内容保真粗判：原文的关键行是否还在产物里
        if back.exists():
            print('\n-- 7. 内容保真（原文关键行 vs 产物）' + '-' * 21)
            src_lines = [l.strip() for l in src.read_text(encoding='utf-8')
                         .splitlines() if l.strip() and not l.strip().startswith('#')]
            out_text = back.read_text(encoding='utf-8')
            out_lines = [l.strip() for l in out_text.splitlines()]

            def squash(s: str) -> str:
                """抹掉**所有**空白再比 —— 用来区分"空格数不同"和"字没了"。"""
                return ''.join(s.split())

            out_squashed = {squash(l) for l in out_lines}
            miss = [l for l in src_lines if l not in out_text]
            # 真丢 = 连抹掉空格都对不上
            really_lost = [l for l in miss if squash(l) not in out_squashed]

            rate = 1 - len(really_lost) / max(1, len(src_lines))
            print(f'  原文 {len(src_lines)} 行；逐字对不上 {len(miss)} 行，'
                  f'其中**抹掉空白后仍对不上** {len(really_lost)} 行')
            print(f'  → 真·内容保真 {rate:.1%}')
            if miss and not really_lost:
                print('  （对不上的全是**空格数差异**，不是内容丢失）')
            for l in really_lost[:6]:
                print(f'        真缺：{l[:100]}')
            results['fidelity'] = rate >= 0.99

        # —— 边界②③：量出来 ——
        print('\n-- 8. 边界②③：顶层语句在增量/全量两条路上 ' + '-' * 14)
        demo_text = demo.read_text(encoding='utf-8')
        want = _top_level_lines(demo_text)
        print(f'  原文顶层语句 {len(want)} 处'
              f'（{want[0][:50] if want else "—"} …）')
        for tag, p in (('增量 --source', dback), ('全量 无 --source', dplain)):
            # 2026-10-05 之后**两条路都该无损**：顶层内容现在随表保真
            # （`TopLevelRegionNode` + 隐藏表），所以这里不再有"预期会丢"的
            # 豁免 —— 全量丢了就是缺陷，与增量一视同仁（"已知边界"这个
            # 标签也随之取消：留着它就会让人习惯性忽略红字）。
            if not p.exists():
                print(f'  [SKIP] {tag}：没有产物（多半被门禁拦下了 —— '
                      f'那正是"不静默降级"）')
                continue
            text = p.read_text(encoding='utf-8')
            have = {squash(l) for l in text.splitlines()}
            gone = [l for l in want if squash(l) not in have]
            mark = 'PASS' if not gone else 'FAIL'
            print(f'  [{mark}] {tag}：'
                  f'顶层语句丢 {len(gone)}/{len(want)} 处'
                  + ('' if not gone else f'，例如 {gone[0][:50]!r}'))
            key = ('inc_keeps_top_level' if tag.startswith('增量')
                   else 'full_keeps_top_level')
            results[key] = not gone

    print('\n' + '=' * 60)
    bad = [k for k, v in results.items() if not v]
    if bad:
        print(f'有 {len(bad)} 步没过：{bad}')
        return 1
    print('全部步骤通过')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
