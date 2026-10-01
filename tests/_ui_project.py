"""
tests/_ui_project.py
====================
**可移植的 Qt 界面测试工程**（不依赖本机那个真实项目）。

为什么需要它
------------
原来 Qt 测试引用的是作者机器上的绝对路径
（`D:\\千连万理\\RenPyTest\\sr_general`），换台机器 / 上 CI 就整片 skip —— 
于是"新前端能不能载入、能不能翻页、能不能保存"这些**最该被守住的行为**
在 CI 上其实一次都没跑过。

这里在 tmp 目录里**现场生成**一个小 Ren'Py 工程（布局、声明、立绘、
背景、音频、变换齐全），任何机器上都能跑。真实项目那份仍然保留为
"加餐"（用 `STORYGRAPH_REAL_PROJECT` 指过去，见 `_source_paths`）。

生成的内容刻意覆盖了几个已知会出错的地方：
  · 多属性立绘（`ling base_arms_akimbo sigh`）—— 表情词不许被压平；
  · 同一角色多个立绘条目 —— extras 不许丢；
  · 逐条位置（`@i11`）—— 不许被整拍兜底值覆盖（"立绘错位"事故）；
  · 自定义 transform（`i11` / `leftish`）—— 位置候选要能选到；
  · `scene`/`play music` 不带 `bgm_` 前缀 —— 旧实现会误判成音效。
"""

from __future__ import annotations

from pathlib import Path

#: 一个最小的合法 PNG（1x1 透明）—— 让缩略图解码**真的能跑通**，
#: 而不是拿到空 QImage 后让"贴图失败"被悄悄掩盖。
PNG_1PX = bytes.fromhex(
    '89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4'
    '890000000a49444154789c63000100000500010d0a2db40000000049454e44ae'
    '426082')

OGG_HEAD = b'OggS' + b'\x00' * 64

SCRIPT = '''define e = Character("爱莲", color="#c8ffc8", image="ling",
                      voice_tag="ling")
define m = Character("玛丽", color="#ffc8c8", image="mary")

# 靠左站（自定义 transform：位置候选里要能选到它）
transform leftish:
    xpos 0.2
    xanchor 0.0

transform i11:
    xpos 0.35

label ch01:
    scene bg classroom
    play music night fadein 1.0
    show ling base_arms_akimbo sigh at i11
    e "早上好，昨晚睡得好吗？"
    voice v_ling_01
    show ling oh
    play sound door
    m "还行，就是雨下了一整晚。"
    show mary smile at right
    menu:
        "去屋顶看看":
            jump ch01_roof
        "先回教室":
            jump ch01_class

label ch01_roof:
    scene bg rooftop
    show ling happy at leftish
    e "风好大。"
    jump ch01_end

label ch01_class:
    e "还是算了。"
    jump ch01_end

label ch01_end:
    return
'''


def build_project(root: Path) -> Path:
    """
    在 `root` 下生成一个可用的 Ren'Py 工程，返回项目根。

    结构（与真实工程一致，好让 `discover_scripts` / 资源表都认）：
        root/game/script/ch01.rpy
        root/game/images/<tag>/<attr>.png
        root/game/music/*.ogg, sounds/*.ogg, voice/*.ogg
    """
    root = Path(root)
    game = root / 'game'
    (game / 'script').mkdir(parents=True, exist_ok=True)

    script = game / 'script' / 'ch01.rpy'
    script.write_text(SCRIPT, encoding='utf-8')

    assets = {
        'images/bg/classroom.png': PNG_1PX,
        'images/bg/rooftop.png': PNG_1PX,
        'images/ling/base_arms_akimbo_sigh.png': PNG_1PX,
        'images/ling/oh.png': PNG_1PX,
        'images/ling/happy.png': PNG_1PX,
        'images/mary/smile.png': PNG_1PX,
        'music/night.ogg': OGG_HEAD,          # 故意不带 bgm_ 前缀
        'sounds/door.ogg': OGG_HEAD,
        'voice/v_ling_01.ogg': OGG_HEAD,
    }
    for rel, data in assets.items():
        p = game / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    # 资源表：有它 `load_asset_registry` 才不会返回 None，
    # 「参与角色 / 资产选择器」这些功能才有数据可读。
    _write_asset_tables(root, game)
    return root


def _write_asset_tables(root: Path, game: Path) -> None:
    """
    写一份最小的资源表（图像）。

    为什么最小的就行：角色清单在真实流程里是**脚本 define 合并进来**的
    （`apply_script_definitions`），所以这里只需要让资源表**存在**，
    好让 `load_asset_registry` 不返回 None。
    """
    rows = []
    for rel in ('images/bg/classroom.png', 'images/bg/rooftop.png',
                'images/ling/base_arms_akimbo_sigh.png',
                'images/ling/oh.png', 'images/ling/happy.png',
                'images/mary/smile.png'):
        aid = Path(rel).stem                       # classroom / oh / happy …
        tag = Path(rel).parts[-2]                  # bg / ling / mary
        rows.append({
            'ID': f'{tag}_{aid}' if tag != 'bg' else f'bg_{aid}',
            'Path': f'game/{rel}',
            'Tag': tag,
            'Attributes': aid,
            'Category': 'background' if tag == 'bg' else 'character',
            'Notes': '',
        })
    try:
        from storygraph.assets.asset_registry import write_asset_excel
        write_asset_excel({'images': rows}, root / 'asset_tables')
    except Exception:                              # noqa: BLE001
        pass                                       # 装不出表也不该让夹具挂掉


def build_and_scan(root: Path):
    """
    生成工程并扫一遍，返回 `(project_root, script_path, graph)`。

    `graph` 可能为 None（解析失败时）—— 调用方自己判断。
    """
    root = build_project(root)
    script = root / 'game' / 'script' / 'ch01.rpy'
    graph = None
    try:
        from storygraph.parsing.discover import discover_scripts
        from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
        files = discover_scripts(root)
        _pr, graph = IncrementalRenPyAnalyzer().analyze(files=files, graph=None)
    except Exception:                        # noqa: BLE001
        graph = None
    return root, script, graph


__all__ = ['PNG_1PX', 'OGG_HEAD', 'SCRIPT', 'build_project', 'build_and_scan']
