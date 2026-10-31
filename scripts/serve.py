#!/usr/bin/env python3
"""Static server for the Housing Explorer build.

`python3 -m http.server` does not map .mjs, so it serves the MapLibre 6 module
bundle as application/octet-stream and the browser refuses to execute it.
Register the JS MIME types explicitly and serve without caching the HTML.
"""
import functools
import http.server
import mimetypes
import os
import sys

mimetypes.add_type("text/javascript", ".mjs")
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("application/json", ".json")
mimetypes.add_type("text/css", ".css")


class Handler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        if self.path.endswith((".html", ".mjs", ".js", ".css", ".json")):
            self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def log_message(self, format, *args):  # noqa: A002 - matches the base signature
        sys.stderr.write("%s - %s\n" % (self.address_string(), format % args))


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + "/web"
    port = int(os.environ.get("PORT", "3118"))
    bind = os.environ.get("BIND", "127.0.0.1")
    handler = functools.partial(Handler, directory=root)
    with http.server.ThreadingHTTPServer((bind, port), handler) as httpd:
        print(f"serving {root} on http://{bind}:{port}", flush=True)
        httpd.serve_forever()


if __name__ == "__main__":
    main()
