import threading
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import urlopen

from frontend.serve_static import SafeBathHandler


def test_static_pages_and_sensitive_paths():
    server = ThreadingHTTPServer(("127.0.0.1", 0), SafeBathHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        assert urlopen(base + "/").status == 200
        assert urlopen(base + "/caretaker.html").status == 200
        for path in ("/config.local.js", "/.env", "/../.env"):
            try:
                urlopen(base + path)
                assert False, f"expected {path} to be blocked"
            except HTTPError as error:
                assert error.code == 404
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
