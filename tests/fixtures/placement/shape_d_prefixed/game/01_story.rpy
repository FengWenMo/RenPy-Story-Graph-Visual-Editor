label start:
    "故事开始。"
    call intro
    call middle
    return

label intro:
    ernie "你好。"
    $ ernie_happiness += 1
    return

label middle:
    bert "我们到了。"
    $ bert_happiness += 1
    return
