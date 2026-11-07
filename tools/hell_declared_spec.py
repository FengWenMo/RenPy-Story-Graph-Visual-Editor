# -*- coding: utf-8 -*-
"""
地狱测试 · 第七轮：**生成器 × 校验器自相矛盾**（2026-11-03）
==========================================================

换的轴：前六轮测的是"产物对不对"，这一轮测**工具自己的两句话打不打架**。

为什么单独立一轮（真实事故，用户连报两轮）：

    卡片里那句 `qing base` 是**选择器自己写出来的**（`resolve_pick('qing_normal')
    → spec='qing base'`，因为 `definitions.rpy` 里 `attribute base default:
    "normal"`），而同一张卡片旁边那条提示说「`qing` 不是已知的角色立绘标签」。
    工具先算出一句合法的 `show`，再说它不合法。

真实数据（真机 `sr_general`）：

| 路径 | 判据 | 结论 |
|---|---|---|
| 选择器 `resolve_pick` | `pick_context.known_tags`（四路，含 layeredimage 声明） | 合法 |
| 预览 `image_path` | 认 `layeredimage` + 属性反查 | 有图 |
| 卡片提示 `known_tag_warning` | `Symbols.is_actor_tag`（**只查角色表**） | 不合法 |

不变量（这一轮的判据表）
------------------------

**I7 自产自销**：`resolve_pick` 产出 `ok=True` 的每个 spec，
`known_tag_warning(spec)` 必须为空。**任何**"生成器/校验器不一致"都会现形，
不需要预先知道缺陷形状。

**I8 声明优先**：项目 `.rpy` 里 `layeredimage` / `image` 声明过的 tag+属性，
永远不许被判成"不是立绘"或"没有这个属性"。

**I9 判不了就闭嘴**：声明认得**不完整**时（表达式属性 / 无 `image_format` /
定义在 `python:` 块里 / 文件读不出来），只许**不吭声**，不许报"不是立绘"
（假警报比不提示更糟 —— 用户会去改本来没问题的东西）。

**I10 口径一致**：同一个 spec，选择器 / 预览 / 提示 / chip 候选四处给的结论
必须同向。横切轴：大小写、下划线写法、多属性、`@` 变换后缀。

跑法
----
    D:\\Python\\python.exe tools/hell_declared_spec.py            # 合成形态
    D:\\Python\\python.exe tools/hell_declared_spec.py --real     # + 真机全量闭环
    D:\\Python\\python.exe tools/hell_declared_spec.py --real --limit 200

退出码：0 = 全绿；1 = 有红（每条红都打印"最小复现"）。

⚠ 本脚本只读：合成项目建在临时目录，真机项目一个字节都不写。
⚠ 跑真机那一段**不要**和其他 pytest 并发（仓库内 basetemp 会互删）。
"""
from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

REAL = Path(r'D:\千连万理\RenpyTest\sr_general')

#: xlsx 需要一张合法的小 png（`declared_sprite_words` 的属性表按**真实文件**建）
PNG = (b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00'
       b'\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc'
       b'\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82')

FAILED: list = []


def red(case: str, detail: str) -> None:
    FAILED.append((case, detail))
    print(f'  ✗ {case}\n      {detail}')


def green(case: str) -> None:
    print(f'  ✓ {case}')


# ===========================================================================
#  一、合成形态矩阵：layeredimage 的八种真实写法
# ===========================================================================
#
# 每一条都是**真实项目里会出现的**写法，不是编的：
#   · 字面文件名 / 属性名≠文件名  —— 真机 qing（base→normal, angry→speechless）
#   · 无 image_format            —— 早期写法，一律 `attribute x: "file"`
#   · 条件组 `when`              —— 真机 lan 那套（记忆：条件不成立整组不叠）
#   · always / if_any 组          —— Ren'Py 官方文档里的写法
#   · 表达式属性（`"n_" + x`）     —— 工具**认不出**，此时只许闭嘴
#   · `_` 开头的私有属性           —— 内部图层，不该进候选但也不许报错
#   · `at` 变换 / `default`       —— 默认显示哪个
#   · 定义在 `python:` 块里        —— 工具完全隐形（对工具是"没有声明"）
CASES = {
    # (标签, 目录名, 声明文本, 期望)
    'literal': (
        'qing', 'qing',
        'layeredimage qing:\n'
        "    image_format 'images/characters/qing/{image}.png'\n"
        '    attribute base default:\n'
        '        "normal"\n'
        '\n'
        '    group expressions:\n'
        '        attribute angry:\n'
        "            'speechless'\n",
        {'ok': ['base', 'angry'], 'bad': ['nosuch']},
    ),
    'no-image-format': (
        'plain', 'plain',
        'layeredimage plain:\n'
        '    attribute calm default:\n'
        "        'calm'\n"
        '    attribute shout:\n'
        "        'shout'\n",
        {'ok': ['calm', 'shout'], 'bad': ['nosuch']},
    ),
    'conditional-group': (
        'cond', 'cond',
        'layeredimage cond:\n'
        "    image_format 'images/characters/cond/{image}.png'\n"
        '    attribute base default:\n'
        '        "base"\n'
        '\n'
        '    group outfit when hanfu:\n'
        '        attribute hanfu:\n'
        "            'hanfu'\n",
        {'ok': ['base', 'hanfu'], 'bad': ['nosuch']},
    ),
    'always-group': (
        'alw', 'alw',
        'layeredimage alw:\n'
        "    image_format 'images/characters/alw/{image}.png'\n"
        '    always:\n'
        "        'sprite_shadow'\n"
        '    attribute base default:\n'
        "        'base'\n",
        {'ok': ['base'], 'bad': ['nosuch']},
    ),
    'private-attr': (
        'priv', 'priv',
        'layeredimage priv:\n'
        "    image_format 'images/characters/priv/{image}.png'\n"
        '    attribute _line:\n'
        "        '_line'\n"
        '    attribute base default:\n'
        "        'base'\n",
        {'ok': ['base'], 'bad': ['nosuch']},
    ),
    'underscore-attr-name': (
        'us', 'us',
        'layeredimage us:\n'
        "    image_format 'images/characters/us/{image}.png'\n"
        '    attribute base_hand_up default:\n'
        "        'base_hand_up'\n",
        {'ok': ['base_hand_up'], 'bad': ['nosuch']},
    ),
    # —— 下面两类是"认不出来"的：只许闭嘴，不许误报 ——
    'expression-attr': (
        'expr', 'expr',
        'layeredimage expr:\n'
        "    image_format 'images/characters/expr/{image}.png'\n"
        '    attribute base default:\n'
        "        'base'\n"
        '    attribute dyn:\n'
        '        "n_" + suffix\n',
        {'ok': ['base'], 'quiet': ['dyn']},
    ),
    'python-block': (
        'pyb', 'pyb',
        'python:\n'
        '    pass\n'
        '\n'
        '# 工具对下面这种"只在运行时才存在"的声明是看不见的\n'
        'init python:\n'
        '    renpy.image("pyb base", "images/characters/pyb/base.png")\n',
        {'quiet': ['pyb']},
    ),
}


def _make_project(root: Path, tag: str, dirname: str, decl: str) -> None:
    sprite_dir = root / 'game' / 'images' / 'characters' / dirname
    sprite_dir.mkdir(parents=True, exist_ok=True)
    # 目录里放几张真文件：属性表是按真实文件建的
    for name in ('normal', 'speechless', 'base', 'shout', 'calm', 'hanfu',
                 'sheng', 'sprite_shadow', '_line', 'base_hand_up'):
        (sprite_dir / f'{name}.png').write_bytes(PNG)
    (root / 'game' / 'definitions.rpy').write_text(decl, encoding='utf-8')
    (root / 'game' / 'script.rpy').write_text(
        'label start:\n    return\n', encoding='utf-8')


def _registry_for(root: Path, tag: str, dirname: str):
    """造一份最小资源表 + 角色表（角色表里**故意不放这个 tag** —— 真机就是
    `qing` 由 `q` 出声，自己不在角色表里）。"""
    from storygraph.assets.asset_registry import AssetRegistry
    reg = AssetRegistry()
    reg.project_root = str(root)
    d = root / 'game' / 'images' / 'characters' / dirname
    for p in sorted(d.glob('*.png')):
        reg.images[f'{tag}_{p.stem}'] = {
            'ID': f'{tag}_{p.stem}', 'Tag': tag, 'Attributes': p.stem,
            'Value': p.stem,
            'Path': f'game/images/characters/{dirname}/{p.name}',
            'Category': 'character'}
    # 一个无关角色，保证"角色表非空"（空表时 `is_actor_tag` 会放行一切，
    # 那样这一轮什么都测不出来 —— 判据校准第 12 条：别自己制造前提）
    reg.characters['other'] = {'ID': 'other', 'Name': '别人',
                               'ImageTag': 'othertag'}
    return reg


def run_matrix() -> None:
    from storygraph.story.dsl import Symbols
    from storygraph.ui_qt.sprite_grid import known_tag_warning

    print('\n== 一、声明形态矩阵（I8 声明优先 / I9 判不了就闭嘴）==')
    for name, (tag, dirname, decl, expect) in CASES.items():
        root = Path(tempfile.mkdtemp(prefix=f'sgh_{name}_'))
        try:
            _make_project(root, tag, dirname, decl)
            reg = _registry_for(root, tag, dirname)
            sym = Symbols(reg)
            declared = sym.declared_attrs_of(tag)

            # I8：声明读得出来吗
            for attr in expect.get('ok', []):
                if attr not in declared:
                    red(f'{name}: 声明没读到「{attr}」',
                        f'declared_attrs_of({tag!r}) = {declared}\n'
                        f'      声明文本：{decl.strip()[:80]!r}')
                    continue
                warn = known_tag_warning(f'{tag} {attr}', sym)
                if warn:
                    red(f'{name}: 声明过的「{tag} {attr}」被警告',
                        f'      warning = {warn!r}')
                else:
                    green(f'{name}: {tag} {attr}')

            # 属性名写错：要么说清"有哪几个"，要么闭嘴（不许说"不是立绘"）
            for attr in expect.get('bad', []):
                warn = known_tag_warning(f'{tag} {attr}', sym)
                if '不是已知的角色立绘标签' in warn:
                    red(f'{name}: 属性写错被说成"不是立绘"',
                        f'      {tag!r} 明明声明过，warning = {warn!r}')
                elif not warn:
                    red(f'{name}: 属性写错却一声不吭',
                        f'      {tag} {attr} 不存在，应该有提示')
                else:
                    green(f'{name}: {tag} {attr}（正确报"没有这个属性"）')

            # I9：认不出来的声明 → 只许闭嘴
            for attr in expect.get('quiet', []):
                warn = known_tag_warning(f'{tag} {attr}', sym)
                if '不是已知的角色立绘标签' in warn:
                    red(f'{name}: 认不出的声明被误报成"不是立绘"',
                        f'      warning = {warn!r}')
                else:
                    green(f'{name}: {tag} {attr}（认不出 → 不误报）')
        except Exception as exc:                   # noqa: BLE001
            red(f'{name}: 探针自己炸了', f'{type(exc).__name__}: {exc}\n'
                                       + traceback.format_exc(limit=3))
        finally:
            shutil.rmtree(root, ignore_errors=True)


# ===========================================================================
#  二、口径一致（I10）：同一个 spec，四种写法
# ===========================================================================

def run_spellings() -> None:
    from storygraph.story.dsl import Symbols
    from storygraph.ui_qt.sprite_grid import known_tag_warning

    print('\n== 二、写法横切（I10 口径一致）==')
    root = Path(tempfile.mkdtemp(prefix='sgh_spell_'))
    try:
        tag, dirname = 'qing', 'qing'
        _make_project(root, tag, dirname, CASES['literal'][2])
        reg = _registry_for(root, tag, dirname)
        sym = Symbols(reg)

        cases = [
            ('qing base', '官方写法'),
            ('qing  base', '多一个空格'),
            (' qing base ', '首尾空白'),
            ('qing base angry', '一次写两个属性'),
            ('qing base@left', '带 AT 变换后缀'),
            ('qing base @left', '空格 + AT 后缀'),
            ('Qing base', '首字母大写'),
            ('qing_base', '下划线写法（卡片可能存这种）'),
        ]
        for spec, why in cases:
            warn = known_tag_warning(spec, sym)
            if '不是已知的角色立绘标签' in warn:
                red(f'写法「{spec}」（{why}）被判成"不是立绘"',
                    f'      warning = {warn!r}')
            else:
                green(f'{spec!r}（{why}）')
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ===========================================================================
#  三、I7 自产自销（真机全量）：选择器写出的每个 spec，提示必须认可
# ===========================================================================

def run_self_consistency(limit: int = 0) -> None:
    if not REAL.is_dir():
        print('\n== 三、自产自销 ==\n  （真机项目不在本机，跳过）')
        return
    from storygraph.assets.registry_load import load_asset_registry
    from storygraph.app.picker import resolve_pick
    from storygraph.story.dsl import Symbols
    from storygraph.ui_qt.sprite_grid import known_tag_warning

    print('\n== 三、I7 自产自销（真机全量）==')
    reg = load_asset_registry(REAL)
    sym = Symbols(reg)
    # ⚠ **上下文只建一次**：`resolve_pick` 不传上下文时每次都会重建
    # （读映射 + 声明 + 建副本索引），1979 张图跑下来要十几分钟。
    # 显式传进去，测的仍是同一条链路。
    from storygraph.app.picker import pick_context
    from storygraph.assets.asset_identity import build_copy_index
    ctx = pick_context(reg, str(REAL))
    # 副本索引也要只建一次：`resolve_pick` 内部默认每调用一次重建
    # （1979 行的两两比较 → 平方级），这是全量跑不完的真正原因。
    copies = build_copy_index(ctx['all_rows'])
    rows = list(reg.images.items())
    if limit:
        rows = rows[:limit]
    n_ok = n_bad = 0
    import time
    t0 = time.time()
    for n, (aid, row) in enumerate(rows, 1):
        if n % 50 == 0:
            print(f'    … {n}/{len(rows)}  {time.time() - t0:.1f}s', flush=True)
        _t1 = time.time()
        try:
            res = resolve_pick(aid, row, registry=reg,
                               project_root=str(REAL),
                               known_tags=ctx['known_tags'],
                               known_chars=ctx['known_chars'],
                               mapping=ctx['mapping'],
                               all_rows=ctx['all_rows'],
                               sprite_words=ctx['sprite_words'],
                               copy_index=copies)
        except Exception as exc:                  # noqa: BLE001
            red(f'{aid}: resolve_pick 抛异常',
                f'{type(exc).__name__}: {exc}')
            continue
        _dt = time.time() - _t1
        if _dt > 2.0:
            print(f'    ⏱ 慢图 {aid} {_dt:.1f}s  {row.get("Path")!r}',
                  flush=True)
        if not res.ok or not res.spec:
            continue                              # 选择器自己拒绝了，不算"自相矛盾"
        # ⚠ **判据校准**（第一版把背景图也算进来，报了一条假红）：
        # `bg` / `cg` 能 `show`，但**不该放进立绘格** —— 卡片提示说"这不是立绘"
        # 是设计，不是自相矛盾。I7 只问"立绘格里的 spec，提示认不认"。
        kind = getattr(res.identity, 'kind', '') if res.identity else ''
        if kind != 'sprite':
            continue
        # I11：spec 里**首词被重复拼了一次**（`bg bg_1` 那种）——
        # Ren'Py 的自动 image name 是 `bg_1`（单段），多一层就找不到。
        parts = res.spec.split()
        if len(parts) >= 2 and parts[1].lower().startswith(parts[0].lower() + '_'):
            red(f'{aid}: spec 把 tag 拼了两遍 → {res.spec!r}',
                f'      path = {row.get("Path")!r}\n'
                f'      引擎自动注册的名字只有一段：{parts[1]!r}')
            continue
        n_ok += 1
        warn = known_tag_warning(res.spec, sym)
        if warn:
            n_bad += 1
            red(f'{aid}: 选择器写出 {res.spec!r}，提示却说它不行',
                f'      warning = {warn!r}\n'
                f'      path    = {row.get("Path")!r}\n'
                f'      tag     = {row.get("Tag")!r} '
                f'attrs={row.get("Attributes")!r}')
    print(f'  选择器产出 {n_ok} 条 spec，其中 {n_bad} 条被提示否定')
    if not n_bad:
        green(f'自产自销闭环（{n_ok} 条）')


# ===========================================================================

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--real', action='store_true', help='跑真机全量闭环')
    ap.add_argument('--limit', type=int, default=0, help='真机只跑前 N 张图')
    args = ap.parse_args()

    print('=' * 66)
    print('地狱测试 · 第七轮：生成器 × 校验器（2026-11-03）')
    print('=' * 66)
    run_matrix()
    run_spellings()
    if args.real:
        run_self_consistency(args.limit)

    print('\n' + '=' * 66)
    if FAILED:
        print(f'RESULT: FAIL —— {len(FAILED)} 条红')
        for case, _ in FAILED:
            print(f'  x {case}')
        return 1
    print('RESULT: PASS —— 所有判据通过')
    return 0


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    raise SystemExit(main())
