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


def _ui_text() -> str:
    """所有 UI 源码拼起来（用户能看到的字都在这儿）。"""
    parts = []
    for d in UI_DIRS[:1]:
        for fp in d.rglob('*.py'):
            parts.append(fp.read_text(encoding='utf-8', errors='replace'))
    return '\n'.join(parts)


def documented_entry_texts() -> list:
    """文档里「」包起来的、看起来是**界面文字**的那些。"""
    text = DOC.read_text(encoding='utf-8')
    out = []
    for hit in _CLICKABLE.findall(text):
        # 只挑"按钮/标签"味儿的：含 emoji、或含 UI 动词、或不含代码符号
        if any(ch in hit for ch in '🤖🔑🧩➕💬▶⤓🎬❓'):
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
    for topic in ('🤖 AI 助手', '🧩 自定义模块', '导入章节'):
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
