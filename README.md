# rvsc-tools

**This is a viewer. It does not write configuration files.** Reading a `.rvsc` is
safe; writing one is not — a malformed configuration sent to an inverter/charger
can damage batteries or equipment. Modification is deliberately out of scope. To
change settings use Victron's own VEConfigure and VRM Remote VEConfigure, which
validate what they write.

**Verified against: MultiPlus 24/1200/25-16, firmware 2667558, VEConfigure 1.33.**
That is the only configuration this project has been directly confirmed against.
Other MultiPlus/Quattro models and firmware versions are expected to parse
correctly, because the underlying file format is self-describing (see
[FORMAT.md](FORMAT.md) §5) rather than dependent on per-model lookup tables —
but that is unconfirmed, not established. If you try this against a different
model or firmware, reports (with any serial numbers redacted) are welcome; see
"Contributing" below.

## What this is

`rvsc-tools` reads Victron VEConfigure `.rvsc` settings files — the configuration
backups VEConfigure saves to disk for MultiPlus, Quattro, and related
inverter/chargers — and decodes them into a human-readable list of settings. It
ships two ways to do that:

- **A web viewer**, a single self-contained page with no build step, no server,
  and no dependencies.
- **A command-line tool** (`rvsc.py`) for local/offline use, scripting, and
  batch inspection.

Both are strictly read-only. Neither can save, modify, or export a changed
`.rvsc` file.

## What this is not

- Not affiliated with, endorsed by, or sponsored by Victron Energy B.V. See
  [NOTICE.md](NOTICE.md).
- Not a configuration editor. It cannot save, modify, or export a `.rvsc` file.
- Not a substitute for VEConfigure or VRM Remote VEConfigure. Use those vendor
  tools, which validate what they write, for anything that changes device
  settings.

## Why this exists

Victron's own VEConfigure application does not run natively on macOS or Linux,
which leaves owners of Victron equipment unable to inspect a saved configuration
on those platforms without a Windows VM or physical access to the device through
VRM. This project lets you read a `.rvsc` file you already have — from a backup,
from a commissioning session, or exported from VRM Remote VEConfigure — on
whatever computer you're using, without installing anything.

See `CASE-STUDY.md` for a real example of this being useful: diagnosing a
non-charging MultiPlus offline, from a saved `.rvsc` file alone, before vendor
tooling was available.

## Live web viewer

**[https://talas9.github.io/rvsc-tools/](https://talas9.github.io/rvsc-tools/)**

Open it, drop in a `.rvsc` file (or two, to compare). That's the whole
interface. It runs entirely in your browser:

- No file is uploaded anywhere — parsing happens with the browser's
  `FileReader` API, entirely on your machine.
- No network requests of any kind: no analytics, no CDN, no external fonts. The
  page is a single self-contained HTML file. You can verify this yourself with
  your browser's network inspector, or by loading the page and then
  disconnecting from the internet before selecting a file.
- Works offline once loaded; can be saved and opened as a local file.

Features:

- Single-file view: every setting, its raw and decoded value, default, valid
  range, and whether it differs from default.
- **Diff mode**: load two files side by side and see only the settings that
  differ between them — useful for comparing a working configuration against a
  broken one, or auditing what a commissioning engineer actually changed.
- Known boolean flags (battery type, storage mode, charge behavior) decoded
  into plain language.
- A confidence indicator per value (see [Confidence levels](#confidence-levels)).

## CLI usage

A companion command-line tool, `rvsc.py`, is included for local/offline and
scripting use:

```sh
python3 rvsc.py <file.rvsc>                           # shorthand for `show <file.rvsc>`
python3 rvsc.py show <file.rvsc>                       # print settings grouped by VEConfigure tab/group
python3 rvsc.py show <file.rvsc> --changed-only         # print only settings that differ from default
python3 rvsc.py show <file.rvsc> --advanced             # technical table: identifiers, offsets, scale, min/max
python3 rvsc.py show <file.rvsc> --show-unused          # also print settings normally hidden as spare/padding
python3 rvsc.py diff <fileA.rvsc> <fileB.rvsc>          # show only settings that differ between two files
python3 rvsc.py flags <file.rvsc>                       # print decoded boolean flags
```

The default `show` view mirrors VEConfigure's own Simple view: settings are
grouped under VEConfigure's own tab and group headings, printed with their
human label (never a bare `EPROM_*`/`EBIT_*` identifier) and an interpreted
value — enums as their full option text, booleans as "Enabled"/"Disabled",
numbers with their unit. Only settings this project has confirmed a label
for are shown this way; everything else appears under an "Unmapped" section,
with obvious spare/padding slots hidden by default. Values that differ from
their factory default are marked, with the default value shown alongside in
muted text. Colour is used automatically on a terminal; set `NO_COLOR=1`, or
pipe the output, to disable it. `--advanced` (or `--raw`) prints the previous
technical table instead.

Run `python3 rvsc.py --help` (or `python3 rvsc.py <command> --help`) for the
current, authoritative list of options — this README describes the common case,
not the full interface.

## File format

Full specification, methodology, and confidence ratings: **[FORMAT.md](FORMAT.md)**.
Summary:

`.rvsc` is a flat, little-endian, length-delimited container. Each section:

| Field | Type | Meaning |
|---|---|---|
| `nameLen` | `u16` | length of the section name, in bytes |
| `name` | `u8[nameLen]` | ASCII section name (not null-terminated) |
| `absolute_end_offset` | `u32` | absolute byte offset, from file start, where this section's payload ends |
| `payload` | `u8[]` | `absolute_end_offset − current_offset` bytes |

The file-level signature section (`VEConfig setting section file`) is the one
exception: it has no end-offset or payload, just a name.

Two sections carry the actual settings, in parallel index order:

| Section | Contents |
|---|---|
| `BareSettingInfo` | one 10-byte record per setting: `i16 scale, i16 offset, u16 default, u16 min, u16 max` |
| `BareSettingData` | one `u16` raw value per setting |

Decoding a raw value:

```
if scale < 0:  real = (raw + offset) / abs(scale)
else:          real = (raw + offset) * scale
```

The offset at which the `BareSettingData` value array begins relative to
`BareSettingInfo`'s index range is not given directly by any length field — it's
recovered by scoring candidate alignments against each setting's own declared
`[min, max]`, weighted to favor tightly-bounded (meaningful) settings over
wide-open or degenerate ones, and picking the best fit. See FORMAT.md §6 for the
full methodology and the reasoning against a naive "most matches wins" approach,
which was tested and found to pick the wrong alignment on unused/spare records.

## Confidence levels

Not every decoded value carries the same certainty:

- **HIGH** — numeric settings (voltages, currents) validated against the file's
  own declared `min`/`max` range, and in the reference case cross-checked
  against independent, external facts (device nameplate ratings, documented
  vendor defaults). Shown with a solid confidence marker in the viewer.
- **LOWER** — individual boolean flag bits (battery type, storage mode, etc.).
  These meanings were established for one reference MultiPlus profile and are
  not re-derived from each file's own data the way numeric settings are, so
  they may not hold for every model or firmware version. Shown explicitly
  labelled "lower confidence" in the viewer and in FORMAT.md.

Full detail, including which specific values are anchored against which
external facts: FORMAT.md §6 and §9.

## Development

### Regenerating the web viewer

The web viewer (`docs/index.html`) is built from `core/settings.json`, the
setting-index-to-name/scale/confidence mapping. After editing
`core/settings.json`, regenerate the viewer:

```sh
python3 tools/build_web.py
```

Do not hand-edit `docs/index.html`'s generated data directly; edit
`core/settings.json` and rebuild.

### Running tests

The test suite is Python stdlib `unittest` — no test framework needs to be
installed:

```sh
python3 -m unittest discover tests -v
```

## Legal / not affiliated

See **[NOTICE.md](NOTICE.md)** for the full legal notice: this project is not
affiliated with Victron Energy B.V.; the format was examined for
interoperability purposes only, under applicable interoperability law; no
Victron source code, binaries, or copyrighted assets are included; and a safety
warning about battery/inverter configuration in general.

## License

MIT. See [LICENSE](LICENSE).

## Contributing

Issues and pull requests are welcome, particularly `.rvsc` files (with any
serial numbers or other identifying data redacted) from other Victron models —
the format specification in FORMAT.md has so far only been directly verified
against one MultiPlus model/firmware combination, and confirming or correcting
it against other devices is the most useful contribution right now. See
FORMAT.md §8, "Open Questions," for specific known gaps.
