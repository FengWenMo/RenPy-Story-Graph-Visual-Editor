# -*- coding: utf-8 -*-
"""
地狱测试：把"最极端的魔改写法"喂进真引擎，看 oracle 到底答得对不对。

⚠ 这是**探针/试验**，不是产品代码。它回答的问题是：
   "如果程序真的用各种方式魔改了语法，我们规划的三条线（lint oracle / 定义表覆盖层 / 扫描发现）
    哪些成立、哪些会在哪个具体写法上失灵？"

用法：
    python tools/hell_engine_oracle.py            # 跑全部用例
    python tools/hell_engine_oracle.py C3 C7      # 只跑指定用例

依赖：本机 Ren'Py SDK。路径见 SDK_CANDIDATES / 环境变量 STORYGRAPH_RENPY_SDK。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile

SDK_CANDIDATES = [
    os.environ.get("STORYGRAPH_RENPY_SDK", ""),
    r"D:/software/renpy-8.4.1-sdk",
]

#: fork 模拟用的 SDK（由 build_fork_sdk 建好；缺失则该组用例跳过）
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SDK_FORK = os.path.join(HERE, ".pip-tmp", "fork_sdk_full")

# 每个用例：{名字: {"files": {相对 game/ 的文件名: 内容}, "note": 说明}}
# 基础积木：与真机 sr_general 的 layeredimage 写法同形
LAYERED = '''layeredimage lan:
    group expressions:
        attribute happy default:
            Solid("#0f0")
        attribute sad:
            Solid("#ff0")
    group outfit:
        attribute clothes2:
            Solid("#f00")
        attribute base_swimsuit:
            Solid("#00f")
'''

PROBE = '''init 999 python:
    import json
    def _p():
        o = {}
        try:
            o["images"] = sorted(renpy.list_images())
        except Exception as e:
            o["images_error"] = repr(e)
        try:
            o["tags"] = sorted(renpy.get_available_image_tags())
        except Exception as e:
            o["tags_error"] = repr(e)
        try:
            img = renpy.get_registered_image("lan")
            o["lan_attrs"] = [{"g": str(getattr(a, "group", "")),
                               "a": str(getattr(a, "attribute", "")),
                               "t": type(a).__name__}
                              for a in (getattr(img, "attributes", None) or [])]
        except Exception as e:
            o["lan_attrs_error"] = repr(e)
        try:
            o["lan_ordered"] = [str(x) for x in renpy.get_ordered_image_attributes("lan")]
        except Exception as e:
            o["lan_ordered_error"] = repr(e)
        try:
            o["labels"] = sorted(renpy.get_all_labels())
        except Exception as e:
            o["labels_error"] = repr(e)
        try:
            o["dynamic_img_seen"] = bool(renpy.has_image("dyn_made_up"))
        except Exception as e:
            o["dynamic_img_error"] = repr(e)
        return o
    try:
        with open(os.path.join(renpy.config.basedir, "facts.json"), "w",
                  encoding="utf-8") as f:
            json.dump(_p(), f, ensure_ascii=False, indent=1)
    except Exception as e:
        with open(os.path.join(renpy.config.basedir, "facts_ERROR.txt"), "w",
                  encoding="utf-8") as f:
            f.write(repr(e))
'''

CASES = {
    "C1_baseline": {
        "note": "地基：标准 layeredimage + 一次合法 show。预期：lint 绿",
        "files": {
            "a_script.rpy": LAYERED + '\nlabel start:\n    show lan clothes2 happy\n    return\n',
            "zz_probe.rpy": PROBE,
        },
    },
    "C2_cds_ok": {
        "note": "正确 CDS（python early + 字面量名），在**另一个**文件使用。预期：lint 绿",
        "files": {
            "aa_cds.rpy": 'python early:\n'
                          '    def _sg_weather(lex):\n        return lex.rest()\n'
                          '    renpy.register_statement("weather", parse=_sg_weather)\n',
            "b_script.rpy": LAYERED + '\nlabel start:\n    weather 雨 0.5\n    return\n',
            "zz_probe.rpy": PROBE,
        },
    },
    "C3_cds_load_order": {
        "note": "★ CDS 定义在**后**加载的文件（zzz），却在**前**加载的文件（aaa）使用。"
                "预期：引擎拒绝（证明「扫到 ≠ 生效」）",
        "files": {
            "aaa_use.rpy": LAYERED + '\nlabel start:\n    weather2 雨 0.5\n    return\n',
            "zzz_cds.rpy": 'python early:\n'
                           '    def _sg_w2(lex):\n        return lex.rest()\n'
                           '    renpy.register_statement("weather2", parse=_sg_w2)\n',
            "zz_probe.rpy": PROBE,
        },
    },
    "C4_cds_dynamic_name": {
        "note": "★ CDS 名字**不是字面量**（拼出来的）。引擎应当接受；但扫描器只认字面量 → 扫不到",
        "files": {
            "aa_cds.rpy": 'python early:\n'
                          '    def _sg_w3(lex):\n        return lex.rest()\n'
                          '    _nm = "wea" + "ther3"\n'
                          '    renpy.register_statement(_nm, parse=_sg_w3)\n',
            "b_script.rpy": LAYERED + '\nlabel start:\n    weather3 雨 0.5\n    return\n',
            "zz_probe.rpy": PROBE,
        },
    },
    "C5_cds_block": {
        "note": "带块的自定义语句（block=True）+ 块体里放台词。预期：lint 绿",
        "files": {
            "aa_cds.rpy": 'python early:\n'
                          '    def _sg_cue(lex):\n        return lex.rest()\n'
                          '    renpy.register_statement("mycue", parse=_sg_cue, block=True)\n',
            "b_script.rpy": LAYERED + '\nlabel start:\n    mycue "闪电":\n'
                            '        "打雷了"\n        "好大的雨"\n    return\n',
            "zz_probe.rpy": PROBE,
        },
    },
    "C6_cds_lint_callback": {
        "note": "CDS 自带 lint= 回调，且回调里报错。预期：lint 报出程序自己的错误",
        "files": {
            "aa_cds.rpy": 'python early:\n'
                          '    def _sg_p(lex):\n        return lex.rest()\n'
                          '    def _sg_lint(o):\n'
                          '        renpy.error("SG-LINT: 这个用法被我方 lint 拒绝")\n'
                          '    renpy.register_statement("strict", parse=_sg_p, lint=_sg_lint)\n',
            "b_script.rpy": LAYERED + '\nlabel start:\n    strict 任意\n    return\n',
            "zz_probe.rpy": PROBE,
        },
    },
    "C7_cds_override_builtin": {
        "note": "★ 用 CDS **覆盖内置语句** pause（模拟「改已有语句的解析规则」）。观察会发生什么",
        "files": {
            "aa_cds.rpy": 'python early:\n'
                          '    def _sg_pause(lex):\n        return lex.rest()\n'
                          '    def _sg_ex(o):\n        pass\n'
                          '    renpy.register_statement("pause", parse=_sg_pause, execute=_sg_ex)\n',
            "b_script.rpy": LAYERED + '\nlabel start:\n    pause 1.0\n    "台词"\n    return\n',
            "zz_probe.rpy": PROBE,
        },
    },
    "C8_attr_named_at": {
        "note": "★ 属性**恰好叫 at**（与关键字撞车）：show lan at → 引擎当成属性还是变换？",
        "files": {
            "a_script.rpy": '''layeredimage lan:
    group g:
        attribute happy default:
            Solid("#0f0")
        attribute at:
            Solid("#00f")
label start:
    show lan at
    show lan at happy
    return
''',
            "zz_probe.rpy": PROBE,
        },
    },
    "C9_attr_named_with": {
        "note": "★ 属性叫 with（与 with 语句撞车）",
        "files": {
            "a_script.rpy": '''layeredimage lan:
    group g:
        attribute happy default:
            Solid("#0f0")
        attribute with:
            Solid("#00f")
label start:
    show lan with
    return
''',
            "zz_probe.rpy": PROBE,
        },
    },
    "C10_conflicting_attrs": {
        "note": "★ 同组两个属性同时 show（Ren'Py 应报 ambiguous）",
        "files": {
            "a_script.rpy": '''layeredimage lan:
    group g:
        attribute happy default:
            Solid("#0f0")
        attribute sad:
            Solid("#ff0")
label start:
    show lan happy sad
    return
''',
            "zz_probe.rpy": PROBE,
        },
    },
    "C11_cn_attr": {
        "note": "中文属性名",
        "files": {
            "a_script.rpy": '''layeredimage lan:
    group g:
        attribute happy default:
            Solid("#0f0")
        attribute 泳装:
            Solid("#00f")
label start:
    show lan 泳装
    show lan 泳装 happy
    return
''',
            "zz_probe.rpy": PROBE,
        },
    },
    "C12_attr_order_and_sticky": {
        "note": "属性**换序** + **粘滞**（show lan 不写属性）+ show expression + with",
        "files": {
            "a_script.rpy": LAYERED + '''
label start:
    show lan clothes2 happy
    show lan happy clothes2
    show lan
    show lan with dissolve
    show expression Solid("#000") as dynbg
    scene black with fade
    hide lan
    return
''',
            "zz_probe.rpy": PROBE,
        },
    },
    "C13_masking": {
        "note": "★★ 掩蔽效应：同一文件里既有 parse error（weather），又有非法属性引用。"
                "预期：非法属性**不会被报**",
        "files": {
            "a_script.rpy": LAYERED + '\nlabel start:\n    weather 雨 0.5\n'
                            '    show lan 完全不存在\n    return\n',
            "zz_probe.rpy": PROBE,
        },
    },
    "C14_dynamic_image": {
        "note": "★ init 里用 renpy.image() 动态登记 + 晚于探针(999)登记的图，探针看得见吗",
        "files": {
            "a_script.rpy": LAYERED + '''
init 0 python:
    renpy.image("dyn_made_up", Solid("#123"))

init 1000 python:
    renpy.image("dyn_after_probe", Solid("#456"))

label start:
    show dyn_made_up
    show dyn_after_probe
    return
''',
            "zz_probe.rpy": PROBE,
        },
    },
    "C15_if_any_attr": {
        "note": "★ 条件属性（if_any / if_all，真机里用过）：枚举与收窄还准吗",
        "files": {
            "a_script.rpy": '''layeredimage lan:
    group base:
        attribute normal default:
            Solid("#0f0")
    group extra:
        attribute hat if_any "normal":
            Solid("#00f")
label start:
    show lan normal hat
    return
''',
            "zz_probe.rpy": PROBE,
        },
    },
    "C16_override_probe": {
        "note": "★★ C7 的决定性版：注册一个**必定报错**的 pause 覆盖。"
                "若我方 parse 真的生效 → lint 报 SG-OVERRIDE；若内置赢 → 静默",
        "files": {
            "aa_cds.rpy": 'python early:\n'
                          '    def _sg_ov(lex):\n'
                          '        renpy.error("SG-OVERRIDE: pause 已被项目改写")\n'
                          '        return None\n'
                          '    renpy.register_statement("pause", parse=_sg_ov)\n',
            "b_script.rpy": LAYERED + '\nlabel start:\n    pause 1.0\n    return\n',
            "zz_probe.rpy": PROBE,
        },
    },
    "C17_masking_probe_only": {
        "note": "★★ 对照 C13：只有一个 parse error（无可疑属性）时，"
                "探针是否照样死掉 → 说明「先修 parse error 再采集」是硬前提",
        "files": {
            "a_script.rpy": LAYERED + '\nlabel start:\n    weather 雨 0.5\n    return\n',
            "zz_probe.rpy": PROBE,
        },
    },

    # ================= 第二轮：没证明的部分 =================
    "D1a_override_pause_reject": {
        "note": "★★ C7 的行为级：把 pause 覆盖成「只认 MAGIC」。喂标准 `pause 1.0` "
                "→ 若报错，说明覆盖=**替换**（内置 parse 没了），不是叠加",
        "files": {
            "aa_cds.rpy": 'python early:\n'
                          '    def _p(lex):\n'
                          '        if lex.keyword("MAGIC"):\n            return "ok"\n'
                          '        renpy.error("SG-PAUSE: 现在只认 MAGIC 语法")\n'
                          '        return None\n'
                          '    renpy.register_statement("pause", parse=_p)\n',
            "b_script.rpy": LAYERED + '\nlabel start:\n    pause 1.0\n    return\n',
            "zz_probe.rpy": PROBE,
        },
    },
    "D1b_override_pause_accept": {
        "note": "★★ 同上覆盖，但喂新语法 `pause MAGIC` → 预期被接受",
        "files": {
            "aa_cds.rpy": 'python early:\n'
                          '    def _p(lex):\n'
                          '        if lex.keyword("MAGIC"):\n            return "ok"\n'
                          '        renpy.error("SG-PAUSE: 现在只认 MAGIC 语法")\n'
                          '        return None\n'
                          '    renpy.register_statement("pause", parse=_p)\n',
            "b_script.rpy": LAYERED + '\nlabel start:\n    pause MAGIC\n    return\n',
            "zz_probe.rpy": PROBE,
        },
    },
    "D2_override_show": {
        "note": "★★★ 最高优先级：用 CDS 覆盖内置 `show`。喂标准 show → 若报错，"
                "说明**所有标准 show 的生成逻辑都会失效**",
        "files": {
            "aa_cds.rpy": 'python early:\n'
                          '    def _p(lex):\n'
                          '        renpy.error("SG-SHOW: show 已被项目覆盖")\n'
                          '        return None\n'
                          '    renpy.register_statement("show", parse=_p)\n',
            "b_script.rpy": LAYERED + '\nlabel start:\n    show lan clothes2 happy\n    return\n',
            "zz_probe.rpy": PROBE,
        },
    },
    "D3_override_default_say": {
        "note": "★★★ 用 name=\"\" **替换 say 语句**，且 parse 必定报错 → 若报错，"
                "说明默认语句**真的被整体替换**（否则 say 仍是内置）",
        "files": {
            "aa_cds.rpy": 'python early:\n'
                          '    def _p(lex):\n'
                          '        renpy.error("SG-SAY: 默认 say 语句已被项目替换")\n'
                          '        return None\n'
                          '    renpy.register_statement("", parse=_p)\n',
            "b_script.rpy": LAYERED + '\nlabel start:\n    "裸台词"\n'
                            '    lyj "有说话人的台词"\n    return\n',
            "zz_probe.rpy": PROBE,
        },
    },
    "D4a_fork_statement_fork_sdk": {
        "note": "★★ fork 模拟：引擎**自带** weather6（我们改的 fork SDK），"
                "项目里**一个 CDS 都没有**。预期：被接受 → 说明 fork 加语句对扫描器完全隐形",
        "sdk": "fork",
        "files": {
            "b_script.rpy": LAYERED + '\nlabel start:\n    weather6 雨 0.5\n    return\n',
            "zz_probe.rpy": PROBE,
        },
    },
    "D4b_fork_statement_std_sdk": {
        "note": "★★ 对照：同一个工程换成**官方 SDK** → 预期被拒（证明 weather6 来自 fork）",
        "sdk": "default",
        "files": {
            "b_script.rpy": LAYERED + '\nlabel start:\n    weather6 雨 0.5\n    return\n',
            "zz_probe.rpy": PROBE,
        },
    },
    "D5_cross_file_same_tag": {
        "note": "★★ 跨文件同 tag：两个文件各写一个 layeredimage lan。"
                "观察是「报错」「后者赢」还是「合并」——工具得知道该用哪个",
        "files": {
            "a1.rpy": 'layeredimage lan:\n    group g1:\n'
                      '        attribute x default:\n            Solid("#0f0")\n',
            "a2.rpy": 'layeredimage lan:\n    group g2:\n'
                      '        attribute y:\n            Solid("#00f")\n',
            "b_script.rpy": 'label start:\n    show lan x\n    return\n',
            "zz_probe.rpy": PROBE,
        },
    },
    "D5b_cross_file_plain_image": {
        "note": "★★ 跨文件同 tag（普通 image 版）：两个文件都 `image lan x = …`",
        "files": {
            "a1.rpy": 'image lan x = Solid("#0f0")\n',
            "a2.rpy": 'image lan x = Solid("#00f")\n',
            "b_script.rpy": 'label start:\n    show lan x\n    return\n',
            "zz_probe.rpy": PROBE,
        },
    },
    "D6_probe_residue": {
        "note": "★★ 探针副作用 + 半编译：跑一次 → **只删探针 .rpy、留下 .rpyc** → 再跑一次",
        "inspect_residue": True,
        "files": {
            "a_script.rpy": LAYERED + '\nlabel start:\n    show lan clothes2 happy\n    return\n',
            "zz_probe.rpy": PROBE,
        },
    },
    "D7a_test_headless": {
        "note": "★★ 只问一件事：`test` 命令在 headless(dummy) 下能不能跑（不加 assert）",
        "cmd": ["test", "sg_suite"],
        "files": {
            "b_script.rpy": LAYERED + '''
label start:
    "hi"
    return

testsuite sg_suite:
    testcase sg_t:
        run Jump("start")
''',
            "zz_probe.rpy": PROBE,
        },
    },
    "D7b_behavior_assert": {
        "note": "★★ 行为级判别：覆盖 pause 的 execute 置标志，用 assert 读。"
                "若 assert 通过 → 覆盖的 execute **真的执行了**（=替换），且 test 可用",
        "cmd": ["test", "sg_suite"],
        "files": {
            "aa_cds.rpy": 'python early:\n'
                          '    def _p(lex):\n        return lex.rest()\n'
                          '    def _ex(o):\n        store.sg_hit = True\n'
                          '    renpy.register_statement("pause", parse=_p, execute=_ex)\n',
            "b_script.rpy": LAYERED + '''
label start:
    pause 1.0
    return

testsuite sg_suite:
    testcase sg_t:
        run Jump("start")
        assert sg_hit == True
''',
            "zz_probe.rpy": PROBE,
        },
    },
}


def find_sdk(which: str = "default") -> str:
    if which == "fork":
        if os.path.isfile(os.path.join(SDK_FORK, "renpy.py")):
            return SDK_FORK
        raise SystemExit(f"fork SDK 不在 {SDK_FORK}（先跑 build_fork_sdk）")
    for c in SDK_CANDIDATES:
        if c and os.path.isfile(os.path.join(c, "renpy.py")):
            return c
    raise SystemExit("找不到 Ren'Py SDK；设 STORYGRAPH_RENPY_SDK=<sdk 目录>")


def make_project(root: str, files: dict, sdk: str) -> str:
    proj = os.path.join(root, "proj")
    gamedir = os.path.join(proj, "game")
    os.makedirs(gamedir, exist_ok=True)
    for name, text in files.items():
        with open(os.path.join(gamedir, name), "w", encoding="utf-8") as f:
            f.write(text)
    return proj


def run_cmd(sdk: str, proj: str, cmd_tail=None) -> tuple:
    """cmd_tail 默认 ["lint", "--error-code"]；可换成 ["test", "suitename"] 等。"""
    py = os.path.join(sdk, "lib", "py3-windows-x86_64", "python.exe")
    if not os.path.isfile(py):
        py = sys.executable
    env = dict(os.environ)
    env["SDL_VIDEODRIVER"] = "dummy"
    env["SDL_AUDIODRIVER"] = "dummy"
    cmd = [py, os.path.join(sdk, "renpy.py"), proj] + list(cmd_tail or ["lint", "--error-code"])
    p = subprocess.run(cmd, cwd=sdk, env=env, capture_output=True, text=True,
                       errors="replace", timeout=600)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def residue_snapshot(proj: str) -> dict:
    g = os.path.join(proj, "game")
    out = {"rpy": [], "rpyc": [], "cache": []}
    for base, key, pat in ((g, "rpy", ".rpy"), (g, "rpyc", ".rpyc")):
        try:
            for fn in sorted(os.listdir(base)):
                if fn.endswith(pat):
                    out[key].append(fn)
        except Exception:
            pass
    c = os.path.join(g, "cache")
    if os.path.isdir(c):
        try:
            out["cache"] = sorted(os.listdir(c))[:12]
        except Exception:
            pass
    return out


def probe_residue_flow(sdk: str, proj: str) -> dict:
    """缺口：探针注入的副作用 + 半编译状态。
    步骤：①已有一次运行 → ②删探针 .rpy（保留 .rpyc）→ ③再跑一次 lint → ④记录。"""
    res = {"before": residue_snapshot(proj)}
    pg = os.path.join(proj, "game")
    for fn in os.listdir(pg):
        # ⚠ 只删 .rpy，**故意留下 .rpyc** —— 这才是"孤儿 rpyc"的场景
        if fn.startswith("zz_probe") and fn.endswith(".rpy"):
            try:
                os.remove(os.path.join(pg, fn))
                res.setdefault("removed", []).append(fn)
            except Exception as e:
                res.setdefault("remove_error", []).append(f"{fn}: {e!r}")
    res["after_remove"] = residue_snapshot(proj)
    rc, out = run_cmd(sdk, proj)
    res["second_lint_exit"] = rc
    res["second_lint_lines"] = pick_lines(out)[:6]
    res["after_second"] = residue_snapshot(proj)
    return res


INTERESTING = ("expected statement", "is not an image", "is not loadable",
               "ambiguous", "not defined", "SG-LINT", "SyntaxError", "error")


def pick_lines(out: str) -> list:
    keep = []
    for ln in out.splitlines():
        s = ln.strip()
        if not s:
            continue
        if s.startswith("File ") or ".rpy:" in s or any(k in s for k in INTERESTING):
            keep.append(s)
    # 去重、保序
    seen, res = set(), []
    for ln in keep:
        if ln not in seen:
            seen.add(ln)
            res.append(ln)
    return res[:14]


def facts_of(proj: str) -> dict:
    p = os.path.join(proj, "facts.json")
    if os.path.isfile(p):
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            return {"_read_error": repr(e)}
    e = os.path.join(proj, "facts_ERROR.txt")
    if os.path.isfile(e):
        with open(e, encoding="utf-8") as f:
            return {"_probe_error": f.read()[:200]}
    return {}


def main(argv) -> int:
    print(f"默认 SDK = {SDK_CANDIDATES[1]}")
    print(f"fork  SDK = {SDK_FORK} "
          f"({'存在' if os.path.isfile(os.path.join(SDK_FORK, 'renpy.py')) else '缺失'})\n")
    wanted = [a for a in argv[1:] if not a.startswith("-")]
    names = [n for n in CASES if (not wanted or any(w in n for w in wanted))]

    root = tempfile.mkdtemp(prefix="sg_hell_")
    rows = []
    for name in names:
        case = CASES[name]
        which = case.get("sdk", "default")
        try:
            sdk = find_sdk(which)
        except SystemExit as e:
            print("=" * 74)
            print(f"[{name}] 跳过：{e}")
            continue
        work = os.path.join(root, name)
        os.makedirs(work, exist_ok=True)
        proj = make_project(work, case["files"], sdk)
        cmd = case.get("cmd")
        try:
            rc, out = run_cmd(sdk, proj, cmd)
        except Exception as e:
            rc, out = -1, f"run failed: {e!r}"
        facts = facts_of(proj)
        lines = pick_lines(out)
        print("=" * 74)
        print(f"[{name}] ({which} SDK, cmd={' '.join(cmd or ['lint --error-code'])})")
        print(f"  {case['note']}")
        print(f"  exit = {rc}")
        for ln in lines:
            print(f"    {ln}")
        if facts:
            keys = {k: (len(v) if isinstance(v, list) else v)
                    for k, v in facts.items() if k != "lan_attrs"}
            print(f"  facts: {keys}")
            la = facts.get("lan_attrs")
            if isinstance(la, list):
                print(f"  lan_attrs({len(la)}): "
                      f"{[(r.get('g'), r.get('a')) for r in la][:10]}")
        else:
            print("  facts: (未产出)")

        if case.get("inspect_residue"):
            res = probe_residue_flow(sdk, proj)
            print(f"  残留 before      : {res['before']}")
            print(f"  删除 {res.get('removed')}")
            print(f"  残留 after_remove: {res['after_remove']}")
            print(f"  第二次 exit = {res['second_lint_exit']}")
            for ln in res["second_lint_lines"]:
                print(f"      {ln}")
            print(f"  残留 after_second: {res['after_second']}")

        rows.append((name, which, rc, bool(facts), len(lines)))

    print("\n" + "=" * 74)
    print("汇总")
    print(f"{'用例':<30}{'SDK':<9}{'exit':<7}{'facts':<8}{'报错行'}")
    for n, w, rc, f, k in rows:
        print(f"{n:<30}{w:<9}{rc:<7}{str(f):<8}{k}")
    print(f"\n临时目录（可查原始输出）: {root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
