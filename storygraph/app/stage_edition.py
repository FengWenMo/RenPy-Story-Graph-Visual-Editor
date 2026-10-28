"""
storygraph.app.stage_edition
============================
**演出专用版**（Stage Edition）的两件"随包礼物"：项目预设 + 收到项目就打开。

背景（2026-10-29，用户拍板）
----------------------------
交付对象是**文案和策划**：他们没有 VSCode、没有 Ren'Py、没有 Python，
拿到的只有两样东西 ——「完整项目文件夹」和「这个工具」。所以工具这一侧
必须自己把"看不懂的配置"配好：

  1. **真实项目的 rules + 布局随包发**（`storygraph/presets/`）：
     `storygraph.rules.json` 里是「show」的 角色/服装/表情 三列候选
     （文案在卡片上点下拉就能选，不用背文件名）；`layout.json` 是剧情图
     摆好的版式（43 章的坐标，打开就看得见全貌）。
     项目里**已经有**同名文件时一个字都不动；**缺**才从预设补 ——
     "补缺"和"覆盖"是两件事（十条铁律第 5 条）。
  2. **收到项目文件夹就自动打开**：工具旁边放了 `项目/`（或解压进项目根，
     或把项目文件夹拖到 exe 上），启动时自己找到它并打开 ——
     不让文案先学会"📁 打开项目在哪、要选到哪一层"。

⚠ 只在**演出专用版**里生效（`engine_preview.stage_edition()`）。标准版
打开别人的项目时，行为必须和以前一模一样（不写任何文件、不自动换项目）。

为什么预设是"写进项目"而不是"只在内存里兜底"
----------------------------------------------
`storygraph.rules.json` 是**工具自己的配置**（不是用户剧本），项目里有了它，
之后无论用哪个版本打开、发给谁，候选列都还在 —— 它跟项目走，而不是跟
这一台电脑走。布局同理：`<项目>/.storygraph/layout.json` 是项目级版式，
文案挪一挪模块，下次打开还是那样。

写盘安全
--------
· 只写**缺的**那两个文件；已有文件连 mtime 都不碰；
· 用"临时文件 + `os.replace`"落盘（写一半断电不会留下半份 JSON）；
· 预设读不出来 / 写不进去 → **不猜、不补空文件**，把原因返回给界面说清楚。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from storygraph.app import engine_preview as EP
from storygraph.core.safeguard import swallow#: 打包版里预设目录的名字（与 `packaging/make_stage_release.py` 必须同名）。
PRESET_DIRNAME = '项目预设'

#: 预设里的两个文件（`storygraph/presets/` 里也是这两个名字）。
RULES_NAME = 'storygraph.rules.json'
LAYOUT_NAME = 'layout.json'

#: 项目里这四个路径任一存在，就算"已经有这份配置"（不补）。
RULES_TARGETS = ('storygraph.rules.json', 'game/storygraph.rules.json')
LAYOUT_TARGETS = ('.storygraph/layout.json', 'storygraph_layout.json')

#: 写进项目的规范位置（`storygraph.statement_registry.manifest_path` 与
#: `ui_qt.window_graph_ops` 的自动保存位置各自认这个）。
RULES_WRITE = 'storygraph.rules.json'
LAYOUT_WRITE = '.storygraph/layout.json'

_COPY = 'copied'
_EXISTS = 'exists'
_NO_PRESET = 'no-preset'
_ERROR = 'error'

#: 演出专用版包里**本来就有**的目录（它们不是"收到的项目"）。
#: `find_received_project` 的宽扫描要把它们排掉 —— 否则用户没放项目时，
#: 工具会自动打开 `demo/`（示例工程），还会把预设补进 demo 里。
_KNOWN_PACKAGE_DIRS = frozenset({
    'renpy_sdk', 'renpy', 'lib', 'launcher', 'doc', 'gui', 'tutorial',
    'the_question', 'sdk-fonts', 'update', 'renpy.app', 'manual_img',
    'demo', PRESET_DIRNAME,
})


def app_dir() -> Path | None:
    """工具自己所在的目录（打包后是 exe 旁边；源码运行是仓库根）。"""
    return EP.app_dir()


def _has_game(path: Path) -> bool:
    """
    `path` 里有没有**名字精确等于 `game`** 的目录。

    Windows 不区分大小写：项目旁边放个 `Game/` 目录时 `(path/'game').is_dir()`
    也会是 True —— 于是"旁边某层有个同名大小写不同的目录"被当成 Ren'Py 项目，
    自动打开/自动补预设就会落在错的地方。名字必须精确。
    """
    try:
        for entry in os.scandir(path):
            if entry.name == 'game' and entry.is_dir():
                return True
    except OSError:
        return False
    return False


def preset_dir() -> Path | None:
    """
    预设放在哪：

      · 打包版：`<exe 旁边>/项目预设/`（发布脚本铺进去的），
      · 源码运行：`<仓库根>/storygraph/presets/`（就是被复制进发布包的那份）。

    两边都不存在返回 `None`（标准版没有预设，是正常状态，不是错误）。
    """
    root = app_dir()
    if root is None:
        return None
    if getattr(sys, 'frozen', False) is False and (root / 'storygraph' / 'presets').is_dir():
        return root / 'storygraph' / 'presets'
    packaged = root / PRESET_DIRNAME
    if packaged.is_dir():
        return packaged
    dev = root / 'storygraph' / 'presets'
    if dev.is_dir():
        return dev
    return None


def preset_file(name: str) -> Path | None:
    d = preset_dir()
    if d is None:
        return None
    p = d / name
    return p if p.is_file() else None


def _first_existing(root: Path, rels) -> str:
    for rel in rels:
        if (root / rel).is_file():
            return rel
    return ''


def _write_bytes_atomic(dst: Path, data: bytes) -> None:
    """
    原子写：先写同目录的临时文件，再 `os.replace`。

    为什么不用 `shutil.copy` 直接落盘：复制到一半（磁盘满 / 被杀进程）
    会留下半份 JSON —— 下次打开工具时 rules 解析失败，界面表现为
    "下拉候选全没了"，很难联想到是上次被打断的复制。
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + '.storygraph-tmp')
    tmp.write_bytes(data)
    os.replace(tmp, dst)


def apply_presets(project_root, *, dry_run: bool = False) -> dict:
    """
    把随包预设**补**进项目（只补缺，绝不覆盖）。

    返回（界面/控制台直接拿它说话）::

        {'rules': 'copied'|'exists'|'no-preset'|'error',
         'layout': …,
         'actions': ['已随包补齐 …', …],     # 给用户看的人话
         'errors': ['…'],                    # 出错原因（不含敏感信息）
         'root': '<项目根>'}

    `dry_run=True` 只算"会补什么"，一个字节都不写（测试与预览用）。
    """
    root = Path(str(project_root or '')).expanduser()
    out = {'root': str(root), 'rules': _NO_PRESET, 'layout': _NO_PRESET,
           'actions': [], 'errors': []}
    if not root.is_dir():
        out['errors'].append(f'项目目录不存在：{root}')
        return out

    plan = []
    if not _first_existing(root, RULES_TARGETS):
        src = preset_file(RULES_NAME)
        if src is None:
            out['rules'] = _NO_PRESET
        else:
            plan.append(('rules', src, root / RULES_WRITE))
    else:
        out['rules'] = _EXISTS

    if not _first_existing(root, LAYOUT_TARGETS):
        src = preset_file(LAYOUT_NAME)
        if src is None:
            out['layout'] = _NO_PRESET
        else:
            plan.append(('layout', src, root / LAYOUT_WRITE))
    else:
        out['layout'] = _EXISTS

    for kind, src, dst in plan:
        if dry_run:
            out[kind] = _COPY
            out['actions'].append(f'会随包补齐：{dst}')
            continue
        try:
            data = src.read_bytes()
            _write_bytes_atomic(dst, data)
            out[kind] = _COPY
            out['actions'].append(f'已随包补齐：{dst}')
        except OSError as e:                # noqa: PERF203 - 两个文件各自报
            out[kind] = _ERROR
            out['errors'].append(f'补不进去：{dst}（{e}）')
    if out['rules'] == _COPY:
        # rules 是 `StatementRegistry.for_project` 的输入，而且它**带缓存**
        # （按项目根缓一份）——补完不清缓存，下拉候选还是旧的空表，
        # 用户会以为"预设没生效"。
        try:
            from storygraph.story.statement_registry import clear_cache
            clear_cache()
        except Exception as e:              # noqa: BLE001 - 清不掉只是慢一点
            swallow('app.stage_edition.clear_registry_cache', e)
    return out


def find_received_project(package_dir=None) -> Path | None:
    """
    "工具收到项目文件夹"时，项目在哪？

    找法按优先级（第一个成立的算）：

      1. `<包目录>/项目/`（发版时把项目放在这个固定名字里）；
      2. `<包目录>/project/`（英文名，有人会这么放）；
      3. 包目录**本身就是项目根**（解压进项目文件夹里）；
      4. 包目录的**上一级就是项目根**（`项目/StoryGraph-Stage/…` 这种解压法）；
      5. 上面某一层里有**唯一一个**子目录带 `game/` —— 就是它。

    ⚠ 宁可返回 `None` 也不猜：`项目A/`、`项目B/` 并排放着时要让用户自己选，
    猜错的代价是"打开的不是我要的项目、还自动往它里面补规则"。

    源码运行（仓库根）时也可能正好上一级是某个项目 —— 这是**开发便利**；
    是否真的自动打开由 `main_qt` 用"演出专用版"开关把门。
    """
    root = Path(package_dir) if package_dir is not None else app_dir()
    if root is None:
        return None
    try:
        root = root.resolve()
    except OSError as e:                    # noqa: BLE001 - 解析不了就用原路径
        swallow('app.stage_edition.resolve_received', e)

    # 3) 包目录本身就是项目根
    if _has_game(root):
        return root

    for name in ('项目', 'project', '游戏'):
        candidate = root / name
        if not candidate.is_dir():
            continue
        if _has_game(candidate):
            return candidate
        # 固定名字的目录里再套一层（解压时"多了一层同名目录"很常见）
        subs = [p for p in candidate.iterdir()
                if p.is_dir() and _has_game(p)]
        if len(subs) == 1:
            return subs[0]
        if len(subs) > 1:
            return None                     # 有歧义：交给用户选

    # 4) 包目录的上一级是项目根
    try:
        parent = root.parent
    except OSError:
        parent = None
    if parent is not None and parent != root and _has_game(parent):
        return parent

    # 5) 包目录里唯一一个带 game/ 的子目录（**只在打包版里做**）。
    #    源码运行时仓库根下就有 `demo/game/`（演示工程），这一条会把 demo
    #    当成"随包项目"自动打开 —— 开发时看着像工具自己乱开项目。
    #    打包版同理：包里本来就带 `demo/`（示例工程），它**不是**"收到的
    #    项目"；不排掉的话，用户没放项目时会自动打开 demo，还会把预设
    #    补进 demo 里（实测自检里就报出了"随包项目：demo"）。
    if not getattr(sys, 'frozen', False):
        return None
    try:
        subs = [p for p in root.iterdir()
                if p.is_dir() and _has_game(p)
                and p.name not in _KNOWN_PACKAGE_DIRS
                and not p.name.startswith('_')]
    except OSError:
        subs = []
    if len(subs) == 1:
        return subs[0]
    return None


def received_project_hint() -> str:
    """没有自动找到项目时，说清楚"该把它放哪"（给第一次用的人看）。"""
    return ('没找到随工具一起发过来的项目文件夹。把「项目」文件夹放在 '
            'StoryGraph.exe 旁边（或把项目文件夹拖到 StoryGraph.exe 上），'
            '下次启动会自动打开；也可以点「📁 打开项目」自己选。')


__all__ = ['PRESET_DIRNAME', 'RULES_NAME', 'LAYOUT_NAME', 'RULES_TARGETS',
           'LAYOUT_TARGETS', 'RULES_WRITE', 'LAYOUT_WRITE',
           'app_dir', 'preset_dir', 'preset_file', 'apply_presets',
           'find_received_project', 'received_project_hint']
