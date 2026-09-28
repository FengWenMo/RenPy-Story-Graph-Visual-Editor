# 由剧本表自动生成，请勿在此文件手工改动结构

label ch02:
    scene bg_night
    show eileen_happy at left
    play music bgm_night
    e "你来了。"
    play sound sfx_door
    voice v_eileen_01
    m "门没锁。"
    "进去"
    queue sound sfx_footstep
    jump inner
    "离开"
    "他转身走了。"
    return
