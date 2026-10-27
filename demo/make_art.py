"""给 demo 画**能看的**占位美术（真 PNG）。

    python demo/make_art.py            # 只重画图片
    python demo/make_demo.py           # 重建整个 demo（会调这里）

为什么要有它（2026-10-27，写发布包时实测）：
    以前 `make_demo.py` 往 `game/images/` 里写的是 **48 字节的假 PNG**
    （只有 `\\x89PNG` 文件头 + 一串 0）。资源表按"文件在不在"认得它们，
    所以资源表、体检、导出全都绿灯；但**任何一个真读像素的地方都读不出来**：

      · 卡片页「当前舞台（这一拍生效）」一片黑 + 「⚠ 缺 1 个素材」；
      · 立绘卡里写着「读不出」；
      · 「🎬 演出预览」只能降级成占位方块。

    第一次打开 demo 的人看到的就是这些 —— 而 demo 的全部意义就是
    "打开就能看见一个像样的画面"。所以这里用几块颜色 + 一行字画出
    看得懂的占位图：**不是美术资源**，是让界面有东西可渲染。

不动的东西：`music/` `sounds/` `voice/` 里的占位 `.ogg` 仍是假字节
（工具只查存在性、不播；真 ogg 得用编码器，不在这份脚本的职责里）。
"""
from __future__ import annotations

import sys
from pathlib import Path

W, H = 1920, 1080                 # 背景：Ren'Py 默认 1920×1080
SW, SH = 700, 1200                # 立绘：竖图，渲染时按站位缩放

#: 背景：(上方色, 下方色, 画面上的字, 副标题)
BACKGROUNDS = {
    'bg/bg_classroom.png': ('#2f3a46', '#4d5f70', '教室', '放学后 · 雨'),
    'bg/bg_rooftop.png': ('#22303a', '#3d5866', '屋顶', '夜里 · 风'),
}

#: 立绘：(主色, 皮肤色, 字, 表情花样)
SPRITES = {
    'eileen/happy.png': ('#e9899f', '#f6dcc8', '爱莲 · 开心', 'happy'),
    'eileen/sad.png': ('#8e93b8', '#f0dccb', '爱莲 · 难过', 'sad'),
    'eileen/happy_blush.png': ('#f06f92', '#f9d3c4', '爱莲 · 脸红', 'blush'),
    'mary/smile.png': ('#6f9ed6', '#f6dcc8', '玛丽 · 微笑', 'smile'),
}


def _font(size: int):
    """找一个能写中文的字体；找不到就退默认（字小一点，但不至于崩）。"""
    from PIL import ImageFont

    for name in ('msyh.ttc', 'msyhbd.ttc', 'simhei.ttf', 'simsun.ttc',
                 'arial.ttf'):
        path = Path(r'C:\Windows\Fonts') / name
        if path.is_file():
            try:
                return ImageFont.truetype(str(path), size)
            except Exception:                        # noqa: BLE001
                continue
    try:
        return ImageFont.load_default(size=size)
    except Exception:                                # noqa: BLE001
        return ImageFont.load_default()


def _rgb(value: str) -> tuple:
    value = value.lstrip('#')
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def _mix(a, b, t: float) -> tuple:
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def _background(top: str, bottom: str, title: str, sub: str):
    """一块渐变背景 + 地平线 + 大字（够看出"这是室内 / 天台"就行）。"""
    from PIL import Image, ImageDraw

    a, b = _rgb(top), _rgb(bottom)
    img = Image.new('RGB', (W, H), a)
    draw = ImageDraw.Draw(img)
    for y in range(H):                       # 竖向渐变：上暗下亮
        draw.line([(0, y), (W, y)], fill=_mix(a, b, y / max(1, H - 1)))
    horizon = int(H * 0.72)
    draw.rectangle([0, horizon, W, H], fill=_mix(b, (20, 24, 28), 0.28))
    draw.line([(0, horizon), (W, horizon)], fill=_mix(b, (255, 255, 255), 0.25),
              width=3)
    draw.text((120, 240), title, font=_font(180),
              fill=_mix(_rgb(top), (255, 255, 255), 0.86))
    draw.text((124, 470), sub, font=_font(64),
              fill=_mix(_rgb(top), (255, 255, 255), 0.60))
    draw.text((120, H - 90), 'demo 占位背景 —— 不是美术资源，只为让预览有画面',
              font=_font(38), fill=_mix(_rgb(bottom), (255, 255, 255), 0.55))
    return img


def _sprite(main: str, skin: str, label: str, mood: str):
    """一张竖立绘：头 + 身体 + 表情。透明背景（与真实立绘一致）。"""
    from PIL import Image, ImageDraw

    body, face = _rgb(main), _rgb(skin)
    img = Image.new('RGBA', (SW, SH), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    cx = SW // 2
    head_r = 118
    head_cy = 300
    # 身体（上窄下宽的梯形，像一件衣服）
    d.polygon([(cx - 150, 470), (cx + 150, 470), (cx + 250, SH - 40),
               (cx - 250, SH - 40)], fill=body + (255,))
    # 肩 / 衣领
    d.polygon([(cx - 150, 470), (cx + 150, 470), (cx + 60, 600),
               (cx - 60, 600)], fill=_mix(body, (255, 255, 255), 0.35) + (255,))
    # 脖子 + 头
    d.rectangle([cx - 34, 400, cx + 34, 480], fill=face + (255,))
    d.ellipse([cx - head_r, head_cy - head_r, cx + head_r, head_cy + head_r],
              fill=face + (255,))
    # 头发
    d.chord([cx - head_r - 6, head_cy - head_r - 14, cx + head_r + 6,
             head_cy + 40], 180, 360, fill=_mix(body, (40, 30, 30), 0.55) + (255,))
    # 眼睛 / 嘴（三种表情用最简单的笔画区分）
    eye_y = head_cy + 8
    if mood == 'sad':
        d.line([(cx - 62, eye_y), (cx - 22, eye_y + 12)], fill=(60, 60, 70, 255), width=7)
        d.line([(cx + 22, eye_y + 12), (cx + 62, eye_y)], fill=(60, 60, 70, 255), width=7)
        d.arc([cx - 40, eye_y + 50, cx + 40, eye_y + 96], 200, 340,
              fill=(120, 70, 70, 255), width=6)
    else:
        for ex in (cx - 46, cx + 46):
            d.ellipse([ex - 11, eye_y - 12, ex + 11, eye_y + 12],
                      fill=(45, 45, 55, 255))
        d.arc([cx - 34, eye_y + 40, cx + 34, eye_y + 86], 20, 160,
              fill=(150, 70, 80, 255), width=6)
    if mood == 'blush':
        for bx in (cx - 92, cx + 92):
            d.ellipse([bx - 30, eye_y + 26, bx + 30, eye_y + 62],
                      fill=(240, 120, 140, 120))
    # 名字牌（放在胸口，方便一眼对上是哪个角色）
    d.rounded_rectangle([cx - 200, SH - 210, cx + 200, SH - 120], radius=24,
                        fill=(0, 0, 0, 90))
    text = label
    font = _font(44)
    tw = d.textlength(text, font=font)
    d.text((cx - tw / 2, SH - 196), text, font=font, fill=(255, 255, 255, 235))
    return img


def write_all(game_dir) -> list:
    """把上面两张表里的图全画进 `game/`（覆盖旧文件）。返回写了的路径。"""
    game = Path(game_dir)
    written = []
    for rel, (top, bottom, title, sub) in BACKGROUNDS.items():
        path = game / 'images' / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        _background(top, bottom, title, sub).save(path)
        written.append(path)
    for rel, (main, skin, label, mood) in SPRITES.items():
        path = game / 'images' / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        _sprite(main, skin, label, mood).save(path)
        written.append(path)
    return written


def main(argv=None) -> int:
    root = Path(__file__).resolve().parent
    files = write_all(root / 'game')
    for p in files:
        print(f'{p.relative_to(root)}  {p.stat().st_size / 1024:.1f} KB')
    print(f'共 {len(files)} 张')
    return 0


if __name__ == '__main__':
    sys.exit(main())
