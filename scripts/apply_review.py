#!/usr/bin/env python3
"""apply_review.py — 台詞チェックの結果（collect_results.py の JSON）を台本に入れ、次の版のエピソード dir を作る。

台本が telops.json の形のとき用。台本に承認の手続きなど独自の流れがある運用では、結果の JSON をその流れで反映してよい。

  python3 .claude/skills/daihon-mirune/scripts/apply_review.py \\
      --episode-dir <回の dir> --review review_YYYYMMDD.json --site output/daihon-mirune \\
      -o <次の版の dir>

入れるもの:
  fix   話し手（speaker_to）・台詞（to）・読み（yomi。「A → B」の組みは置き換え、文そのものは tts_text に）
  del   行を消す
  add   その行の後に足す（同じシーン）
  voice 選んだ候補の声を、その行の声にする

書くもの（-o の dir）:
  telops.json          直した台本（行番号は振り直し）
  script.md            元のまま写す（シーン名のため）
  voices/*.wav         残す声（選んだ候補か、前の版の声）。build_check_page.py の命名で置く
  review_changes.json  新しい行番号 → 前の版で何を直したか（ページの「変更・候補だけ」と「前回」の表示に使う）
声が無い行は、build_check_page.py の --tts missing で作る。足した行のほか、話し手・台詞・読みを変えたのに
候補を選んでいない行もここに入る（前の声は古い内容を読んでいるので使わない）。読みだけを変えた行は
kinds に reading_only を付け、ページが「読みだけ変更」と出す。
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path


def apply_yomi(text: str, yomi: str) -> str:
    if not re.search(r"→|->|⇒", yomi):
        return yomi
    for part in re.split(r"[、,，;；\n]", yomi):
        m = re.match(r"^\s*(.+?)\s*(?:→|->|⇒)\s*(.+?)\s*$", part)
        if m:
            text = text.replace(m.group(1), m.group(2))
    return text


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--episode-dir", required=True, help="前の版のエピソード dir（telops.json がある所）")
    ap.add_argument("--review", required=True, help="collect_results.py の出力")
    ap.add_argument("--site", required=True, help="ページの出力 dir（前の版の声 audio/<id>/NNN.mp3 と候補の声がある所）")
    ap.add_argument("--old-id", help="前の版の回 id（既定: review の episode）")
    ap.add_argument("--config", help="config.json（メモに話し手の表示名を出すため。省略時は ID のまま）")
    ap.add_argument("-o", "--out", required=True, help="次の版のエピソード dir（無ければ作る。中の telops.json などは上書き）")
    a = ap.parse_args()

    src, site, out = Path(a.episode_dir), Path(a.site), Path(a.out)
    telops = json.loads((src / "telops.json").read_text(encoding="utf-8"))
    if isinstance(telops, dict):
        telops = telops.get("telops") or telops.get("lines") or []
    review = json.loads(Path(a.review).read_text(encoding="utf-8"))
    old_id = a.old_id or review["episode"]
    names = {}
    if a.config and Path(a.config).exists():
        names = {s.get("id"): s.get("name") or s.get("id") for s in json.loads(Path(a.config).read_text(encoding="utf-8")).get("speakers", [])}
    nm = lambda sid: names.get(sid, sid)
    by_n: dict = {}
    for e in review["edits"]:
        by_n.setdefault(e.get("n", e.get("after")), []).append(e)

    if not shutil.which("ffmpeg"):
        print("ffmpeg が無い（声を wav にして置くのに要る）", file=sys.stderr)
        return 1
    (out / "voices").mkdir(parents=True, exist_ok=True)
    for old in (out / "voices").glob("*.wav"):
        old.unlink()  # 前に作った版の声を残さない
    if (src / "script.md").exists():
        shutil.copy2(src / "script.md", out / "script.md")

    prev_path = src / "review_changes.json"
    prev_changes = json.loads(prev_path.read_text(encoding="utf-8")).get("lines", {}) if prev_path.exists() else {}
    new, changes, plan = [], {}, []  # plan: (新しい n, 声の元 mp3 or None, scene, speaker)
    for n, t in enumerate(telops, 1):
        t = dict(t)
        edits = by_n.get(n, [])
        kinds, notes, voice_src = [], [], None
        if any(e["op"] == "del" for e in edits):
            continue
        for e in edits:
            if e["op"] == "fix":
                if e.get("speaker_to"):
                    notes.append(f"話し手 {nm(t.get('speaker'))} → {nm(e['speaker_to'])}")
                    t["speaker"] = e["speaker_to"]
                    kinds.append("speaker")
                if e.get("to") and e["to"] != e.get("from"):
                    notes.append(f"台詞「{e['from']}」→「{e['to']}」")
                    t["text"] = e["to"]
                    t.pop("tts_text", None)
                    kinds.append("text")
                if e.get("yomi"):
                    t["tts_text"] = apply_yomi(t.get("tts_text") or t.get("text", ""), e["yomi"])
                    notes.append(f"読み「{t['tts_text']}」")
                    kinds.append("reading")
                if e.get("memo"):
                    notes.append(f"メモ: {e['memo']}")
            elif e["op"] == "voice":
                voice_src = site / e["file"]
                notes.append(f"声 候補 {e['take']}")
                kinds.append("voice")
        # 前の版で「声は前のまま」だった行は、今回も触っていなければ今回で声を作り直す（直した意図は声にある）
        carried = prev_changes.get(str(n)) or {}
        if not edits and "voice_old" in carried.get("kinds", []):
            kinds = [k for k in carried["kinds"] if k != "voice_old"]
            notes = [p for p in carried.get("note", "").split("・") if p and not p.startswith("声は前のまま")]
            kinds.append("carried")
        if voice_src is None:
            if {"speaker", "text", "reading"} & set(kinds):
                # 話し手・台詞・読みを変えたのに候補を選んでいない: 前の声は古い内容を読んでいるので使わない。
                # 声の無い行として残し、build_check_page.py --tts missing が新しい内容で作る
                notes.append("声は新しい内容で作り直し")
                kinds.append("voice_new")
            else:
                prev = site / "audio" / old_id / f"{n:03d}.mp3"
                voice_src = prev if prev.exists() else None
        if "reading" in kinds and not {"speaker", "text"} & set(kinds):
            kinds.insert(0, "reading_only")  # 台詞はそのまま、読み方だけを変えた行（ページで目立たせる）
        new.append(t)
        if kinds:
            changes[len(new)] = {"from_n": n, "kinds": kinds, "note": "・".join(notes)}
        plan.append((len(new), voice_src, t.get("scene", ""), t.get("speaker", "")))
        for e in edits:
            if e["op"] == "add":
                new.append({"scene": t.get("scene", ""), "speaker": e["speaker"], "text": e["text"]})
                changes[len(new)] = {"from_n": None, "kinds": ["added"],
                                     "note": f"{n:03d} の後に追加" + (f"・メモ: {e['memo']}" if e.get("memo") else "")}
                plan.append((len(new), None, t.get("scene", ""), e["speaker"]))

    missing = 0
    for n, mp3, scene, speaker in plan:
        if not mp3 or not mp3.exists():
            missing += 1
            continue
        wav = out / "voices" / f"{str(scene).split('_')[0].zfill(2)}_{n:03d}_{speaker}.wav"
        r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(mp3), str(wav)], capture_output=True, text=True)
        if r.returncode != 0:
            print(f"  注意: {mp3.name} を wav にできなかった: {r.stderr.strip()[:200]}", file=sys.stderr)
            missing += 1

    (out / "telops.json").write_text(json.dumps(new, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    (out / "review_changes.json").write_text(json.dumps({"from": old_id, "lines": changes}, ensure_ascii=False, indent=1) + "\n",
                                             encoding="utf-8")
    print(f"書き出した: {out}")
    print(f"  {len(telops)} 行 → {len(new)} 行（直した・足した行 {len(changes)}、声が無い行 {missing}）")
    for k in sorted(changes):
        print(f"  {k:03d}  {changes[k]['note']}")
    if missing:
        print("  次: build_check_page.py --tts missing で、声の無い行を作る")
    return 0


if __name__ == "__main__":
    sys.exit(main())
