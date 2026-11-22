#!/usr/bin/env python3
"""dev/serve.py — 書き出したページを手元で開くための小さな HTTP サーバ。

  python3 dev/serve.py output/daihon-mirune [--port 8766]

file:// ではページの fetch（data/*.json）が通らない。また Python 標準の http.server は .html に charset を付けないので、
日本語の台詞やボタンが文字化けする。ここでは .html と .json に charset=utf-8 を付けて返す。
"""
import argparse
import functools
import http.server


class Handler(http.server.SimpleHTTPRequestHandler):
    extensions_map = {**http.server.SimpleHTTPRequestHandler.extensions_map,
                      ".html": "text/html; charset=utf-8", ".json": "application/json; charset=utf-8"}

    def log_message(self, *args):
        pass


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("root", help="開く dir（build_check_page.py の --site）")
    ap.add_argument("--port", type=int, default=8766)
    a = ap.parse_args()
    print(f"http://127.0.0.1:{a.port}/index.html#<回 id>  （止めるときは Ctrl+C）")
    http.server.ThreadingHTTPServer(("127.0.0.1", a.port), functools.partial(Handler, directory=a.root)).serve_forever()


if __name__ == "__main__":
    main()
