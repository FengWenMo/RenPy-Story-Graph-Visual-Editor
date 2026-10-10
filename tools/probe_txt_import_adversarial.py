"""txt→renpy 对抗性探针：用一批"生僻资料"去压 `txt_import`。

⚠ 本脚本**只读**（不写盘、不改仓库），只打印事实。
用法：`D:\\Python\\python.exe tools\\probe_txt_import_adversarial.py`
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.authoring.txt_import import (                      # noqa: E402
    CharacterMap, _norm_key, convert_text, read_text_file)
from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer  # noqa: E402
from storygraph.story.beat_build import beats_from_parse_result     # noqa: E402
from storygraph.story.beat_render import render_beats_to_rpy        # noqa: E402

MATERIAL = ROOT / 'tests' / 'data' / 'txt_hell_material.txt'

FINDINGS: list = []


def find(no: str, title: str, detail: str = '') -> None:
    FINDINGS.append((no, title, detail))
    print(f'\n[!] {no} {title}')
    if detail:
        print(detail)


def ok(msg: str) -> None:
    print(f'    ok  {msg}')


def show(tag: str, text: str) -> None:
    print(f'    --- {tag} ---')
    for ln in text.splitlines():
        print(f'    | {ln}')


# ==================== 0. 往返（我们自己的渲染器） ====================

def roundtrip(rpy_text: str):
    """产物 → 我们的解析器 → 渲染；返回 (渲染文本, ParseResult)。"""
    with tempfile.TemporaryDirectory(prefix='probe_txt_') as td:
        p = Path(td) / 'a.rpy'
        p.write_text(rpy_text, encoding='utf-8')
        pr, _ = IncrementalRenPyAnalyzer().analyze(files=[p], graph=None)
        return render_beats_to_rpy(beats_from_parse_result(pr)), pr


# ==================== 1. 生僻资料整章 ====================

CHARS_REALISH = {
    'jc': {'ID': 'jc', 'Name': '「蒋忱」'},
    'yhj': {'ID': 'yhj', 'Name': '「叶怀瑾」'},
    'lyj': {'ID': 'lyj', 'Name': '「凌跃捷」'},
    'l': {'ID': 'l', 'Name': '「蓝」'},
    'a': {'ID': 'a', 'Name': '「甲」'},
    'b': {'ID': 'b', 'Name': '「乙」'},
    'mm': {'ID': 'mm', 'Name': '妈妈', 'Aliases': 'MOM|母亲|老妈'},
    'ld': {'ID': 'ld', 'Name': '领导', 'Aliases': 'LINGDAO'},
}


def attack_material():
    print('=' * 78)
    print('1. 生僻资料整章（大块文本 + 错行 + 生僻字符）')
    print('=' * 78)
    text = read_text_file(MATERIAL)
    r = convert_text(text, characters=CharacterMap(CHARS_REALISH),
                     filename=MATERIAL.name)
    print(f'    行型统计 : {r.counts}')
    print(f'    未映射   : {r.unmapped}')
    print(f'    丢弃     : {r.dropped}')
    print(f'    告警 {len(r.warnings)} 条:')
    for w in r.warnings:
        print(f'      - {w}')
    show('产物', r.rpy_text)

    # 丢行 = 0
    if r.dropped:
        find('A1', '生僻资料里有行被静默丢掉', str(r.dropped))
    else:
        ok('没有丢行')

    # 往返逐字节
    back, pr = roundtrip(r.rpy_text)
    if back != r.rpy_text:
        diff = [(i, x, y) for i, (x, y) in
                enumerate(zip(r.rpy_text.splitlines(), back.splitlines()))
                if x != y][:8]
        find('A2', '生僻资料产物往返**不**逐字节一致',
             f'行数 {len(r.rpy_text.splitlines())} vs '
             f'{len(back.splitlines())}\n' + '\n'.join(map(str, diff)))
    else:
        ok('往返逐字节一致')

    # 我们自己的产物必须能被自己的解析器干净地读出来
    n_say = sum(1 for s in pr.statements if getattr(s, 'kind', '') == 'say')
    print(f'    解析出的 say 语句：{n_say}')
    return r


# ==================== 2. Ljk / ljk 是两个不同的人 ====================

def attack_case_collision():
    print('\n' + '=' * 78)
    print('2. 若 Ljk 与 ljk 是**两个不同的人**')
    print('=' * 78)
    rows = {'ljk': {'ID': 'ljk', 'Name': '「李佳凯」'},
            'Ljk': {'ID': 'Ljk', 'Name': '「陆嘉珂」'}}
    cm = CharacterMap(rows)
    print(f'    归一化键 ljk -> {_norm_key("ljk")!r}, Ljk -> {_norm_key("Ljk")!r}')
    print(f'    by_key      : {cm.by_key}')
    print(f'    layer_of    : {cm.layer_of}')
    print(f'    conflicts   : {cm.conflicts}')
    print(f'    conflict_警告: {cm.conflict_warnings()}')
    for tok in ('Ljk', 'ljk', 'LJK'):
        print(f'    lookup({tok!r}) = {cm.lookup(tok)}')

    r = convert_text('Ljk"我是陆嘉珂"\nljk"我是李佳凯"', characters=cm)
    show('产物', r.rpy_text)
    print(f'    warnings = {r.warnings}')

    if not cm.conflicts and not r.warnings:
        find('B1', '两个角色只因大小写不同 → **静默合并**，一个把另一个顶掉',
             f'Ljk 与 ljk 是两个不同的人，但 by_key 只有 {cm.by_key!r}，'
             '零告警；两行台词都写成同一个变量 → 说话人错。')
    if 'Ljk "我是陆嘉珂"' in r.rpy_text and 'ljk "我是李佳凯"' in r.rpy_text:
        ok('产物里保留了原文 token（未静默改名）')
    else:
        find('B1b', '产物里角色 token 被改写', r.rpy_text)


# ==================== 3. 单字母角色名 ====================

def attack_single_letter():
    print('\n' + '=' * 78)
    print('3. 单字母角色（真实剧本里就是 `L`）→ 一切以 l 开头的写法？')
    print('=' * 78)
    cm = CharacterMap({'l': {'ID': 'l', 'Name': '「蓝」'},
                       'lyj': {'ID': 'lyj', 'Name': '「凌跃捷」'}})
    for tok in ('L', 'l', 'LINGDAO', '李', 'ld'):
        print(f'    lookup({tok!r}) = {cm.lookup(tok)}   '
              f'candidates = {cm.candidates(tok)}')
    r = convert_text('LINGDAO"开会了"', characters=cm)
    print(f'    未映射 LINGDAO 的候选 = {cm.candidates("LINGDAO")}')
    warn = [w for w in r.warnings if 'LINGDAO' in w]
    print(f'    告警：{warn}')
    if any('l' in cm.candidates('LINGDAO') for _ in [0]) and \
            cm.candidates('LINGDAO'):
        find('C1', '未映射候选被单字母角色名污染',
             f'`LINGDAO` 的候选 = {cm.candidates("LINGDAO")} —— '
             '提示用户"相近的角色"，实际毫无关系（前缀/子串太松）。')
    else:
        ok('候选没有明显污染')


# ==================== 4. 撞车与强层赢 ====================

def attack_strongest_layer():
    print('\n' + '=' * 78)
    print('4. 撞车：别名撞别人的显示名 → 报出来，但映射按**强层赢**')
    print('=' * 78)
    rows = {'aaa': {'ID': 'aaa', 'Name': '「甲」', 'Aliases': '「乙」'},
            'bbb': {'ID': 'bbb', 'Name': '「乙」'}}
    cm = CharacterMap(rows)
    print(f'    by_key    = {cm.by_key}')
    print(f'    conflicts = {cm.conflicts}')
    var, layer = cm.lookup('乙')
    print(f'    lookup(乙) = {(var, layer)}  ← 必须是显示名的 bbb/display（不是先登记的 aaa）')
    if not cm.conflicts:
        find('D1', '别名撞显示名**没有报出来**', str(cm.by_key))
    elif (var, layer) != ('bbb', 'display'):
        find('D2', '"强层赢"失效（正名输给了别人的别名）', f'{(var, layer)}')
    else:
        ok('报了，且强层赢生效')


# ==================== 5. 别名把两个不同的人合并 ====================

def attack_alias_merge():
    print('\n' + '=' * 78)
    print('5. 两人共享同一别名（用户手滑把别名填重了）')
    print('=' * 78)
    rows = {'aaa': {'ID': 'aaa', 'Name': '「甲」', 'Aliases': 'A'},
            'bbb': {'ID': 'bbb', 'Name': '「乙」', 'Aliases': 'A'}}
    cm = CharacterMap(rows)
    print(f'    by_key    = {cm.by_key}')
    print(f'    conflicts = {cm.conflicts}')
    r = convert_text('A"我是谁"', characters=cm)
    print(f'    产物台词：{[x for x in r.rpy_text.splitlines() if "我是谁" in x]}')
    print(f'    warnings = {r.warnings}')
    if not cm.conflicts:
        find('E1', '同一别名指到两个人没有报出来', str(cm.by_key))
    else:
        ok('报了')


# ==================== 6. 生僻字符 / 编码 ====================

def attack_weird_chars():
    print('\n' + '=' * 78)
    print('6. 生僻字符与编码')
    print('=' * 78)
    cm = CharacterMap(CHARS_REALISH)
    cases = {
        '全角拉丁': 'ＬＪＫ"全角字母"',
        '西里尔 МОМ': 'МОМ"西里尔字母"',
        '零宽空格': 'L\u200bJK"零宽空格分隔"',
        '软连字符': 'L\u00adJK"软连字符"',
        '不换行空格': 'L\u00a0JK"不换行空格"',
        '组合附加符': 'L̂JK"带组合符"',
        '变体选择符': 'L\ufe0fJK"变体选择符"',
        '半角片假名': 'ﾊﾞﾌﾞ"半角片假名"',
        '阿拉伯-印度数字': '١٢٣"数字"',
        'RTL 覆盖符': 'L\u202eJK"RTL覆盖"',
        '单独代理对': 'JC"\ud800"',
    }
    for name, text in cases.items():
        try:
            r = convert_text(text, characters=cm)
            line = [x for x in r.rpy_text.splitlines()
                    if x.strip() and not x.startswith('label')
                    and x.strip() not in ('pass', 'return')]
            back, _ = roundtrip(r.rpy_text)
            rt = 'OK' if back == r.rpy_text else '往返不一致'
            print(f'    {name:14s} -> {line!r}  未映射={dict(r.unmapped)}  {rt}')
            if rt != 'OK':
                find('F1', f'{name} 往返不一致', f'{line}')
        except Exception as exc:                      # noqa: BLE001
            find('F2', f'{name} 直接抛异常', f'{type(exc).__name__}: {exc}')


# ==================== 7. 编码读取 ====================

def attack_encoding():
    print('\n' + '=' * 78)
    print('7. 编码：UTF-16 / 无 BOM 的 UTF-16 / 混合')
    print('=' * 78)
    payload = 'JC"你好"\n旁白\n'
    for enc in ('utf-16', 'utf-16-le', 'utf-16-be', 'utf-32', 'cp936',
                'big5', 'shift_jis', 'latin-1'):
        with tempfile.TemporaryDirectory(prefix='probe_enc_') as td:
            p = Path(td) / f'{enc}.txt'
            try:
                p.write_bytes(payload.encode(enc))
            except UnicodeEncodeError as exc:
                print(f'    {enc:10s} 无法编码：{exc}')
                continue
            try:
                got = read_text_file(p)
                good = 'JC' in got and '你好' in got
                print(f'    {enc:10s} 读出 {got!r}  {"OK" if good else "内容可疑"}')
                if not good:
                    find('G1', f'{enc} 读出来是乱码/内容不对',
                         f'{got[:60]!r} —— 该编码解码"成功"了，'
                         '于是后面所有行都成了旁白/不认识的字符，零告警')
            except UnicodeDecodeError as exc:
                print(f'    {enc:10s} 抛 UnicodeDecodeError（可接受：调用方能报）')
            except Exception as exc:                  # noqa: BLE001
                find('G2', f'{enc} 抛了别的异常', f'{type(exc).__name__}: {exc}')
    # 空文件 / 只有 BOM / 二进制
    for name, raw in (('空', b''), ('只有BOM', b'\xef\xbb\xbf'),
                      ('二进制', bytes(range(256)))):
        with tempfile.TemporaryDirectory(prefix='probe_enc_') as td:
            p = Path(td) / 'x.txt'
            p.write_bytes(raw)
            try:
                got = read_text_file(p)
                print(f'    {name:8s} -> {got[:40]!r}')
            except Exception as exc:                  # noqa: BLE001
                print(f'    {name:8s} -> {type(exc).__name__}: {exc}')


# ==================== 8. 确定性 / 重入 ====================

def attack_determinism():
    print('\n' + '=' * 78)
    print('8. 确定性与 CharacterMap 复用（同一个对象被复用两次）')
    print('=' * 78)
    cm = CharacterMap(CHARS_REALISH)
    a = convert_text('JC"一"\nMOM"二"', characters=cm)
    b = convert_text('JC"一"\nMOM"二"', characters=cm)
    print(f'    两次产物相同：{a.rpy_text == b.rpy_text}')
    print(f'    复用后 conflicts 长度：{len(cm.conflicts)}')
    if a.rpy_text != b.rpy_text:
        find('H1', '同一个 CharacterMap 复用两次产物不同', '')
    if len(cm.conflicts) != 0:
        find('H2', 'CharacterMap 被复用后 conflicts 增长', str(cm.conflicts))
    else:
        ok('复用不产生副作用')


def main() -> int:
    attack_material()
    attack_case_collision()
    attack_single_letter()
    attack_strongest_layer()
    attack_alias_merge()
    attack_weird_chars()
    attack_encoding()
    attack_determinism()

    print('\n' + '=' * 78)
    print(f'发现 {len(FINDINGS)} 处需要看的事实：')
    for no, title, _detail in FINDINGS:
        print(f'  [{no}] {title}')
    print('=' * 78)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
