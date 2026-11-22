#!/usr/bin/env python3
"""tts.py — 台詞の声を音声生成サービスで作る（行に wav が無い時の代わり）。

設定は 2 か所に分ける:
  config.json の "tts"   どのサービス・どのモデルを使うか（commit してよい）
  API キー               環境変数か、skill 直下の .env（commit しない。.gitignore 済み）

話し手ごとの声は config.json の speakers[].voice（サービスの声 ID）。空ならサービスの既定の声。
サービスを足すときは、synth(text, voice, out) を持つ class を 1 つ書いて PROVIDERS に登録する。
"""
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
# config.json に入っていたら止める項目（キーは .env か環境変数にだけ置く）
SECRET_FIELDS = {"api_key", "apikey", "key", "token", "secret", "authorization"}


class TTSError(RuntimeError):
    pass


def _dotenv(name: str):
    p = SKILL_DIR / ".env"
    if not p.is_file():
        return None
    for line in p.read_text(encoding="utf-8").splitlines():
        k, sep, v = line.partition("=")
        if sep and k.strip() == name:
            v = v.strip().strip("\"'")
            return v or None
    return None


def secret(name: str):
    return os.environ.get(name) or _dotenv(name)


class FishAudio:
    """Fish Audio（https://fish.audio）。既定のモデルは無料枠の s2.1-pro-free。
    キーは FISH_API_KEY。voice は Fish の声モデル ID（reference_id、32 桁の英数字）。"""
    key_env = "FISH_API_KEY"
    default_model = "s2.1-pro-free"

    def __init__(self, cfg: dict):
        self.url = (cfg.get("base_url") or "https://api.fish.audio").rstrip("/") + "/v1/tts"
        self.model = cfg.get("model") or self.default_model
        self._key = secret(self.key_env)
        if not self._key:
            raise TTSError(f"Fish Audio のキーが無い: 環境変数 {self.key_env} に入れるか、"
                           f".env.example を .env に写して {self.key_env}=... を書く")

    def describe(self) -> str:
        return f"Fish Audio（モデル {self.model}）"

    def synth(self, text: str, voice: str, out: Path) -> None:
        body = {"text": text, "format": "mp3", "latency": "normal"}
        if voice:
            body["reference_id"] = voice
        req = urllib.request.Request(self.url, data=json.dumps(body).encode("utf-8"), method="POST", headers={
            "Content-Type": "application/json", "Authorization": "Bearer " + self._key, "model": self.model})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                audio = r.read()
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:200]
            hint = {401: "キーが違う", 402: "無料枠・残高が足りない", 404: "声 ID が見つからない"}.get(e.code, "")
            raise TTSError(f"Fish Audio が {e.code} を返した{('（' + hint + '）') if hint else ''}: {detail}") from None
        except urllib.error.URLError as e:
            raise TTSError(f"Fish Audio につながらない（api.fish.audio への通信が許可されているか確認）: {e.reason}") from None
        if len(audio) < 100:
            raise TTSError("Fish Audio の返事が空だった")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(audio)


PROVIDERS = {"fish": FishAudio}


def make(cfg: dict):
    """config.json の "tts" から provider を作る。"provider": "none" なら None（声を作らない）。"""
    cfg = cfg or {}
    bad = sorted(k for k in cfg if k.lower() in SECRET_FIELDS)
    if bad:
        raise TTSError(f"config.json の tts に {', '.join(bad)} がある。キーは config.json に書かず、"
                       ".env か環境変数に置くこと（config.json から消してから打ち直す）")
    name = (cfg.get("provider") or "fish").lower()
    if name == "none":
        return None
    if name not in PROVIDERS:
        raise TTSError(f"知らない provider: {name}（使えるもの: {', '.join(PROVIDERS)}, none）")
    return PROVIDERS[name](cfg)
