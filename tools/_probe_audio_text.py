"""探针 3：多音轨 / 位置参数 / 文本类语句 —— 读成什么、卡片给不给改。"""
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from storygraph.app import beats as B
from storygraph.app.editor import EditorSession
from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
from storygraph.story.beat_build import beats_from_parse_result
from storygraph.story.beat_render import render_beats_to_rpy
from storygraph.story.statement_registry import StatementRegistry, clear_cache

RULES = {
    'statements': [
        # ① 一条"多轨"音效：三个格子，其中 bus 是固定字，另两个是值
        {'name': 'sfx_mix', 'label': '多轨音效',
         'pattern': [
             {'kind': 'keyword', 'text': 'sfx_mix', 'column': '动作名'},
             {'kind': 'choice', 'column': '声道', 'choices': ['se', 'se2', 'amb']},
             {'kind': 'value', 'column': '文件'},
             {'kind': 'optional', 'text': 'gain', 'column': '音量'},
         ]},
        # ② 花字：内容是一个**位置参数**（引号串），样式是具名参数
        {'name': 'styled', 'label': '花字',
         'pattern': [
             {'kind': 'keyword', 'text': 'styled', 'column': '动作名'},
             {'kind': 'value', 'column': '文字'},
             {'kind': 'value', 'column': '颜色'},
             {'kind': 'value', 'column': '字号'},
         ]},
        # ③ 多声部：一行里好几个角色一起说
        {'name': 'chorus', 'label': '合唱',
         'pattern': [
             {'kind': 'keyword', 'text': 'chorus', 'column': '动作名'},
             {'kind': 'choice', 'column': '人物', 'choices': ['lan', 'ling'],
              'multi': True},
             {'kind': 'value', 'column': '内容'},
         ]},
    ]
}

SCRIPT = '''\
label a:
    play music "bgm/a.ogg" fadeout 1.0
    play ambience "amb/wind.ogg"
    play sound "se/boom.ogg"
    stop ambience fadeout 2.0
    sfx_mix se "se/a.ogg" gain=-6
    styled "他说\\"你好\\"并笑了" "#ff0000" 32
    chorus lan ling "一起说"
    voice "v/001.ogg"
    "收尾"
'''


def main():
    tmp = Path(tempfile.mkdtemp(prefix='probe-card3-'))
    try:
        root = tmp / 'proj'
        (root / 'game').mkdir(parents=True)
        (root / 'storygraph.rules.json').write_text(
            json.dumps(RULES, ensure_ascii=False, indent=2), encoding='utf-8')
        src = root / 'game' / 'ch.rpy'
        src.write_text(SCRIPT, encoding='utf-8')
        clear_cache()
        pr = IncrementalRenPyAnalyzer().analyze(files=[src], graph=None)[0]
        beats = beats_from_parse_result(pr)
        print('=== 原始行 ===')
        for ln in src.read_text(encoding='utf-8').splitlines():
            print('   ', ln)
        print(f'\n=== {len(beats)} beats ===')
        reg = StatementRegistry.for_project(root)
        for i, b in enumerate(beats):
            print(f'--- beat {i} kind={b.kind!r}')
            for attr in ('speaker', 'text', 'show', 'scene', 'bgm', 'sfx',
                         'voice', 'music', 'music_params', 'music_action',
                         'sound', 'sound_params', 'sound_action',
                         'spec_head', 'spec_name', 'raw', 'opaque'):
                v = getattr(b, attr, '')
                if v:
                    print(f'    {attr} = {v!r}')
            cols, values = B.spec_arg_fields_for(b, reg)
            print('    cols =', [c.get('header') for c in cols])
            print('    vals =', values)
            print('    fields =', {k: v for k, v in B.beat_to_fields(b).items() if v})

        print('\n=== 渲染（逐字对比）===')
        out = render_beats_to_rpy(beats, label_name='a')
        for ln in out.splitlines():
            print('   ', ln)

        print('\n=== 会话：逐拍 apply 不改任何东西 ===')
        s = EditorSession(project_root=root)
        s.load(src)
        before = render_beats_to_rpy(s.beats, label_name='a')
        for idx in range(len(s)):
            f = s.fields(idx)
            s.apply(idx, f, cards=s.cards(idx), advanced_open=True,
                    stage_loaded=f.get('Stage'))
        after = render_beats_to_rpy(s.beats, label_name='a')
        print('   same?', before == after)
        if before != after:
            import difflib
            print('\n'.join(difflib.unified_diff(before.splitlines(),
                                                 after.splitlines(),
                                                 'before', 'after',
                                                 lineterm='')))
    finally:
        clear_cache()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    main()
