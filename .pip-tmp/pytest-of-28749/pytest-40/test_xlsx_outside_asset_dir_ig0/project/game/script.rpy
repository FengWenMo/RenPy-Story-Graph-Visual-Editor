define e = Character("Eileen", color="#c8ffc8")
label start:
    scene bg classroom
    play music bgm_theme
    show bg street at leftish
    e "Hello."
    jump ending
label ending:
    return
