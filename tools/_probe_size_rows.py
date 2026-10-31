# -*- coding: utf-8 -*-
"""临时探针：size_rows 在合成帧上的输出。"""
import tempfile
from pathlib import Path

from PIL import Image

from storygraph.app.stage import StageFrame
from storygraph.app.stage_render import size_rows

GUI = '''init python:
    gui.init(400, 300)
'''
TRANSFORMS = '''transform bgfit:
    fit "cover"
transform half:
    zoom 0.5
transform anim:
    zoom 0.5
    on show:
        easein .2 alpha 1.0
'''
SCRIPT = ('''image bg big = "images/big.png"
image bg small = "images/small.png"
image cg cg4k = "images/cg4k.png"
label start:
    return
''')

root = Path(tempfile.mkdtemp())
game = root / 'game'
(game / 'images').mkdir(parents=True)
(game / 'gui.rpy').write_text(GUI, encoding='utf-8')
(game / 'transform.rpy').write_text(TRANSFORMS, encoding='utf-8')
(game / 'script.rpy').write_text(SCRIPT, encoding='utf-8')
Image.new('RGBA', (800, 600), (255, 0, 0, 255)).save(game / 'images' / 'big.png')
Image.new('RGBA', (200, 150), (0, 255, 0, 255)).save(game / 'images' / 'small.png')
Image.new('RGBA', (1600, 1200), (0, 0, 255, 255)).save(
    game / 'images' / 'cg4k.png')

frame = StageFrame(index=1, line_id='t.0002', label='t', speaker='', text='',
                   scene='bg big', scene_at='', layers='cg cg4k',
                   layers_at={'cg': ''}, scene_src=0, layer_src={'cg': 1})
rows = size_rows(frame, project_root=root, screen=(400, 300))
print('rows:', len(rows))
for row in rows:
    print('---', row['index'], row['kind'], row['tag'], row['spec'],
          row['label'], row['at'], row['source'], row['natural'])
    print('   note:', row['note'])
    for it in row['items']:
        print('   item:', it['key'], repr(it['at']), it['ok'],
              it['current'], '|', it['text'], '|', it['why'])
