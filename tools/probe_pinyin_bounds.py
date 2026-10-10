"""用真实汉字反推 GB2312 一级汉字的**首字母分界**（我记的那张表错了一个字：

`瑾` 被判成 z，实际是 j —— 说明分界值有两处偏差）。这里直接扫一遍：
每个分界字母的**第一个字**（用 `unicodedata` 的拼音读不出来，所以用
"扫出所有边界，看哪个边界让 `瑾` 落在 j 段"的办法）。

做法：不猜边界，直接把**整段一级汉字**按"已知首字母的锚点字"切开。
"""

from pathlib import Path

#: 每个字母给一个"确定的锚点字"（这些字的拼音首字母没有争议）
ANCHORS = {
    'a': '啊', 'b': '八', 'c': '擦', 'd': '搭', 'e': '蛾', 'f': '发',
    'g': '噶', 'h': '哈', 'j': '击', 'k': '喀', 'l': '垃', 'm': '妈',
    'n': '拿', 'o': '哦', 'p': '啪', 'q': '期', 'r': '然', 's': '撒',
    't': '塌', 'w': '挖', 'x': '昔', 'y': '压', 'z': '匝',
}

#: 一级汉字区（GB2312 0xB0A1–0xD7F9）
codes = []
for hi in range(0xB0, 0xD8):
    for lo in range(0xA1, 0xFF):
        try:
            ch = bytes([hi, lo]).decode('gb2312')
        except UnicodeDecodeError:
            continue
        if '\u4e00' <= ch <= '\u9fff':
            codes.append(((hi << 8) | lo, ch))

print(f'一级汉字共 {len(codes)} 个：{codes[0][1]} … {codes[-1][1]}')

# 找每个锚点字的码位，作为该字母段的下界
bounds = []
for letter, ch in ANCHORS.items():
    b = ch.encode('gb2312')
    code = (b[0] << 8) | b[1]
    if code < 0xB0A1 or code > 0xD7F9:
        print(f'  ⚠ 锚点 {ch} 不在一级区')
        continue
    bounds.append((code, letter))
bounds.sort()
print('\n实测分界（下界）：')
for i, (code, letter) in enumerate(bounds):
    end = bounds[i + 1][0] - 1 if i + 1 < len(bounds) else 0xD7F9
    ch0 = bytes([code >> 8, code & 0xFF]).decode('gb2312')
    ch1 = bytes([end >> 8, end & 0xFF]).decode('gb2312')
    print(f'  {letter}: 0x{code:04X} ({ch0}) … 0x{end:04X} ({ch1})')

print('\n生成常量：')
lines = []
for i, (code, letter) in enumerate(bounds):
    end = bounds[i + 1][0] - 1 if i + 1 < len(bounds) else 0xD7F9
    lines.append(f"    (0x{code:04X}, 0x{end:04X}, '{letter}'),")
print('_PY_RANGES = (\n' + '\n'.join(lines) + '\n)')

# 自检：用几张真机角色表里的名字验证
NAMES = ['蒋忱', '叶怀瑾', '凌跃捷', '杜恒远', '星桥理子', '蓝', '妈妈',
         '舍友A', '班导', '教官', '校医', '姥爷', '杜恒远&A&B', '阿姨',
         '保安', '服务员', '汤X凤', '青']
print('\n自检：')
for name in NAMES:
    got = []
    for ch in name:
        try:
            b = ch.encode('gb2312')
        except UnicodeEncodeError:
            got.append(ch if ch.isascii() else '?')
            continue
        code = (b[0] << 8) | b[1]
        letter = ''
        for c0, c1, lt in [(s, e, l) for s, e, l in
                           [(c, (bounds[i + 1][0] - 1 if i + 1 < len(bounds)
                                 else 0xD7F9), l)
                            for i, (c, l) in enumerate(bounds)]]:
            if c0 <= code <= c1:
                letter = lt
                break
        got.append(letter or '?')
    print(f'  {name:12s} -> {"".join(got)}')
