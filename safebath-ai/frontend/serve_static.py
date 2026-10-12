#!/usr/bin/env python3
"""Small LAN static server for SafeBath frontend; intentionally hides secrets."""
import argparse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parent


class SafeBathHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def _blocked(self):
        path = unquote(urlsplit(self.path).path)
        parts = Path(path).parts
        return any(part.startswith(".") or part.lower() == "config.local.js"
                   or part.lower().startswith(".env") for part in parts)

    def do_GET(self):
        if self._blocked():
            self.send_error(404)
            return
        return super().do_GET()

    def do_HEAD(self):
        if self._blocked():
            self.send_error(404)
            return
        return super().do_HEAD()

    def translate_path(self, path):
        requested = unquote(urlsplit(path).path).lstrip("/")
        target = (ROOT / requested).resolve()
        if target != ROOT and ROOT not in target.parents:
            return str(ROOT / "__blocked__")
        return str(target)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bind", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.bind, args.port), SafeBathHandler)
    print(f"[safebath-web] serving {ROOT} on {args.bind}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
