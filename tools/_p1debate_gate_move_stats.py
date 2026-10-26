# -*- coding: utf-8 -*-
"""
P1 讨论探针（把关派）· ② 补充：相邻移动 vs 长距移动的成功率
============================================================
审计说"真机复杂文件上移动拍被拒——移动等于不可用"。本探针量出
"到底哪些移动不可用"：按距离分档统计，给 ② 的判词当证据。

跑法：/d/Python/python tools/_p1debate_gate_move_stats.py
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.app.editor import EditorSession          # noqa: E402
from storygraph.story.patcher import patch_export        # noqa: E402

POMO = Path(r'D:\千连万理\RenPyTest\sr_general\game\pomodorro\script_pomo.rpy')


def _patched(s, path):
    res = s.compile()
    return patch_export(s.beats, path, res.rpy_text,
                        registry=s.registry(), symbols=s.symbols,
                        trust_ranges=True)


def main():
    if not POMO.exists():
        print('[SKIP] 真机项目不在')
        return
    tmp = Path(tempfile.mkdtemp(prefix='p1gate_mstat_'))
    game = tmp / 'game'
    game.mkdir()
    dst = game / POMO.name
    shutil.copyfile(POMO, dst)

    by_dist: dict = {}          # 距离 → {'ok': n, 'rej': n, 'rej_why': {...}}
    for src_i in range(55 - 1):
        for dist in (1, 2, 3, 5):
            dst_i = src_i + dist
            if dst_i >= 55:
                continue
            s = EditorSession(project_root=tmp)
            s.load(dst)
            mv = s.move_beat(src_i, dst_i)
            rec = by_dist.setdefault(dist, {'ok': 0, 'rej': 0, 'why': {}})
            if not mv.get('ok'):
                rec['rej'] += 1
                k = '结构层拦截(块头/拆块)'
                rec['why'][k] = rec['why'].get(k, 0) + 1
                continue
            p = _patched(s, dst)
            if p.ok:
                rec['ok'] += 1
            else:
                rec['rej'] += 1
                rs = ' | '.join(str(r)[:40] for r in p.reasons)
                key = (rs.split('（')[0][:28] or '(空)')
                rec['why'][key] = rec['why'].get(key, 0) + 1
    print('真机 script_pomo.rpy（55 拍）按移动距离统计：')
    for dist in sorted(by_dist):
        rec = by_dist[dist]
        total = rec['ok'] + rec['rej']
        rate = rec['ok'] / total * 100 if total else 0
        print(f'  距离 {dist}: 成功 {rec["ok"]}/{total} ({rate:.0f}%)')
        for k, v in sorted(rec['why'].items(), key=lambda kv: -kv[1]):
            print(f'        [{v:3d}] {k}')


if __name__ == '__main__':
    main()
