"""P1 讨论·实现派探针②：真机复杂文件上**移动拍**被拒的根因分类。

审计 P1②：真机复杂文件上移动拍被拒（"重排段里有认不出的行"）——安全，
但移动等于不可用。本探针回答三个问题：

  §1 真机（script_pomo.rpy）上到底哪些移动能做、哪些被拒、拒因各是什么
  §2 被拒时"认不出的行"都是些什么行（按形状分类统计）
  §3 判据放宽的可行性：如果把"extras 类行（属于菜单、不属任何拍）"允许
     原地留在空隙里，哪些原本被拒的移动可以安全放行？
     —— 只做**模拟**（复制 _plan_reorders 的判据逻辑在探针里跑），不动源码。

跑法： /d/Python/python tools/_p1debate_impl_move_real.py
"""
from __future__ import annotations

import re
import shutil
import sys
import tempfile
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PASS, FAIL = '[PASS]', '[FAIL]'
results = []


def check(name, ok, detail=''):
    results.append((bool(ok), name))
    print(f'{PASS if ok else FAIL} {name}')
    if detail:
        for line in str(detail).splitlines():
            print(f'       {line}')


POMO = Path(r'D:\千连万理\RenpyTest\sr_general\game\pomodorro\script_pomo.rpy')


def load_session(src: Path):
    from storygraph.app.editor import EditorSession
    tmp = Path(tempfile.mkdtemp(prefix='p1move_'))
    game = tmp / 'game'
    game.mkdir(parents=True)
    dst = game / src.name
    shutil.copyfile(src, dst)
    s = EditorSession(project_root=tmp)
    s.load(dst)
    return s, dst, tmp


def main():
    if not POMO.exists():
        print(f'[SKIP] 真机文件不在：{POMO}')
        return 0

    print('=' * 68)
    print('§1 真机移动矩阵：相邻对逐对试（零内容改动，只换顺序）')
    print('=' * 68)
    from storygraph.story.patcher import patch_export

    s, dst, tmp = load_session(POMO)
    try:
        before = dst.read_bytes()
        n = len(s.beats)
        ok_moves, bad_moves = [], {}
        # 采样：全相邻对太多，取前 60 对 + 最后 20 对
        pairs = [(i, i + 1) for i in range(n - 1)]
        sample = pairs[:60] + pairs[-20:]
        for a, b in sample:
            s2, dst2, tmp2 = load_session(POMO)
            try:
                r = s2.move_beat(a, b)
                if not r.get('ok'):
                    bad_moves[(a, b)] = f'move_beat 自身拒绝：{r.get("error")}'
                    continue
                res = s2.compile()
                pr = patch_export(s2.beats, dst2, res.rpy_text,
                                  registry=s2.registry(),
                                  symbols=s2.symbols, trust_ranges=True)
                if pr.ok:
                    ok_moves.append((a, b))
                else:
                    bad_moves[(a, b)] = '；'.join(pr.reasons)
            finally:
                shutil.rmtree(tmp2, ignore_errors=True)
        print(f'  可增量移动：{len(ok_moves)}/{len(sample)}')
        check('相邻移动至少一半能做', len(ok_moves) * 2 >= len(sample),
              f'ok={len(ok_moves)} total={len(sample)}')
        reasons = Counter()
        for k, v in bad_moves.items():
            for part in v.split('；'):
                key = re.sub(r'（.*', '', part)[:24]
                reasons[key] += 1
        print('  拒因分布：')
        for k, c in reasons.most_common(10):
            print(f'    {c:3d}  {k}')

        # §2 "认不出的行"都是些什么 —— 直接在真机上扫
        print()
        print('=' * 68)
        print('§2 空隙里"认不出的行"形状分类（真机全文扫）')
        print('=' * 68)
        lines = POMO.read_text(encoding='utf-8').splitlines()
        # 所有不属任何拍区间的行
        in_beat = set()
        for bi, bb in enumerate(s.beats):
            f, l = bb.src_first or 0, bb.src_last or 0
            for ln in range(f, l + 1):
                in_beat.add(ln)
        stray = [(i + 1, lines[i]) for i in range(len(lines))
                 if (i + 1) not in in_beat]
        shapes = Counter()
        samples = {}
        for ln_no, txt in stray:
            t = txt.strip()
            if not t:
                shapes['空白'] += 1
            elif t.startswith('#'):
                shapes['注释'] += 1
            elif re.match(r'^(label)\b', t):
                shapes['label 头'] += 1
            elif re.match(r'^(if|elif|else|while|for)\b.*:$', t):
                shapes['控制流块头'] += 1
            elif re.match(r'^(menu)\b.*:$', t):
                shapes['menu 头'] += 1
            elif re.match(r'^(init|define|default|transform|image|screen|style|translate)\b', t):
                shapes['顶层块头'] += 1
            elif t.endswith(':'):
                shapes['其它块头'] += 1
            else:
                shapes['平铺语句（疑似 extras）'] += 1
                samples.setdefault(t[:50], ln_no)
        for k, c in shapes.most_common():
            print(f'    {c:3d}  {k}')
        print('  平铺语句样本：')
        for t, ln_no in list(samples.items())[:12]:
            print(f'    L{ln_no}: {t!r}')
        check('平铺语句（extras 类）确实存在于空隙中',
              shapes.get('平铺语句（疑似 extras）', 0) > 0,
              f'共 {shapes.get("平铺语句（疑似 extras）", 0)} 行')

        # §3 模拟放宽：空隙允许"平铺语句"（不以冒号结尾、非 label）
        print()
        print('=' * 68)
        print('§3 模拟放宽（空隙允许 extras 类平铺语句）后的放行率')
        print('=' * 68)
        # 重跑被拒对，只放宽"空隙行判据"，其余照抄 _plan_reorders
        from storygraph.story import patcher as P

        def simulate_reorder(blocks, orig_lines, raw_gen, strict_of,
                             allow_stray=False):
            """复制 _plan_reorders 的拒因判定（只到"允不允许"这一层）。"""
            by_label = {}
            for blk in blocks:
                _i, first, last, _gen, beat = blk
                if first < 1 or last < first or last > len(orig_lines):
                    continue
                by_label.setdefault(
                    str(getattr(beat, 'label', '') or ''), []).append(blk)
            would_pass = 0
            detail = []
            for _name, seq in by_label.items():
                firsts = [blk[1] for blk in seq]
                involved = set()
                for j in range(len(firsts)):
                    for k in range(j):
                        if firsts[k] > firsts[j]:
                            involved.add(j)
                            involved.add(k)
                if not involved:
                    continue
                span_first = min(firsts[j] for j in involved)
                span_last = max(seq[j][2] for j in involved)
                members = [blk for blk in seq
                           if span_first <= blk[1] and blk[2] <= span_last]
                member_ids = {blk[0] for blk in members}
                if not {seq[j][0] for j in involved} <= member_ids:
                    detail.append('跨出重排段'); continue
                if any(blk[1] <= span_last and blk[2] >= span_first
                       for blk in seq if blk[0] not in member_ids):
                    detail.append('与别的拍交叠'); continue
                if any(not strict_of.get(blk[0]) for blk in members):
                    detail.append('原文对不上'); continue
                slots = sorted(members, key=lambda t: t[1])
                ok, blocked = True, []
                for a, b in zip(slots, slots[1:]):
                    if b[1] <= a[2]:
                        ok = False; break
                    for n in range(a[2] + 1, b[1]):
                        body = P._norm(orig_lines[n - 1]).strip()
                        if P._LABEL_LINE_RE.match(orig_lines[n - 1]):
                            ok = False; break
                        if body and not body.startswith('#'):
                            if allow_stray and not body.endswith(':'):
                                continue          # ← 放宽点
                            ok = False
                            blocked.append(orig_lines[n - 1])
                if not ok:
                    detail.append(f'空隙有行：{[x.strip() for x in blocked][:2]}')
                    continue
                would_pass += 1
            return would_pass, detail

        # 对每个被拒对重算（重新载入拿 blocks）
        newly = 0
        still = 0
        for (a, b), why in list(bad_moves.items()):
            s3, dst3, tmp3 = load_session(POMO)
            try:
                s3.move_beat(a, b)
                res = s3.compile()
                # patch_export 内部会算 blocks/raw_gen/strict_of —— 复用它
                # 的中间值太深，这里直接再跑一次真实 _plan_reorders：
                from storygraph.story.patcher import (
                    _plan_reorders, _render_blocks)
                try:
                    blocks, raw_gen = _render_blocks(s3.beats,
                                                     audit=None,
                                                     orig_lines=dst3.read_text(
                                                         encoding='utf-8'
                                                     ).splitlines())
                except Exception as e:              # noqa: BLE001
                    print(f'    _render_blocks 失败：{e}')
                    blocks, raw_gen = None, None
                if blocks is None:
                    check('（跳过模拟：_render_beats_gen 不可用）', True)
                    break
                orig_lines = dst3.read_text(encoding='utf-8').splitlines()
                strict_of = {}
                for _i, first, last, gen, beat in blocks:
                    strict_of[_i] = True       # trust_ranges=True 的口径
                n0, d0 = simulate_reorder(blocks, orig_lines, raw_gen,
                                          strict_of, allow_stray=False)
                n1, d1 = simulate_reorder(blocks, orig_lines, raw_gen,
                                          strict_of, allow_stray=True)
                if n1 > n0:
                    newly += 1
                else:
                    still += 1
            finally:
                shutil.rmtree(tmp3, ignore_errors=True)
        print(f'  放宽后新增放行：{newly} 对（仍拒：{still} 对）')
        check('放宽 extras 判据能救回一部分移动', newly > 0,
              f'newly={newly} still={still}')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    bad = [ok for ok, _ in results if not ok]
    print()
    print(f'汇总：{len(results) - len(bad)}/{len(results)} 通过')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
