"""
**友好项目**判据：验证用户那条原则真的成立
=============================================

> 用户 2026-10-11："工具的价值不在于'不依赖程序'，而在于**'依赖程序时成本最低'**。
>  程序写一行 CDS 注册，工具自动派生声明；程序写一个验证函数，工具自动调用。
>  这才是团队协作该有的效率。"

这份判据就把那句话**逐条验一遍**：

  ① 程序写**一行** `renpy.register_statement("mycue", …)` →
     工具**自动派生**出 `mycue` 的声明（**不需要**人手写 `rules.json`）；
  ② 于是 `mycue` 在工具眼里**是认识的**：不是"未声明写法"、也不进"发现"列表；
  ③ 程序**不写**的那一行，工具**必须**报出来（否则"自动跟上"没有意义）；
  ④ 声明里写的**参数名/形状**要能跟着 CDS 代码走（改 CDS → 声明跟着改）。

⚠ 与地狱项目相对：地狱项目验"工具扛不扛得住怪东西"，友好项目验
"**程序花最小力气，工具能走多远**"。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

#: 程序"多写的那一行"—— 这是整个契约的核心：**仅此而已**，没有 rules.json。
CDS_FILE = '''init python:
    renpy.register_statement("mycue", parse=lambda l: l.eol())
'''


def _friendly_project(tmp_path, *, cds=True, use=True):
    """造一个**友好项目**：程序写了 CDS 注册（可选），剧本里用了它。"""
    root = tmp_path / 'friendly'
    game = root / 'game'
    game.mkdir(parents=True)
    (game / 'images').mkdir(parents=True, exist_ok=True)
    if cds:
        (game / 'zz_cds.rpy').write_text(CDS_FILE, encoding='utf-8')
    if use:
        (game / 'ch01.rpy').write_text(
            'label ch01:\n    mycue "闪电"\n    return\n', encoding='utf-8')
    return root


class TestFriendlyProject:
    """程序多写一行 → 工具自动跟上。"""

    def test_the_tool_derives_the_declaration_from_the_cds_line(self, tmp_path):
        """
        ① 程序只写了一行 `register_statement` →
        工具**自己扫出**这个声明（`rules.json` 里什么都没有）。
        """
        from storygraph.story.statement_registry import (StatementRegistry,
                                                         clear_cache)
        root = _friendly_project(tmp_path)
        assert not (root / 'storygraph.rules.json').exists(), \
            '友好项目的前提就是：人手什么都不用写'
        clear_cache()
        reg = StatementRegistry.for_project(root)
        assert 'mycue' in reg.names(), \
            f'工具没能从 CDS 派生声明：{sorted(reg.names())}'

    def test_a_declared_cds_head_is_not_reported_as_unknown(self, tmp_path):
        """
        ② 派生出来之后，`mycue` 就是**认识的** ——
        不许出现在"还没声明的写法"里（那会把人教错）。
        """
        from storygraph.story.project_conventions import unknown_statements
        from storygraph.story.statement_registry import clear_cache
        root = _friendly_project(tmp_path)
        clear_cache()
        assert 'mycue' not in unknown_statements(root), \
            '程序已经写了 CDS，工具还把它当"不认识"'

    def test_a_quoted_argument_is_indistinguishable_from_dialogue(self, tmp_path):
        """
        ⭐⭐ **这条是边界，不是缺陷**（第一版判据写错了，被真机纠正）：

        `mycue "闪电"` 与「角色 `mycue` 说『闪电』」**在字面上完全同形** ——
        只靠读文本，**任何**扫描器都分不出来（信息不足，不是没做好）。

        所以正确的分工是：
          · **扫描器**：对同形的东西**不猜**（宁可不报，也不能把台词当写法）；
          · **引擎**：`lint` 会说 `expected statement` —— **那才是判据**。
            程序写了 CDS 注册，引擎就认；没写，引擎就报错 ⇒
            这正是"**依赖程序时成本最低**"：那一行代码是**唯一**的可靠信号。
        """
        from storygraph.story.project_conventions import unknown_statements
        from storygraph.story.statement_registry import (clear_cache,
                                                         discover_undeclared)
        root = _friendly_project(tmp_path, cds=False)      # 程序少写了那一行
        clear_cache()
        # 扫描器**保持沉默**（不许把台词当写法 —— 那比漏报更糟）
        assert 'mycue' not in unknown_statements(root), \
            '同形的东西不许靠猜：台词会被误报成写法'
        assert 'mycue' not in [r['head'] for r in discover_undeclared(root)]
        # ⚠ 于是"这一行到底行不行"只能问引擎（`lint`）——
        #   工具不许假装自己知道（规则 N1：扫描永不宣称"只有这些"）。

    def test_the_scanner_and_the_report_agree(self, tmp_path):
        """
        ⭐ 两个出口**必须一致**（地狱项目里它们曾经互相矛盾：
        报告 0 种、发现栏却列出人物 `lyj`）—— 背地里两套判据是 bug 之源。
        """
        from storygraph.story.project_conventions import unknown_statements
        from storygraph.story.statement_registry import (clear_cache,
                                                         discover_undeclared)
        for cds in (True, False):
            root = _friendly_project(tmp_path / f'c{int(cds)}', cds=cds)
            clear_cache()
            report = 'mycue' in unknown_statements(root)
            found = 'mycue' in [r['head'] for r in discover_undeclared(root)]
            assert report == found, (cds, report, found)

    def test_removing_the_cds_line_makes_it_unknown_again(self, tmp_path):
        """
        ⑤ 声明是**跟着 CDS 代码走**的：把那一行删掉，工具立刻改口 ——
        不存在"上次派生过就一直记住"的脏缓存。
        """
        from storygraph.story.statement_registry import (StatementRegistry,
                                                         clear_cache)
        root = _friendly_project(tmp_path)
        clear_cache()
        assert 'mycue' in StatementRegistry.for_project(root).names()
        # 程序把那一行删了
        (root / 'game' / 'zz_cds.rpy').write_text('', encoding='utf-8')
        clear_cache()
        assert 'mycue' not in StatementRegistry.for_project(root).names(), \
            'CDS 删了，工具还记着 —— 声明没跟着代码走'


#: 程序侧的钩子（**第二轮**：用户原则里的"程序写一个验证函数，工具自动调用"）
HOST_HOOK = '''"""程序提供的钩子（项目自己写；工具会找到并调用）。"""


def declarations():
    return [{'name': 'weather', 'label': '天气'}]


def validate_statement(head, text):
    if head == 'mycue':
        return ('闪电' in text), 'mycue 只认「闪电」'
    if head == 'weather':
        return True, ''
    return None          # 别的我不管
'''


class TestHostHooks:
    """程序的验证函数：**工具自动调用**（零配置，放对文件名就行）。"""

    def _project(self, tmp_path, hook=HOST_HOOK):
        root = _friendly_project(tmp_path, cds=False)
        if hook is not None:
            (root / 'storygraph.host.py').write_text(hook, encoding='utf-8')
        return root

    def test_no_hook_means_nothing_happens(self, tmp_path):
        """
        ⚠ 第一条自我约束：**程序不给就不发生** —— 没有钩子时工具行为
        与以前**一模一样**（不许凭空多一分依赖）。
        """
        from storygraph.story.host_hooks import load_host_hooks
        hooks = load_host_hooks(self._project(tmp_path, hook=None))
        assert hooks.available is False
        assert hooks.declarations() == []
        assert hooks.validate('mycue', 'mycue "闪电"') == (None, '')

    def test_declarations_from_the_program_are_used(self, tmp_path):
        """① 程序 `declarations()` 说的名字 → 工具当成"认识"（不用写 rules.json）。"""
        from storygraph.story.project_conventions import unknown_statements
        from storygraph.story.statement_registry import clear_cache
        root = self._project(tmp_path)
        (root / 'game' / 'ch02.rpy').write_text(
            'label ch02:\n    weather rain 强度=3\n    return\n', encoding='utf-8')
        clear_cache()
        assert 'weather' not in unknown_statements(root), \
            '程序已经用 declarations() 声明了 weather，工具还报"不认识"'

    def test_validate_true_takes_the_head_off_the_report(self, tmp_path):
        """
        ② 程序的 `validate_statement()` 说 True → 这一行程序认了 →
        不算"工具不认识"（**这就是"程序写一个验证函数"那一条**）。
        """
        from storygraph.story.project_conventions import unknown_statements
        from storygraph.story.statement_registry import clear_cache
        root = self._project(tmp_path)
        clear_cache()
        got = unknown_statements(root)
        assert 'mycue' not in got, f'程序验证通过了，工具还报：{sorted(got)}'

    def test_validate_false_still_reports_the_head(self, tmp_path):
        """
        ③ 程序说 **False**（"我认它，但这一行不对"）→ 工具**照样报出来**
        （它有话要说，只是理由来自程序）。
        """
        from storygraph.story.project_conventions import unknown_statements
        from storygraph.story.statement_registry import clear_cache
        root = self._project(tmp_path)
        (root / 'game' / 'ch01.rpy').write_text(
            'label ch01:\n    mycue "打雷"\n    return\n', encoding='utf-8')
        clear_cache()
        assert 'mycue' in unknown_statements(root), \
            '程序说这一行不对，工具却沉默了'

    def test_a_broken_hook_never_breaks_the_tool(self, tmp_path):
        """
        ⚠ 第二条自我约束：**程序写的代码不许弄坏工具** ——
        钩子语法错 / 抛异常 / 返回垃圾，一律只记下来，工具照常干活。
        """
        from storygraph.story.host_hooks import load_host_hooks
        for bad in ('def validate_statement(:\n    ???',
                    'raise RuntimeError("炸")',
                    'def declarations():\n    return "不是列表"\n\n'
                    'def validate_statement(head, text):\n'
                    '    raise ValueError("炸")\n'):
            root = _friendly_project(tmp_path / f'b{abs(hash(bad)) % 999}',
                                     cds=False)
            (root / 'storygraph.host.py').write_text(bad, encoding='utf-8')
            hooks = load_host_hooks(root)
            # 关键：**不抛异常**，而且失败有记录（报告里要看得见）
            assert hooks.declarations() == []
            assert hooks.validate('mycue', 'x')[0] is None
            assert hooks.notes, f'钩子坏了却什么都没记：{bad[:30]!r}'

    def test_validate_none_is_not_a_yes(self, tmp_path):
        """④ `None`（程序不管）**绝不等于**"程序说没问题"—— 否则就是假绿。"""
        from storygraph.story.host_hooks import load_host_hooks
        hooks = load_host_hooks(self._project(tmp_path))
        ok, why = hooks.validate('something_else', 'something_else x=1')
        assert ok is None, '程序不管的东西被当成了"程序说通过"'
        assert why == ''
