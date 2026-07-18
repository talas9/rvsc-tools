#!/usr/bin/env python3
"""
tools/build_web.py - inject core/settings.json into docs/index.html.

core/settings.json is the single source of truth for the .rvsc container/record
format, the EPROM_* identifier table, known flag bits, and enum labels. This
script injects that JSON, verbatim, as a `const RVSC_SETTINGS = {...};`
JavaScript statement between two markers in docs/index.html:

    /* BEGIN GENERATED SETTINGS */
    ...generated content...
    /* END GENERATED SETTINGS */

so the shipped, self-contained docs/index.html never duplicates a literal
name/flag/enum table of its own. Running this script twice in a row with an
unchanged core/settings.json produces byte-identical output (idempotent).

READ-ONLY with respect to .rvsc files: this script never opens a .rvsc file at
all, only core/settings.json (read) and docs/index.html (read + optionally
rewrite).

Usage:
    python3 tools/build_web.py            # regenerate docs/index.html in place
    python3 tools/build_web.py --check    # exit 1 if docs/index.html is stale (CI)
"""

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
SETTINGS_PATH = REPO_ROOT / "core" / "settings.json"
INDEX_HTML_PATH = REPO_ROOT / "docs" / "index.html"

BEGIN_MARKER = "/* BEGIN GENERATED SETTINGS */"
END_MARKER = "/* END GENERATED SETTINGS */"


def build_generated_block(settings_json_text):
    # Re-serialize (rather than pasting the raw file text) so formatting is
    # normalized and idempotent regardless of how core/settings.json itself is
    # formatted.
    data = json.loads(settings_json_text)
    payload = json.dumps(data, indent=2, ensure_ascii=True)
    return f"{BEGIN_MARKER}\nconst RVSC_SETTINGS = {payload};\n{END_MARKER}"


def splice(html_text, generated_block):
    try:
        start = html_text.index(BEGIN_MARKER)
        end = html_text.index(END_MARKER) + len(END_MARKER)
    except ValueError as e:
        raise ValueError(
            f"could not find {BEGIN_MARKER!r} / {END_MARKER!r} markers in {INDEX_HTML_PATH}"
        ) from e
    return html_text[:start] + generated_block + html_text[end:]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true",
        help="exit 1 if docs/index.html is stale vs core/settings.json, without writing",
    )
    parser.add_argument(
        "--settings", default=str(SETTINGS_PATH), help="path to core/settings.json"
    )
    parser.add_argument(
        "--out", default=str(INDEX_HTML_PATH), help="path to docs/index.html"
    )
    args = parser.parse_args(argv)

    settings_path = Path(args.settings)
    out_path = Path(args.out)

    settings_text = settings_path.read_text(encoding="utf-8")
    generated_block = build_generated_block(settings_text)

    current_html = out_path.read_text(encoding="utf-8")
    new_html = splice(current_html, generated_block)

    if args.check:
        if new_html == current_html:
            print(f"OK: {out_path} is up to date with {settings_path}")
            return 0
        else:
            print(f"STALE: {out_path} does not match {settings_path} - run tools/build_web.py", file=sys.stderr)
            return 1

    if new_html == current_html:
        print(f"OK: {out_path} already up to date (no change)")
        return 0

    out_path.write_text(new_html, encoding="utf-8")
    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
