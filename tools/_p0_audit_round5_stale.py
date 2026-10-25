"""P0 审计·第五轮·第三层：**表过期**时会不会静默吃掉程序员的行。

场景（真实协作节奏，50 人团队）：策划导出过表 → 程序员在同一份 .rpy 里
加了 `pause` / `$` / 注释 → 策划再导一次。

判据：程序员的每一行**要么还在，要么被明确报出来**。静默消失 = P0。

跑法： python tools/_p0_audit_round5_stale.py
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable

BASE = '''# 第一章
label ch1:
    jc "第一句"
    jc "第二句"
    jc "第三句"
    return
'''

# 程序员加的行（每条都该有下落）
PROG_LINES = ['    pause 0.5', '    $ flag = True', '    # 程序员备注',
              '    play music "bgm.ogg" fadein 1.0']


def run(args):
    p = subprocess.run([PY, 'cli.py'] + args, cwd=str(ROOT),
                       capture_output=True, text=True, encoding='utf-8',
                       errors='replace')
    return p.returncode, (p.stdout or '') + (p.stderr or '')


def main():
    tmp = Path(tempfile.mkdtemp(prefix='sg_p0_r5s_'))
    ok = True
    try:
        game = tmp / 'game'
        game.mkdir(parents=True)
        ch = game / 'ch1.rpy'
        ch.write_text(BASE, encoding='utf-8')

        rc, out = run(['table', 'rpy2xlsx', str(ch), '--project', str(tmp),
                       '--no-engine-lint'])
        print(f'① rpy2xlsx rc={rc}')
        xlsx = ch.with_suffix('.xlsx')
        if not xlsx.exists():
            print(out[:800])
            return 1

        # ② 程序员在同一份文件里加行（不动任何台词）
        lines = BASE.split('\n')
        i = lines.index('    jc "第二句"') + 1
        for k, add in enumerate(PROG_LINES):
            lines.insert(i + k, add)
        ch.write_text('\n'.join(lines), encoding='utf-8')
        with_prog = ch.read_bytes()

        # ③ 策划再导一次（表还是旧的）
        rc, out = run(['table', 'xlsx2rpy', str(xlsx), '--source', str(ch),
                       '--out', str(ch), '--project', str(tmp),
                       '--no-engine-lint'])
        after = ch.read_bytes()
        print(f'② xlsx2rpy（表过期）rc={rc}')
        tail = [l for l in out.strip().splitlines() if l.strip()][-6:]
        for l in tail:
            print('   ', l)

        lost = [l for l in PROG_LINES
                if l.encode('utf-8') not in after]
        print(f'\n程序员加的 {len(PROG_LINES)} 行，导完后丢了 {len(lost)} 行：')
        for l in lost:
            print('   LOST:', l)
        if not lost:
            print('   （都还在）')
        # 判据（"不许静默丢行"的准确口径）：**每一行要么还在，要么被点名**。
        # 删掉本身是设计内行为（表里没有对应内容），静默才是 P0。
        unnamed = [l.strip() for l in lost
                   if l.strip() not in out and l.strip().split()[0] not in out]
        silent = bool(unnamed)
        print(f'\n静默丢行？{"是 → P0" if silent else "否（都点名了）"}'
              f'（rc={rc}；文件变了={after != with_prog}；'
              f'丢 {len(lost)} 行，其中没点名 {len(unnamed)} 行）')
        for l in unnamed:
            print('   UNNAMED:', l)
        ok = not silent
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
