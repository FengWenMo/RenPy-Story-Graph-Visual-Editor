# -*- coding: utf-8 -*-
"""
一句台词中间换表情（2026-11-14）
================================

用户："在同一句中需要反复变换表情的情况，我们的编辑器可否再做得更直接一点？
我理解 Renpy 是写 {nw} 后接 extend，新建语句然后再分表情的……但要是剧本不明白
怎么办？有无更方便的编辑方式呢？"

Ren'Py 的写法是**三条语句**：

    l "蒋忱，你在这里吗……{nw}"
    show l base surprised
    extend "哇！"

`{nw}` = 这句不等点击就往下走；`extend` = 接着同一个对话框往下写。所以游戏里
看到的还是一整句话，只是中间换了张画。

编辑器这边的对应关系**已经天然成立**（解析器把这三行收成两张卡：对白 + 接续，
`show` 挂在接续那张上，导出零差异）。缺的只是"怎么把它做出来"——
本文件钉的是那个动作（`EditorSession.split_say_at`）：

  · 在光标处断开 → 前半句加 `{nw}`、后半句进一张新的「接续」卡；
  · **不换表情时一行 `show` 都不多写**（新卡的 `show` 是空的 = 沿用台上的人）；
  · 换表情就写在那张卡上，导出自动变成上面那三行；
  · 一步撤销（`SplitOp`）能退回没拆之前 —— 不是"先撤新卡、再改回文字"两步。

真机（`sr_general` 的章节形状，见 `tools/_probe_split_say.py`）：拆 → 换表情 →
导出 → 重新载入 = 同样两张卡；再导一次逐字节相同（不动点）。
"""
from __future__ import annotations

import pytest

CH = ('label ch01:\n'
      '    l "蒋忱，你在这里吗……哇！"\n'
      '    return\n')


def _project(tmp_path, body=CH, name='ch01.rpy'):
    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True, exist_ok=True)
    path = root / 'game' / name
    path.write_text(body, encoding='utf-8')
    return root, path


def _session(root, path):
    from storygraph.app.editor import EditorSession

    s = EditorSession(project_root=root)
    s.load(path)
    return s


def _say_index(session, needle='蒋忱'):
    return next(i for i, b in enumerate(session.beats)
                if needle in str(b.text or ''))


@pytest.fixture
def session(tmp_path):
    root, path = _project(tmp_path)
    return _session(root, path)


# ---------------------------------------------------------------------------
#  ① 拆开之后是什么
# ---------------------------------------------------------------------------

class TestSplitMakesTheClassicPattern:

    def test_两张卡_前半带_nw_后半是接续(self, session):
        idx = _say_index(session)
        cut = session.beats[idx].text.index('哇')
        res = session.split_say_at(idx, cut)

        assert res['ok'], res.get('error')
        assert session.beats[idx].text == '蒋忱，你在这里吗……{nw}'
        assert session.beats[res['index']].kind == 'extend'
        assert session.beats[res['index']].text == '哇！'
        # 接续卡沿用同一个说话人（extend 本来就不写说话人）
        assert session.beats[res['index']].speaker == 'l'
        # 新卡是新身份（协作锁 / XLIFF / TTS 都按 line_id 认）
        assert (session.beats[res['index']].line_id
                != session.beats[idx].line_id)

    def test_不换表情时不多写一行_show(self, session, tmp_path):
        """新卡的 `show` 是空的 —— 台上的立绘照旧，产物里不该多一行。"""
        idx = _say_index(session)
        res = session.split_say_at(idx, session.beats[idx].text.index('哇'))
        out = tmp_path / 'out.rpy'
        r = session.export(out)
        assert r.get('ok'), r.get('blocking')
        text = out.read_text(encoding='utf-8')
        assert 'l "蒋忱，你在这里吗……{nw}"' in text
        assert 'extend "哇！"' in text
        assert 'show ' not in text, text

    def test_换表情就变成三行_顺序也对(self, session, tmp_path):
        idx = _say_index(session)
        res = session.split_say_at(idx, session.beats[idx].text.index('哇'))
        session.beats[res['index']].show = 'l base surprised'
        out = tmp_path / 'out.rpy'
        assert session.export(out).get('ok')
        text = out.read_text(encoding='utf-8')
        say = text.index('l "蒋忱，你在这里吗……{nw}"')
        show = text.index('show l base surprised')
        ext = text.index('extend "哇！"')
        assert say < show < ext, text
        assert text.count('show ') == 1

    def test_导出的产物重新载入还是同样两张卡(self, session, tmp_path):
        """闭合：产物 → 解析 → 还是"对白 + 接续"，而且再导一次逐字节相同。"""
        idx = _say_index(session)
        res = session.split_say_at(idx, session.beats[idx].text.index('哇'))
        session.beats[res['index']].show = 'l base surprised'
        out = tmp_path / 'out.rpy'
        assert session.export(out).get('ok')

        from storygraph.app.editor import EditorSession
        again = EditorSession(project_root=session.project_root)
        again.load(out)
        cards = [(b.kind, b.text, b.show) for b in again.beats][:2]
        assert cards[0][:2] == ('say', '蒋忱，你在这里吗……{nw}')
        assert cards[1] == ('extend', '哇！', 'l base surprised')

        out2 = tmp_path / 'out2.rpy'
        assert again.export(out2).get('ok')
        assert out2.read_text(encoding='utf-8') == out.read_text(encoding='utf-8')

    def test_旁白也能这样断(self, tmp_path):
        """旁白没有说话人：`extend` 那半句也不能凭空长出一个说话人。"""
        root, path = _project(tmp_path, 'label ch01:\n'
                                      '    "前半后半"\n'
                                      '    return\n')
        s = _session(root, path)
        idx = _say_index(s, '前半后半')
        res = s.split_say_at(idx, 2)
        assert res['ok'], res.get('error')
        assert s.beats[res['index']].kind == 'extend'
        assert s.beats[res['index']].speaker == ''

        out = tmp_path / 'out.rpy'
        assert s.export(out).get('ok')
        text = out.read_text(encoding='utf-8')
        assert '"前半{nw}"' in text and 'extend "后半"' in text
        # 重新载入还是两口，而且都没有说话人
        from storygraph.app.editor import EditorSession
        again = EditorSession(project_root=root)
        again.load(out)
        assert [b.kind for b in again.beats][:2] == ['narration', 'extend']
        assert again.beats[1].speaker == ''

    def test_一句话可以反复拆_接续卡也能再拆(self, tmp_path):
        """"同一句中**反复**变换表情"：接续卡再拆一次照样成立。"""
        root, path = _project(tmp_path, 'label ch01:\n'
                                      '    l "前半后半句"\n'
                                      '    return\n')
        s = _session(root, path)
        first = s.split_say_at(0, 2)
        assert first['ok'], first.get('error')
        second = s.split_say_at(first['index'], 1)
        assert second['ok'], second.get('error')
        assert [b.kind for b in s.beats][:3] == ['say', 'extend', 'extend']
        assert [b.text for b in s.beats][:3] == ['前半{nw}', '后{nw}', '半句']
        # 说话人一路带下去（三张卡是同一个人的同一句话）
        assert {b.speaker for b in s.beats[:3]} == {'l'}


# ---------------------------------------------------------------------------
#  ② 撤销：一次退回没拆之前
# ---------------------------------------------------------------------------

class TestUndoIsOneStep:

    def test_撤销一次就回到原句(self, session):
        before = [b.text for b in session.beats]
        idx = _say_index(session)
        res = session.split_say_at(idx, session.beats[idx].text.index('哇'))
        assert res['ok']

        done = session.undo_structural()
        assert done.get('ok'), done.get('error')
        assert [b.text for b in session.beats] == before
        assert '{nw}' not in session.beats[idx].text
        assert all(b.kind != 'extend' for b in session.beats)

    def test_撤销之后还能再拆一次(self, session):
        idx = _say_index(session)
        assert session.split_say_at(idx, 10)['ok']
        assert session.undo_structural()['ok']
        again = session.split_say_at(idx, 3)
        assert again['ok'], again.get('error')
        assert session.beats[0].text.endswith('{nw}')


# ---------------------------------------------------------------------------
#  ③ 什么时候**不许**拆（不猜，说清楚怎么用）
# ---------------------------------------------------------------------------

class TestRefusals:

    @pytest.mark.parametrize('pos', [0, -1, 999])
    def test_光标在两头时不猜(self, session, pos):
        idx = _say_index(session)
        before = [b.text for b in session.beats]
        res = session.split_say_at(idx, pos)
        assert not res['ok']
        assert '光标' in res['error']
        assert [b.text for b in session.beats] == before

    def test_不是台词卡就拒绝(self, tmp_path):
        root, path = _project(tmp_path, 'label ch01:\n'
                                      '    jump other\n'
                                      'label other:\n'
                                      '    return\n')
        s = _session(root, path)
        res = s.split_say_at(0, 1)
        assert not res['ok']
        assert '台词卡' in res['error']

    def test_条件块里的那句不许拆(self, tmp_path):
        """`if` 里拆开会多出一个同条件的 `if` 块，而补丁对条件叠加会拒写 ——
        宁可不动（结构一个字都不许改），也不许悄悄退回全量重生成。"""
        root, path = _project(tmp_path, 'label ch01:\n'
                                      '    if love >= 5:\n'
                                      '        l "前半……哇！"\n'
                                      '    return\n')
        s = _session(root, path)
        idx = _say_index(s, '哇')
        res = s.split_say_at(idx, s.beats[idx].text.index('哇'))
        assert not res['ok']
        assert '条件块' in res['error']
        assert len(s.beats) == 2                  # say + return 那拍
        assert '{nw}' not in s.beats[idx].text
        # 界面在**按下按钮之前**就要说同一句话（前端不许另编一套理由）
        assert s.split_blocked_reason(s.beats[idx]) == res['error']

    def test_越界拍号也不炸(self, session):
        assert not session.split_say_at(99, 1)['ok']
