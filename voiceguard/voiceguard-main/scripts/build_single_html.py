"""Pack the frontend into one HTML file that can be sent around and opened with a double-click.

    python scripts/build_single_html.py                      # writes voiceguard-frontend.html
    python scripts/build_single_html.py --api https://your-backend.example.com

The file contains the page, its styles and all its scripts. It still needs a VoiceGuard
backend to analyse audio (the scripted demo works without one):
  * opened as a file, it talks to http://localhost:8000 (what run.bat starts);
  * --api bakes in another backend address; it can also be edited at the top of the script
    block inside the file (window.VOICEGUARD_API);
  * served by the backend itself, it uses that server.

Run it again after changing anything in frontend/.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "frontend"
OUT = ROOT / "voiceguard-frontend.html"
WORKLET_CALL = 'new URL("./pcm-worklet.js", import.meta.url)'
EXPORT = re.compile(r"^export (?=(?:class|const|function) (\w+))", re.M)


def read(name: str) -> str:
    return (SRC / name).read_text(encoding="utf-8")


def as_function(source: str) -> str:
    """An ES module as an expression that returns its exports: (() => { ...; return { A, B }; })()"""
    names = [m.group(1) for m in EXPORT.finditer(source)]
    return f"(() => {{\n{EXPORT.sub('', source)}\nreturn {{ {', '.join(names)} }};\n}})()"


def replace_once(text: str, old: str, new: str) -> str:
    if text.count(old) != 1:
        raise SystemExit(f"Expected exactly one {old!r} in the frontend; found {text.count(old)}.")
    return text.replace(old, new)


def build(api: str) -> str:
    # The audio worklet must be loaded from a URL, so it travels as text and becomes a data: URL
    # (a blob: URL is refused when the page is opened as a file).
    client = replace_once(read("voiceguard-client.js"), WORKLET_CALL, "WORKLET_URL")
    app = read("app.js")
    for line in ('import { VoiceGuardClient } from "./voiceguard-client.js";', 'import { DEMO } from "./demo.js";'):
        app = replace_once(app, line, "")
    script = f"""
// Where the VoiceGuard backend lives. "" means: the server this page came from, or
// http://localhost:8000 when the page is opened as a file. Put your backend's HTTPS address
// here if it is hosted elsewhere, e.g. "https://your-backend.example.com".
window.VOICEGUARD_API = {json.dumps(api)};
if (!window.VOICEGUARD_API && location.protocol === "file:") window.VOICEGUARD_API = "http://localhost:8000";

(() => {{
"use strict";
const WORKLET_URL = "data:text/javascript;charset=utf-8," + encodeURIComponent({json.dumps(read("pcm-worklet.js"))});
const {{ VoiceGuardClient }} = {as_function(client)};
const {{ DEMO }} = {as_function(read("demo.js"))};
{app}
}})();
"""
    html = read("index.html")
    html = replace_once(html, '<link rel="stylesheet" href="styles.css">', f"<style>\n{read('styles.css')}</style>")
    html = replace_once(html, '<script src="config.js"></script>\n', "")
    html = replace_once(html, '<script type="module" src="app.js"></script>\n', "")
    # Classic scripts run where they stand, so this one goes last, after the page it drives.
    script = script.replace("</script", "<\\/script")
    html = replace_once(html, "</body>", f"<script>{script}</script>\n</body>")
    return html.replace("<!doctype html>", "<!doctype html>\n<!-- Built by scripts/build_single_html.py from frontend/. Edit the sources, not this file. -->", 1)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Pack the frontend into one HTML file.")
    ap.add_argument("--api", default="", help='backend address to bake in, e.g. "https://your-backend.example.com"')
    ap.add_argument("--out", type=Path, default=OUT, help=f"output file (default: {OUT.name})")
    args = ap.parse_args(argv)
    html = build(args.api.rstrip("/"))
    args.out.write_text(html, encoding="utf-8", newline="\n")
    print(f"wrote {args.out}  ({len(html.encode('utf-8')) / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
