# -*- coding: utf-8 -*-
"""
探针：Ren'Py 引擎对 `show lan aa happy`（aa 未声明）到底报不报？

造一个最小项目跑 lint，只看结论。不写任何用户文件。
"""
import shutil
import subprocess
import tempfile
from pathlib import Path

EXE = r'D:\software\renpy-8.4.1-sdk\renpy.exe'

SCRIPT = """\
layeredimage lan:
    image_format 'images/characters/lan/{image}.png'
    attribute happy default:
        'happy'
    attribute close_happy:
        'close_happy'

label start:
    show lan happy
    show lan aa happy
    show lan ba
    return
"""


def run(extra_decl: str = '') -> tuple:
    td = Path(tempfile.mkdtemp(prefix='sg_lint_'))
    try:
        g = td / 'game'
        (g / 'images' / 'characters' / 'lan').mkdir(parents=True)
        for n in ('happy', 'close_happy', 'aa'):
            (g / 'images' / 'characters' / 'lan' / f'{n}.png').write_bytes(
                b'\x89PNG\r\n\x1a\n' + b'\x00' * 64)
        (g / 'script.rpy').write_text(extra_decl + SCRIPT, encoding='utf-8')
        proc = subprocess.run([EXE, str(td), 'lint'], capture_output=True,
                              text=True, encoding='utf-8', errors='replace',
                              timeout=300)
        return proc.returncode, (proc.stdout or '') + (proc.stderr or '')
    finally:
        shutil.rmtree(td, ignore_errors=True)


def show(title: str, code: int, out: str) -> None:
    print('=' * 60)
    print(title, '| 退出码', code)
    hits = [ln.strip() for ln in out.split('\n')
            if any(k in ln for k in ('error', 'Error', 'Could not', 'line ',
                                     'attribute', 'image'))]
    for ln in hits[:12]:
        print('  ', ln[:150])
    if not hits:
        print('   （没有相关报错）')


if __name__ == '__main__':
    print('【A】aa / ba 都没声明（`aa` 的图**存在**）')
    code, out = run()
    show('A', code, out)

    print('\n【B】用 init python 定义 `aa` 作为 dict 键（真机 CC_OUTFIT_*）')
    decl = ("init python:\n"
            "    CC_OUTFIT_LABEL = {'aa': '普通'}\n")
    code2, out2 = run(decl)
    show('B', code2, out2)
