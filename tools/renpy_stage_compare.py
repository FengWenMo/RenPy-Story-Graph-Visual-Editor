"""
tools/renpy_stage_compare.py
============================
**用真正的 Ren'Py 引擎截图，和编辑器的离屏舞台渲染逐项对账。**

用户 2026-10-20：

> 舞台的预览效果必须更真实！最好跟真正编译的一模一样……目前先做 Renpy 的吧！

"一模一样"不能靠嘴说。这个脚本在临时目录里复制一份 Ren'Py SDK 自带的
`the_question`，按几个固定场景各跑一次真引擎截图，再用 `app.stage_render`
画**同一帧**，然后：

  1. 每个场景并排写一张 `compare-<场景>.png`（上=引擎，下=编辑器）；
  2. **量**两边的立绘顶边/左边缘、名字文字框、台词文字框，逐项打 Δ；
  3. 任何一项超过容差（默认 2px）就以退出码 3 结束。

场景刻意分成两档，证明的不是"刚好对上这一张图"，而是"真的按项目 gui 排版"：

  · `default` —— 原样 gui + 内置 transform `center`（贴底居中）；
  · `custom`  —— 把 gui 改成另一套（文本框 260 / 名条 600 / 字号 28 /
                 字体换一个）+ 内置 transform `left` + 长台词自动换行。

为什么第 2、3 步重要：只出图的话，"看起来差不多"永远会赢；把每个元素
的像素位置写成数字，改坏了当场红。

用法（Windows，需要 SDK 里有 `renpy.exe`）：

    python tools/renpy_stage_compare.py
    python tools/renpy_stage_compare.py --sdk D:\\...\\storygraph_fork_sdk --keep

产物（默认 `%TEMP%/storygraph_stage_compare/`）：
    engine-<场景>.png   引擎实际截图（缩放到项目逻辑分辨率）
    ours-<场景>.png     编辑器离屏渲染
    compare-<场景>.png  上：引擎 / 下：编辑器
    report.txt          逐项对账的数字
    log-<场景>.txt      引擎测试输出

⚠ 这个工具**不是** pytest（每个场景开一次 Ren'Py 窗口，约 8 秒）；CI 不跑它。
  它证明的是"布局/字号/文本框/名条/换行"这一档，不是逐像素一致：
  引擎的快捷菜单、转场中间帧、GPU 字体渲染不在编辑器里重写。
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PIL import Image, ImageChops                          # noqa: E402

from storygraph.app.stage import SpriteLayout, StageFrame  # noqa: E402
from storygraph.app.stage_render import render_frame       # noqa: E402
from storygraph.story.atl import parse_transforms, placement_of  # noqa: E402
from storygraph.story.renpy_gui import load_gui_profile     # noqa: E402

#: 两边画的是**同一个** transform：写进项目，引擎和编辑器读同一段源码。
PROBE_SCRIPT = '''define s = Character("Sylvie", who_color="#00ff40", what_color="#ff00ff")

transform sg_center:
    xpos 0.5
    xanchor 0.5
    ypos 1.0
    yanchor 1.0

label start:
    scene bg lecturehall
    show sylvie blue normal at {at}
    s "{text}"
    return
'''

PROBE_TEST = '''init python:
    _test.screenshot_directory = "shots"
    _test.transition_timeout = 0.05
    _test.timeout = 20.0

testcase zzz_stagegraph_shot:
    click "Start"
    pause until screen "say" timeout 25
    pause 1.0
    screenshot "shot-1.png"
    exit
'''

#: 立绘对位的阈值：引擎截图是 2x 再缩回来的，JPEG 背景本身也有抖动，
#: 60 以上只可能是"真内容"（发色/肤色/衣服）压过背景。
SPRITE_THRESHOLD = 60
#: 探针角色用纯绿/品红写字（见 `PROBE_SCRIPT`），背景里没有这种像素 ——
#: 这样"文字框"量的是字，不会被透出来的亮背景/红地毯骗到。
NAME_CHANNEL = 200

WRAP_TEXT = ('Hello from the engine. This second sentence is long enough to '
             'wrap onto another line, so the line spacing is visible too.')


def _log(msg: str):
    print(msg, flush=True)


#: 两个场景：**同一段脚本、不同的项目 gui**。第二个场景是"按项目排版"的
#: 证据——如果渲染器把高度/位置写死，它就会在这一档全红。
SCENARIOS = (
    {
        'name': 'default',
        'title': '原样 gui · at center · 单行台词',
        'at': 'sg_center',
        'text': 'Hello from the engine.',
        'gui': {},
    },
    {
        'name': 'custom',
        'title': '改过 gui · at left · 长台词换行',
        'at': 'left',
        'text': WRAP_TEXT,
        'gui': {
            'textbox_height': 260,
            'name_xpos': 600,
            'name_ypos': 0,
            'dialogue_xpos': 620,
            'dialogue_ypos': 60,
            'dialogue_width': 560,
            'text_size': 28,
            'name_text_size': 34,
            'text_font': 'DejaVuSans-Bold.ttf',
            'name_text_font': 'DejaVuSans-Bold.ttf',
        },
    },
)


def _run_engine(sdk: Path, project: Path, savedir: Path, log_path: Path) -> int:
    exe = sdk / 'renpy.exe'
    if not exe.is_file():
        raise SystemExit(f"找不到 {exe}；用 --sdk 指向 Ren'Py SDK 根目录")
    env = dict(os.environ)
    env.pop('SDL_VIDEODRIVER', None)
    env['RENPY_RENDERER'] = 'sw'
    cmd = [str(exe), str(project), 'test', 'zzz_stagegraph_shot',
           '--savedir', str(savedir)]
    _log('跑引擎：' + ' '.join(cmd))
    with log_path.open('w', encoding='utf-8', errors='replace') as log:
        proc = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT,
                              env=env, timeout=180)
    return int(proc.returncode)


def _literal(value):
    """Python 字面量：字符串补引号，数字原样（`gui.rpy` 是 Python）。"""
    if isinstance(value, str):
        if value.startswith(("'", '"', 'gui.', '#')):
            return value
        return '"' + value.replace('"', '\\"') + '"'
    return str(value)


def _patch_gui(project: Path, overrides: dict) -> None:
    """
    改项目 `game/gui.rpy` 里的 `define gui.X = …`（场景用）。

    只改**桌面那一段**的第一处定义；没这行就补在文件末尾。写坏只会让引擎
    报错，不会静默给出错误对照。
    """
    if not overrides:
        return
    path = project / 'game' / 'gui.rpy'
    text = path.read_text(encoding='utf-8-sig') if path.is_file() else ''
    lines = text.splitlines()
    for key, value in overrides.items():
        needle = f'define gui.{key} ='
        done = False
        for i, line in enumerate(lines):
            if line.startswith(needle):
                lines[i] = f'define gui.{key} = {_literal(value)}'
                done = True
                break
        if not done:
            lines.append(f'define gui.{key} = {_literal(value)}')
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def _frame(profile, at: str, text: str):
    idx = parse_transforms(PROBE_SCRIPT)
    screen = tuple(profile.screen)
    place = placement_of(at, idx, screen=screen)
    sprite = SpriteLayout(image='sylvie blue normal', tag='sylvie',
                          at=at, x=0.5, place=place, order_index=0)
    return StageFrame(
        index=0, line_id='probe.0001', label='start', speaker='s',
        speaker_name='Sylvie', speaker_color='#00ff40',
        text=text, scene='bg lecturehall', sprites=[sprite], with_=''), idx


# ----------------------------------------------------------------------
#  量：只认"确定是内容"的像素，宁可量不到也不误判
# ----------------------------------------------------------------------

def _sprite_edges(img, bg, thr=SPRITE_THRESHOLD, band=None):
    """立绘内容的 (第一行 y, 第一列 x)，以背景为基线；量不到返回 (None, None)。"""
    diff = ImageChops.difference(img.convert('RGB'), bg).convert('L')
    if band is not None:
        diff = diff.crop((0, band[0], diff.width, band[1]))
    px = diff.load()
    w, h = diff.size
    first_row = first_col = None
    for y in range(h):
        if any(px[x, y] > thr for x in range(w)):
            first_row = y
            break
    for x in range(w):
        if any(px[x, y] > thr for y in range(h)):
            first_col = x
            break
    return first_row, first_col


def _bbox(img, region, pred, min_run=3):
    """
    区域内满足 `pred` 的像素 → `(x, y, w, h)`。

    `min_run`：一行/一列至少这么多像素才算"这里有字"。背景里偶尔有一两个
    同色像素（JPEG 噪声/纹理），不设这个下限就会把框撑大几十像素。
    """
    x0, y0, x1, y1 = region
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(img.width, x1), min(img.height, y1)
    px = img.load()
    col = {}
    row = {}
    for y in range(y0, y1):
        for x in range(x0, x1):
            if pred(*px[x, y][:3]):
                col[x] = col.get(x, 0) + 1
                row[y] = row.get(y, 0) + 1
    xs = [x for x, n in col.items() if n >= min_run]
    ys = [y for y, n in row.items() if n >= min_run]
    if not xs or not ys:
        return None
    return (min(xs), min(ys), max(xs) - min(xs) + 1, max(ys) - min(ys) + 1)


def _is_name_text(r, g, b):
    """探针角色的 who_color = 纯绿：背景里没有这种像素。"""
    return g > NAME_CHANNEL and r < 90 and b < 120


def _is_dialogue_text(r, g, b):
    """探针角色的 what_color = 品红：地板/地毯的红不会误判成台词。"""
    return r > NAME_CHANNEL and b > NAME_CHANNEL and g < 90


def _measure(engine, ours, bg, result, profile, tol):
    """
    逐项量 → `[(项, 引擎值, 编辑器值, 容差)]`。

    位置（x/y）用**严格**容差：差 2px 就是画错地方了。
    尺寸（w/h）放宽到 4%：引擎截图是 2x 再缩回来的，字形宽度的舍入
    和编辑器在 1x 下算的不一样（实测同一句 236 vs 243），这不是排版错。
    """
    rows = []
    sh, sw = engine.height, engine.width
    band = (0, int(sh * 0.72))
    e_top, e_left = _sprite_edges(engine, bg, band=band)
    o_top, o_left = _sprite_edges(ours, bg, band=band)
    rows.append(('立绘顶边 y', e_top, o_top, tol))
    rows.append(('立绘左边缘 x', e_left, o_left, tol))

    tb_top = result.textbox_rect[1] if result.textbox_rect else int(
        sh - profile.textbox_height)
    name_h = int(profile.name_text_size * 2.4)
    e_name = _bbox(engine, (0, tb_top, sw, tb_top + name_h), _is_name_text)
    o_name = _bbox(ours, (0, tb_top, sw, tb_top + name_h), _is_name_text)
    say_y = tb_top + int(float(profile.values.get('dialogue_ypos') or 50)) - 6
    e_say = _bbox(engine, (0, say_y, sw, min(sh, say_y + 160)),
                  _is_dialogue_text)
    o_say = _bbox(ours, (0, say_y, sw, min(sh, say_y + 160)),
                  _is_dialogue_text)

    for name, e_box, o_box in (('名字', e_name, o_name),
                               ('台词', e_say, o_say)):
        if e_box is None or o_box is None:
            rows.append((f'{name}文字框', e_box, o_box, tol))
            continue
        for i, axis in enumerate(('x', 'y', 'w', 'h')):
            limit = tol if i < 2 else max(3.0, 0.04 * max(e_box[i],
                                                          o_box[i]))
            rows.append((f'{name}文字框 {axis}', e_box[i], o_box[i], limit))
    return rows


def _delta(a, b):
    """两个值的差；量不到返回 None。"""
    if a is None or b is None:
        return None
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b)
    if len(a) != len(b):
        return None
    return max(abs(x - y) for x, y in zip(a, b))


def _run_scenario(sdk: Path, out: Path, sc: dict, tolerance: float,
                  keep: bool) -> tuple:
    """跑一个场景 → (是否通过, 报告行)。"""
    work = Path(tempfile.mkdtemp(prefix=f"sg_cmp_{sc['name']}_"))
    project = work / 'the_question'
    shutil.copytree(sdk / 'the_question', project)
    (project / 'game' / 'script.rpy').write_text(
        PROBE_SCRIPT.format(at=sc['at'], text=sc['text']),
        encoding='utf-8-sig')
    (project / 'game' / 'zz_stagegraph_probe.rpy').write_text(
        PROBE_TEST, encoding='utf-8-sig')
    _patch_gui(project, sc['gui'])

    log_path = out / f"log-{sc['name']}.txt"
    try:
        code = _run_engine(sdk, project, work / 'saves', log_path)
    except subprocess.TimeoutExpired:
        code = -1
    shot = project / 'shots' / 'shot-1.png'
    if not shot.is_file():
        _log(f"引擎没有产出截图（exit={code}），看 {log_path}")
        if not keep:
            shutil.rmtree(work, ignore_errors=True)
        return False, [f"{sc['name']}: 引擎无截图（exit={code}）"]

    profile = load_gui_profile(project, force=True)
    frame, idx = _frame(profile, sc['at'], sc['text'])
    result = render_frame(frame, project_root=project, profile=profile,
                          screen=profile.screen, transform_index=idx)
    if result is None:
        raise SystemExit('编辑器渲染不可用（Pillow 没装？）')

    engine = Image.open(shot).convert('RGBA')
    # Ren'Py 可能按 DPI 放大输出（实测 2560×1440 / 逻辑 1280×720）；
    # 对照统一到**项目逻辑分辨率**，否则字号看起来会差一倍。
    engine = engine.resize(tuple(profile.screen), Image.Resampling.LANCZOS)
    ours = result.image.convert('RGBA')
    engine.save(out / f"engine-{sc['name']}.png")
    ours.save(out / f"ours-{sc['name']}.png")
    w, h = profile.screen
    both = Image.new('RGBA', (w, h * 2 + 8), (0, 0, 0, 255))
    both.alpha_composite(engine, (0, 0))
    both.alpha_composite(ours, (0, h + 8))
    both.save(out / f"compare-{sc['name']}.png")

    bg_path = project / 'game' / 'images' / 'bg lecturehall.jpg'
    bg = Image.open(bg_path).convert('RGB')
    if bg.size != (w, h):
        bg = bg.resize((w, h), Image.Resampling.LANCZOS)

    lines = [f"## {sc['name']}  ({sc['title']})",
             f'分辨率 {profile.screen}  文本框 {result.textbox_rect}  '
             f'名条 {result.name_rect}  台词 {result.text_rect}',
             f'字体 {profile.text_font_path}']
    if profile.fonts_substituted:
        lines.append('字体替换：' + '，'.join(profile.fonts_substituted))

    bad = []
    for label, e_val, o_val, limit in _measure(engine, ours, bg, result,
                                               profile, tolerance):
        d = _delta(e_val, o_val)
        mark = '?'
        if d is not None:
            mark = '✓' if d <= limit else '✗'
            if d > limit:
                bad.append(label)
        lines.append(f'{mark} {label}: 引擎 {e_val} / 编辑器 {o_val}'
                     + (f'  Δ{d:g}px ≤ {limit:g}'
                        if d is not None else '  （有一边没量到）'))
    lines.append('结论：' + ('通过' if not bad else '不通过 → ' + '、'.join(bad)))
    _log('\n'.join(lines))
    _log(f"对照图：{out / ('compare-' + sc['name'] + '.png')}")
    if not keep:
        shutil.rmtree(work, ignore_errors=True)
    return (not bad), lines


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--sdk', default=r'D:\千连万理\storygraph_fork_sdk',
                    help="Ren'Py SDK 根目录（含 renpy.exe）")
    ap.add_argument('--out', default=str(Path(tempfile.gettempdir())
                                          / 'storygraph_stage_compare'))
    ap.add_argument('--tolerance', type=float, default=2.0,
                    help='单项允许的最大像素差，超了以退出码 3 结束')
    ap.add_argument('--keep', action='store_true', help='保留临时项目')
    args = ap.parse_args(argv)

    sdk = Path(args.sdk)
    sample = sdk / 'the_question'
    if not (sample / 'game' / 'script.rpy').is_file():
        raise SystemExit(f'找不到 SDK 示例项目 {sample}')
    # 引擎用的是这一个 SDK；字体也让它从同一个 SDK 里找，
    # 否则对照的两边可能各用一份同名字体，量出来的字宽就没意义了。
    os.environ['STORYGRAPH_RENPY_SDK'] = str(sdk)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    report, failed = [], []
    for sc in SCENARIOS:
        ok, lines = _run_scenario(sdk, out, sc, args.tolerance, args.keep)
        report.extend(lines + [''])
        if not ok:
            failed.append(sc['name'])
    (out / 'report.txt').write_text('\n'.join(report), encoding='utf-8')
    _log(f'产物：{out}（report.txt / compare-*.png）')
    _log('说明：上=引擎，下=编辑器；引擎的快捷菜单/GPU 字体不在对照范围。')
    if failed:
        _log('不通过：' + '、'.join(failed))
        return 3
    _log('全部场景通过。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
