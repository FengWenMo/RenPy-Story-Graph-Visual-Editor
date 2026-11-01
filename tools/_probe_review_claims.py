"""临时探针：把审查报告里 5.2 / 5.3 / 5.4 的指控逐条复核一遍（真机跑 CLI 主链路）。

用法：python tools/_probe_review_claims.py
产物全在 .pip-tmp/review_probe 下，跑完不动仓库里任何其它东西。
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / '.pip-tmp' / 'review_probe'


def run(args, **kw):
    env = dict(**{'PYTHONIOENCODING': 'utf-8', 'PYTHONPATH': str(ROOT)})
    p = subprocess.run([sys.executable, '-m', 'storygraph.cli', *args],
                       capture_output=True, text=True, encoding='utf-8',
                       errors='replace', cwd=str(ROOT), env={**env},
                       timeout=180, **kw)
    return p.returncode, (p.stdout or '') + (p.stderr or '')


def case_special_chars(name, sample: str):
    """含特殊分隔符的剧本：表 → 回写，看字节有没有被动。"""
    d = WORK / name
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    rpy = d / 'ch.rpy'
    rpy.write_bytes(sample.encode('utf-8'))
    code, out = run(['table', 'rpy2xlsx', str(rpy)])
    xlsx = rpy.with_suffix('.xlsx')
    code2, out2 = run(['table', 'xlsx2rpy', str(xlsx), '--source', str(rpy)])
    after = rpy.read_bytes()
    same = after == sample.encode('utf-8')
    print(f'[{name}] 末产 = {sample!r}')
    print(f'  rpy2xlsx exit={code} → {out.strip().splitlines()[-1] if out.strip() else ""}')
    print(f'  xlsx2rpy exit={code2} → {out2.strip().splitlines()[-1] if out2.strip() else ""}')
    print(f'  逐字节相同：{"是" if same else "**否**"}')
    if not same:
        print(f'  写回后的字节：{after!r}')
    return same


def main() -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    sample_u2028 = (
        'label ch:\n'
        '    "第一句。"\n'
        '    # 备注：这里有个看不见的软回车␨\n'
        '    "第二句。"\n'
        '    return\n'
    ).replace('␨', '\u2028')
    sample_ff = sample_u2028.replace('\u2028', '\x0c')
    ok1 = case_special_chars('u2028', sample_u2028)
    ok2 = case_special_chars('formfeed', sample_ff)

    # —— 不存在 / 目录 / 空文件 ——
    d = WORK / 'bad_inputs'
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    empty = d / 'empty.rpy'
    empty.write_text('', encoding='utf-8')
    (d / 'dir.rpy').mkdir()
    for label, args in (
            ('不存在的 .rpy', ['table', 'rpy2xlsx', str(d / 'nope.rpy')]),
            ('目录当 .rpy', ['table', 'rpy2xlsx', str(d / 'dir.rpy')]),
            ('空 .rpy', ['table', 'rpy2xlsx', str(empty)]),
            ('不存在的项目目录', ['validate', str(d / 'no_project')]),
            ('项目目录没有 game/', ['validate', str(d)]),
    ):
        code, out = run(args)
        tail = [ln for ln in out.strip().splitlines() if ln.strip()][-1] if out.strip() else ''
        print(f'[{label}] exit={code} | {tail}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
