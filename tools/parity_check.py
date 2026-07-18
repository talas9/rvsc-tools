#!/usr/bin/env python3
"""
tools/parity_check.py - assert the Python (rvsc.py) and JavaScript
(docs/index.html) .rvsc parsers agree, setting-by-setting, on a given file.

Both implementations are supposed to derive 100% of their format/naming/
bitfield/enum knowledge from the SAME core/settings.json (see that file's
top-level "$comment" and record_types/flags/enums "$comment" fields). This
tool is the gate that actually PROVES they agree, rather than trusting that
two independently-maintained code paths stayed in sync by construction.

Method:
  1. Extract the JS parser's core functions + its injected RVSC_SETTINGS
     constant straight out of docs/index.html (between the
     "/* BEGIN GENERATED SETTINGS */" / "/* END GENERATED SETTINGS */"
     markers for the settings table, and between the "CORE PARSER" /
     "FORMATTING HELPERS" banner comments for the parsing functions), and
     assemble them into a small, self-contained temporary Node module.
  2. Run that module under `node` against the target .rvsc file, emitting a
     normalized JSON comparison record on stdout.
  3. Run rvsc.py's own parser (imported in-process, not shelled out) against
     the same file and build the identical normalized JSON shape.
  4. Diff every setting (index, name, raw, decoded value, default, min, max,
     in_range) plus the chosen alignment (base/k/score/valid_records/
     count_data) and the file's detected serial (presence/equality only -
     the value itself is never printed unless the two disagree, to avoid
     spraying a real device serial into CI logs).

READ-ONLY: this script never writes to the target .rvsc file, and never
modifies core/settings.json, rvsc.py, or docs/index.html. It only writes a
temporary Node module under /tmp.

Usage:
    python3 tools/parity_check.py [file.rvsc]      # defaults to tests/fixture.rvsc

Exit codes:
    0  - parsers agree (or `node` is unavailable - skipped cleanly, not a
         failure of an optional runtime)
    1  - parsers disagree, or a genuine error occurred (bad file, missing
         sections in docs/index.html, etc.)
"""

import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
INDEX_HTML_PATH = REPO_ROOT / "docs" / "index.html"
DEFAULT_FIXTURE = REPO_ROOT / "tests" / "fixture.rvsc"

sys.path.insert(0, str(REPO_ROOT))
import rvsc  # noqa: E402

BEGIN_SETTINGS_MARKER = "/* BEGIN GENERATED SETTINGS */"
END_SETTINGS_MARKER = "/* END GENERATED SETTINGS */"
CORE_PARSER_BANNER = "CORE PARSER"
FORMATTING_HELPERS_BANNER = "FORMATTING HELPERS"


# ---------------------------------------------------------------------------
# 1. Extract the JS parser out of docs/index.html
# ---------------------------------------------------------------------------

def extract_js_settings_block(html_text):
    """Pull out the literal `const RVSC_SETTINGS = {...};` statement."""
    try:
        start = html_text.index(BEGIN_SETTINGS_MARKER) + len(BEGIN_SETTINGS_MARKER)
        end = html_text.index(END_SETTINGS_MARKER)
    except ValueError as e:
        raise ValueError(
            f"could not find generated-settings markers "
            f"({BEGIN_SETTINGS_MARKER!r} / {END_SETTINGS_MARKER!r}) in {INDEX_HTML_PATH}"
        ) from e
    block = html_text[start:end].strip()
    if "RVSC_SETTINGS" not in block:
        raise ValueError(
            f"generated-settings block in {INDEX_HTML_PATH} does not define RVSC_SETTINGS"
        )
    return block


def extract_js_parser_functions(html_text):
    """Pull out the core parser functions: everything between the
    "CORE PARSER" and "FORMATTING HELPERS" banner comments. Locating by these
    existing banner comments (rather than hardcoding a function list) keeps
    this working if docs/index.html's parser functions are edited, as long as
    the banners themselves stay - if they don't, that's a real structural
    change worth failing loudly on, not silently working around."""
    try:
        core_banner_pos = html_text.index(CORE_PARSER_BANNER)
    except ValueError as e:
        raise ValueError(
            f"could not find {CORE_PARSER_BANNER!r} banner comment in {INDEX_HTML_PATH}"
        ) from e
    # Functions start after that banner comment's closing "*/".
    func_start = html_text.index("*/", core_banner_pos) + 2

    try:
        helpers_banner_pos = html_text.index(FORMATTING_HELPERS_BANNER, func_start)
    except ValueError as e:
        raise ValueError(
            f"could not find {FORMATTING_HELPERS_BANNER!r} banner comment "
            f"(end of parser functions) in {INDEX_HTML_PATH}"
        ) from e
    # Back up to the start of that banner's opening comment delimiter.
    func_end = html_text.rindex("/*", func_start, helpers_banner_pos)

    block = html_text[func_start:func_end].strip()
    required = ("function parseSections", "function findAlignment", "function parseRvsc")
    missing = [r for r in required if r not in block]
    if missing:
        raise ValueError(
            f"extracted JS parser block from {INDEX_HTML_PATH} is missing expected "
            f"function(s): {missing} - extraction boundaries may be wrong"
        )
    return block


JS_MAIN_TEMPLATE = r"""
// --- auto-assembled by tools/parity_check.py - do not edit by hand ---
"use strict";
const fs = require("fs");

%(settings_block)s

%(parser_block)s

function normalizeSettings(settings) {
  return settings.map(s => ({
    index: s.index,
    name: s.name,
    raw: s.raw,
    decoded: s.decoded,
    default_raw: s.info.default,
    default_decoded: s.defaultDecoded,
    min_raw: s.info.min,
    max_raw: s.info.max,
    in_range: !!s.inRange,
  })).sort((a, b) => a.index - b.index);
}

function main() {
  const path = process.argv[2];
  const bytes = new Uint8Array(fs.readFileSync(path));
  const parsed = parseRvsc(bytes, path);
  if (parsed.error) {
    console.error("JS_PARSE_ERROR: " + parsed.error);
    process.exit(1);
  }
  const out = {
    serial: parsed.serial,
    alignment: parsed.alignment ? {
      score: parsed.alignment.score,
      base: parsed.alignment.base,
      k: parsed.alignment.k,
      in_range: parsed.alignment.inRange,
      valid_records: parsed.alignment.validRecords,
      count_data: parsed.alignment.countData,
    } : null,
    settings: normalizeSettings(parsed.settings),
  };
  process.stdout.write(JSON.stringify(out));
}

main();
"""


def build_js_module(html_text, tmp_dir):
    settings_block = extract_js_settings_block(html_text)
    parser_block = extract_js_parser_functions(html_text)
    module_text = JS_MAIN_TEMPLATE % {
        "settings_block": settings_block,
        "parser_block": parser_block,
    }
    module_path = tmp_dir / "rvsc_parser_extracted.js"
    module_path.write_text(module_text, encoding="utf-8")
    return module_path


def run_js_parser(module_path, rvsc_path):
    proc = subprocess.run(
        ["node", str(module_path), str(rvsc_path)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"JS parser (node {module_path}) exited {proc.returncode}\n"
            f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"JS parser produced non-JSON stdout: {proc.stdout!r}\nstderr: {proc.stderr}"
        ) from e


# ---------------------------------------------------------------------------
# 2. Run the Python parser (in-process)
# ---------------------------------------------------------------------------

def run_python_parser(rvsc_path):
    cfg = rvsc.load_config()
    settings, meta = rvsc.load_settings(str(rvsc_path), cfg)
    a = meta["alignment"]
    out = {
        "serial": meta["serial"],
        "alignment": {
            "score": a["score"],
            "base": a["base"],
            "k": a["k"],
            "in_range": a["in_range"],
            "valid_records": a["valid_records"],
            "count_data": a["count_data"],
        },
        "settings": sorted((
            {
                "index": s.index,
                "name": s.name,
                "raw": s.raw,
                "decoded": s.value,
                "default_raw": s.default_raw,
                "default_decoded": s.default_value,
                "min_raw": s.min_raw,
                "max_raw": s.max_raw,
                "in_range": bool(s.in_range),
            }
            for s in settings
        ), key=lambda d: d["index"]),
    }
    return out


# ---------------------------------------------------------------------------
# 3. Compare
# ---------------------------------------------------------------------------

FLOAT_TOL = 1e-9


def _num_eq(a, b):
    if isinstance(a, float) or isinstance(b, float):
        if a is None or b is None:
            return a == b
        return abs(a - b) <= FLOAT_TOL
    return a == b


def compare(py_result, js_result):
    """Return a list of human-readable diff lines. Empty list == agreement."""
    diffs = []

    # Serial: compare equality only; never print an actual serial unless the
    # two genuinely disagree (avoids spraying a real device serial into logs
    # on every clean run).
    if py_result["serial"] != js_result["serial"]:
        diffs.append(
            f"serial MISMATCH: python={py_result['serial']!r} js={js_result['serial']!r}"
        )

    pa, ja = py_result["alignment"], js_result["alignment"]
    for key in ("base", "k", "in_range", "valid_records", "count_data"):
        if pa[key] != ja[key]:
            diffs.append(f"alignment.{key} MISMATCH: python={pa[key]!r} js={ja[key]!r}")
    if not _num_eq(pa["score"], ja["score"]):
        diffs.append(f"alignment.score MISMATCH: python={pa['score']!r} js={ja['score']!r}")

    py_by_idx = {s["index"]: s for s in py_result["settings"]}
    js_by_idx = {s["index"]: s for s in js_result["settings"]}
    all_idx = sorted(set(py_by_idx) | set(js_by_idx))

    only_py = sorted(set(py_by_idx) - set(js_by_idx))
    only_js = sorted(set(js_by_idx) - set(py_by_idx))
    if only_py:
        diffs.append(f"settings present in python only: {only_py}")
    if only_js:
        diffs.append(f"settings present in js only: {only_js}")

    fields = ("name", "raw", "decoded", "default_raw", "default_decoded",
              "min_raw", "max_raw", "in_range")
    for idx in all_idx:
        p = py_by_idx.get(idx)
        j = js_by_idx.get(idx)
        if p is None or j is None:
            continue
        for f in fields:
            pv, jv = p[f], j[f]
            eq = _num_eq(pv, jv) if f in ("decoded", "default_decoded") else (pv == jv)
            if not eq:
                diffs.append(
                    f"idx {idx} ({p['name']}).{f} MISMATCH: python={pv!r} js={jv!r}"
                )

    return diffs


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main(argv):
    rvsc_path = Path(argv[1]) if len(argv) > 1 else DEFAULT_FIXTURE
    if not rvsc_path.exists():
        print(f"error: {rvsc_path} does not exist", file=sys.stderr)
        return 1

    if shutil.which("node") is None:
        print("node not found on PATH - skipping JS/Python parity check "
              "(optional runtime, not failing CI on its absence)")
        return 0

    if not INDEX_HTML_PATH.exists():
        print(f"error: {INDEX_HTML_PATH} not found", file=sys.stderr)
        return 1

    try:
        html_text = INDEX_HTML_PATH.read_text(encoding="utf-8")
        py_result = run_python_parser(rvsc_path)
    except (OSError, ValueError) as e:
        print(f"error preparing parity check: {e}", file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory(dir="/tmp", prefix="rvsc_parity_") as tmp:
        tmp_dir = Path(tmp)
        try:
            module_path = build_js_module(html_text, tmp_dir)
            js_result = run_js_parser(module_path, rvsc_path)
        except (ValueError, RuntimeError) as e:
            print(f"error running JS parser: {e}", file=sys.stderr)
            return 1

    diffs = compare(py_result, js_result)

    print(f"file: {rvsc_path}")
    print(f"python: {len(py_result['settings'])} settings, "
          f"alignment score={py_result['alignment']['score']:.4f} "
          f"(base={py_result['alignment']['base']}, k={py_result['alignment']['k']}, "
          f"{py_result['alignment']['in_range']}/{py_result['alignment']['valid_records']} in-range)")
    print(f"js:     {len(js_result['settings'])} settings, "
          f"alignment score={js_result['alignment']['score']:.4f} "
          f"(base={js_result['alignment']['base']}, k={js_result['alignment']['k']}, "
          f"{js_result['alignment']['in_range']}/{js_result['alignment']['valid_records']} in-range)")

    if not diffs:
        print("PARITY OK: python and javascript parsers agree on every setting.")
        return 0

    print()
    print(f"PARITY FAILURE: {len(diffs)} disagreement(s) between python and javascript parsers:")
    for d in diffs:
        print(f"  - {d}")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
