"""
P3-15 守卫：`content_hash` 的**宽度契约**
========================================

风险清单 P3-15：`content_hash` 只有 **32 bit**（`md5(...)[:8]`）。
生日碰撞门槛 `2**16 = 65536` 个不同内容 —— 真实项目（sr_general）
已经 3800 条（约 0.17%），一个 30 章的中大型项目就到 1% 量级。

撞了的后果**不是丢内容，是"身份"错**（所以它比"偶尔算错"更阴）：

  · `core/uid.make_translation_uid` 把 `{label}_{hash}` 当翻译单元**身份**，
    `_unique_id` 用 `_1`/`_2` 兜冲突 —— 两条**不同**台词撞上同一 hash 时，
    第二条被改名，**已有译文就对不上号了**（不是丢了，是错位）；
  · `collab_store` 拿它当"内容变没变"的判据
    （`row['content_hash'] == chash`）—— 撞了就当没变，**改动静默不同步**。

这一版把它加到 **16 位 hex（64 bit）**，门槛抬到 `2**32 ≈ 43 亿`。

本文件守三件事
--------------
1. **位数够宽**（下限 16）—— 防止有人手滑改回 8；
2. **只有一个真源** —— `Statement` 与 `TranslationUnitGenerator` 不许
   各写一个数字。这是**真实存在过的**问题：改这一版时发现
   `renpy_parser.py::_unique_id` 里写死着 `[:8]`，而 `statement.py` 里
   也写死着 `[:8]`；把一边改成 16 之后，两者不再"逐字节一致"，
   **而所有测试都是绿的**（各自内部都自洽）。所以这条判据现在是：
   "两处算出来的 uid 必须相同"，而不是"两处都引用了同一个常量"
   （后者靠 import 就能满足，前者才是契约）。
3. **老数据不会静默丢失** —— 既有协作库里存的是 8 位 hash，
   拿来和新的 16 位比**必然不等** → `collab_store` 认为"变了" → 更新。
   方向是**安全**的（宁可多传一次，不可当成没变）。
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1] / 'storygraph'

#: 位数下限。改小就要改这条，并说明为什么。
MIN_HEX = 16


class TestWidthIsWideEnough:

    def test_constant_is_wide_enough(self):
        from storygraph.core.statement import CONTENT_HASH_HEX
        assert CONTENT_HASH_HEX >= MIN_HEX, (
            f'content_hash 只有 {CONTENT_HASH_HEX} 位 hex '
            f'（{CONTENT_HASH_HEX * 4} bit）—— 生日碰撞门槛只有 '
            f'2**{CONTENT_HASH_HEX * 2} = {2 ** (CONTENT_HASH_HEX * 2)} 个内容。'
            f'见本文件开头：撞了会让翻译单元身份错位、协作同步漏改。')

    def test_real_hash_has_that_width(self):
        from storygraph.core.statement import CONTENT_HASH_HEX, Statement
        got = Statement.make_say('e', 'Hello').content_hash
        assert len(got) == CONTENT_HASH_HEX, got
        assert re.fullmatch(r'[0-9a-f]+', got), got

    def test_menu_and_extend_too(self):
        """参与哈希的 kind 都要跟着（漏一个就长度不一）。"""
        from storygraph.core.statement import CONTENT_HASH_HEX, Statement
        for s in (Statement.make_say('e', 'x'),
                  Statement.make_extend('x'),
                  Statement.make_menu('x'),
                  Statement.make_menu_option('x')):
            assert len(s.content_hash) == CONTENT_HASH_HEX, s


class TestOneSingleSourceOfTruth:

    def test_statement_and_tu_generator_agree(self):
        """
        **契约**：同一个 `(label, content)`，`Statement` 侧拼出来的 uid
        必须和 `TranslationUnitGenerator._unique_id` 拼出来的一样。

        为什么不断言"两处都引用 CONTENT_HASH_HEX"：那只证明 import 了常量，
        不证明**算法**一致（前缀怎么拼、用不用 `\\0` 分隔、要不要 strip
        都还没验）。这里直接比**产物**，那才是文档里说的"逐字节一致"。

        这条判据是拆出来的：改 P3-15 时发现 `_unique_id` 里写死 `[:8]`、
        `statement.py` 里也写死 `[:8]`，把前者改成 16 之后
        **全部测试仍然绿** —— 因为没有任何一条在比较两边的**产物**。
        """
        from storygraph.core.statement import CONTENT_HASH_HEX, Statement
        from storygraph.core.uid import make_translation_uid
        from storygraph.parsing.renpy_parser import TranslationUnitGenerator

        cases = [
            ('ch01', 'say\0e\0Hello'),
            ('ch01', 'say\0\0旁白一句话'),
            ('chapter_1_3', 'menu\0要不要进去？'),
            ('unnamed', 'extend\0再说一句'),
        ]
        gen = TranslationUnitGenerator()
        for label, content in cases:
            got = gen._unique_id(label, content)
            h = hashlib.md5(content.encode('utf-8')).hexdigest()
            expect = make_translation_uid(label, h[:CONTENT_HASH_HEX])
            assert got == expect, (
                f'两边拼出来的翻译单元 id 不一致：\n'
                f'  TranslationUnitGenerator: {got}\n'
                f'  core.uid（Statement 的算法）: {expect}\n'
                f'  内容 = {content!r}\n'
                f'（这就是"content_hash 与 TU 的 hash 部分必须逐字节一致"）')
        _ = Statement                          # 契约的另一半见下一条

    def test_statement_hash_equals_the_tu_hash_part(self):
        """`Statement.content_hash` 就是 uid 里 `_` 后面那一段。"""
        from storygraph.core.statement import Statement
        from storygraph.core.uid import make_translation_uid

        s = Statement.make_say('e', 'Hello')
        uid = make_translation_uid('ch01', s.content_hash)
        assert uid.rsplit('_', 1)[-1] == s.content_hash, uid


class TestNoHardcodedWidthInSource:
    """
    源码里**不许再出现写死的截断位数**。

    这条是"单一真源"的**静态**版本：动态那条（比产物）只能证明
    "当前这几个 case 一致"，静态这条能抓住"另一个文件里还有一处 `[:8]`"
    —— 将来那条路走不到，动态测试就发现不了。
    """

    #: 允许出现 `[:8]` 的地方（+ 为什么）。
    #:
    #: **不是** "content_hash" 的位宽，而是**碰巧也截了 8 位**的别的东西 ——
    #: 这种地方必须逐个说清，否则下一个人会以为"这条规则只是没查全"。
    ALLOWED: dict = {
        'parsing/renpy_parser.py': (
            '第 3071 行那一处是**翻译文件数据库的键**，不是 content_hash：\n'
            '    `key = f"{lang}.strings_{md5(原文)[:8]}"`\n'
            '输入是译文文件里 `old "…"` 的**原文**（只有那个字符串，\n'
            '没有 kind 前缀、没有 \\0 分隔），用途是"在**已有的** tl 文件里\n'
            '回查这一条的译文"。\n'
            '所以：\n'
            '  · 它**不参与** `content_hash ↔ TU hash 逐字节一致` 那条契约；\n'
            '  · 加宽它**只会弄坏东西** —— 旧 tl 文件里键长这样\n'
            '    (`chinese.strings_1a2b3c4d`)，改成 16 位就再也查不到，\n'
            '    用户的既有译文会对不上号（正是 P3-15 想避免的那种伤害）。\n'
            '要动它得先有一套 tl 键迁移方案，那是另一件事。'),
    }

    def test_no_hardcoded_8_char_hash_truncation(self):
        offenders = []
        for p in sorted(PKG.rglob('*.py')):
            rel = p.relative_to(PKG).as_posix()
            if rel in self.ALLOWED:
                continue
            for i, ln in enumerate(p.read_text(encoding='utf-8').splitlines(),
                                    1):
                # 注释行不算：本文件与 `statement.py` 的说明里
                # **故意**引用了旧写法（`原来是 md5(...).hexdigest()[:8]`），
                # 那是文档，不是代码。
                if ln.lstrip().startswith('#'):
                    continue
                if 'hexdigest()[:8]' in ln:
                    offenders.append(f'{rel}:{i}  {ln.strip()}')
        assert not offenders, (
            '这些地方还在写死 `hexdigest()[:8]` —— 位数必须走 '
            '`core.statement.CONTENT_HASH_HEX`：\n'
            + '\n'.join('  ' + o for o in offenders)
            + '\n\n（两处各写一个数字，改一边就会漂 —— 见本文件开头。）')

    def test_the_allowlist_entry_still_exists(self):
        """
        豁免必须**仍然对应真实存在的那一处** —— 否则清单会变成
        "历史遗留的免死金牌"，而真正的违规藏在后面。
        """
        hits = 0
        for rel in self.ALLOWED:
            src = (PKG / rel).read_text(encoding='utf-8')
            hits += sum(1 for ln in src.splitlines()
                        if 'hexdigest()[:8]' in ln
                        and not ln.lstrip().startswith('#'))
        assert hits == 1, (
            f'`ALLOWED` 里那个豁免说明对应 {hits} 处 `[:8]`（应该恰好 1）—— '
            f'要么它被改掉了（那就可以删掉这条豁免），要么又多了一处'
            f'（那要单独说明）。')

    def test_uid_docstring_does_not_promise_8(self):
        """`core/uid.py` 的说明要跟着位数走（文档漂了比代码漂更难查）。"""
        src = (PKG / 'core' / 'uid.py').read_text(encoding='utf-8')
        assert '8 位 hex' not in src, (
            '`core/uid.py` 的 docstring 还写着"8 位 hex" —— 位数已经改了')


class TestOldDataIsNotSilentlyLost:

    def test_old_short_hash_is_treated_as_changed(self):
        """
        旧记录（8 位）与新算法（16 位）比较**必然不等** →
        消费方应当认为"变了"并更新。方向安全：宁可多同步一次。

        这条其实是在钉 `collab_store` 的**比较方向**（`!=` 就要更新），
        而不是钉一个具体实现 —— 用最朴素的复现来说明：
        """
        from storygraph.core.statement import Statement
        new = Statement.make_say('e', 'Hello').content_hash
        old = new[:8]
        assert old != new, (
            '8 位前缀居然等于 16 位全串（说明位数没真的加宽）')
        # 消费方的规则是"不等就更新" —— 所以旧记录会被刷新，不会静默当没变
        assert (old == new) is False
