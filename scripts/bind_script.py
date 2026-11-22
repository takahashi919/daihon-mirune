#!/usr/bin/env python3
"""bind_script.py — チェックを終えた台本を「製本」する。聞く（ask）と、書き出す（bind）の 2 段。

流れ（SKILL.md の 5）:
  1. ある回の結果に直しが 1 つも無い（訂正・削除・追加・声の差し替えが 0、声の依頼も残っていない）
  2. ask   … 製本の確認ページを組み立てる。Claude が新しい artifact として公開し、オーナーに URL を渡す
             ページでは通しで聞けて、「製本する／まだ直す」の答えがページの db（bind/answer）に残る
  3. Claude が答えを読む（ArtifactData get collection="bind" doc_id="answer"）
       bind   → bind で最終版を書き出す
       revise → メモを読み、直したい所を聞いて、もう一回チェック帳へ
  4. bind  … 最終版の dir を書く

  python3 .claude/skills/daihon-mirune/scripts/bind_script.py ask  --site output/daihon-mirune --id <回> \\
      --history results/review_v1.json results/review_v2.json ... -o output/daihon-mirune/bind/<回>
  python3 .claude/skills/daihon-mirune/scripts/bind_script.py bind --site output/daihon-mirune --id <回> \\
      [--episode-dir <回の dir>] [--history ...] -o <製本の置き場>

読むもの:
  <site>/data/<id>.json     ページに載せた台詞（話し手の名前と色・読み・声の場所が揃っている）
  <site>/audio/<id>/*.mp3   その回の声
  --history                 各回の collect_results.py の出力（古い順）。チェックの履歴として載せる

bind が書くもの（-o の dir）:
  script.md       製本台本（シーンごと・話し手の名前つき。読みが台詞と違う行は読みも書く）
  telops.json     --episode-dir の telops.json の写し（あれば）
  audio/NNN.mp3   行ごとの声
  full.mp3        通しの音声（行の間に --gap 秒の無音）
  manifest.json   回 id・行数・話し手・長さ・チェックの履歴
"""
import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent


def duration(path: Path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def rounds_from(history: list) -> list:
    """各回の結果 JSON を、ページの「チェックの履歴」の 1 行ずつにする。"""
    out = []
    for h in history:
        d = json.loads(Path(h).read_text(encoding="utf-8"))
        count = {"fix": 0, "del": 0, "add": 0, "voice": 0}
        for e in d.get("edits", []):
            if e.get("op") in count:
                count[e["op"]] += 1
        out.append({"id": d.get("episode", Path(h).stem), **count})
    return out


def load_episode(site: Path, ep_id: str) -> dict:
    data = json.loads((site / "data" / f"{ep_id}.json").read_text(encoding="utf-8"))
    data.setdefault("speakers", {})
    return data


def cmd_ask(a) -> int:
    site, out = Path(a.site).resolve(), Path(a.out).resolve()
    ep = load_episode(site, a.id)
    (out / "audio").mkdir(parents=True, exist_ok=True)
    lines, total, no_audio = [], 0.0, 0
    for l in ep["lines"]:
        rel = ""
        if l.get("audio") and (site / l["audio"]).exists():
            rel = f"audio/{l['n']:03d}.mp3"
            shutil.copy2(site / l["audio"], out / rel)  # 確認ページは別の artifact なので、声もそこへ写す
            total += duration(out / rel)
        else:
            no_audio += 1
        lines.append({"n": l["n"], "scene": l.get("scene", ""), "sceneName": l.get("sceneName", ""), "sp": l.get("sp", ""),
                      "text": l.get("text", ""), "tts": l.get("tts", ""), "audio": rel})
    used = sorted({l["sp"] for l in lines}, key=lambda s: [l["sp"] for l in lines].index(s))
    rounds = rounds_from(a.history or [])
    bind = {
        "id": a.id, "title": ep.get("title", a.id),
        "speakers": {s: ep["speakers"].get(s, {"name": s}) for s in used},
        "lines": lines, "duration": round(total, 1),
        "rounds": rounds,
        # 製本してよいかの判断材料。0 でない項目は、ページが注意として出す
        "checks": {"over50": sum(1 for l in lines if len(l["text"]) > 50), "noAudio": no_audio, "pendingVoices": a.pending,
                   "lastClean": bool(rounds) and not any(rounds[-1][k] for k in ("fix", "del", "add", "voice"))},
    }
    (out / "bind.json").write_text(json.dumps(bind, ensure_ascii=False, indent=1), encoding="utf-8")
    shutil.copy2(SKILL_DIR / "assets" / "bind.html", out / "index.html")
    print(f"書き出した: {out}")
    print(f"  「{bind['title']}」 {len(lines)} 行・{total:.0f} 秒・チェック {len(bind['rounds'])} 回")
    print("  次: Artifact publish（新しい artifact）file_path=index.html、files=bind.json と audio/*.mp3、capabilities={\"db\": {}}")
    return 0


def cmd_bind(a) -> int:
    site, out = Path(a.site).resolve(), Path(a.out).resolve()
    ep = load_episode(site, a.id)
    if not shutil.which("ffmpeg"):
        print("ffmpeg が無い（通しの音声を作るのに要る）", file=sys.stderr)
        return 1
    (out / "audio").mkdir(parents=True, exist_ok=True)
    name = lambda sp: (ep["speakers"].get(sp) or {}).get("name") or sp

    md, scene, parts, total = [f"# {ep.get('title', a.id)}", ""], None, [], 0.0
    used = []
    for l in ep["lines"]:
        if l["sp"] not in used:
            used.append(l["sp"])
    md += ["登場: " + "／".join(name(s) for s in used), ""]
    for l in ep["lines"]:
        if l.get("scene") != scene:
            scene = l.get("scene")
            md += ["", f"## {l.get('sceneName') or scene}", ""]
        line = f"{l['n']:03d}　**{name(l['sp'])}**　{l['text']}"
        if l.get("tts") and l["tts"] != l["text"]:
            line += f"　（読み: {l['tts']}）"
        md.append(line + "  ")
        if l.get("audio") and (site / l["audio"]).exists():
            dst = out / "audio" / f"{l['n']:03d}.mp3"
            shutil.copy2(site / l["audio"], dst)
            parts.append(dst)
            total += duration(dst)
    (out / "script.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    if a.episode_dir and (Path(a.episode_dir) / "telops.json").exists():
        shutil.copy2(Path(a.episode_dir) / "telops.json", out / "telops.json")

    if parts:
        # 行の声をそのままつなぐと詰まって聞こえるので、間に無音を挟む。形式は 1 本目に揃えて読み直す
        gap = out / "audio" / "_gap.mp3"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-t", str(a.gap),
                        "-b:a", "64k", str(gap)], check=True)
        listing = out / "audio" / "_concat.txt"
        listing.write_text("".join(f"file '{p.name}'\nfile '{gap.name}'\n" for p in parts), encoding="utf-8")
        r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(listing),
                            "-ac", "1", "-ar", "44100", "-b:a", "96k", str(out / "full.mp3")], capture_output=True, text=True)
        gap.unlink(); listing.unlink()
        if r.returncode != 0:
            print(f"通しの音声を作れなかった: {r.stderr.strip()[:300]}", file=sys.stderr)
            return 1

    manifest = {"id": a.id, "title": ep.get("title", a.id), "lines": len(ep["lines"]),
                "speakers": {s: name(s) for s in used}, "duration": round(total + a.gap * len(parts), 1),
                "rounds": rounds_from(a.history or []),
                "bound_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"製本した: {out}")
    print(f"  script.md・audio/（{len(parts)} 行）・full.mp3（約 {manifest['duration']:.0f} 秒）・manifest.json")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("ask", "bind"):
        p = sub.add_parser(name)
        p.add_argument("--site", required=True, help="ページの出力 dir（data/<id>.json と audio/<id>/ がある所）")
        p.add_argument("--id", required=True, help="製本する回 id（直しが 0 になった版）")
        p.add_argument("--history", nargs="*", help="各回の collect_results.py の出力（古い順）")
        p.add_argument("-o", "--out", required=True)
        if name == "ask":
            p.add_argument("--pending", type=int, default=0, help="残っている声の候補の依頼の数（0 でなければページが注意を出す）")
        else:
            p.add_argument("--episode-dir", help="その回の dir（telops.json を写す）")
            p.add_argument("--gap", type=float, default=0.4, help="通しの音声で行の間に入れる無音（秒）")
    a = ap.parse_args()
    return cmd_ask(a) if a.cmd == "ask" else cmd_bind(a)


if __name__ == "__main__":
    sys.exit(main())
