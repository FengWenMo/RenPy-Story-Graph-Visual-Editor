"""突变验证：把 `CharacterMap.lookup` 换回**旧实现**（大小写撞车时静默挑一个），
新判据必须变红；换回来必须全绿（J2 变异验证）。

原理：往 pytest 里注入一个**插件**（`-p mut_case` + `PYTHONPATH` 指向它），
插件在收集测试之前把 `CharacterMap.lookup` 换成旧实现。
跑两次（注入 / 不注入）比结果 —— 不改仓库里的任何文件。

    python tools/mut_txt_case_collision.py
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR = ROOT / '.pip-tmp' / 'mutplug'
PLUGIN = PLUGIN_DIR / 'mut_case.py'
TARGET = 'tests/test_txt_import_adversarial.py'

PLUGIN_SRC = '''
"""pytest 插件：把 lookup 换回旧实现（大小写撞车时静默挑一个）。"""
import re

from storygraph.authoring.txt_import import CharacterMap


def _norm_key(name):
    s = re.sub(r"[\\s_]+", "", str(name or ""))
    s = re.sub(r"[「」『』\\"'\\u201c\\u201d]", "", s)
    return s.lower()


def _old_lookup(self, token):
    """旧实现：只有 by_key 一条路 —— 谁先登记，两个人就都归谁。"""
    key = _norm_key(token)
    if not key:
        return None, ""
    var = self.by_key.get(key)
    return (var, self.layer_of.get(key, "")) if var else (None, "")


CharacterMap.lookup = _old_lookup
'''


def _run(label: str, extra: list) -> int:
    env = dict(os.environ)
    if extra:
        env['PYTHONPATH'] = str(PLUGIN_DIR) + os.pathsep + env.get('PYTHONPATH', '')
    r = subprocess.run(
        [sys.executable, '-m', 'pytest', TARGET, '-q', '--basetemp',
         f'.pip-tmp/mut-{label}', '-p', 'no:cacheprovider', *extra],
        cwd=ROOT, capture_output=True, env=env)
    text = (r.stdout or b'').decode('utf-8', 'replace')
    tail = [ln for ln in text.strip().splitlines() if ln.strip()]
    print(f'  [{label}] {tail[-1] if tail else "(无输出)"}')
    return r.returncode


def main() -> int:
    PLUGIN_DIR.mkdir(parents=True, exist_ok=True)
    PLUGIN.write_text(PLUGIN_SRC, encoding='utf-8')

    print('[突变] lookup → 旧实现（大小写撞车时静默挑一个）')
    mutated = _run('mut', ['-p', 'mut_case'])
    print('[对照] 现实现')
    clean = _run('clean', [])

    print(f'\n突变退出码 {mutated}（非 0 = 判据变红）；'
          f'对照退出码 {clean}（0 = 全绿）')
    if mutated == 0:
        print('❌ 突变没被发现 —— 新判据是**假绿**（没盯着这个行为）')
        return 1
    if clean != 0:
        print('❌ 对照也失败 —— 判据或环境本身有问题')
        return 1
    print('✅ 突变验证通过：这些判据真的挡得住"静默合并两个人"')
    return 0


if __name__ == '__main__':
    sys.exit(main())
