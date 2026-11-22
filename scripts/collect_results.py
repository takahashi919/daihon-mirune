#!/usr/bin/env python3
"""collect_results.py — 台詞チェック帳に保存された結果を、反映用の JSON 1 つにまとめる。

ページの保存先（artifact の db）:
  reviews/<id>/lines/<n:03d>   行ごと {n, st: ok|fix|del, text, yomi, memo, adds[], original, speaker, at}
  reviews/<id>                 全体コメント {general, title, at}
  reviews/<id>/voices/<n:03d>  声の候補 {n, req, text, speaker, status, candidates[{k, file}], for_req, pick}

ArtifactData の list（collection "reviews/<id>/lines"）と get（"reviews/<id>"）の結果をファイルに落として渡す。
out_dir を付けて読んだ時の「1 行 1 ファイル」のフォルダもそのまま渡せる。
出力はページの「機械用」と同じ形 {"episode", "edits": [...], "general"}。apply_review.py の --review に渡す。

  python3 .claude/skills/daihon-mirune/scripts/collect_results.py <out_dir>/reviews/<id>/lines \\
      --general <out_dir>/reviews/<id>.json \\
      --episode demo-v2 -o review_20261031.json
"""
import argparse
import json
import sys
from pathlib import Path


def docs_in(node):
    """ArtifactData の返し方（配列・{documents:[…]}・{data:{…}} など）に依らず、行の文書だけを拾う。"""
    if isinstance(node, dict):
        if isinstance(node.get("n"), int) and "st" in node:
            yield node
            return
        for v in node.values():
            yield from docs_in(v)
    elif isinstance(node, list):
        for v in node:
            yield from docs_in(v)


def voice_docs(node):
    if isinstance(node, dict):
        if isinstance(node.get("n"), int) and "req" in node:
            yield node
            return
        for v in node.values():
            yield from voice_docs(v)
    elif isinstance(node, list):
        for v in node:
            yield from voice_docs(v)


def general_in(node):
    if isinstance(node, dict):
        if isinstance(node.get("general"), str):
            return node["general"]
        for v in node.values():
            g = general_in(v)
            if g is not None:
                return g
    elif isinstance(node, list):
        for v in node:
            g = general_in(v)
            if g is not None:
                return g
    return None


def load(path: str):
    p = Path(path)
    if not p.exists():
        return []  # 何も保存されていない（全部採用のまま）と、ArtifactData は フォルダを作らない
    if p.is_dir():
        return [json.loads(f.read_text(encoding="utf-8")) for f in sorted(p.glob("*.json"))]
    return json.loads(p.read_text(encoding="utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("lines_json", help="collection reviews/<id>/lines の list 結果（ファイルか、1 行 1 ファイルのフォルダ）")
    ap.add_argument("--general", help="doc reviews/<id> の get 結果（全体コメント）")
    ap.add_argument("--voices", help="collection reviews/<id>/voices の list 結果（選んだ声の候補を op: voice で出す）")
    ap.add_argument("--episode", required=True, help="ページ上の回 id")
    ap.add_argument("-o", "--out", help="書き出し先（省略時は stdout）")
    a = ap.parse_args()

    docs = {d["n"]: d for d in docs_in(load(a.lines_json))}  # 同じ行が二度出ても最後の 1 件
    edits, tally = [], {"ok": 0, "fix": 0, "del": 0, "add": 0, "voice": 0}
    for n in sorted(docs):
        d = docs[n]
        st, original = d.get("st", "ok"), d.get("original", "")
        memo = (d.get("memo") or "").strip()
        if st == "fix" and (d.get("text") or "").strip() == original and not (d.get("yomi") or "").strip() \
                and not memo and (not d.get("sp") or d.get("sp") == d.get("speaker")):
            st = "ok"  # 何も変えていない訂正（下に台詞を足したかっただけ、など）は採用として扱う
        if st == "fix":
            e = {"op": "fix", "n": n, "from": original, "to": (d.get("text") or "").strip(),
                 "yomi": (d.get("yomi") or "").strip(), "memo": memo}
            if d.get("sp") and d.get("speaker") and d["sp"] != d["speaker"]:
                e["speaker_from"], e["speaker_to"] = d["speaker"], d["sp"]
            edits.append(e)
        elif st == "del":
            edits.append({"op": "del", "n": n, "text": original, "memo": memo})
        tally[st if st in tally else "ok"] += 1
        for add in d.get("adds") or []:
            text = (add.get("text") or "").strip()
            if text:
                edits.append({"op": "add", "after": n, "speaker": add.get("sp", ""), "text": text,
                              "memo": (add.get("memo") or "").strip()})
                tally["add"] += 1

    if a.voices:
        for v in voice_docs(load(a.voices)):
            # 候補が今の依頼に答えたもので、「今の声」(pick 0) 以外を選んだ行だけ
            c = next((c for c in v.get("candidates") or [] if c.get("k") == v.get("pick")), None)
            if v.get("status") == "ready" and v.get("for_req") == v.get("req") and c:
                edits.append({"op": "voice", "n": v["n"], "take": c["k"], "file": c["file"],
                              "speaker": v.get("speaker", ""), "text": v.get("text", "")})
                tally["voice"] += 1
    edits.sort(key=lambda e: (e.get("n", e.get("after", 0)), e["op"] == "voice"))

    out = {"episode": a.episode, "edits": edits}
    if a.general:
        g = general_in(load(a.general))
        if g and g.strip():
            out["general"] = g.strip()
    text = json.dumps(out, ensure_ascii=False, indent=1)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    else:
        print(text)
    # 行の文書は「採用」に戻した行も残るので、採用の数は保存された行だけを数えた値になる
    print(f"訂正 {tally['fix']}／削除 {tally['del']}／追加 {tally['add']}／声の差し替え {tally['voice']}"
          + ("／全体コメントあり" if "general" in out else ""), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
