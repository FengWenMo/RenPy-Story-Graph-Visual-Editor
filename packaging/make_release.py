"""
打包发布物：exe 目录 + 剧本空表格 + demo 工程 + 3 分钟上手 + LICENSE
====================================================================

`packaging/build_exe.ps1` 的第 5 步调它。也可以单独跑（用已有的 `dist\\StoryGraph\\`）：

    python packaging/make_release.py
    python packaging/make_release.py --stage-only        # 只铺目录，不 zip
    python packaging/make_release.py --dist dist\\StoryGraph --out dist\\release

为什么要一个脚本（2026-10-23，竞争分析 P0-1）：
    上一版 zip（`StoryGraph-1.0.0-win64-2026-10-21.zip`）实测
    **xlsx = 0 个、md = 0 个、demo = 0 个** —— 双击能开，但"打开项目"没东西可开、
    想从零开始没有模板、出问题没有一页说明。竞品随包附空表格；我们连"装完干嘛"
    都没有。这个脚本把"该塞什么"写成清单，少一样就直接报错（不默默发出去）。

清单（少一项就失败，别改成"警告"）：

  · `StoryGraph.exe` / `StoryGraph-CLI.exe`（PyInstaller 产物，**必须**在 dist 里）
  · `剧本空表格.xlsx` —— 空 Beats 表 + 「列说明」页
  · `demo\\` —— 能双击打开的示例工程（**不带** collab.db / _import_tmp / build/）
  · `快速上手.md` —— 1 页速查（docs/快速上手_3分钟.md 的发布版）
  · `新手快速操作手册.md` + `manual_img\\` —— 完整新手手册与它的两张截图
  · `LICENSE` —— GPLv3（第三方组件说明在文末）
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
DEMO_SKIP_DIRS = {'build', '__pycache__', '.pytest_cache', 'saves', 'cache',
                  '.git'}
#: demo 里**不发**的文件（协作库是用户本机数据；`_import_tmp` 是中间产物）
DEMO_SKIP_FILES = {'collab.db', '_import_tmp.xlsx', '.DS_Store', 'Thumbs.db'}

#: 发布包必须有的东西（用于收尾自检）
REQUIRED = ('StoryGraph.exe', 'StoryGraph-CLI.exe',
            '剧本空表格.xlsx', '快速上手.md', '新手快速操作手册.md',
            'manual_img/01_structure.png', 'manual_img/02_card.png',
            'LICENSE',
            'demo/剧本表_ch01.xlsx', 'demo/game/script/ch01.rpy',
            'demo/game/images/bg/bg_classroom.png')


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
        if any(part in DEMO_SKIP_DIRS for part in rel.parts[:-1]):
            continue
        if path.name in DEMO_SKIP_FILES:
            continue
        out.append((path, rel.as_posix()))
    return out


def collect_extras(project_root=ROOT, workdir=None) -> list:
    """
    发布包里的**非 exe** 部分：`[(源文件, 包内相对路径)]`。

    缺任何一项直接 `FileNotFoundError`：宁可构建失败，也不要发一个
    "打开没 demo、从零没模板"的包（上一版就是这样）。
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


def check_manifest(staging) -> list:
    """收尾自检：返回缺失项（空列表 = 齐了）。"""
    staging = Path(staging)
    missing = []
    for rel in REQUIRED:
        if (staging / rel).is_file():
            continue
        # PyInstaller 的产物名可能带版本后缀；exe 单独放宽一点
        if rel.endswith('.exe') and list(staging.glob(rel.replace('.exe', '*.exe'))):
            continue
        missing.append(rel)
    return missing


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
    ap = argparse.ArgumentParser(description='组装发布包（exe + 模板 + demo + 说明 + LICENSE）')
    ap.add_argument('--project-root', default=str(ROOT))
    ap.add_argument('--dist', default='dist/StoryGraph')
    ap.add_argument('--out', default='dist/release')
    ap.add_argument('--zip', default='dist/StoryGraph-1.0.0-win64.zip')
    ap.add_argument('--root-name', default='StoryGraph',
                    help='zip 里那层目录名（解压出来看到的名字）')
    ap.add_argument('--stage-only', action='store_true', help='只铺目录，不压 zip')
    args = ap.parse_args(argv)

    root = Path(args.project_root)
    dist = (root / args.dist) if not Path(args.dist).is_absolute() else Path(args.dist)
    out = (root / args.out) if not Path(args.out).is_absolute() else Path(args.out)
    zip_path = (root / args.zip) if not Path(args.zip).is_absolute() else Path(args.zip)

    extras = collect_extras(root, workdir=out.parent / '_extras')
    staged = stage(dist, out, extras)
    missing = check_manifest(staged)
    if missing:
        print('发布包缺东西，拒绝继续：\n  ' + '\n  '.join(missing), file=sys.stderr)
        return 1

    count_xlsx = len(list(staged.rglob('*.xlsx')))
    count_md = len(list(staged.rglob('*.md')))
    print(f'目录：{staged}')
    print(f'  xlsx {count_xlsx} 个 · md {count_md} 个 · '
          f'文件 {sum(1 for _ in staged.rglob("*") if _.is_file())} 个')
    if args.stage_only:
        print('（--stage-only：没有压 zip）')
        return 0
    make_zip(staged, zip_path, root_name=args.root_name)
    size = zip_path.stat().st_size / (1024 * 1024)
    print(f'zip：{zip_path}（{size:.1f} MB）')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
