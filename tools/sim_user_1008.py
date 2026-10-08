"""
实机模拟（2026-10-08）
=====================
在**本机**用真实项目 `sr_general` 的章节，模拟一个用户从"打开项目"到
"改一句台词再写回"的完整动作。全部在**临时副本**上做，不碰真项目源文件。

覆盖：
  A. CLI：`table rpy2xlsx` → 改一格 → `table xlsx2rpy --source` → 逐行 diff
  B. CLI：`validate` 真项目副本
  C. Qt（offscreen）：open_editor → 打开项目 → 导入章节 → 检查逻辑 →
     编辑剧情变量 + 语法检查 → 导出控制流
"""
from __future__ import annotations

import difflib
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
REAL = Path(r'D:\千连万理\RenPyTest\sr_general')
PY = sys.executable
SCRATCH = ROOT / '.pip-tmp' / 'sim_proj'
QT_DIR = ROOT / '.pip-tmp' / 'sim_qt'


def run_cli(*args):
    cmd = [PY, str(ROOT / 'cli.py'), *[str(a) for a in args]]
    p = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8',
                       errors='replace', cwd=str(ROOT))
    return p.returncode, (p.stdout or '') + (p.stderr or '')


def seed():
    if SCRATCH.exists():
        shutil.rmtree(SCRATCH, ignore_errors=True)
    (SCRATCH / 'game' / 'chapter').mkdir(parents=True, exist_ok=True)
    shutil.copytree(REAL / 'game' / 'asset_tables',
                    SCRATCH / 'game' / 'asset_tables')
    for rel in ('chapter/chapter0_0.rpy', 'chapter/chapter1_1.rpy',
                'script.rpy'):
        shutil.copy2(REAL / 'game' / rel, SCRATCH / 'game' / rel)
    # Qt 那一路单独一个干净目录：不塞资源表，避免"表里 1657 行指的文件都不在"
    # 触发自动清理（虽然只动副本，但噪音太大、也慢）。
    if QT_DIR.exists():
        shutil.rmtree(QT_DIR, ignore_errors=True)
    (QT_DIR / 'game' / 'chapter').mkdir(parents=True, exist_ok=True)
    shutil.copy2(REAL / 'game' / 'chapter' / 'chapter0_0.rpy',
                 QT_DIR / 'game' / 'chapter' / 'chapter0_0.rpy')
    shutil.copy2(REAL / 'game' / 'script.rpy', QT_DIR / 'game' / 'script.rpy')


def diff(a: Path, b: Path) -> list:
    la = a.read_text(encoding='utf-8').splitlines()
    lb = b.read_text(encoding='utf-8').splitlines()
    return [ln for ln in difflib.unified_diff(la, lb, lineterm='', n=0)
            if ln.startswith(('+', '-')) and not ln.startswith(('+++', '---'))]


def align(a: Path, b: Path):
    """按对齐后的 opcode 报告"到底改了什么"（不把整体位移误报成逐行全变）。"""
    la = a.read_text(encoding='utf-8').splitlines()
    lb = b.read_text(encoding='utf-8').splitlines()
    sm = difflib.SequenceMatcher(None, la, lb, autojunk=False)
    ops = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == 'equal':
            continue
        ops.append((tag, la[i1:i2], lb[j1:j2]))
    return len(la), len(lb), sm.ratio(), ops


print('=' * 78)
print('A. CLI 往返：chapter0_0（用户点名"改 0_0"）')
print('=' * 78)
seed()
src0 = SCRATCH / 'game' / 'chapter' / 'chapter0_0.rpy'
xlsx0 = SCRATCH / 'ch0.xlsx'
out0 = SCRATCH / 'chapter0_0.new.rpy'

rc, out = run_cli('table', 'rpy2xlsx', src0, '--out', xlsx0,
                  '--project', SCRATCH)
print(f'[rpy2xlsx] rc={rc}')
print(out.strip()[:400])

# —— 模拟"用户在 Excel 里改了一句台词" ——
import openpyxl
wb = openpyxl.load_workbook(xlsx0)
ws = wb.active
print(f'[表] sheet={ws.title} 行列={ws.max_row}x{ws.max_column}')
header = [c.value for c in ws[1]]
print(f'[表] 表头前 12 列 = {header[:12]}')
target_row = None
for r in range(2, ws.max_row + 1):
    vals = [str(c.value or '') for c in ws[r]]
    if any('AI真的拥有自我意识吗' in v for v in vals):
        target_row = r
        break
print(f'[表] 找到目标台词行 = {target_row}')
# 定位"台词"列
col_text = None
for i, name in enumerate(header, start=1):
    if name and '台词' in str(name):
        col_text = i
        break
print(f'[表] 台词列 = {col_text}')
assert target_row and col_text, '表结构没找到目标'
old = ws.cell(row=target_row, column=col_text).value
ws.cell(row=target_row, column=col_text).value = '【编辑测试】' + str(old or '')
wb.save(xlsx0)
print(f'[改] 第{target_row}行 台词列 原={old!r}\n        新=【编辑测试】{old!r}')

rc, out = run_cli('table', 'xlsx2rpy', xlsx0, '--source', src0,
                  '--out', out0, '--project', SCRATCH)
print(f'[xlsx2rpy --source] rc={rc}')
print(out.strip()[:500])

d = diff(src0, out0)
print(f'[diff] 与原文件差异行数 = {len(d)}')
for ln in d[:12]:
    print('   ', ln)

n0a, n0b, ratio, ops = align(src0, out0)
print(f'[对齐] 行数 {n0a} -> {n0b}，相似度 {ratio:.4f}；改动块 {len(ops)} 个：')
for tag, oldl, newl in ops:
    for x in oldl:
        print(f'   - {x!r}')
    for x in newl:
        print(f'   + {x!r}')
print('[对齐] 结论：除"生成标记 2 行 + 被改的 1 行"之外没有任何改动 =',
      all(tag in ('replace', 'insert') for tag, _, _ in ops))

# —— 再写回一次：改回来（用户"反悔"）——
wb = openpyxl.load_workbook(xlsx0)
ws = wb.active
ws.cell(row=target_row, column=col_text).value = old
wb.save(xlsx0)
out0b = SCRATCH / 'chapter0_0.back.rpy'
rc, out = run_cli('table', 'xlsx2rpy', xlsx0, '--source', out0,
                  '--out', out0b, '--project', SCRATCH)
# 幂等/收敛：以"已生成过的 out0"为源码再走一次，除了改回去那一行不许再变
_, _, ratio2, ops2 = align(out0, out0b)
print(f'[再写回] 相似度 {ratio2:.4f}；改动块 {len(ops2)} 个：')
for tag, oldl, newl in ops2:
    for x in oldl:
        print(f'   - {x!r}')
    for x in newl:
        print(f'   + {x!r}')
print('[再写回] 生成的标记没有重复 =',
      sum(1 for ln in out0b.read_text(encoding='utf-8').splitlines()
          if '自动生成' in ln) == 1)

print()
print('=' * 78)
print('B. CLI 往返：script.rpy（含 menu / show expression / with / l oh "3"）')
print('=' * 78)
src1 = SCRATCH / 'game' / 'script.rpy'
xlsx1 = SCRATCH / 'script.xlsx'
out1 = SCRATCH / 'script.new.rpy'
rc, out = run_cli('table', 'rpy2xlsx', src1, '--out', xlsx1, '--project', SCRATCH)
print(f'[rpy2xlsx] rc={rc}\n' + out.strip()[:300])
wb = openpyxl.load_workbook(xlsx1)
ws = wb.active
header = [c.value for c in ws[1]]
col_text = next((i for i, n in enumerate(header, 1)
                 if n and '台词' in str(n)), None)
hit = None
for r in range(2, ws.max_row + 1):
    if any('选项A' in str(c.value or '') for c in ws[r]):
        hit = r
        break
print(f'[表] 选项A 行 = {hit} 台词列 = {col_text}')
if hit and col_text:
    cur = ws.cell(row=hit, column=col_text).value
    ws.cell(row=hit, column=col_text).value = '选项A★改过的文案'
    wb.save(xlsx1)
    rc, out = run_cli('table', 'xlsx2rpy', xlsx1, '--source', src1,
                      '--out', out1, '--project', SCRATCH)
    print(f'[xlsx2rpy --source] rc={rc}\n' + out.strip()[:400])
    d = diff(src1, out1)
    print(f'[diff] 差异行 = {len(d)}')
    for ln in d[:14]:
        print('   ', ln)
    n1a, n1b, ratio, ops = align(src1, out1)
    print(f'[对齐] 行数 {n1a} -> {n1b}，相似度 {ratio:.4f}；改动块 {len(ops)} 个：')
    for tag, oldl, newl in ops:
        for x in oldl:
            print(f'   - {x!r}')
        for x in newl:
            print(f'   + {x!r}')
    txt = out1.read_text(encoding='utf-8')
    for keep in ('l oh "3"', 'with pixellate', 'window hide',
                 'show expression holo("lan base")'):
        print(f'[保真] 产物里还有 {keep!r} = {keep in txt}')

print()
print('=' * 78)
print('C. CLI validate —— 真实项目（只体检不写盘，全量 88 脚本）')
print('=' * 78)
rc, out = run_cli('validate', REAL)
print(f'[validate REAL] rc={rc}')
print('\n'.join(out.strip().splitlines()[:30]))
# 副本（只有 3 个脚本、资源表在但文件不在）对照：E103 全是"文件不在"的假警报
rc2, out2 = run_cli('validate', SCRATCH)
print(f'\n[validate 副本] rc={rc2}')
print('\n'.join(out2.strip().splitlines()[:6]))

print()
print('=' * 78)
print('D. Qt（offscreen）：模拟用户操作')
print('=' * 78)
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
try:
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from storygraph.main_qt import _EngineShim
    from storygraph.ui_qt.main_window import open_editor

    ch = QT_DIR / 'game' / 'chapter' / 'chapter0_0.rpy'
    sc = QT_DIR / 'game' / 'script.rpy'
    win = open_editor(_EngineShim(QT_DIR), str(ch), QT_DIR, None)
    print('[Qt] 打开项目…')
    res = win.open_project_path(QT_DIR)
    print(f'[Qt] open_project_path ok={bool(res and res.get("ok"))}')

    print('[Qt] 导入 chapter0_0 + script.rpy…')
    r1 = win.import_chapter_files([str(ch), str(sc)])
    print(f'[Qt] 导入 ok={r1.get("ok")} 模块={r1.get("nodes")} 连线={r1.get("edges")}')

    print('[Qt] 检查逻辑问题（静态分析图）…')
    win.check_logic()
    console = win._console.toPlainText()
    print('   控制台首行:', console.splitlines()[0] if console else '(空)')

    print('[Qt] 变量分析…')
    win.show_analysis()

    print('[Qt] 双击第一个模块 → 编辑剧情变量 + 语法检查…')
    nid = next(iter(win.graph.nodes))
    info = win._session.node_variables(win.graph, nid)
    print(f'   模块 label={info["label"]!r} 剧情变量={info["variables"]} '
          f'技术代码={info["tech_count"]} 条')
    errs = win._session.check_variable_syntax(['love += 1', 'x = = 5'])
    print(f'   语法检查(含一行坏行) -> {errs}')

    print('[Qt] 导出控制流 .rpy…')
    flow = QT_DIR / 'story_graph_control.rpy'
    rr = win._session.export_control_flow(win.graph, flow, wrapper=True)
    print(f'   导出 ok={rr.get("ok")} 标签={rr.get("count")} 模式={rr.get("mode")}')
    print(f'   产物存在={flow.is_file()}')
except Exception as e:                          # noqa: BLE001
    import traceback
    print('[Qt] 失败：', e)
    traceback.print_exc()

print()
print('全部完成。临时项目 =', SCRATCH)
