"""
交付验收脚本：**单人 · Ren'Py 剧本调整 / 增添 / 增量导出** 走一遍真实旅程。

为什么要有它（而不是只看单测）：用户要判断的是"这套工具能不能干完这条活"，
而单测是按功能点切的。这里按**用户的实际动作顺序**走：

    ① 打开项目、打开一章          → 拍到内存
    ② 调整：改一句台词             → 导出（增量）→ 原文其余逐字节不变
    ③ 增添：插一张新卡（当前之后）  → 填内容 → 再导出 → 新行落在正确位置
    ④ 幂等：连着再导一次           → 产物不变
    ⑤ 体检：validate              → 结论

只读真实项目，**在临时副本上改**（绝不碰源文件）。
用法：python tools/acceptance_workflow.py [章节.rpy]
"""
from __future__ import annotations

import difflib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REAL = Path(r'D:\千连万理\RenPyTest\sr_general')
PY = sys.executable
# 从仓库根跑（`python tools/acceptance_workflow.py`）时，`storygraph` 不在
# `sys.path` 上 —— 自己补，别依赖调用者的环境变量。
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

#: 样本：既要"有第三类内容"（能验证增量真的保住了东西），又要有**真台词**
#: 可改 —— 纯注释/空台词的拍走不了 patcher 的逐拍对位守卫（它靠
#: 台词/说话人/图像名认身份），那会把"工具缺陷"和"样本选错"混在一起。
DEFAULT_SAMPLE = REAL / 'game' / 'minigame' / 'ball_moving_game.rpy'


def _diff(a: str, b: str) -> list:
    return [ln for ln in difflib.unified_diff(a.splitlines(), b.splitlines(),
                                              n=0, lineterm='')
            if ln[:1] in '+-' and ln[:3] not in ('---', '+++')]


def main(argv) -> int:
    src = Path(argv[1]) if len(argv) > 1 else DEFAULT_SAMPLE
    if not src.exists():
        print('真实项目不在，跳过')
        return 0

    from storygraph.app.editor import EditorSession

    ok = True
    with tempfile.TemporaryDirectory(prefix='accept_') as td:
        work = Path(td)
        chapter = work / src.name
        shutil.copy2(src, chapter)          # 只在副本上动手
        original = chapter.read_text(encoding='utf-8')

        print(f'样本：{src.name}（{len(original.splitlines())} 行）\n')

        # ① 打开一章
        s = EditorSession(project_root=REAL)
        n = s.load(chapter)
        print(f'① 打开章节：{n} 拍，标签 {sorted({b.label for b in s.beats})}')

        # ② 调整一句台词 → 导出（**原地增量写回，不该拦**）
        #
        # 2026-10-05 改口（用户实测报的）：这一步从前是"先拦一次、用户点
        # 「写入（增量）」再写"（`export(chapter)` 然后 `force=True`）。
        # 用户的原话是"明明只是增量的对一个卡片的修改……为何要阻止我拿新的
        # 文件去替换旧的？" —— 增量合并出来的全文里，原文每个字节都还在
        # （只换掉改过的那几行），覆盖它什么都不毁，所以那次拦是错的。
        # 现在断言：**一次就写进去**，且仍然只动那一行。
        says = [b for b in s.beats if b.text.strip()]
        if not says:
            print('   （这一章没有台词可改，改用注释）')
            target_beat = s.beats[0]
            target_beat.comment = (target_beat.comment or '') + '\n验收改动'
        else:
            target_beat = says[0]
            target_beat.text = target_beat.text + '（验收改动）'

        gate = s.export(chapter)                 # 原地写回：手写文件也不该拦
        print(f'② 原地导出：ok={gate["ok"]} codes={gate["codes"]}')
        print(f'    note：{gate.get("note", "")[:80]}')
        print(f'    used_patch={gate.get("used_patch")}'
              f' → 前端据此说真话'
              f'（{"这次是增量、不会丢东西" if gate.get("used_patch") else "会丢东西"})')
        if not gate['ok'] or not gate.get('used_patch'):
            ok = False
            print('   ✗ 原地增量写回被拦下了（用户报的那条缺陷回来了）')

        res = gate
        changed1 = chapter.read_text(encoding='utf-8')
        d1 = _diff(original, changed1)
        print(f'②b 写入结果：ok={res["ok"]}')
        print(f'   与原文差异 {len(d1)} 行（期望：标记 2 行 + 改的那一句）')
        for ln in d1[:6]:
            print(f'     {ln[:96]}')
        if not res['ok'] or len(d1) > 4:
            ok = False
            print('   ✗ 调整这一步没达到"只动改过的那一行"')

        # ③ 增添：插一张新卡（当前之后）→ 填内容 → 再导出
        #
        # ⚠ 导出会**重新解析**刚写出的文件（原地写回后内存对齐到磁盘），
        # 所以这里不能抓着 `Beat` 对象不放 —— 每一次都按**序号**重新取。
        # （第一版抓着 `target_beat` 不放，重载后对象已经不在列表里了。）
        idx = next((i for i, b in enumerate(s.beats) if '验收改动' in (b.text or '')
                    or '验收改动' in (b.comment or '')), 0)
        ins = s.insert_beat(idx, after=True)
        print(f'③ 插入卡片：ok={ins["ok"]}，落点第 {ins["index"] + 1} 拍'
              f'（当前是第 {idx + 1} 拍 → 期望 {idx + 2}）')
        if not ins['ok'] or ins['index'] != idx + 1:
            ok = False
            print('   ✗ 插入方向不对')
        s.beats[ins['index']].text = '验收新插的一句'
        res2 = s.export(chapter)                 # 目标已带生成标记，不用 force
        changed2 = chapter.read_text(encoding='utf-8')
        lines = changed2.splitlines()
        pos_new = next((i for i, l in enumerate(lines) if '验收新插的一句' in l), -1)
        pos_old = next((i for i, l in enumerate(lines)
                        if '验收改动' in l), -1)
        print(f'   再导出：ok={res2["ok"]} codes={res2["codes"]}')
        print(f'   新句在第 {pos_new + 1} 行，改过的原句在第 {pos_old + 1} 行'
              f' → {"顺序正确 ✓" if 0 < pos_old < pos_new else "顺序不对 ✗"}')
        if not res2['ok'] or not (0 < pos_old < pos_new):
            ok = False

        # ④ 幂等：内容不变时再导一次
        #    （这次目标已带生成标记，门禁不再拦 —— 不用 force）
        res3 = s.export(chapter)
        changed3 = chapter.read_text(encoding='utf-8')
        same = changed3 == changed2
        print(f'④ 再导一次（幂等）：ok={res3["ok"]} codes={res3["codes"]}')
        print(f'    note：{res3.get("note", "")[:90]}')
        print(f'    blocking：{res3.get("blocking")}')
        print(f'    产物{"不变 ✓" if same else "变了 ✗"}')
        if not res3['ok'] or not same:
            ok = False

        # ⑤ 体检（在临时工程上跑，不动源项目）
        proj = work / 'proj' / 'game'
        proj.mkdir(parents=True)
        shutil.copy2(chapter, proj / chapter.name)
        p = subprocess.run([PY, str(ROOT / 'cli.py'), 'validate',
                            str(work / 'proj')],
                           cwd=ROOT, capture_output=True, text=True,
                           encoding='utf-8', errors='replace')
        first = [ln for ln in (p.stdout or '').splitlines() if '通过' in ln]
        print(f'⑤ 体检：exit={p.returncode} {first[0] if first else ""}')
        if p.returncode != 0:
            ok = False

    print('\n结论：', '这条工作流跑通了' if ok else '工作流有断点（见上面 ✗）')
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
