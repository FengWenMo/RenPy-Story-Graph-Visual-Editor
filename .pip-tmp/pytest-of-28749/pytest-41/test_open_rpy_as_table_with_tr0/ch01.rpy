define e = Character("Eileen", color="#c8ffc8", image="eileen")

# 靠左站
transform leftish:
    xpos 0.2

label ch01:
    scene bg classroom
    play music bgm_theme
    show eileen happy at leftish
    e "早上好。"
    return
