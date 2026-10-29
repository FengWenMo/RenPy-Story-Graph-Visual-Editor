"""
打包发布物：exe 目录 + 剧本空表格 + demo 工程（可选）+ 上手/手册 + LICENSE
==========================================================================

`packaging/build_exe.ps1` 的第 5 步调它。也可以单独跑（用已有的 `dist\\StoryGraph\\`）：

    python packaging/make_release.py
    python packaging/make_release.py --stage-only        # 只铺目录，不 zip
    python packaging/make_release.py --dist dist\\StoryGraph --out dist\\release
    python packaging/make_release.py --no-demo --zip     # 程序版：不 demo（干净包）

为什么要一个脚本（2026-10-23，竞争分析 P0-1）：
    上一版 zip（`StoryGraph-1.0.0-win64-2026-10-21.zip`）实测
    **xlsx = 0 个、md = 0 个、demo = 0 个** —— 双击能开，但"打开项目"没东西可开、
    想从零开始没有模板、出问题没有一页说明。竞品随包附空表格；我们连"装完干嘛"
    都没有。这个脚本把"该塞什么"写成清单，少一样就直接报错（不默默发出去）。

清单（少一项就失败，别改成"警告"）：

  · `StoryGraph.exe` / `StoryGraph-CLI.exe`（PyInstaller 产物，**必须**在 dist 里）
  · `剧本空表格.xlsx` —— 空 Beats 表 + 「列说明」页（"完全初始化"的那份模板；
    程序自己不需要 demo 才能跑，但"从零开始"要有这张空表）
  · `demo\\` —— 能双击打开的示例工程（**只有标准版带**；`--no-demo` 时整棵不发）
    （**不带** `collab.db` / `_import_tmp.xlsx` / 任何 `build*` 产物树
    / `make_*.py` 开发者脚本 / `协作\\` 演示残留）
  · `快速上手.md` —— 1 页速查（docs/快速上手_3分钟.md 的发布版）
  · `新手快速操作手册.md` + `manual_img\\` —— 完整新手手册与它的两张截图
  · `LICENSE` —— GPLv3（第三方组件说明在文末）

为什么有 `--no-demo`（2026-10-29 用户拍板"程序用的那一版"）
------------------------------------------------------------
用户要一版**只给程序用**的干净包：不带示例工程（`demo/`）、不带"能重建 /
本机状态 / 生成出来的占位美术"那类素材和文件夹。程序自己能把项目打开、
把规则和布局补起来，不需要拿 demo 当拐杖。上一版（含 demo 占位美术和手
册截图）被判定"不干净"，这一版从清单开始就不收 demo —— 少了就用
`REQUIRED_BASE` 判，而不是"有 demo 也行"。

所以清单分成两段：`REQUIRED_BASE`（程序版/演出专用版都必须要）+
`REQUIRED_DEMO`（只有带 demo 的标准版才要）。两向自检照旧：
缺了报错、多了（比如 `--no-demo` 却混进一棵 `demo/`）也报错。

两向自检（缺一样、多一样都拒绝发）：
  · `check_manifest` 防"缺" —— 上一版的教训（zip 里 xlsx=0 / demo=0）；
  · `check_forbidden` 防"多" —— 2026-10-28 的教训（demo 里混进 8 个构建
    产物 + 2 个开发者脚本，"双击能开"这条判据完全发现不了）。
"""
from __future__ import annotations

import argparse
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: 随包发的**文档**：(仓库里的源文件, 包内名字)。缺任何一份都失败 ——
#: 2026-10-27 之前包里只有一张 1 页的速查，用户问得最多的是"按钮在哪、
#: 为什么导出被拦"，那一页答不了。
MANUALS = (
    ('docs/快速上手_3分钟.md', '快速上手.md'),
    ('docs/新手快速操作手册.md', '新手快速操作手册.md'),
)
#: 手册里的截图目录（md 用相对路径 `manual_img/xx.png` 引用，包内同布局）
MANUAL_IMG_DIR = 'docs/manual_img'
MANUAL_IMG_DEST = 'manual_img'

#: demo 里**不发**的目录（产物/缓存/协作库：要么能重建，要么是用户本机状态）
DEMO_SKIP_DIRS = {'__pycache__', '.pytest_cache', 'saves', 'cache', '.git',
                  '协作'}
#: 目录名以这些开头的，整棵不发。**这是 2026-10-28 补的**：
#: 原来只写死了一个 `build`，于是 `build_out/` / `build_renpy/` /
#: `build_unity/` 三棵产物树整个漏进发布包（实测 zip 里 8 个文件）——
#: 用户解压后打开 demo，会看到一份 `story.generated.rpy`，
#: 而没有任何东西告诉他"这份不是你的源文件，是上次构建的产物"。
#: 构建产物一律不发：点一次「构建」就能重新生成，属于"能重建的不该随包"。
DEMO_SKIP_PREFIXES = ('build',)
#: demo 里**不发**的文件（协作库是用户本机数据；`_import_tmp` 是中间产物；
#: `make_*.py` 是**开发者重建 demo 的脚本**，终端用户拿到只会多一堆看不懂的 py）
DEMO_SKIP_FILES = {'collab.db', '_import_tmp.xlsx', '.DS_Store', 'Thumbs.db',
                   'make_art.py', 'make_demo.py'}

#: 收尾自检「不该有」的判据（`check_forbidden` 用；**目录**与**文件**分开列，
#: 因为 glob 的 `build*` 只会命中目录本身，命不中目录里的文件 —— 第一版就
#: 这么漏过一次）。前两条是 2026-10-28 那次事故的正脸。
FORBIDDEN_DEMO_DIR_PREFIXES = ('build',)        # demo/build*：构建产物树
FORBIDDEN_DEMO_DIRS = {'协作', '__pycache__', '.pytest_cache'}
FORBIDDEN_DEMO_SUFFIXES = {'.py', '.pyc', '.bak', '.db'}   # 脚本 / 备份 / 本机库
#: **程序版 / 演出专用版**也必须有的东西：exe + 初始化模板 + 两份文档 +
#: 手册截图 + LICENSE。这些没有一个是"示例"，删掉任何一个包都不完整。
REQUIRED_BASE = ('StoryGraph.exe', 'StoryGraph-CLI.exe',
                 '剧本空表格.xlsx', '快速上手.md', '新手快速操作手册.md',
                 'manual_img/01_structure.png', 'manual_img/02_card.png',
                 'LICENSE')
#: 示例工程 `demo/` 里必须有的三件锚点（只有**带 demo** 的标准版才要求）：
#: 一份能打开的表 / 一章能导入的脚本 / 一张能显示的背景。少一件说明
#: demo 被"清过头"了，发出去等于没 demo。
REQUIRED_DEMO = ('demo/剧本表_ch01.xlsx', 'demo/game/script/ch01.rpy',
                 'demo/game/images/bg/bg_classroom.png')
#: 标准版（默认，带 demo）的完整清单 = 基础 + demo。
#: `--no-demo`（程序版）只查 `REQUIRED_BASE`。
REQUIRED = REQUIRED_BASE + REQUIRED_DEMO


def write_template(path, *, registry=None) -> Path:
    """
    生成**剧本空表格模板**：空 Beats 表 + 「列说明」页。

    空表也要走真实导出器（`export_beats_to_excel`）—— 自己拼一个只有表头的 xlsx，
    列名/隐藏列/说明页迟早与产品对不上，而用户拿到的模板就是"填了导不回去"。
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(ROOT))
    from storygraph.story.excel_export import export_beats_to_excel
    export_beats_to_excel([], path, registry=registry)
    return path


def demo_files(demo_root) -> list:
    """`demo/` 里能随包发的文件：`[(源文件, 相对 demo 的路径)]`。"""
    demo_root = Path(demo_root)
    out = []
    if not demo_root.is_dir():
        return out
    for path in sorted(demo_root.rglob('*')):
        if not path.is_file():
            continue
        rel = path.relative_to(demo_root)
        parts = rel.parts[:-1]
        if any(part in DEMO_SKIP_DIRS for part in parts):
            continue
        # 前缀匹配（`build` → 也挡 `build_out` / `build_renpy` / `build_unity`）
        if any(part.startswith(DEMO_SKIP_PREFIXES) for part in parts):
            continue
        if path.name in DEMO_SKIP_FILES:
            continue
        out.append((path, rel.as_posix()))
    return out


def collect_extras(project_root=ROOT, workdir=None, *, include_demo=True) -> list:
    """
    发布包里的**非 exe** 部分：`[(源文件, 包内相对路径)]`。

    缺任何一项直接 `FileNotFoundError`：宁可构建失败，也不要发一个
    "打开没 demo、从零没模板"的包（上一版就是这样）。

    `include_demo=False` 是**程序版**：不收集 `demo/`（示例不要了）。
    ⚠ 这时也不再检查 demo 是否存在 —— "不要"和"缺了"是两件事，
    后者该报错、前者不该。
    """
    root = Path(project_root)
    work = Path(workdir) if workdir else (root / 'dist' / '_extras')
    extras = []

    extras.append((write_template(work / '剧本空表格.xlsx'), '剧本空表格.xlsx'))

    for src_rel, dest in MANUALS:
        path = root / src_rel
        if not path.is_file():
            raise FileNotFoundError(f'缺少说明文档：{path}')
        extras.append((path, dest))

    img_dir = root / MANUAL_IMG_DIR
    shots = sorted(img_dir.glob('*.png')) if img_dir.is_dir() else []
    if not shots:
        raise FileNotFoundError(
            f'缺少手册截图：{img_dir}（跑 python tools/_shot_manual.py 生成）')
    extras.extend((p, f'{MANUAL_IMG_DEST}/{p.name}') for p in shots)

    license_file = root / 'LICENSE'
    if not license_file.is_file():
        raise FileNotFoundError(f'缺少 LICENSE：{license_file}')
    extras.append((license_file, 'LICENSE'))

    if not include_demo:
        return extras

    demo = demo_files(root / 'demo')
    if not demo:
        raise FileNotFoundError(f'demo/ 里没有可发的文件：{root / "demo"}')
    extras.extend((src, 'demo/' + rel) for src, rel in demo)
    return extras


def stage(dist_dir, out_dir, extras) -> Path:
    """把 exe 目录 + extras 铺到一个干净的目录里（out 存在就整体重建）。"""
    dist, out = Path(dist_dir), Path(out_dir)
    if not dist.is_dir():
        raise FileNotFoundError(
            f'找不到 PyInstaller 产物目录：{dist}\n'
            f'先跑 packaging\\build_exe.ps1（或手动 PyInstaller），再来组装。')
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(dist, out)
    for src, rel in extras:
        dest = out / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
    return out


def check_manifest(staging, required=REQUIRED) -> list:
    """
    收尾自检：返回缺失项（空列表 = 齐了）。

    `required` 可换成 `REQUIRED_BASE`（程序版 / 演出专用版：不带 demo）。
    """
    staging = Path(staging)
    missing = []
    for rel in required:
        if (staging / rel).is_file():
            continue
        # PyInstaller 的产物名可能带版本后缀；exe 单独放宽一点
        if rel.endswith('.exe') and list(staging.glob(rel.replace('.exe', '*.exe'))):
            continue
        missing.append(rel)
    return missing


def check_forbidden(staging, *, allow_demo=True) -> list:
    """
    收尾自检的**另一半**：包里"不该有"的东西（返回多余项，空列表 = 干净）。

    `allow_demo=False`（程序版）时，`demo/` 本身也是"不该有"的 ——
    用户明确说了示例不要了，别让它从别的路径再混回来。

    与 `check_manifest` 互补 —— 那个防"缺"，这个防"多"。为什么必须有：
    2026-10-28 实测，`demo/` 下混进了 8 个构建产物（`build_out/`
    `build_renpy/` `build_unity/`）和 2 个开发者脚本（`make_art.py`
    `make_demo.py`）。它们**一个都不影响 exe 运行**，所以
    "双击能开 + selfcheck 通过"这条判据永远发现不了 ——
    只有"点开 zip 看里面有什么"才发现。这一条把它变成自动判据。

    只看 `demo/` 与 `__pycache__`：`_internal/` 里那些 `.pyd` / `.dll` /
    `.qm` 是 PyInstaller 的正当产物，不能一刀切。
    """
    staging = Path(staging)
    hits = []
    if not allow_demo and (staging / 'demo').exists():
        # 只记一条：这个包的约定是"没有 demo 这棵树"，
        # 里面有什么都不重要，整棵都不该在。
        hits.append('demo')
    for path in staging.rglob('*'):
        rel = path.relative_to(staging)
        parts = rel.parts
        # 整棵树都不该在的目录（demo 的产物 / 缓存）
        if any(p in FORBIDDEN_DEMO_DIRS for p in parts):
            hits.append(rel.as_posix())
            continue
        if parts[0] == 'demo':
            inner_dirs = parts[1:-1]
            if any(p.startswith(FORBIDDEN_DEMO_DIR_PREFIXES) for p in inner_dirs):
                hits.append(rel.as_posix())
                continue
            if path.is_file() and path.suffix.lower() in FORBIDDEN_DEMO_SUFFIXES:
                hits.append(rel.as_posix())
                continue
        if path.is_file() and path.suffix.lower() == '.pyc':
            hits.append(rel.as_posix())
    return sorted(set(hits))


def make_zip(staging, zip_path, root_name=None) -> Path:
    """
    把 staging 目录压成 zip；条目一律以 `root_name/` 开头（解压不撒一地）。

    `root_name` 默认 `StoryGraph` —— **不是** staging 目录名：staging 常叫
    `dist/release`，直接拿它当包名，用户解压出来就是一个叫 `release` 的文件夹。
    """
    staging, zip_path = Path(staging), Path(zip_path)
    root_name = str(root_name or 'StoryGraph')
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as z:
        for path in sorted(staging.rglob('*')):
            if path.is_file():
                rel = path.relative_to(staging).as_posix()
                z.write(path, f'{root_name}/{rel}')
    return zip_path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='组装发布包（exe + 模板 + 说明 + LICENSE；demo 可选）')
    ap.add_argument('--project-root', default=str(ROOT))
    ap.add_argument('--dist', default='dist/StoryGraph')
    ap.add_argument('--out', default='dist/release')
    ap.add_argument('--zip', default='dist/StoryGraph-1.0.0-win64.zip')
    ap.add_argument('--root-name', default='StoryGraph',
                    help='zip 里那层目录名（解压出来看到的名字）')
    ap.add_argument('--stage-only', action='store_true', help='只铺目录，不压 zip')
    ap.add_argument('--no-demo', action='store_true',
                    help='程序版：不发示例工程 demo/（"完全初始化"的干净包）')
    args = ap.parse_args(argv)

    root = Path(args.project_root)
    dist = (root / args.dist) if not Path(args.dist).is_absolute() else Path(args.dist)
    out = (root / args.out) if not Path(args.out).is_absolute() else Path(args.out)
    zip_path = (root / args.zip) if not Path(args.zip).is_absolute() else Path(args.zip)

    include_demo = not args.no_demo
    required = REQUIRED if include_demo else REQUIRED_BASE

    extras = collect_extras(root, workdir=out.parent / '_extras',
                            include_demo=include_demo)
    staged = stage(dist, out, extras)
    missing = check_manifest(staged, required)
    if missing:
        print('发布包缺东西，拒绝继续：\n  ' + '\n  '.join(missing), file=sys.stderr)
        return 1
    extra_junk = check_forbidden(staged, allow_demo=include_demo)
    if extra_junk:
        print('发布包里有多余的东西（不该发出去的），拒绝继续：\n  '
              + '\n  '.join(extra_junk), file=sys.stderr)
        return 1

    count_xlsx = len(list(staged.rglob('*.xlsx')))
    count_md = len(list(staged.rglob('*.md')))
    print(f'目录：{staged}')
    print(f'  xlsx {count_xlsx} 个 · md {count_md} 个 · '
          f'文件 {sum(1 for _ in staged.rglob("*") if _.is_file())} 个 · '
          f'demo {"有" if include_demo else "无（程序版）"}')
    if args.stage_only:
        print('（--stage-only：没有压 zip）')
        return 0
    make_zip(staged, zip_path, root_name=args.root_name)
    size = zip_path.stat().st_size / (1024 * 1024)
    print(f'zip：{zip_path}（{size:.1f} MB）')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
