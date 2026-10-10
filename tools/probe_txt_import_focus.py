"""定向探针 2：把第一轮发现的疑点钉死（大小写撞车 / 假说话人 / 大块代码 / 编码）。"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.authoring.txt_import import (                      # noqa: E402
    CharacterMap, convert_text, extract_brackets, read_text_file)
from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer  # noqa: E402
from storygraph.story.beat_build import beats_from_parse_result     # noqa: E402
from storygraph.story.beat_render import render_beats_to_rpy        # noqa: E402


def roundtrip(rpy_text: str) -> str:
    with tempfile.TemporaryDirectory(prefix='probe2_') as td:
        p = Path(td) / 'a.rpy'
        p.write_text(rpy_text, encoding='utf-8')
        pr, _ = IncrementalRenPyAnalyzer().analyze(files=[p], graph=None)
        return render_beats_to_rpy(beats_from_parse_result(pr))


def head(t: str) -> None:
    print('\n' + '=' * 78 + f'\n{t}\n' + '=' * 78)


def body(r) -> list:
    out = []
    for ln in r.rpy_text.splitlines():
        if ln.strip() and not ln.startswith('label') and ln.strip() != 'pass':
            out.append(ln)
    return out


head('A. 大小写撞车：Ljk 与 ljk 是两个不同的人（逐行看映射）')
rows = {'ljk': {'ID': 'ljk', 'Name': '「李佳凯」'},
        'Ljk': {'ID': 'Ljk', 'Name': '「陆嘉珂」'}}
cm = CharacterMap(rows)
print(f'by_key    = {cm.by_key}')
print(f'layer_of  = {cm.layer_of}')
print(f'conflicts = {cm.conflicts}')
for tok in ('ljk', 'Ljk', 'LJK'):
    print(f'lookup({tok!r}) = {cm.lookup(tok)}')
r = convert_text('ljk"我是李佳凯"\nLjk"我是陆嘉珂"', characters=cm)
for ln in body(r):
    print(f'  > {ln}')
print(f'warnings = {r.warnings}')
print('结论：两行都写成 ljk —— 第二个人被第一个人顶掉，产物里**看不出**哪行错。')

head('B. 假说话人：英文句子/带下划线 token')
for text in ['So we said "hello"',
             'The man said "hi"',
             'L_J_K"带下划线"',
             'OK"好的"',
             'USA"美国"',
             'NASA"航天局"',
             'A B C"字母"',
             'We"我们"']:
    r = convert_text(text, characters={'jc': {'ID': 'jc', 'Name': '「蒋忱」'}})
    got = [x for x in body(r) if x.strip() != 'return']
    print(f'  {text!r:34s} -> {got}  unmapped={dict(r.unmapped)}')

head('C. 大块代码/生僻资料：一段 Ren\'Py 代码原文被当成剧本')
code_block = '''label start:
    scene bg room
    show lyj happy at left
    $ flag = True
    if flag:
        lyj "我用代码说话"
    menu:
        "选项一":
            jump end
        "选项二":
            return
    python:
        for i in range(3):
            print(i)
    return
'''
r = convert_text(code_block, characters={'lyj': {'ID': 'lyj', 'Name': '「凌跃捷」'}})
print('  --- 产物 ---')
for ln in r.rpy_text.splitlines():
    print(f'  | {ln}')
print(f'  未映射={dict(r.unmapped)}  丢弃={r.dropped}')
try:
    back = roundtrip(r.rpy_text)
    print(f'  往返一致：{back == r.rpy_text}')
except Exception as exc:                       # noqa: BLE001
    print(f'  往返抛异常：{type(exc).__name__}: {exc}')

head('D. 交错括号的"注释"里带引号 / 括号跨引号')
for text in ['JC"他说（笑）了"',
             'JC"括号里(半角)混排"',
             '"括号在引号外"（注释）',
             '（注释里带"引号"）正文',
             'JC"未闭合的引号',
             '（未闭合的括号 正文']:
    cleaned, comments = extract_brackets(text)
    r = convert_text(text, characters={'jc': {'ID': 'jc', 'Name': '「蒋忱」'}})
    print(f'  {text!r:30s} -> cleaned={cleaned!r} comments={comments!r}')
    print(f'  {"":30s}    产物 {[x for x in body(r) if x.strip() != "return"]}')

head('E. 编码：UTF-16 无 BOM 被"成功"解成 GBK')
with tempfile.TemporaryDirectory(prefix='probe2_enc_') as td:
    for enc in ('utf-16-le', 'utf-16-be'):
        p = Path(td) / f'{enc}.txt'
        p.write_bytes('JC"你好"\n旁白\n'.encode(enc))
        got = read_text_file(p)
        r = convert_text(got, characters={'jc': {'ID': 'jc', 'Name': '「蒋忱」'}})
        print(f'  {enc}: 读出 {got!r}')
        print(f'  {"":10s} 产物 {[x for x in body(r) if x.strip() != "return"]}'
              f' 告警={r.warnings}')

head('F. 单独代理对 / 非法字符')
try:
    convert_text('JC"坏字符 \ud800 在这里"')
    print('  没抛异常')
except Exception as exc:                        # noqa: BLE001
    print(f'  {type(exc).__name__}: {exc}')
    print('  → 转换本身不炸，是**调用方写盘/编码时报错**；'
          '但如果喂给 UI 预览，异常会从别处冒出来。')

head('G. 超长/大规模：1 万行')
big = '\n'.join(f'JC"第 {i} 行"（注释{i}）' for i in range(10000))
import time                                     # noqa: E402
t0 = time.time()
r = convert_text(big, characters={'jc': {'ID': 'jc', 'Name': '「蒋忱」'}})
t1 = time.time()
print(f'  1 万行转换 {t1 - t0:.2f}s  产物 {len(r.rpy_text.splitlines())} 行  '
      f'丢弃={len(r.dropped)} 未映射={len(r.unmapped)}')
t0 = time.time()
back = roundtrip(r.rpy_text)
t1 = time.time()
print(f'  往返 {t1 - t0:.2f}s  一致={back == r.rpy_text}')
