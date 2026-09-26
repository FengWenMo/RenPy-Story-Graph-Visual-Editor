# Unity 接入说明

产物：
- `UnityDialogueData.json` —— 用 `JsonUtility.FromJson<DialogueData>(text)` 直接读。
  字段全部是标量或 `string[]`，没有嵌套字典（JsonUtility 不支持）。
- `story_ir.json` —— 引擎中立 IR（更完整，含 stage 快照与资源清单），
  需要自定义解析时用它。

数据规模：4 行演出 / 1 个 label /
2 个角色 / 文本 36 字。

## 建议的 C# 结构

```csharp
[Serializable] public class DialogueLine {
    public string lineId, label, kind, speakerId, speakerName, text,
                  condition, background, bgm, bgmAction, sfx, sfxAction,
                  voice, lineHash;
    public int seq;
    public string[] poses;
}
[Serializable] public class DialogueCharacter {
    public string id, displayName, color, imageTag, voiceTag;
}
[Serializable] public class DialogueAsset {
    public string id, type, path, tag, category, doc;
}
[Serializable] public class DialogueData {
    public string schema, project;
    public DialogueLine[] lines;
    public DialogueCharacter[] characters;
    public DialogueAsset[] assets;
}
```

## 两点约定

1. **不要自己叠加舞台状态**：`background` / `poses` / `bgm` 已经是算好的
   舞台快照，"这句台词时台上是谁、在哪"直接读，不要重新实现 scene/show/hide。
2. **lineId 是主键**：存档、埋点、配音对轨、翻译都指向它。不要用数组下标。
