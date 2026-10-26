"""P1 讨论·实现派探针①d：extras 形态的**真实生成行形状**（铁律 10：写 .py 不用 heredoc）。

同时验证 `_squash('set seen')` 是否真在文件 squash 集合里（上个调试结果可疑）。
跑法： /d/Python/python tools/_p1debate_impl_extras_shape.py
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

EXTRAS = ('label start:\n'
          '    menu:\n'
          '        set seen\n'
          '        "开始":\n'
          '            e "a"\n'
          '    return\n')


def main():
    from storygraph.story import patcher as P
    tmp = Path(tempfile.mkdtemp(prefix='p1ex_'))
    try:
        game = tmp / 'game'
        game.mkdir()
        p = game / 'script.rpy'
        p.write_bytes(EXTRAS.encode('utf-8'))
        from storygraph.app.editor import EditorSession
        s = EditorSession(project_root=tmp)
        s.load(p)
        f = s.fields(0)
        f.update({'Text': '新标题'})
        s.apply(0, f, cards=s.cards(0), advanced_open=True,
                stage_loaded=f.get('Stage'))
        for i, b in enumerate(s.beats):
            print(f'beat[{i}] kind={getattr(b, "kind", "?")} '
                  f'src={b.src_first}-{b.src_last} text={b.text!r} '
                  f'raw={getattr(b, "raw", "")!r}')
        orig_lines = p.read_text(encoding='utf-8').splitlines()
        blocks, raw_gen = P._render_blocks(s.beats, None, orig_lines)
        for i, first, last, gen, b in blocks:
            print(f'  gen[{i}] src={first}-{last}: {gen}')
        sq = set(P._squash(ln) for ln in orig_lines if P._squash(ln))
        print('file squash set:', sorted(sq))
        print('_squash("set seen") =', repr(P._squash('set seen')),
              'in set:', P._squash('set seen') in sq)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    main()
