#!/usr/bin/env python3
"""make_voice_candidates.py — ページで「声の候補を作る」が押された行に、声の候補を 3 つずつ作る。

ページの依頼（artifact の db）:
  reviews/<id>/voices/<n:03d>   {n, req, text, speaker, status: requested|ready|failed, candidates[], pick, at}
    text / speaker はページが決めた「読ませる文」と「話し手」（訂正中ならその新しい台詞・読みの直し・話し手）

ArtifactData の list（collection "reviews/<id>/voices"、out_dir 付き）で落としたフォルダかファイルを渡す。
status が requested の文書だけを作る。書くもの:
  <site>/audio/<id>/cand/<n:03d>-<依頼の時刻>-<k>.mp3   候補の声（k = 1..takes）
  <site>/voice_updates.json                              db へ書き戻す batch の writes（if_version は自分で足す）

  python3 .claude/skills/daihon-mirune/scripts/make_voice_candidates.py \\
      output/daihon-mirune/requests/reviews/<id>/voices --episode <id> [--takes 3]

候補は同じ文を同じ声で何度か読ませたもの（毎回少しずつ間や抑揚が違う）。手順は SKILL.md の「声の候補」。
"""
import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import tts as tts_mod  # noqa: E402  (scripts/ が sys.path の先頭に入る)

SKILL_DIR = Path(__file__).resolve().parent.parent


def requests_in(node):
    """ArtifactData の返し方に依らず、声の依頼の文書だけを拾う。"""
    if isinstance(node, dict):
        if isinstance(node.get("n"), int) and "req" in node and "status" in node:
            yield node
            return
        for v in node.values():
            yield from requests_in(v)
    elif isinstance(node, list):
        for v in node:
            yield from requests_in(v)


def reading(line: dict, doc: dict) -> str:
    """ページの voiceRequest と同じ規則で、訂正した行に読ませる文を決める。"""
    original = line.get("text", "")
    text = (doc.get("text") or "").strip()
    out = text if text and text != original else (line.get("tts") or original)
    yomi = (doc.get("yomi") or "").strip()
    if yomi:
        if re.search(r"→|->|⇒", yomi):
            for part in re.split(r"[、,，;；\n]", yomi):
                m = re.match(r"^\s*(.+?)\s*(?:→|->|⇒)\s*(.+?)\s*$", part)
                if m:
                    out = out.replace(m.group(1), m.group(2))
        else:
            out = yomi
    return out


def implied_requests(lines_path: Path, data_path: Path, reqs: dict) -> list:
    """保存済みの訂正のうち、声が変わる（話し手・台詞・読み）のに依頼が無い・古い行を、依頼として足す。"""
    lines = {l["n"]: l for l in json.loads(data_path.read_text(encoding="utf-8")).get("lines", [])}
    docs = load(lines_path)
    docs = docs if isinstance(docs, list) else [docs]
    now = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    out = []
    for d in docs:
        n, line = d.get("n"), lines.get(d.get("n"))
        if not isinstance(n, int) or not line or d.get("st") != "fix":
            continue
        sp = d.get("sp") or line.get("sp", "")
        text = reading(line, d)
        if sp == line.get("sp") and text == (line.get("tts") or line.get("text")):
            continue
        cur = reqs.get(n)
        if cur and cur.get("text") == text and cur.get("speaker") == sp and cur.get("status") in ("requested", "ready"):
            continue
        out.append({"n": n, "req": now, "text": text, "speaker": sp, "status": "requested", "candidates": [],
                    "for_req": "", "pick": 0, "at": now, "_new": cur is None})
    return out


def load(path: Path):
    if path.is_dir():
        return [json.loads(f.read_text(encoding="utf-8")) for f in sorted(path.glob("*.json"))]
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("requests", help="collection reviews/<id>/voices の list 結果（ファイルか、1 文書 1 ファイルのフォルダ）")
    ap.add_argument("--episode", required=True, help="ページ上の回 id")
    ap.add_argument("--site", default=str(SKILL_DIR.parent.parent.parent / "output" / "daihon-mirune"))
    ap.add_argument("--config", default=str(SKILL_DIR / "config.json"))
    ap.add_argument("--speakers", help="collection speakers の list 結果。ページの「話し手」タブで登録した声 ID を config.json より優先する")
    ap.add_argument("--lines", help="collection reviews/<id>/lines の list 結果。声が変わる訂正には、ボタンが押されていなくても候補を作る")
    ap.add_argument("--data", help="回の data/<id>.json（--lines の時。既定は <site>/data/<id>.json）")
    ap.add_argument("--takes", type=int, default=3, help="1 行あたりの候補の数（既定 3）")
    ap.add_argument("--all", action="store_true", help="requested 以外（作り済み・失敗）も作り直す")
    a = ap.parse_args()

    cfg_path = Path(a.config)
    cfg = json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.exists() else {}
    voice_of = {str(s.get("id", "")): s.get("voice") or "" for s in cfg.get("speakers", [])}
    if a.speakers:
        # ページで登録した直後の話し手も、config.json を写す前にその声で作れるように
        from sync_speakers import load as load_reg, merge, registry_docs
        live = {"speakers": [dict(s) for s in cfg.get("speakers", [])]}
        merge(live, list(registry_docs(load_reg(Path(a.speakers)))))
        voice_of = {str(s.get("id", "")): s.get("voice") or "" for s in live["speakers"]}
    try:
        synth = tts_mod.make(cfg.get("tts", {}))
    except tts_mod.TTSError as e:
        print(f"音声生成の準備ができない: {e}", file=sys.stderr)
        return 1
    if synth is None:
        print("config.json の tts が provider: none なので、声の候補は作れない", file=sys.stderr)
        return 2

    req_path = Path(a.requests)
    reqs = {d["n"]: d for d in requests_in(load(req_path))} if req_path.exists() else {}
    todo = [reqs[n] for n in sorted(reqs) if a.all or reqs[n].get("status") == "requested"]
    site = Path(a.site).resolve()
    if a.lines:
        data = Path(a.data) if a.data else site / "data" / f"{a.episode}.json"
        for d in implied_requests(Path(a.lines), data, reqs):
            todo = [t for t in todo if t["n"] != d["n"]] + [d]
            print(f"  {d['n']:03d} 訂正で声が変わるので依頼を足した（{d['speaker']}「{d['text']}」）")
        todo.sort(key=lambda d: d["n"])
    if not todo:
        print("作る依頼が無い（status が requested の文書が 0 件）")
        return 0

    writes, files, failed, created = [], [], 0, []
    for d in todo:
        n, text, speaker = d["n"], (d.get("text") or "").strip(), d.get("speaker", "")
        tag = re.sub(r"\D", "", str(d.get("req", "")))[:17] or "0"
        cands, err = [], ""
        if not text:
            err = "読ませる文が空だった"
        for k in range(1, a.takes + 1):
            if err:
                break
            rel = f"audio/{a.episode}/cand/{n:03d}-{tag}-{k}.mp3"
            try:
                synth.synth(text, voice_of.get(speaker, ""), site / rel)
            except tts_mod.TTSError as e:
                err = str(e)
                break
            cands.append({"k": k, "file": rel})
            files.append(rel)
        if speaker and speaker not in voice_of:
            print(f"  注意: {n:03d} の話し手 {speaker} は config.json に未登録（サービスの既定の声で作った）", file=sys.stderr)
        if err:
            failed += 1
            print(f"  {n:03d} 失敗: {err}", file=sys.stderr)
            data = {"status": "failed", "error": err[:300], "for_req": d.get("req", "")}
        else:
            print(f"  {n:03d} {speaker}「{text}」 候補 {len(cands)} つ")
            # for_req: どの依頼に答えた候補か。ページは今の依頼と違う候補を出さない（作っている間に依頼し直された時）
            data = {"status": "ready", "candidates": cands, "for_req": d.get("req", ""), "pick": 0}
        if "_new" in d:
            # Claude が足した依頼: ページの依頼と同じ形の文書ごと書く（既存の文書なら if_version が要る）
            data = {k: v for k, v in d.items() if k != "_new"} | data
            if d.get("_new"):
                created.append(f"{n:03d}")
            writes.append({"op": "set", "collection": f"reviews/{a.episode}/voices", "doc_id": f"{n:03d}", "data": data})
        else:
            writes.append({"op": "update", "collection": f"reviews/{a.episode}/voices", "doc_id": f"{n:03d}", "data": data})

    out = site / "voice_updates.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"writes": writes, "files": files, "created": created}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"書き出した: {out}")
    print(f"  {synth.describe()} で {len(todo) - failed} 行 × {a.takes} 候補（失敗 {failed} 行）")
    print("  次: files を Artifact publish で足し、writes に list で見た version を if_version として付けて ArtifactData batch"
          + (f"（新しく作る文書 {', '.join(created)} は if_version なし）" if created else ""))
    return 1 if failed and failed == len(todo) else 0


if __name__ == "__main__":
    sys.exit(main())
