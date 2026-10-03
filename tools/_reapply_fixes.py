#!/usr/bin/env python
"""
重放四处在 `storygraph/` 里的修复（2026-10-05）。

为什么要做成脚本：这几处改动曾在一次误还原里丢过一次，
而它们分散在 4 个文件、都是中文文件（`edit` 工具易踩
"file changed since it was read"）。做成**可重放**的脚本，
再丢一次也只是重跑一遍。

四处：
  1. `story/beat_render.py` —— `python:` 块内的**空行**不许丢（两处）
  2. `app/editor.py`       —— `Notes` 从 `ADVANCED_FIELDS` 里取消
  3. `app/session_export.py` —— `_transforms_decision` 补进 `REQUIRES`
  4. `story/beat_model.py` —— `to_row` 把 `notes` 并进注释列

全部是**幂等**的：已经改过就跳过。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / 'storygraph'

EDITS = []


def edit(rel: str, old: str, new: str, why: str):
    EDITS.append((rel, old, new, why))


# ---------------------------------------------------------------- 1. 空行
edit(
    'story/beat_render.py',
    """                if not nxt.strip():
                    k = j
                    while k < n and not lines[k].strip():
                        k += 1
                    if k < n and lines[k][:1] == CODE_BLOCK_BODY:
                        j = k
                        continue
                break""",
    """                # **空行也是块体的一部分**（2026-10-05 修）。
                #
                # 实测来源：`mini_game_puzzle.rpy` 的多行调用里有一个空行
                # （`cols=3,` 与 `grid_width=800,` 之间）。块标记是**行首字符**，
                # 而空行没有行首字符 —— 旧循环到它这里就停了，于是：
                #   · 空行之后那几行**掉出块体**；
                #   · `python:` 块被从中间截断，尾巴按"块外行"逐行处理；
                #   · 那一整段在往返后逐行文本对不上（审计算一条丢失）。
                # 判据要"往前看"：空行**后面还有块体行**才算块内空行，
                # 否则它是块与下一段之间的分隔（那种要留在块外）。
                if not nxt.strip():
                    k = j
                    while k < n and not lines[k].strip():
                        k += 1
                    if k < n and lines[k][:1] == CODE_BLOCK_BODY:
                        j = k
                        continue
                break""",
    'python 块内空行不许丢（块体收集）')

edit(
    'story/beat_render.py',
    """        if body:
            body = [ln for ln in body if ln.strip() != '']""",
    """        # **只去掉首尾的空行，中间的空行留着**（2026-10-05 修）。
        #
        # 旧写法是 `body = [ln for ln in body if ln.strip() != '']` ——
        # 把**所有**空行都扔了。那对拍之间的空行无所谓（纯排版），
        # 但对 `python:` 块内的空行是**实打实的内容丢失**：
        # 实测 `mini_game_puzzle.rpy` 的多行调用里 `cols=3,` 与
        # `grid_width=800,` 之间有一个空行，它被扔掉之后往返的逐行文本
        # 就对不上（审计算一条丢失），而且作者手写的排版被改掉了。
        #
        # 为什么不能干脆全留：`render_beat_to_rpy` 的返回值里，
        # 拍与拍之交可能有空行，全留会在 label 里堆出很多空行
        # （每个拍之间一个），产物会很散。所以只 trim 首尾。
        while body and not body[0].strip():
            body.pop(0)
        while body and not body[-1].strip():
            body.pop()""",
    'python 块内空行不许丢（label 收尾）')

# ---------------------------------------------------------------- 2. Notes
edit(
    'app/editor.py',
    """#: 高级区字段（默认折叠）
ADVANCED_FIELDS = ['SFX', 'CodeBlock', 'Label', 'Kind', 'Notes', 'Stage']""",
    """#: 高级区字段（默认折叠）
#:
#: **不含 `Notes`**（2026-10-05 定：这个字段取消）。它以前在这里，
#: 于是前端照样建"备注"控件、照样把值回读进 `Beat.notes` ——
#: 而 `BEAT_FIELDS` 里**没有这一列**，`to_row` 不写它，
#: 所以编剧认真写的备注**导出一次就蒸发，且不报错**。
#:
#: 二选一里选了"取消"而不是"补一列"，理由是它与 `Comment` 完全重叠：
#: `Comment`（表头「注释」）在**主区**、是 `BEAT_FIELDS` 里真有的列、
#: 导出会写。留两个语义相同的输入框，只会让人猜该填哪个。
#:
#: 老表里的 `Notes` 列**仍然读得回来**（`Beat.from_row` 把它并进
#: `Comment`，绝不丢字）—— 这条兼容链路没动，见
#: `tests/test_editor_session_mixin_contract.py` 的 `TestFieldContract`。
ADVANCED_FIELDS = ['SFX', 'CodeBlock', 'Label', 'Kind', 'Stage']""",
    'Notes 从高级区取消（它与主区 Comment 重叠且注定蒸发）')

# ------------------------------------------------------- 3. REQUIRES 声明
edit(
    'app/session_export.py',
    """    它**要求宿主提供** `registry()` / `refresh_symbols()` / `compile()` /
    `symbols` / `project_root` / `script_path` / `beats` —— 见 `REQUIRES`。
    \"\"\"

    #: 本 mixin 要求宿主（或其它 mixin）提供的成员（**实测**，不是凭印象）。
    REQUIRES = ('registry', 'compile', 'symbols', 'project_root',
                'script_path', 'beats')""",
    """    它**要求宿主提供** `registry()` / `compile()` / `symbols` /
    `project_root` / `script_path` / `beats`，以及跨块共享状态
    `_transforms_decision` —— 见 `REQUIRES`。
    \"\"\"

    #: 本 mixin 要求宿主（或其它 mixin）提供的成员（**实测**，不是凭印象）。
    #:
    #: `_transforms_decision` 是**跨块共享状态**（2026-10-05 补）：
    #: `AssetRegistryMixin.register_missing_assets` 在资源表变了之后把它
    #: 清成 `None`（写），本 mixin 的 `should_write_transforms` 读它当缓存。
    #:
    #: 漏声明的后果**不是报错**，而是"属性是否存在取决于之前发生过什么"：
    #: `getattr(self, '_transforms_decision', None)` 在宿主没初始化时
    #: 静默拿 `None` → 重新判定一遍 → 症状是"偶尔多写一段 transform 定义"，
    #: 没人会把这和拆分联系起来。所以它必须两头都说清：
    #: 读它的人（本 mixin）声明、写它的人（资产块）声明、
    #: 宿主 `EditorSession.__init__` 初始化。
    #: 守卫：`tests/test_editor_session_mixin_contract.py`。
    REQUIRES = ('registry', 'compile', 'symbols', 'project_root',
                'script_path', 'beats', '_transforms_decision')""",
    'ExportMixin 补声明 _transforms_decision')

# ------------------------------------------------------- 4. notes 并进注释
edit(
    'story/beat_model.py',
    """        bgm = self.bgm or _bgm_cell
        sfx = self.sfx or _sfx_cell
        return [
            self.line_id, self.seq, self.label, self.kind,
            self.speaker, self.comment, menu_caption_text(self.text),
            self.condition,""",
    """        bgm = self.bgm or _bgm_cell
        sfx = self.sfx or _sfx_cell
        # `notes` 已经取消（UI 不再暴露，见 `ADVANCED_FIELDS` 的说明），
        # 但**不能因此静默丢掉别人设过的值**（2026-10-05 修，
        # `test_editor_session_mixin_contract.TestFieldContract` 抓到）：
        # `Beat(notes='…')` 直接构造出来的拍（脚本、单测、老的协作数据）
        # 走 `to_row` 时那个值会**蒸发且不报错**。
        #
        # 修法与 `from_row` 对称：读的时候把 `Notes` 并进 `comment`，
        # 写的时候也把 `self.notes` 并进注释列。这样"设过 notes 的拍"
        # 往返一次之后内容还在（落在「注释」列）。
        comment = self.comment
        if self.notes:
            comment = (f'{comment}\\n{self.notes}'.strip()
                       if comment else self.notes)
        return [
            self.line_id, self.seq, self.label, self.kind,
            self.speaker, comment, menu_caption_text(self.text),
            self.condition,""",
    'to_row 把 notes 并进注释列（对称于 from_row）')


def main() -> int:
    done = skipped = failed = 0
    for rel, old, new, why in EDITS:
        p = PKG / rel
        t = p.read_text(encoding='utf-8')
        if new in t:
            print(f'  · 已是目标状态，跳过  {rel}  ({why})')
            skipped += 1
            continue
        if old not in t:
            print(f'** 找不到锚点  {rel}  ({why})')
            failed += 1
            continue
        p.write_text(t.replace(old, new, 1), encoding='utf-8')
        print(f'  ✓ {rel}  ({why})')
        done += 1
    print(f'\n改了 {done}，跳过 {skipped}，失败 {failed}')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
