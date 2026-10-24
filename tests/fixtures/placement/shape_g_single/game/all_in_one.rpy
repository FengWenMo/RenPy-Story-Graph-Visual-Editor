# 原型阶段：声明、剧情、跳转全在一个文件
define n = Character("旁白")
default hp = 100
default game_over = False

label start:
    n "你醒来了。"
    $ hp -= 10
    jump explore

label explore:
    n "前面有两条路。"
    menu:
        "进洞":
            jump cave
        "回头":
            jump back

label cave:
    n "洞里很黑。"
    if hp <= 0:
        $ game_over = True
        jump bad_end
    jump start

label back:
    n "你回到了原点。"
    return

label bad_end:
    n "你倒下了。"
    return
