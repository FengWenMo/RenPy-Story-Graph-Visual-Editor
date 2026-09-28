define e = Character("Eileen", color="#c8ffc8")
define m = Character("Mary")

label start:
    $ love = 0
    e "第一条"
    menu:
        "选项A":
            jump route_a
        "选项B" if love > 1:
            jump route_b

label route_a:
    $ love += 1
    jump ending

label route_b:
    m "另一条"
    jump ending

label ending:
    e "结束"
    return
