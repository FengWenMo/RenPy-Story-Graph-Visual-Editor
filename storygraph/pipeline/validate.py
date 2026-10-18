"""
validate.py
===========
三级校验：把"这份演出表/脚本能不能进构建"讲清楚。

分级不是为了好看，是为了对应**三种不同的人**和三种修法：

  L1 · 单条（一行一格）  —— 文案自己能修
      空台词、说话人没定义、引用的资产没登记、音频动作写错、
      文本里的 `{}` 标签不配对、台词超出 UI 显示上限。

  L2 · 表内（一张表内部）—— 策划/组长能修
      LineId 重复、seq 断号、label 缺失、菜单选项没有对应 menu、
      StageOrder 与实际舞台列不一致。

  L3 · 全库（跨章节/跨表）—— 程序 + 主策一起看
      断裂跳转、不可达 label、未使用资产、角色没有台词、
      资源表登记了但文件不存在、多语言缺条目。

设计约束：
  · 只报告，不修改。修由人决定（编辑器里已有对应动作）。
  · 结果可机器消费（Issue 是 dataclass，可 json 化），CI 能卡门。
  · 复用既有真源：资产引用判定一律走 asset_registry.resolve_references，
    不再自建第二套匹配规则。
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from storygraph.assets.asset_registry import (RENPY_BUILTIN_TRANSFORMS,
                                              is_template_path,
                                              resolve_references)
from storygraph.core.vocabulary import ASSET_TYPE_ATTR as _TYPE_ATTR
from storygraph.story.beat_model import (
    BEAT_HEADERS,
    KIND_MENU_OPTION,
    is_literal_speaker,
    split_hide_items,
    split_show_items,
)
# 自定义语句头行的参数扫描：**唯一真源**在 `story/spec_args.py`
# （导出摊列 / 读表折回头行 / 这里的校验三处共用一份；契约 H1 不许
# `story` import `pipeline`，所以真源必须在 `story` 这一层）。
from storygraph.story.spec_args import spec_arg_values

from storygraph.core.safeguard import swallow

L1 = 'L1'
L2 = 'L2'
L3 = 'L3'

ERROR = 'error'
WARNING = 'warning'

# UI 里一句台词的安全长度（超过就要换行/拆页，否则对话框会溢出）。
# 仅作为「提醒」级别（W101），不是硬错误；设为 <=0 即关闭该检查。
DEFAULT_CHAR_LIMIT = 120

_VALID_ACTIONS = {'play', 'queue', 'stop'}

# Ren'Py 文本标签 {b}…{/b}、{color=#fff}、{w}、{nw}、[变量] 等：
# 只检查花括号是否成对，不做完整语法解析（那需要 Ren'Py 自己的解析器）。
_RE_TEXT_TAG = re.compile(r'\{[^{}]*\}|\[[^\[\]]*\]')


@dataclass
class Issue:
    level: str
    code: str
    severity: str
    message: str
    where: str = ''           # 定位（line_id / label / 文件）
    hint: str = ''            # 怎么修

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ValidationReport:
    issues: list = field(default_factory=list)

    @property
    def errors(self) -> list:
        return [i for i in self.issues if i.severity == ERROR]

    @property
    def warnings(self) -> list:
        return [i for i in self.issues if i.severity == WARNING]

    @property
    def ok(self) -> bool:
        return not self.errors

    def by_level(self, level: str) -> list:
        return [i for i in self.issues if i.level == level]

    def extend(self, issues) -> None:
        self.issues.extend(issues)

    def to_dict(self) -> dict:
        return {
            'ok': self.ok,
            'error_count': len(self.errors),
            'warning_count': len(self.warnings),
            'by_level': {lv: len(self.by_level(lv)) for lv in (L1, L2, L3)},
            'issues': [i.to_dict() for i in self.issues],
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    def summary(self) -> str:
        d = self.to_dict()
        head = (f"{'通过' if d['ok'] else '未通过'}："
                f"错误 {d['error_count']}，警告 {d['warning_count']}"
                f"（L1 {d['by_level'][L1]} / L2 {d['by_level'][L2]}"
                f" / L3 {d['by_level'][L3]}）")
        return head

    def format(self, max_per_level: int = 40) -> str:
        lines = [self.summary(), '']
        label = {L1: 'L1 单条', L2: 'L2 表内', L3: 'L3 全库'}
        for lv in (L1, L2, L3):
            issues = self.by_level(lv)
            if not issues:
                continue
            lines.append(f'—— {label[lv]} ——')
            for i in issues[:max_per_level]:
                mark = '✗' if i.severity == ERROR else '!'
                loc = f" [{i.where}]" if i.where else ''
                lines.append(f'  {mark} {i.code}{loc} {i.message}')
                if i.hint:
                    lines.append(f'      → {i.hint}')
            if len(issues) > max_per_level:
                lines.append(f'  …还有 {len(issues) - max_per_level} 条')
            lines.append('')
        return '\n'.join(lines).rstrip()


def _ref_ok(name: str, asset_type: str, registry, index=None,
            builtin_ok=False) -> bool:
    if not name:
        return True
    if builtin_ok and name in RENPY_BUILTIN_TRANSFORMS:
        return True
    if registry is None:
        return True
    return bool(resolve_references(name, registry, index))


# 资产类型 → registry 属性名（判断"这一类资产到底管没管"）：
# 已收敛到 `storygraph.core.vocabulary.ASSET_TYPE_ATTR`，顶部以别名导入。


def _category_managed(registry, asset_type: str) -> bool:
    """
    这一类资产在项目里到底管没管？

    这类"未登记"要分级报告，否则是纯噪音：
      · 项目里有 images.xlsx（管了）而 `bg_room` 不在里面
        → **错误**，这是真的断裂引用，构建出来会缺图。
      · 项目里压根没有图片表（没管）
        → **警告**，说明资产管线还没上；报成错误会让 build 直接失败，
          把"还没登记资产表"这种正常阶段卡死。
    """
    if registry is None:
        return False
    attr = _TYPE_ATTR.get(asset_type)
    if not attr:
        return False
    return bool(getattr(registry, attr, {}) or {})


# ==================================================================
#  L1：单条
# ==================================================================

def _image_ref_issue(name, registry, index):
    """
    图像引用的判定：精确解析 → tag + 属性都声明过 → 才是"未登记"。

    合成立绘（`layeredimage ye:`）的写法是 `show ye base_handdown happy`：
    引用由 tag 和若干 attribute 组成，资源表里往往只登记了 tag。
    只要 tag 存在、属性也都在 layeredimage 里声明过，就不该报未登记
    （Ren'Py 运行时会自己合成）。否则真实项目一次报上百条。
    """
    if resolve_references(name, registry, index):
        return None
    parts = str(name or '').split()
    if not parts:
        return None
    tag_id = resolve_references(parts[0], registry, index)
    if not tag_id:
        return 'unknown'
    row = (registry.images or {}).get(tag_id[0]) or {}
    declared = set(row.get('_attrs') or [])
    if not declared:
        return 'attrs'          # 表里有这个 tag，但没登记属性清单
    unknown = [p for p in parts[1:] if p not in declared]
    return 'attrs' if unknown else None


def validate_level1(beats, registry=None,
                    char_limit: int = DEFAULT_CHAR_LIMIT) -> list:
    from storygraph.assets.asset_registry import AssetIndex
    index = AssetIndex(registry) if registry is not None else None
    issues = []
    for b in beats or []:
        where = getattr(b, 'line_id', '') or f"{b.label}.{b.seq:04d}"

        _l1_empty_say(b, where, issues)
        _l1_speaker(b, where, registry, index, issues)
        _l1_asset_refs(b, where, registry, index, issues)
        _l1_text(b, where, char_limit, issues)
        _l1_audio_actions(b, where, issues)
        _l1_audio_params(b, where, issues)
        _l1_scene_show_collision(b, where, issues)
        _l1_condition(b, where, issues)

    issues.extend(_cell_sanitize_issues(beats))
    return issues

def _l1_empty_say(b, where, issues) -> None:
    """`validate_level1` 的一个检查块（原样搬出，只为拆复杂度）。"""
    # 空台词：`""`（清空对话框）是常见写法，警告即可；
    # 有说话人却空了台词，那才是明显的漏写 → 错误。
    #
    # ⚠ **结构头不是台词**（2026-10-07 实测，真机 +1 条假警）：
    # 循环 / 相机的**组头拍**、以及未建模块的保真拍，自己都不带台词 ——
    # 它们是**结构锚点**。不看 `loop_header` / `camera_header` / `opaque`
    # 这三位，`while current_round < game_rounds:` 那一拍就会长得像一句
    # 空台词，被报成"空台词行（`""`）"。
    # （旧形态之所以没报，只是因为它把整块 `while` 和**后面那句台词**
    #   并进了同一张拍 —— 那是另一个缺陷，不该被当成这里的挡箭牌。）
    empty_say = (b.kind in ('say', 'narration') and not b.text
                 and not b.comment and not b.raw and not b.code
                 and not b.sound and not b.voice and not b.music
                 and not b.scene and not b.show and not b.hide
                 and not getattr(b, 'stage', '')
                 and not getattr(b, 'bgm', '')
                 and not getattr(b, 'sfx', '')
                 and not getattr(b, 'loop_header', '')
                 and not getattr(b, 'camera_header', '')
                 and not getattr(b, 'opaque', '')
                 and (b.music_action or '') != 'stop'
                 and (b.sound_action or '') != 'stop')
    if empty_say:
        if b.speaker:
            issues.append(Issue(
                L1, 'E101', ERROR,
                f"说话人 '{b.speaker}' 有台词行但内容是空的", where,
                '补台词，或把这一行删掉'))
        else:
            issues.append(Issue(
                L1, 'W113', WARNING, '空台词行（`""`）', where,
                '常见用法是清空对话框；若是漏写就补上'))



def _l1_speaker(b, where, registry, index, issues) -> None:
    """`validate_level1` 的一个检查块（原样搬出，只为拆复杂度）。"""
    # 说话人未定义：角色表**明显不完整**时只给警告。
    # 否则"角色解析漏了一批（如 DynamicCharacter）"会翻成上千条
    # 错误把真正的问题埋掉——真实项目一次报了 1000+ 条就是这个原因。
    #
    # ⚠ **字符串字面量说话人**（`"系统" "提示"`）不查：它不是角色变量，
    # 天然不在角色表里（2026-10-05 修 —— 否则每处这种写法都报一条
    # "说话人不在资源表角色里"的假警）。
    if b.speaker and not is_literal_speaker(b.speaker) \
            and registry is not None \
            and not resolve_references(b.speaker, registry, index):
        managed = _category_managed(registry, 'character')
        issues.append(Issue(
            L1, 'E102' if managed else 'W110',
            ERROR if managed else WARNING,
            f"说话人 '{b.speaker}' 不在资源表角色里", where,
            '在 characters.xlsx 登记，或改成本表已有的角色变量名'
            if managed else
            '角色表还是空的：先「从脚本导入角色表」，再回来看这条'))



def _l1_asset_refs(b, where, registry, index, issues) -> None:
    """`validate_level1` 的一个检查块（原样搬出，只为拆复杂度）。"""
    # 资产引用未登记
    for atype, name, builtin_ok in _beat_asset_refs(b):
        if _ref_ok(name, atype, registry, index, builtin_ok):
            continue
        managed = _category_managed(registry, atype)

        # 图像：tag 存在且属性都声明过 → 正常（合成立绘的常见写法）
        if atype == 'image' and registry is not None:
            status = _image_ref_issue(name, registry, index)
            if status is None:
                continue
            if status == 'attrs':
                issues.append(Issue(
                    L1, 'W111', WARNING,
                    f"图片 '{name}' 的属性组合未在资源表声明"
                    f"（tag 已存在）", where,
                    '合成立绘按 tag+属性合成；如需精确管理可在'
                    ' images.xlsx 登记该组合'))
                continue

        # 音频：脚本写的是**文件路径**且文件不存在 → 警告。
        # Ren'Py 对加载不到的音频只是静音 + 写日志，不会崩，
        # 所以它不是"构建会失败"级别的错误。
        if atype in ('music', 'sound', 'voice') and _looks_like_path(name):
            issues.append(Issue(
                L1, 'W112', WARNING,
                f"音频 '{name}' 在资源表里找不到对应条目", where,
                '核对文件名/路径；Ren\'Py 会静音但日志会报错'))
            continue

        issues.append(Issue(
            L1, 'E103' if managed else 'W110',
            ERROR if managed else WARNING,
            f"{atype} '{name}' 未登记", where,
            '在对应资源表登记，或在候选表新增后刷新候选'
            if managed else
            '这类资产还没建资源表：建好后引用即可下拉选择'))



def _l1_text(b, where, char_limit, issues) -> None:
    """`validate_level1` 的一个检查块（原样搬出，只为拆复杂度）。"""
    # 台词长度 / 文本标签
    if b.text:
        # char_limit <= 0 时关闭长度提醒（台词长短由作者把控，
        # 不应作为硬性门槛——一行超过 60/120 字在长台词里很常见）。
        if char_limit and len(b.text) > char_limit:
            issues.append(Issue(
                L1, 'W101', WARNING,
                f'台词 {len(b.text)} 字，超过 UI 上限 {char_limit}', where,
                '拆成两拍，或调短（如不需要此提醒可把字符上限设为 0 关闭）'))
        issues.extend(_check_text_tags(b.text, where))



def _l1_audio_actions(b, where, issues) -> None:
    """`validate_level1` 的一个检查块（原样搬出，只为拆复杂度）。"""
    # 音频动作取值（BGM / SFX 列里的 [音乐] / [音效] 编译出来的动作）
    for col, val in (('BGM', b.music_action), ('SFX', b.sound_action)):
        if val and val not in _VALID_ACTIONS:
            issues.append(Issue(
                L1, 'W102', WARNING,
                f'{col} 列的动作 {val!r} 非法（应为 play/queue/stop）',
                where, '改成 play / queue / stop'))



def _l1_audio_params(b, where, issues) -> None:
    """`validate_level1` 的一个检查块（原样搬出，只为拆复杂度）。"""
    # 音频参数写在没有音频的拍上
    if b.music_params and not b.music and b.music_action != 'stop':
        issues.append(Issue(
            L1, 'W103', WARNING, 'BGM 列写了参数却没有音乐', where,
            '补上音乐名，或清空参数'))
    if b.sound_params and not b.sound:
        issues.append(Issue(
            L1, 'W104', WARNING, 'SFX 列写了参数却没有音效', where,
            '补上音效名，或清空参数'))



def _l1_scene_show_collision(b, where, issues) -> None:
    """`validate_level1` 的一个检查块（原样搬出，只为拆复杂度）。"""
    # 同一拍里 scene 与 show 撞同一图像：顺序敏感，容易出错
    scene_img = (b.scene or '').strip()
    if scene_img:
        for item in split_show_items(b.show):
            if (item.get('image') or '').strip() == scene_img:
                issues.append(Issue(
                    L1, 'W105', WARNING,
                    f'scene 与 show 用了同一图像 {scene_img!r}', where,
                    '确认 Stage 里的书写顺序是否符合本意'))



def _l1_condition(b, where, issues) -> None:
    """`validate_level1` 的一个检查块（原样搬出，只为拆复杂度）。"""
    # 条件表达式肉眼可见的壳
    if b.condition and not _looks_like_condition(b.condition):
        issues.append(Issue(
            L1, 'W107', WARNING,
            f'条件 {b.condition!r} 看起来不是合法表达式', where,
            '写成 love >= 5 这样的 Python 表达式'))




def _cell_sanitize_issues(beats) -> list:
    """
    **读表时被清洗过的单元格**要说出来（W114，2026-10-05 评审意见）。

    背景：单行列（说话人/立绘/位置/…）的值会被逐字拼进一行代码，
    单元格里的真换行漏出去就是**物理换行 —— 产物不可解析**。所以读表时
    把控制字符压成了空格（见 `beat_model.SINGLE_LINE_BEAT_FIELDS`）。

    但**压平本身就是"改了用户的数据"**：`ling\\nX` 变成 `ling X`。
    产物确实能跑了，可值跟他在表里写的不一样了。本项目的立场是
    **静默改数据比报错坏得多**（P0 就是这么定义的）——
    "救了用户却没告诉他"同样属于这一条。

    所以：清洗时在 `Beat.sanitized_cells` 上留痕，这里**汇总成一条**报出来
    （一行一条会把整列粘贴弄成几十条噪音，而这条要的是"你哪几格脏了"）。

    聚合而不是逐条：真实场景是"从别处粘了一列多行文本"，
    那会一次脏几十行；逐条报会刷屏，人就不看了。
    """
    hits = [(b, list(getattr(b, 'sanitized_cells', None) or []))
            for b in beats or []]
    hits = [(b, cols) for b, cols in hits if cols]
    if not hits:
        return []

    cols = sorted({c for _b, cs in hits for c in cs})
    names = '、'.join(BEAT_HEADERS.get(c, c) for c in cols)
    spots = []
    for b, _cols in hits[:6]:
        spots.append(getattr(b, 'line_id', '')
                     or f'{getattr(b, "label", "")}.{getattr(b, "seq", 0):04d}')
    more = f' 等 {len(hits)} 行' if len(hits) > len(spots) else ''
    return [Issue(
        L1, 'W114', WARNING,
        f'{len(hits)} 行的单元格里有换行/制表符（列：{names}），'
        f'读表时已按"一行一个值"压成空格 —— 产物能跑，但**值被改过**',
        '、'.join(spots) + more,
        '这些格本来不该有换行（多半是从别处粘进来的多行文本）：'
        '核对一下值有没有被并到一起；单元格里 Alt+Enter 的换行在这里没有语义')]


def _beat_asset_refs(beat) -> list:
    """一条 Beat 引用的资产：[(类型, 名字, 是否放行内置)]"""
    from storygraph.assets.asset_registry import is_dynamic_image_spec, \
        is_dynamic_transform_spec

    refs = []

    def img(name):
        name = (name or '').strip()
        if name and not is_dynamic_image_spec(name):
            refs.append(('image', name, False))

    def tf(name):
        name = (name or '').strip()
        if name and not is_dynamic_transform_spec(name):
            refs.append(('transform', name, True))

    img(beat.scene)
    for item in split_show_items(beat.show):
        img(item.get('image'))
    for item in split_hide_items(beat.hide):
        img(item.get('image'))
    for t in (beat.at or '').split():
        tf(t)
    tf(beat.transform)
    tf(beat.with_)
    if beat.music:
        refs.append(('music', beat.music.strip(), False))
    if beat.sound:
        refs.append(('sound', beat.sound.strip(), False))
    if beat.voice:
        refs.append(('voice', beat.voice.strip(), False))
    return refs


def _looks_like_path(name: str) -> bool:
    """引用看起来像"文件路径"（含目录分隔符或音频扩展名）。"""
    s = str(name or '')
    if '/' in s or '\\' in s:
        return True
    low = s.lower()
    return any(low.endswith(ext) for ext in
               ('.ogg', '.mp3', '.wav', '.m4a', '.flac', '.opus', '.aac'))


def _check_text_tags(text: str, where: str) -> list:
    issues = []
    if text.count('{') != text.count('}'):
        issues.append(Issue(
            L1, 'W108', WARNING, '文本标签 {} 不配对', where,
            '检查 {b} / {color=#fff} 之类的闭合'))
    if text.count('[') != text.count(']'):
        issues.append(Issue(
            L1, 'W109', WARNING, '插值 [] 不配对', where,
            '检查 [变量] 的闭合'))
    return issues


def _looks_like_condition(cond: str) -> bool:
    """粗判：不能含中文、不能以运算符开头/结尾、括号要配对。"""
    if re.search(r'[\u4e00-\u9fff]', cond):
        return False
    stripped = cond.strip()
    if re.match(r'^[=<>!&|)]', stripped) or re.search(r'[=<>!&|(]$', stripped):
        return False
    if cond.count('(') != cond.count(')'):
        return False
    return True


def _is_silent_char_excluded(cid: str, data: dict) -> bool:
    """W302 不该报的角色：内置旁白、占位名、单字母小游戏角色、用旁白像者。

    · `narrator` 是 Ren'Py 内置旁白，永远静默，报它没意义；
    · 显示名为空 / `???` / `-` 的是占位或没配好的角色；
    · 单 ASCII 字母名（m/n/t/u…）几乎都是小游戏 / UI 的静默占位角色，
      不是叙事说话人；
    · `n = Character(image="narrator")` 这类"借旁白像"的角色也排除。
    """
    if cid == 'narrator':
        return True
    name = (data.get('Name') or '').strip()
    if not name or name in ('???', '?', '-'):
        return True
    if len(cid) == 1 and cid.isascii() and cid.isalpha():
        return True
    if (data.get('ImageTag') or '').strip() == 'narrator':
        return True
    return False


# ==================================================================
#  L2：表内
# ==================================================================

#: 从自定义语句的头行里抽 `key=value`（值可以是引号串或裸 token）。
#:
#: ⚠ **唯一真源已经搬到 `story/spec_args.py`**（2026-10-07，"每参数一列"）：
#: 同一个扫描器现在有三个消费者（导出摊列 / 读表折回头行 / 这里的校验），
#: 而契约 H1 不许 `story` import `pipeline` —— 三份各写一遍迟早在某处漂移。
#: 顶部已经 import 了它，`from storygraph.pipeline.validate import
#: spec_arg_values` 的老调用方与单测照旧可用。


#: `W122` 认得出来的**语句动词**：只有头 token 落在这里面的行才参与
#: "块体里允许写什么"的判定。裸 Python（`target = ling`）与认不出的写法
#: 一律**放过** —— 它们的"头"是变量名，拿去比允许表只会误报。
_BODY_VERB_HEADS = frozenset({
    'scene', 'show', 'hide', 'with', 'pause', 'window', 'camera',
    'play', 'stop', 'queue', 'voice', 'menu', 'if', 'elif', 'else',
    'while', 'for', 'jump', 'call', 'return', 'python', 'pass',
    'init', 'label', 'define', 'default', 'transform', 'screen',
})


def validate_spec_args(beats, statements=None, project_root=None) -> list:
    """
    自定义语句的**参数校验**（D1，2026-10-07）。

    声明过的结构不校验，等于没声明 —— 项目写了 `columns` 却没人看，
    那它只是一段注释。两条判据：

      · `W120` —— 声明为 `required` 的参数在头行里**找不到**；
      · `W121` —— 参数值不在声明的 `choices` 里。
        ⚠ 它是**警告不是错误**：候选是"常见取值"，不是白名单 ——
        项目随时可能写个新值，拦下来只会让人去关掉校验。

    声明从 `storygraph.rules.json` 或宿主 `register_statement` 来；
    都没有就一条不报（**少报胜过乱报**）。

    ⚠ 读的是 `spec_head` 优先，而"每参数一列"的值在 `from_row` 里**已经
    折进 `spec_head`** —— 所以改一格参数列，这里校验到的就是**改后**的值
    （不需要在这里再去看那些动态列；看两处反而会有两套真相）。
    """
    from storygraph.story.statement_registry import (StatementRegistry,
                                                     spec_body_allows,
                                                     spec_columns)
    if statements is None and project_root:
        statements = StatementRegistry.for_project(project_root)
    if not statements:
        return []
    issues = []
    for b in (beats or ()):
        name = str(getattr(b, 'spec_name', '') or '')
        if not name:
            continue
        cols = spec_columns(statements, name)
        if not cols:
            continue
        head = (str(getattr(b, 'spec_head', '') or '')
                or str(getattr(b, 'opaque', '') or '').split('\n')[0])
        vals = spec_arg_values(head)
        where = (getattr(b, 'line_id', '')
                 or f'{getattr(b, "label", "")}.{getattr(b, "seq", 0):04d}')
        missing = [c['name'] for c in cols
                   if c['required'] and c['name'] not in vals]
        if missing:
            issues.append(Issue(
                L1, 'W120', WARNING,
                f'自定义语句 {name} 缺少声明的必需参数：{"、".join(missing)}',
                where,
                f'声明写在 storygraph.rules.json / renpy.register_statement 里。'
                f'头行现在是：{head}'))
        for c in cols:
            if not c['choices'] or c['name'] not in vals:
                continue
            if vals[c['name']] not in c['choices']:
                issues.append(Issue(
                    L1, 'W121', WARNING,
                    f'自定义语句 {name} 的参数 {c["name"]}="'
                    f'{vals[c["name"]]}" 不在声明的候选里',
                    where,
                    '候选是**常见取值**、不是白名单：确实要用新值就忽略这条。'
                    '候选清单也写在导出表格那一格的注释里'))
        # `W122` —— **块体里出现了声明之外的语句**（设计文档 §2.3 第三条规则，
        # 2026-10-07）。这是"结构级"约束（设计文档 §0 的 ②层）：语法形状能看出
        # "有块体"，但看不出"这个块里允许写什么" —— 只能被声明
        # （`body: {"allows": ["say","show"]}`）。
        #
        # ⚠ 判据刻意保守：**认不出头的行（裸 Python / `$` / 注释）一律放过**，
        # 只在"这行有个明确的头、而它不在允许表里"时报警 —— 同 W121 的口径：
        # 少报胜过乱报（报多了人就去关校验）。
        allowed = spec_body_allows(statements, name)
        body = str(getattr(b, 'opaque', '') or '')
        if allowed and body.strip():
            from storygraph.core.vocabulary import statement_head_token
            for raw_line in body.split('\n')[1:]:      # 第 0 行是头行
                line = raw_line.strip()
                if not line or line.startswith('#'):
                    continue
                head_tok = statement_head_token(line)
                # **台词行**要归一成 `say`：`"台词"` 的头 token 就是那句话本身，
                # `e "台词"` 的头是说话人变量 —— 两者在"块里允许写什么"这个问题上
                # 都是台词（不然写了 `allows: ["say"]` 的项目会被自己人误报）。
                if head_tok[:1] in ('"', "'") or '"' in line or "'" in line:
                    head_tok = 'say'
                # ⚠ **认不出的行放过**：裸 Python（`target = ling`）的头 token
                # 是 `target` 这种"看着像头"的东西，拿去比允许表会误报。
                # 所以只在头是**明确的 Ren'Py 语句动词**时才判（少报胜过乱报）。
                if head_tok not in _BODY_VERB_HEADS:
                    continue
                if head_tok not in allowed:
                    issues.append(Issue(
                        L1, 'W122', WARNING,
                        f'自定义语句 {name} 的块体里出现了声明之外的语句：'
                        f'{head_tok}',
                        where,
                        f'声明的允许清单：{"、".join(allowed)}'
                        f'（写在 storygraph.rules.json 的 body.allows）。'
                        f'这一行是：{line}'))
    return issues


def validate_level2(beats, registry=None, menu_labels=None) -> list:
    issues = []
    beats = list(beats or [])

    seen_line_ids = {}
    seq_by_label = {}
    menu_options_by_label = {}
    # 有没有 `menu` 行：以解析结果为准（`menu:` 没有标题也算 menu），
    # 不能用"有没有 caption 非空的 MENU 拍"来判断，否则会误报
    # "有菜单选项却没有 menu 行"。
    has_menu_caption = set(menu_labels or ())

    for b in beats:
        where = getattr(b, 'line_id', '') or f"{b.label}.{b.seq:04d}"

        if not b.label:
            issues.append(Issue(
                L2, 'E201', ERROR, '这一行没有 Label', where,
                '填上所属章节 label（生成脚本时用它分段）'))

        if b.line_id:
            if b.line_id in seen_line_ids:
                issues.append(Issue(
                    L2, 'E202', ERROR,
                    f"LineId '{b.line_id}' 在第 "
                    f"{seen_line_ids[b.line_id]} 拍已出现过", where,
                    'LineId 是协作主键，必须唯一；重算 LineId 或调整顺序'))
            else:
                seen_line_ids[b.line_id] = b.seq

        seq_by_label.setdefault(b.label, []).append(b.seq)
        if b.kind == KIND_MENU_OPTION:
            menu_options_by_label.setdefault(b.label, 0)
            menu_options_by_label[b.label] += 1

    # seq 断号/重复
    for label, seqs in seq_by_label.items():
        if len(set(seqs)) != len(seqs):
            issues.append(Issue(
                L2, 'E203', ERROR, f"label '{label}' 里 Seq 有重复",
                label, '重排 Seq'))
        ordered = sorted(set(seqs))
        gaps = [a for a, b2 in zip(ordered, ordered[1:]) if b2 != a + 1]
        if gaps:
            issues.append(Issue(
                L2, 'W201', WARNING,
                f"label '{label}' 的 Seq 不连续（{len(ordered)} 拍，"
                f"{len(gaps)} 处断号）", label,
                '断号通常无害；若是删行造成，可重排'))

    # 菜单选项没有 menu 标题
    for label, count in menu_options_by_label.items():
        if label not in has_menu_caption:
            issues.append(Issue(
                L2, 'W202', WARNING,
                f"label '{label}' 有 {count} 个菜单选项却没有 menu 行",
                label, '补一行 menu（Caption 填提示语）或用 jump 代替'))

    # 导入期"工具替你做了什么"（目前是 Excel 行序按 Seq 重排，P0-3）。
    # 必须报出来：重排本身安全，但"我拖动的那一行没生效"必须让人知道，
    # 否则他会以为工具在随机改顺序。
    for b in beats:
        for note in (getattr(b, 'runtime_notes', None) or ()):
            issues.append(Issue(
                L2, 'W203', WARNING, str(note),
                getattr(b, 'line_id', '') or getattr(b, 'label', ''),
                '这是导入时按「序号」列做的自动排序；要改剧情顺序，'
                '请直接改「序号」列（只拖动行 / 按别的列排序不会生效）'))

    return issues


# ==================================================================
#  L3：全库
# ==================================================================

# Ren'Py 里常见的入口 label（按优先级）
_ENTRY_LABELS = ('start', 'splashscreen', 'main_menu', 'before_main_menu',
                 'beginstory', 'beginstory1', 'begin')


def _entry_node_id(graph):
    """找到图的入口节点；找不到返回 None。"""
    by_name = {}
    for nid, n in graph.nodes.items():
        by_name.setdefault(n.label_name, nid)
    current = next((nid for nid, n in graph.nodes.items() if n.is_current),
                   None)
    if current is not None:
        return current
    for cand in _ENTRY_LABELS:
        if cand in by_name:
            return by_name[cand]
    return None


def resolve_asset_path(project_root, rel):
    """
    资产表里的相对路径 → 真实存在的文件（找不到返回 None）。

    真实项目里 Path 的**基准五花八门**：早期扫描根可能是游戏的上级目录
    （`sr_general/game/images/x.png`）、game 目录（`images/x.png`）、
    甚至项目根（`game/images/x.png`）。只按 project_root 拼会把上千个
    存在的文件判成"缺失"（实测 1446 条误报）。这里逐个基准试。
    """
    if not rel:
        return None
    rel = str(rel).replace('\\', '/').lstrip('/')
    root = Path(project_root)
    parts = rel.split('/')
    candidates = [root / rel, root.parent / rel]
    if len(parts) > 1:
        tail = '/'.join(parts[1:])
        candidates += [root / tail, root.parent / tail]
    candidates.append(root / 'game' / rel)
    for c in candidates:
        try:
            if c.exists():
                return c
        except OSError:
            continue
    return None


def validate_level3(beats, registry=None, graph=None,
                    script_warnings=None, project_root=None,
                    project_speakers=None) -> list:
    issues = []
    beats = list(beats or [])

    # 断裂跳转：动态 jump / 运行时求得的目标静态判不出来，所以是**警告**
    # （真实项目里 minigame 之类用 expression 跳转很常见，报成错误会淹没真问题）。
    for w in (script_warnings or []):
        if '未定义的跳转目标' in str(w):
            issues.append(Issue(
                L3, 'W301', WARNING, str(w), '',
                '确认目标是否在别处定义（含动态跳转）；确实缺失就补上'))

    if graph is not None:
        issues.extend(_unreachable_issues(graph))

    if registry is None:
        return issues

    # 未使用资产
    referenced = set(getattr(registry, 'references', {}) or {})
    for attr, label in (('images', '图片'), ('music', '音乐'),
                        ('sounds', '音效'), ('transforms', '变换')):
        data = getattr(registry, attr, {}) or {}
        if not data:
            continue
        unused = [aid for aid in data if aid not in referenced]
        if not unused:
            continue
        ratio = len(unused) / len(data)
        sample = '、'.join(sorted(unused)[:6])
        issues.append(Issue(
            L3, 'W301', WARNING,
            f'{label}：{len(unused)}/{len(data)} 个未被任何脚本引用'
            f'（如 {sample}）', attr,
            '确认是否废弃；确实不用就从资源表删除'))
        # 比例异常高时给出真正的原因提示，而不是让人去逐个核对两千条
        if ratio > 0.8 and len(data) > 50:
            issues.append(Issue(
                L3, 'W303', WARNING,
                f'{label}的未引用比例高达 {ratio:.0%}，资源表很可能把'
                f'UI/参考图/临时素材也扫进来了', attr,
                '用「重新扫描」并关闭 include_ui 重建资源表，'
                '或确认这些素材是否本就不该进表'))

    # 角色没有台词（项目级检查）
    # 某一章没说话，不代表整本书没说——单章体检不报 W302，否则会把
    # "只在别章出场"或"小游戏/UI 用的静默角色"（m/n/t/u、narrator）
    # 误报成问题。只有拿到"整本书的说话人集合"时才报，并排除
    # 内置旁白 / 占位名 / 单字母小游戏角色 / 用旁白像的角色。
    if beats and project_speakers is not None:
        chars = getattr(registry, 'characters', {}) or {}
        silent = [cid for cid in chars
                  if cid not in project_speakers
                  and not _is_silent_char_excluded(cid, chars.get(cid, {}))]
        if silent and len(chars) > 1:
            issues.append(Issue(
                L3, 'W302', WARNING,
                f'{len(silent)} 个角色在整本书里都没有台词（如 '
                f'{"、".join(sorted(silent)[:6])}）', 'characters',
                '确认是尚未出场，还是小游戏/UI 用的静默角色'))

    # 资源表登记了但文件不存在
    #
    # ⚠ **模板路径不参与对账**（2026-10-18，真机 E302 误报）：`{image}` 这种
    # 路里带占位符的行，文件名是运行时拼的，永远不可能 `exists()` ——
    # 判据只有一处（`asset_registry.is_template_path`），占位行同理
    # （`is_placeholder_row`）。
    if project_root:
        missing = []
        for attr in ('images', 'music', 'sounds', 'voices'):
            for aid, row in (getattr(registry, attr, {}) or {}).items():
                rel = (row or {}).get('Path')
                if not rel or is_template_path(rel):
                    continue
                if not resolve_asset_path(project_root, rel):
                    missing.append(f'{aid}({rel})')
        if missing:
            issues.append(Issue(
                L3, 'E302', ERROR,
                f'{len(missing)} 个资产的源文件不存在：'
                f'{"、".join(missing[:6])}', '资源表',
                '修正 Path 或补文件；否则构建出的包会缺资源'))

    # Beats 与「舞台快照」是否同源（用户评审第 12 条）
    issues.extend(_beats_snapshot_issues(beats))

    # 资源命名规范建议（用户评审第 13 条）——**只建议，绝不自动改写**
    issues.extend(_naming_convention_issues(registry))

    return issues


#: 资源命名里"跨平台/版本管理有风险"的字符。
#: 注意：这些字符是**文件名内容**，`dsl.normalize_text` 绝不能去归一化它们
#: （`bgm/夜晚，断电之后….mp3` 就是这么被改坏的）。所以这里只出一张
#: 建议清单给 PM 排期改名，改不改由人决定。
_NAMING_RISKY = (
    (' ', '空格'),
    ('，', '全角逗号'), ('。', '全角句号'), ('？', '全角问号'),
    ('！', '全角叹号'), ('：', '全角冒号'), ('；', '全角分号'),
    ('、', '顿号'), ('（', '全角括号'), ('）', '全角括号'),
    ('…', '省略号'), ('“', '全角引号'), ('”', '全角引号'),
    ('「', '全角引号'), ('」', '全角引号'),
)

#: 不规范资产少于这个数就不提——新项目一打开就满屏"命名不规范"没人会看。
_NAMING_NOISE_FLOOR = 8


def _naming_convention_issues(registry) -> list:
    """
    资源命名规范建议（用户评审第 13 条）。

    真实项目里素材名带中文、空格、省略号、问号
    （`bgm/无法言说的事情…….mp3`），跨平台与版本管理都有风险。这里算一份
    **建议清单**，不报错、不自动改名——改名会打断所有已有引用，
    必须由人决定。每条只给数量与前几个例子。
    """
    if registry is None:
        return []
    bad = []
    total = 0
    for attr in ('images', 'music', 'sounds', 'voices'):
        for aid, row in (getattr(registry, attr, {}) or {}).items():
            total += 1
            name = str((row or {}).get('Path') or aid or '')
            hit = [label for ch, label in _NAMING_RISKY if ch in name]
            if any(ord(c) > 0x4DFF for c in name):
                hit.append('非 ASCII 字符')
            if hit:
                bad.append(f'{aid}（{"、".join(sorted(set(hit)))}）')
    if len(bad) < _NAMING_NOISE_FLOOR:
        return []
    return [Issue(
        L3, 'W313', WARNING,
        f'{len(bad)} / {total} 个资产名含空格、中文、省略号等字符，'
        f'跨平台与版本管理有风险（如 {"；".join(bad[:3])}）', '资源表',
        '建议新素材改用「英文/拼音 + 下划线」；已有素材改名要同步改引用，'
        '所以这里只给建议、不自动改（本工具不会动你的文件名）')]


def _beats_snapshot_issues(beats) -> list:
    """
    Beats 与舞台快照的一致性：**表里的舞台指令，快照有没有真的认出来**。

    为什么需要它：舞台快照是从 Beats 推导的派生视图（Excel 的「舞台快照」
    工作表、编辑器的预览面板都用它）。推导一旦漏了某种写法，表格会显示
    "台上没人"，而生成的脚本里立绘明明在——**表格就成了骗人的东西**。

    只报**一种**确定可信的情况：`show` / `hide` 写完**舞台状态一点没变**，
    而且这个图像名的 tag 在整段剧本里从没被快照认成过角色或图层。
    那说明这个名字快照根本不认识（错字、写成了资产 ID 而不是图像名…），
    表格与脚本必然不一致。

    刻意**不报**下面这些，因为它们是合法写法（报了就是刷屏，
    真实项目实测会多出 60+ 条噪声）：
      · `scene bg X` 重复声明同一个背景（Ren'Py 里是合法的"重置舞台"）；
      · 重复 show 台上已有的同一张图（幂等）；
      · `show cg X` 之类只动非角色图层（快照会记为 CG/图层变化）。
    """
    from storygraph.story.stage_snapshot import stage_snapshots

    out = []
    try:
        snaps = stage_snapshots(list(beats or []))
    except Exception as e:                     # noqa: BLE001 - 派生视图坏了不该拦住体检
        swallow('pipeline.validate._beats_snapshot_issues', e)
        return out

    # 整段剧本里被快照认成"角色"或"图层"的 tag 集合
    known_tags = set()
    for s in snaps:
        for slot in s['state'].get('actors', ()):
            if slot and slot[0]:
                known_tags.add(slot[0].split()[0])
        for img in (s['state'].get('layers', {}) or {}).values():
            if img:
                known_tags.add(img.split()[0])
        if s['scene']:
            known_tags.add(s['scene'].split()[0])

    for beat, snap in zip(beats or [], snaps):
        where = getattr(beat, 'line_id', '') or f'{beat.label}.{beat.seq:04d}'
        if snap['display_changes']:
            continue
        # 没有任何变化：逐个看它写了什么，只有"名字根本不认识"才报
        unknown = []
        for spec in _stage_specs_of(beat):
            tag = spec.split()[0] if spec.split() else ''
            if tag and tag not in known_tags:
                unknown.append(spec)
        if unknown:
            out.append(Issue(
                L3, 'W311', WARNING,
                f'这一拍的舞台指令没有产生任何变化，而且这些图像名快照不认识：'
                f'{"、".join(unknown[:4])}', where,
                '图像名既没被认成立绘、也没被认成背景/CG 图层——'
                '多半是写错了（或误填了资产 ID）。表格会与生成的脚本不一致'))
    return out


def _stage_specs_of(beat) -> list:
    """这一拍舞台指令里出现的图像名（立绘 / 退场 / 背景）。"""
    from storygraph.core.vocabulary import split_stage_items
    out = []
    for raw in split_stage_items(beat.show or ''):
        item = raw.strip()
        if item:
            out.append(item.split('@')[0].strip())
    for raw in split_stage_items(beat.hide or ''):
        item = raw.strip()
        if item:
            out.append(item.split('@')[0].strip())
    if (beat.scene or '').strip():
        out.append(beat.scene.split('@')[0].strip())
    return [s for s in out if s]

def _unreachable_issues(graph) -> list:
    """从入口做可达性分析，报出到不了的 label。找不到入口就不报。"""
    if not getattr(graph, 'nodes', None):
        return []
    start = _entry_node_id(graph)
    if start is None:
        return [Issue(
            L3, 'W304', WARNING,
            f'图里没有可识别的入口 label（{"/".join(_ENTRY_LABELS[:3])}…），'
            f'已跳过可达性检查', 'graph',
            '入口 label 命名不同时属正常；如需检查可在图里把某个节点'
            '设为当前章节')]

    reached = _reached_nodes(graph, start)
    unreachable = [n.label_name for nid, n in graph.nodes.items()
                   if nid not in reached]
    if not unreachable:
        return []
    return [Issue(
        L3, 'E303', WARNING,
        f'{len(unreachable)} 个 label 从入口走不到（如 '
        f'{"、".join(sorted(unreachable)[:6])}）', 'graph',
        '多数是动态跳转/调试残留；确实不该存在再清理')]


def _reached_nodes(graph, start_id) -> set:
    """图上从入口可达的节点集合（宽搜，与可达性分析结论一致）。"""
    seen = {start_id}
    queue = [start_id]
    while queue:
        cur = queue.pop()
        for e in graph.edges.values():
            if e.from_id != cur:
                continue
            if e.to_id not in seen:
                seen.add(e.to_id)
                queue.append(e.to_id)
    return seen


# ==================================================================
#  汇总
# ==================================================================

def validate_all(beats, registry=None, graph=None, script_warnings=None,
                 project_root=None, menu_labels=None,
                 char_limit: int = DEFAULT_CHAR_LIMIT,
                 dedupe: bool = True, cap_per_code: int = 40,
                 project_speakers=None
                 ) -> ValidationReport:
    """
    跑完三级校验。

    **刻意加了去重与上限**：真实项目（2600 张图、2000+ 行）跑一次能刷出
    上千条同类信息——那不是报告，是噪音，人的反应是不看。同一 code 只留
    前 `cap_per_code` 条，其余折成一行"还有 N 条同类"。
    CI 要全量就把 dedupe=False、cap_per_code=0。
    """
    report = ValidationReport()
    report.extend(validate_level1(beats, registry, char_limit=char_limit))
    # 自定义语句的参数（D1）：声明过的结构不校验，等于没声明。
    report.extend(validate_spec_args(beats, project_root=project_root))
    report.extend(validate_level2(beats, registry, menu_labels=menu_labels))
    report.extend(validate_level3(beats, registry, graph,
                                  script_warnings, project_root,
                                  project_speakers))
    if dedupe or cap_per_code:
        report.issues = _dedupe_and_cap(report.issues, cap_per_code)
    return report


def _dedupe_and_cap(issues: list, cap_per_code: int) -> list:
    """同一 (code, message, where) 只留一条；同一 code 最多留 cap_per_code 条。"""
    seen = set()
    counts = {}
    kept = []
    overflow = {}
    for issue in issues:
        key = (issue.code, issue.message, issue.where)
        if key in seen:
            continue
        seen.add(key)
        counts[issue.code] = counts.get(issue.code, 0) + 1
        if cap_per_code and counts[issue.code] > cap_per_code:
            overflow[issue.code] = overflow.get(issue.code, 0) + 1
            continue
        kept.append(issue)
    for code, n in overflow.items():
        sample = next((i for i in issues if i.code == code), None)
        if sample is None:
            continue
        kept.append(Issue(
            sample.level, code, sample.severity,
            f'……另有 {n} 条同类问题（同类仅展示前 {cap_per_code} 条）',
            '', sample.hint))
    return kept
