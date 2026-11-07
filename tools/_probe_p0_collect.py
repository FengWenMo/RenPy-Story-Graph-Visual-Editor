# -*- coding: utf-8 -*-
"""
P0 验收：真跑一次采集，看 v2 那四段**真的写出来了**吗。

用一个小工程（一个 label + 一张图），`mode='copy'` —— **不碰真项目**。
"""
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, r'D:\千连万理\Renpy_Story_Graph_Editor')
from storygraph.core import engine_facts as EF                  # noqa: E402
from storygraph.engine import decide                            # noqa: E402
from storygraph.pipeline import renpy_adapter as RA             # noqa: E402

SDK = r'D:\software\renpy-8.4.1-sdk'
PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 64

SCRIPT = """\
layeredimage lan:
    image_format 'images/characters/lan/{image}.png'
    attribute happy default:
        'happy'

label start:
    show lan happy
    "hello"
    return
"""


def main() -> int:
    td = Path(tempfile.mkdtemp(prefix='sg_p0_'))
    try:
        root = td / 'tiny'
        g = root / 'game'
        (g / 'images' / 'characters' / 'lan').mkdir(parents=True)
        (g / 'images' / 'characters' / 'lan' / 'happy.png').write_bytes(PNG)
        (g / 'script.rpy').write_text(SCRIPT, encoding='utf-8')

        t0 = time.time()
        got = RA.collect(str(root), SDK, mode='copy', timeout=600)
        print(f'采集 {time.time() - t0:.1f}s ok={got["ok"]} '
              f'reason={got["reason"][:60]!r}')
        facts = got.get('facts') or {}

        print('\n--- v2 四段 ---')
        print('schema     :', facts.get('schema'))
        print('adapter    :', facts.get('adapter'))
        caps = facts.get('capabilities') or {}
        for k, v in caps.items():
            print(f'  {k:26} {v}')
        print('degraded   :', facts.get('degraded'))
        print('freshness  :', facts.get('freshness'))

        print('\n--- 校验 ---')
        problems = EF.validate_facts(facts)
        print('validate_facts:', problems or '通过')
        ok, why = EF.is_collectable(facts)
        print('is_collectable:', ok, why[:60])

        print('\n--- 档位 ---')
        name = decide.rung(facts, collectable=ok)
        print('rung:', decide.describe(name, facts))
        print('envelope:', RA.envelope(facts))
        return 0
    finally:
        shutil.rmtree(td, ignore_errors=True)


if __name__ == '__main__':
    raise SystemExit(main())
