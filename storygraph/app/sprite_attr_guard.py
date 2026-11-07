# -*- coding: utf-8 -*-
"""
导出前的**立绘属性门禁**：这些 `show` 的属性，项目真的声明过吗？
================================================================

用户 2026-11-05（`base_hand_down` 事故之后）：

> 加强判据……导出时也要再警告一次，并且说明其可能导致无法运行……
> 只报，不乱改。

卡片上那条黄条只是提示，它挡不住：
  · 旧数据（`chapter2_5.rpy` 里 `show ye base_hand_down …` 早就写进去了）；
  · Stage 列手写；
  · 从 `.xlsx` 导回来的表；
  · 用户在候选里选错、然后保存。
导出那一刻必须有人再看一眼，但**不许静默改**——要不要照原样写，人拍板。

## 判据（与卡片提示同一口径）

对**项目声明过**的 tag（`layeredimage` / `image`）：

  1. `declared_attrs_of(tag)` —— 项目声明的属性；
  2. `attrs_of_tag(tag)` —— 资源表认的属性（`_hd_cut` 这类内部件靠它）；
  3. `defined_words()` —— 项目 `define` / `init python` 里定义过的字面量
     （`aa` 这种"服装代号、不对应任何 png"的哨兵靠它，**不许报**）；
  4. `token_image_path(tag, word)` —— 按 `image_format` 展开后图片真的在
     （声明漏写 `attribute`、但图在）。

四条都不沾的词才报。**拼法变体**（`base_hand_down` ↔ `base_handdown`，
只看大小写 / `_` / `-`）单独给出 `fix`，但**不自动改**。

⚠ **不判"未知 tag"**：那是 `app.stage_identity_guard` 的活（`show refusing`
这类名字项目里根本不存在）。两个模块各管一层，别在这里重复报。

⚠ **不判"没有声明却依旧能跑"的哨兵**：`aa` 不在 `layeredimage` 里，但在
`defined_words` 里、剧本里用了 41 处、引擎忽略它正好落到默认层。这类词
**一个都不报** —— 判据是"项目有没有定义它"，不是"它有没有对应 png"。

## 后果说明（只报告，不吓人）

未知属性在 `LayeredImage` 里**不会抛异常**：`_duplicate` 对认不出的属性
只打一行 developer 警告，然后照常渲染。所以后果分两种：

  · 这个词是**拼写错误** → 那一层不会出现，游戏能跑，但画面不是你要的；
  · 这个词背后是**未知 tag / 语法错** → 才可能直接起不来（那两类由
    `stage_suspect` / `stage_identity_guard` 单独报）。

本模块的 `report()` 会把这句话写清楚，不把"能跑但画面错"说成"一定崩"。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from storygraph.app import beats as B

#: `Beat.show` 的多值分隔符（与 `beat_model` 同口径）。
_SEP = ';'


@dataclass
class AttrSuspect:
    """一条"属性项目里没声明"的 show 项（一个字都不动，只指认）。"""

    index: int                 # 第几拍（0 基）
    seq: object = ''           # `Beat.seq`
    label: str = ''
    spec: str = ''             # 原文：这一条 show 项
    tag: str = ''              # spec 首词
    attr: str = ''             # 认不出的那个属性词
    fix: str = ''              # 拼法变体对应的正确写法（认不出 = ''）
    reason: str = ''           # 人话：为什么觉得它没声明
    consequence: str = ''      # 人话：导出后会怎样
    target: object = None      # 指向那个 Beat（不参与展示）

    def as_dict(self) -> dict:
        return {'index': self.index, 'seq': self.seq, 'label': self.label,
                'spec': self.spec, 'tag': self.tag, 'attr': self.attr,
                'fix': self.fix, 'reason': self.reason,
                'consequence': self.consequence}


@dataclass
class ScanResult:
    """`scan_sprite_attrs` 的返回值（结构照 `stage_suspect.ScanResult`）。"""

    suspects: list = field(default_factory=list)
    checked: int = 0           # 看了几条 show 项
    judged: int = 0            # 其中"项目声明过 tag、判得了"的条数
    degraded: bool = False     # True = 没有符号表 / 没有声明，一条都没判

    def __bool__(self) -> bool:
        return bool(self.suspects)

    def __len__(self) -> int:
        return len(self.suspects)

    def report(self) -> str:
        """命令行 / 日志用的一段人话（只提示，不阻塞）。"""
        if not self.suspects:
            return ''
        lines = [f'「立绘」里有 {len(self.suspects)} 条 show 写了项目没声明的属性：']
        for it in self.suspects:
            lines.append(f'  · 第 {it.seq} 拍：{it.spec}　—— {it.reason}')
        lines.append(
            '  导出后最坏会让游戏起不来；最常见是引擎忽略这些词：能跑，'
            '但这一层不会出现，画面不是你要的。请在卡片里改成项目声明过的写法。')
        return '\n'.join(lines)


def _word_of(token) -> str:
    """`close_happy@i11` → `close_happy`；末尾 `:`（ATL 块头）也剥掉。"""
    return str(token or '').split('@', 1)[0].rstrip(':').strip()


def _declared(symbols, tag: str) -> list:
    """项目声明过这个 tag 吗（声明列表；空 = 不归这一层管）。"""
    return [str(w) for w in (symbols.declared_attrs_of(tag) or ()) if str(w)]


def _known(symbols, tag: str, declared) -> set:
    """与卡片提示同一口径的"认识"集合（四类证据）。"""
    known = set(declared or ())
    known |= {str(w) for w in (symbols.attrs_of_tag(tag) or ()) if str(w)}
    known |= {str(w) for w in (symbols.defined_words() or ()) if str(w)}
    return known


def _fix_for(word: str, known) -> str:
    """拼法变体 → 项目里的正确写法（唯一命中才给；否则 = ''）。"""
    key = B.attr_word_key(word)
    if not key:
        return ''
    hits = {str(w) for w in known
            if str(w) != word and B.attr_word_key(str(w)) == key}
    return next(iter(hits)) if len(hits) == 1 else ''


def _reason(tag: str, word: str, fix: str) -> str:
    if fix:
        return (f'「{tag}」没有「{word}」这个属性 —— 项目里写的是「{fix}」'
                f'（`_` 也是属性名的一部分，多一个下划线就是另一个属性）。')
    return (f'「{tag}」没有「{word}」这个属性，项目里也没找到拼法相近的声明。')


def _consequence(word: str, fix: str) -> str:
    base = (f'引擎会忽略「{word}」：游戏通常还能跑，但这一层不会出现，'
            f'画面不是你要的。')
    if fix:
        return base + f' 如果这是拼写错误，应写成「{fix}」。'
    return base + ' 如果这个词本来该是一张图，先确认项目里到底叫什么。'


def unknown_attrs_of_spec(spec: str, symbols) -> list:
    """
    一条 show 项 → 认不出的属性（`[{'tag','attr','fix','reason','consequence'}]`）。

    **只读**，不改 `spec`。tag 没声明 / 没有符号表 → `[]`（不归这一层管）。
    """
    if symbols is None:
        return []
    text = str(spec or '').strip()
    if not text:
        return []
    tag, attrs, _clauses = B.split_show_spec(
        text, B._declared_clause_keywords(symbols))
    if not tag or not attrs:
        return []
    declared = _declared(symbols, tag)
    if not declared:
        return []                               # 未知 tag 归 identity_guard
    known = _known(symbols, tag, declared)
    out = []
    for raw in attrs:
        word = _word_of(raw)
        if not word or word in known:
            continue
        if symbols.token_image_path(tag, word):
            known.add(word)                     # 图在 = 声明漏写也算认识
            continue
        fix = _fix_for(word, known)
        out.append({'tag': tag, 'attr': word, 'fix': fix,
                    'reason': _reason(tag, word, fix),
                    'consequence': _consequence(word, fix)})
    return out


def scan_sprite_attrs(beats, symbols) -> ScanResult:
    """
    扫一遍这批拍的 `show`，挑出"属性项目没声明"的那些。

    **只读**：不碰 `Beat.show`、不碰文件。导出前由
    `ui_qt.window_export._review_stage_suspects` 调用；结果只展示，
    要不要照原样导出由人拍板。
    """
    out = ScanResult()
    if symbols is None:
        out.degraded = True
        return out
    seen = set()
    any_declared = False
    for i, beat in enumerate(beats or ()):
        for spec in [p.strip() for p in
                     str(getattr(beat, 'show', '') or '').split(_SEP)
                     if p.strip()]:
            out.checked += 1
            tag = B.split_show_spec(spec)[0]
            if tag and _declared(symbols, tag):
                any_declared = True
                out.judged += 1
            for hit in unknown_attrs_of_spec(spec, symbols):
                key = (i, spec, hit['attr'])
                if key in seen:
                    continue
                seen.add(key)
                out.suspects.append(AttrSuspect(
                    index=i, seq=getattr(beat, 'seq', ''),
                    label=getattr(beat, 'label', ''), spec=spec,
                    tag=hit['tag'], attr=hit['attr'], fix=hit['fix'],
                    reason=hit['reason'], consequence=hit['consequence'],
                    target=beat))
    # 有 show、但一条都没判过 → 没有声明可依据，别假装"干净"。
    if out.checked and not any_declared:
        out.degraded = True
    return out


__all__ = ['AttrSuspect', 'ScanResult', 'unknown_attrs_of_spec',
           'scan_sprite_attrs']
