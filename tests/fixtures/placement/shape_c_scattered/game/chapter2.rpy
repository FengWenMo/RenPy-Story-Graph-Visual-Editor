default chapter2_done = False
default chapter2_score = 0
define c2 = Character("角色二")

label ch2:
    c2 "第二章开始。"
    $ chapter2_score += 1
    jump ch3
