# -*- coding: utf-8 -*-
"""临时探针：尺寸面板的匹配 / 行构造在真机 transform 上对不对。"""
from storygraph.story.atl import parse_transforms
from storygraph.story import size_presets as SP

TEXT = '''
transform cg38402160:
    zoom 0.5
    anchor (0,0)
transform bgfit:
    fit "cover"
transform bgnearfit:
    fit "cover"
    nearest 1
transform focus(x=640, z=0.30):
    xcenter x xoffset 400 yoffset 1200 yanchor 1.0 ypos 1.03 zoom z*1.00
    on show:
        zoom z*0.95 alpha 0.00
        easein .25 yoffset 1200 zoom z*1 alpha 1.00
transform half_only:
    zoom 0.5
transform contain:
    fit "contain"
'''
index = parse_transforms(TEXT)
screen = (1920, 1080)
for natural in ((3840, 2160), (2304, 1296), (1000, 1000), (1920, 1080)):
    print('== natural', natural)
    for key in ('cover', 'contain', 'half'):
        print('  ', key, 'target', SP.target_box(key, natural, screen),
              '->', SP.match_preset(key, natural, screen, index))
    print('   custom 0.5 ->', SP.match_preset('custom', natural, screen, index,
                                              zoom=0.5))
    print('   custom 0.75 ->', SP.match_preset('custom', natural, screen, index,
                                               zoom=0.75))
print('options:', [(o['name'], o['brief'], o['changes_size'])
                   for o in SP.transform_options(index, (3840, 2160), screen)])
