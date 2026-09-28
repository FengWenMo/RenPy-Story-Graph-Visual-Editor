define e = Character("Eileen")

label start:
    $ love = 0
    if love > 5:
        $ flag = "high"
        jump good
    elif love > 2:
        call mid
    else:
        jump bad

label choice:
    menu:
        "去左边":
            jump left
        "去右边" if money > 10:
            call right
    return

label good:
    e "好结局。"
    return

label mid:
    return

label bad:
    return

label left:
    return

label right:
    return
