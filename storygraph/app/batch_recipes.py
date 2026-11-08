# -*- coding: utf-8 -*-
"""
把编剧的一句话翻成批处理规则（**纯函数**，UI / CLI / 测试共用）
================================================================

需求（2026-11-07，用户）：

> 我当然知道！那是 json 文件！是人亲手写进去的！可是我不要手写 json！
> 我这个工具是给编剧设计的！我需要自定义模块中的可视化界面！

所以这一层存在的唯一理由：**人只说"什么时候加、加什么"，代码由工具拼**。

    「每次立绘出现或改变（含退场）」 + 「只溶解立绘层，0.1 秒」
        ↓  build_rule()
    {"when": {"change": "sprite", "transition": "none",
              "code_not_contains": "renpy.transition"},
     "insert": {"placement": "after_each_change",
                "text": "$ renpy.transition(Dissolve(0.1), layer=\\"master\\")"}}

⚠ 「每次」= **一拍里每一条 `show`/`hide` 后面都补一行**（`after_each_change`），
不是"每拍一行"；要后者选「每拍只在最后一次…」（`after_last_change`）。
2026-11-08 第十九轮改的这一条：用户指着 2_2 里并排的三条 `show` 说
「本来是两条分别的 show！你应该加两个那一句话……你只给了一个」。

三条纪律
--------
① **校验只有一份**：拼出来的规则一律过 `batch_edit.parse_rule`（和读文件
   同一条路）。这里绝不自己写一套"看起来合法"的判据 —— 否则迟早出现
   "界面说能存、引擎说不能用"。
② **认不出就不改**：文件里已有、但界面表达不了的规则（`replace` / `delete` /
   带 `text_contains`/`speaker`/`label` 条件的），`read_form` 返回 `None` +
   人话原因；保存时**原样带回去**（绝不因为"界面没显示"就把它删了）。
③ **值没变就是一个字节都不动**：`build_rule` 只造它认识的键，不重排、
   不补默认值以外的字段；`dump_rules` 用 `indent=2` + 末尾换行，反复保存
   的结果逐字相同（判据 `test_batch_recipes.py::test_save_is_stable`）。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from storygraph.app.batch_edit import (SCHEMA, BatchRuleError, parse_rule)

#: 「什么时候加」——值 → 人话。顺序就是界面下拉的顺序（常用在前）。
#:
#: 2026-11-08 第十九轮：用户拿真机 2_2 指着一个拍里的三条 `show` 说
#: 「本来是两条分别的 show！你应该加两个那一句话……你只给了一个……」——
#: 所以"每次"按字面意思落到 `after_each_*`（**每一条都加**），
#: 而"只加在最后一次"另立两个值，认得出、也说得出。
MODES = (
    ('sprite_change', '每次立绘出现或改变（含退场）—— 每一条都加'),
    ('sprite_show', '每次立绘出现（不含退场）—— 每一条都加'),
    ('sprite_change_last', '每拍只在最后一次立绘变化后面加一行'),
    ('sprite_show_last', '每拍只在最后一次立绘后面加一行'),
    ('say_line', '每一句台词后面'),
    ('every_beat', '每一拍后面'),
)

#: 「加什么」——值 → 人话。
CUE_KINDS = (
    ('master_transition', '只溶解立绘层（推荐：对话框不跟着溶）'),
    ('full_transition', '整屏溶解（对话框也会跟着溶）'),
    ('named', '项目里定义好的转场（只写名字）'),
    ('raw', '自定义一行代码（高级）'),
)

#: 内置转场构造器（Ren'Py 自带，不需要项目声明）。**只有这些能写 `名字(秒)`**。
BUILTIN_EFFECTS = ('Dissolve', 'Fade', 'ImageDissolve', 'MoveTransition',
                   'Pixellate', 'Swing', 'AlphaDissolve')

#: 层名候选（`layer=` 的常用值；项目自己的层可以手输）。
LAYERS = ('master', 'screens', 'overlay', 'transient', 'top')

#: 默认值 —— 也用于"新建一条规则"的初始表单。
DEFAULT_FORM = {
    'id': '',
    'mode': 'sprite_change',
    'cue': 'master_transition',
    'effect': 'Dissolve',
    'seconds': '0.1',
    'layer': 'master',
    'always': False,
    'named': '',
    'raw': '',
    'skip_existing_transition': True,
    'skip_if_present': True,
}

#: `mode` → `(when, placement)`。**唯一一份映射**（读回来也用它反查）。
_MODE_RULES = {
    'sprite_change': ({'change': 'sprite'}, 'after_each_change'),
    'sprite_show': ({'show': 'sprite'}, 'after_each_show'),
    'sprite_change_last': ({'change': 'sprite'}, 'after_last_change'),
    'sprite_show_last': ({'show': 'sprite'}, 'after_last_show'),
    'say_line': ({'say': True}, 'after_beat'),
    'every_beat': ({}, 'after_beat'),
}

#: `when` 里**界面不表达**的键 —— 出现任何一个就判"这条界面改不了"。
_UNEXPRESSIBLE_WHEN = ('text_contains', 'speaker', 'label', 'code_contains')

_SECONDS_RE = re.compile(r'^\d+(?:\.\d+)?$')
#: 反解用：`Dissolve(0.1)` → ('Dissolve', '0.1')
_EFFECT_RE = re.compile(r'^([A-Za-z_]\w*)\s*\(\s*([0-9.]+)\s*\)$')
#: 反解用：整行 `$ renpy.transition(...)`。
_CALL_RE = re.compile(r'^\$\s*renpy\.transition\((.*)\)$', re.S)


def label_of(table, value: str) -> str:
    """值 → 人话（认不出就原样返回，不猜）。"""
    for key, text in table:
        if key == value:
            return text
    return str(value or '')


def _seconds(value) -> str:
    """秒数 → 写进代码的那个字符串（只认正数；认不出抛 `BatchRuleError`）。"""
    text = str(value if value is not None else '').strip()
    if not _SECONDS_RE.match(text):
        raise BatchRuleError(f'时长要写数字（秒），收到 {text!r}')
    number = float(text)
    if number <= 0:
        raise BatchRuleError('时长必须大于 0')
    # `0.10` → `0.1`（同一秒数不留两种写法，否则反复保存会改字节）
    return f'{number:g}'


def build_text(form) -> str:
    """
    表单 → **要插进去的那一行**（不含缩进，缩进跟锚点走）。

    四种 `cue` 的产物：

      · `master_transition` → `$ renpy.transition(Dissolve(0.1), layer="master")`
      · `full_transition`   → `$ renpy.transition(Dissolve(0.1))`
      · `named`             → `$ renpy.transition(my_wipe, layer="master")`
      · `raw`               → 人写的那一行（**必须以 `$` 开头**，否则引擎报错）
    """
    f = dict(DEFAULT_FORM)
    f.update({k: v for k, v in dict(form or {}).items() if k in DEFAULT_FORM})
    cue = str(f.get('cue') or '')
    always = ', always=True' if f.get('always') else ''

    if cue == 'raw':
        text = str(f.get('raw') or '').strip()
        if not text:
            raise BatchRuleError('「自定义一行代码」不能是空的')
        if not text.startswith('$') and not text.startswith('python:'):
            raise BatchRuleError(
                '自定义代码要以 `$` 开头（例如 `$ renpy.transition(...)`）——'
                '不带 `$` 的那行在引擎里不是合法语句')
        return text

    if cue == 'named':
        name = str(f.get('named') or '').strip()
        if not name:
            raise BatchRuleError('选「项目里定义好的转场」就要填那个名字')
        if any(ch in name for ch in '()"\''):
            raise BatchRuleError(f'{name!r} 不像一个转场名字（是不是把括号一起粘进来了？）')
        layer = str(f.get('layer') or '').strip()
        tail = f', layer="{layer}"' if layer else ''
        return f'$ renpy.transition({name}{tail}{always})'

    effect = str(f.get('effect') or '').strip()
    if effect not in BUILTIN_EFFECTS:
        raise BatchRuleError(
            f'{effect!r} 不是内置转场构造器；项目自己的转场请选'
            f'「项目里定义好的转场」')
    seconds = _seconds(f.get('seconds'))
    if cue == 'full_transition':
        return f'$ renpy.transition({effect}({seconds}){always})'
    layer = str(f.get('layer') or '').strip()
    if not layer:
        raise BatchRuleError('「只溶解立绘层」必须选一个层（通常是 master）')
    return f'$ renpy.transition({effect}({seconds}), layer="{layer}"{always})'


def build_rule(form, rule_id: str = '') -> dict:
    """
    表单 → **一条规则 dict**（可直接进 `storygraph.batch.json` 的 `rules`）。

    产物一定过 `parse_rule` —— 拼错/漏字段在这里就炸，不会写进文件。
    """
    f = dict(DEFAULT_FORM)
    f.update({k: v for k, v in dict(form or {}).items() if k in DEFAULT_FORM})
    mode = str(f.get('mode') or '')
    if mode not in _MODE_RULES:
        raise BatchRuleError(f'不认识「什么时候加」：{mode!r}')
    when, placement = _MODE_RULES[mode]
    when = dict(when)
    if f.get('skip_existing_transition'):
        when['transition'] = 'none'
    text = build_text(f)
    if f.get('skip_existing_transition') and 'renpy.transition' in text:
        # 双保险：`transition: none` 认的是"这一拍有没有转场"，
        # 而 `code_not_contains` 认的是"这一拍有没有这一行" —— 后者能挡住
        # "同一拍被两条规则盯上"和"作者自己写了一行不同的参数"。
        when['code_not_contains'] = 'renpy.transition'
    rid = str(rule_id or f.get('id') or '').strip() or 'cue'
    raw = {
        'id': rid,
        'when': when,
        'insert': {
            'placement': placement,
            'text': text,
            'skip_if_present': bool(f.get('skip_if_present', True)),
        },
    }
    parse_rule(raw, f'规则 {rid!r}')      # 唯一校验：不合法当场抛
    return raw


def describe(rule) -> str:
    """
    规则 → 一句人话（规则列表里显示的那种）。

    只描述它**认得出来**的部分；认不出的部分照原样贴一小段，不美化。
    """
    if not isinstance(rule, dict):
        return str(rule)
    when = rule.get('when') or {}
    body = rule.get('insert') or rule.get('replace') or rule.get('delete') or {}
    if 'insert' in rule:
        text = body.get('text')
        text = '\n'.join(text) if isinstance(text, list) else str(text or '')
        where = {
            'after_each_change': '每一条立绘变化后面',
            'after_each_show': '每一条立绘后面',
            'after_last_change': '最后一个立绘变化后面',
            'after_last_show': '最后一个立绘后面',
            'after_beat': '这一拍末尾',
            'before_beat': '这一拍开头',
        }.get(str(body.get('placement') or ''), str(body.get('placement') or ''))
        cond = _describe_when(when)
        return f'{cond} → 在{where}加一行：{_short(text)}'
    if 'replace' in rule:
        append = str(body.get('append') or '')
        if append:
            return (f'{_describe_when(when)} → 在符合条件的一行后面追加：'
                    f'{_short(append.strip())}')
        return f'{_describe_when(when)} → 替换符合条件的一行'
    if 'delete' in rule:
        return f'{_describe_when(when)} → 删掉符合条件的一行'
    return _short(json.dumps(rule, ensure_ascii=False))


def _describe_when(when) -> str:
    if not when:
        return '每一拍'
    parts = []
    if when.get('change'):
        parts.append('立绘变化' if when['change'] == 'sprite' else
                     f'立绘变化（{when["change"]}）')
    if when.get('show'):
        parts.append('立绘出现' if when['show'] == 'sprite' else
                     f'立绘出现（{when["show"]}）')
    if when.get('hide'):
        parts.append('立绘退场' if when['hide'] == 'sprite' else
                     f'立绘退场（{when["hide"]}）')
    if 'say' in when:
        parts.append('有台词' if when['say'] else '没有台词')
    for key in _UNEXPRESSIBLE_WHEN:
        if key in when:
            parts.append(f'{key}={when[key]!r}')
    if when.get('transition') == 'none':
        parts.append('且这一拍还没有转场')
    return ' / '.join(parts) or '每一拍'


def _short(text: str, limit: int = 72) -> str:
    one = ' '.join(str(text or '').split())
    return one if len(one) <= limit else one[:limit] + '…'


def _mode_of(when: dict, placement: str) -> str:
    """
    `when + placement` → 「什么时候加」的那个值；认不出 → `''`。

    单独一个函数，是因为这里有一条**很窄**的判据：除了 `transition` /
    `code_not_contains` 这两个界面自己的开关，多出任何一个条件就宁可返回空
    （让调用方说"这条界面改不了"），也**不把复杂规则渲染成一个看着像、改了
    就变味的表单**。窄判据放自己一处，`read_form` 才读得下去。
    """
    for key, (want_when, want_place) in _MODE_RULES.items():
        if want_place != placement:
            continue
        if any(when.get(k) != v for k, v in want_when.items()):
            continue
        extra = set(when) - set(want_when) - {'transition', 'code_not_contains'}
        if extra:
            continue
        return key
    return ''


def read_form(rule) -> tuple:
    """
    规则 dict → `(表单, 原因)`：能编辑就给表单，不能就给 `(None, 人话原因)`。

    **绝不硬凑**：`replace`/`delete`/带章名或说话人条件的规则一律"只读"，
    宁可在界面上说"这条改不了"，也不要把一条复杂规则渲染成一个看着像、
    其实改了就变味的表单。
    """
    if not isinstance(rule, dict):
        return None, '这条不是规则对象'
    if 'insert' not in rule:
        op = '替换' if 'replace' in rule else ('删除' if 'delete' in rule else '未知')
        return None, f'这是一条「{op}」规则，界面只支持"加一行"'
    when = dict(rule.get('when') or {})
    extra = [k for k in when if k in _UNEXPRESSIBLE_WHEN]
    if extra:
        return None, ('这条带了界面表达不了的条件：' + '、'.join(extra)
                      + '（例如"只处理某一章/某个人"）')
    if 'hide' in when:
        return None, '这条只盯"退场"，界面里没有对应的选项'
    body = dict(rule.get('insert') or {})
    placement = str(body.get('placement') or 'after_last_show')
    mode = _mode_of(when, placement)
    if not mode:
        return None, f'这条的"加在哪"（{placement}）界面里没有对应选项'
    text = body.get('text')
    text = '\n'.join(text) if isinstance(text, list) else str(text or '')
    form = dict(DEFAULT_FORM)
    form['id'] = str(rule.get('id') or '')
    form['mode'] = mode
    form['skip_existing_transition'] = (
        when.get('transition') == 'none'
        or when.get('code_not_contains') == 'renpy.transition')
    form['skip_if_present'] = bool(body.get('skip_if_present', True))
    (form['cue'], form['effect'], form['seconds'], form['layer'],
     form['always'], form['named'], form['raw']) = _read_text(text)
    return form, ''


def _read_text(text: str) -> tuple:
    """一行代码 → `(cue, effect, seconds, layer, always, named, raw)`。"""
    one = ' '.join(str(text or '').split())
    m = _CALL_RE.match(one)
    if not m:
        return 'raw', DEFAULT_FORM['effect'], DEFAULT_FORM['seconds'], \
            DEFAULT_FORM['layer'], False, '', text
    args = _split_args(m.group(1))
    if not args:
        return 'raw', DEFAULT_FORM['effect'], DEFAULT_FORM['seconds'], \
            DEFAULT_FORM['layer'], False, '', text
    first = args[0]
    kw = {}
    for part in args[1:]:
        key, _sep, val = part.partition('=')
        if _sep:
            kw[key.strip()] = val.strip().strip('"\'')
    always = kw.get('always', '').lower() in ('true', '1')
    layer = kw.get('layer', '')
    eff = _EFFECT_RE.match(first)
    if eff:
        cue = 'full_transition' if not layer else 'master_transition'
        return cue, eff.group(1), eff.group(2), layer or DEFAULT_FORM['layer'], \
            always, '', ''
    if layer:
        return 'named', DEFAULT_FORM['effect'], DEFAULT_FORM['seconds'], layer, \
            always, first, ''
    return 'raw', DEFAULT_FORM['effect'], DEFAULT_FORM['seconds'], \
        DEFAULT_FORM['layer'], always, '', text


def _split_args(body: str) -> list:
    """顶层逗号切开（括号/引号里的逗号不算）。"""
    out, buf, depth, quote = [], [], 0, ''
    for ch in str(body or ''):
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = ''
            continue
        if ch in '"\'':
            quote = ch
            buf.append(ch)
        elif ch in '([{':
            depth += 1
            buf.append(ch)
        elif ch in ')]}':
            depth -= 1
            buf.append(ch)
        elif ch == ',' and depth == 0:
            out.append(''.join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    tail = ''.join(buf).strip()
    if tail:
        out.append(tail)
    return [x for x in out if x]


# ----------------------------------------------------------------------
#  读 / 写 规则文件（写只有一条路：core.atomicio）
# ----------------------------------------------------------------------

def load_rules(path) -> list:
    """
    读规则文件 → `[{'raw': 规则 dict, 'form': 表单或 None, 'why': 原因}]`。

    严格（任一条不合法整份抛）与 `batch_edit.load_rule_file` 同一口径 ——
    坏文件不许"半读半改"，那是把用户的规则改坏的经典路径。
    """
    p = Path(path)
    try:
        data = json.loads(p.read_text(encoding='utf-8-sig'))
    except OSError as exc:
        raise BatchRuleError(f'读不了规则文件：{p}（{exc}）') from exc
    except UnicodeError as exc:
        raise BatchRuleError(f'规则文件不是 UTF-8：{p}（{exc}）') from exc
    except ValueError as exc:
        raise BatchRuleError(f'规则文件不是合法 JSON：{p}（{exc}）') from exc
    if not isinstance(data, dict) or str(data.get('schema') or '') != SCHEMA:
        raise BatchRuleError(f'规则文件 schema 必须是 {SCHEMA!r}：{p}')
    raw_rules = data.get('rules')
    if not isinstance(raw_rules, list) or not raw_rules:
        raise BatchRuleError(f'规则文件里没有 rules：{p}')
    seen = set()
    out = []
    for i, raw in enumerate(raw_rules):
        rule = parse_rule(raw, f'{p.name} rules[{i}]')      # 唯一校验
        if rule.id in seen:
            raise BatchRuleError(f'规则 id 重复：{rule.id!r}（{p}）')
        seen.add(rule.id)
        form, why = read_form(raw)
        out.append({'raw': dict(raw), 'form': form, 'why': why})
    return out


def dump_rules(items) -> str:
    """
    `load_rules` 的那种列表 → JSON 文本（**可编辑的用表单重算，其余原样**）。

    这条规矩是为了"界面没显示 ≠ 可以删掉"：人在界面上改了第一条，
    第二条手写的 `replace` 规则必须一个字节不少地跟着走。
    """
    rules = []
    for item in items or ():
        form = (item or {}).get('form')
        raw = (item or {}).get('raw')
        if form is not None:
            rules.append(build_rule(form, rule_id=str(raw.get('id') or '')
                                    if isinstance(raw, dict) else ''))
        elif isinstance(raw, dict):
            rules.append(raw)
        else:
            raise BatchRuleError('规则项既没有表单也没有原文')
    if not rules:
        raise BatchRuleError('一条规则都没有 —— 至少留一条再保存')
    ids = [str(r.get('id') or '') for r in rules]
    dup = {i for i in ids if ids.count(i) > 1}
    if dup:
        raise BatchRuleError(f'规则名字重复：{sorted(dup)}')
    return json.dumps({'schema': SCHEMA, 'rules': rules},
                      ensure_ascii=False, indent=2) + '\n'


def save_rules(path, items) -> Path:
    """把规则写回文件（原子写 + 多代备份；**不会**在失败时留下半个文件）。"""
    from storygraph.core.atomicio import write_text_atomic

    text = dump_rules(items)
    return write_text_atomic(path, text, backup=True)


def next_id(items) -> str:
    """给"新建"取一个不撞车的名字（`规则1`、`规则2`…；人也能改）。"""
    used = {(item or {}).get('raw', {}).get('id') for item in items or ()}
    n = 1
    while f'规则{n}' in used:
        n += 1
    return f'规则{n}'


__all__ = [
    'BUILTIN_EFFECTS', 'CUE_KINDS', 'DEFAULT_FORM', 'LAYERS', 'MODES',
    'build_rule', 'build_text', 'describe', 'dump_rules', 'label_of',
    'load_rules', 'next_id', 'read_form', 'save_rules',
]
