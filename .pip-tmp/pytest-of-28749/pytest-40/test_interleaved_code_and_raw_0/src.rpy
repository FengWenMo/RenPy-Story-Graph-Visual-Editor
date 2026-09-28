label chapter1_4:
    # bg1.2
    scene bg bg1_2
    "第二天一早起来。"
    play music "bgm/night.mp3" fadein 1.0
    "5条新消息"
    hide screen msg_dialogue
    $ msg_add('L|叶怀瑾|hello')
    show screen msg_dialogue
    pause
    hide screen msg_dialogue
    $ msg_add('L|叶怀瑾|world')
    show screen msg_dialogue
    pause
    return
