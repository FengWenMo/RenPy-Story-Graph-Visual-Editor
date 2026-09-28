label t:
    scene bg room
    show e a
    e "one"
    show e b at left
    e "two"
    hide e
    show e c at right
    hide e with dissolve
    show f x at left
    e "four"
    play music theme fadein 1.0
    stop music
    voice v_001
    return
