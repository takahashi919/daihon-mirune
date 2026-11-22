#!/usr/bin/env python3
"""sync_speakers.py — ページの「話し手」タブで登録・変更・削除された話し手を config.json に写す。

ページの登録先（artifact の db）:
  speakers/<id>   {id, name, color, voice, at}（削除は {id, deleted: true, at}）

ArtifactData の list（collection "speakers"、out_dir 付き）で落としたフォルダかファイルを渡す。
config.json の tts はそのまま。speakers は db の内容で足す・上書きする・消す（db に無い人はそのまま残す）。
次の build_check_page.py の回からも、同じ話し手が出るようにするため。

  python3 .claude/skills/daihon-mirune/scripts/sync_speakers.py output/daihon-mirune/results/speakers [--config config.json] [--dry-run]
"""
import argparse
import json
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
FIELDS = ("name", "color", "voice")


def registry_docs(node):
    if isinstance(node, dict):
        if isinstance(node.get("id"), str) and ("deleted" in node or "name" in node or "voice" in node):
            yield node
            return
        for v in node.values():
            yield from registry_docs(v)
    elif isinstance(node, list):
        for v in node:
            yield from registry_docs(v)


def load(path: Path):
    if path.is_dir():
        return [json.loads(f.read_text(encoding="utf-8")) for f in sorted(path.glob("*.json"))]
    return json.loads(path.read_text(encoding="utf-8"))


def merge(cfg: dict, docs) -> list:
    """cfg["speakers"] を docs で書き換え、変わった点を文で返す。"""
    speakers = cfg.setdefault("speakers", [])
    by_id = {s.get("id"): s for s in speakers}
    changes = []
    for d in sorted(docs, key=lambda d: d.get("at", "")):
        sid = d["id"]
        if d.get("deleted"):
            if sid in by_id:
                speakers.remove(by_id.pop(sid))
                changes.append(f"消す  {sid}")
            continue
        new = {"id": sid, **{k: (d.get(k) or "") for k in FIELDS}}
        new["name"] = new["name"] or sid
        cur = by_id.get(sid)
        if cur is None:
            speakers.append(new)
            by_id[sid] = new
            changes.append(f"足す  {sid}（{new['name']}）")
        elif any((cur.get(k) or "") != new[k] for k in FIELDS):
            diff = ", ".join(f"{k}: {cur.get(k) or '—'} → {new[k] or '—'}" for k in FIELDS if (cur.get(k) or "") != new[k])
            cur.update(new)
            changes.append(f"直す  {sid}（{diff}）")
    return changes


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("registry", help="collection speakers の list 結果（ファイルか、1 文書 1 ファイルのフォルダ）")
    ap.add_argument("--config", default=str(SKILL_DIR / "config.json"))
    ap.add_argument("--dry-run", action="store_true", help="書かずに変わる点だけ出す")
    a = ap.parse_args()

    cfg_path = Path(a.config)
    cfg = json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.exists() else {"tts": {}, "speakers": []}
    changes = merge(cfg, list(registry_docs(load(Path(a.registry)))))
    for c in changes:
        print("  " + c)
    if not changes:
        print("config.json は db の話し手と同じ（変更なし）")
        return 0
    if a.dry_run:
        print(f"（--dry-run なので書いていない: {len(changes)} 件）")
        return 0
    cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"書いた: {cfg_path}（{len(changes)} 件）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
