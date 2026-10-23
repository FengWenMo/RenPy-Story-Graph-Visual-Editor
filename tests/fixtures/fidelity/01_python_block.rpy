# 它测什么：python 块 + 行尾内联注释 —— 两次历史事故（丢 18 行、fadeout 1.0 参数被吃）
# 就发生在这一带。块体必须逐字回去，工具**不进块内部**。
# 来源：结构取自真机（init python / 块内注释 / $ 赋值 + 行尾注释），台词全部替换。
define e = Character("台词A", color="#c8ffc8", image="eileen")

init python:
    # 顶层块：给存档一个默认值（工具不该改写块里的任何一行）
    persistent_seen = set()
    DEFAULT_VOLUME = 0.8

label ch_python:
    scene bg classroom with dissolve
    show eileen happy at left   # 行尾内联注释：位置必须保住
    python:
        # 块内注释也不能丢
        score = 3
        score += 1
        renpy.notify("台词B")   # 块里的行尾注释
    e "台词C。"
    $ persistent.lock_lst = ["a", "b"]   # 脚本层赋值 + 注释
    return
