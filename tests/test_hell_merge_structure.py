# -*- coding: utf-8 -*-
"""
地狱测试（第二轮）：**合出来的那份，到底能不能启动**。
==========================================================

第十五轮那 23 条守的是"不弄坏他的字节"（幂等 / 逐字节 / 不可见字符 / 边界 /
失败时不脏 / 规模 / 随机）。这一轮换一个角度，专挑**每一份单独看都合法、
合起来却没有意义**的情形 —— 行级合并只认行、不认缩进，漏洞就在这里：

1. **他会把你改的那几行挪进别的块里**（第十六轮抓到，最严重的一类）：
   他删掉 `menu:` 那一行、你改了菜单项 —— 合并"成功"，产物是

       label a:
               "改过的左边":        ← 8 个空格，上面没有块头

   一个**两边谁都没写过**的文件：Ren'Py 一启动就报缩进错，而回执说"合上了"。
   现在 `write_gate` 判 `CLASH_REPARENTED`、一个字节都不写，并且**不许**说成
   "撞在同一处"（那是假话，他没碰你那一行）。

2. **合起来会变成空文件**：两边各删一半 —— 从前 `merge_text` 用 `''` 兼作
   "空结果"和"合不了"两个意思，于是拒写，理由却是"撞在同一处"。

3. **他那行的末尾换行被吞掉**：基线末尾没有换行、他往文件末尾追加一行（带
   换行）时，合并结果按**基线**的结尾拼 → 他那行的 `\\n` 没了，
   `git diff` 报 "\\ No newline at end of file"。

4. 随机对照：自己构造的改动**唯一答案**已知，合并结果必须与之逐字相同；
   重复行那一档退一步，只要求"一行不多、一行不少"（顺序可辩，内容不可丢）。

约定：本文件里所有"逐字节"判据都用 `newline=''` 写盘 —— 不这样的话
`Path.write_text()` 在 Windows 上会把 `\\n` 翻成 `\\r\\n`，比字节永远不相等
（第十五轮踩过，探针自己报了 5 个假警报）。
"""

import ast
import random
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

#: 一个**结构完整**的章节：menu 里有选项、选项里有台词。
BASE = ('default score = 0\n'
        '\n'
        'label a:\n'
        '    menu:\n'
        '        "去左边":\n'
        '            e "左边"\n'
        '        "去右边":\n'
        '            e "右边"\n'
        '    e "收尾"\n')
MINE = '改过的左边'
HIS_DELETE_HEADER = BASE.replace('    menu:\n', '')


def _write(path: Path, text: str) -> None:
    """逐字节写（`newline=''` 不翻译换行）。"""
    path.write_text(text, encoding='utf-8', newline='')


def _session(tmp_path, text=BASE):
    from storygraph.app.editor import EditorSession

    src = tmp_path / 'ch.rpy'
    _write(src, text)
    s = EditorSession(project_root=tmp_path)
    s.load(src)
    return s, src


def _edit_choice(s, new=MINE):
    """把菜单第一项的文字改掉（走**拍**，也就是真正的那条编辑路）。"""
    for b in s.beats:
        if '去左边' in str(getattr(b, 'text', '') or ''):
            b.text = new
            return True
    raise AssertionError('没找到"去左边"那一张卡')


def _draft_of(base=BASE, new=MINE):
    """我这版全文（`commit_text` 的第三个参数就是它）。"""
    return base.replace('"去左边"', f'"{new}"')


# ---------------------------------------------------------------------------
#  一、合出来会坏：他删/改了你那一块的**开头那一行**
# ---------------------------------------------------------------------------

class TestReparented:
    def test_export_refuses_and_keeps_his_file(self, tmp_path):
        """
        他删掉 `menu:`、我改菜单项 → **拒写**，文件逐字节还是他的。

        改之前（第十五轮的代码）：`ok=True`、写出去一份 8 个空格悬空的
        `"改过的左边":` —— Ren'Py 起不来，而回执说"两边都落上了"。
        """
        s, src = _session(tmp_path)
        _edit_choice(s)
        _write(src, HIS_DELETE_HEADER)

        res = s.export(src)
        assert res['ok'] is False, res
        assert src.read_bytes() == HIS_DELETE_HEADER.encode('utf-8'), \
            '拒写却动了文件（或者写了一份两边谁都没写过的）'
        msg = ' '.join(res.get('blocking') or [])
        assert '落进别的块' in msg, f'没说清合起来会坏在哪：{msg}'
        assert '撞在' not in msg, f'这句是假话（他没碰那一行）：{msg}'

    def test_commit_text_reports_the_header_line(self, tmp_path):
        """`commit_text` 这条路：`clash_kind=reparented`，并指出是哪一行。"""
        from storygraph.story.write_gate import (CLASH_REPARENTED,
                                                 GATE_CONFLICT, commit_text)

        fp = tmp_path / 'ch.rpy'
        _write(fp, HIS_DELETE_HEADER)
        before = fp.read_bytes()
        c = commit_text(fp, BASE, _draft_of())

        assert c.state == GATE_CONFLICT and not c.ok
        assert c.clash_kind == CLASH_REPARENTED, c.clash_kind
        assert c.final_text == '', '拒写却给了可用的正文（有人会拿它去写盘）'
        # 第 4 行是基线里的 `    menu:`，第 5 行才是我的菜单项
        assert c.parent == (4, 'menu:'), c.parent
        assert c.clash == ((4, 4),), c.clash
        assert fp.read_bytes() == before, '拒写却动了文件'
        assert '「menu:」' in c.error() and '第 4 行' in c.error(), c.error()

    def test_wording_never_claims_a_collision(self, tmp_path):
        """
        两种新的"合不了"都**不是撞车** —— 措辞里不许出现"撞在…"。

        （只说"不是同一处"还不够：兜底那句"正撞在第 4 行"同样在说撞车，
         第十六轮变异 M9 就是从这个缝里溜过去的。）
        """
        from storygraph.story.write_gate import commit_text

        fp = tmp_path / 'ch.rpy'
        _write(fp, HIS_DELETE_HEADER)
        c = commit_text(fp, BASE, _draft_of())
        for text in (c.error(), c.sentence()):
            assert '撞在' not in text, text
            assert '没有写' in text, text
        assert '落进别的块' in c.error(), c.error()

    def test_deleted_if_header_inside_the_same_label(self, tmp_path):
        """
        同 label 里删掉 `if flag:`（块头）而我改块里最后一句 → 拒。

        为什么这条必须单独测：父语句不在文件顶层，`label a:` 之上还有一层，
        判据是"父语句换了"，不是"缩进变 0 了"。
        """
        from storygraph.story.write_gate import (CLASH_REPARENTED,
                                                 merge_three_way)

        base = ('label a:\n'
                '    if flag:\n'
                '        e "一"\n'
                '        e "二"\n'
                '    e "三"\n')
        disk = base.replace('    if flag:\n', '')
        draft = base.replace('e "二"', 'e "二改"')
        v = merge_three_way(base, disk, draft)
        assert not v.ok and v.clash_kind == CLASH_REPARENTED, v.as_dict()
        assert v.parent == (2, 'if flag:'), v.parent

    def test_export_and_commit_agree_on_the_same_trio(self, tmp_path):
        """同一组三份文本，导出那条路与 `commit_text` 必须同一个结论。"""
        from storygraph.story.write_gate import CLASH_REPARENTED, commit_text

        fp = tmp_path / 'ch.rpy'
        _write(fp, HIS_DELETE_HEADER)
        assert commit_text(fp, BASE, _draft_of()).clash_kind == CLASH_REPARENTED

        s, src = _session(tmp_path)
        _edit_choice(s)
        _write(src, HIS_DELETE_HEADER)
        res = s.export(src)
        assert res['ok'] is False
        assert '落进别的块' in ' '.join(res.get('blocking') or [])


class TestReparentedIsNotOverzealous:
    """这几条是**反向**的：真能合的时候不许拒（误伤也是坏）。"""

    def test_renaming_the_block_header_still_merges(self, tmp_path):
        """
        `menu:` → `menu 选项:`（同一缩进、仍是块头）→ **照合**。

        块还在、层级没变，我的那几行照样嵌在这个块头下面；拦它就是把一件
        本来能做好的事推给用户手工做。
        """
        from storygraph.story.write_gate import MERGED, merge_three_way

        disk = BASE.replace('    menu:\n', '    menu 选项:\n')
        v = merge_three_way(BASE, disk, _draft_of())
        assert v.state == MERGED, v.as_dict()
        assert 'menu 选项:' in v.final_text and MINE in v.final_text

    def test_changing_a_sibling_choice_still_merges(self):
        """他改同一个菜单里**另一个**选项（同一缩进）→ 照合，两处都在。"""
        from storygraph.story.write_gate import merge_three_way

        disk = BASE.replace('"去右边"', '"去右边（他改过）"')
        v = merge_three_way(BASE, disk, _draft_of())
        assert v.ok, v.as_dict()
        assert '去右边（他改过）' in v.final_text and MINE in v.final_text

    def test_insert_outside_the_block_does_not_touch_the_parent(self):
        """他在 `menu` 块**外面**加一句（离我 3 行）→ 父语句没变，照合。"""
        from storygraph.story.write_gate import merge_three_way

        disk = BASE.replace('    e "收尾"\n', '    e "收尾"\n    e "他加的一句"\n')
        v = merge_three_way(BASE, disk, _draft_of())
        assert v.ok, v.as_dict()
        assert '他加的一句' in v.final_text and MINE in v.final_text

    def test_deleting_a_comment_above_my_line_is_not_a_parent_change(self):
        """
        整行注释不构成块：他删掉夹在块头和我的选项之间的那行注释，
        不该判成"父语句没了"（缩进比我还浅的注释最容易踩这个）。
        """
        from storygraph.story.write_gate import merge_three_way

        base = BASE.replace('    menu:\n',
                            '    menu:\n    # 他之前写的注释\n')
        disk = base.replace('    # 他之前写的注释\n', '')
        v = merge_three_way(base, disk, _draft_of(base))
        assert v.ok, v.as_dict()
        assert '他之前写的注释' not in v.final_text and MINE in v.final_text

    def test_deleting_a_blank_line_above_my_block_is_not_a_parent_change(self):
        """空行同理：他删掉块头上面那个空行，父语句还是 `label a:`。"""
        from storygraph.story.write_gate import merge_three_way

        base = ('default score = 0\n'
                '\n'
                'label a:\n'
                '\n'
                '    e "一"\n'
                '    e "二"\n')
        disk = base.replace('label a:\n\n', 'label a:\n')
        v = merge_three_way(base, disk, base.replace('e "二"', 'e "二改"'))
        assert v.ok, v.as_dict()
        assert '二改' in v.final_text


# ---------------------------------------------------------------------------
#  二、合起来会把文件拆空
# ---------------------------------------------------------------------------

class TestErased:
    def test_two_disjoint_deletions_do_not_become_an_empty_file(self, tmp_path):
        """
        他删上半、我删下半（两段**不挨着**）→ 拒，**不写成空文件**。

        改之前：`merge_text` 返回 `''`，而 `''` 同时是"合不了"的哨兵 ——
        于是拒了，但理由说"撞在同一处"（假话：两段隔着一行）。
        """
        from storygraph.story.write_gate import (CLASH_ERASED,
                                                 GATE_CONFLICT, commit_text)

        base = 'a\nb\nc\nd\n'
        fp = tmp_path / 'x.rpy'
        _write(fp, 'a\nb\n')
        before = fp.read_bytes()
        c = commit_text(fp, base, 'c\nd\n', history=False)

        assert c.state == GATE_CONFLICT and not c.ok
        assert c.clash_kind == CLASH_ERASED, c.clash_kind
        assert c.final_text == ''
        assert fp.read_bytes() == before, '把文件写空了'
        assert '变成空的' in c.error(), c.error()
        assert '撞在' not in c.error() and '撞在' not in c.sentence(), \
            c.sentence()

    def test_merge_text_still_returns_empty_string(self):
        """`merge_text` 的公开契约不变：合不了 → `''`（老调用方不用改）。"""
        from storygraph.story.write_gate import merge_text

        assert merge_text('a\nb\nc\nd\n', 'a\nb\n', 'c\nd\n') == ''


# ---------------------------------------------------------------------------
#  三、文件末尾的那个换行符归谁
# ---------------------------------------------------------------------------

class TestTrailingNewlineOwnership:
    def test_his_appended_line_keeps_its_newline(self, tmp_path):
        """
        基线末尾没换行、他**在末尾加了一行**（带换行）→ 合完末尾必须有那个
        换行。改之前按基线的结尾拼，他那行的 `\\n` 被吞掉
        （`git diff` 报 "\\ No newline at end of file"）。
        """
        from storygraph.story.write_gate import merge_text, merge_three_way

        base = 'label a:\n    e "x"\n    e "y"'
        disk = base + '\n    e "他加的"\n'
        draft = base.replace('"x"', '"我改的"')
        want = disk.replace('"x"', '"我改的"')

        assert merge_text(base, disk, draft) == want
        assert merge_three_way(base, disk, draft).final_text == want

    def test_the_written_file_ends_with_exactly_one_newline(self, tmp_path):
        from storygraph.story.write_gate import commit_text

        base = 'label a:\n    e "x"\n    e "y"'
        disk = base + '\n    e "他加的"\n'
        fp = tmp_path / 'ch.rpy'
        _write(fp, disk)
        c = commit_text(fp, base, base.replace('"x"', '"我改的"'))

        assert c.state == 'merged', c.as_dict()
        raw = fp.read_bytes()
        assert raw.endswith(b'\n') and not raw.endswith(b'\n\n'), raw[-12:]
        text = raw.decode('utf-8')
        assert '他加的' in text and '我改的' in text

    def test_my_own_trailing_newline_is_kept(self):
        """末尾是我加的行 → 按**我的草稿**的结尾（它带换行就带）。"""
        from storygraph.story.write_gate import merge_text

        base = 'a\nb'
        disk = 'A\nb'                        # 他改第 1 行
        draft = 'a\nb\nM\n'                  # 我在末尾加一行（带换行）
        assert merge_text(base, disk, draft) == 'A\nb\nM\n'

    def test_export_path_keeps_his_trailing_newline(self, tmp_path):
        """整条导出路：他往末尾加一行、我改中间一句，落盘那份末尾有换行。"""
        base = ('default score = 0\n'
                '\n'
                'label a:\n'
                '    e "一句"\n'
                '    e "二句"\n'
                '    e "三句"')            # 注意：**没有**末尾换行
        s, src = _session(tmp_path, base)
        for b in s.beats:
            if str(getattr(b, 'text', '') or '') == '二句':
                b.text = '我改的二句'
        _write(src, base + '\n    e "他加的"\n')

        res = s.export(src)
        assert res['ok'], res
        raw = src.read_bytes()
        assert raw.endswith(b'\n') and not raw.endswith(b'\n\n'), raw[-12:]
        text = raw.decode('utf-8')
        assert '他加的' in text and '我改的二句' in text


# ---------------------------------------------------------------------------
#  四、随机对照：唯一答案已知
# ---------------------------------------------------------------------------

def _edits(rng, n, taken, count):
    """随机改动段 `[(起, 止, 新行), …]`；与 `taken` 互不 `_touch`。"""
    from storygraph.story.write_gate import _touch

    ops = []
    for _ in range(count * 61):
        if len(ops) >= count:
            break
        i1 = rng.randrange(0, n + 1)
        i2 = min(n, i1 + rng.choice([0, 0, 1, 2]))
        new = [f'x{rng.randrange(10**6)}' for _ in
               range(rng.choice([0, 0, 1, 1, 2]))]
        op = (i1, i2, new)
        if i1 == i2 and not new:          # 空改动不算一段（difflib 也看不见）
            continue
        if any(_touch(op, o) for o in ops) or any(_touch(op, o) for o in taken):
            continue
        ops.append(op)
    return sorted(ops)


def _apply(base, ops):
    out, pos = [], 0
    for i1, i2, new in ops:
        assert i1 >= pos
        out.extend(base[pos:i1])
        out.extend(new)
        pos = i2
    out.extend(base[pos:])
    return out


class TestRandomAgainstConstructedAnswer:
    def test_six_hundred_unique_line_cases(self):
        """
        600 组随机改动（行内容**唯一** → 唯一正确答案），合并结果必须逐字相同，
        而且左右交换不改变结果。
        """
        from storygraph.story.write_gate import merge_text

        rng = random.Random(16611)
        checked = 0
        for _ in range(600):
            n = rng.randrange(3, 30)
            base = [f'L{i}' for i in range(n)]
            his = _edits(rng, n, [], rng.choice([1, 1, 2]))
            mine = _edits(rng, n, his, rng.choice([1, 1, 2]))
            base_t = '\n'.join(base) + '\n'
            disk_t = '\n'.join(_apply(base, his)) + '\n'
            draft_t = '\n'.join(_apply(base, mine)) + '\n'
            want = '\n'.join(_apply(base, sorted(his + mine))) + '\n'

            got = merge_text(base_t, disk_t, draft_t)
            if not got:
                continue                     # 保守拒写（相邻/挪块）不算错
            assert got == want, (
                f'base={base}\nhis={his}\nmine={mine}\n'
                f'want={want.splitlines()}\ngot={got.splitlines()}')
            assert merge_text(base_t, draft_t, disk_t) == got, '左右交换结果变了'
            checked += 1
        assert checked >= 400, f'能合的样本太少（{checked}），判据没测到东西'

    def test_two_hundred_duplicate_line_cases_lose_nothing(self):
        """
        重复行那一档（内容一样、**位置**说不准）：不要求顺序，
        只要求**一行不多、一行不少**（丢行/复制才是真的坏）。
        """
        from storygraph.story.write_gate import _touch, merge_text

        pool = ['    e "a"', '    e "b"', '', '    if x:', '        e "c"',
                'label z:']
        rng = random.Random(16612)
        checked = 0
        for _ in range(200):
            n = rng.randrange(4, 16)
            base = [rng.choice(pool) for _ in range(n)]
            his = _edits(rng, n, [], 1)
            mine = _edits(rng, n, his, 1)
            base_t = '\n'.join(base) + '\n'
            disk_t = '\n'.join(_apply(base, his)) + '\n'
            draft_t = '\n'.join(_apply(base, mine)) + '\n'
            merged = merge_text(base_t, disk_t, draft_t)
            if not merged:
                continue
            want = _apply(base, sorted(his + mine))
            assert sorted(merged.split('\n')[:-1]) == sorted(want), (
                f'内容对不上（丢行或复制了行）\nbase={base}\n'
                f'his={his}\nmine={mine}\n得到={merged.split(chr(10))}')
            checked += 1
        assert checked >= 60, checked

    def test_refusals_that_are_not_collisions_never_call_it_a_collision(self):
        """
        **说人话的守则**：判不了可以拒，但拒因只有两种 —— 说真话的（撞车）
        和**不说撞车**的（挪块 / 拆空）。

        注意"撞没撞"要按 **difflib 实际认出来的改动段**算，不能按我构造的：
        重复行会让对齐换一种解法，我这边"隔着一行"可能在对齐里变成"挨着"。
        """
        from storygraph.core.textlines import split_lines
        from storygraph.story.write_gate import (CLASH_ERASED,
                                                 CLASH_OVERLAP,
                                                 CLASH_REPARENTED,
                                                 CLASH_TOUCHING, _touch,
                                                 edits_between, merge_three_way)

        pool = ['    e "a"', '    e "b"', '    menu:', '        "x":',
                '    if x:', '        e "c"', '    e "d"']
        rng = random.Random(16613)
        seen = 0
        for _ in range(200):
            n = rng.randrange(4, 14)
            base = [rng.choice(pool) for _ in range(n)]
            his = _edits(rng, n, [], 1)
            mine = _edits(rng, n, his, 1)
            base_t = '\n'.join(base) + '\n'
            disk_t = '\n'.join(_apply(base, his)) + '\n'
            draft_t = '\n'.join(_apply(base, mine)) + '\n'
            v = merge_three_way(base_t, disk_t, draft_t)
            if v.ok:
                continue
            seen += 1
            left = edits_between(split_lines(base_t), split_lines(disk_t))
            right = edits_between(split_lines(base_t), split_lines(draft_t))
            real_touch = any(_touch(a, b) for a in left for b in right)
            if real_touch:
                assert v.clash_kind in (CLASH_OVERLAP, CLASH_TOUCHING), \
                    v.clash_kind
                continue
            assert v.clash_kind in (CLASH_REPARENTED, CLASH_ERASED), (
                f'没撞车却报 {v.clash_kind!r}：base={base} his={his} mine={mine}')
            assert '撞在' not in v.error(), v.error()
            assert '撞在' not in v.sentence(), v.sentence()
        assert seen >= 1, '一个都没拒到，判据没测到东西'


# ---------------------------------------------------------------------------
#  五、机制本身：判定只有一处
# ---------------------------------------------------------------------------

class TestOneImplementation:
    def test_merge_text_is_a_thin_wrapper(self):
        """
        `merge_text` 只许是 `_merge_plan` 的一层壳（AST：函数体只有一条 return）。

        为什么钉这个：两个入口（导出 / `commit_text`）如果各写一份"能不能合"，
        就会出现"一个说撞车、另一个说挪块"——第十四轮 `merge_text` 写完半年
        没人调，就是这么来的。
        """
        path = ROOT / 'storygraph/story/write_gate.py'
        tree = ast.parse(path.read_text(encoding='utf-8'))
        funcs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
        assert 'merge_text' in funcs and '_merge_plan' in funcs
        body = [n for n in funcs['merge_text'].body
                if not (isinstance(n, ast.Expr)
                        and isinstance(n.value, ast.Constant))]
        assert len(body) == 1 and isinstance(body[0], ast.Return), (
            'merge_text 里又有逻辑了 —— 合并只许在 _merge_plan 里')
        assert '_merge_plan' in ast.unparse(body[0]), 'merge_text 没走 _merge_plan'

    def test_verdict_agrees_with_merge_text(self):
        """`merge_three_way` 说"合上了" ⇔ `merge_text` 给出了非空正文。"""
        from storygraph.story.write_gate import merge_text, merge_three_way

        cases = [
            (BASE, HIS_DELETE_HEADER, _draft_of()),
            (BASE, BASE.replace('"去右边"', '"他改的右边"'), _draft_of()),
            (BASE, BASE.replace('    e "收尾"\n', ''), _draft_of()),
            ('a\nb\nc\nd\n', 'a\nb\n', 'c\nd\n'),
        ]
        for base, disk, draft in cases:
            text = merge_text(base, disk, draft)
            v = merge_three_way(base, disk, draft)
            assert bool(text) == v.ok, (bool(text), v.as_dict())
            if text:
                assert v.final_text == text, (v.final_text, text)


# ---------------------------------------------------------------------------
#  六、规模：新加的闸不是 O(n²)
# ---------------------------------------------------------------------------

class TestScale:
    def test_twenty_thousand_lines_merge_quickly(self):
        """
        2 万行、他改开头、我改结尾 —— 合得上，而且不是几十秒。

        "父语句"那道闸对**我改的每一段**扫一遍上方，是 O(行数)；如果写成
        "每一段都重新扫全文"，2 万行就会明显变慢。
        """
        from storygraph.story.write_gate import merge_text

        lines = ['label a:'] + [f'    e "第 {i} 句"' for i in range(20000)]
        base = '\n'.join(lines) + '\n'
        disk = base.replace('第 1 句', '他改的第 1 句')
        draft = base.replace('第 19999 句', '我改的最后一句')

        t0 = time.perf_counter()
        got = merge_text(base, disk, draft)
        spent = time.perf_counter() - t0

        assert got, '没合上'
        assert '他改的第 1 句' in got and '我改的最后一句' in got
        assert got.count('\n    e "') == 20000, '有行被吞了或重复了'
        assert spent < 10, f'2 万行用了 {spent:.1f}s（闸写成 O(n²) 了？）'
