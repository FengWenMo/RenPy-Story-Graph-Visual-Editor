// StoryIr.cs —— `UnityDialogueData.json` 的参考 C# 结构（2026-10-19 第一片）
//
// 用途：让 Unity 侧 `JsonUtility.FromJson<DialogueData>(text)` 直接读工具导出的
// JSON。**这不是播放器**：条件求值、菜单 UI、立绘合成仍由 Unity 项目实现。
//
// 导出命令（在故事库根目录）：
//   python cli.py build <项目根> --engine unity --out <输出目录>
// 产物：UnityDialogueData.json / story_ir.json / UNITY_IMPORT.md
//
// 字段与 `storygraph/pipeline/engine_exporters.py::to_unity` 一一对应；
// 改字段时两边一起改（`tests/test_unity_timeline.py` 守着 timeline/structure）。

using System;

namespace StoryGraph
{
    [Serializable]
    public class TimelineEvent
    {
        public string op;          // scene/show/hide/with/music/sound/voice/at/transform
        public string target;      // 图像名 / 音频名 / 转场名
        public string at;          // show 的位置或变换
        public string clause;      // show 的完整修饰子句（含 behind/as 等）
        public string transition;  // 挂在 show/hide 上的转场
        public string action;      // music/sound 的 play/queue/stop
        public string @params;     // 原样参数串（JSON 字段名就是 params；
                                   // C# 里 params 是关键字，用 @ 转义）
        public int order;          // 同一条 line 内按它排序
    }

    [Serializable]
    public class DialogueLine
    {
        public string lineId;
        public string label;
        public int seq;
        public string kind;
        public string speakerId;
        public string speakerName;
        public string text;
        public string condition;       // 累积条件；Unity 侧自己求值
        public string background;
        public string[] poses;
        public string bgm;
        public string bgmAction;
        public string sfx;
        public string sfxAction;
        public string voice;
        public string nest;            // 原始块路径（menu:1>B）
        public string structurePath;   // 同 nest（给不认 nest 字段的版本）
        public string structureKind;   // if/menu/loop/option/空
        public bool structureKnown;    // false = 老表不知道，别当顶层
        public TimelineEvent[] timeline;
        public string transition;      // 整条 line 的 with
        public string[] targets;       // jump/call 的 label 接线
        public string lineHash;
    }

    [Serializable]
    public class DialogueCharacter
    {
        public string id;
        public string displayName;
        public string color;
        public string imageTag;
        public string voiceTag;
    }

    [Serializable]
    public class DialogueAsset
    {
        public string id;
        public string type;
        public string path;
        public string tag;
        public string category;
        public string doc;
    }

    [Serializable]
    public class DialogueData
    {
        public string schema;
        public string project;
        public DialogueLine[] lines;
        public DialogueCharacter[] characters;
        public DialogueAsset[] assets;
    }
}
