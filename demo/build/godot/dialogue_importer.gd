# dialogue_importer.gd —— 读 GodotDialogueData.json（由编辑器导出）
# 用法：把本文件与 GodotDialogueData.json 放进项目，附到一个 Node 上即可。
#
# 同目录下还有一份 `story_ir.json`（引擎中立 IR）：字段更全（含 stage 快照、
# 资源清单、DSL 原文），需要自定义解析时用它。本加载器读的是**扁平适配版**。
extends Node

const IR_PATH := "res://GodotDialogueData.json"

var lines_by_id: Dictionary = {}
var labels: Dictionary = {}
var characters: Dictionary = {}

func _ready() -> void:
    var f := FileAccess.open(IR_PATH, FileAccess.READ)
    assert(f != null, "找不到 GodotDialogueData.json")
    var data: Dictionary = JSON.parse_string(f.get_as_text())
    for c in data.get("characters", []):
        characters[c["id"]] = c
    for l in data.get("lines", []):
        lines_by_id[l["line_id"]] = l
        if not labels.has(l["label"]):
            labels[l["label"]] = []
        labels[l["label"]].append(l["line_id"])
    print("载入 %d 行 / %d 个 label" % [lines_by_id.size(), labels.size()])

# 按 line_id 取一条演出数据；stage 里已经是"算好的舞台状态"，
# 不需要再自己叠加 scene/show/hide。
func get_line(line_id: String) -> Dictionary:
    return lines_by_id.get(line_id, {})

func label_lines(label: String) -> Array:
    return labels.get(label, [])
