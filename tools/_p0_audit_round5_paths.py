"""P0 审计·第五轮·第二层：真实写入路径 × 诅咒文件。

只跑 `plan_* → apply_*` 的真 API，不改产品代码。判据是"构造期望字节"。

跑法： python tools/_p0_audit_round5_paths.py
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PASS, FAIL = '[PASS]', '[FAIL]'
LS = b'\xe2\x80\xa8'          # U+2028 的 UTF-8 字节
results = []


def check(name, ok, detail=''):
    results.append((ok, name))
    print(f'{PASS if ok else FAIL} {name}')
    if detail:
        for line in str(detail).splitlines():
            print(f'       {line}')


SCRIPT = '''# 删边测试
label main:
    e "第一句"
    jump side
    e "第三句"
    return

label side:
    e "支线"
    return
'''


def build_project(root: Path, body: bytes):
    (root / 'game').mkdir(parents=True, exist_ok=True)
    p = root / 'game' / 'ch01.rpy'
    p.write_bytes(body)
    return p


def find_jump_beat(project_root, path):
    from storygraph.app.editor import EditorSession
    s = EditorSession(project_root=str(project_root))
    s.load(str(path))
    for b in s.beats:
        if str(getattr(b, 'raw', '') or '').strip().startswith('jump '):
            return b
    return None


def case_source_delete(label, body: bytes):
    from storygraph.app.source_line_delete import (apply_source_line_delete,
                                                  plan_source_line_delete)
    tmp = Path(tempfile.mkdtemp(prefix='sg_p0_r5p_'))
    try:
        p = build_project(tmp, body)
        before0 = p.read_bytes()
        try:
            beat = find_jump_beat(tmp, p)
        except ValueError as exc:                   # 载入阶段就拒了
            check(f'{label}：载入即拒（字节不动）', True, f'{exc}')
            check(f'{label}：拒写后字节确实未动', p.read_bytes() == before0)
            return
        if beat is None:
            check(f'{label}：找得到 jump 拍', False, '解析不出 jump')
            return
        plan = plan_source_line_delete(str(tmp), str(p), beat.line_id)
        if not plan.ok:
            check(f'{label}：计划被拒（合法，但要说清）', True,
                  f'blocking={plan.blocking}')
            return
        before = p.read_bytes()
        res = apply_source_line_delete(plan)
        after = p.read_bytes()
        nl = b'\r\n' if b'\r\n' in before else b'\n'
        exp = before.replace(b'    jump side' + nl, b'')
        check(f'{label}：删一拍后其余字节不变', after == exp,
              f'ok={res.get("ok")} err={res.get("error")!r}\n'
              f'期望={exp!r}\n实际={after!r}')
        check(f'{label}：U+2028 没有被当换行符写出去',
              after.count(LS) == before.count(LS),
              f'before={before.count(LS)} after={after.count(LS)}')
        check(f'{label}：非法字节 0xFF 还在', (b'\xff' not in before)
              or (b'\xff' in after),
              f'before 有 0xFF={b"\xff" in before} after 有={b"\xff" in after}')
        check(f'{label}：末尾换行形态不变',
              before.endswith(nl) == after.endswith(nl),
              f'before 末尾={before[-4:]!r} after 末尾={after[-4:]!r}')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def case_route(label, body: bytes):
    from storygraph.story.control_route import (apply_control_route,
                                                plan_control_route)
    tmp = Path(tempfile.mkdtemp(prefix='sg_p0_r5q_'))
    try:
        (tmp / 'game' / 'script').mkdir(parents=True)
        (tmp / 'game' / 'script' / 'ch01.rpy').write_text(
            'label ch01:\n    e "一"\n    return\n', encoding='utf-8')
        (tmp / 'game' / 'script' / 'ch02.rpy').write_text(
            'label ch02:\n    e "二"\n    return\n', encoding='utf-8')
        ctl = tmp / 'game' / 'script' / 'chapter_controller.rpy'
        ctl.write_bytes(body)
        plan = plan_control_route(str(tmp), 'ch01', 'ch02', 'jump')
        if not plan.ok:
            check(f'{label}：计划', False, f'blocking={plan.blocking}')
            return
        before = ctl.read_bytes()
        res = apply_control_route(plan)
        after = ctl.read_bytes()
        if not res.get('ok') and after == before:
            # 拒写也是合法结论 —— 只要**字节没动**且说话说清了。
            check(f'{label}：拒写且字节未动（说清原因）',
                  'UTF-8' in str(res.get('error') or ''),
                  f'err={res.get("error")!r}')
            return
        check(f'{label}：接线 ok', bool(res.get('ok')),
              f'err={res.get("error")!r}')
        b_lines = [l for l in before.split(b'\n')]
        a_lines = after.split(b'\n')
        missing = [l for l in b_lines if l and l not in a_lines]
        check(f'{label}：原有行逐字节还在', not missing,
              f'丢={missing!r}\nbefore={before!r}\nafter={after!r}')
        # 接线可能是"替换 return 为 jump"（0 行）或"插一行"（1 行）
        check(f'{label}：行数只增不减且不超过 1',
              0 <= len(a_lines) - len(b_lines) <= 1,
              f'新增 {len(a_lines) - len(b_lines)} 行')
        check(f'{label}：U+2028 没有被当换行符',
              after.count(LS) == before.count(LS),
              f'before={before.count(LS)} after={after.count(LS)}\n'
              f'after={after!r}')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    case_source_delete('L0 纯 LF 基线', SCRIPT.encode('utf-8'))

    # V1：CRLF + 台词里塞 U+2028（先看解析器认不认这个文件）
    v1 = SCRIPT.replace('e "第一句"', 'e "第一\u2028句"').replace(
        '\n', '\r\n').encode('utf-8')
    case_source_delete('V1 CRLF+台词内U+2028', v1)

    # V1b：U+2028 在**文件末尾的注释**里（不影响拍的物理行号）
    v1b = (SCRIPT + '# 尾注\u2028续\n').replace('\n', '\r\n').encode('utf-8')
    case_source_delete('V1b CRLF+尾注U+2028', v1b)

    # V2：CRLF + 非法 UTF-8 字节（注释里）
    v2 = SCRIPT.replace('\n', '\r\n').encode('utf-8').replace(
        '删边测试'.encode('utf-8'), b'\xe5\x88\xa0\xff\xe8\xbe\xb9')
    case_source_delete('V2 CRLF+非法字节', v2)

    case_source_delete('V3 末尾无换行', SCRIPT.rstrip('\n').encode('utf-8'))

    ctl = ('label ch01_control:\r\n    call ch01\r\n    return\r\n\r\n'
           'label ch02_control:\r\n    call ch02\r\n    return\r\n')
    case_route('R1 编排层 CRLF', ctl.encode('utf-8'))
    case_route('R2 编排层 CRLF+U+2028',
               ctl.replace('call ch02', 'call ch02 # 注\u2028释').encode('utf-8'))
    case_route('R3 编排层 CRLF+非法字节',
               ctl.encode('utf-8').replace(
                   b'label ch02_control',
                   b'# \xff\xfe\r\nlabel ch02_control'))

    ok_n = sum(1 for ok, _ in results if ok)
    print(f'\n汇总：{ok_n}/{len(results)} 通过')
    return 0 if ok_n == len(results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
