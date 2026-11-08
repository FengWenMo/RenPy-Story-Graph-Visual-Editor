# -*- coding: utf-8 -*-
"""
导出三路合并的**地狱测试**（2026-11-08 第十五轮）
================================================

`tests/test_export_three_way.py` 那 16 条证明的是"正常配合下它做对了"。
这一轮问的是另一类问题：**在脏数据上、在第二次、在看不见的字符上、
在他把文件改坏之后，它会不会把用户的文件弄坏**。

判据全是**性质**，不是功能：幂等、逐字节、边界、失败时不脏、规模、对称性。

写这个文件时抓到三件事（都修在这一轮里）：

  1. **`write_gate` 用 `str.splitlines()` 切行** —— 一行中间的 U+2028
     （从 Word 粘台词常带）会被**撕成两行**，`"你好\\u2028世界"` 变成
     `"你好` + `世界"`，台词没结上引号，整章读不成。而且它**静默**：
     合并报成功、文件写出去、只有打开游戏才会发现。`patcher` 早就用
     `core.textlines.split_lines`（只认 `\\n`），`write_gate` 是漏的那一处。
  2. `generation_guard.top_level_chunks` 同一个坑的**另一种后果**：那半截
     `世界"` 看着像一条"顶层语句"，于是 `OPAQUE_LOST` **假拒写**
     （补丁明明对了，导出却被拦）。
  3. **紧挨着 ≠ 同一处**：他在你改的那一行旁边加一行时，消息说"改的正是
     同一处" —— 用户会去找"他什么时候改了我这句"，找不到。现在两种措辞分开
     （都拒写，但一种说"紧挨着"）。
"""

from __future__ import annotations

import ast
import os
import random
import sys
import tempfile
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

BASE = ('default score = 0\n'
        '\n'
        'label a:\n'
        '    e "第一句"\n'
        '    e "第二句"\n'
        '    e "第三句"\n')
MINE = '我改过的第二句'
HIS = '换成别的说法甲'

#: "看起来是换行、其实不是"的三个字符：软回车 / 换页 / NEL
SPECIALS = {'u2028': '\u2028', 'formfeed': '\x0c', 'nel': '\x85'}


def _write(path: Path, text: str) -> None:
    """**逐字节**写（`newline=''` 不翻译换行）—— 比字节的判据只能这么写。"""
    path.write_text(text, encoding='utf-8', newline='')


def _session(tmp_path, text=BASE):
    from storygraph.app.editor import EditorSession

    src = tmp_path / 'ch.rpy'
    _write(src, text)
    s = EditorSession(project_root=tmp_path)
    s.load(src)
    return s, src


def _edit(s, old, new=MINE):
    for b in s.beats:
        if str(getattr(b, 'text', '') or '') == old:
            b.text = new
            return
    raise AssertionError(f'没有哪一拍写着「{old}」')


def _versions_of(src: Path) -> list:
    """`.storygraph/versions` 下这一章留过的旧版本（按修改时间排）。"""
    root = src.parent.parent / '.storygraph' / 'versions'
    if not root.exists():
        return []
    return sorted((p for p in root.rglob('*.rpy') if p.is_file()),
                  key=lambda p: p.stat().st_mtime_ns)


# ---------------------------------------------------------------------------
#  一、幂等：第二刀不许再动
# ---------------------------------------------------------------------------

class TestIdempotent:
    def test_second_export_touches_nothing(self, tmp_path):
        """
        合并写出去之后再导一次：`ok`、`untouched`、**字节相同**、
        而且**不再多留一代旧版本**（"一样就不碰"是第十轮立的规矩）。
        """
        s, src = _session(tmp_path)
        _edit(s, '第二句')
        _write(src, BASE.replace('第一句', HIS))

        first = s.export(src)
        assert first['ok'] and first['used_patch'], first
        raw1 = src.read_bytes()
        versions1 = len(_versions_of(src))

        second = s.export(src)
        assert second['ok'], second
        assert second.get('untouched') is True, second
        assert src.read_bytes() == raw1, '第二次导出把文件改了'
        assert len(_versions_of(src)) == versions1, '第二次导出白留了一代旧版本'

    def test_third_time_with_a_new_external_edit_still_merges(self, tmp_path):
        """合过一次之后，他再改一笔，还能再合 —— 不许"一次之后就不灵了"。"""
        s, src = _session(tmp_path)
        _edit(s, '第二句')
        _write(src, BASE.replace('第一句', HIS))
        assert s.export(src)['ok']

        body = src.read_text(encoding='utf-8')
        _write(src, body.replace('第三句', '他后来又改的第三句'))
        res = s.export(src)
        assert res['ok'], res
        after = src.read_text(encoding='utf-8')
        assert '他后来又改的第三句' in after
        assert HIS in after and MINE in after


# ---------------------------------------------------------------------------
#  二、逐字节：拒绝时不动；合并时没碰过的字节不动
# ---------------------------------------------------------------------------

class TestByteForByte:
    def test_refused_export_leaves_the_file_byte_identical(self, tmp_path):
        """撞在同一行 → 拒写；**逐字节**不变（不是"内容看起来一样"）。"""
        s, src = _session(tmp_path)
        _edit(s, '第二句')
        theirs = BASE.replace('第二句', '他也改了这一句')
        _write(src, theirs)
        res = s.export(src)
        assert res['ok'] is False, res
        assert src.read_bytes() == theirs.encode('utf-8')

    def test_crlf_survives_a_merge(self, tmp_path):
        """CRLF 文件合并之后，每一行都还是 CRLF（`\\r\\n` 数 == `\\n` 数）。"""
        crlf = BASE.replace('\n', '\r\n')
        src = tmp_path / 'ch.rpy'
        _write(src, crlf)
        from storygraph.app.editor import EditorSession
        s = EditorSession(project_root=tmp_path)
        s.load(src)
        _edit(s, '第二句')
        _write(src, '# 外部加的一行\r\n' + crlf)

        res = s.export(src)
        assert res['ok'], res
        raw = src.read_bytes()
        assert raw.count(b'\r\n') == raw.count(b'\n'), '有行被改成了 LF'
        assert b'\n' in raw

    def test_bom_is_kept_exactly_once(self, tmp_path):
        src = tmp_path / 'ch.rpy'
        src.write_bytes(b'\xef\xbb\xbf' + BASE.encode('utf-8'))
        from storygraph.app.editor import EditorSession
        s = EditorSession(project_root=tmp_path)
        s.load(src)
        _edit(s, '第二句')
        src.write_bytes(b'\xef\xbb\xbf' + BASE.replace('第一句', HIS)
                        .encode('utf-8'))

        res = s.export(src)
        assert res['ok'], res
        raw = src.read_bytes()
        assert raw.startswith(b'\xef\xbb\xbf')
        assert raw.count(b'\xef\xbb\xbf') == 1, 'BOM 多了一个（或跑到了行首之外）'

    def test_missing_trailing_newline_is_not_added(self, tmp_path):
        """文件末尾本来没有换行 —— 合并之后也不许凭空多一个。"""
        text = BASE.rstrip('\n')
        s, src = _session(tmp_path, text)
        _edit(s, '第二句')
        _write(src, text.replace('第一句', HIS))

        res = s.export(src)
        assert res['ok'], res
        raw = src.read_bytes()
        assert not raw.endswith(b'\n'), '末尾凭空多了换行'

    def test_untouched_lines_keep_their_own_line_endings(self, tmp_path):
        """
        混合换行（前几行 CRLF、后几行 LF）：**没碰过的行一个字节都不许变**。

        这是 `source_style._restore_line_endings` 的承诺；合并这条路也得守
        ——它写出去的是一份**新拼的**文本，最容易把整份归一化。
        """
        mixed = ('default score = 0\r\n'
                 '\r\n'
                 'label a:\r\n'
                 '    e "第一句"\r\n'
                 '    e "第二句"\n'
                 '    e "第三句"\n')
        src = tmp_path / 'ch.rpy'
        _write(src, mixed)
        from storygraph.app.editor import EditorSession
        s = EditorSession(project_root=tmp_path)
        s.load(src)
        _edit(s, '第二句')
        _write(src, mixed.replace('第一句', HIS))

        res = s.export(src)
        assert res['ok'], res
        after = src.read_bytes().decode('utf-8')
        # 前 4 行（没碰过）还是 CRLF
        head = after.split('\n')[:5]
        assert head[0].endswith('\r'), f'第一行丢了 CRLF：{head[0]!r}'
        assert head[3].endswith('\r'), f'第 4 行丢了 CRLF：{head[3]!r}'
        assert HIS in after and MINE in after


# ---------------------------------------------------------------------------
#  三、看不见的字符：一行不许变成两行
# ---------------------------------------------------------------------------

class TestInvisibleSeparators:
    @pytest.mark.parametrize('name', sorted(SPECIALS))
    def test_a_line_with_an_invisible_separator_stays_one_line(
            self, tmp_path, name):
        """
        行内有 U+2028 / 换页 / NEL 时，合并**不许把它换成真换行**。

        改之前在 `write_gate.merge_text`：`splitlines()` 把它切开、
        `'\\n'.join()` 拼回去 —— 那一行被撕成两行，台词没了结尾引号，
        而回执是 `ok=True`（静默写坏）。
        """
        mark = SPECIALS[name]
        text = ('default score = 0\n'
                '\n'
                'label a:\n'
                f'    e "你好{mark}世界"\n'
                '    e "第二句"\n')
        s, src = _session(tmp_path, text)
        _edit(s, '第二句')
        _write(src, text.replace('default score = 0', 'default score = 1'))

        res = s.export(src)
        assert res['ok'], res
        after = src.read_text(encoding='utf-8')
        assert f'    e "你好{mark}世界"\n' in after, (
            f'{name} 那一行被改了（撕成两行 / 换成真换行）：{after!r}')
        # 没有被撕开的残片（"世界\"" 单独成行）
        assert not any(ln.strip() == '世界"' for ln in after.split('\n')), \
            '那一行被撕成了两行'
        assert MINE in after

    def test_merge_text_keeps_the_separator(self):
        """`merge_text` 这一层单独守一遍（不经过导出，坏起来更快定位）。"""
        from storygraph.story.write_gate import merge_text

        base = 'a\nb\u2028c\nd\n'
        disk = 'A\nb\u2028c\nd\n'
        draft = 'a\nb\u2028c\nD\n'
        merged = merge_text(base, disk, draft)
        assert merged == 'A\nb\u2028c\nD\n', repr(merged)

    def test_no_splitlines_in_the_merge_chain(self):
        """
        静态闸：这条链路上**不许**再出现 `str.splitlines()`（AST 扫，不算注释/文档）。

        `patcher` 早就有这一条（`test_patch_line_splitting.py`），
        第十四/十五轮新加的两处（`write_gate`、`generation_guard`
        的顶层块切分与逐行播报）是漏网的 —— 上面那几条判据是行为侧，这条是源码侧。
        """
        targets = {
            'write_gate': ROOT / 'storygraph/story/write_gate.py',
            'generation_guard': ROOT / 'storygraph/story/generation_guard.py',
        }
        bad = []
        for name, path in targets.items():
            tree = ast.parse(path.read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr == 'splitlines'):
                    bad.append(f'{name}:{node.lineno}')
        assert not bad, ('这些地方还在用 str.splitlines() 切行 —— '
                         'U+2028 会把一行撕成两行：\n  ' + '\n  '.join(bad))


# ---------------------------------------------------------------------------
#  四、边界：紧挨着 ≠ 同一处
# ---------------------------------------------------------------------------

class TestAdjacent:
    @pytest.mark.parametrize('where', ['上面', '下面'])
    def test_adjacent_insert_is_refused_and_says_so(self, tmp_path, where):
        """
        他在你改的那一行**旁边**加一行 → **拒**（第十四轮定的：宁可二选一），
        但措辞必须说"紧挨着"，不能说"改的正是同一处"。

        为什么这算健壮性：说成"同一处"时用户会去找"他什么时候改了我这句"，
        找不到 —— 然后就不信这个提示了。
        """
        inject = (lambda t: t.replace('    e "第二句"\n',
                                      '    # 他加的一行\n    e "第二句"\n')
                  if where == '上面' else
                  t.replace('    e "第二句"\n',
                            '    e "第二句"\n    # 他加的一行\n'))
        s, src = _session(tmp_path)
        _edit(s, '第二句')
        theirs = inject(BASE)
        _write(src, theirs)

        res = s.export(src)
        assert res['ok'] is False, res
        msg = ' '.join(res.get('blocking') or [])
        assert '紧挨着' in msg or '旁边' in msg, f'措辞不对：{msg}'
        assert '改的正是同一处' not in msg, f'这是假话（他没动那一行）：{msg}'
        assert '一个字节都没写' in msg, msg
        assert src.read_bytes() == theirs.encode('utf-8')

    def test_his_delete_of_another_line_is_not_resurrected(self, tmp_path):
        """他删掉一整行 → 合并**不许把它复活**（复活 = 拿我的版本覆盖他的删除）。"""
        s, src = _session(tmp_path)
        _edit(s, '第二句')
        _write(src, BASE.replace('    e "第一句"\n', ''))

        res = s.export(src)
        assert res['ok'], res
        after = src.read_text(encoding='utf-8')
        assert '第一句' not in after, '他删掉的那行被复活了'
        assert MINE in after

    def test_his_delete_of_my_line_is_refused(self, tmp_path):
        """他删的正是我要改的那行 → 拒（删 vs 改，说不清该留谁）。"""
        s, src = _session(tmp_path)
        _edit(s, '第二句')
        theirs = BASE.replace('    e "第二句"\n', '')
        _write(src, theirs)

        res = s.export(src)
        assert res['ok'] is False, res
        assert src.read_bytes() == theirs.encode('utf-8')


# ---------------------------------------------------------------------------
#  五、失败时：不崩、不脏
# ---------------------------------------------------------------------------

class TestFailsSafe:
    def test_source_deleted_between_load_and_export(self, tmp_path):
        """源文件被删了 → 拒（或抛），**不许**凭空写出一份"全量"。"""
        s, src = _session(tmp_path)
        _edit(s, '第二句')
        src.unlink()
        res = s.export(src)
        assert res['ok'] is False, res
        assert not src.exists(), '凭空又造了一个文件'

    def test_readonly_file_is_refused_and_content_is_unchanged(self, tmp_path):
        """只读文件：拒（或抛 OSError），**内容一个字节不变**，不许崩在别处。"""
        s, src = _session(tmp_path)
        _edit(s, '第二句')
        theirs = BASE.replace('第一句', HIS)
        _write(src, theirs)
        os.chmod(src, 0o444)
        try:
            try:
                res = s.export(src)
                assert res['ok'] is False, res
            except OSError:
                pass                      # 抛出来也接受（前端会弹「导出失败」）
            assert src.read_bytes() == theirs.encode('utf-8')
        finally:
            os.chmod(src, 0o666)

    def test_his_broken_syntax_is_refused_and_the_file_stays_his(self, tmp_path):
        """
        他把缩进改坏了 → 引擎闸门拦住，**文件留在他的样子**（我们一个字不写）。

        这一条防的是"合并出来的文件也坏了、但还是写出去"：`OPAQUE_LOST` /
        `ENGINE_PARSE_ERROR` 任意一条拦住即可。
        """
        s, src = _session(tmp_path)
        _edit(s, '第二句')
        theirs = BASE.replace('    e "第一句"', 'e "第一句"')
        _write(src, theirs)

        res = s.export(src)
        assert res['ok'] is False, res
        assert src.read_bytes() == theirs.encode('utf-8'), '把他的坏文件覆盖掉了'
        assert res.get('codes'), '拒了却不说为什么'


# ---------------------------------------------------------------------------
#  六、规模
# ---------------------------------------------------------------------------

class TestScale:
    def test_two_thousand_beats_with_two_distant_external_edits(self, tmp_path):
        """2000 拍、他改首尾两处、我改中间一处：三处都对，而且不是几十秒。"""
        lines = ['default score = 0', '', 'label a:']
        lines += [f'    e "第 {i} 句"' for i in range(2000)]
        text = '\n'.join(lines) + '\n'
        s, src = _session(tmp_path, text)
        _edit(s, '第 1000 句', '我改的第一千句')
        _write(src, text.replace('第 10 句', '他改的第十句').replace(
            '第 1990 句', '他改的第一九九〇句'))

        t0 = time.perf_counter()
        res = s.export(src)
        spent = time.perf_counter() - t0
        assert res['ok'], res
        after = src.read_text(encoding='utf-8')
        assert '他改的第十句' in after
        assert '他改的第一九九〇句' in after
        assert '我改的第一千句' in after
        # 一句都不许少、也不许重复（改名的那三句仍以 `    e "` 开头）
        assert after.count('\n    e "') == 2000, '有行被吞了或重复了'
        assert spent < 30, f'两千拍用了 {spent:.1f}s（是不是退化成 O(n²) 了）'


# ---------------------------------------------------------------------------
#  七、随机性质：不重叠的改动**总是**能合，而且结果与左右无关
# ---------------------------------------------------------------------------

class TestRandomDisjointEdits:
    def test_two_hundred_random_cases(self):
        """
        固定种子、200 组随机的不重叠改动：合并结果必须 == "两边都落实"，
        而且**交换左右不该改结果**（disjoint 时合并没有方向性）。
        """
        from storygraph.story.write_gate import merge_text

        rng = random.Random(20261108)
        checked = 0
        for _ in range(200):
            n = rng.randint(8, 40)
            base = [f'l{i}' for i in range(n)]
            # 左边改 [i, j)，右边改 [k, l)，满足 j <= k（不重叠）
            i = rng.randint(0, n - 2)
            j = rng.randint(i + 1, n - 1)
            k = rng.randint(j, n - 1)
            l = rng.randint(k + 1, n)
            theirs = base[:i] + [f'H{i}'] + base[j:]
            mine = base[:k] + [f'M{k}'] + base[l:]
            expected = base[:i] + [f'H{i}'] + base[j:k] + [f'M{k}'] + base[l:]
            a = merge_text('\n'.join(base), '\n'.join(theirs),
                           '\n'.join(mine))
            b = merge_text('\n'.join(base), '\n'.join(mine),
                           '\n'.join(theirs))
            assert a == '\n'.join(expected), (
                f'base={base} i..j={i},{j} k..l={k},{l}\n'
                f'theirs={theirs}\nmine={mine}\n得到={a.splitlines()}')
            assert a == b, '左右交换之后结果变了（不重叠时不该有方向性）'
            checked += 1
        assert checked == 200

    def test_touching_always_returns_empty(self):
        """
        反过来：**挨上了就一定合不了**（返回 `''`）—— 判据在 `_touch`，不在运气。

        这一条守的是"能合"和"合不了"的分界：`_touch` 说碰到（同一行两改、
        同一点两插、插入点落在替换段的端点），`merge_text` 就一个字节都不给。
        """
        from storygraph.story.write_gate import merge_text

        base = 'a\nb\nc\nd\n'
        # 不撞的：两段离得很开
        assert merge_text(base, 'A\nb\nc\nd\n', 'a\nb\nc\nD\n') != ''
        # 同一行两改 → 撞
        assert merge_text(base, 'a\nB\nc\nd\n', 'a\nX\nc\nd\n') == '', \
            '同一行两改居然合上了'
        # 两边都没改 → 原样返回（不算"撞"）
        assert merge_text(base, base, base) == base
        # 相邻：我改第 3 行（`c`，0 基 2），他正好插在它前面 / 后面
        # —— 第十四轮定的是**拒**（谁在前说不准），这里钉住它
        assert merge_text(base, 'a\nb\nH\nc\nd\n', 'a\nb\nC\nd\n') == '', \
            '他插在我改的那行**上面**，居然合上了'
        assert merge_text(base, 'a\nb\nc\nH\nd\n', 'a\nb\nC\nd\n') == '', \
            '他插在我改的那行**下面**，居然合上了'
        # 隔着一行（中间还有 `b`）→ 不撞，能合
        assert merge_text(base, 'a\nH\nb\nc\nd\n', 'a\nb\nC\nd\n') != '', \
            '中间隔着一行，本来不该撞'


# ---------------------------------------------------------------------------
#  八、措辞：给用户的那句话必须是一行
# ---------------------------------------------------------------------------

class TestWordingIsOneLine:
    def test_card_reason_never_contains_a_newline(self):
        """
        `card_at_lines` 要拼进状态栏/单行提示 —— 台词里带换行时不许把它带出来。

        （`Beat.text` 里的换行会被读表清洗成空格，但那是另一条路的保证；
          这里守的是"就算它带，也不许漏进那句提示"。）
        """
        from types import SimpleNamespace

        from storygraph.app.session_export import card_at_lines

        beats = [SimpleNamespace(src_first=3, src_last=4,
                                 text='第一行\n第二行\t带制表符')]
        got = card_at_lines(beats, [(3, 3)])
        assert got, '没认出卡号'
        assert '\n' not in got and '\t' not in got, repr(got)
