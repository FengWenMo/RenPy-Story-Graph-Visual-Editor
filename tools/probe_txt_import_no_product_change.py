"""证明：本轮改动**没有动产物**（与 git HEAD 版逐字节比对真机 5 章）。

做法：把 HEAD 的 `txt_import.py` 取到临时目录，用 importlib 按路径分别加载
新旧两个模块，同一个输入各跑一次比 sha256。
"""

from __future__ import annotations

import glob
import hashlib
import importlib.util
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.authoring.txt_import import CharacterMap, convert_file  # noqa: E402
from storygraph.pipeline.build import load_asset_registry                # noqa: E402

TXT_DIR = Path(r'D:\千连万理\txt--renpy脚本转换工具')
REAL = Path(r'D:\千连万理\RenpyTest\sr_general')


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    old_src = subprocess.run(
        ['git', 'show', 'HEAD:storygraph/authoring/txt_import.py'],
        cwd=ROOT, capture_output=True, check=True).stdout.decode('utf-8')
    with tempfile.TemporaryDirectory(prefix='probe_old_') as td:
        old_path = Path(td) / 'old_txt_import.py'
        old_path.write_text(old_src, encoding='utf-8')
        old = load_module(old_path, 'old_txt_import')

        rows = (load_asset_registry(REAL) or {}).characters if REAL.is_dir() else {}
        # ⚠ 角色表 dict 各自建 CharacterMap：新旧两个模块的 CharacterMap 是**两个类**，
        # 交叉传进去会被 isinstance 判成 dict（这里两边的角色表内容完全一样）。
        bad = 0
        for f in sorted(glob.glob(str(TXT_DIR / '*-定稿.txt'))):
            a = convert_file(f, characters=rows).rpy_text
            b = old.convert_file(f, characters=rows).rpy_text
            same = a == b
            bad += 0 if same else 1
            print(f'  {Path(f).name:34s} {"一致" if same else "**不一致**"}  '
                  f'行 {len(a.splitlines())}  sha '
                  f'{hashlib.sha256(a.encode()).hexdigest()[:12]}')
        # 顺带：合成用例（大小写撞车那类）本来就应当**不同** —— 那是修好的缺陷
        synth = 'ljk"我是李佳凯"\nLjk"我是陆嘉珂"\nMOM"妈妈"\nOK"好的"'
        srows = {'ljk': {'ID': 'ljk', 'Name': '「李佳凯」'},
                 'Ljk': {'ID': 'Ljk', 'Name': '「陆嘉珂」'}}
        sa = old.convert_text(synth, characters=srows).rpy_text
        from storygraph.authoring.txt_import import convert_text as new_convert
        sb = new_convert(synth, characters=srows).rpy_text
        print('\n  合成用例（应当不同 —— 这就是本轮修的缺陷）：')
        for tag, t in (('旧', sa), ('新', sb)):
            print(f'    [{tag}] ' + ' | '.join(
                x.strip() for x in t.splitlines()
                if x.strip() and not x.startswith('label')))
        print(f'\n  真机 5 章产物差异：{bad} 个文件')
        return 1 if bad else 0


if __name__ == '__main__':
    raise SystemExit(main())
