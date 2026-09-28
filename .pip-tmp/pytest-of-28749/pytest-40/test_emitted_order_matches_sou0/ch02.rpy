define e = Character("Eileen")
define m = Character("Mary")

label ch02:
    scene bg night
    show eileen happy at left
    play music bgm_night
    e "你来了。"
    play sound sfx_door
    voice v_eileen_01
    m "门没锁。"
    queue sound sfx_footstep
    menu:
        "进去":
            jump inner
        "离开":
            "他转身走了。"
    return
