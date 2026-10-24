# 官方模板：声明、剧情、跳转全在这个文件
define e = Character("Eileen", color="#c8ffc8")
define m = Character("Mary", color="#ffc8c8")
default points = 0
default seen_roof = False

label start:
    scene bg room
    e "早上好。"
    $ points += 1
    menu:
        "去屋顶":
            jump roof
        "留下":
            jump stay

label roof:
    scene bg roof
    e "风景真好。"
    $ seen_roof = True
    return

label stay:
    e "那我们继续吧。"
    return
