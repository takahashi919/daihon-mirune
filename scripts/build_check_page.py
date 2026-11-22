#!/usr/bin/env python3
"""build_check_page.py — 1 回ぶんの台詞を、台詞チェック帳のページに載せる形へ書き出す。

読むもの（回の dir）:
  telops.json   台詞の正本（scene / speaker / text / tts_text）。行番号 n は telops の通し番号（1 始まり）
  script.md     `## <scene> — <シーン名>` の見出しからシーン名を取る
  voices/       `<scene の頭 2 桁>_<n:03d>_<speaker>.wav`（apply_review.py もこの命名で置く）
  review_changes.json  apply_review.py が書く「前の版で直した行」（あれば。ページの「前回」表示と絞り込みに使う）
  config.json   話し手（表示名・色・声 ID）と音声生成サービスの設定（--config、既定は skill 直下）。
                書くのは assets/speakers.html の話者登録フォーム。API キーはここに書かない（.env か環境変数）

書くもの（--site、既定はプロジェクト直下の output/daihon-mirune/）:
  index.html             ページ本体（assets/index.html の写し）
  data/<id>.json         この回の台詞
  data/index.json        回の一覧。同じ id は差し替え、新しい回を先頭に置く
  audio/<id>/<n:03d>.mp3 声（ffmpeg で mp3 にする。wav が無い行は「音声なし」で出る。
                         --tts missing ならその行だけ、--tts all なら全行を音声生成サービスで作る）

  python3 .claude/skills/daihon-mirune/scripts/build_check_page.py \\
      --episode-dir .claude/skills/daihon-mirune/examples/demo --id demo --title "見本の台本"

公開済みのページへ足すときは、先に公開中の data/index.json を --site/data/ に取ってから打つ
（取らないと一覧が今回の回だけになり、前の回が選べなくなる）。手順は SKILL.md。
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import tts as tts_mod  # noqa: E402  (scripts/ が sys.path の先頭に入る)

SKILL_DIR = Path(__file__).resolve().parent.parent
HEADING_RE = re.compile(r"^##\s+(\S+)\s+[—-]+\s+(.+?)\s*$")
# 登録されていない話し手に順に当てる色（ページの chip の背景。文字は白／ダーク時は黒）
PALETTE = ["#4DB8FF", "#FF7F50", "#C39BEF", "#4CD97E", "#F2B84B", "#F07FB0", "#5FC9C0", "#A0A8B8"]


def load_config(path: Path) -> dict:
    if not path.exists():
        return {"tts": {}, "speakers": []}
    cfg = json.loads(path.read_text(encoding="utf-8"))
    cfg.setdefault("tts", {})
    cfg.setdefault("speakers", [])
    return cfg


def speaker_table(registered: list, used: list):
    """ページに渡す {id: {name, color, voice}} と、未登録の話し手 id の一覧を返す。
    登録済みは全員載せる（「＋ 台詞を追加」で今回いない話し手も選べるように）。未登録は id をそのまま名前にする。"""
    table, unknown = {}, []
    for s in registered:
        sid = str(s.get("id", "")).strip()
        if sid:
            table[sid] = {"name": s.get("name") or sid, "color": s.get("color") or PALETTE[len(table) % len(PALETTE)],
                          "voice": s.get("voice") or ""}
    for sid in used:
        if sid and sid not in table:
            unknown.append(sid)
            table[sid] = {"name": sid, "color": PALETTE[len(table) % len(PALETTE)]}
    return table, unknown


def scene_names(script_md: Path) -> dict:
    names = {}
    if script_md.exists():
        for line in script_md.read_text(encoding="utf-8").splitlines():
            m = HEADING_RE.match(line)
            if m:
                # 見出しの後半（「（序盤の見せ場）」「vs クラドセラケ」）は台本の書き手向けなので、
                # ページの見出しとシーン送りには頭の一語だけを出す
                names[m.group(1)] = re.split(r"[（(\s]", m.group(2), maxsplit=1)[0] or m.group(2)
    return names


def find_wav(voices: Path, n: int, scene: str, speaker: str):
    exact = voices / f"{str(scene).split('_')[0].zfill(2)}_{n:03d}_{speaker}.wav"
    if exact.exists():
        return exact
    # scene の付け替えで頭 2 桁がずれた wav も、行番号と話し手が合えば拾う
    hits = sorted(voices.glob(f"*_{n:03d}_{speaker}.wav"))
    return hits[0] if hits else None


def to_mp3(wav: Path, mp3: Path) -> bool:
    mp3.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav), "-ac", "1", "-b:a", "48k", str(mp3)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print(f"  注意: {wav.name} を mp3 にできなかった: {r.stderr.strip()[:200]}", file=sys.stderr)
    return r.returncode == 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--episode-dir", required=True, help="回の dir（telops.json がある所）")
    ap.add_argument("--id", help="ページ上の回 id。直した後の版は <回>-v2 のように別 id にする（既定: dir 名）")
    ap.add_argument("--title", help="回の選択欄に出す名前（既定: script.md の最初の見出し）")
    ap.add_argument("--site", default=str(SKILL_DIR.parent.parent.parent / "output" / "daihon-mirune"))
    ap.add_argument("--no-audio", action="store_true", help="声を載せない（台詞だけ見てもらう）")
    ap.add_argument("--config", default=str(SKILL_DIR / "config.json"), help="話し手と音声生成の設定（既定: skill 直下の config.json）")
    ap.add_argument("--tts", choices=["off", "missing", "all"], default="off",
                    help="音声生成サービスで声を作る: missing = wav が無い行だけ / all = 全行（既定 off）")
    ap.add_argument("--scene-name", action="append", default=[], metavar="SCENE=名前",
                    help="シーン名を指定する（script.md の見出しに無いシーンや、名前を変えたい時。何度でも）")
    a = ap.parse_args()

    ep_dir = Path(a.episode_dir).resolve()
    telops_path = ep_dir / "telops.json"
    if not telops_path.exists():
        print(f"telops.json が無い: {telops_path} — 先に telops を作ること", file=sys.stderr)
        return 1
    telops = json.loads(telops_path.read_text(encoding="utf-8"))
    if isinstance(telops, dict):
        telops = telops.get("telops") or telops.get("lines") or []
    ep_id = a.id or ep_dir.name
    script_md = ep_dir / "script.md"
    names = scene_names(script_md)
    for pair in a.scene_name:
        key, _, val = pair.partition("=")
        if not val:
            print(f"--scene-name は SCENE=名前 の形で: {pair}", file=sys.stderr)
            return 2
        names[key] = val
    title = a.title
    if not title and script_md.exists():
        h1 = next((l[2:].strip() for l in script_md.read_text(encoding="utf-8").splitlines() if l.startswith("# ")), "")
        title = h1 or ep_id
    title = title or ep_id

    cfg = load_config(Path(a.config))
    voice_of = {str(s.get("id", "")): s.get("voice") or "" for s in cfg["speakers"]}
    speakers, unknown = speaker_table(cfg["speakers"], list(dict.fromkeys(t.get("speaker", "") for t in telops)))
    synth = None
    if not a.no_audio and a.tts != "off":
        try:
            synth = tts_mod.make(cfg["tts"])
        except tts_mod.TTSError as e:
            print(f"音声生成の準備ができない: {e}", file=sys.stderr)
            return 1
        if synth is None:
            print("config.json の tts が provider: none なので、--tts は使えない", file=sys.stderr)
            return 2

    site = Path(a.site).resolve()
    (site / "data").mkdir(parents=True, exist_ok=True)
    if not a.no_audio and a.tts != "all" and not shutil.which("ffmpeg"):
        print("ffmpeg が無い。入れるか、--tts all（全行を音声生成）か --no-audio を付けること", file=sys.stderr)
        return 1
    audio_dir = site / "audio" / ep_id
    if audio_dir.exists():
        shutil.rmtree(audio_dir)  # 同じ id を作り直すとき、行数が減った分の古い mp3 を残さない

    voices = ep_dir / "voices"
    prev_path = ep_dir / "review_changes.json"
    prev = json.loads(prev_path.read_text(encoding="utf-8")).get("lines", {}) if prev_path.exists() else {}
    lines, missing, made = [], 0, 0
    for n, t in enumerate(telops, 1):
        scene, speaker = t.get("scene", ""), t.get("speaker", "")
        audio = ""
        if not a.no_audio:
            rel = f"audio/{ep_id}/{n:03d}.mp3"
            wav = None
            if a.tts != "all" and voices.is_dir():
                wav = find_wav(voices, n, scene, speaker)
            if wav and to_mp3(wav, site / rel):
                audio = rel
            elif synth:
                # 読みの直し（tts_text）があればそちらを読ませる。ページに出す台詞は text のまま
                try:
                    synth.synth(t.get("tts_text") or t.get("text", ""), voice_of.get(speaker, ""), site / rel)
                    audio, made = rel, made + 1
                except tts_mod.TTSError as e:
                    print(f"  注意: {n:03d} の声を作れなかった: {e}", file=sys.stderr)
                    missing += 1
            else:
                missing += 1
        line = {"n": n, "scene": scene, "sceneName": names.get(scene, scene), "sp": speaker,
                "text": t.get("text", ""), "tts": t.get("tts_text") or "", "audio": audio}
        if str(n) in prev:
            line["prev"] = {"kinds": prev[str(n)].get("kinds", []), "note": prev[str(n)].get("note", "")}
        lines.append(line)

    (site / "data" / f"{ep_id}.json").write_text(
        json.dumps({"episode": ep_id, "title": title, "speakers": speakers, "lines": lines}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    index_path = site / "data" / "index.json"
    try:
        episodes = json.loads(index_path.read_text(encoding="utf-8")).get("episodes", [])
    except (OSError, ValueError):
        episodes = []
    episodes = [{"id": ep_id, "title": title, "lines": len(lines)}] + [e for e in episodes if e.get("id") != ep_id]
    index_path.write_text(json.dumps({"episodes": episodes}, ensure_ascii=False, indent=1), encoding="utf-8")
    shutil.copy2(SKILL_DIR / "assets" / "index.html", site / "index.html")

    print(f"書き出した: {site}")
    print(f"  回 {ep_id}「{title}」 {len(lines)} 行" + ("" if a.no_audio else f"（音声なし {missing} 行）")
          + (f" — {synth.describe()} で {made} 行の声を作った" if synth else ""))
    if unknown:
        print(f"  注意: 未登録の話し手 {', '.join(unknown)} は id のまま表示した。"
              "assets/speakers.html で登録して config.json を差し替え、打ち直すと名前と色が付く")
    print(f"  一覧の回: {', '.join(e['id'] for e in episodes)}")
    if len(episodes) == 1:
        print("  注意: 一覧が今回の回だけ。公開済みのページへ足すなら、公開中の data/index.json を取ってから打ち直すこと")
    return 0


if __name__ == "__main__":
    sys.exit(main())
