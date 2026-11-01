# -*- coding: utf-8 -*-
"""
storygraph.assets.mapping_edit
==============================
**把一份映射当"键值对表格"改**（用户 2026-10-30，⑧）

用户原话：

> json的自定义能不能用工具显式化……？本质上它就是键值对嘛！也就是类似于之前
> 探测未定义角色名称的样子，弄一个显化的UI，然后点击保存的话再存为json……？
> 因为实际上使用json定义对非程序员门槛有点高。

## 它是什么 / 不是什么

它是**界面背后的那张表**：纯逻辑、不碰 Qt。界面
（`ui_qt/mapping_editor.py`）只负责把 `Row` 摆出来、把人的输入交回来。

它**不做判定**：一条图是什么仍然是 `assets/asset_identity.resolve_image` 的
事，这份表只是"人写的覆盖"。所以这里不 import 任何 UI 框架（契约 §2）。

## 六段 = 四种形状，但都是"键 → 值"

| 段 | 形状 | 第一列 | 其余列 |
|---|---|---|---|
| `characters` | `{tag: {display, aliases, avatar}}` | 角色 tag | 显示名 / 别名 / 头像 |
| `images`     | `{资产ID或相对路径: {kind, char, attr}}` | 这张图 | 是什么 / 角色 / 属性 |
| `rules`      | `[{glob, kind, char, attr}]`（**顺序有意义**） | 第几条 | 路径通配 / 是什么 / 角色 / 属性 |
| `outfit_codes` | `[{char, code, attr, label}]`（顺序有意义） | 第几条 | 角色 / 服装代号 / 剧本里怎么写 / 说明 |
| `expression_codes` | `[{char, no, attr, label}]`（顺序有意义） | 第几条 | 角色 / 表情编号 / 剧本里怎么写 / 说明 |
| `attributes` | `{维度: {label}}` | 维度名 | 显示名 |

后两段是 2026-10-31 补的：用户要的是**美术需求表的写法**（`B7` = 服装 B +
第 7 号表情），而不是"一堆属性名"。它们只管换算，不参与认图；
换算本身在 `assets/sprite_codes.py`（`translate`）。

用户说"本质上它就是键值对"——对，所以他看到的就是**一张能打的表**，
不是一个要他敲大括号的文本框。

## 三条硬规矩（都为了"别改坏人的文件"）

1. **只动它认识的键**：`self._data` 是原文件的深拷贝，改单元格只改那一个
   字段；不认识的顶层键 / 条目字段**原样留着**（`validate_mapping` 对它们
   只警告，这里更没有理由删）。`to_data()` 的 diff 因此只会出现人真改的行。
2. **空行不落盘**：界面里点「新增一行」会先摆一行空壳（默认键 `新角色` /
   `新图片` / `新分组`）。人什么都没填就不该在 JSON 里留 `"新角色": {}` ——
   `to_data()` 会把"默认键 + 一个字段都没有"的行丢掉，**并且这件事界面必须
   说出来**（`blank_rows()` 给它数）。人自己改了键的空行**不丢**（那是他的
   决定）。
3. **不偷偷补 schema**：`to_data()` 只在"确有内容但没有 schema"时补上
   `storygraph.mapping/1`（否则 `validate_mapping` 每次都要警告一句"缺
   schema"，人还以为自己的文件有问题）。空表不补。

## 兼容写法

映射条目里 `tag` 是 `char` 的旧写法、`attrs` 是 `attr` 的旧写法
（`asset_mapping.identity_from_entry` 两者都认）。改这一格时必须改**原来
那个键**，不能再加一个 —— 否则一条里同时出现 `char` 和 `tag`，谁赢看
读取顺序，等于制造歧义。
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass

from storygraph.assets import vocabulary as V
from storygraph.assets.asset_identity import KINDS
from storygraph.assets.sprite_codes import (SECTION_EXPRESSIONS, SECTION_OUTFITS)

#: 映射的结构版本（与 `asset_mapping.MAPPING_SCHEMA` 同一个值；这里不 import
#: 它，免得 assets 内部两个模块互相 import 绕成环）。
MAPPING_SCHEMA = 'storygraph.mapping/1'

#: 能编辑的六段（顺序就是界面里的顺序：先人，再图，再批量规则，
#: 再"代号"两段，最后显示名）。
SECTIONS = ('characters', 'images', 'rules', SECTION_OUTFITS,
            SECTION_EXPRESSIONS, 'vocabulary', 'attributes')

#: 词汇表那一格的"是什么"下拉：`(显示文字, 值, 悬停解释)` —— 从
#: `vocabulary.KIND_SPECS` 现算，**只有一个真源**（加一种 = 那边加一条）。
VOCAB_CHOICES = tuple((spec.label, key, spec.hint)
                      for key, spec in V.KIND_SPECS.items())

#: `aliases` 这一格的分隔符（角色表里惯用 `|`，也认中文逗号/顿号）。
_LIST_SEP = re.compile(r'[|,，、;；]+')


@dataclass(frozen=True)
class Field:
    """一列：JSON 里的一个键 + 人话表头。"""

    key: str
    label: str
    kind: str = 'text'          # text / choice / list
    hint: str = ''
    #: 旧写法（`tag` / `attrs`）：改这一格时改**它**，不另加一个键。
    alias: str = ''
    #: 表格里的初始列宽（像素）。只影响观感，不参与任何判定。
    width: int = 130
    #: 给开发者的"技术写法"（JSON 里这一格叫什么），只在 tooltip 的第二行出现。
    tech: str = ''
    #: `kind='choice'` 时这一列的下拉 `(显示文字, 写进文件的值, 悬停解释)`；
    #: 空 = 用默认的"这是哪种图"那套（见 `mapping_editor.KIND_CHOICES`）。
    choices: tuple = ()


@dataclass(frozen=True)
class Section:
    """一段（`characters` / `images` / `rules` / `attributes`）。"""

    name: str
    label: str
    hint: str
    key_label: str
    fields: tuple
    #: 数组段（`rules`）：**顺序有意义**（先中先得），所以能上移下移。
    ordered: bool = False
    #: 「新增一行」时先给的默认键（空行不落盘，见模块 docstring 规矩 2）。
    auto_key: str = ''
    #: 给开发者的"技术写法"（这一段在 JSON 里的键名）。
    tech: str = ''
    #: 这一段空着的时候，界面该说什么（"怎么开始"）。
    empty: str = ''


@dataclass(frozen=True)
class Problem:
    """一行（或一整段）的问题。`bad` = 这条不会生效；`warn` = 可能是笔误。"""

    section: str
    index: int          # 行号；-1 = 整段
    level: str          # 'bad' / 'warn'
    text: str


@dataclass(frozen=True)
class Row:
    """界面要摆的一行（**不可变**，每次 `rows()` 现算）。"""

    section: str
    index: int
    key: str
    values: tuple
    #: 这一条里不认识的键（原样保留，这里不显示也不能改）。
    unknown: tuple = ()
    #: 这一条不是对象（`"x": "cg"` 这种简短写法）：界面改不了，只能去 JSON 页。
    broken: bool = False


#: 六段的规格。新增一段 = 这里加一条 + `SECTIONS` 里加名字。
#:
#: ⚠ **这里每一个字都是给编剧 / 文案看的**（用户 2026-10-30：
#: "tag 一类的……谁知道你是怎么叫的呢……我需要一个编剧跟文案都能看明白的表！"）：
#:   · `label` / `key_label` / `Field.label` 是**屏幕上直接显示的**表头 ——
#:     不许出现 tag / ID / glob / kind / attr / 键 / 值 / 通配 / 维度 / schema；
#:   · `hint` 是表上方那一句"这段是干什么的、怎么填"，说人话，给例子；
#:   · 机器那边叫什么，写在 `tech` 里，只进 tooltip（开发者要查时能看到）。
#: 判据：`tests/test_mapping_edit.py::test_no_jargon_in_the_table`。
SPECS = {
    'characters': Section(
        name='characters', label='角色',
        tech='characters（角色表）',
        hint='谁是谁：一行一个角色。左边照剧本里的写法抄（例如 ling），'
             '右边写给人看的名字（例如 凌跃捷）。'
             '留空也照样显示资源表里的名字 —— 写了就以这里为准。',
        key_label='剧本里的写法',
        auto_key='新角色',
        empty='还没读到角色表。留空也行：屏幕上显示的就是剧本里那串写法。'
              '想给它换个名字，点下面「新增一行」写一行；'
              '项目里有角色表时，这一段会直接告诉你表里有谁。',
        fields=(
            Field('display', '名字', hint='给人看的名字：剧本和界面上显示的就是它',
                  width=130),
            Field('aliases', '别的写法', kind='list', width=170,
                  hint='同一个人可能被写成「凌」或「lyj」：一行一个，或用逗号隔开',
                  tech='aliases（别名，一组文字）'),
            Field('avatar', '头像图', hint='这个角色头像的图片路径；可以留空',
                  width=170),
        )),
    'images': Section(
        name='images', label='单张图片',
        tech='images（一张图一条）',
        hint='先认这一张：左边写这张图的文件路径（或资源表里那一行的编号），'
             '右边选它是哪种图。这里写过的图，比下面「一类图片」的说法优先。',
        key_label='是哪张图',
        auto_key='新图片',
        empty='还没有单独指定的图 —— 这一段只写"工具猜错了的那几张"。'
              '去「工具怎么认的」页勾上「只看未识别」，选中它们、点「加进表里…」，'
              '再回这里补类型和角色。猜对的不写也能用。',
        fields=(
            Field('kind', '它是哪种图', kind='choice', width=130,
                  hint='立绘？背景？界面？留空就让工具按文件夹和文件名自己猜',
                  tech='kind（可写 sprite / bg / cg / ui …）'),
            Field('char', '属于谁', alias='tag', width=110,
                  hint='这张图是哪个角色的（立绘、预览图要填；背景/CG 可留空）',
                  tech='char（旧写法 tag）'),
            Field('attr', '表情／状态', alias='attrs', width=140,
                  hint='比如 happy、生气、泳装；写剧本里 show 后面那个词',
                  tech='attr（旧写法 attrs）'),
        )),
    'rules': Section(
        name='rules', label='一类图片',
        tech='rules（从上往下，先对上的算数）',
        hint='一次认一批：写清"哪个文件夹里的图都算什么"，从上往下，先对上的算数。'
             '一般不用手写 ——「导出映射初稿」会按文件夹自动生成一份。',
        key_label='顺序',
        ordered=True,
        empty='还没有批量规则。留空也行 —— 工具照文件夹自己猜。'
              '想让一批图统一算成同一种，点上方「导出映射初稿」会按文件夹'
              '自动生成一份，再在这里改。',
        fields=(
            Field('glob', '文件在哪儿', width=250,
                  hint='例：game/art/full/ling/*.png；* = 这一层任意文件名，'
                       '** = 连子文件夹一起',
                  tech='glob（路径通配）'),
            Field('kind', '它是哪种图', kind='choice', width=130,
                  tech='kind（可写 sprite / bg / cg / ui …）'),
            Field('char', '属于谁', alias='tag', width=110,
                  hint='这一类图都是哪个角色的；可以写 <tag> 表示"从文件名里取"',
                  tech='char（可以用 <tag> 占位）'),
            Field('attr', '表情／状态', alias='attrs', width=130,
                  hint='可以写 <stem> 表示"取文件名、去掉扩展名"',
                  tech='attr（可以用 <stem> 占位）'),
        )),
    SECTION_OUTFITS: Section(
        name=SECTION_OUTFITS, label='服装代号',
        tech='outfit_codes（一条一身衣服：字母 → 剧本里的写法）',
        hint='美术需求表里「B 是哪身衣服」就写在这儿：一行一身衣服，'
             '左边给字母，右边写剧本里那个词（例如 base_swimsuit）。'
             '填了它，输入「角色 + 衣服 + 表情」就能换成 B7 这样的代号。',
        key_label='序号',
        ordered=True,
        empty='还没有服装代号。「按项目自动填」会照着剧本注释数一遍 —— '
              '剧本里写过的 `# 表情B7`，它就去数 B 是哪身衣服。'
              '数不出来的留空，照着美术需求表补上字母就行。',
        fields=(
            Field('char', '角色', width=120,
                  hint='这身衣服是谁的（剧本里 show 后面那个写法）'),
            Field('code', '代号', width=70,
                  hint='美术需求表给的字母：A / B / C……一个角色一套',
                  tech='code（一个字母）'),
            Field('attr', '剧本里怎么写', width=180,
                  hint='剧本里 show 后面那个词，例如 base_swimsuit；'
                       '写着 aa 的表示"这身衣服不额外写"',
                  tech='attr（Ren\'Py 的属性名）'),
            Field('label', '说明', width=260,
                  hint='给人看的说法（泳装 / 校服），也顺手记一句"凭什么这么填"'),
        )),
    SECTION_EXPRESSIONS: Section(
        name=SECTION_EXPRESSIONS, label='表情编号',
        tech='expression_codes（一条一个表情：编号 → 剧本里的写法）',
        hint='「7 号是哪个表情」：一行一个表情，左边是编号，右边是剧本里那个词。'
             '这张表一般不用手填 ——「按项目自动填」会直接读角色文件夹里的'
             '对应.txt（有几份读几份），再拿剧本注释核对一遍。',
        key_label='序号',
        ordered=True,
        empty='还没有表情编号。「按项目自动填」会去 game/images/characters/ '
              '下面每个角色文件夹里找对应.txt（或中英对应.txt），读进来。',
        fields=(
            Field('char', '角色', width=120,
                  hint='这个表情是谁的（剧本里 show 后面那个写法）'),
            Field('no', '编号', width=70,
                  hint='美术需求表给的第几号表情',
                  tech='no（数字，存成文字）'),
            Field('attr', '剧本里怎么写', width=180,
                  hint='剧本里 show 后面那个词，例如 indifferent',
                  tech='attr（Ren\'Py 的属性名）'),
            Field('label', '说明', width=260,
                  hint='中文叫法（不在意 / 生气），也是"这一条从哪来的"'),
        )),
    'vocabulary': Section(
        name='vocabulary', label='中文叫法',
        tech='vocabulary（一条一个"引擎写法 → 人话"）',
        hint='想让屏幕上出现「1人，近处」而不是 i11？想把 BGM 显示成 18 号？'
             '一行一条：左边说它是哪一类东西，中间写剧本/资源表里的原名，'
             '右边写你想看到的说法和编号。**查不到的照旧显示原名**。',
        key_label='序号',
        ordered=True,
        empty='还没有中文叫法。留空就照原样显示剧本/资源表里那串写法'
              '（i11、bg9_2、bgm/嗯？.mp3）。想让屏幕说人话，一行登记一条：'
              '位置、音乐、背景、角色、过场调用五种都在这儿。'
              '服装/表情在上面两段填。',
        fields=(
            Field('kind', '是什么', kind='choice', width=110,
                  choices=VOCAB_CHOICES,
                  hint='这一条是哪一类东西 —— 决定它会在哪个界面生效',
                  tech='kind（transform / music / sound / voice / bg / '
                       'character / cue / costume / expression）'),
            Field('id', '原名', width=220,
                  hint='剧本或资源表里本来就有的那串字（例如 i11、'
                       'bgm/嗯？.mp3、bg9_2、DY、my_wipe）',
                  tech='id（写进剧本的真值）'),
            Field('label', '叫什么', width=150,
                  hint='你想在屏幕上看到的说法（例如 1人，近处 / 大爷 / 闪白）',
                  tech='label（只影响屏幕）'),
            Field('code', '编号', width=90,
                  hint='美术/音乐表里的编号（18 / bg9.2 / B）；和"叫什么"'
                       '都填了就显示「18 嗯？」',
                  tech='code（只影响屏幕）'),
            Field('char', '角色', width=110,
                  hint='只有服装/表情要填：这一条是谁的',
                  tech='char（服装/表情的作用域）'),
            Field('note', '备注', width=200,
                  hint='给人看的一句话（从哪来的、别丢）'),
        )),
    'attributes': Section(
        name='attributes', label='表情怎么叫',
        tech='attributes（显示名，只影响界面）',
        hint='这一栏只改界面上怎么称呼、不影响剧本：左边是工具内部的分组名'
             '（例如 expressions），右边是界面上给它起的名字（例如 表情）。',
        key_label='分组（工具内部）',
        auto_key='新分组',
        empty='还没有自定义叫法。工具默认就把表达情绪的那一组叫「表情」—— '
              '空着就行，想换个名字再在这里加一行。',
        fields=(
            Field('label', '界面上叫它', hint='界面上这一组属性显示成什么',
                  width=140),
        )),
}


# ---------------------------------------------------------------------------
#  值 <-> 字符串
# ---------------------------------------------------------------------------

def split_list(text) -> list:
    """`凌|lyj` → `['凌', 'lyj']`（保序去重；空项丢掉）。"""
    if isinstance(text, (list, tuple)):
        words = [str(x) for x in text]
    else:
        words = _LIST_SEP.split(str(text or ''))
    out = []
    for word in words:
        word = str(word or '').strip()
        if word and word not in out:
            out.append(word)
    return out


def display_value(kind: str, raw) -> str:
    """
    工作副本里的值 → 单元格里显示的文字（**只读**，不改数据）。

    `list` 类字段（`aliases`）用 ` | ` 连起来 —— 与角色表 `Aliases` 一格的
    写法一致，人不用学第二种分隔符。
    """
    if raw is None:
        return ''
    if kind == 'list':
        return ' | '.join(split_list(raw))
    if isinstance(raw, (list, tuple)):
        return ' '.join(str(x).strip() for x in raw if str(x).strip())
    if isinstance(raw, dict):
        return ' '.join(f'{k}={v}' for k, v in raw.items())
    return str(raw).strip()


def value_for(kind: str, text):
    """单元格里的文字 → 写回 JSON 的值（`list` 给数组，其余给字符串）。"""
    text = str(text or '').strip()
    if kind == 'list':
        return split_list(text)
    return text


def choice_text(field, raw) -> str:
    """
    下拉列：文件里的值 → 屏幕上那几个字（`transform` → 位置 / 镜头）。

    自己带 `choices` 的列（词汇表的"是什么"）用自己那套；其余交给界面上的
    `KIND_TEXT`（那是"图片类别"专用的）。认不出返回 `''`，由调用方兜底
    **原样显示** —— 人手改的 JSON 不该在屏幕上变成空白。
    """
    want = str(raw if raw is not None else '').strip()
    for choice in getattr(field, 'choices', ()) or ():
        if len(choice) >= 2 and str(choice[1]) == want:
            return str(choice[0])
    return ''


# ---------------------------------------------------------------------------
#  编辑模型
# ---------------------------------------------------------------------------

class MappingEdit:
    """
    一份映射的可编辑工作副本。

    用法（界面）::

        edit = MappingEdit(state.data)
        edit.set_field('images', 0, 'kind', 'cg')
        data = edit.to_data()      # 交给 `asset_mapping.save_mapping`

    **每次 `rows()` 都现算**（不缓存）：映射最多几百条，重建一次表比重算
    一整个 `identity_map` 便宜得多；缓存反而会在"改了一格但忘了失效"时
    显示旧值。
    """

    def __init__(self, data=None):
        source = data if isinstance(data, dict) else {}
        self._data = copy.deepcopy(source)
        #: 原本就存在但**是空的**段（`"images": {}`）：人没动它就别在保存时
        #: 把这把钥匙删掉 —— 那不是人改的，是历史遗留，diff 里出现"删了
        #: images"只会让人以为自己弄坏了什么。
        self._initial_empty = {
            name for name in SECTIONS
            if isinstance(self._data.get(name), (dict, list))
            and not self._data[name]}


    # -- 读 ------------------------------------------------------------

    def section_data(self, name: str):
        """这一段的原始内容（字典 / 数组；没有就是空的那个）。"""
        spec = SPECS[name]
        return self._data.setdefault(name, [] if spec.ordered else {})

    def editable(self, name: str) -> bool:
        """
        这一段能不能编辑（结构对不对）。

        文件里 `images` 写成数组这种坏结构不归界面修 —— 让它原样留着、
        在界面上说一句"这段结构不对"，比"界面把它改成 {} 再保存"安全得多。
        """
        value = self._data.get(name)
        if value is None:
            return True
        expected = list if SPECS[name].ordered else dict
        return isinstance(value, expected)

    def counts(self) -> dict:
        """每段几条（界面左栏的 `角色 3` 用它）。"""
        out = {}
        for name in SECTIONS:
            value = self._data.get(name)
            out[name] = len(value) if isinstance(value, (dict, list)) else 0
        return out

    def rows(self, name: str) -> list:
        """这一段的所有行（只读快照）。"""
        spec = SPECS[name]
        value = self._data.get(name)
        entries = []
        if isinstance(value, dict):
            entries = list(value.items())
        elif isinstance(value, list):
            entries = [(None, item) for item in value]
        out = []
        for index, (key, entry) in enumerate(entries):
            if not isinstance(entry, dict):
                # 坏条目（`"x": "cg"` 这种简短写法）不猜测、不显示成"空行"：
                # 摆成一行空壳 + 一句问题，人才知道该回 JSON 页改。
                out.append(Row(name, index, str(key or ''),
                               tuple('' for _ in spec.fields), broken=True))
                continue
            values = tuple(display_value(f.kind, entry.get(self._key_of(entry, f)))
                           for f in spec.fields)
            unknown = tuple(k for k in entry
                            if k not in {f.key for f in spec.fields}
                            and k not in {f.alias for f in spec.fields if f.alias})
            out.append(Row(name, index, '' if key is None else str(key),
                           values, unknown))
        return out

    def entries(self, name: str) -> list:
        """
        这一段的**原始条目**（浅拷贝的 `dict` 列表，界面拿它做去重 / 预览）。

        与 `rows()` 的区别：`rows` 是给人看的（值已经翻成文字），这里是文件里
        原样的字段（`kind` 还是 `bg`）。
        """
        value = self._data.get(name)
        if isinstance(value, dict):
            return [dict(v) for v in value.values() if isinstance(v, dict)
                    and v]
        if isinstance(value, list):
            return [dict(v) for v in value if isinstance(v, dict) and v]
        return []

    def blank_rows(self) -> int:
        """有几行是"点了新增但一个字没填"的空壳（`to_data()` 会丢掉它们）。"""
        return sum(1 for name in SECTIONS for row in self.rows(name)
                   if self._is_blank(name, row.index))

    # -- 改 ------------------------------------------------------------

    def set_field(self, name: str, index: int, field: str, text) -> str:
        """
        改一格。返回**错误文字**（`''` = 改成功）。

        空字符串 = 删掉这个键（而不是留一个 `""`）—— 读的一方对"没写这个键"
        和对"写了空串"其实一样，但写进文件里 `"kind": ""` 会让人以为有问题。
        """
        if not self.editable(name):
            return '这一段的结构不对，先到「原始文件」页改对。'
        target = self._field(name, field)
        if target is None:
            return f'不认识的列：{field}'
        entry = self._entry(name, index)
        if not isinstance(entry, dict):
            return '这一行的内容不是对象，改不了（请在 JSON 页改）。'
        key = self._key_of(entry, target)
        value = value_for(target.kind, text)
        if value == [] or value == '':
            entry.pop(key, None)
        else:
            entry[key] = value
        return ''

    def set_key(self, name: str, index: int, text) -> str:
        """改第一列（`rules` 没有键 → 直接返回错误）。返回错误文字，`''` = 成功。"""
        spec = SPECS[name]
        if spec.ordered:
            return '规则没有名字，靠上下顺序决定谁先中 —— 用「上移 / 下移」。'
        if not self.editable(name):
            return '这一段的结构不对，先到「原始文件」页改对。'
        container = self._data.get(name)
        if not isinstance(container, dict):
            return '这一段没有内容。'
        keys = list(container)
        if not 0 <= index < len(keys):
            return '这一行已经没了（列表刚刷新过）。'
        old = keys[index]
        new = str(text or '').strip()
        if not new:
            return '键不能为空。'
        if new == old:
            return ''
        if new in container:
            return f'已经有「{new}」了 —— 两个同名条目会互相盖掉。'
        items = [(new if k == old else k, v) for k, v in container.items()]
        container.clear()
        container.update(items)
        return ''

    def add(self, name: str, key='', fields=None) -> int:
        """
        加一行，返回它的行号（失败 -1）。

        键已经存在时**不新建**，把 `fields` 并到那一条上并返回它 ——
        「把对照表选中的图补进映射」点两次不该出现两条一样的。
        """
        spec = SPECS[name]
        if not self.editable(name):
            return -1
        fields = dict(fields or {})
        container = self.section_data(name)      # 没有这一段就现建一个
        if spec.ordered and not isinstance(container, list):
            return -1
        if not spec.ordered and not isinstance(container, dict):
            return -1
        if spec.ordered:
            container.append(fields)
            return len(container) - 1
        wanted = str(key or '').strip() or self._unique_key(name, spec.auto_key)
        if wanted in container:
            entry = container[wanted]
            if isinstance(entry, dict):
                entry.update(fields)
            return list(container).index(wanted)
        container[wanted] = fields
        return list(container).index(wanted)

    def remove(self, name: str, indices) -> int:
        """删掉这些行（返回真删掉几条）。"""
        if not self.editable(name):
            return 0
        wanted = sorted({int(i) for i in indices if int(i) >= 0}, reverse=True)
        container = self._data.get(name)
        removed = 0
        if isinstance(container, dict):
            keys = list(container)
            for index in wanted:
                if 0 <= index < len(keys):
                    container.pop(keys[index], None)
                    removed += 1
        elif isinstance(container, list):
            for index in wanted:
                if 0 <= index < len(container):
                    del container[index]
                    removed += 1
        return removed

    def move(self, name: str, index: int, delta: int) -> int:
        """把第 `index` 行挪 `delta` 格（返回新行号；没动就原样返回）。"""
        if not self.editable(name):
            return index
        container = self._data.get(name)
        target = index + int(delta)
        if isinstance(container, dict):
            keys = list(container)
            if not (0 <= index < len(keys) and 0 <= target < len(keys)):
                return index
            items = list(container.items())
            items.insert(target, items.pop(index))
            container.clear()
            container.update(items)
            return target
        if isinstance(container, list):
            if not (0 <= index < len(container) and 0 <= target < len(container)):
                return index
            container.insert(target, container.pop(index))
            return target
        return index

    def count_references(self, name: str, key: str) -> int:
        """
        `characters` 改了键之后，还有几条 `images` / `rules` 写着旧键。

        **不改它们**：映射里的 `char` 可能是有意的（指向不存在的角色，
        工具只是不认），自动跟着改等于替人做语义决定（铁律 9）。
        """
        if name != 'characters':
            return 0
        old = str(key or '').strip()
        if not old:
            return 0
        hits = 0
        for section in ('images', 'rules'):
            value = self._data.get(section)
            entries = value.values() if isinstance(value, dict) else (
                value if isinstance(value, list) else ())
            for entry in entries:
                if isinstance(entry, dict) and str(
                        entry.get('char') or entry.get('tag') or '').strip() == old:
                    hits += 1
        return hits

    # -- 结果 ----------------------------------------------------------

    def to_data(self) -> dict:
        """
        工作副本 → 可以写盘的字典（丢空壳行、必要时补 schema）。

        · 原本就空的段（`"images": {}`）原样保留；
        · 人把条目删光的段**不再留着一个空对象**（`"rules": []` 只会让人以为
          还有东西）；
        · 空壳行（默认键 + 一个字段都没有）丢掉，`blank_rows()` 报给界面。
        """
        data = copy.deepcopy(self._data)
        for name in SECTIONS:
            spec = SPECS[name]
            value = data.get(name)
            if isinstance(value, dict):
                kept = {k: v for k, v in value.items()
                        if not self._is_blank_entry(spec, k, v)}
                if kept or name in self._initial_empty:
                    data[name] = kept
                else:
                    data.pop(name, None)
            elif isinstance(value, list):
                kept = [v for v in value if not _is_empty(v)]
                if kept or name in self._initial_empty:
                    data[name] = kept
                else:
                    data.pop(name, None)
        content = {k: v for k, v in data.items() if k != 'schema' and v}
        if content and 'schema' not in data:
            data = {'schema': MAPPING_SCHEMA, **data}
        return data

    # -- 自检 ----------------------------------------------------------

    def problems(self) -> list:
        """
        一眼能看出的问题（界面按行标色）。**不拦保存** —— 与
        `asset_mapping.validate_mapping` 同一个口径：坏条目只提示，因为
        "这一条用不上"和"整份文件不能写"是两件事。
        """
        out = []
        seen_glob = {}
        for name in SECTIONS:
            if not self.editable(name):
                out.append(Problem(name, -1, 'bad',
                                   f'文件里的「{SPECS[name].label}」那段写法不对'
                                   f'（应该是一组对象）。到「原始文件」页改，'
                                   f'或删掉那段让工具重新生成。'))
                continue
            for row in self.rows(name):
                # 点了「新增一行」还没填的空壳不算问题（它本来就不落盘）。
                if self._is_blank(name, row.index):
                    continue
                out.extend(self._row_problems(name, row))
                if name == 'rules' and row.values and str(row.values[0]).strip():
                    glob = str(row.values[0]).strip()
                    if glob in seen_glob:
                        out.append(Problem(name, row.index, 'warn',
                                           f'和上面第 {seen_glob[glob] + 1} 条的'
                                           f'「文件在哪儿」一模一样 —— 从上往下'
                                           f'先对上的算数，这一条永远不会生效。'))
                    else:
                        seen_glob[glob] = row.index
        return out

    # -- 内部 ----------------------------------------------------------

    def _row_problems(self, name: str, row: Row) -> list:
        spec = SPECS[name]
        if row.broken:
            return [Problem(name, row.index, 'bad',
                            '这一条在文件里被写成了短写法（不是对象）—— '
                            '这里改不了，去「原始文件」页改。')]
        values = dict(zip((f.key for f in spec.fields), row.values))
        out = []
        if not row.key.strip() and not spec.ordered:
            out.append(Problem(name, row.index, 'bad',
                               f'这一行还没写「{spec.key_label}」。'))
        elif not spec.ordered and row.key == spec.auto_key:
            out.append(Problem(name, row.index, 'warn',
                               f'还是默认的「{spec.auto_key}」—— 换成剧本里'
                               f'真正用的那个写法，别人才知道它指谁。'))
        if name == 'characters':
            if not values.get('display', '').strip():
                out.append(Problem(name, row.index, 'warn',
                                   '没填「名字」—— 界面上只能看到那串英文写法。'))
        elif name == 'images':
            kind = values.get('kind', '').strip()
            if kind and kind not in KINDS:
                out.append(Problem(name, row.index, 'bad',
                                   f'「它是哪种图」不是认识的类别：{kind}'))
            elif not kind:
                out.append(Problem(name, row.index, 'warn',
                                   '没选「它是哪种图」—— 工具会按文件夹和'
                                   '文件名自己猜。'))
            if kind == 'sprite' and not values.get('char', '').strip():
                out.append(Problem(name, row.index, 'warn',
                                   '立绘没写「属于谁」—— 剧本里的 show '
                                   '找不到它。'))
            if '\\' in row.key:
                out.append(Problem(name, row.index, 'warn',
                                   '路径里用了反斜杠；这里统一写 /（斜杠），'
                                   '换台机器才对得上。'))
        elif name == 'rules':
            glob = values.get('glob', '').strip()
            if not glob:
                out.append(Problem(name, row.index, 'bad',
                                   '没写「文件在哪儿」—— 没有路径，这条用不上。'))
            elif glob.startswith(('/', '\\')) or ':' in glob:
                out.append(Problem(name, row.index, 'warn',
                                   '像是从盘符开始的全路径（绝对路径）—— '
                                   '这里要写项目里的相对路径，换台机器才不失效。'))
            kind = values.get('kind', '').strip()
            if kind and kind not in KINDS:
                out.append(Problem(name, row.index, 'bad',
                                   f'「它是哪种图」不是认识的类别：{kind}'))
        elif name == 'attributes':
            if not values.get('label', '').strip():
                out.append(Problem(name, row.index, 'warn',
                                   '没填「界面上叫它」—— 界面上显示的还是'
                                   '工具内部那个名字。'))
        elif name in (SECTION_OUTFITS, SECTION_EXPRESSIONS):
            out.extend(self._code_problems(name, row, values))
        elif name == 'vocabulary':
            out.extend(self._vocab_problems(row, values))
        return out

    def _vocab_problems(self, row: Row, values) -> list:
        """
        词汇表一行的自检（与 `vocabulary` 模块同口径，**不重复实现**）。

        这一行会不会生效，取决于三件事：种类认不认得、原名有没有写、
        带作用域的种类有没有写角色 —— 三件都在这里说清楚。
        """
        out = []
        kind = values.get('kind', '').strip()
        ident = values.get('id', '').strip()
        char = values.get('char', '').strip()
        name = 'vocabulary'
        if not ident:
            out.append(Problem(name, row.index, 'bad',
                               '没写「原名」—— 这一行对不上任何东西。'))
        if not kind:
            out.append(Problem(name, row.index, 'warn',
                               '没选「是什么」—— 不知道拿它去对哪个界面。'))
        elif not V.is_kind(kind):
            out.append(Problem(name, row.index, 'bad',
                               f'「{kind}」不是认识的种类。'))
        elif V.KIND_SPECS[kind].scoped and not char:
            out.append(Problem(name, row.index, 'warn',
                               f'{V.KIND_SPECS[kind].label}是按角色分的 —— '
                               f'没写「角色」时它只能当通配用。'))
        if not (values.get('label', '').strip()
                or values.get('code', '').strip()):
            out.append(Problem(name, row.index, 'warn',
                               '「叫什么」和「编号」都没填 —— 屏幕上还是'
                               '那串原名，等于没改。'))
        for other in self.rows(name):
            if other.index == row.index:
                continue
            other_values = dict(zip((f.key for f in SPECS[name].fields),
                                    other.values))
            if (other_values.get('kind', '').strip() == kind
                    and other_values.get('id', '').strip() == ident
                    and other_values.get('char', '').strip() == char):
                out.append(Problem(name, row.index, 'warn',
                                   f'和上面第 {other.index + 1} 条一模一样'
                                   f'—— 只有一条会生效。'))
                break
        return out

    def _code_problems(self, name: str, row: Row, values) -> list:
        """
        代号两段的自检：**填不全就等于没填**（换算时查不到），所以缺哪格都说话。

        比"缺了"更值得说的是"重了"：同一个角色两个 B，换算永远只认先出现的
        那个，人不看这张表就永远查不出来。
        """
        out = []
        char = values.get('char', '').strip()
        attr = values.get('attr', '').strip()
        code = values.get('code', '').strip().upper() if name == SECTION_OUTFITS \
            else values.get('no', '').strip()
        if not char:
            out.append(Problem(name, row.index, 'warn',
                               '没写「角色」—— 不知道该算在谁头上。'))
        if not attr:
            out.append(Problem(name, row.index, 'warn',
                               '没写「剧本里怎么写」—— 换算时找不到它。'))
        if not code:
            # 空代号是**自动填的正常结果**（剧本里没数出来）：说清楚它等人补，
            # 比 "这一条只在填了以后才算数" 好照做。
            if name == SECTION_OUTFITS:
                out.append(Problem(name, row.index, 'warn',
                                   '这身衣服还没有代号（剧本注释里数不出来）'
                                   '—— 照美术需求表补一个字母。'))
            else:
                out.append(Problem(name, row.index, 'warn',
                                   '这个表情还没有编号 —— 照美术需求表补一个号。'))
            return out
        if name == SECTION_OUTFITS and not re.fullmatch(r'[A-Z]', code):
            out.append(Problem(name, row.index, 'warn',
                               f'「{code}」不像代号 —— 美术需求表用的是单个'
                               '大写字母（A / B / C）。'))
        mine = [r for r in self.rows(name)
                if r.index != row.index
                and str(r.values[0] if r.values else '').strip() == char]
        for other in mine:
            other_code = (str(other.values[1] if len(other.values) > 1 else '')
                          .strip().upper())
            if other_code != code:
                continue
            out.append(Problem(name, row.index, 'warn',
                               f'和上面第 {other.index + 1} 条重复了：'
                               f'{char} 的「{code}」—— 换算只认先出现的那条。'))
            break
        return out

    def _field(self, name: str, field: str):
        for candidate in SPECS[name].fields:
            if candidate.key == field or candidate.alias == field:
                return candidate
        return None

    @staticmethod
    def _key_of(entry: dict, field: Field) -> str:
        """这一格该读/写哪个键：旧写法（`tag`/`attrs`）优先保持原样。"""
        if field.alias and field.alias in entry and field.key not in entry:
            return field.alias
        return field.key

    def _entry(self, name: str, index: int):
        container = self._data.get(name)
        if isinstance(container, dict):
            keys = list(container)
            return container.get(keys[index]) if 0 <= index < len(keys) else None
        if isinstance(container, list):
            return container[index] if 0 <= index < len(container) else None
        return None

    def _is_blank(self, name: str, index: int) -> bool:
        spec = SPECS[name]
        entry = self._entry(name, index)
        if not isinstance(entry, dict):
            return False
        if spec.ordered:
            # 数组段（规则 / 服装代号 / 表情编号）没有"默认键"这回事：
            # 一个字段都没填的行就是空壳。
            return _is_empty(entry)
        container = self._data.get(name)
        keys = list(container) if isinstance(container, dict) else []
        key = keys[index] if 0 <= index < len(keys) else ''
        return self._is_blank_entry(spec, key, entry)

    @staticmethod
    def _is_blank_entry(spec: Section, key, entry) -> bool:
        if not isinstance(entry, dict) or entry:
            return False
        # 只有"默认键 + 一个字段都没有"才算空壳；人改过键的空行是他的决定。
        return str(key or '').strip() == spec.auto_key

    def _unique_key(self, name: str, base: str) -> str:
        container = self._data.get(name)
        existing = set(container) if isinstance(container, dict) else set()
        if base not in existing:
            return base
        n = 2
        while f'{base}{n}' in existing:
            n += 1
        return f'{base}{n}'


def _is_empty(value) -> bool:
    """空字典 / 空数组（用来丢"点了新增但没填"的规则行）。"""
    return isinstance(value, (dict, list)) and not value
