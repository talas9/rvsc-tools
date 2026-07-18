#!/usr/bin/env python3
"""
rvsc.py - READ-ONLY parser/diff tool for Victron VEConfigure ".rvsc" setting files.

SAFETY: This tool NEVER writes to a .rvsc file. Every file is opened in mode 'rb' only.
There is no code path anywhere in this file that opens a .rvsc file for writing.

All format knowledge (section names, record layout, the EPROM_* identifier table, known
flag bits, enum labels, and confidence tiers) lives in core/settings.json, which this
tool loads at runtime - see that file for the single source of truth. Nothing here
duplicates a literal name/flag/enum table; see FORMAT.md for the full specification.

Container: a sequence of sections. The very first section (the file header) is
[u16 nameLen][name bytes] with NO end-offset field. Every subsequent section is:

    [u16 nameLen][name bytes][u32 absolute_end_offset][payload bytes...]

`absolute_end_offset` is the absolute byte offset in the file where that section's
payload ends. The final section's end offset equals the file size.

BareSettingInfo payload = array of fixed-size records (one per setting index);
BareSettingData payload = parallel array of raw u16 values, same indexing. See
core/settings.json "record_layout" for field order/types.

Decode rule:  if scale < 0:  real = (raw + offset) / abs(scale)
              if scale > 0:  real = (raw + offset) * scale
              if scale == 0: value has no linear meaning (bitfield/reserved slot)

Neither array's start offset is fixed across files/firmware revisions, so this tool
finds the correct start offset for each array at runtime using a RANGE-WEIGHTED,
self-validating alignment search - see find_alignment() below for the full rationale
and core/settings.json "alignment" for the tunable constants. This is the same
algorithm used by docs/index.html (kept identical on purpose - see tools/build_web.py).
"""

import json
import struct
import sys
import re
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_SETTINGS_PATH = SCRIPT_DIR / "core" / "settings.json"

_TYPE_TO_STRUCT = {"i16": "h", "u16": "H", "i32": "i", "u32": "I", "i8": "b", "u8": "B"}


class Config:
    """Wraps core/settings.json: section names, record layout, alignment tunables,
    the EPROM_* name table, known flags, and enum labels. Loaded once per run."""

    def __init__(self, raw):
        self.raw = raw
        self.section_names = {k: v["name"] for k, v in raw["sections"].items()}

        layout = raw["record_layout"]
        self.info_fields = layout["info_fields"]
        self.info_rec_size = layout["info_record_size"]
        self.data_rec_size = layout["data_record_size"]
        endian = "<" if layout.get("endianness", "little") == "little" else ">"
        self.info_struct_fmt = endian + "".join(
            _TYPE_TO_STRUCT[layout["info_field_types"][f]] for f in self.info_fields
        )
        self.data_struct_fmt = endian + _TYPE_TO_STRUCT[layout["data_field_type"]]

        align = raw["alignment"]
        self.info_header_search_range = align["info_header_search_range"]
        self.min_valid_record_fraction = align["min_valid_record_fraction"]

        self.epron_names = raw["naming"]["epron_names"]

        self.flags = raw["flags"]["items"]  # list of {id, setting_name, bit, label}

        self.enums = raw["enums"]["items"]  # {setting_name: {"1": "Fixed", ...}}

    def setting_name(self, index):
        # index 0 is the unnamed sentinel record (verified: scale==0 in every file seen).
        names = self.epron_names
        if index >= 1 and (index - 1) < len(names):
            return names[index - 1]
        return f"setting_{index}"

    def enum_label(self, setting_name, raw_value):
        table = self.enums.get(setting_name)
        if not table:
            return None
        return table.get(str(raw_value))


def load_config(path=None):
    path = Path(path) if path else DEFAULT_SETTINGS_PATH
    with open(path, "r", encoding="utf-8") as f:  # READ-ONLY, text config, not a .rvsc file.
        raw = json.load(f)
    return Config(raw)


# ---------------------------------------------------------------------------
# Container parsing
# ---------------------------------------------------------------------------

class Section:
    __slots__ = ("name", "start", "payload_start", "end")

    def __init__(self, name, start, payload_start, end):
        self.name = name
        self.start = start
        self.payload_start = payload_start
        self.end = end


def parse_sections(data):
    """Parse the section container. Returns a list of Section (header section has
    payload_start == None since it carries no payload/end-offset field)."""
    sections = []
    if len(data) < 2:
        raise ValueError("file too small to contain a header section")

    namelen = struct.unpack_from("<H", data, 0)[0]
    if 2 + namelen > len(data):
        raise ValueError("malformed header section (name length overruns file)")
    header_name = data[2:2 + namelen].decode("latin1")
    sections.append(Section(header_name, 0, None, 2 + namelen))

    off = 2 + namelen
    while off < len(data):
        if off + 2 > len(data):
            break
        namelen = struct.unpack_from("<H", data, off)[0]
        if namelen == 0 or off + 2 + namelen + 4 > len(data):
            break
        nameoff = off + 2
        name = data[nameoff:nameoff + namelen].decode("latin1")
        pos = nameoff + namelen
        end_abs = struct.unpack_from("<I", data, pos)[0]
        payload_start = pos + 4
        if end_abs < payload_start or end_abs > len(data):
            break
        sections.append(Section(name, off, payload_start, end_abs))
        off = end_abs

    return sections


def sections_by_name(sections):
    return {s.name: s for s in sections if s.payload_start is not None}


# ---------------------------------------------------------------------------
# Decoding
# ---------------------------------------------------------------------------

def decode_value(raw, scale, offset):
    if scale == 0:
        return None
    if scale < 0:
        return (raw + offset) / abs(scale)
    return (raw + offset) * scale


# ---------------------------------------------------------------------------
# Alignment: range-weighted self-validating search
# ---------------------------------------------------------------------------
#
# CHOICE OF ALGORITHM (see also docs/index.html findAlignment(), which is kept
# byte-for-byte equivalent on purpose):
#
# An earlier version of this tool scored every (info_offset, data_offset) candidate
# by the RAW COUNT of nonzero-scale records whose RAW value fell inside its own
# raw [min, max], then broke ties by picking the smallest offset within a fixed
# tolerance (3 points) of the top score. That worked on the one reference file it
# was tuned against, but it has two structural weaknesses on files we have never
# seen: (1) it compares raw values against raw bounds, which is a unit mismatch for
# any scaled field and was found to be an outright bug; (2) because both arrays are
# fixed-stride, shifting the info array by +N*10 bytes and the data array by +N*2
# bytes together re-reads the same underlying bytes with the first N records
# dropped, and dropping records can only raise or hold a raw match-count score
# never lower it - so a raw argmax is structurally biased toward these "stride
# alias" candidates. The old tool patched this with a hand-tuned tie-break
# tolerance constant, which has no principled basis and could silently fail on a
# file family where the alias/true-alignment score gap differs from 3 points.
#
# This version instead weights each in-range match by 1 / (decoded window width),
# and excludes degenerate records (scale == 0, or max <= min) from scoring
# entirely. A stride-alias candidate that "wins" only by dropping real leading
# records with wide, loosely-bounded ranges no longer has anywhere to hide: wide
# ranges score little even when matched, so the true alignment - which matches
# many TIGHT, meaningful ranges - outscores it directly, with no arbitrary
# tolerance window needed. This was verified empirically, not just argued: on both
# available real reference files (see tests / README), this algorithm and the old
# raw-count-with-tie-break algorithm converge on the IDENTICAL absolute byte
# alignment, which confirms the underlying self-validation technique is sound; the
# range-weighted form was kept as the one true implementation because it reaches
# that same answer without depending on any hand-tuned constant, which is what
# principally matters for files this tool has not yet seen (other MultiPlus/Quattro
# models, other firmware).
#
# The search also allows the info array's matching record index to be OFFSET
# (possibly negative) relative to the data array's index 0, not just its byte
# start: BareSettingInfo can be a larger master table of which BareSettingData
# only covers a contiguous window (this is exactly what the reference files
# turned out to contain - the first 34 info records have no corresponding data).


def find_alignment(data, cfg, info_section, data_section):
    info_start, info_end = info_section.payload_start, info_section.end
    data_start, data_end = data_section.payload_start, data_section.end

    info_rec_size = cfg.info_rec_size
    data_rec_size = cfg.data_rec_size
    count_data = (data_end - data_start) // data_rec_size
    if count_data <= 0:
        raise ValueError("BareSettingData payload has no complete records")

    data_raw_vals = [
        struct.unpack_from(cfg.data_struct_fmt, data, data_start + i * data_rec_size)[0]
        for i in range(count_data)
    ]

    best = {"score": -1.0, "base": 0, "k": 0, "in_range": 0, "valid_records": 0}

    for base in range(0, cfg.info_header_search_range):
        info_len = info_end - info_start - base
        if info_len < info_rec_size:
            continue
        n_total = info_len // info_rec_size

        # Precompute every candidate record once per base (not once per (k, i) pair).
        records = []
        for idx in range(n_total):
            off = info_start + base + idx * info_rec_size
            rec = struct.unpack_from(cfg.info_struct_fmt, data, off)
            records.append(dict(zip(cfg.info_fields, rec)))

        for k in range(-count_data, n_total):
            score = 0.0
            in_range = 0
            valid_records = 0
            for i in range(count_data):
                idx = k + i
                if idx < 0 or idx >= n_total:
                    continue
                rec = records[idx]
                scale, offset, mn, mx = rec["scale"], rec["offset"], rec["min"], rec["max"]
                if scale == 0 or mx <= mn:
                    continue
                valid_records += 1
                raw = data_raw_vals[i]
                val = decode_value(raw, scale, offset)
                dmn = decode_value(mn, scale, offset)
                dmx = decode_value(mx, scale, offset)
                lo, hi = (dmn, dmx) if dmn <= dmx else (dmx, dmn)
                if val is not None and lo <= val <= hi:
                    in_range += 1
                    score += 1.0 / (hi - lo + 1)

            if valid_records >= count_data * cfg.min_valid_record_fraction and score > best["score"]:
                best = {"score": score, "base": base, "k": k, "in_range": in_range, "valid_records": valid_records}

    if best["score"] < 0:
        raise ValueError(
            "could not find a plausible BareSettingInfo/BareSettingData alignment "
            "(no candidate met the minimum valid-record fraction)"
        )

    best["count_data"] = count_data
    best["info_start"] = info_start
    best["data_start"] = data_start
    return best


class Setting:
    __slots__ = ("index", "name", "raw", "value", "scale", "offset", "default_raw",
                 "default_value", "min_raw", "max_raw", "enum_label", "default_enum_label",
                 "in_range")


def build_settings(data, cfg, alignment, info_section):
    """Materialize the Setting list for the winning alignment.

    The naming rule ("index i (i>=1) maps to EPROM_NAMES[i-1], index 0 is the unnamed
    sentinel") is defined over the INFO-TABLE record index (`idx` below), not over the
    raw BareSettingData byte position -- BareSettingData can be, and in the reference
    files is, a window into a larger BareSettingInfo master table that starts partway
    through it (`k` != 0). Data positions whose `idx` falls outside the info table's
    valid record range have no corresponding info record at all and are not real
    settings, so they are excluded here (matching the window the original,
    non-k-shifted aligner implicitly saw)."""
    info_start = alignment["info_start"]
    data_start = alignment["data_start"]
    base = alignment["base"]
    k = alignment["k"]
    count_data = alignment["count_data"]
    info_end = info_section.end
    n_total = (info_end - info_start - base) // cfg.info_rec_size

    settings = []
    for i in range(count_data):
        idx = k + i
        if not (0 <= idx < n_total):
            continue

        raw = struct.unpack_from(cfg.data_struct_fmt, data, data_start + i * cfg.data_rec_size)[0]
        info_off = info_start + base + idx * cfg.info_rec_size
        rec = struct.unpack_from(cfg.info_struct_fmt, data, info_off)
        fields = dict(zip(cfg.info_fields, rec))

        s = Setting()
        s.index = idx
        s.name = cfg.setting_name(idx)
        s.raw = raw
        s.scale = fields["scale"]
        s.offset = fields["offset"]
        s.default_raw = fields["default"]
        s.min_raw = fields["min"]
        s.max_raw = fields["max"]
        s.value = decode_value(raw, s.scale, s.offset)
        s.default_value = decode_value(s.default_raw, s.scale, s.offset)
        dmn = decode_value(s.min_raw, s.scale, s.offset)
        dmx = decode_value(s.max_raw, s.scale, s.offset)
        if s.scale != 0 and dmn is not None and dmx is not None and s.max_raw > s.min_raw:
            lo, hi = (dmn, dmx) if dmn <= dmx else (dmx, dmn)
            s.in_range = s.value is not None and lo <= s.value <= hi
        else:
            s.in_range = False

        s.enum_label = cfg.enum_label(s.name, s.raw)
        s.default_enum_label = cfg.enum_label(s.name, s.default_raw)
        settings.append(s)

    return settings


def load_settings(path, cfg=None):
    if cfg is None:
        cfg = load_config()
    with open(path, "rb") as f:  # READ-ONLY. Never opened for writing.
        data = f.read()

    sections = parse_sections(data)
    by_name = sections_by_name(sections)
    info_key = cfg.section_names["info"]
    data_key = cfg.section_names["data"]
    if info_key not in by_name or data_key not in by_name:
        raise ValueError(f"{path}: could not find {info_key}/{data_key} sections")

    info_section = by_name[info_key]
    data_section = by_name[data_key]
    alignment = find_alignment(data, cfg, info_section, data_section)
    settings = build_settings(data, cfg, alignment, info_section)

    serial = find_serial(data)

    meta = {
        "path": path,
        "size": len(data),
        "sections": sections,
        "alignment": alignment,
        "serial": serial,
    }
    return settings, meta


SERIAL_RE = re.compile(rb"[A-Z]{2}\d{4}[A-Z0-9]{4,8}")


def find_serial(data):
    m = SERIAL_RE.search(data)
    return m.group().decode("ascii") if m else None


# ---------------------------------------------------------------------------
# Known flag bits
# ---------------------------------------------------------------------------

def decode_flags(settings, cfg):
    by_name = {s.name: s for s in settings}
    results = []
    for flag in cfg.flags:
        s = by_name.get(flag["setting_name"])
        if s is None or s.raw is None:
            continue
        on = bool((s.raw >> flag["bit"]) & 1)
        results.append({
            "id": flag["id"],
            "label": flag["label"],
            "setting_name": flag["setting_name"],
            "bit": flag["bit"],
            "on": on,
            "setting": s,
        })
    return results


# ---------------------------------------------------------------------------
# CLI - show / diff / flags
# ---------------------------------------------------------------------------

def fmt_value(v):
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.2f}"
    return str(v)


def print_header(meta):
    print(f"file: {meta['path']}")
    print(f"size: {meta['size']} bytes")
    print("sections:")
    for s in meta["sections"]:
        if s.payload_start is None:
            print(f"  0x{s.start:04x}  {s.name!r:35s} (header, no payload)")
        else:
            print(f"  0x{s.start:04x}  {s.name!r:35s} payload=[0x{s.payload_start:04x}, 0x{s.end:04x})")
    a = meta["alignment"]
    pct = (a["in_range"] / a["valid_records"] * 100) if a["valid_records"] else 0.0
    print(
        f"alignment: base={a['base']} k={a['k']} score={a['score']:.4f} "
        f"({a['in_range']}/{a['valid_records']} in-range, {pct:.1f}%; {a['count_data']} total records)"
    )
    if meta["serial"]:
        print(f"serial: {meta['serial']}")
    else:
        print("serial: (not found)")
    print()


def cmd_show(args):
    if not args:
        print("usage: rvsc.py show <file> [--changed-only]", file=sys.stderr)
        return 2
    path = args[0]
    changed_only = "--changed-only" in args[1:]

    cfg = load_config()
    settings, meta = load_settings(path, cfg)
    print_header(meta)

    print(f"{'idx':>4} {'name':35s} {'raw':>7} {'value':>12} {'default':>12} {'min':>7} {'max':>7}  ")
    for s in settings:
        changed = (s.raw != s.default_raw)
        if changed_only and not changed:
            continue
        marker = "*" if changed else " "
        value_str = fmt_value(s.value)
        if s.enum_label:
            value_str = f"{value_str} ({s.enum_label})"
        print(
            f"{s.index:>4} {s.name:35s} {s.raw:>7} {value_str:>12} "
            f"{fmt_value(s.default_value):>12} {fmt_value(s.min_raw):>7} {fmt_value(s.max_raw):>7} {marker}"
        )
    return 0


def cmd_diff(args):
    if len(args) < 2:
        print("usage: rvsc.py diff <fileA> <fileB>", file=sys.stderr)
        return 2
    path_a, path_b = args[0], args[1]
    cfg = load_config()

    try:
        settings_a, meta_a = load_settings(path_a, cfg)
    except (OSError, ValueError) as e:
        print(f"cannot read {path_a}: {e}", file=sys.stderr)
        return 1
    try:
        settings_b, meta_b = load_settings(path_b, cfg)
    except (OSError, ValueError) as e:
        print(f"cannot read {path_b}: {e}", file=sys.stderr)
        return 1

    print(f"A: {path_a}  (alignment score {meta_a['alignment']['score']:.4f})")
    print(f"B: {path_b}  (alignment score {meta_b['alignment']['score']:.4f})")
    print()

    by_index_a = {s.index: s for s in settings_a}
    by_index_b = {s.index: s for s in settings_b}
    common = sorted(set(by_index_a) & set(by_index_b))

    print(f"{'idx':>4} {'name':35s} {'A raw':>7} {'A value':>12} {'B raw':>7} {'B value':>12}")
    n_diff = 0
    for idx in common:
        sa, sb = by_index_a[idx], by_index_b[idx]
        if sa.raw != sb.raw:
            n_diff += 1
            print(
                f"{idx:>4} {sa.name:35s} {sa.raw:>7} {fmt_value(sa.value):>12} "
                f"{sb.raw:>7} {fmt_value(sb.value):>12}"
            )
    if n_diff == 0:
        print("(no differences)")
    print(f"\n{n_diff} setting(s) differ")
    return 0


def cmd_flags(args):
    if not args:
        print("usage: rvsc.py flags <file>", file=sys.stderr)
        return 2
    path = args[0]
    cfg = load_config()
    settings, meta = load_settings(path, cfg)
    print_header(meta)

    flags = decode_flags(settings, cfg)
    print(f"Known flags (confidence: {cfg.raw['flags'].get('confidence', 'unknown')} - see FORMAT.md):")
    for f in flags:
        s = f["setting"]
        print(
            f"  {f['label']:38s} [bit{f['bit']} of {f['setting_name']} (idx {s.index})] = "
            f"{'ON' if f['on'] else 'OFF'}"
        )
    return 0


def main():
    if len(sys.argv) < 2:
        print("usage: rvsc.py <show|diff|flags> ...", file=sys.stderr)
        return 2
    cmd = sys.argv[1]
    args = sys.argv[2:]
    try:
        if cmd == "show":
            return cmd_show(args)
        elif cmd == "diff":
            return cmd_diff(args)
        elif cmd == "flags":
            return cmd_flags(args)
        else:
            print(f"unknown command: {cmd}", file=sys.stderr)
            return 2
    except (OSError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
