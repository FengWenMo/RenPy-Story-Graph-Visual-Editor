"""突变验证：把 apply_rename_patch 的"未确认不写"去掉，闸门判据必须变红。

⚠ 用 .py 文件而不是 heredoc —— Git Bash 会把字符串里的 `\n` 改成 `/n`（H3）。
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / 'storygraph' / 'assets' / 'asset_extensions.py'
TEST = ROOT / 'tests' / 'test_hell_rename_confirm_gate.py'
BASE = ROOT / '.pip-tmp' / 'pt-mutgate'

orig = TARGET.read_text(encoding='utf-8')
needle = '    if not confirm:'
assert orig.count(needle) >= 1, f'找不到突变点（{orig.count(needle)} 处）'
mutated = orig.replace(needle, '    if False:  # MUTATION', 1)
assert mutated != orig
TARGET.write_text(mutated, encoding='utf-8')
print(f'mutated: {orig.count(needle)} 处 -> 1 处改成 if False')

try:
    p = subprocess.run([sys.executable, '-m', 'pytest', '-q',
                        f'--basetemp={BASE}', str(TEST)],
                       cwd=str(ROOT), capture_output=True, text=True,
                       encoding='utf-8', errors='replace')
    tail = (p.stdout or '').strip().splitlines()[-3:]
    print('突变后：', ' | '.join(tail))
    print('突变是否被抓住（应为 True）:', p.returncode != 0)
finally:
    TARGET.write_text(orig, encoding='utf-8')
    print('restored:', TARGET.read_text(encoding='utf-8') == orig)

p = subprocess.run([sys.executable, '-m', 'pytest', '-q',
                    f'--basetemp={BASE}_ok', str(TEST)],
                   cwd=str(ROOT), capture_output=True, text=True,
                   encoding='utf-8', errors='replace')
print('还原后：', ' | '.join((p.stdout or '').strip().splitlines()[-2:]))
