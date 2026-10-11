"""造一个**地狱项目**：故意堆满边界，用来攻击本工具。

用户 2026-10-11："造一个地狱项目！结合真实项目去攻击此工具"

## 为什么要它

用户自己的顾虑被现实验证过：**"我的这个真实项目可能不够真实"** ——
它 8GB、1913 段对白，但**注册表 0 条、CDS 一条没有**，
所以四层验证最容易出问题的边界（自定义写法、覆盖内置语句、计算出来的名字…）
在它身上**根本碰不到**。"跑通了"很可能只是"没碰到"。

## 造法：**借真实项目的规模 + 塞进合成边界**

· **外壳借真实项目**：把 `sr_general` 的 `.rpy` 轻量拷进来（真实的章节结构、
  真实的 `label`/`show`/`jump` 用法、真实的 1913 段对白）——
  这样工具面对的是**真规模**，不是玩具；
· **里面塞地狱**：下面 `HOT_FILES` 里的每一个文件都针对一个**具体边界**，
  并写明"攻击目标"与"期望工具怎么反应"。

## 纪律

· 生成物在 `.pip-tmp/hell/`（**临时区**，不进版本库 —— 它含真实项目内容）；
· 生成脚本本身**进版本库**（可复现）；
· **只读真实项目**，绝不改它。

    python tools/make_hell_project.py [真实项目根] [输出目录]
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

REAL = (Path(sys.argv[1]) if len(sys.argv) > 1
        else Path(r'D:\千连万理\RenpyTest\sr_general'))
OUT = (Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / '.pip-tmp' / 'hell')

# ---------------------------------------------------------------------------
#  地狱文件：一个文件 = 一次攻击
# ---------------------------------------------------------------------------

#: 每个条目的注释都写清 **攻击目标** 与 **期望工具怎么反应**。
HOT_FILES: dict = {}


def hot(name: str, body: str, why: str):
    HOT_FILES[f'game/hell_{name}.rpy'] = (body, why)


# ① 自定义写法（CDS）：工具完全不认识 —— 但要**如实报**、不能崩
hot('cds_unknown', '''# 攻击：CDS 注册的自定义写法，工具/引擎都不认识
label hell_cds_unknown:
    weather rain 强度=3
    return
''', '① 自定义写法：扫描要报出来、lint 要指出来、不能当成"没问题"')

# ② 与内置语句**同名**的自定义注册（覆盖内置 = 形状不保证，N5）
hot('cds_override', '''init python:
    renpy.register_statement("pause", parse=lambda l: l.eol())


# 攻击：用 CDS **覆盖内置** `pause`（覆盖 = 完全替换，不是叠加）
label hell_override:
    pause
    return
''', '② 覆盖内置语句：最高级警告（形状不保证）—— 而且这里 `pause` 已换语义')

# ③ 名字是**算出来**的（扫描器只认字面量，必然看不见 —— N1/C4）
hot('computed_name', '''# 攻击：语句名由变量拼出来 —— 任何扫描器都看不见
label hell_computed:
    $ _act = "wea" + "ther"
    $ renpy.call_in_new_context("hell_cds_unknown")
    jump expression "hell_" + "cds_unknown"
    return
''', '③ 计算出来的名字：规则 N1 —— 扫描**永不宣称"只有这些"**')

# ④ ATL 块冒号（【⑩】曾把 `heihei:` 当成属性，31 假阳性）
hot('atl_colon', '''image hell_lan = "images/lan.png"

label hell_atl:
    show hell_lan:
        xalign 0.5
        ease 1.0 yalign 0.3
    # ⚠ 这一行曾经被误报成属性 `heihei:`
    show hell_lan heihei:
        zoom 1.2
    hide hell_lan
    return
''', '④ ATL 块冒号：不许把 `heihei:` 当属性（曾经的 31 假阳性）')

# ⑤ `with` / `window` / `pause` 这类**内置关键字**（用户点名过）
hot('builtin_keywords', '''label hell_builtin:
    scene black with dissolve
    show hell_lan at center
    window show
    pause 0.5
    hide hell_lan with dissolve
    window hide
    return
''', '⑤ 内置关键字（with / window / at / pause）：**一个都不许**报成"不认识"')

# ⑥ 中文标点 / 全角空格 / CRLF / BOM 混进来
hot('encoding_zoo', 'label hell_encoding:\n'
                    '    lyj "全角逗号，全角句号。全角括号（）"\n'
                    '    lyj "全角空格\u3000在中间"\n'
                    '    # 制表符\t在注释里\n'
                    '    return\n',
    '⑥ 中文标点/全角空格/制表符：解析不能崩，也不能把它们当成语法')

# ⑦ 同名 label（引擎会报错，工具要能说清是哪儿）
hot('dup_label', '''label hell_cds_unknown:
    return
''', '⑦ 同名 label：引擎会报；工具要指出冲突位置（不是"采集失败"了事）')

# ⑧ 极长行 / 极深缩进 / 空文件 / 只有注释的文件
hot('long_line', 'label hell_long:\n    lyj "' + '很长的台词' * 400 + '"\n'
                 '    return\n',
    '⑧ 极长行：扫描/导出不能因为一行过长而截断或崩')
HOT_FILES['game/hell_empty.rpy'] = ('', '⑨ 空文件：不许当成"有内容但读不出来"')
HOT_FILES['game/hell_only_comment.rpy'] = (
    '# 只有注释\n# 什么也没有\n', '⑩ 只有注释：不许报成"未知写法"')

# ⑪ 多词语句（键是元组：('play','music')）+ 引擎内置
hot('multiword', '''label hell_multiword:
    play music "bgm/x.ogg" fadein 1.0
    play sound "se/y.ogg"
    stop music fadeout 0.5
    nvl clear
    return
''', '⑪ 多词语句：注册表里的键是**元组**，渲染成写法要用空格接')

# ⑫ 缩进深浅不一致 / 行尾注释
hot('messy_indent', '''label hell_messy:
  lyj "浅缩进"
    lyj "深缩进"   # 行尾注释
    if True:      # 条件后面带注释
        pass
    return
''', '⑫ 缩进不一致 + 行尾注释：解析要能吃下（真实项目就长这样）')

# ⑬ 工具"认识的"但**参数名没声明**的写法（裸参数）
hot('bare_param', '''init python:
    renpy.register_statement("swim_suit", parse=lambda l: l.eol())


label hell_bare:
    swim_suit lyj intensity=3 无法识别的参数
    return
''', '⑬ 裸参数：引擎认识这个写法，但参数没声明 —— 要报"没声明的参数"')

for _rel, (_body, _why) in HOT_FILES.items():
    pass


def main() -> int:
    if not (REAL / 'game').is_dir():
        print(f'真实项目不在本机：{REAL}（只造合成边界，不借它的规模）')
    else:
        print(f'借真实项目的规模：{REAL}')
    if OUT.exists():
        shutil.rmtree(OUT, ignore_errors=True)
    (OUT / 'game' / 'images').mkdir(parents=True, exist_ok=True)

    # ① 先拷真实项目的**语法文件**（借规模）
    copied = 0
    if (REAL / 'game').is_dir():
        for src in sorted(REAL.rglob('*.rpy')):
            rel = src.relative_to(REAL)
            try:
                if src.stat().st_size > 2 * 1024 * 1024:
                    continue
                dest = OUT / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)
                copied += 1
            except OSError as e:                    # noqa: BLE001
                print(f'  跳过 {rel}：{e}')
    # 真实项目里那些会让引擎编译不过的文件，**不借**（否则地狱文件全被挡住）
    for bad in ('game/tmp_check_probe.rpy', 'errors.txt'):
        p = OUT / bad
        if p.is_file():
            p.unlink()
            print(f'  已剔除（它自己有 parse error，会挡住后面所有组）：{bad}')

    # ⚠⚠ **必须一起拷人物表**（真机踩到）：`discover_undeclared` 靠它判断
    #   "这一行是台词"（`lyj "台词"` 里的 `lyj` 是角色）—— 少了它，
    #   攻击会报出"把人物当成未知写法"这个**假缺陷**（我第一版就这样，
    #   差一点就去改工具的判定逻辑）。**地狱项目造假 → 攻击结论也假。**
    tables = REAL / 'game' / 'asset_tables'
    if tables.is_dir():
        shutil.copytree(tables, OUT / 'game' / 'asset_tables',
                        dirs_exist_ok=True)
        n = sum(1 for _ in (OUT / 'game' / 'asset_tables').rglob('*.xlsx'))
        print(f'  拷了人物表/资源表：{n} 个 xlsx（**不能省** —— 少了它攻击会误报）')

    # ② 再塞地狱
    for rel, (body, _why) in HOT_FILES.items():
        fp = OUT / rel
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(body, encoding='utf-8')
    print(f'拷了 {copied} 个真实 .rpy，塞了 {len(HOT_FILES)} 个地狱文件 → {OUT}')
    print('地狱文件一览（每个都写明攻击什么）：')
    for rel in sorted(HOT_FILES):
        print(f'  · {rel}')
        print(f'      {HOT_FILES[rel][1]}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
