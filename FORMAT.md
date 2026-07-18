# The Victron VEConfigure `.rvsc` File Format

## A Technical Specification and Methodology

Status: living document. Confidence levels are stated explicitly per claim. This
specification is derived from publicly distributed software and from format analysis
of files produced by that software when saving configuration settings to disk. It is
an independent technical work and is not published by, endorsed by, or affiliated with
Victron Energy.

## 1. Scope and Purpose

`.rvsc` files are produced by Victron's VEConfigure application when a technician saves
the settings of a Victron inverter/charger (MultiPlus, Quattro, and related products)
to disk, either as a backup or for offline preparation of a device configuration. The
format is undocumented by the vendor. This document specifies the container structure,
the settings encoding, and the methodology used to establish each claim, so that the
specification can be checked, reproduced, and extended by others.

## 2. Reference Configuration

**Verified against: MultiPlus 24/1200/25-16, firmware 2667558, VEConfigure 1.33.**
This is the only configuration against which anything in this document has been
confirmed. Other MultiPlus/Quattro models and other firmware versions are
*expected* to parse correctly, because the format is self-describing (Section 5)
rather than dependent on per-model hardcoded tables — but that expectation is
unconfirmed. Treat every claim about hardware this specification has not been run
against as a hypothesis, not an established fact, until someone checks it.
Reports (with any serial numbers redacted) from other models and firmware
versions are explicitly invited — see README.md "Contributing."

All VERIFIED claims in this document were established against one concrete file,
produced by one concrete device and one concrete version of the vendor tool:

| Property | Value |
|---|---|
| Device | MultiPlus 24/1200/25-16 |
| Firmware | 2667558 |
| VEConfigure version | 1.33 |
| File size | 4562 bytes |
| Device serial number | [redacted] |

This is the only configuration for which every claim below has been directly
confirmed against known-good, independently-sourced values (see Section 6,
"Indexing Anchors"). Claims about other models, other firmware versions, or files of
other sizes are explicitly marked INFERRED or UNKNOWN and should be treated as
hypotheses, not established fact, until independently checked.

## 3. Methodology

Three independent analyses of the format were carried out and cross-checked against
one another. Where the analyses agreed, the result is reported here as VERIFIED.
Where they initially disagreed (this happened for individual flag-bit positions),
the disagreement is noted and the resolution method is given. No claim in this
document rests on a single, unconfirmed observation.

The general approach was:

1. Identify the file's section structure by inspecting the byte layout of several
   `.rvsc` files and looking for repeated, self-similar patterns (length-prefixed
   named blocks).
2. Cross-reference candidate structures against the ordered setting-identifier
   tables present in the publicly distributed VEConfigure application binary
   (Borland Delphi runtime type information exposes symbolic names for many
   internal structures).
3. Where the binary layout was still ambiguous, determine it empirically by
   scoring candidate interpretations against the data itself — most usefully, by
   exploiting the fact that this format is self-describing (Section 5) and
   checking which alignment makes decoded values fall inside their own declared
   range, using a range-width-weighted score rather than a raw count (Section
   6.1 — a naive count is measurably fooled by wide-open spare records).
4. Confirm the resulting field indices against real-world, independently known
   hardware and firmware facts (Section 6), rather than against the file itself,
   to avoid circular validation.
5. Cross-check the whole pipeline against a real diagnostic case where the
   specification was used to make a falsifiable prediction ahead of confirmation by
   the vendor's own tool. See `CASE-STUDY.md`.

## 4. Container Format

All multi-byte integers are little-endian. The file is a flat sequence of
length-delimited sections. Each section has the form:

```
u16   nameLen              length of the section name, in bytes
u8[]  name                 ASCII section name, nameLen bytes, not null-terminated
u32   absolute_end_offset  absolute byte offset (from file start) at which this
                            section's payload ends
u8[]  payload               (absolute_end_offset - current_offset) bytes
```

The `absolute_end_offset` field is not a length; it is an absolute file offset. The
payload of a section runs from immediately after the u32 field to that offset. For
the final section in the file, this offset equals the total file size.

Confidence: HIGH. Section end pointers were confirmed to resolve correctly (i.e.,
land exactly on the start of the next section's name-length field, or exactly at
end-of-file for the last section) at three separate locations within the reference
file.

**Exception:** the first section in the file, `VEConfig setting section file`, does
not follow the template above. It has no `absolute_end_offset` field and no
payload — it is `u16 nameLen` + `u8[] name` and nothing else. It must be recognized
structurally (its position as the first section, and the absence of a length field
before the next section's name begins) rather than identified purely by matching
its name string, since name-only detection gives no signal that this section's
shape differs from every other section's.

Section names observed in the reference file, in order:

1. `VEConfig setting section file` — file-level header/identification section. No
   end-offset or payload (see exception above).
2. `Mk2vscInfo` — metadata related to the MK2 communication interface used to talk
   to the device. 10 ASCII bytes (see naming-artifact note below).
3. `BareSettingInfo` — per-setting metadata (Section 5).
4. `BareSettingData` — per-setting raw values (Section 5).

**Naming artifact:** an earlier draft of this document listed section 2's name as
`Mk2vscInfo=`, with a trailing `=`. That was wrong. The correct name is exactly
`Mk2vscInfo`, 10 bytes, matching `nameLen = 10`. The trailing `=` is not part of
the name — it is the low byte of the `u32 absolute_end_offset` field that
immediately follows the name, which happened to be a printable ASCII character in
the reference file. A tool that identifies section boundaries by scanning for runs
of printable characters, rather than by reading `nameLen` and taking exactly that
many bytes, will pull that offset byte into the apparent name and get a name that
is subtly, silently wrong. This is a useful cautionary example in its own right:
even in a self-describing, length-prefixed format, inferring structure from raw
byte content instead of walking the declared length fields produces a plausible
but incorrect result — always parse `nameLen` first and take exactly that many
bytes.

Confidence: HIGH for presence, order, and framing in the reference file. MEDIUM for
whether this exact section list and order is invariant across all product/firmware
combinations — not independently tested outside the reference file.

An `Assistants` section is expected to exist in files where a VEConfigure
"assistant" (an optional loadable control feature) has been added to the device
configuration. The reference file has no assistant loaded, so no such section was
present to analyze. Its layout is UNKNOWN (see Section 8).

## 5. Settings Model

This is the central design fact of the format and the reason a generic parser is
possible at all: **the format is self-describing.** Each setting's valid range,
default, and decoding scale are stored in the file alongside the data, rather than
being baked into the parsing application. A correct parser does not need
hardcoded, per-model knowledge of what setting N means numerically in order to
decode and validate it — only the mapping from index to a human-meaningful name
requires external knowledge (see Section 7).

### 5.1 BareSettingInfo

Payload is a packed array of fixed-size records, one per setting index, with no
padding between records:

```
struct SettingInfo {
    i16 scale;     // signed
    i16 offset;    // signed
    u16 default;   // raw units
    u16 min;       // raw units
    u16 max;       // raw units
};                 // 10 bytes
```

Confidence: HIGH. This record layout corresponds to the structure documented in
Victron's own published MK2 protocol material for the `CommandGetSettingInfo`
response, which independently corroborates the field layout recovered from the
file.

### 5.2 BareSettingData

Payload is a packed array of `u16` raw values, in the same index order as
`BareSettingInfo`, one value per setting, no padding:

```
struct SettingData {
    u16 raw;
};                 // 2 bytes
```

### 5.3 Decoding

Given a setting's `(scale, offset, raw)` triple from the paired Info/Data records
at the same index:

```
if scale < 0:
    real = (raw + offset) / abs(scale)
else:
    real = (raw + offset) * scale
```

The sign of `scale` selects division (for settings that need sub-integer
precision, e.g. voltages to two decimal places, encoded with `scale = -100`) versus
multiplication (for settings whose natural unit is coarser than one raw unit).
`min`, `max`, and `default` in `SettingInfo` are raw units and must be run through
the same transform to be compared meaningfully against a decoded `real` value.

Confidence: HIGH. Validated in Section 6.

### 5.4 Why self-description matters

Because each setting is fully specified by data present in the file itself, a
parser written against this specification should — in principle — remain valid
across different MultiPlus and Quattro models and firmware revisions without
per-model tables, provided the container and record layout themselves are stable
across those variants (this stability is INFERRED, not verified; see Section 8).
This is a materially different, and more robust, situation than a format that
requires a hardcoded lookup table per product line: a self-describing format
validates its own parse (Section 6.1), and a change in firmware that redefines a
setting's meaning would be expected to also update its recorded scale/min/max,
travelling with the data rather than living out-of-band in a parser that would
otherwise go stale.

## 6. Alignment and Indexing

### 6.1 Determining the value array start by self-validation

The byte offset at which `BareSettingData` values begin was not given directly by
any length field; it had to be determined empirically. The general approach —
score every plausible candidate start offset by how well the resulting decoded
values respect their own declared `[min, max]` bounds — is sound, but two specific
implementation choices matter enough that getting them wrong silently produces a
wrong answer that still looks plausible. Both are documented below as findings,
because a self-describing format validates its own parse only if the validation
is scored correctly.

**Finding 1 — naive "count of values in range" picks the wrong alignment.** The
first, simplest scoring rule tried was: decode the array at each candidate offset
and count what fraction of values fall inside their declared `[min, max]`; the
candidate with the highest fraction wins. This rule fails on real files. Many
setting slots are unused or spare `BareSettingInfo` records, and spare records
tend to carry wide-open bounds (e.g. `min = 0, max = 255`) that accept almost any
raw value by chance — they contribute no real signal but count exactly the same
as a genuinely well-bounded setting. An over-shifted candidate alignment can line
up more of these wide-open, trivially-satisfied records than the correct
alignment does, and out-score the correct alignment even though it is
misinterpreting every meaningful setting.

**Corrected method — range-width-weighted scoring.** Instead of an unweighted
count, each record's contribution to a candidate's score is weighted by
`1 / (decoded window width)`: a tightly-bounded, meaningful setting (a narrow
voltage or current range) contributes far more to the score than a wide-open
spare record, and degenerate wide-open records are excluded from scoring
altogether rather than being allowed to accumulate weight for free. Measured on
the reference file, this makes the correct alignment win decisively and
unambiguously:

- Correct alignment: 143/144 values in range, weighted score **9.08**.
- The alignment that had won under the naive unweighted rule, re-scored with the
  weighted metric: 34/116 values in range, weighted score **0.14**.

A roughly 65x gap in weighted score is not a matter of interpretation. The naive
rule is not merely less precise than the weighted one — on this file it is
confidently wrong, and the weighted rule is what actually recovers the correct
alignment.

**Finding 2 — the comparison must use decoded values, not raw values.** A second,
independently found trap: `min`, `max`, and the candidate value must all be
compared after applying the Section 5.3 scale/offset transform, not as raw `u16`s.
It is easy to compare `raw` directly against `min`/`max` because all three are the
same width and the comparison looks type-correct with no cast required. This is
silently wrong for every setting whose `scale` is not `1`: `min`/`max`/`default`
are declared in raw units, but the quantity that must fall between them is the
*decoded* value. Comparing decoded-against-raw (or raw-against-raw) does not
crash or produce an obviously nonsensical count — most raw values are still small
positive numbers that happen to fall inside typical raw `min`/`max` windows by
coincidence — so the resulting validity flags are wrong for every scaled setting
while the aggregate in-range count still looks plausible. This makes the bug
worse than Finding 1's, not better: it does not announce itself. The fix is to
run `raw`, `min`, `max`, and `default` through the same Section 5.3 transform
(using the record's own `scale`/`offset`) before any comparison is made.

Confidence: HIGH for the reference file, using the corrected (range-width-weighted,
decoded-value) method described above. The technique itself generalizes to any
self-describing settings format that carries per-field validity ranges; whether
the same alignment holds in other file sizes/models is INFERRED and must be
re-verified per file family, not assumed.

### 6.2 Indexing anchors — confirmation against real hardware, not pattern-matching

Two setting indices were pinned to specific real-world meanings by matching their
declared numeric ceiling against the reference device's own nameplate ratings,
which are physical facts external to the file:

- The setting whose declared `max` decodes to exactly 25 corresponds to the
  charger current rating — the reference device's nameplate charger current is
  25 A.
- The setting whose declared `max` decodes to exactly 16.0 (raw scale -10)
  corresponds to the AC input current limit — the reference device's nameplate AC
  input rating is 16 A.

Two additional indices were cross-checked against Victron's publicly documented
default values for 24 V systems, independent of this file:

- Repeated absorption interval: default decodes to 7 days.
- Maximum absorption duration: default decodes to 8 hours.
- Repeated absorption duration: default decodes to 1 hour.
- DC input low shutdown voltage: default decodes to 18.60 V.
- Temperature compensation: default decodes to 0.0324 V/degC.

The significance of the two nameplate matches is that they are independent of the
file's own internal consistency (unlike Section 6.1's self-validation) and
independent of each other (they are two different physical ratings printed on the
same piece of hardware, landing exactly on two different settings' declared
maxima). Two external, unrelated hardware facts each landing exactly on a decoded
value is the anchor that makes the index-to-meaning mapping credible rather than
coincidental.

Confidence: HIGH for the anchored indices listed above. MEDIUM-to-UNKNOWN for
indices without an equivalent external anchor (Section 8).

### 6.3 Setting identifiers from the vendor application

The publicly distributed VEConfigure application binary contains ordered
identifier tables (`EPROM_*`, `EBIT_*` symbolic names) surfaced through Borland
Delphi runtime type information compiled into the executable. Two independent
tables within that single binary were found to reproduce the same setting
ordering, which cross-validates the index-to-name mapping obtained from Sections
6.1 and 6.2 using an entirely separate source (static data in the application
itself, rather than dynamic behavior of the file).

Confidence: HIGH for the indices that appear in both internal tables and align
with Section 6.2's anchors. Indices present in the application's tables but with no
independent hardware or documented-default anchor remain at MEDIUM confidence.

## 7. Known Field Offsets — Reference File Only

The following are concrete byte offsets found in the 4562-byte reference file.
They are provided as worked examples, not as a fixed schema. **These offsets are
specific to this exact file and must not be hardcoded into a general parser.** A
correct implementation locates fields by walking the section framing and the
`BareSettingInfo`/`BareSettingData` index arrays (Section 5), and only uses fixed
offsets as illustration or as a sanity check against a specific known file.

| Offset | Field | Scale | Notes |
|---|---|---|---|
| 0x104d | Absorption voltage | -100 | |
| 0x104f | Float voltage | | |
| 0x1051 | Charge current | 1 | max = 25 (see 6.2) |
| 0x1055 | AC input current limit | -10 | max = 16.0 (see 6.2) |
| 0x105d | Charge characteristic | | 1 = Fixed, 2 = Adaptive, 3 = Adaptive + BatterySafe |
| 0x105f | DC input low shutdown | -100 | |

Flag words (bitfields), confidence MEDIUM (see Section 8 for why):

| Offset | Bit | Meaning |
|---|---|---|
| 0x1049 | 6 | DisableCharge |
| 0x1049 | 11 | EnableReducedFloat (Storage mode) |
| 0x1049 | 14 | WeakACInput |
| 0x10c1 | 4 | LithiumBattery |

Initial analyses of the flag words disagreed on bit-to-meaning mapping at first
pass. The mapping above was resolved by cross-referencing against the VEConfigure
application's own UI form definitions (which bind specific checkbox/control
widgets to specific bit positions), not by inference from the file alone. This is
why numeric settings (HIGH confidence, corroborated by two independent hardware
anchors and a self-validating alignment score) and boolean flags (MEDIUM
confidence, corroborated only by application-side form definitions, with no
external hardware anchor equivalent to Section 6.2) are held to different
confidence levels in this document.

## 8. Integrity: No Checksum Protects the Settings Payload

Finding: no CRC, checksum, or hash covers the `BareSettingInfo` or
`BareSettingData` payload, or the file as a whole.

Evidence:

- The application's file-load path was examined and found to perform only a
  string-tag equality comparison per section (i.e., it checks that a section's
  name matches an expected string) before accepting that section's contents. No
  integrity computation occurs on that path.
- A standard CRC32 lookup table is present in the application binary, but it has
  zero code cross-references — it is linked in as part of a statically-linked
  general-purpose library and is not invoked anywhere in the file load/save path
  for this format.
- Every field in the file that is shaped like a checksum (i.e., a fixed-size
  integer field not otherwise accounted for by Sections 4-5) was tested against
  CRC32, Adler32, and additive/XOR checksums computed over more than ten candidate
  byte ranges of the file. No match was found. This test was repeated across two
  different file snapshots with different content, with the same negative result.

Confidence: HIGH that no integrity mechanism protects this format, within the
scope of what was tested.

**This absence of a checksum is not a license to hand-edit `.rvsc` files bound for
live hardware.** The file's only protection against malformed data reaching a
device is whatever validation VEConfigure itself performs on load and whatever
validation firmware performs on receipt over the MK2 protocol — neither of which
was exhaustively characterized here. A structurally valid but semantically wrong
file (for example, a charge voltage above what a battery chemistry tolerates, or a
current limit above what wiring supports) will not be rejected by a file-format
check, because no such check exists. Section-name equality is not a safety
mechanism. Treat writing to this format as equivalent in risk to editing the
device's settings by hand.

## 9. Confidence Summary

- HIGH: container/section framing; `BareSettingInfo`/`BareSettingData` record
  layout; scale-decoding transform; numeric settings anchored in Section 6
  (validated by self-consistent alignment scoring plus two independent hardware
  nameplate anchors plus two independently-derived vendor identifier tables).
- MEDIUM: individual boolean flag bit positions. Initial independent analyses
  disagreed on flag word offsets before resolution against the application's own
  UI form definitions; no hardware-nameplate-equivalent anchor exists for flags.
- UNKNOWN: the `Assistants` section encoding (no assistant was loaded in the
  reference file, so nothing could be analyzed); setting indices beyond those
  documented in publicly available MK2 protocol material; one setting later
  identified only as "sustain voltage" by observing it rendered in a greyed-out
  (disabled) state in the vendor application's UI, with no independent
  confirmation of its numeric behavior.

## 10. Open Questions

- How the `Assistants` section is encoded when an assistant is present — entirely
  unexamined, since the reference file has none loaded.
- Whether the container and record layout described here hold unchanged for
  files produced by other Victron models (e.g., Quattro) or by other firmware
  versions. Only one model/firmware/file-size combination has been directly
  examined (Section 2).
- Whether setting-index ordering is stable across firmware versions, or whether a
  firmware update could insert, remove, or reorder settings, which would silently
  break offset-based assumptions carried over from one file to another.
- The meaning of setting indices that exist in the file and in the application's
  internal identifier tables but fall outside the range documented in publicly
  available MK2 protocol material — several such indices remain unidentified.

## 11. Attribution and Provenance

This specification was derived from publicly distributed Victron software
(VEConfigure) examined for interoperability purposes, and from format analysis of
`.rvsc` files that software produces, cross-checked against Victron's own publicly
published MK2 protocol documentation and against physical, independently
verifiable facts about a real reference device (Section 2, Section 6.2). It is not
sourced from any non-public Victron material. No device serial number appears in
this document or in the accompanying case study.
