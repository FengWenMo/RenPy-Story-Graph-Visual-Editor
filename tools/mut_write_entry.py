"""突变验证：在**未登记**的函数里加一处裸写盘 → 落盘入口守卫必须变红。"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / 'storygraph' / 'pipeline' / 'renpy_exporter.py'
TEST = ROOT / 'tests' / 'test_single_write_entry.py'
BASE = ROOT / '.pip-tmp' / 'pt-mutwrite'

orig = TARGET.read_text(encoding='utf-8')
marker = "    lines = [\"# 由剧情编辑器自动生成的控制流（请勿手动修改跳转部分）\"]"
assert marker in orig, '找不到插入锚点'
mutated = orig.replace(
    marker,
    marker + "\n    from pathlib import Path as _P\n"
             "    _P(filepath).write_text('x', encoding='utf-8')  # MUTATION",
    1)
assert mutated != orig
TARGET.write_text(mutated, encoding='utf-8')
print('mutated: 在 export_to_renpy 里插入了一处裸写盘')

try:
    p = subprocess.run([sys.executable, '-m', 'pytest', '-q',
                        f'--basetemp={BASE}', str(TEST)],
                       cwd=str(ROOT), capture_output=True, text=True,
                       encoding='utf-8', errors='replace')
    print('突变后：', ' | '.join((p.stdout or '').strip().splitlines()[-2:]))
    print('被抓住（应为 True）:', p.returncode != 0)
finally:
    TARGET.write_text(orig, encoding='utf-8')
    print('restored:', TARGET.read_text(encoding='utf-8') == orig)

p = subprocess.run([sys.executable, '-m', 'pytest', '-q',
                    f'--basetemp={BASE}_ok', str(TEST)],
                   cwd=str(ROOT), capture_output=True, text=True,
                   encoding='utf-8', errors='replace')
print('还原后：', ' | '.join((p.stdout or '').strip().splitlines()[-2:]))
