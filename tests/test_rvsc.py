#!/usr/bin/env python3
"""
tests/test_rvsc.py - unittest suite for rvsc.py. Python stdlib only.

tests/fixture.rvsc is entirely SYNTHETIC: it is built byte-by-byte by
build_fixture_bytes() below from made-up section names, records, and values -
it is never a copy of any real device's file and contains no serial number.
setUpModule() (re)writes tests/fixture.rvsc from that builder every run, so
the committed file always matches exactly what these tests exercise.

Run with:  python3 -m unittest tests.test_rvsc -v   (from the repo root)
       or:  python3 tests/test_rvsc.py -v
"""

import struct
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import rvsc  # noqa: E402

FIXTURE_PATH = Path(__file__).resolve().parent / "fixture.rvsc"

HEADER_NAME = b"VEConfig setting section file"
MK2_NAME = b"Mk2vscInfo"
INFO_NAME = b"BareSettingInfo"
DATA_NAME = b"BareSettingData"


def build_container(sections):
    """sections: ordered list of (name: bytes, payload: bytes | None). The
    first entry is always written as a headerless signature section (no end
    offset), matching the real container format and rvsc.parse_sections()."""
    data = bytearray()
    for i, (name, payload) in enumerate(sections):
        data += struct.pack("<H", len(name))
        data += name
        if i == 0:
            continue
        end_off = len(data) + 4 + len(payload)
        data += struct.pack("<I", end_off)
        data += payload
    return bytes(data)


# Synthetic BareSettingInfo records: (scale, offset, default, min, max).
# idx0 is a degenerate sentinel (scale == 0), matching the naming rule that
# index 0 is always unnamed. Index position in this table is meaningful: cfg
# (loaded from the real core/settings.json) resolves setting NAMES purely by
# position via naming.epron_names, regardless of this being a synthetic file
# - epron_names[0] and [1] are "EPROM_FlagsWord0"/"EPROM_FlagsWord1", so
# idx1/idx2 here ARE resolved as flags words by rvsc.py, same as in a real
# file. idx1/idx2 are therefore deliberately built as bitfield-shaped records
# (narrow declared [min, max], but a raw value far outside it - simulating a
# flags word with high bits set, exactly like EPROM_FlagsWord0 in real
# reference files) to exercise the flags-word range-check exclusion. idx3..7
# are non-degenerate, non-flags-word records that deliberately cover both a
# negative scale (divide) and a positive scale (multiply), plus a nonzero
# offset, so scale-decoding is exercised both ways by one fixture.
FIXTURE_INFO_RECORDS = [
    (0, 0, 0, 0, 0),                # idx0: sentinel
    (1, 0, 0, 0, 100),               # idx1: EPROM_FlagsWord0 (bitfield, narrow declared range)
    (1, 0, 0, 0, 100),               # idx2: EPROM_FlagsWord1 (bitfield, narrow declared range)
    (-100, 0, 2500, 2000, 3000),     # idx3: EPROM_UBatAbs - negative scale (divide) -> 25.00
    (10, 0, 5, 1, 10),               # idx4: EPROM_UBatFloat - positive scale (multiply) -> 50
    (1, 0, 1, 1, 3),                  # idx5: EPROM_IBatBulk - scale 1, small enum-shaped range
    (-10, -5, 100, 50, 200),          # idx6: EPROM_UInvSetpoint - negative scale + nonzero offset -> 9.5
    (1, 0, 25, 0, 25),                 # idx7: EPROM_IMainsLimit - scale 1, at its own max
]
FIXTURE_INFO_BASE_HEADER = b"\x00"  # 1 stray byte before record 0 (base == 1)

# BareSettingData: 3 leading "junk" records with no BareSettingInfo counterpart
# (this is what forces the aligner to find a negative k, exactly like the two
# real reference files, where the data array is a window into a larger info
# table), followed by one raw value per info record above. idx1/idx2 (the two
# flags words) are set FAR outside their own declared [0, 100] range on
# purpose - if the aligner's range check did not exclude flags words, this
# fixture would fail to find a plausible alignment at all. Every other
# non-degenerate record is set to its own declared default, i.e. in-range.
FIXTURE_JUNK_DATA = [9999, 1234, 42]
FIXTURE_REAL_DATA = [7, 50000, 60000, 2500, 5, 2, 100, 25]


def build_fixture_bytes(real_data=None):
    """Build one synthetic .rvsc file. Pass a different `real_data` (8 raw u16
    values, one per FIXTURE_INFO_RECORDS entry) to get a second, DIFFERING
    synthetic file for diff tests - still fully synthetic, still no serial."""
    real_data = FIXTURE_REAL_DATA if real_data is None else real_data
    assert len(real_data) == len(FIXTURE_INFO_RECORDS)

    info_payload = FIXTURE_INFO_BASE_HEADER + b"".join(
        struct.pack("<hhHHH", *r) for r in FIXTURE_INFO_RECORDS
    )
    data_payload = b"".join(
        struct.pack("<H", v) for v in (FIXTURE_JUNK_DATA + list(real_data))
    )
    return build_container([
        (HEADER_NAME, None),
        (MK2_NAME, b"\x00" * 8),
        (INFO_NAME, info_payload),
        (DATA_NAME, data_payload),
    ])


def setUpModule():
    FIXTURE_PATH.write_bytes(build_fixture_bytes())


class ContainerParsingTests(unittest.TestCase):
    def test_parses_expected_sections_in_order(self):
        data = build_fixture_bytes()
        sections = rvsc.parse_sections(data)
        names = [s.name for s in sections]
        self.assertEqual(names, [
            HEADER_NAME.decode("latin1"),
            MK2_NAME.decode("latin1"),
            INFO_NAME.decode("latin1"),
            DATA_NAME.decode("latin1"),
        ])
        self.assertIsNone(sections[0].payload_start)  # header has no payload
        for s in sections[1:]:
            self.assertIsNotNone(s.payload_start)
            self.assertGreaterEqual(s.end, s.payload_start)

    def test_section_end_offsets_are_contiguous(self):
        data = build_fixture_bytes()
        sections = rvsc.parse_sections(data)
        # Every section (after the header) should start exactly where the
        # previous one's payload ended.
        for prev, cur in zip(sections, sections[1:]):
            self.assertEqual(cur.start, prev.end)
        self.assertEqual(sections[-1].end, len(data))

    def test_fixture_file_on_disk_matches_builder(self):
        self.assertTrue(FIXTURE_PATH.exists())
        self.assertEqual(FIXTURE_PATH.read_bytes(), build_fixture_bytes())


class AlignmentSelectionTests(unittest.TestCase):
    def test_finds_the_planted_offset_and_shift(self):
        cfg = rvsc.load_config()
        settings, meta = rvsc.load_settings(str(FIXTURE_PATH), cfg)
        a = meta["alignment"]
        self.assertEqual(a["base"], 1)
        self.assertEqual(a["k"], -3)
        # 5 non-degenerate, non-flags-word records (idx3..idx7) were set to
        # their own declared default, i.e. always in-range. idx1/idx2 are
        # flags words with raw values far outside their declared [0, 100] -
        # if they were NOT excluded from scoring, this fixture would either
        # fail to find a plausible alignment at all, or valid_records/in_range
        # would be 7, not 5. See FlagsWordExclusionTests below for a more
        # direct assertion of the exclusion itself.
        self.assertEqual(a["valid_records"], 5)
        self.assertEqual(a["in_range"], 5)

    def test_leading_junk_records_are_excluded_from_settings(self):
        cfg = rvsc.load_config()
        settings, meta = rvsc.load_settings(str(FIXTURE_PATH), cfg)
        # 3 leading junk data records have no BareSettingInfo counterpart and
        # must not appear as settings at all.
        indices = [s.index for s in settings]
        self.assertEqual(indices, [0, 1, 2, 3, 4, 5, 6, 7])

    def test_sentinel_index_zero_is_unnamed(self):
        cfg = rvsc.load_config()
        settings, meta = rvsc.load_settings(str(FIXTURE_PATH), cfg)
        self.assertEqual(settings[0].name, "setting_0")


class FlagsWordExclusionTests(unittest.TestCase):
    """EPROM_FlagsWord0..3 are bitfields, not scalars: individual bits are
    meaningful (see cfg.flags), but the raw word as a whole has no linear
    relationship to a declared [min, max], so range-checking it is a category
    error - it was found breaking the alignment self-check's in-range count
    on real reference files (index 1 / EPROM_FlagsWord0, data offset 0x1049)
    even though every documented flag bit inside it decoded correctly."""

    def test_is_flags_word_matches_by_name(self):
        cfg = rvsc.load_config()
        # idx1/idx2 in the fixture resolve (via the real naming table) to
        # EPROM_FlagsWord0/EPROM_FlagsWord1.
        self.assertTrue(cfg.is_flags_word(1))
        self.assertTrue(cfg.is_flags_word(2))
        # A plain scalar setting must not be misidentified as a flags word.
        self.assertFalse(cfg.is_flags_word(3))

    def test_flags_word_excluded_from_alignment_scoring(self):
        cfg = rvsc.load_config()
        settings, meta = rvsc.load_settings(str(FIXTURE_PATH), cfg)
        a = meta["alignment"]
        # idx1/idx2 (EPROM_FlagsWord0/1) are deliberately out of their own
        # declared [0, 100] range in the fixture data. If the range check
        # were not excluding flags words, they could only ever be counted as
        # "valid but out of range" (pulling in_range below valid_records) or,
        # if some other candidate alignment happened to avoid them, change
        # which offset wins entirely. Neither happens: valid_records and
        # in_range both come out to exactly the 5 real scalar records.
        self.assertEqual(a["valid_records"], 5)
        self.assertEqual(a["in_range"], 5)

    def test_flags_word_setting_in_range_is_none(self):
        cfg = rvsc.load_config()
        settings, meta = rvsc.load_settings(str(FIXTURE_PATH), cfg)
        by_index = {s.index: s for s in settings}
        # idx1/idx2 are flags words: in_range is deliberately N/A (None), not
        # False, so a bitfield is never displayed/counted as "out of range"
        # the way a genuinely out-of-spec scalar setting would be.
        self.assertIsNone(by_index[1].in_range)
        self.assertIsNone(by_index[2].in_range)
        self.assertEqual(by_index[1].name, "EPROM_FlagsWord0")
        self.assertEqual(by_index[2].name, "EPROM_FlagsWord1")
        # Their raw values are preserved untouched despite being numerically
        # outside the declared range - this tool never mutates or hides raw
        # data, it only stops mis-scoring it as a scalar.
        self.assertEqual(by_index[1].raw, 50000)
        self.assertEqual(by_index[2].raw, 60000)
        # A real scalar setting still gets a normal True/False in_range.
        self.assertTrue(by_index[3].in_range)


class ScaleDecodingTests(unittest.TestCase):
    def test_negative_scale_divides(self):
        # scale=-100, offset=0: real = (raw + offset) / 100
        self.assertEqual(rvsc.decode_value(2500, -100, 0), 25.0)

    def test_negative_scale_with_offset(self):
        # scale=-10, offset=-5: real = (raw - 5) / 10
        self.assertEqual(rvsc.decode_value(100, -10, -5), 9.5)

    def test_positive_scale_multiplies(self):
        # scale=10, offset=0: real = raw * 10
        self.assertEqual(rvsc.decode_value(5, 10, 0), 50)

    def test_zero_scale_has_no_linear_meaning(self):
        self.assertIsNone(rvsc.decode_value(1234, 0, 0))

    def test_fixture_end_to_end_decoded_values(self):
        cfg = rvsc.load_config()
        settings, meta = rvsc.load_settings(str(FIXTURE_PATH), cfg)
        by_index = {s.index: s for s in settings}
        self.assertEqual(by_index[3].value, 25.0)   # negative scale
        self.assertEqual(by_index[4].value, 50)     # positive scale
        self.assertEqual(by_index[6].value, 9.5)    # negative scale + offset


class FlagBitDecodingTests(unittest.TestCase):
    def _fake_setting(self, name, raw):
        s = rvsc.Setting()
        s.name = name
        s.raw = raw
        s.index = 0
        return s

    def test_decodes_known_bits_on_and_off(self):
        cfg = rvsc.load_config()
        # bit6 (DisableCharge) and bit14 (WeakACInput) set; bit11 (Storage) clear.
        raw = (1 << 6) | (1 << 14)
        settings = [self._fake_setting("EPROM_FlagsWord0", raw)]
        flags = rvsc.decode_flags(settings, cfg)
        by_id = {f["id"]: f for f in flags}
        self.assertTrue(by_id["EBIT_DisableCharge"]["on"])
        self.assertTrue(by_id["EBIT_WeakACInput"]["on"])
        self.assertFalse(by_id["EBIT_EnableReducedFloat"]["on"])

    def test_missing_setting_is_skipped_not_errored(self):
        cfg = rvsc.load_config()
        flags = rvsc.decode_flags([], cfg)
        self.assertEqual(flags, [])

    def test_enum_label_lookup(self):
        cfg = rvsc.load_config()
        self.assertEqual(cfg.enum_label("EPROM_ChargeCharacteristic", 1), "Fixed")
        self.assertEqual(cfg.enum_label("EPROM_ChargeCharacteristic", 2), "Adaptive")
        self.assertIsNone(cfg.enum_label("EPROM_ChargeCharacteristic", 99))
        self.assertIsNone(cfg.enum_label("EPROM_UBatAbs", 1))  # no enum table for this name


class DiffTests(unittest.TestCase):
    def test_diff_between_two_synthetic_files(self):
        cfg = rvsc.load_config()
        changed_data = list(FIXTURE_REAL_DATA)
        changed_data[3] = 2800   # idx3 (UBatAbs): 25.00 -> 28.00
        changed_data[7] = 18     # idx7 (IMainsLimit): 25 -> 18

        with tempfile.TemporaryDirectory() as tmp:
            path_a = Path(tmp) / "a.rvsc"
            path_b = Path(tmp) / "b.rvsc"
            path_a.write_bytes(build_fixture_bytes())
            path_b.write_bytes(build_fixture_bytes(real_data=changed_data))

            settings_a, _ = rvsc.load_settings(str(path_a), cfg)
            settings_b, _ = rvsc.load_settings(str(path_b), cfg)

        by_index_a = {s.index: s for s in settings_a}
        by_index_b = {s.index: s for s in settings_b}
        diffs = sorted(
            idx for idx in by_index_a
            if idx in by_index_b and by_index_a[idx].raw != by_index_b[idx].raw
        )
        self.assertEqual(diffs, [3, 7])
        self.assertEqual(by_index_a[3].value, 25.0)
        self.assertEqual(by_index_b[3].value, 28.0)

    def test_identical_files_have_no_diff(self):
        cfg = rvsc.load_config()
        with tempfile.TemporaryDirectory() as tmp:
            path_a = Path(tmp) / "a.rvsc"
            path_b = Path(tmp) / "b.rvsc"
            path_a.write_bytes(build_fixture_bytes())
            path_b.write_bytes(build_fixture_bytes())
            settings_a, _ = rvsc.load_settings(str(path_a), cfg)
            settings_b, _ = rvsc.load_settings(str(path_b), cfg)
        diffs = [
            s_a.index for s_a, s_b in zip(settings_a, settings_b)
            if s_a.raw != s_b.raw
        ]
        self.assertEqual(diffs, [])


class MalformedFileTests(unittest.TestCase):
    def test_truncated_file_raises_valueerror_not_crash(self):
        data = build_fixture_bytes()
        cfg = rvsc.load_config()
        with tempfile.TemporaryDirectory() as tmp:
            for cut in (1, 10, 40, len(data) - 5, len(data) - 1):
                path = Path(tmp) / f"truncated_{cut}.rvsc"
                path.write_bytes(data[:cut])
                try:
                    rvsc.load_settings(str(path), cfg)
                except ValueError:
                    pass  # expected for most cuts
                except Exception as e:  # pragma: no cover - fail loudly, don't hide the type
                    self.fail(f"cut={cut} raised {type(e).__name__} instead of ValueError: {e}")

    def test_empty_file_raises_valueerror(self):
        cfg = rvsc.load_config()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "empty.rvsc"
            path.write_bytes(b"")
            with self.assertRaises(ValueError):
                rvsc.load_settings(str(path), cfg)

    def test_garbage_file_does_not_crash(self):
        cfg = rvsc.load_config()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "garbage.rvsc"
            path.write_bytes(b"\xff" * 200 + b"\x00" * 50)
            try:
                rvsc.load_settings(str(path), cfg)
            except ValueError:
                pass  # acceptable: no plausible alignment / no sections found
            except Exception as e:  # pragma: no cover
                self.fail(f"garbage input raised {type(e).__name__} instead of ValueError: {e}")

    def test_missing_sections_raises_valueerror(self):
        cfg = rvsc.load_config()
        data = build_container([
            (HEADER_NAME, None),
            (MK2_NAME, b"\x00" * 4),
        ])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "no_settings.rvsc"
            path.write_bytes(data)
            with self.assertRaises(ValueError):
                rvsc.load_settings(str(path), cfg)


class ReadOnlyTests(unittest.TestCase):
    def test_no_write_mode_open_calls_in_rvsc_source(self):
        source = (REPO_ROOT / "rvsc.py").read_text(encoding="utf-8")
        import re
        # Every open( call in rvsc.py must be read-only ("r" or "rb"); fail if
        # any write/append mode is ever introduced.
        for m in re.finditer(r'open\([^)]*\)', source):
            call = m.group()
            self.assertFalse(
                any(bad in call for bad in ['"w"', "'w'", '"a"', "'a'", '"wb"', "'wb'", '"ab"', "'ab'"]),
                f"found a non-read-only open() call in rvsc.py: {call}",
            )


if __name__ == "__main__":
    unittest.main()
