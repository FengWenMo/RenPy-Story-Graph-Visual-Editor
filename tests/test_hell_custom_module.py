"""
tests/test_hell_custom_module.py
================================
**自定义动作模块的地狱测试（白盒攻击版）**。

被攻击的模块 = 用户在界面里"教工具认识项目自己的写法"的那一条完整链路：

    authoring/pattern.py            ← 位置序列（格子）+ 试切 `match_line`
    authoring/learn_statement.py    ← 表单 → 声明、候选来源、立绘分组、形状反推
    story/statement_registry.py     ← 声明落盘 / 装配 / 动态列（`declare_statement` …）
    story/spec_args.py              ← 头行参数解析 + 折回（`set_positional_arg` …）
    story/beat_model.spec_arg_cells ← 卡片/表格**读值**那一侧
    ui_qt/dialog_learn.py           ← 弹窗把表单交出去的那一步（`built()`）

这一份与 `tests/test_custom_statement_entry.py` 的分工
-----------------------------------------------------
那份测"**做过的功能还在**"（回归）。这一份只做**攻击**，按用户点名要的四类打：

  ① **切不出来**：`match_line` / `_tokens` 在真形状上切错、切出假成功、吞掉后一段；
  ② **参数名不对**：反推出来的名字是不是真参数（`act` / `color` / `size` / `bold`
     这种从界面语言里串出来的"看着像真的、其实不是这个语句的参数"）；
  ③ **不符合真实项目**：真机 `D:\\千连万理\\RenPyTest\\sr_general` 上实测
     （立绘分组、来源清单、真词值），并与"照定义原文独立复算"的期望对比；
  ④ **经过特化、通用性不足**：把真项目的约定（顶层 `layeredimage`、ASCII 语句名、
     空格分词、`params` 与 `pattern` 同时给）**换掉**，看还成不成立。

判据口径（写死在这里，免得日后越改越松）
--------------------------------------
  · **真机断言**只用 ASCII 值 + 长度 + 集合关系，不把中文写进断言
    （脚本编码在不同机器上会变，中文断言会假红）；
  · 真机项目不在（换机器 / CI）→ **整类 skip**，而不是静默通过；
  · 每条"当场是红的"缺陷都写明：**现象 / 根因（文件:行）/ 影响的真实用法 /
    修法方向**，并把"修好之后应该是什么"用另一条断言写出来（`test_fixed_…`）——
    这样修完它自己会从红转绿，不需要回来改测试；
  · 所有严格断言**不许**带 `xfail`：`xfail` 会把"又坏了"变成"又是预期内的"。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
#  真机项目（换机器 → skip，而不是静默通过）
# ---------------------------------------------------------------------------

#: 真机验收对象（与 `.workbuddy/memory/MEMORY.md` §三 同一份）。
REAL_ROOT = Path(r'D:\千连万理\RenPyTest\sr_general')

#: 判"这个真机项目真的在"—— 只看目录不够（空目录也会通过）。
REAL_READY = (REAL_ROOT / 'game' / 'definitions.rpy').is_file()

requires_real = pytest.mark.skipif(
    not REAL_READY,
    reason=f'没有真机项目：{REAL_ROOT}（换机器/CI 时正常跳过）')

#: 真机里**确定存在**的立绘 tag（`game/definitions.rpy` 的顶层 `layeredimage`）。
REAL_TAGS = ('lan', 'ling', 'ye', 'xing', 'qing')

#: 真机里 `<tag>_cc`（角色卡预览的复制品，`game/cc_preview_images.rpy`）。
REAL_CC_TAGS = ('lan_cc', 'ling_cc', 'ye_cc', 'xing_cc', 'qing_cc')


@pytest.fixture(autouse=True)
def _clean_registry_cache():
    """
    每个用例前后都清一次注册表缓存。

    为什么必须 autouse：`StatementRegistry.for_project` 带**全局缓存**
    （键 = 项目根字符串）。用例之间共用 `tmp_path` 之外的路径（真机根
    更是同一个字符串），不清就会读到上一个用例装配的那份 —— 于是"改了
    rules.json 没生效"这类假红/假绿都会出现。
    """
    from storygraph.story.statement_registry import clear_cache

    clear_cache()
    yield
    clear_cache()


# ---------------------------------------------------------------------------
#  工具：独立复算 + 造项目
# ---------------------------------------------------------------------------

def _rpy_files_under(root: Path) -> list:
    """`root/**/*.rpy`（与 `learn_statement.layeredimage_groups` 的取证范围一致）。"""
    return sorted(root.rglob('*.rpy'))


def oracle_layeredimage(root: Path) -> dict:
    """
    **照定义原文独立复算** `{tag: {组: [属性…]}}` —— 不复用被测模块的一行代码。

    口径（Ren'Py 的 `layeredimage` 语义）：缩进的 `group <名>` 换组；
    `attribute <名>` 归到当前组；同名组**再出现 = 继续加**（真机 `expressions`
    就是两个 `when` 块）；`if_any` / `if_all` 之类的**子句**不换组
    （里面的 `attribute` 仍算当前组的候选值 —— 这正是本测试要盯的口径）。
    """
    out: dict = {}
    for fp in _rpy_files_under(root):
        tag = None
        group = None
        for raw in fp.read_text(encoding='utf-8', errors='replace').splitlines():
            line = raw.rstrip()
            if not line.strip() or line.lstrip().startswith('#'):
                continue
            m = re.match(r'^layeredimage\s+([A-Za-z_]\w*)\s*:', line)
            if m:
                tag, group = m.group(1), None
                out.setdefault(tag, {})
                continue
            if tag is None:
                continue
            if not line[:1].isspace():
                tag, group = None, None          # 出了这个块
                continue
            g = re.match(r'\s+group\s+([A-Za-z_]\w*)', line)
            if g:
                group = g.group(1)
                out[tag].setdefault(group, [])
                continue
            a = re.match(r'\s+attribute\s+([A-Za-z_]\w*)', line)
            if a and group:
                val = a.group(1)
                if val not in out[tag][group]:
                    out[tag][group].append(val)
    return {t: g for t, g in out.items() if g}


def dedup_cc_copies(table: dict) -> dict:
    """
    独立复算 `<tag>_cc` 去重（**只复算"应该怎么做"，不抄被测实现**）。

    口径（`learn_statement.layeredimage_groups` 的 docstring 写明）：同名去掉
    `_cc` 之后还有本体、且"组和值完全一样"→ 那是复制品，丢掉。
    """
    out = {}
    for tag, groups in table.items():
        base = tag[:-3] if tag.endswith('_cc') else ''
        if base and base in table and table[base] == groups:
            continue
        out[tag] = groups
    return {t: g for t, g in out.items() if g}


def write_custom_project(root: Path, entries, *, groups=None,
                         game_files=None) -> Path:
    """
    造一个"有自定义写法"的最小 Ren'Py 工程。

    `entries` = `storygraph.rules.json` 的 `statements` 列表（原样写）；
    `game_files` = `{相对路径: 内容}`（默认给一个空 `script.rpy`）。
    """
    root.mkdir(parents=True, exist_ok=True)
    (root / 'game').mkdir(parents=True, exist_ok=True)
    data: dict = {'statements': list(entries)}
    if groups is not None:
        data['attr_groups'] = list(groups)
    (root / 'storygraph.rules.json').write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    for rel, text in (game_files or {'script.rpy': 'label start:\n    return\n'}).items():
        fp = root / 'game' / rel
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(text, encoding='utf-8')
    return root


def write_character_table(root: Path, characters: dict) -> Path:
    """
    给合成项目写一张真 `characters.xlsx`。

    为什么需要它：`suggest_shape` 的"这一项是不是角色"**只认资产表**
    （`_role_table` → `load_asset_registry`）。没有表时它照样能读值，
    但名字会保守地叫「对象」—— 这是**正常的降级**，不是 bug。
    要测"认成角色"就必须给表，否则测的是降级那条路。
    """
    from storygraph.assets.asset_registry import (_TABLE_FIELDS,
                                                  write_dict_to_excel)

    target = root / 'game' / 'asset_tables'
    target.mkdir(parents=True, exist_ok=True)
    write_dict_to_excel(target / 'characters.xlsx', characters,
                        _TABLE_FIELDS['character'])
    return target


def build_cells(root: Path, line: str, multi_policy: str = ''):
    """
    一行脚本 → `({参数名: 值}, 列描述)`（走卡片/表格真正用的那两条路）。

    `multi_policy`（2026-10-14 加）：多值格的消歧策略 ——
    `''` = 默认（**甲：order-first**）、`'greedy'` = 乙。
    ⚠ 现在**只有甲实现了**；传 `'greedy'` 时实现应当**退化回甲**（不乱），
    判据也只要求"要么吃满、要么与甲一致"。
    """
    from storygraph.story.beat_model import Beat
    from storygraph.story.statement_registry import dynamic_spec_columns

    reg = _registry(root)
    b = Beat()
    b.opaque = line
    if multi_policy:
        b.multi_policy = multi_policy          # 预留：实现接上后会真的生效
    cols = dynamic_spec_columns([b], reg)
    cells = b.spec_arg_cells(cols)
    return {str(c.get('param') or ''): v for c, v in zip(cols, cells)}, cols, b


def _registry(root: Path):
    from storygraph.story.statement_registry import (StatementRegistry,
                                                     clear_cache)
    clear_cache()
    return StatementRegistry.for_project(str(root))


def fold_cell(root: Path, line: str, param: str, value: str) -> str:
    """
    把某一格改掉 → 折回头行（写回那条路）。

    ⚠ **走与卡片同一条路**（2026-10-14 修）：`dynamic_spec_columns` 摊出来的
    列描述里带着 `pattern`，而 `spec_arg_cells` 会把它记在拍上、
    折回那一侧再用它按**格子区间**整段换值。以前这里只把 `{header: value}`
    交给折回、**格子丢在半路**，于是折回静默退回"位置号"那套 ——
    多值格只换掉第一个词（`mypose happy smile at left`）。
    这正是白盒判据 `test_the_card_reader_uses_the_declared_pattern_not_a_recount`
    要防的"两套口径"。
    """
    from storygraph.story.beat_model import Beat, fold_spec_arg_columns
    from storygraph.story.statement_registry import dynamic_spec_columns

    reg = _registry(root)
    b = Beat()
    b.opaque = line
    cols = dynamic_spec_columns([b], reg)
    # 与卡片同一条读值路：它顺手把"这一拍用哪套格子"记在拍上
    b.spec_arg_cells(cols)
    header = next((str(c['header']) for c in cols
                   if c.get('param') == param), '')
    assert header, f'没长出参数列「{param}」：{[c["header"] for c in cols]}'
    fold_spec_arg_columns(b, {header: value})
    return b.statement_head()


#: 真机里会当成"候选值"出现的、"不是值"的词（注释片段 / 修饰词 / 括号残渣）。
_NOT_VALUES = {'#白色转场', 'with', 'at', 'True)', 'expression'}


# =====================================================================
#  一、真机：立绘分组（① 切不出来 · ③ 不符合真实项目）
# =====================================================================

@requires_real
class TestRealProjectLayeredImageGroups:
    """`learn_statement.layeredimage_groups` 在真机上必须**照定义原文**成立。"""

    def test_matches_an_independent_recount_of_the_definitions(self):
        """
        与被测模块无关地重算一遍：结果必须**逐组一致**。

        为什么这一条是"地狱级"的第一刀：用户投诉的正是
        "lan 实际上没有 hanfu_base 这种选项……必须以真实的有的资源为准"。
        "按 tag 分开"只要任何一处（缩进、`when`、`_cc`、合并）出错，
        候选里就会出现**别的角色的属性**，而界面上看不出来。

        ⚠ 复算里也照 docstring 的口径做了 `<tag>_cc` 去重（真机里
        `cc_preview_images.rpy` 的 5 份复制品与本体逐字相同）——
        所以这一条同时钉住"去重做对了"和"没顺手把本体也删了"。
        """
        from storygraph.authoring.learn_statement import layeredimage_groups

        got = layeredimage_groups(str(REAL_ROOT))
        want = dedup_cc_copies(oracle_layeredimage(REAL_ROOT))
        assert set(got) == set(want), (
            '立绘 tag 集合与定义原文（去重后）不一致',
            sorted(set(got) ^ set(want)))
        for tag in sorted(want):
            assert got[tag] == want[tag], (
                f'{tag} 的组/属性与定义原文不一致',
                {k: sorted(set(v) ^ set(want[tag].get(k, [])))
                 for k, v in got[tag].items()},
                {k: v for k, v in want[tag].items() if k not in got[tag]})

    def test_the_five_real_tags_are_found(self):
        """真机顶层就是这 5 个立绘（多一个少一个都是错）。"""
        from storygraph.authoring.learn_statement import layeredimage_groups

        got = layeredimage_groups(str(REAL_ROOT))
        assert set(REAL_TAGS) <= set(got), ('真机立绘没读全', sorted(got))
        assert set(got) == set(REAL_TAGS), (
            '读出来的 tag 不止真机那 5 个（多半把 <tag>_cc 复制品也算进来了）',
            sorted(set(got) - set(REAL_TAGS)))

    def test_cc_copies_are_deduplicated(self):
        """
        `<tag>_cc`（`game/cc_preview_images.rpy` 里的预览复制品）**不许**出现在结果里。

        真机里 `lan_cc` 与 `lan` 的组/值**逐字相同**，所以去重"看起来"成立了 ——
        这一条把"看起来"钉成判据：只要有人把 `_cc` 的某一句话改了（真项目里
        很常见：预览图少两个表情），去重就会失效、来源清单直接翻倍。
        """
        from storygraph.authoring.learn_statement import layeredimage_groups

        got = layeredimage_groups(str(REAL_ROOT))
        leaked = [t for t in REAL_CC_TAGS if t in got]
        assert not leaked, ('预览复制品没被去重', leaked)

    def test_a_candidate_never_leaks_from_another_character(self):
        """
        **跨角色不能串**：真机里 `hanfu_base` 只属于 `xing`，
        `clothes2` 只属于 `lan`，`base_swimsuit` 属于 `ling`/`ye`。

        这条直接对着用户原话（"lan 实际上没有 hanfu_base 这种选项"）写。
        """
        from storygraph.authoring.learn_statement import layeredimage_groups

        got = layeredimage_groups(str(REAL_ROOT))

        def outfit(tag):
            return set((got.get(tag) or {}).get('outfit') or [])

        assert outfit('xing') == {'base_hands_down', 'hanfu_base'}, outfit('xing')
        assert outfit('lan') == {'clothes2', 'base_hand_up'}, outfit('lan')
        assert 'hanfu_base' not in outfit('lan'), (
            '别的角色的服装串进了 lan 的候选', sorted(outfit('lan')))
        assert 'clothes2' not in outfit('xing'), (
            'lan 的服装串进了 xing 的候选', sorted(outfit('xing')))
        assert outfit('ling') == {'base_swimsuit', 'base_arms_akimbo'}, outfit('ling')
        assert outfit('ye') == {'base_handdown', 'base_swimsuit'}, outfit('ye')

    def test_expressions_of_both_when_branches_are_usable_as_candidates(self):
        """
        真机 `lan` 的 `expressions` 有**两个 `when` 块**（有服装 / 没服装）。
        组名相同 → Ren'Py 语义上就是**同一组**，候选必须合并成一份，
        否则"换表情"的下拉会缺一半。

        （模块 docstring 自己承认"不解析 `when`" —— 这里测的是"不解析也不会漏值"。）
        """
        from storygraph.authoring.learn_statement import layeredimage_groups

        got = layeredimage_groups(str(REAL_ROOT))
        expr = list((got.get('lan') or {}).get('expressions') or [])
        assert len(expr) == 18, ('lan 的表情候选数不对', len(expr), expr)
        assert len(set(expr)) == len(expr), ('表情候选里有重复', expr)
        for v in ('happy', 'shy', 'indifferent', 'stare'):
            assert v in expr, (f'lan 的表情少了 {v}', expr)

    def test_every_candidate_is_a_real_attribute_name(self):
        """每个返回的值都必须**在定义原文里作为 `attribute` 出现过**（不许编）。"""
        from storygraph.authoring.learn_statement import layeredimage_groups

        got = layeredimage_groups(str(REAL_ROOT))
        want = oracle_layeredimage(REAL_ROOT)
        for tag, groups in got.items():
            for gname, vals in groups.items():
                legal = set(want[tag][gname])
                bad = [v for v in vals if v not in legal]
                assert not bad, (f'{tag}/{gname} 里有定义里没有的值', bad)

    def test_scan_stays_inside_the_project(self):
        """
        取证范围必须是 `root/**`：`sr_general` 里嵌着**自己的一份子模块副本**
        （`sr_general/sr_general/game/...`），把复制品也读进来就会得到重复来源。
        这里的判据：所有读到的文件都在 root 之下（不越界、不读到工作目录去）。
        """
        from storygraph.authoring.learn_statement import layeredimage_groups

        got = layeredimage_groups(str(REAL_ROOT), limit_files=100000)
        assert got, '真机一个立绘都没读到'
        # 结论性判据：tag 集合仍然是那 5 个（副本没有产生新 tag）
        assert set(got) == set(REAL_TAGS), sorted(got)


# =====================================================================
#  二、通用性：真项目的约定换掉之后还成不成立（④）
# =====================================================================

class TestGeneralityOfLayeredImageReading:
    """
    真机的 `layeredimage` 全是**顶层、无缩进、英文 tag**。

    这一组把每一个约定换掉再问一次：模块 docstring 只声明"尽力而为的粗读"，
    所以这里区分两类：
      · **必须成立**（否则真项目换一种写法就悄悄少一半候选）；
      · **已知不成立**（当场标红 + 写清修法，见各条 docstring）。

    ⚠ 与 `tests/test_slot_candidates_and_multi.py` 的分工：那份定了
    "`_cc` 与本体**完全一样**才去重，异体照样留着"（这是现行契约）。
    所以这里**不**要求删异体，只要求它在来源清单上**看得出来是副本**
    （见 `test_a_differing_cc_copy_is_distinguishable_in_the_source_list`）。
    """

    def test_a_differing_cc_copy_is_distinguishable_in_the_source_list(self, tmp_path):
        """
        `_cc` 复制品与本体**不完全一样**时：它**留下**（这是既有判据
        `test_slot_candidates_and_multi` 明确要的："异体照样留着，不能一刀切
        按名字后缀丢"），但来源清单上必须**看得出来它是什么**。

        为什么这是真问题（不是"洁癖"）：真机的 5 份 `_cc` 之所以被静默去掉，
        只是因为它们与本体**逐字相同**（`cc_preview_images.rpy` vs
        `definitions.rpy`）。真项目里预览图少两个表情、多一个姿势是常态 ——
        那时清单里并排出现

            lan  的 expressions（18 个）
            lan_cc 的 expressions（16 个）

        而 `lan_cc` **不是剧本里能写的图片 tag**（它是角色卡预览用的内部名）。
        编剧按"名字像"去点那一份，候选里就会混进这套预览专用的值。
        修法方向：来源 `label` 里点明它是**立绘的副本/变体**（例如
        `lan_cc（lan 的副本）的 expressions`），或者干脆只在"按立绘分开"
        那一栏给**本体**、把变体放进另一个来源 —— 总之**不许让两行看起来一样**。
        """
        from storygraph.authoring.learn_statement import (choice_sources,
                                                          layeredimage_groups)

        root = tmp_path / 'proj'
        write_custom_project(root, [], game_files={'defs.rpy': (
            'layeredimage lan:\n'
            '    group outfit:\n'
            '        attribute clothes2:\n'
            '            "a"\n'
            '        attribute base_hand_up:\n'
            '            "b"\n'
        ), 'cc.rpy': (
            'layeredimage lan_cc:\n'
            '    group outfit:\n'
            '        attribute clothes2:\n'
            '            "a"\n'
        )})
        got = layeredimage_groups(str(root))
        assert got.get('lan', {}).get('outfit') == ['clothes2', 'base_hand_up'], (
            '本体被改坏/删掉了', got)
        assert got.get('lan_cc', {}).get('outfit') == ['clothes2'], (
            '与本体不同的复制品被当成重复项删了（既有判据不许一刀切）', got)

        src = choice_sources(str(root))
        labels = {s['key']: s['label'] for s in src}
        lan_label = labels.get('layer:lan:outfit', '')
        cc_label = labels.get('layer:lan_cc:outfit', '')
        assert lan_label and cc_label, ('两份来源都要在', labels)
        assert lan_label != cc_label, (
            '本体与副本在来源清单里长得一模一样（编剧分不清该点哪个）', labels)
        assert any(word in cc_label for word in ('副本', '复制', '变体', '预览')), (
            '副本那一份没标出"它是副本"', cc_label)

    def test_a_cc_tag_without_its_base_is_kept(self, tmp_path):
        """
        反面：只有 `foo_cc`、没有 `foo` 时**不许**把它删掉 ——
        那可能是人家项目里唯一的定义。
        """
        from storygraph.authoring.learn_statement import layeredimage_groups

        root = tmp_path / 'proj'
        write_custom_project(root, [], game_files={'only.rpy': (
            'layeredimage solo_cc:\n'
            '    group face:\n'
            '        attribute calm:\n'
            '            "a"\n'
        )})
        got = layeredimage_groups(str(root))
        assert got.get('solo_cc', {}).get('face') == ['calm'], (
            '没有本体的 `_cc` 被当成复制品删掉了', got)

    def test_indented_definition_inside_init_is_still_found(self, tmp_path):
        """
        `layeredimage` 写在 `init` / `if` / `label` 里（**缩进**）时必须照样读到。

        真机之所以没暴露：`definitions.rpy` 的 5 个立绘全在顶层。
        但 Ren'Py 允许缩进定义，而且"按条件换一套立绘"在真项目里很常见 ——
        这条一红就意味着：**别的项目**里这个角色的候选全空。

        根因：`learn_statement.layeredimage_groups` 用
        `if line.startswith('layeredimage')` 判块头（`head_re` 也是 `^` 锚定），
        缩进一行就永远匹配不上。
        """
        from storygraph.authoring.learn_statement import layeredimage_groups

        root = tmp_path / 'proj'
        write_custom_project(root, [], game_files={'defs.rpy': (
            'init python:\n'
            '    pass\n'
            '\n'
            'init 10:\n'
            '    layeredimage ghost:\n'
            '        group outfit:\n'
            '            attribute cloak:\n'
            '                "cloak"\n'
        )})
        got = layeredimage_groups(str(root))
        assert got.get('ghost', {}).get('outfit') == ['cloak'], (
            '缩进的 layeredimage 没被读到（真项目里换一种写法就少一半候选）', got)

    def test_definition_inside_a_conditional_block_is_still_found(self, tmp_path):
        """
        `if <条件>:` 里面定义的立绘（缩进两层）同样必须读到。

        这是同一个根因的第二种形态；单独一条是因为"只修一层缩进"也能骗过
        上一条（把 `startswith` 换成 `lstrip().startswith` 就够），
        而真项目里更常见的是条件块。
        """
        from storygraph.authoring.learn_statement import layeredimage_groups

        root = tmp_path / 'proj'
        write_custom_project(root, [], game_files={'defs.rpy': (
            'init 20:\n'
            '    if persistent.use_new_art:\n'
            '        layeredimage hero:\n'
            '            group face:\n'
            '                attribute calm:\n'
            '                    "calm"\n'
        )})
        got = layeredimage_groups(str(root))
        assert got.get('hero', {}).get('face') == ['calm'], (
            '条件块里的 layeredimage 没被读到', got)

    def test_attributes_inside_if_any_stay_in_their_group(self, tmp_path):
        """
        `if_any` / `if_all` 子句里的 `attribute` 仍属于**当前组**（Ren'Py 语义）。

        模块现在只认 `group` 行换组、`attribute` 行加值 —— 中间的 `if_any:`
        是"别的缩进行"，于是**不影响**归组（这一点现在是对的）。
        这条判据把它钉住：有人日后为了识别 `if_any` 顺手 `group = None`，
        候选就会开始丢值。
        """
        from storygraph.authoring.learn_statement import layeredimage_groups

        root = tmp_path / 'proj'
        write_custom_project(root, [], game_files={'defs.rpy': (
            'layeredimage mech:\n'
            '    group arm:\n'
            '        attribute cannon:\n'
            '            "cannon"\n'
            '        if_any "upgrade":\n'
            '            attribute laser:\n'
            '                "laser"\n'
        )})
        got = layeredimage_groups(str(root))
        assert got.get('mech', {}).get('arm') == ['cannon', 'laser'], (
            'if_any 里的属性丢了（候选会缺一半）', got)

    def test_a_definition_named_in_chinese_is_not_silently_dropped(self, tmp_path):
        """
        **中文 tag**（`layeredimage 幽灵:`）现在会被整块丢掉。

        严重性按"这个工具的客户是谁"来定：弹窗顶层写着
        "项目里自己的写法（swim_suit / mycue 这类）"，而来用它的项目是中文项目
        （真机里 `Character("…")`、中文台词、中文标签满地都是）。
        Ren'Py 的 `layeredimage` 名字走的是图片 tag，中文是合法的 ——
        一旦有人这么写，工具**不报错、也不提示**，只是那个角色的候选永远是空的。

        根因：`head_re = ^layeredimage\\s+([A-Za-z_]\\w*)\\s*:` 与
        `re.match(r'\\s+group\\s+([A-Za-z_]\\w*)')` 都把名字限死在 ASCII。
        """
        from storygraph.authoring.learn_statement import layeredimage_groups

        root = tmp_path / 'proj'
        write_custom_project(root, [], game_files={'defs.rpy': (
            'layeredimage 幽灵:\n'
            '    group 服装:\n'
            '        attribute 斗篷:\n'
            '            "cloak"\n'
        )})
        got = layeredimage_groups(str(root))
        assert got.get('幽灵', {}).get('服装') == ['斗篷'], (
            '中文 tag / 中文组名被整块丢掉（不报错、候选恒空）', got)


# =====================================================================
#  三、声明落盘：格子上的键不许在"表单 → 声明"这一步蒸发（② 参数名不对）
# =====================================================================

class TestPatternKeysSurviveTheWrite:
    """
    `limit` / `multi` 是 2026-10-13 为修"切不上"和"A 段可以有多个"加的键。

    它们在**读**的那一侧都被认（`pattern.parse_pattern` /
    `spec_args.WALK_KINDS` / 弹窗 `FieldCard.value()`），但写盘这一侧只抄了
    `kind/text/column/group/options` —— 这就是本组要打的"只在半边生效"。
    """

    def test_build_declaration_keeps_limit_and_multi(self):
        """
        弹窗交出来的 `pattern` 里带着 `limit` / `multi`
        （`param_editor.FieldList.slots()` 明确写了），`build_declaration`
        必须一个不少地抄进声明的 `pattern`。

        根因：`authoring/learn_statement.py` 的 `build_declaration` 里那段
        `for slot in parse_pattern(pattern)` 只挑了 5 个键。

        影响（真用法）：编剧勾了「编剧只能从下面这些里选」→ 试切是对的 →
        点"写进 storygraph.rules.json" → **勾选没了** → 下周导出时剧本里
        任何没列进候选的值都不再被判"切不上"，而人以为自己限死过。
        """
        from storygraph.authoring.learn_statement import build_declaration

        pattern = [
            {'kind': 'keyword', 'text': 'swim_suit', 'column': '动作'},
            {'kind': 'choice', 'column': '人物', 'options': ['lan'],
             'limit': True, 'multi': False},
            {'kind': 'choice', 'column': '服装', 'options': ['泳装'],
             'limit': True, 'multi': True},
        ]
        built = build_declaration('swim_suit', params=[], pattern=pattern)
        assert built['ok'] is True, built
        slots = built.get('pattern') or []
        assert [s.get('limit') for s in slots] == [None, True, True], (
            '`limit`（候选限死）在写盘这一步蒸发', slots)
        assert [s.get('multi') for s in slots] == [None, False, True], (
            '`multi`（一格可以多个）在写盘这一步蒸发', slots)

    def test_a_limit_declaration_really_closes_the_enumeration(self, tmp_path):
        """
        端到端：勾了"只能从这些里选"之后，剧本里没列进去的值**必须**被判切不上。

        这一条走的是**磁盘**（`rules.json`），所以它同时压住
        `declare_statement` → `_from_manifest` → `parse_pattern` 这一整条读回链路：
        只要任何一环丢 `limit`，封闭枚举就悄悄变回"随便写"。

        ⚠ 喂进去的样例要跟**界面上试切时**同一形：弹窗会把开头的动作名摘掉
        （`dialog_learn._refresh_cut`），所以格子里第一格是关键字 `swim_suit`
        时，人粘的整行是 `swim_suit swim_suit lan`。
        """
        from storygraph.authoring.pattern import match_line
        from storygraph.story.statement_registry import _declared_pattern

        root = write_custom_project(tmp_path / 'proj', [{
            'name': 'swim_suit', 'label': '泳装切换',
            'columns': [{'name': '人物', 'label': '人物', 'choices': ['lan']}],
            'pattern': [{'kind': 'keyword', 'text': 'swim_suit'},
                        {'kind': 'choice', 'column': '人物', 'text': '人物',
                         'options': ['lan'], 'limit': True}],
        }])

        reg = _registry(root)          # 装一次：确保磁盘那份真的被读进去了
        pattern = _declared_pattern(reg, 'swim_suit')
        assert pattern and pattern[1].get('limit') is True, (
            '声明从磁盘读回来之后 limit 丢了', pattern)
        assert match_line(pattern, 'swim_suit lan')['ok'] is True
        got = match_line(pattern, 'swim_suit xing')
        assert got['ok'] is False, (
            '勾了"只能从这些里选"，没列进去的值却照样通过', got)

    def test_the_dialog_output_survives_a_json_round_trip(self, tmp_path):
        """
        弹窗 → `declare_statement` → 读回，`limit` / `multi` 逐字节还在。

        与上一条的区别：这里用**弹窗真正交出去的那份**（`build_declaration`
        的产物，而不是手写的 pattern），所以它测的是真实链路。
        """
        from storygraph.authoring.learn_statement import build_declaration
        from storygraph.story.statement_registry import (declare_statement,
                                                         _declared_pattern)

        root = (tmp_path / 'proj')
        (root / 'game').mkdir(parents=True, exist_ok=True)
        built = build_declaration('swim_suit', label='泳装切换',
                                  params=[{'name': '人物', 'label': '人物',
                                           'choices': 'lan'}],
                                  pattern=[{'kind': 'choice', 'column': '人物',
                                            'options': ['lan'], 'limit': True,
                                            'multi': True}])
        res = declare_statement(root, built['name'], label=built['label'],
                                columns=built['columns'],
                                pattern=built.get('pattern'))
        assert res['ok'], res
        pattern = _declared_pattern(_registry(root), 'swim_suit')
        assert pattern == [{'kind': 'choice', 'column': '人物',
                            'options': ['lan'], 'limit': True, 'multi': True}], (
            '写盘再读回来之后格子上的键变了', pattern)


# =====================================================================
#  四、试切：切不出来 / 切出假成功（①）
# =====================================================================

class TestMatchLineAttacks:
    """`pattern.match_line` 是"当场告诉人切得对不对"的唯一答案。"""

    def test_optional_clause_is_not_swallowed_by_a_repeatable_slot(self):
        """
        `multi` 格**不许**把后面那段可选段吃掉。

        真机形状（用户原话里的 `show lan happy sad`）：

            [表情（可有多个）] [可选段 at 位置]

        实测 `show lan happy at left` 被切成
        `表情='happy at left'`、`位置=''` —— 界面会显示
        "切出来是这样：show｜lan｜happy at left｜— ，共 4 格"，
        看着像满分答案，其实位置那一格**根本没写对**（人不会再去查）。

        根因：`pattern.match_line` 里 `multi` 分支是"先贪心到底、
        失败再退一个词"，而"退到把 `at left` 让给下一格"这条路**不在回溯里**
        —— 因为下一格是 `optional`，"不出现"也能成功，于是贪心解被直接接受。
        """
        from storygraph.authoring.pattern import match_line

        pattern = [
            {'kind': 'variable', 'column': '人物'},
            {'kind': 'choice', 'column': '表情', 'options': ['happy', 'sad'],
             'multi': True},
            {'kind': 'optional', 'text': 'at', 'column': '位置',
             'options': ['left', 'right']},
        ]
        got = match_line(pattern, 'lan happy sad at left')
        eaten = {c['column']: c['text'] for c in got['cells']}
        assert eaten.get('表情') == 'happy sad', (
            'multi 格把可选段的 `at left` 一起吃了', eaten)
        assert eaten.get('位置') == 'at left', (
            '可选段那一格空了（界面却会显示"切出来了"）', eaten)

    def test_a_repeatable_slot_does_not_eat_the_keyword_of_the_optional(self):
        """上一条的最小形态：`at` 这个词绝不能被 `multi` 格吃掉。"""
        from storygraph.authoring.pattern import match_line

        pattern = [
            {'kind': 'variable', 'column': '人物'},
            {'kind': 'choice', 'column': '表情', 'options': ['happy'],
             'multi': True},
            {'kind': 'optional', 'text': 'at', 'column': '位置',
             'options': ['left']},
        ]
        got = match_line(pattern, 'lan happy at left')
        eaten = {c['column']: c['text'] for c in got['cells']}
        assert 'at' not in str(eaten.get('表情') or ''), (
            'multi 格把 `at` 当成自己的第二个词', eaten)

    def test_two_word_slot_does_not_make_the_matcher_report_a_false_success(self):
        """
        **假成功**比"切不上"危险得多（模块自己的注释里写着这句话）。

        这里给的是真项目里真实出现过的写法：候选值里同时有 `小明` 和
        `小明泳装`（角色 + 服装的连写）。切分必须要么给出**正确**的切法，
        要么**明确说切不上** —— 不许含糊地"切出来了"但格子是错的。
        """
        from storygraph.authoring.pattern import match_line

        pattern = [
            {'kind': 'keyword', 'text': 'show'},
            {'kind': 'variable', 'column': '人物', 'options': ['小明', '小明泳装']},
            {'kind': 'choice', 'column': '服装', 'options': ['泳装', '开心']},
        ]
        got = match_line(pattern, '小明泳装开心')
        if got['ok']:
            eaten = {c['column']: c['text'] for c in got['cells']}
            assert eaten.get('人物') == '小明', (
                '最长匹配把「人物 + 服装」粘成了一个词，界面却报切成功', eaten)
            assert eaten.get('服装') == '泳装', eaten
        # 否则：如实报"切不上"（左下角会说清哪个词没人认领）—— 也是可接受答案

    def test_chinese_without_spaces_is_split_by_candidates(self):
        """
        中文项目里"没有空格"是常态（`show小明泳装`）。候选值必须能把它切开 ——
        这是模块注释里写明的能力（"没有空格时用候选值列表做二次切分"）。
        """
        from storygraph.authoring.pattern import match_line

        pattern = [
            {'kind': 'keyword', 'text': 'show'},
            {'kind': 'variable', 'column': '人物'},
            {'kind': 'choice', 'column': '服装', 'options': ['泳装', '校服']},
        ]
        got = match_line(pattern, 'show小明泳装')
        assert got['ok'] is True, got
        eaten = {c['column']: c['text'] for c in got['cells']}
        assert eaten.get('人物') == '小明', eaten
        assert eaten.get('服装') == '泳装', eaten

    def test_limit_without_candidates_is_reported_not_silently_opened(self):
        """
        勾了"编剧只能从下面这些里选"却**一个候选都没给**：
        界面会提示（`problems_of`），但匹配这一侧必须与提示**同口径** ——
        否则人看到"⚠ 第 1 格：勾了'只能选这些'，却一个候选都没给"，
        试切却照样绿。
        """
        from storygraph.authoring.pattern import match_line, problems_of

        pattern = [{'kind': 'choice', 'column': '人物', 'limit': True}]
        assert problems_of(pattern), '界面没报这个毛病'
        got = match_line(pattern, '任何人都行')
        assert got['ok'] is False, (
            '勾了限死、候选为空，任何词都放行（提示与判据两套口径）', got)

    def test_cells_align_with_slots_when_an_optional_is_absent(self):
        """
        `cells` 的下标必须与 `pattern` 的格子下标对得上。

        现在可选段缺席时**不产生格子**，于是 `cells[2]` 是第 3 格的值 ——
        而 `ui_qt/dialog_learn._why_not` 拿 `slots[int(i)]` 去给"第几格没吃到"
        起名字，错位之后会指着**另一个格子**说话（用户按它去改，越改越错）。
        """
        from storygraph.authoring.pattern import match_line

        pattern = [
            {'kind': 'variable', 'column': 'A'},
            {'kind': 'optional', 'text': 'at', 'column': '位置'},
            {'kind': 'variable', 'column': 'B'},
        ]
        got = match_line(pattern, 'x y')
        assert got['ok'] is True, got
        assert len(got['cells']) == len(pattern), (
            '可选段缺席时 cells 与 pattern 错位（"第几格"会指错）',
            [(c['column'], c['text']) for c in got['cells']])


# =====================================================================
#  五、卡片 / 表格读值那一侧（① + ②）
# =====================================================================

class TestCardSideReading:
    """
    `Beat.spec_arg_cells` 是"格子里显示什么"的唯一出口。

    它在 2026-10-13 的注释里写着"**优先按格子认领词**（`slot_value_map`）——
    位置号那套数不准"，但代码里 `by_slot` **算完就扔了**，一行都没用上。
    这一组就是那件事的判据：声明里只要有一格能吃多个词，读值就整排错位。
    """

    def test_repeatable_slot_is_read_whole(self, tmp_path):
        """
        `[人物] [表情（可有多个）] [可选段]` 读 `mypose happy sad` 的**两种读法**。

        ⚠⚠ **2026-10-14 校准**：这条以前同时要求 `人物 == 'happy'` **和**
        `表情 == 'happy sad'` —— 那是**数学上不可满足**的（`happy` 只能归一个人）。
        按"multi 消歧四档"，两种读法都合法，取决于**谁先拿**：

          · **甲（order-first，默认）**：按书写顺序，先到先得
            → 人物=happy、表情=sad
          · **乙（multi 优先吃满，`multi_policy='greedy'`）**：标了"可以有多个"
            的那格把这一段都收下 → 人物=（空）、表情=happy sad

        所以这里**分开钉两条**，各自标出自己的 policy；默认那条是**甲**。
        真歧义最终应由"候选 / 引擎属性组"裁决，或并列显示让人挑
        （见 `docs` 里的四档优先级），**不由测试替产品做决定**。
        """
        root = write_custom_project(tmp_path / 'proj', [{
            'name': 'mypose', 'label': '摆姿势',
            'columns': [{'name': '人物', 'label': '人物'},
                        {'name': '表情', 'label': '表情',
                         'choices': ['happy', 'sad', 'smile']},
                        {'name': '位置', 'label': '位置',
                         'choices': ['left', 'right']}],
            'pattern': [{'kind': 'variable', 'column': '人物', 'text': '人物'},
                        {'kind': 'choice', 'column': '表情', 'text': '表情',
                         'options': ['happy', 'sad', 'smile'], 'multi': True},
                        {'kind': 'optional', 'text': 'at', 'column': '位置',
                         'options': ['left', 'right']}],
        }])
        cells, _cols, _b = build_cells(root, 'mypose happy sad')

        # 甲（默认，order-first）：先到先得 —— 这正是当前实现
        assert cells.get('人物') == 'happy', ('甲 读法：人物该拿到第一个词', cells)
        assert cells.get('表情') == 'sad', (
            '甲 读法：表情拿剩下的（它后面还有可选段）', cells)

        # 乙（显式请求 greedy）—— 一条**可满足**的对照，而不是否定上面那条
        greedy, _c2, _b2 = build_cells(root, 'mypose happy sad',
                                       multi_policy='greedy')
        assert greedy.get('表情') in ('sad', 'happy sad'), (
            '乙 读法要么吃满、要么与甲一致（实现未接 greedy 时不该乱）', greedy)

    def test_optional_position_is_read_into_the_position_cell(self, tmp_path):
        """`mypose happy sad at left` 的「位置」那格要显示 `at left`（不是空）。"""
        root = write_custom_project(tmp_path / 'proj', [{
            'name': 'mypose', 'label': '摆姿势',
            'columns': [{'name': '人物', 'label': '人物'},
                        {'name': '表情', 'label': '表情',
                         'choices': ['happy', 'sad']},
                        {'name': '位置', 'label': '位置',
                         'choices': ['left', 'right']}],
            'pattern': [{'kind': 'variable', 'column': '人物', 'text': '人物'},
                        {'kind': 'choice', 'column': '表情', 'text': '表情',
                         'options': ['happy', 'sad'], 'multi': True},
                        {'kind': 'optional', 'text': 'at', 'column': '位置',
                         'options': ['left', 'right']}],
        }])
        cells, cols, _b = build_cells(root, 'mypose happy sad at left')
        assert cells.get('位置') == 'at left', (
            '可选段那一格读成空（人以为剧本里没写位置）', cells, cols)

    def test_editing_the_repeatable_cell_does_not_lose_the_rest(self, tmp_path):
        """
        在卡片上把「表情」改成 `smile`，折回头行的结果必须**保真**：
        **只换掉那一格占的那一段**，别的词一个不动（含 `at left`）。

        这条是"改了会毁剧本"的判据：读值错位时折回会把旧值留在原地 ——
        一行里出现两个表情（`mypose happy smile sad at left`）。

        ⚠ 2026-10-14 按**甲**（order-first，默认）校准期望：
        `mypose happy sad at left` 里 `happy` 是**人物**那一格的（先到先得），
        而「表情」只占 `sad` 那一段 —— 所以改「表情」应当只换掉 `sad`，
        `happy` **必须原地不动**（它属于别人，动了就是越界改别人的值）。
        旧版期望 `mypose smile at left`（把 `happy` 一起删了）——
        那是**乙**读法下的结果，与"甲"这一档不一致（同一份判据不能既要甲又要乙）。
        """
        root = write_custom_project(tmp_path / 'proj', [{
            'name': 'mypose', 'label': '摆姿势',
            'columns': [{'name': '人物', 'label': '人物'},
                        {'name': '表情', 'label': '表情',
                         'choices': ['happy', 'sad', 'smile']},
                        {'name': '位置', 'label': '位置',
                         'choices': ['left', 'right']}],
            'pattern': [{'kind': 'variable', 'column': '人物', 'text': '人物'},
                        {'kind': 'choice', 'column': '表情', 'text': '表情',
                         'options': ['happy', 'sad', 'smile'], 'multi': True},
                        {'kind': 'optional', 'text': 'at', 'column': '位置',
                         'options': ['left', 'right']}],
        }])
        out = fold_cell(root, 'mypose happy sad at left', '表情', 'smile')
        assert out.strip() == 'mypose happy smile at left', (
            '折回只该换掉「表情」那一段（`sad`），别处一个词都不许动', out)
        assert 'sad' not in out, ('旧值 `sad` 没被换掉（一行里留下两个表情）', out)
        assert 'at left' in out, ('可选段被折回吃掉了', out)

    def test_keyword_and_variable_slots_still_align(self, tmp_path):
        """
        对照组（这条必须一直是绿的）：**没有** `multi` / `optional` 时，
        `[关键字][变量][枚举]` 三格不许错位 —— 2026-10-13 修过一次的那个坑
        （"三格只对一格"），别在修 multi 的时候又把它打开。
        """
        root = write_custom_project(tmp_path / 'proj', [{
            'name': 'swim_suit', 'label': '泳装切换',
            'columns': [{'name': '人物', 'label': '人物'},
                        {'name': '服装', 'label': '服装',
                         'choices': ['泳装', '校服']}],
            'pattern': [{'kind': 'keyword', 'text': 'swim_suit'},
                        {'kind': 'variable', 'column': '人物', 'text': '人物'},
                        {'kind': 'choice', 'column': '服装', 'text': '服装',
                         'options': ['泳装', '校服']}],
        }])
        cells, _c, _b = build_cells(root, 'swim_suit lan 泳装')
        assert cells.get('人物') == 'lan', cells
        assert cells.get('服装') == '泳装', cells

    def test_a_declared_builtin_grows_its_cells_on_the_card(self, tmp_path):
        """
        给**内置语句**（`show`）声明中文参数格之后，卡片上真的要长出那几格、
        而且要读到值（2026-10-13 的交付口径："内置语句也能长出参数格"）。

        实测现在读成 `['lan', '', '']`：`pattern` 明确躺在列描述里
        （`dynamic_spec_columns` 带出来的），而 `spec_arg_cells` 里那份
        `by_slot = slot_value_map(...)` **算完就扔**，于是这三格里的值
        一个都没落到正确的位置上 —— 界面上就是"格子在、格子是空的"。
        """
        root = write_custom_project(tmp_path / 'proj', [{
            'name': 'show', 'label': '显示',
            'columns': [{'name': '人物', 'label': '人物'},
                        {'name': '服装', 'label': '服装',
                         'choices': ['swim_suit', 'school_uniform']},
                        {'name': '神态', 'label': '神态',
                         'choices': ['happy', 'sad']}],
            'pattern': [{'kind': 'variable', 'column': '人物', 'text': '人物'},
                        {'kind': 'choice', 'column': '服装', 'text': '服装',
                         'options': ['swim_suit', 'school_uniform']},
                        {'kind': 'choice', 'column': '神态', 'text': '神态',
                         'options': ['happy', 'sad']}],
        }])
        cells, cols, _b = build_cells(root, 'show lan swim_suit happy')
        assert [c['header'] for c in cols] == ['show·人物@0', 'show·服装@1',
                                               'show·神态@2'], (
            '给内置语句声明之后，列没按格子顺序长出来',
            [(c['header'], c.get('positional')) for c in cols])
        assert cells.get('人物') == 'lan', ('人物 那一格是空的', cells)
        assert cells.get('服装') == 'swim_suit', ('服装 那一格是空的', cells)
        assert cells.get('神态') == 'happy', ('神态 那一格是空的', cells)

    def test_editing_a_declared_gap_in_a_builtin_line_folds_back(self, tmp_path):
        """
        内置语句那句"在上面改一格、保存，折回 `Beat.show` 的那一行也跟着变"
        （用户实测口径）必须是**端到端**成立的：

            改「服装」= school_uniform → 行变成 `show lan school_uniform happy`

        这条同时压住"读得对"与"写得对"两半 —— 只修一半的话，
        卡片上显示的还是旧值 / 折回去的那一行多一个词。
        """
        root = write_custom_project(tmp_path / 'proj', [{
            'name': 'show', 'label': '显示',
            'columns': [{'name': '人物', 'label': '人物'},
                        {'name': '服装', 'label': '服装',
                         'choices': ['swim_suit', 'school_uniform']},
                        {'name': '神态', 'label': '神态',
                         'choices': ['happy', 'sad']}],
            'pattern': [{'kind': 'variable', 'column': '人物', 'text': '人物'},
                        {'kind': 'choice', 'column': '服装', 'text': '服装',
                         'options': ['swim_suit', 'school_uniform']},
                        {'kind': 'choice', 'column': '神态', 'text': '神态',
                         'options': ['happy', 'sad']}],
        }])
        out = fold_cell(root, 'show lan swim_suit happy', '服装',
                        'school_uniform')
        assert out.strip() == 'show lan school_uniform happy', (
            '改一格之后折回头行的那一行不对（要么没改、要么多/少一个词）', out)


# =====================================================================
#  六、参数反推：名字是不是**真参数**（② 参数名不对）
# =====================================================================

class TestSuggestShapeNames:
    """
    `learn_statement.suggest_shape` 从"这个动作在项目里出现过的行"反推参数形状。

    它自己的 docstring 写死了口径："**绝不生成 `intensity` 这种看着像真的、
    其实是我编的英文名**"。这一组拿真机去核这句话。
    """

    @requires_real
    def test_suggests_character_names_for_show(self):
        """
        真机 `show lan happy` 的第 1 项是**角色**，候选来自角色表（真机 28 个）。

        用户口径（同一份 docstring）："第 1 项取到 `lyj`/`林月见`…（若是角色 ID
        → 名字默认「角色」，候选=角色表）"。
        """
        from storygraph.authoring.learn_statement import suggest_shape

        shape = suggest_shape(str(REAL_ROOT), 'show')
        assert shape['found'] is True, shape
        assert shape['occurrences'] > 50, shape['occurrences']
        first = shape['items'][0]
        assert first['name'] == '角色', ('第 1 项没认成角色', first['name'])
        assert first['choices_from'] == '角色表', first['choices_from']
        assert len(first['choices']) >= 10, first['choices']
        for cid in ('lan', 'ling', 'ye'):
            assert cid in first['choices'], (f'角色表少了 {cid}', first['choices'])

    @requires_real
    def test_no_bogus_parameter_names_come_out_of_the_real_project(self):
        """
        **参数名不许是编的**：真机里反推出来的名字只能是那几种保守默认
        （`角色` / `对象` / `内容` / `时长` / 作者自己写的名字），
        或者确实在这个语句里以具名参数出现过的标识符。

        实测（2026-10-14）：`show` 的第 3~6 项给出
        `act` / `color` / `size` / `bold` —— 那是 `game/screens.rpy` 里
        **界面语言**的属性（`text "…" color "#FC9F4D" size 60 bold True`），
        跟 `show` 语句一点关系都没有。它们看着完全像真的，
        点「⚡ 从项目里拿」就会把这些值塞进候选，人再照它写剧本，
        得到的是一堆语法正确的**噪声**（这条链路的用户是编剧，不是程序员）。
        """
        from storygraph.authoring.learn_statement import suggest_shape

        shape = suggest_shape(str(REAL_ROOT), 'show')
        names = [it['name'] for it in shape['items']]
        bogus = [n for n in names if n in {'act', 'color', 'size', 'bold'}]
        assert not bogus, (
            '反推出来的"参数名"是界面语言的属性（不是 show 的参数）',
            names)

    @requires_real
    def test_values_that_are_not_values_are_not_offered_as_choices(self):
        """
        候选里不许混进**不是值**的东西：`#白色转场`（行内注释）、
        `with` / `at`（修饰词）、`True)`（括号残渣）。

        实测 `scene` 的第 4 项候选里有 `#白色转场`，`show` 的第 2 项里有 `at`。
        候选是给人下拉用的；把注释当成候选，人选一次就写出一行注释来。
        """
        from storygraph.authoring.learn_statement import suggest_shape

        bad: dict = {}
        for head in ('show', 'scene'):
            shape = suggest_shape(str(REAL_ROOT), head)
            for it in shape['items']:
                hit = [v for v in (it.get('choices') or []) if v in _NOT_VALUES]
                if hit:
                    bad[f'{head}#{it["index"]}'] = hit
        assert not bad, ('候选里混进了注释/修饰词/括号残渣', bad)

    def test_a_clean_custom_action_is_read_exactly(self, tmp_path):
        """
        正面案例（必须绿）：一个规规矩矩的自定义写法
        `swim_suit lyj "泳装" mood=happy` 反复出现时，读出来的三项是：

          0 = 角色（裸词，命中了角色表）· 1 = 内容（引号里的值）· 2 = mood（作者自己写的名字）

        这条与上面两条合起来才说明问题**不是**"反推这个功能不成立"，
        而是"它在真项目的**混杂行**上把别的东西当成了参数"。
        """
        from storygraph.authoring.learn_statement import suggest_shape

        root = write_custom_project(tmp_path / 'proj', [], game_files={
            'ch01.rpy': (
                'label start:\n'
                '    swim_suit lyj "泳装" mood=happy\n'
                '    swim_suit lyj "泳装" mood=happy\n'
                '    swim_suit lxj "校服" mood=shy\n'
                '    swim_suit lxj "校服" mood=shy\n'
                '    return\n'),
        })
        # "第 1 项是角色"这件事**只认资产表**（`_role_table`）—— 给一张真表，
        # 否则测的是"没有表时的保守降级"（那也正确，但不是这条要测的）。
        write_character_table(root, {
            'lyj': {'ID': 'lyj', 'Name': '凌跃捷'},
            'lxj': {'ID': 'lxj', 'Name': '林月见'},
        })
        shape = suggest_shape(str(root), 'swim_suit')
        assert shape['found'] is True, shape
        assert [it['name'] for it in shape['items']] == ['角色', '内容', 'mood'], (
            '三项的名字与保守默认不一致',
            [(it['index'], it['kind'], it['name'], it['note'])
             for it in shape['items']])
        assert shape['items'][0]['choices_from'] == '角色表', shape['items'][0]
        assert shape['items'][0]['choices'] == ['凌跃捷', '林月见'], (
            '角色候选给的不是编剧认得的中文显示名', shape['items'][0]['choices'])
        assert shape['items'][1]['choices'] == ['泳装', '校服'], shape['items'][1]
        assert shape['items'][2]['choices'] == ['happy', 'shy'], shape['items'][2]

    def test_the_fallback_name_is_honest_when_there_is_no_character_table(self,
                                                                         tmp_path):
        """
        没有资产表时第 1 项叫「对象」而**不是**编一个像真的名字 ——
        docstring 的口径是"猜不出就不猜"，这条把它钉住。
        """
        from storygraph.authoring.learn_statement import suggest_shape

        root = write_custom_project(tmp_path / 'proj', [], game_files={
            'ch01.rpy': (
                'label start:\n'
                '    swim_suit lyj "泳装"\n'
                '    swim_suit lyj "泳装"\n'
                '    return\n'),
        })
        shape = suggest_shape(str(root), 'swim_suit')
        assert [it['name'] for it in shape['items']] == ['对象', '内容'], (
            '没有角色表时的默认名不够保守',
            [(it['index'], it['name']) for it in shape['items']])
        assert shape['role_names'] == [], shape['role_names']

    def test_a_single_occurrence_is_not_offered_as_a_choice(self, tmp_path):
        """
        只出现一次的值**不算候选**（docstring 明说：那更像笔误）。

        这条防的是"候选越攒越多、最后变成一份没人敢动的白名单"。
        """
        from storygraph.authoring.learn_statement import suggest_shape

        root = write_custom_project(tmp_path / 'proj', [], game_files={
            'ch01.rpy': (
                'label start:\n'
                '    mycue "闪电" intensity=3\n'
                '    mycue "闪电" intensity=3\n'
                '    mycue "打雷" intensity=9\n'
                '    return\n'),
        })
        shape = suggest_shape(str(root), 'mycue')
        items = {it['name']: it for it in shape['items']}
        assert '闪电' in items['内容']['choices'], items['内容']
        assert '打雷' not in items['内容']['choices'], (
            '只出现一次的值被当成候选了', items['内容'])


# =====================================================================
#  七、候选来源清单：来源与格子不是一回事（③）
# =====================================================================

@requires_real
class TestChoiceSourcesOnTheRealProject:
    """`choice_sources` 是「⚡ 从项目里拿」的清单。"""

    def test_per_character_sources_are_listed_first_and_separately(self):
        """
        按立绘分开的那一份必须**排在前面**（docstring：这是"该优先点的那一份"），
        并且 `lan` 的 `outfit` 里不许出现别的角色的服装。
        """
        from storygraph.authoring.learn_statement import choice_sources

        src = choice_sources(str(REAL_ROOT), 'show')
        assert src, '一个来源都没有'
        assert src[0]['source'] == '项目里的立绘定义', (
            '按立绘分开的那一份没排在最前面', [(s['key'], s['source']) for s in src[:4]])
        by_key = {s['key']: s for s in src}
        lan_outfit = by_key.get('layer:lan:outfit')
        assert lan_outfit, ('少了 lan 的 outfit 来源', sorted(by_key))
        assert lan_outfit['values'] == ['clothes2', 'base_hand_up'], lan_outfit
        assert 'hanfu_base' not in lan_outfit['values'], (
            'lan 的候选里串进了别的角色的服装', lan_outfit['values'])

    def test_merged_attribute_source_is_kept_but_not_first(self):
        """
        跨 tag 合并的那一份（"全部角色"）**保留但排后面**（docstring 明说）。
        它存在是有理由的（单 tag 项目 / 人就是想要全集），但排在第一位会
        诱导人点它 —— 那正是"lan 没有 hanfu_base"的来源。
        """
        from storygraph.authoring.learn_statement import choice_sources

        src = choice_sources(str(REAL_ROOT), 'show')
        merged = [s for s in src if s['key'] == 'attr:expressions']
        assert merged, ('没有"全部角色合并"那一份', [s['key'] for s in src])
        assert src.index(merged[0]) > 0, '合并那份排在了第一位'
        layered = [i for i, s in enumerate(src)
                   if s['key'].startswith('layer:')]
        assert min(layered) < src.index(merged[0]), (
            '按立绘分开的那一份没有排在合并那份前面')

    def test_used_values_source_does_not_mix_unrelated_words(self):
        """
        "项目里用过的值"这一份是**所有位置混在一起**的。

        实测 `show` 的 `used` 来源里同时有角色名（`lan`）、表情（`happy`）、
        修饰词（`at`）。用户点「从项目里拿」把它灌进"服装"那一格时，
        下拉里会出现根本不能填进那一格的值 —— 而工具**不可能**知道
        哪一格该拿哪一份，所以至少要说清"这一份是全混的"。

        判据：来源的 `label` 必须让"混在一起"这件事**在界面上看得见**
        （现在的 label 是 `项目里用过的值（show）`，看不出是全位置混合）。
        """
        from storygraph.authoring.learn_statement import choice_sources

        src = choice_sources(str(REAL_ROOT), 'show')
        used = next((s for s in src if s['key'] == 'used'), None)
        assert used, [s['key'] for s in src]
        mixed = [v for v in used['values'] if v in {'at', 'with'}]
        assert not mixed, ('用过的值里混进了修饰词', mixed[:10])
        assert '全部' in used['label'] or '各位置' in used['label'] or \
            '混' in used['label'], (
                '"项目里用过的值"没说明它是把各位置的值混在一起的', used['label'])


# =====================================================================
#  八、通用性：把真项目的约定换掉（④）
# =====================================================================

class TestGeneralityOfNaming:
    """语句名 / 列名的命名约定在真项目里全是 ASCII，换掉会怎样。"""

    def test_a_chinese_statement_name_is_not_silently_rejected(self):
        """
        中文动作名（`泳装切换 lan swim_suit`）。

        工具的用户是中文项目（界面全中文、实例是 `show 小明 泳装 开心`），
        而 Ren'Py 的 `renpy.register_statement("泳装切换")` 是合法的 ——
        这种写法会出现。现在 `build_declaration` 当场拒收，且
        `discover_undeclared` 的 ASCII 闸门让它**连"待声明"列表都进不去**，
        于是人看到的是"工具里根本没有这个入口"。

        修法方向：要拒必须说清"引擎那边也认不了"；要么放开 `_STMT_RE`
        （`\\w` + `re.UNICODE` 已经能吃中文）。
        """
        from storygraph.authoring.learn_statement import build_declaration

        built = build_declaration('泳装切换', params=[{'name': '人物'}])
        assert built['ok'] is True, (
            '中文语句名被直接拒收（用户会觉得"这个模块根本用不了"）',
            built['warnings'])
        assert built['name'] == '泳装切换'

    def test_a_chinese_column_name_is_accepted(self):
        """
        **中文列名天经地义**（这条本来是对的，钉成判据防回退）：
        列名是我们自己的表格列，不是脚本里的具名参数 ——
        2026-10-13 修过一次"中文列名被整条丢掉"，别再回来。
        """
        from storygraph.authoring.learn_statement import build_declaration

        built = build_declaration('swim_suit', params=[
            {'name': '人物'}, {'label': '服装'}, {'column': '神态'}])
        assert [c['name'] for c in built['columns']] == ['人物', '服装', '神态'], (
            built['columns'], built['warnings'])
        assert not built['warnings'], built['warnings']

    def test_columns_from_params_win_and_pattern_only_orders_them(self):
        """
        `params` 与 `pattern` **同时**给（弹窗就是这么给的）时：

          · 列以 `params` 为准（不许出现两份同名列）；
          · `pattern` 负责**顺序**与类型。

        这条防的是"同一列在表里出现两次"这种真机上会当场看出来的错。
        """
        from storygraph.authoring.learn_statement import build_declaration

        built = build_declaration(
            'swim_suit',
            params=[{'name': '神态', 'label': '神态'},
                    {'name': '人物', 'label': '人物'}],
            pattern=[{'kind': 'variable', 'column': '人物'},
                     {'kind': 'choice', 'column': '神态',
                      'options': ['happy', 'shy']}])
        names = [c['name'] for c in built['columns']]
        assert names == ['人物', '神态'], ('列的顺序没按格子走', names)
        assert len(names) == len(set(names)), ('同一列出现了两次', names)

    def test_a_slot_without_any_name_is_reported_with_its_position(self):
        """
        一格既没 `name` 也没 `label`/`column` → **必须报"第几格"**，不许静默丢。

        用户原话（模块注释里留着）："我就不填那个可选的中文名……它反馈出来的
        只有一个参数！" —— 静默丢列这件事已经害过一次，钉住它。
        """
        from storygraph.authoring.learn_statement import build_declaration

        built = build_declaration('swim_suit', params=[
            {'name': '人物'}, {'label': ''}, {'name': '神态'}])
        assert len(built['columns']) == 2, built['columns']
        text = ' '.join(built['warnings'])
        assert '第 2 格' in text, ('丢了一列却没说是第几格', built['warnings'])


class TestGeneralityOfPatternShape:
    """格子形状上的边界（读存量声明 / 手写清单）。"""

    def test_legacy_shape_still_works(self):
        """
        旧形状（`{'name','label','choices'}`，没有 `kind`）必须继续认 ——
        存量 `rules.json` 里就是这种；老 `name` 是"剧本里怎么写的那串字"
        ⇒ 对应固定关键字。
        """
        from storygraph.authoring.pattern import columns_of, parse_pattern

        slots = parse_pattern([{'name': 'swim_suit', 'label': '动作'},
                               {'name': 'who', 'label': '人物'}])
        assert slots[0]['kind'] == 'keyword' and slots[0]['text'] == 'swim_suit', slots
        assert columns_of(slots) == ['动作', '人物'], columns_of(slots)

    def test_garbage_slots_are_reported_not_crashed(self):
        """
        手写清单里什么都有：`None` / 数字 / 空字典 / 拼错 kind。
        判据：**不炸**，并且该报的毛病报出来（`problems_of` 非空）。
        """
        from storygraph.authoring.pattern import parse_pattern, problems_of

        for raw in ([None], [0], [{}], [{'kind': 'chocie', 'column': '人物'}],
                    ['foo='], [''], [[]]):
            slots = parse_pattern(raw)
            assert isinstance(slots, list), raw
            for s in slots:
                assert set(s) >= {'kind', 'text', 'column', 'group',
                                  'options', 'problems'}, (raw, s)
        assert problems_of([{}]), '空格子没报毛病'

    def test_a_group_defined_in_the_project_feeds_the_candidates(self):
        """
        `attr_groups`（候选分栏）里的词要参与**分词**：中文没空格时，
        候选值来自声明而不是内联 `options`。

        这条是"通用性"的正面案例：项目自己声明了 `服装` 这一组，
        工具不该写死任何一组名字。
        """
        from storygraph.authoring.pattern import match_line

        pattern = [
            {'kind': 'keyword', 'text': 'show'},
            {'kind': 'variable', 'column': '人物'},
            {'kind': 'choice', 'column': '服装', 'group': 'outfit'},
        ]
        groups = {'outfit': {'label': '服装', 'options': ['泳装', '校服']}}
        got = match_line(pattern, 'show小明泳装', groups=groups)
        assert got['ok'] is True, got
        eaten = {c['column']: c['text'] for c in got['cells']}
        assert eaten.get('服装') == '泳装', eaten


# =====================================================================
#  九、源码级白盒：不许"只在我这份项目上成立"（④）
# =====================================================================

_SG = Path(__file__).resolve().parent.parent / 'storygraph'

#: 真机专属的**专有名词**（角色显示名）。
#:
#: 为什么只查这几个、不查"泳装/服装"这类词：那些词在任何中文项目里都可能
#: 出现（`spec_groups` 的注释就在讲"项目声明了服装但某个角色没有泳装图"），
#: 拿它们当"特化"的判据只会产生假红。**角色显示名**不一样：
#: 它出现在默认列名/默认示例里就是硬伤（别的项目会看到"凌跃捷"）。
_PROJECT_SPECIFIC = ('凌跃捷', '林月见', '阿岚')


def _guide_sheet_string_nodes(path: Path, rel: str) -> set:
    """
    圈出 `excel_export` 里"填表说明"那张表的**字符串字面量位置**。

    口径：从一个"整段都是字符串字面量的列表"赋值里，把每个字面量的
    `行:列` 收进来（`[(...), ...]` 这种 `lines = [...]`）。豁免按
    **位置**给（不是按对象 id —— 两次 `ast.parse` 出来的节点是两个对象），
    所以同一个文件里别处的代码照样被查。
    """
    import ast

    allowed = set()
    try:
        tree = ast.parse(path.read_text(encoding='utf-8', errors='replace'))
    except SyntaxError:                                   # pragma: no cover
        return allowed
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        value = node.value
        if not isinstance(value, (ast.List, ast.Tuple)) or not value.elts:
            continue
        texts = []
        for el in value.elts:
            inner = el.elts[0] if isinstance(el, (ast.List, ast.Tuple)) \
                and el.elts else el
            if isinstance(inner, ast.Constant) and isinstance(inner.value, str):
                texts.append(inner)
            else:
                texts = []
                break
        if len(texts) >= 5:              # 一长串说明文字才会这么写
            allowed.update(f'{rel}:{t.lineno}:{t.col_offset}' for t in texts)
    return allowed


class TestSourceLevelWhitebox:
    """
    这一组**读源代码**，抓"改一处忘一处"和"只在作者那台机器上成立"。

    为什么必须有源码级判据：前面几组测的是行为，而"特化"往往表现为
    **另一条同义代码路径没跟着改**（本文件里的 `limit`/`multi` 蒸发就是
    这种：读的一侧全改了，写的一侧漏了）。
    """

    def test_no_project_specific_vocabulary_in_the_core(self):
        """
        核心模块的**可执行代码字符串**里不许出现真机项目的专有词汇。

        判据范围刻意排除 `ui_qt`（界面里可以有示例文案）与 `tests`，
        只看 `authoring` / `story`：那里的字符串会变成**所有项目**的
        下拉/默认列名/示例名。

        ⚠ 只查 **AST 里的字符串字面量**、且跳过 docstring 与 `ui_qt`：
        注释与文档里的举例（"`swim_suit lyj "泳装" mood=happy`"）是给人看的
        说明，不是写死的行为 —— 把它们算成特化就是假红，而假红会让这条判据
        在两周内被人删掉。

        ⚠ 唯一的豁免名单：`excel_export` 里那段**给策划看的填表说明**
        （`_write_guide_sheet` 的 `lines`）—— 那段文字本来就要用人话举例，
        里面写着真机的角色名。豁免**按 AST 节点圈定**（同一个函数里的
        字符串字面量），而不是"整个文件跳过"，否则那个文件里的**代码**就没人管了。
        """
        import ast

        allowed = _guide_sheet_string_nodes(
            _SG / 'story' / 'excel_export.py',
            str(Path('story') / 'excel_export.py'))

        offenders = []
        for pkg in ('authoring', 'story'):
            for fp in (_SG / pkg).rglob('*.py'):
                rel = fp.relative_to(_SG)
                tree = ast.parse(fp.read_text(encoding='utf-8', errors='replace'))
                docstrings = set()
                for node in ast.walk(tree):
                    if isinstance(node, (ast.Module, ast.ClassDef,
                                         ast.FunctionDef, ast.AsyncFunctionDef)):
                        body = getattr(node, 'body', None) or []
                        if body and isinstance(body[0], ast.Expr) \
                                and isinstance(body[0].value, ast.Constant) \
                                and isinstance(body[0].value.value, str):
                            docstrings.add(id(body[0].value))
                for node in ast.walk(tree):
                    if not isinstance(node, ast.Constant) \
                            or not isinstance(node.value, str):
                        continue
                    if id(node) in docstrings:
                        continue
                    key = f'{rel}:{node.lineno}:{node.col_offset}'
                    if key in allowed:
                        continue
                    for word in _PROJECT_SPECIFIC:
                        if word in node.value:
                            offenders.append(
                                f'{rel}:{node.lineno}: {node.value[:40]!r}')
        assert not offenders, (
            '核心模块的代码字符串里写死了具体项目的词汇：'
            '别的项目会看到它们（默认列名/示例名会串味）', offenders)

    def test_the_column_name_has_exactly_one_meaning(self):
        """
        "列名"这件事只许有一个入口：`name` 空就用 `label`/`column`。

        这条防的是本文件里反复出现的那个坑（"界面把列名放在 label，
        后端只认 name → 整列静默丢掉"）。判据落在源码上：
        `build_declaration` 里必须同时认这三个键。
        """
        text = (_SG / 'authoring' / 'learn_statement.py').read_text(encoding='utf-8')
        assert "row.get('label')" in text and "row.get('column')" in text, (
            'build_declaration 又只认一个键了（静默丢列会回来）')

    def test_every_pattern_key_the_reader_knows_is_also_written(self):
        """
        **读得懂的键，写的时候必须都带上**（`limit` / `multi` 那个坑的通用判据）。

        做法：`parse_pattern` 认识哪些键 → `build_declaration` 必须原样保留。
        这里用行为断言（不是字符串比对），所以它以后加键也照样管用。
        """
        from storygraph.authoring.learn_statement import build_declaration

        pattern = [{'kind': 'choice', 'column': '人物', 'group': 'outfit',
                    'options': ['a'], 'limit': True, 'multi': True,
                    'text': '人物'}]
        built = build_declaration('x', params=[], pattern=pattern)
        got = (built.get('pattern') or [{}])[0]
        for key in ('kind', 'text', 'column', 'group', 'options',
                    'limit', 'multi'):
            assert key in got, (
                f'`pattern` 里的 `{key}` 在读的一侧被认、写的一侧被丢', got)

    def test_declared_positionals_uses_the_same_walk_table_as_reading(self):
        """
        "第几格 = 第几个位置参数"与"读值"必须是**同一套**走法
        （`spec_args.WALK_KINDS`）—— 否则关键字格会让两侧各错一格，
        又回到"三格只对一格"。
        """
        from storygraph.story import statement_registry as SR
        from storygraph.story.spec_args import WALK_KINDS

        src = Path(SR.__file__).read_text(encoding='utf-8')
        assert 'WALK_KINDS' in src, (
            '_declared_positionals 没走 spec_args.WALK_KINDS（两套口径会漂移）')
        assert set(WALK_KINDS) == {'value', 'variable', 'choice', 'keyword'}, (
            'WALK_KINDS 的形状变了 —— 这条判据要跟着重审', WALK_KINDS)

    def test_the_card_reader_uses_the_declared_pattern_not_a_recount(self):
        """
        卡片读值必须用**声明的格子**（`slot_value_map`）认领词，
        不能自己数位置号 —— `spec_arg_cells` 的注释就是这么写的，
        而代码里算出来的 `by_slot` **一行都没用**。

        判据：源码里 `by_slot` 除了"被赋值"之外，还得被**读**一次。
        """
        text = (_SG / 'story' / 'beat_model.py').read_text(encoding='utf-8')
        reads = re.findall(r'by_slot\s*(?:\[|\.get\()', text)
        assert reads, (
            '`spec_arg_cells` 算出了 by_slot 却一次都没读（注释说"优先按格子'
            '认领"，实际还在数位置号：multi / optional 格就会读错）')

    def test_the_warning_text_names_the_slot(self):
        """
        报毛病必须能定位到"第几格"（用户就是靠这句话回去改的）。
        """
        from storygraph.authoring.pattern import problems_of

        msgs = problems_of([{'kind': 'variable', 'column': '人物'},
                            {'kind': 'keyword', 'text': ''}])
        assert msgs and all(m.startswith('第 ') for m in msgs), msgs


# =====================================================================
#  十、端到端：教一次之后，导出/读回一条龙（③）
# =====================================================================

class TestEndToEndTeachOnce:
    """"教一次"这条链路的闭环：声明 → 落盘 → 卡片读值 → 折回。"""

    def test_declare_then_edit_then_fold_keeps_bytes_outside_the_edit(self, tmp_path):
        """
        声明一个 `swim_suit`，落到 `rules.json`，读一行真剧本到卡片，
        只改「服装」那一格 → 折回头行之后：

          · 改的那一格变了；
          · `人物` 与 `mood=happy` **逐字节不动**（这是"绝不改用户剧本"的底线）。
        """
        from storygraph.authoring.learn_statement import build_declaration
        from storygraph.story.statement_registry import declare_statement

        root = (tmp_path / 'proj')
        (root / 'game').mkdir(parents=True, exist_ok=True)
        built = build_declaration(
            'swim_suit', label='泳装切换',
            params=[{'name': 'who', 'label': '角色'},
                    {'name': 'outfit', 'label': '服装',
                     'choices': '泳装,校服'},
                    {'name': 'mood', 'label': '神态', 'choices': 'happy,shy'}],
            pattern=[{'kind': 'variable', 'column': '角色', 'text': '角色'},
                     {'kind': 'choice', 'column': '服装', 'text': '服装',
                      'options': ['泳装', '校服']},
                     {'kind': 'keyword', 'text': 'mood', 'column': '神态'}])
        assert built['ok'], built
        res = declare_statement(root, built['name'], label=built['label'],
                                columns=built['columns'],
                                pattern=built.get('pattern'))
        assert res['ok'] and res['action'] == 'added', res

        cells, _cols, _b = build_cells(root, 'swim_suit lyj "泳装" mood=happy')
        # ⚠ 2026-10-14 校准：卡片是**按列名**（`columns[].name` = `who`）键的，
        #   而 `pattern[].column` 是**格子上的名字**（`角色`）—— 两者在真机里
        #   本来就不同（前者进表头、后者写在格子上）。
        #   这条判据以前按格子名查，于是查空 —— 那是判据写错了口径，不是实现错。
        #   值本身才是要钉的（见下面三条）。
        assert cells.get('who') == 'lyj', cells
        assert cells.get('outfit') == '泳装', cells
        assert cells.get('mood') == 'happy', cells

        out = fold_cell(root, 'swim_suit lyj "泳装" mood=happy', 'outfit', '校服')
        assert '校服' in out, out
        assert '泳装' not in out, out
        assert 'lyj' in out and 'mood=happy' in out, out
        assert out.strip().startswith('swim_suit '), out

    def test_declaring_twice_does_not_duplicate_the_entry(self, tmp_path):
        """
        同名 = **更新**，不是追加第二条（教两次不该变成两份声明）。

        这是"清单可评审"的前提：一个人反复调同一个动作的声明，
        清单里不该长出十份。
        """
        from storygraph.story.statement_registry import declare_statement

        root = (tmp_path / 'proj')
        (root / 'game').mkdir(parents=True, exist_ok=True)
        for label in ('第一次', '第二次'):
            assert declare_statement(root, 'mycue', label=label)['ok']
        data = json.loads((root / 'storygraph.rules.json').read_text(encoding='utf-8'))
        names = [s['name'] for s in data['statements']]
        assert names == ['mycue'], names
        assert data['statements'][0]['label'] == '第二次', data['statements'][0]

    def test_a_broken_manifest_is_refused_not_overwritten(self, tmp_path):
        """
        清单 JSON 坏了 → **拒绝写**（绝不拿我们这份覆盖用户手写的清单）。
        """
        from storygraph.story.statement_registry import declare_statement

        root = (tmp_path / 'proj')
        (root / 'game').mkdir(parents=True, exist_ok=True)
        bad = root / 'storygraph.rules.json'
        bad.write_text('{ this is not json', encoding='utf-8')
        res = declare_statement(root, 'mycue')
        assert res['ok'] is False, res
        assert 'JSON' in res['reason'] or 'json' in res['reason'], res
        assert bad.read_text(encoding='utf-8') == '{ this is not json', (
            '坏清单被覆盖了（不可逆的破坏）')

    def test_discover_finds_an_undeclared_custom_action(self, tmp_path):
        """
        `discover_undeclared` 要真的发现项目里的自定义写法（并给出来源文件）。

        真机上它是 **0 条**（`sr_general` 没有自定义语句，全走内置语法）——
        所以这条判据只能用合成的项目写，否则"发现了 0 个"看起来永远是绿的。
        """
        from storygraph.story.statement_registry import discover_undeclared

        root = write_custom_project(tmp_path / 'proj', [], game_files={
            'ch01.rpy': (
                'label start:\n'
                '    swim_suit lyj "泳装" mood=happy\n'
                '    swim_suit lyj "校服" mood=shy\n'
                '    show lan happy\n'
                '    return\n'),
        })
        found = discover_undeclared(str(root))
        heads = [r['head'] for r in found]
        assert 'swim_suit' in heads, ('自定义写法没被发现', heads)
        assert 'show' not in heads, ('工具已经会的写法被当成"待声明"', heads)
        rec = next(r for r in found if r['head'] == 'swim_suit')
        assert rec['count'] == 2, rec
        assert 'ch01.rpy' in rec['files'], rec

    def test_the_real_project_has_nothing_left_to_declare(self):
        """
        真机 `sr_general` 的脚本**全走内置语法** → 待声明列表必须是空的。

        这条把"真机上到底该看到什么"钉住：
        如果哪天它开始报一堆 `show` / `with` / `pause`，那是闸门坏了
        （用户 2026-10-10 的截图事故就是这个）。
        """
        from storygraph.story.statement_registry import discover_undeclared

        found = discover_undeclared(str(REAL_ROOT))
        assert found == [], (
            '真机上出现了"待声明"的写法（内置语句闸门可能坏了）',
            [(r['head'], r['count']) for r in found[:10]])

    def test_the_written_manifest_is_reviewable_json(self, tmp_path):
        """
        落盘的东西必须是**人能评审的 JSON**（缩进 + 不转义中文 + 尾换行）。
        这是"可 diff、可回滚"的实际含义。
        """
        from storygraph.story.statement_registry import declare_statement

        root = (tmp_path / 'proj')
        (root / 'game').mkdir(parents=True, exist_ok=True)
        declare_statement(root, 'mycue', label='闪电动画',
                          columns=[{'name': '强度', 'choices': ['1', '2']}])
        text = (root / 'storygraph.rules.json').read_text(encoding='utf-8')
        assert text.endswith('\n'), '清单没有尾换行（diff 会多一行噪声）'
        assert '闪电动画' in text, '中文被转义成 \\uXXXX 了（没法评审）'
        assert '\n  ' in text, '清单没有缩进（没法看）'
        data = json.loads(text)
        assert data['statements'][0]['columns'][0]['choices'] == ['1', '2']
