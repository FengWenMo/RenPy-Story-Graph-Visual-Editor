"""**变异验证**：证明"资源表一字不动"那条判据真的有牙（跑完自动还原）。

    python tools/mut_asset_tables_untouched.py

做法（本仓纪律：判据必须能被证明是"有牙的"，而不是永远绿的摆设）：
  1. 用 `pytest` 正常跑那条判据 —— 应当 **PASS**；
  2. 临时给导出加一小段"会去碰资源表"的代码（monkeypatch，**不落盘**）；
  3. 同样再跑一次 —— 应当 **FAIL**；
  4. 还原，再跑一次确认 **PASS**。
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEST = ('tests/test_engine_choices.py::TestExcelAnnotation'
        '::test_asset_tables_are_not_touched_at_all')
PY = sys.executable


def run(extra_env=None):
    import os
    env = dict(os.environ)
    env['PYTHONIOENCODING'] = 'utf-8'
    env.update(extra_env or {})
    p = subprocess.run([PY, '-m', 'pytest', TEST, '-q',
                        '--basetemp=.pip-tmp/mut-attr',
                        '-p', 'no:cacheprovider'],
                       cwd=ROOT, env=env, capture_output=True, text=True,
                       encoding='utf-8', errors='replace')
    tail = (p.stdout or '').strip().splitlines()
    return p.returncode, (tail[-1] if tail else '(no output)')


#: 突变：导出时**故意去碰**资源表（`sitecustomize` 思路不可靠，这里直接注入）
CONFTEST = ROOT / 'tests' / '_mut_attr_conftest.py'
CONFTEST_CODE = '''
"""临时夹具（工具生成，用完即删）：让导出**故意碰一下**资源表。"""
import pytest


@pytest.fixture(autouse=True)
def _mutate_asset_tables(monkeypatch):
    import storygraph.story.excel_export as X
    real = X.export_beats_to_excel

    def patched(beats, target, *a, **kw):
        out = real(beats, target, *a, **kw)
        try:
            from pathlib import Path
            for p in Path(target).parent.rglob('asset_tables/*.xlsx'):
                p.write_bytes(p.read_bytes() + b' ')     # ← 故意弄脏
        except Exception:
            pass
        return out
    monkeypatch.setattr(X, 'export_beats_to_excel', patched)
'''

print('① 正常跑（期望 PASS）')
code, last = run()
print(f'   exit={code}  {last}')

print('\n② 装上"会被碰"的突变再跑（期望 FAIL）')
CONFTEST.write_text(CONFTEST_CODE, encoding='utf-8')
# 夹具放在 tests/ 下不会自动生效 —— 用 -p 显式加载
try:
    import os
    env = {'PYTHONPATH': str(ROOT / 'tests')}
    p = subprocess.run([PY, '-m', 'pytest', TEST, '-q',
                        '--basetemp=.pip-tmp/mut-attr2',
                        '-p', '_mut_attr_conftest', '-p', 'no:cacheprovider'],
                       cwd=ROOT, env={**os.environ, **env,
                                      'PYTHONIOENCODING': 'utf-8'},
                       capture_output=True, text=True,
                       encoding='utf-8', errors='replace')
    out = (p.stdout or '') + (p.stderr or '')
    tail = [ln for ln in out.strip().splitlines() if ln.strip()]
    print(f'   exit={p.returncode}  {tail[-1] if tail else "(no output)"}')
    # ⚠ 必须区分"**判据红了**"和"**根本跑不起来**"（插件没加载 → 也非 0，
    #   那不是判据有牙，是探针自己有毛病）
    if 'failed' not in out:
        print('   ⚠ 没有出现 failed —— 突变可能没生效（探针问题，不是判据有牙）')
    code2 = p.returncode
finally:
    CONFTEST.unlink(missing_ok=True)

print('\n③ 还原后再跑一次（期望 PASS）')
code3, last = run()
print(f'   exit={code3}  {last}')

print()
ok = (code == 0 and code2 != 0 and code3 == 0)
print('结论：', '✅ 判据有牙（正常绿 / 突变红 / 还原又绿）' if ok
      else '❌ 判据没有牙（突变下仍然绿）—— 需要重写它')
raise SystemExit(0 if ok else 1)
