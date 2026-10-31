# -*- coding: utf-8 -*-
"""
临时探针：**「自定义倍数」**在真机上填得上不上（只读）。

两件事分开量：

  ① 纯数据（不建窗口，逐个 `.rpy` 解析，与 `session.load` 同一条路）：
     每一拍每行的四条预设里，**全都没匹配上**的行有多少 —— 那些正是
     只能靠"填一个倍数"解决的行；以及项目里已有的 zoom 变换是什么倍数
     （有等价的就复用、不写文件）；
  ② 界面（offscreen 真窗口）：选中「自定义倍数」那一项、填 0.75，
     卡片右栏长什么样 → `tools/_custom_panel.png`。

⚠ 全程只读：不写 `game/sg_transforms.rpy`，不碰任何拍。

用法：python -u tools/_probe_custom_zoom.py
"""
import os
import tempfile
import time
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QPA_FONTDIR', r'C:\Windows\Fonts')
os.environ['STORYGRAPH_NO_SESSION'] = '1'
os.environ['STORYGRAPH_AUTOSAVE_DIR'] = tempfile.mkdtemp()

REAL = Path(os.environ.get('STORYGRAPH_REAL_PROJECT')
            or r'D:\千连万理\sr_general')

from storygraph.app.stage import build_frames                     # noqa: E402
from storygraph.app.stage_render import size_rows                 # noqa: E402
from storygraph.parsing.discover import discover_scripts           # noqa: E402
from storygraph.parsing.renpy_parser import (ParseResultBuilder,   # noqa: E402
                                            RenPyParser)
from storygraph.pipeline.build import load_asset_registry          # noqa: E402
from storygraph.story import sg_transforms as SG                   # noqa: E402
from storygraph.story import size_presets as SP                    # noqa: E402
from storygraph.story.atl import placement_of, scan_transforms     # noqa: E402
from storygraph.story.beat_build import beats_from_parse_result    # noqa: E402

print('项目：', REAL, flush=True)
index = scan_transforms(REAL)
registry = load_asset_registry(REAL)
print('transform 表：', len(index), '条', flush=True)

# —— ①-A：项目里已有的缩放倍数 ——
zooms = []
for name in sorted(index):
    try:
        place = placement_of(name, index)
    except Exception as e:                                         # noqa: BLE001
        print('  解析失败', name, e)
        continue
    if place is None:
        continue
    xz = float(getattr(place, 'xzoom', 1.0) or 1.0)
    yz = float(getattr(place, 'yzoom', 1.0) or 1.0)
    if abs(xz - yz) < 1e-6 and abs(xz - 1.0) > 1e-6:
        zooms.append((name, xz, str(getattr(place, 'uncertain', '') or '')))
print(f'项目里已有的 zoom 变换：{len(zooms)} 条', flush=True)
for name, z, uncertain in zooms[:12]:
    print(f'  {name}: zoom {z:g}'
          + (f'（算不准：{uncertain}）' if uncertain else ''), flush=True)

for target in (0.75, 0.5, 1.25):
    same = [n for n, z, _u in zooms if abs(z - target) < 1e-6]
    plan = SG.plan_for_project(REAL, 'custom', target, index=index)
    print(f'  自定义 {SP.zoom_label(target)} → 计划 {plan.action}'
          f'{(" / " + plan.name) if plan.name else ""}'
          f'；项目里同倍数的有：{same or "（没有）"}', flush=True)

# —— ①-B：逐文件逐拍的尺寸行 ——
files = list(discover_scripts(REAL))
print(f'\n文件：{len(files)} 个', flush=True)
t0 = time.time()
total_rows = 0
custom_rows = 0
stuck = 0
matched_rows = 0
by_kind = {}
sample = []
for n, path in enumerate(files, 1):
    try:
        pr = ParseResultBuilder().build(RenPyParser().parse(str(path)))
        beats = beats_from_parse_result(pr)
    except Exception as e:                                         # noqa: BLE001
        print('  解析失败', path.name, e)
        continue
    if not beats:
        continue
    frames = build_frames(beats, registry=registry,
                          transform_index=index, project_root=REAL)
    for frame in frames:
        if not (str(getattr(frame, 'scene', '') or '').strip()
                or str(getattr(frame, 'layers', '') or '').strip()):
            continue
        rows = size_rows(frame, registry=registry, project_root=REAL,
                         transform_index=index)
        for row in rows:
            total_rows += 1
            by_kind[row['kind']] = by_kind.get(row['kind'], 0) + 1
            if any(it['key'] == 'custom' for it in row['items']):
                custom_rows += 1
            presets = [it for it in row['items']
                       if it['key'] in ('cover', 'contain', 'half')]
            # "项目里已经有等价变换"= 匹配到现成的（`generate=False` 且 `at` 非空）；
            # 三条预设全是 `generate=True` 的行 = 项目里一条都对不上，
            # 「自定义倍数」这种人填的数就成了唯一不需要新建文件的路。
            if any(not it['generate'] and it['at'] for it in presets):
                matched_rows += 1
            else:
                stuck += 1
                if len(sample) < 6:
                    sample.append((path.name, frame.index + 1, row['spec'],
                                   row['natural'], row['note'][:56],
                                   [it['text'][:34] for it in presets]))
    if n % 20 == 0:
        print(f'  …{n}/{len(files)} 个文件 · {total_rows} 行 '
              f'· {time.time() - t0:.1f}s', flush=True)

print(f'\n扫完：{len(files)} 个文件 · 尺寸行 {total_rows} 个'
      f'（背景 {by_kind.get("scene", 0)} / 图层 {by_kind.get("layer", 0)}）'
      f' · 带「自定义倍数」的 {custom_rows} 个'
      f' · 用时 {time.time() - t0:.1f}s', flush=True)
print(f'铺满/适应/50% 里**至少有一条匹配到项目现成 transform** 的行：'
      f'{matched_rows}', flush=True)
print(f'三条预设全都只能"生成"（项目里一条都不匹配）的行：{stuck}', flush=True)
for name, beat, spec, natural, note, texts in sample:
    print(f'  {name} 第 {beat} 拍：{spec} {natural}', flush=True)
    print(f'     {note}', flush=True)
    print(f'     {texts}', flush=True)

# —— ② 界面截图 ——
try:
    from PySide6.QtWidgets import QApplication
except Exception as e:                                             # noqa: BLE001
    raise SystemExit(f'起不了 Qt：{e}')

from storygraph.ui_qt.main_window import MainWindowQt              # noqa: E402


class _Engine:
    def __init__(self, project_root):
        self.project_root = project_root
        self._registry = registry

    def get_registry(self):
        return self._registry


app = QApplication.instance() or QApplication([])
target = REAL / 'game' / 'chapter' / 'chapter1_2.rpy'
if not target.is_file():
    target = sorted((REAL / 'game').rglob('chapter*.rpy'))[0]
print('\n=== 界面 ===', flush=True)
print('载入：', target, flush=True)
t0 = time.time()
win = MainWindowQt(_Engine(REAL), target)
win._set_page(win.PAGE_CARD)
win.resize(1280, 900)
win.show()
for _ in range(20):
    app.processEvents()
idx = 0
for i in range(min(120, len(win._session.beats))):
    if win._session.stage_size_rows(i):
        idx = i
        break
print(f'第 {idx + 1} 拍有尺寸行（载入 + 建卡片 {time.time() - t0:.1f}s）',
      flush=True)
win._show_index(idx)
for _ in range(30):
    app.processEvents()
deadline = time.time() + 20
while time.time() < deadline and win._session.stage_initial_state() is None:
    app.processEvents()
    time.sleep(0.05)
for _ in range(20):
    app.processEvents()

panel = win._panel.size_panel()
slot = panel.slots()[0]
print('下拉条目：', flush=True)
for i in range(slot['combo'].count()):
    print(f'  [{i}] {slot["combo"].itemText(i)}', flush=True)
slot['combo'].setCurrentIndex(int(slot['custom_index']))
slot['zoom_edit'].setText('0.75')
for _ in range(10):
    app.processEvents()
print('输入框：', repr(slot['zoom_edit'].text()), flush=True)
print('提示行：', slot['zoom_hint'].text(), flush=True)
print('「改」可写：', slot['button'].isEnabled(), flush=True)
print('请求：', panel.request(0), flush=True)
out = Path('tools/_custom_panel.png')
win._panel.grab().save(str(out))
win.hide()
print('saved', out, flush=True)
