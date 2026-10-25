"""第六轮·二分：难缠工程里到底是哪一种形状让增量补丁失败。

⚠ 必须是 **.py 文件**（铁律 #10）：同样的样本经 bash heredoc 会把 `\\n`
写成 `/n`，曾经"复现"出根本不存在的 P0。

跑法： python tools/_p0_audit_round6_bisect.py
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.app.editor import EditorSession  # noqa: E402

BASE = '''define jc = Character("甲")
default love = 0

label {label}:
    scene bg room with dissolve
    play music "bgm/theme.ogg" fadein 1.0
    jc "第一句。"
{EXTRA}    return
'''

FEATURES = [
    ('行尾注释', '    jc "第二句。"    # 行尾注释\n'),
    ('if/else', '    if love > 1:\n        jc "够。"\n    else:\n        jc "不够。"\n'),
    ('elif 链', '    if love > 3:\n        jc "多。"\n'
                '    elif love > 1:\n        jc "中。"\n'
                '    else:\n        jc "少。"\n'),
    ('menu 两项', '    menu:\n        "甲":\n            jc "选甲。"\n'
                  '        "乙":\n            jc "选乙。"\n'),
    ('menu 条件项', '    menu:\n        "甲":\n            jc "选甲。"\n'
                    '        "乙" if love < 2:\n            jc "选乙。"\n'),
    ('menu pass', '    menu:\n        "甲":\n            pass\n'),
    ('call screen', '    call screen phone_dialogue_log\n'),
    ('pause', '    pause 0.5\n'),
    ('$ python', '    $ love += 1\n'),
    ('重复台词', '    jc "同一句"\n    jc "同一句"\n'),
    ('extend', '    extend "补一句。"\n'),
]


def run(src: str, label='ch01'):
    tmp = Path(tempfile.mkdtemp(prefix='sg_r6b_'))
    try:
        g = tmp / 'game'
        g.mkdir()
        p = g / 'ch01.rpy'
        p.write_bytes(src.replace('\n', '\r\n').encode('utf-8'))
        (g / 'screens.rpy').write_text(
            'screen phone_dialogue_log():\n    frame:\n        text "x"\n',
            encoding='utf-8')
        s = EditorSession(project_root=tmp)
        n = s.load(p)
        r = s.export(p)
        reason = (r.get('blocking') or [r.get('note') or ''])[0]
        return n, bool(r.get('ok')), str(reason)[:120].replace('\n', ' ')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    print('=== 单个形状 ===')
    for name, extra in FEATURES:
        n, ok, why = run(BASE.format(label='ch01', EXTRA=extra))
        flag = 'OK  ' if ok else 'FAIL'
        print(f'  {flag} {name:<10} 拍={n:<3} {"" if ok else why}')

    print('\n=== 全叠在一起 ===')
    allx = ''.join(x for _, x in FEATURES)
    n, ok, why = run(BASE.format(label='ch01', EXTRA=allx))
    print(f'  {"OK  " if ok else "FAIL"} 全叠 拍={n} {why if not ok else ""}')

    print('\n=== 点号 label（main.route）===')
    n, ok, why = run(BASE.format(label='main.route', EXTRA=''), label='main.route')
    print(f'  {"OK  " if ok else "FAIL"} 点号 label 拍={n} {why if not ok else ""}')
    n, ok, why = run(BASE.format(label='main.route',
                                 EXTRA=FEATURES[0][1]), label='main.route')
    print(f'  {"OK  " if ok else "FAIL"} 点号 + 行尾注释 {why if not ok else ""}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
