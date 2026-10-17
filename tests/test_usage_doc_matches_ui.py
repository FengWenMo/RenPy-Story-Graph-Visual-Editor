"""
入口说明文档 vs 真实界面文字（2026-10-11）
================================================

## 为什么有这条
本仓出过两类相反的事故，都要靠这条判据防：

  ① **功能做完、用户找不到**（2026-09-28：4 项卡片功能做完、测试也过，
     用户说"我不知道如何打开这个你说的卡牌入口"）；
  ② **文档说的字和界面上的字不一样** —— 用户照着文档找，找不到，
     就会以为"功能没做"（这正是 `tools/audit_stale_docs.py` 想治的病，
     只不过那份治的是"代码里有没有"，这份治的是"**字对不对**"）。

所以：`docs/使用_AI助手与自定义模块_2026-10-11.md` 里凡是**引号引起来让用户去点的
界面文字**，都必须**真的在代码里存在**。文档说"点 `X`"，代码里没有 `X` = 红。

⚠ 反过来不检查（界面上的字不必都写进文档）—— 那是文档取舍，不是错误。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DOC = ROOT / 'docs' / '使用_AI助手与自定义模块_2026-10-11.md'
UI_DIRS = [ROOT / 'storygraph' / 'ui_qt', ROOT / 'storygraph']

#: 文档里"让用户去点/去看"的界面文字（用「」标出）
_CLICKABLE = re.compile(r'「([^」]{2,40})」')

#: 判据要核对的那些「」——**按"有明确落点"挑**，不按"带 emoji"挑。
#:
#: ⚠ 为什么加了"以动作词开头"这一条：2026-10-13 之前只挑带 emoji 的，
#: 于是「打开上次的项目」「忘掉全部记忆…」「下次自动打开上次的项目」这些
#: **用户真的要去点的菜单项**全在判据之外 —— 文档把它们写错名字也不会红。
#: 发现这个缺口正是因为**新加了记忆菜单**（菜单项不带 emoji）。
#:
#: ⚠ 只认**开头**那个词，不看"句子里出现过动作词"：文档里还有一堆
#: 「」是**在解释一句话**（"「引擎没认（导出时它会报错）」"、"「看着像谁在
#: 说话、但我不认识这个名字」"）——那些是对话 / 提示的**内容**，
#: 拿它们去核代码里的字只会得到假红（实测一次报了 5 条假红）。
_UI_VERBS = ('打开', '导入', '清掉', '取消', '勾选', '填', '点', '看',
             '导出', '忘掉', '切换', '分析', '检查', '体检', '预览', '下次',
             '验证', '试切', '保存', '撤销', '删除', '重读', '刷新')

#: 不是动作词开头、但确实是界面标签的那几个（显式列出，不猜）。
_UI_LABELS = ('候选分栏', '记忆', '最近打开的项目')

#: **假装成动作词的散文**（文档里用「」引用的一句话，不是界面文字）。
#:
#: ⚠ 为什么必须显式排：入口说明里有一句卡片文案是
#: 「看着像谁在说话、但我不认识这个名字」—— 它以"看"开头，被上面那条
#: "以动作词开头"的规则误收进来，于是一条**永远不可能成立**的判据
#: 混在里面（代码里当然没有这句话）。这是"规则放宽"的必然代价，
#: 用一份**短且带理由**的排除表兜住，比把规则写得又紧又难懂更好。
_PROSE = ('看着像谁在说话、但我不认识这个名字',)


def _ui_text() -> str:
    """所有 UI 源码拼起来（用户能看到的字都在这儿）。"""
    parts = []
    for d in UI_DIRS[:1]:
        for fp in d.rglob('*.py'):
            parts.append(fp.read_text(encoding='utf-8', errors='replace'))
    return '\n'.join(parts)


def documented_entry_texts() -> list:
    """
    文档里「」包起来的、看起来是**界面文字**的那些。

    挑法（三条取"或"）：
      · 含 emoji（`🤖🔑🧩➕💬▶⤓🎬❓`）—— 工具栏按钮都长这样；
      · **以界面动作词开头**（`_UI_VERBS`）—— 菜单项通常不带 emoji
        （「打开上次的项目」这种），只按 emoji 挑会漏掉它们（实测漏过）；
      · 或在那份显式标签表里（`_UI_LABELS`，不用动作词开头的少数几个）。
    """
    text = DOC.read_text(encoding='utf-8')
    out = []
    for hit in _CLICKABLE.findall(text):
        if hit in _PROSE:
            continue
        if any(ch in hit for ch in '🤖🔑🧩➕💬▶⤓🎬❓'):
            out.append(hit)
        elif hit in _UI_LABELS or hit.startswith(_UI_VERBS):
            out.append(hit)
    return sorted(set(out))


def test_the_usage_doc_exists():
    """这份说明本身要在（目标的交付物之一就是"可直接试用的入口说明"）。"""
    assert DOC.is_file(), f'缺入口说明文档：{DOC.name}'


def test_every_documented_button_really_exists_in_the_ui():
    """
    ⭐ 核心判据：文档让用户点的按钮，代码里必须有**同样的字**。
    否则用户照着找 → 找不到 → 以为功能没做（本仓的历史事故）。
    """
    ui = _ui_text()
    missing = [t for t in documented_entry_texts() if t not in ui]
    assert not missing, (
        '文档让用户点这些，但界面代码里找不到同样的字：\n  '
        + '\n  '.join(missing)
        + '\n（要么改文档、要么改界面 —— 但两者必须一致）')


def test_the_doc_covers_the_three_main_entries():
    """三件事都要在说明里：AI 助手 / 自定义模块 / 纯文字导入。"""
    text = DOC.read_text(encoding='utf-8')
    for topic in ('🤖 AI 助手', '🧩 自定义模块 / 语句', '导入章节'):
        assert topic in text, f'说明里没写「{topic}」'


def test_the_doc_says_where_the_key_is_stored_and_that_it_is_local():
    """key 的存放位置要写明（用户最关心的两件事：存哪、会不会进 git）。"""
    text = DOC.read_text(encoding='utf-8')
    assert 'ai.json' in text and 'LOCALAPPDATA' in text, '没写 key 存哪'
    assert '不进项目' in text or '不进 git' in text, '没说 key 不会进项目'


def test_the_doc_is_honest_about_the_unimplemented_tool():
    """
    ⚠ 诚实性判据：`storygraph_table_export` 是**故意没做**的占位，
    说明里必须写明"还没做"，不许让用户以为能用。
    """
    text = DOC.read_text(encoding='utf-8')
    assert 'table_export' in text, '说明里没提这个工具'
    assert '还没做' in text or '占位' in text, \
        'table_export 是占位却没写明 —— 用户会以为能用'
