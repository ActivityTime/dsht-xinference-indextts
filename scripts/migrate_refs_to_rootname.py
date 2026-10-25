# -*- coding: utf-8 -*-
"""一次性数据迁移：把配置里的 int 索引引用迁移为库名（抗库源重排）。

- emotion_mappings.json: entries[].items[] 的 {root:int} → {root_name:库名}
- voice_groups.json:     groups[].members[] 的 {root:int} → {root_name:库名}
- emotion_map.json:      rules[].audio 的 "索引|rel" → "库名|rel"

只读库配置（console/library.json）取库名；索引失效的项保持原样并报告（不猜）。
"""
import json
import io
import sys
from pathlib import Path

CONSOLE = Path(__file__).resolve().parent.parent / "console"
LIB_FILE = CONSOLE / "library.json"


def load_roots():
    try:
        cfg = json.loads(LIB_FILE.read_text(encoding="utf-8"))
        roots = cfg.get("roots")
        if isinstance(roots, list) and roots:
            return roots
    except (OSError, json.JSONDecodeError):
        pass
    return []


ROOTS = load_roots()


def name_of(idx):
    try:
        i = int(idx)
    except (TypeError, ValueError):
        return None
    if 0 <= i < len(ROOTS):
        return ROOTS[i].get("name")
    return None


def migrate_item(it, where, report):
    if it.get("root_name"):
        it.pop("root", None)
        return
    if "root" not in it:
        return
    nm = name_of(it.get("root"))
    if nm:
        it["root_name"] = nm
        it.pop("root", None)
        report.append("  %s: root=%s → root_name=%s" % (where, it.get("root"), nm))
    else:
        report.append("  %s: root=%s 索引失效，保持原样" % (where, it.get("root")))


def migrate_piped(val, where, report):
    s = str(val or "").strip()
    if not s or "|" not in s:
        return s
    ref, _, rel = s.partition("|")
    ref = ref.strip()
    if not ref.isdigit():
        return s
    nm = name_of(ref)
    if nm:
        report.append("  %s: %s → %s|%s" % (where, s, nm, rel))
        return f"{nm}|{rel}"
    report.append("  %s: %s 索引失效，保持原样" % (where, s))
    return s


def do_file(fname, kind):
    path = CONSOLE / fname
    if not path.is_file():
        print("[skip] %s 不存在" % fname)
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print("[!] %s 读取失败: %s" % (fname, e))
        return
    report = []
    if kind == "emappings":
        for e in data.get("entries", []):
            for i, it in enumerate(e.get("items", [])):
                if it.get("type") == "audio":
                    migrate_item(it, "%s/%s#%d" % (fname, e.get("group", "?"), i), report)
    elif kind == "groups":
        for g in data.get("groups", []):
            for i, m in enumerate(g.get("members", [])):
                migrate_item(m, "%s/%s#%d" % (fname, g.get("name", "?"), i), report)
    elif kind == "emotions":
        for r in data.get("rules", []):
            if r.get("audio"):
                r["audio"] = migrate_piped(r.get("audio"), "%s/%s" % (fname, r.get("tag", "?")), report)
    elif kind == "ttsform":
        for k in ("ref_lib", "emo_audio_lib"):
            if data.get(k):
                data[k] = migrate_piped(data.get(k), "%s/%s" % (fname, k), report)
    if report:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        print("[迁移] %s" % fname)
        for line in report:
            print(line)
    else:
        print("[ok] %s 无需迁移" % fname)


if __name__ == "__main__":
    print("库源: %s" % ([r.get("name") for r in ROOTS]))
    do_file("emotion_mappings.json", "emappings")
    do_file("voice_groups.json", "groups")
    do_file("emotion_map.json", "emotions")
    do_file("tts_defaults.json", "ttsform")
    do_file("adapter_defaults.json", "adapterform")
    print("完成")
