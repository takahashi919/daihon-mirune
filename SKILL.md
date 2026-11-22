---
name: daihon-mirune
description: 台本見るね。台本の台詞を 1 行ずつ声つきで並べた「台詞チェック帳」のページを作り、オーナーが付けた採用・訂正・削除・追加・声の選択を読み取って、次の版を作り、直しが無くなったら製本する。「台本見るね」「台詞チェック帳」「台詞チェック」「台詞を 1 行ずつ見てもらう」「反映して」「声の候補を作って」「製本して」で発動。
---

# daihon-mirune（台本見るね）— 台詞チェック帳

台本を 1 行ずつ声つきでページに並べ、オーナーが直しを付け、Claude が読み取って次の版を出す。これを直しが無くなるまで回し、
最後に「製本してよいか」を聞いて最終版を書き出す。仕組み（db の形・ファイル・スクリプトのつながり）は `docs/ARCHITECTURE.md`。

```
台本 ─1→ チェック帳に回を足す ─→ オーナーがチェック ─2→ 結果を読む ─┬─ 直しあり ─3→ 反映 ─4→ 次の版（-v2…）を足す ─┐
                                   ↑                                   │                                              │
                                   └───────────────────────────────────┼──────────────────────────────────────────────┘
                                                                       └─ 直し 0 ─5→ 製本の確認ページ ─→ 答え: 製本する → 製本
```

## 早見表 — 言われたこと・状況 → やること

| オーナーの言葉・状況 | やること |
|---|---|
| 新しい台本をチェックしたい | 0（初回だけ）→ 1 |
| **このページについての話すべて**（結果の貼り付け・「反映して」「候補を作って」「見て」など、言い方は問わない） | **毎ターンの固定手順**（下）を最初に回す |
| 読んだ結果に直しがある | 3 → 4。新しい版の URL を渡す |
| 読んだ結果に直しが 1 つも無く、声の依頼も残っていない | 5（製本の確認ページを出す） |
| 製本の確認ページで「製本する」と答えた | 5 の bind。書き出した場所を伝える |
| 製本の確認ページで「まだ直す」と答えた | メモを読み、直したい所を聞く。チェック帳で同じ版をもう一度見てもらう |
| 話し手を足したい・声を変えたい | ページの「話し手」タブで登録してもらう（すぐ全画面に出る）。次のターンで `sync_speakers.py` |
| ボタンを押したのに声の候補が出ない | 候補は押した後の **Claude の次の返事** で作る仕様。固定手順の A で作る |

### 毎ターンの固定手順（このページの話をされたら、必ずこの順で）

1. **A. 依頼と登録を片づける**（2-b）: `speakers` と `reviews/<id>/voices` と `reviews/<id>/lines` を list → `make_voice_candidates.py --lines --speakers` →
   候補の mp3 を publish → `writes` を `if_version` 付きで batch → `sync_speakers.py`
2. **B. 結果を読む**（2）: `collect_results.py`。会話に結果が貼られていたら、db から読んだものと **突き合わせて差を報告** する（正は db）
3. **C. 分岐**（早見表）: 直しあり → 3・4 ／ 直し 0 かつ依頼 0 → 5

「たぶん前と同じ」で手順を飛ばさない。依頼は押されたその後にしか分からない。

## 0. 話し手と声の準備（最初に 1 回）

話し手と音声生成サービスの設定は skill 直下の `config.json`。ページには話し手を書き込まない（回ごとの `data/<id>.json` に build が載せる）。

```json
{"tts": {"provider": "fish", "model": "s2.1-pro-free"},
 "speakers": [{"id": "narrator", "name": "ナレーター", "color": "#4DB8FF", "voice": ""}]}
```

- `id` は telops の `speaker` と同じ文字列。`voice` はサービス側の声 ID（Fish Audio なら声モデルの 32 桁）。空ならサービスの既定の声
- ページを作る前は `assets/speakers.html`（話者登録フォーム）で `config.json` を作る。ページができた後は、ページの「話し手」タブ
  （db の `speakers/`。Claude が `sync_speakers.py` で `config.json` に写す）
- 既定の音声生成は **Fish Audio の `s2.1-pro-free`（無料枠）**。使わないなら `"provider": "none"`。足すなら `scripts/tts.py` に class を 1 つ
- **API キーは `config.json` に書かない。** `.env.example` を `.env` に写して `FISH_API_KEY=…`、または環境変数 `FISH_API_KEY`
  （`.env` は `.gitignore` 済み。`config.json` の `tts` にキーらしき項目があると build が止まる）
- 試すなら `examples/demo/`（3 人でこの skill の使い方を話すテスト台本）

## 1. ページに回を足す

```sh
# 公開中の回の一覧を先に取る（取らないと一覧が今回の回だけになり、前の回が選べなくなる）
#   → ページの URL は output/daihon-mirune/page.json の "check"（無ければ初回: 下の「ページの置き場」）
#   → Artifact read  url=<ページの URL>  path="data/index.json"  out_dir=output/daihon-mirune
python3 .claude/skills/daihon-mirune/scripts/build_check_page.py \
    --episode-dir <回の dir> --id <回> [--title "…"] [--scene-name 08_matome=まとめ] [--tts missing]
```

- 読むもの: `telops.json`（台詞の正本）・`script.md`（`## <scene> — <名前>` からシーン名）・`voices/<scene の頭 2 桁>_<n:03d>_<speaker>.wav`
- 出力は `output/daihon-mirune/`（`index.html` / `data/index.json` / `data/<id>.json` / `audio/<id>/NNN.mp3`）
- wav が無い行は「音声なし」。`--tts missing`（無い行だけ）/ `--tts all`（全行）で `config.json` のサービスと声で作る。読み（`tts_text`）があればそちらを読ませる
- 公開は **既存の URL へ** 上書き: Artifact publish に `url`・`file_path` = `output/daihon-mirune/index.html`・
  `files` = 今回の `data/<id>.json`・`data/index.json`・`audio/<id>/*.mp3`。送らなかった前の回のファイルは残る
- 新しくページを作る時だけ `capabilities: {"db": {}}` を付ける（上書きでは省く。付いたまま残る）
- オーナーには `<URL>#<id>` を渡す

## 2. 結果を読む

```sh
# ArtifactData list  url=<URL>  collection="reviews/<id>/lines"   query.limit=1000  out_dir=output/daihon-mirune/results
# ArtifactData list  url=<URL>  collection="reviews/<id>/voices"  query.limit=1000  out_dir=output/daihon-mirune/results
# ArtifactData get   url=<URL>  collection="reviews"  doc_id="<id>"                 out_dir=output/daihon-mirune/results
python3 .claude/skills/daihon-mirune/scripts/collect_results.py \
    output/daihon-mirune/results/reviews/<id>/lines --general output/daihon-mirune/results/reviews/<id>.json \
    --voices output/daihon-mirune/results/reviews/<id>/voices --episode <id> -o <置き場>/review_YYYYMMDD.json
```

出力（ページの「結果をコピー」の機械用と同じ形）:
`{"episode", "edits": [{op: fix, n, from, to, yomi, memo[, speaker_from, speaker_to]} | {op: del, n, text, memo} | {op: add, after, speaker, text, memo} | {op: voice, n, take, file, speaker, text}], "general"}`

- **読むのは db の保存済みの分だけ**（未保存のまま閉じた行は入らない）。行が 1 つも保存されていない回（全部採用）でも読める
- 何も変えていない訂正（台詞・話し手が同じで読み・メモも無い）は採用として数える（下に台詞を足したかっただけのことが多い。ページも保存時に採用に戻す）
- `op: voice` は選んだ声の候補。`file` は `output/daihon-mirune/` 基準

## 2-b. 声の候補（毎ターンの固定手順 A）

ページは音声生成サービスにつながらない（外部へ通信できず、キーもページに置かない）。ページは db に **依頼** を残すだけで、
候補を作るのは Claude。押してすぐには出ず、**Claude の次の返事で出る**。依頼が出るのは 2 通り:
「声の候補を作る」ボタン、または訂正で話し手・台詞・読みを変えて保存した時（ボタンなしで自動）。

```sh
# ArtifactData list  url=<URL>  collection="reviews/<id>/voices"  query.limit=1000  out_dir=output/daihon-mirune/requests  （version を控える）
# ArtifactData list  url=<URL>  collection="speakers"             query.limit=1000  out_dir=output/daihon-mirune/requests
python3 .claude/skills/daihon-mirune/scripts/make_voice_candidates.py \
    output/daihon-mirune/requests/reviews/<id>/voices --episode <id> --speakers output/daihon-mirune/requests/speakers \
    --lines output/daihon-mirune/results/reviews/<id>/lines [--takes 3]
python3 .claude/skills/daihon-mirune/scripts/sync_speakers.py output/daihon-mirune/requests/speakers
```

- `status: requested` の行と、`--lines` で見つけた「声が変わる訂正なのに依頼が無い行」を作る（同じ文を 3 回読ませる。毎回間や抑揚が違う）
- 読みの直しは「A → B」の組み（置き換え）か、読ませたい文そのもの。ページとスクリプトは同じ規則
- 公開: Artifact publish に `url`・`file_path` = `output/daihon-mirune/index.html`・`files` = `voice_updates.json` の `files`
- 書き戻し: `voice_updates.json` の `writes` に、list で控えた version を `if_version` として足して ArtifactData `batch`
  （`created` に出た doc は新規なので付けない）。version が違えば書かれない＝依頼し直されたので作り直す

## 3. 反映する

台本が telops.json の回は、ここで次の版を作る（台本に承認の手続きなど独自の流れがある運用では、結果の JSON をその流れで反映してよい）:

```sh
python3 .claude/skills/daihon-mirune/scripts/apply_review.py --episode-dir <前の版の dir> \
    --review review_YYYYMMDD.json --site output/daihon-mirune --config config.json -o <次の版の dir>
```

- 話し手・台詞・読み（`tts_text`）・削除・追加を入れ、行番号を振り直す。選んだ声はその行の声に、ほかは前の版の声を引き継ぐ
- **話し手・台詞・読みを変えたのに候補を選ばなかった行は、前の声を使わず作り直す**（直した意図は声にある。読みだけの訂正は特に）。
  前の版で古い声のまま残った行も次の反映で作り直す
- `review_changes.json`（新しい行番号 → 前回直したこと）を書く。読みだけの訂正は `reading_only`
- 前の版で残っていた声の依頼は、行番号が同じなら次の版へ持ち越して作る（2-b を新しい id で回す）

## 4. 次の版を足す

直した後の台本で、**別の id**（`<回>-v2`、`-v3`…）を 1 の手順で足す（`--tts missing` で、足した行・作り直す行の声を作る）。
古い版のままだと、オーナーは次の指摘を古い行番号で書いてしまう。`--title` に「何回目・何を反映したか」を書く。

- ページは `review_changes.json` を読んで、行ごとに「前回」と直した種類の印（話し手・台詞・読み・**読みだけ変更**・選んだ声・
  声を作り直し・追加・持ち越し）を出し、その回を「変更・候補だけ」の表示で開く
- まだ誰も保存していない版なら、同じ id で作り直してよい（保存があれば必ず別 id）

## 5. 製本する（直しが無くなったら）

固定手順 B で直しが 0（訂正・削除・追加・声の差し替えが無い）かつ A で依頼が 0 なら、製本してよいかを **確認ページで聞く**。
チャットの文だけで聞かない。

```sh
python3 .claude/skills/daihon-mirune/scripts/bind_script.py ask --site output/daihon-mirune --id <回> \
    --history <各回の review_*.json を古い順に> -o output/daihon-mirune/bind/<回>
#   → Artifact publish（新しい artifact）file_path=output/daihon-mirune/bind/<回>/index.html、
#     files = bind.json と audio/*.mp3、capabilities={"db": {}}、icon="book"
```

- ページは通しで聞けて、チェックの履歴と残りの確認（50 字超え・声なし・作りかけの候補）を出し、「製本する／まだ直す」を聞く
- 答えはそのページの db `bind/answer` `{choice: bind｜revise, note, id, at}`。次のターンで ArtifactData get で読む
- `bind` なら書き出す:

```sh
python3 .claude/skills/daihon-mirune/scripts/bind_script.py bind --site output/daihon-mirune --id <回> \
    --episode-dir <回の dir> --history <同じ> -o <製本の置き場>
```

  `script.md`（製本台本）・`telops.json`・`audio/NNN.mp3`・`full.mp3`（通し）・`manifest.json`（行数・話し手・長さ・履歴）
- `revise` ならメモを読んで直したい所を聞き、チェック帳でもう一度（新しい版は要らない。直しが出たら 3・4）

## やってはいけないこと

- 認証情報をページ・data・`config.json`・依頼文に入れない（キーは `.env` か環境変数だけ）
- 保存がある版の id を作り直しで上書きしない（オーナーの付けた行番号がずれる）
- 結果を推測で作らない。読むのは db、貼られた結果は突き合わせ用
- オーナーが答えていない製本をしない。ページで「製本する」が付いてから
- テスト用の台本（`examples/demo/`）に、試しの直しを反映しない（反映先は作業用のコピー）

## ページの置き場

- 台詞チェック帳のページは 1 枚を使い回す（回は `#<id>` で切り替わる）。初めて公開した時に、その URL を
  `output/daihon-mirune/page.json` に `{"check": "<URL>"}` として書いておく。次からは読んで、そこへ上書きする
- 製本の確認ページは回ごとに新しく作る。URL を同じ `page.json` の `"bind": {"<回>": "<URL>"}` に足す
- `page.json` が無い・読めない時は、Artifact の list でタイトル「台詞チェック帳」を探す。それでも無ければ新しく作る
- 台詞 1 行は 50 字以内を目安にする（ページは超えた行を赤で出す）
