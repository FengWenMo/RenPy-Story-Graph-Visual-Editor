"""
**地狱项目的常驻攻击**（用户 2026-10-11："2、把地狱项目接成常驻攻击"）
=========================================================================

`tools/make_hell_project.py` 那份地狱项目**借了真实项目的规模**（105 个 `.rpy`），
所以它只能在开发机上手工跑（`tools/attack_hell_project.py`）。
这里把它**能在 CI 里跑的那一半固化下来**：用**同一个 `HOT_FILES` 表**
（从生成器里 import，不复制一份）造一个**合成迷你地狱**，然后断言
"这几条必须扛住"。

## 为什么值得常驻

第一轮攻击的三条 ❌ 里，**两条是攻击脚本自己的错**（少拷人物表 → 误报把人物当写法；
`cli.py` 调用方式错 → 误判自检失败），**只有一条是真缺陷**
（中文参数名被静默漏报）。这正说明：**探照灯要固定下来，人要看清"谁错了"。**

## 断言的是"性质"，不是"快照"

· 自定义写法**必须**被报出来（漏报比误报危险）；
· 内置关键字 / ATL 块冒号**必须不**被报（曾经的 31 假阳性）；
· 人物说的台词**必须不**被当成"未知写法"（要有角色表才判得准）；
· 有 parse error 时**必须**如实说不可用、且指出是哪个文件（不许假绿）。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _hot_files() -> dict:
    """
    从**生成器**里取那份 `HOT_FILES`（唯一真源，不复制）——
    免得"地狱文件改了而判据没跟上"。
    """
    path = ROOT / 'tools' / 'make_hell_project.py'
    spec = importlib.util.spec_from_file_location('_make_hell', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return dict(mod.HOT_FILES)


def _build_mini_hell(root: Path) -> Path:
    """造**合成迷你地狱**：只有那 13 个攻击文件（不借真项目规模）。"""
    (root / 'game' / 'images').mkdir(parents=True, exist_ok=True)
    for rel, (body, _why) in _hot_files().items():
        fp = root / rel
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(body, encoding='utf-8')
    return root


def _write_character_table(root: Path) -> None:
    """
    （**暂时没被用到** —— 保留说明为什么不猜）

    工具读人物表走 `load_asset_registry`（`game/asset_tables/*.xlsx`）。
    我第一版在这里**猜**了文件名（`角色表.xlsx`）与列名，结果读不到 →
    判据自己失败。**判据不许猜格式**：要么复用工具自己的常量，
    要么直接测契约（见 `test_dialogue_…`）。真机上的读取已由
    `tools/attack_hell_project.py` 验过（真实项目 28 个 id）。
    """
    return None


class TestHellProjectAttack:
    """合成迷你地狱上的常驻攻击。"""

    def _project(self, tmp_path) -> Path:
        root = tmp_path / 'hell_mini'
        _build_mini_hell(root)
        return root

    def test_unknown_statements_reports_the_custom_head(self, tmp_path):
        """
        ⭐ 第一轮真缺陷的回归线：**中文具名参数**的写法不许被静默漏报
        （`weather rain 强度=3` 原来会让整行被判成"不是语句"）。
        """
        from storygraph.story.project_conventions import unknown_statements
        got = unknown_statements(self._project(tmp_path))
        assert 'weather' in got, f'自定义写法被漏报了：{sorted(got)}'
        assert got['weather']['count'] >= 1

    def test_builtin_heads_and_atl_colon_are_not_reported(self, tmp_path):
        """
        内置关键字（with / window / at / pause / play …）与 **ATL 块冒号**
        （`show x heihei:` 的 `heihei:`）一个都不许被报成"不认识" ——
        后者曾是真实项目里的 **31 个假阳性**。
        """
        from storygraph.story.project_conventions import unknown_statements
        got = unknown_statements(self._project(tmp_path))
        for bad in ('with', 'window', 'at', 'pause', 'scene', 'show', 'hide',
                    'play', 'stop', 'nvl', 'return', 'jump', 'call', 'menu',
                    'if', 'else', 'elif', 'while', 'for', 'pass', 'label',
                    'heihei:', 'heihei'):
            assert bad not in got, f'把内置/ATL 当成了"不认识"：{bad}'

    def test_the_tool_does_not_reach_the_engine_but_says_so(self, tmp_path):
        """
        地狱项目里必然有 parse error（那正是"地狱"的意思）——
        采集**必须**如实说"不可用"，并且**指出是哪个文件**，绝不许假绿。
        """
        from storygraph.pipeline.engine_collect import collect, find_sdk
        # 没装 SDK 时 `collect` 会如实报"找不到 SDK"—— 那是**环境**结论，
        # 不是这个用例要验的"地狱项目结论"。没有 SDK 就跳过
        # （`find_sdk` 找不到是**抛**，不是返回 None）。
        try:
            find_sdk()
        except FileNotFoundError:
            pytest.skip('没有 Ren\'Py SDK —— 这条判据验的是"引擎怎么说"，跑不了')
        res = collect(self._project(tmp_path), timeout=600, mode='copy')
        assert res['ok'] is False, '有 parse error 却说可用 = 假绿'
        reason = str(res.get('reason') or '')
        assert reason, '说了不可用却没说为什么'
        # ⭐ 而且**必须说是哪儿**（地狱项目攻击抓到的）：只说"有 parse error"，
        #   人不知道该去修哪个文件。指第一处（引擎也只报第一处）。
        assert 'hell_' in reason, f'没指出具体是哪个地狱文件：{reason[:200]}'

    def test_dialogue_is_not_an_unknown_statement_when_roles_are_known(
            self, tmp_path):
        """
        ⭐ 第一轮"假缺陷"的教训固化成判据：**认得角色表时**，
        `lyj "台词"` 里的 `lyj`（人物）**不许**被当成"工具还不认识的写法" ——
        那一栏就是界面左边给编剧看的，报错了编剧会以为工具坏了。

        ⚠ 这里**直接测契约**（`_looks_like_dialogue` 收到角色集合时的判断），
        不去猜人物表 xlsx 的列名/表名 —— 猜错了是**判据自己**在制造假缺陷
        （上一轮我就因为"地狱项目少拷人物表"误报了一次）。
        真机上"人物表被正确读到"已由 `tools/attack_hell_project.py` 验过
        （真实项目 28 个 id，`lyj` 确实被排除）。
        """
        from storygraph.story.statement_registry import _looks_like_dialogue
        ids, names = {'lyj', 'lan'}, {'叶怀瑾', '蓝'}
        why = _looks_like_dialogue('lyj', 'lyj "这是一个人物在说话"', (ids, names))
        assert why, '人物+引号台词 被判成了动作'
        assert '台词' in why
        # 没有角色表时**不许瞎猜**（名字够长就什么都不说，不能断定）
        assert not _looks_like_dialogue('lyj', 'lyj "台词"', (set(), set()))
        # 极短名字 + 有引号 → 只能"存疑"，不能说确定
        assert _looks_like_dialogue('a', 'a "台词"', (set(), set()))
