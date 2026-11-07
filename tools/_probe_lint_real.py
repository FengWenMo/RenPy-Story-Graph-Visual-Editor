# -*- coding: utf-8 -*-
"""
探针：镜像真项目（`.rpy` 全文 + 图片**空文件占位**）跑 lint，
问两件事：

  1. 这条路要多久（用户："慢就慢点，要真问"）；
  2. 引擎对真项目的 `show lan aa happy` 到底说什么。

⚠ 只读真项目：镜像在临时目录，**一个字节都不改用户项目**。
"""
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

ROOT = Path(r'D:\千连万理\sr_general')
EXE = r'D:\software\renpy-8.4.1-sdk\renpy.exe'
IMG_EXT = {'.png', '.jpg', '.jpeg', '.webp', '.avif'}


def mirror(root: Path, work: Path) -> dict:
    """`.rpy` 拷全文；图片建**空文件**（引擎只认名字）；其余跳过。"""
    n_rpy = n_img = 0
    for src in root.rglob('*'):
        rel = src.relative_to(root)
        if '.git' in rel.parts or 'build' in rel.parts or 'cache' in rel.parts:
            continue
        if src.is_dir():
            (work / rel).mkdir(parents=True, exist_ok=True)
            continue
        if not src.is_file():
            continue
        dst = work / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        suf = src.suffix.lower()
        if suf == '.rpy':
            shutil.copy2(src, dst)
            n_rpy += 1
        elif suf in IMG_EXT:
            dst.write_bytes(b'')          # 空占位：存在 = 引擎认这个名字
            n_img += 1
    (work / 'game').mkdir(parents=True, exist_ok=True)
    return {'rpy': n_rpy, 'images': n_img}


def main() -> int:
    td = Path(tempfile.mkdtemp(prefix='sg_mirror_'))
    try:
        work = td / ROOT.name
        t0 = time.time()
        stats = mirror(ROOT, work)
        t_mirror = time.time() - t0
        print(f'镜像：{stats}  {t_mirror:.1f}s')

        env = dict(os.environ)
        env['RENPY_SKIP_SPLASHSCREEN'] = '1'
        t0 = time.time()
        proc = subprocess.run([EXE, str(work), 'lint'], capture_output=True,
                              text=True, encoding='utf-8', errors='replace',
                              timeout=1800, env=env)
        t_lint = time.time() - t0
        out = (proc.stdout or '') + (proc.stderr or '')
        print(f'lint：退出码 {proc.returncode}  {t_lint:.1f}s')

        lines = [ln.strip() for ln in out.split('\n')]
        not_image = [ln for ln in lines if 'is not an image' in ln]
        print(f'\n"is not an image" 共 {len(not_image)} 条')
        print('\n含 aa / ba 的:')
        for ln in not_image:
            if ' aa' in ln or ' ba' in ln:
                print('  ', ln[:130])
        print('\n前 8 条:')
        for ln in not_image[:8]:
            print('  ', ln[:130])
        errs = [ln for ln in lines if 'File "game/' in ln]
        print(f'\n其他 File 行 {len(errs)} 条，前 5:')
        for ln in errs[:5]:
            print('  ', ln[:130])
        return 0
    finally:
        shutil.rmtree(td, ignore_errors=True)


if __name__ == '__main__':
    raise SystemExit(main())
