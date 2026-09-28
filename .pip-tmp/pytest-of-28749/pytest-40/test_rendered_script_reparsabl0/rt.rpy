# 由剧本表自动生成，请勿在此文件手工改动结构

label chapter1_2:
    #1.2蓝之初见
    "学姐关上门，把灯打开。"
    # bgm13 bg9.5
    scene bg bg9_5 at bgfit
    stop music
    play music "bgm/无法言说的事情…….mp3" fadein 1.0
    "这里是一间密室。"
    jc "没想到咱学校还有这种高科技的地方……"
    # 表情A6，距离近
    hide ye
    show ye happy at f11
    $ renpy.transition(Dissolve(0.1), layer="master")
    yhj "这里算是青蓝的秘密实验室。"
    # 表情A11
    show ye gratified
    $ renpy.transition(Dissolve(0.1), layer="master")
    yhj "开机了。"
    # 学姐立绘消失
    hide ye
    "学姐长舒一口气。"
    play music "bgm/夜晚，断电之后…….mp3" fadein 1.0
    hidden_name "嗨呀，痒，好痒呀。"
    l "我是……{nw}"
    extend "住在青蓝机器里的意识体。"
    show cg cg02_2 at cg38402160 behind ye
    hide lan with dissolve
    hide ye with dissolve
    $ persistent.lock_lst["cg02.2"] = True
    pause 2.0
    return
