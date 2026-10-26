"""P1 讨论·实现派探针③：改 if 条件的现状矩阵（铁律 10：写 .py，不用 heredoc ——
上一次 heredoc 里 `\n` 被写成 `/n`，样本整个坏掉，结论全废）。

§1 六种条件改动形态（独立 if / 链头 / elif / 嵌套 / 清空 / 没改）
§2 拒绝时文件字节零变化；放行时**只动那一行**
跑法： /d/Python/python tools/_p1debate_impl_if_condition.py
"""
from __future__ import annotations

import shutil
import sys
import tempfile
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


MARKER = '# 由剧本表自动生成'


def _body(text) -> list:
    out, skip = [], False
    for ln in text.splitlines():
        if ln.startswith(MARKER):
            skip = True
            continue
        if skip and not ln.strip():
            skip = False
            continue
        out.append(ln)
    return out


def probe(name, src, idx, cond, expect_ok, expect_body=None):
    from storygraph.app.editor import EditorSession
    tmp = Path(tempfile.mkdtemp(prefix='p1cond_'))
    try:
        game = tmp / 'game'
        game.mkdir()
        p = game / 'script.rpy'
        p.write_bytes(src.encode('utf-8'))
        s = EditorSession(project_root=tmp)
        s.load(p)
        s.apply(idx, {'condition': cond})
        before = p.read_bytes()
        res = s.export(p)
        after = p.read_bytes()
        ok = bool(res.get('ok'))
        told = ' '.join(str(x) for x in res.get('blocking') or [])
        body = _body(after.decode('utf-8'))
        if expect_ok:
            good = ok and (expect_body is None or body == expect_body)
            if not good:
                detail = (f'ok={ok} 拒因={told[:160]}\n'
                          f'期望={expect_body}\n实际={body}')
            else:
                detail = ''
        else:
            good = (not ok) and before == after
            detail = ('' if good else
                      f'ok={ok}（应拒）字节变化={before != after} 拒因={told[:160]}')
        check(name, good, detail)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


S1 = ('label start:\n'
      '    $ flag = True\n'
      '    if flag:\n'
      '        e "在里面"\n'
      '    return\n')
S1_NEW = ['label start:', '    $ flag = True', '    if not flag:',
          '        e "在里面"', '    return']
CHAIN = ('label start:\n'
         '    if a == 1:\n'
         '        e "甲"\n'
         '    elif a == 2:\n'
         '        e "乙"\n'
         '    else:\n'
         '        e "丙"\n'
         '    return\n')
CHAIN_NEW = ['label start:', '    if a == 9:', '        e "甲"',
             '    elif a == 2:', '        e "乙"', '    else:',
             '        e "丙"', '    return']
NEST = ('label start:\n'
        '    if a:\n'
        '        if b:\n'
        '            e "里面"\n'
        '        e "旁的"\n'
        '    return\n')


def main():
    print('=' * 68)
    print('§1 条件改动矩阵')
    print('=' * 68)
    probe('独立 if 改条件 → 应放行且只动一行', S1, 1, 'not flag',
          True, S1_NEW)
    probe('链头改条件 → 应放行且 elif 行不动', CHAIN, 0, 'a == 9',
          True, CHAIN_NEW)
    probe('elif 改条件 → 现状：拒 + 字节零变化', CHAIN, 1, 'a == 9',
          False)
    probe('嵌套 if 改条件 → 现状：拒 + 字节零变化', NEST, 0, 'c',
          False)
    probe('清空条件 → 现状：拒（结构改动）+ 字节零变化', S1, 1, '',
          False)
    probe('条件没改 → 零 diff 放行', S1, 1, 'flag',
          True, S1.splitlines())

    bad = [ok for ok, _ in results if not ok]
    print()
    print(f'汇总：{len(results) - len(bad)}/{len(results)} 通过')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
