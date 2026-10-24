default chapter1_done = False
define c1 = Character("角色一")

label ch1:
    c1 "第一章开始。"
    $ chapter1_done = True
    jump ch2
