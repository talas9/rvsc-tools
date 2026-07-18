# Case Study: Offline Diagnosis of a Non-Charging MultiPlus via `.rvsc` Analysis

This document describes a real diagnostic in which the format specification in
`FORMAT.md` was used to make specific, falsifiable predictions about a device's
configuration before the vendor's own configuration tool was available to check
those predictions, and before the device itself was accessed. Every prediction
was subsequently confirmed exactly. This case study is presented as end-to-end
validation of the specification, not merely as an anecdote: the point is that a
blind prediction derived from format analysis alone matched the vendor's own
software output on every value checked.

No device serial number appears anywhere in this document.

## Symptom

A newly commissioned MultiPlus 24/1200/25-16 was connected to a 25.6 V nominal
LiFePO4 battery bank, built from two 12.8 V packs in series. The inverter
functioned normally. The charger did not: the battery sat at a resting 26.28 V
and 0.0 A of charge current, with the unit's VE.Bus state reporting "Storage."

The vendor's configuration tool (VEConfigure) was not usable in the field: the
operator's platform was macOS on Apple Silicon (ARM), and the vendor tool has no
native support for that platform. This left the operator unable to inspect the
device's live configuration through the normal path.

## Investigation

A `.rvsc` file previously saved from this device (from the commissioning session)
was available. Using the container and settings-decoding rules in `FORMAT.md`
Sections 4-6, this file was parsed independent of any vendor tooling.

## Prediction

Before the vendor tool was run against the device, the following values were
predicted purely from decoding the saved `.rvsc` file against the specification:

| Setting | Predicted value |
|---|---|
| Battery type | Lead-acid (not lithium) |
| Absorption voltage | 24.00 V (at the minimum of its declared range) |
| Float voltage | 24.00 V (at the minimum of its declared range) |
| Storage mode | Enabled |
| Charge current | 18 A |
| Charge curve | Adaptive + BatterySafe |
| Temperature compensation | -32.4 mV/degC |

Both the absorption and float voltage predictions were flagged at prediction time
as sitting at the floor of their allowed range, per the `BareSettingInfo` `min`
field for those indices (Section 5.1/6.2 of `FORMAT.md`) — itself a signal that
these were unlikely to be deliberately chosen values for a 25.6 V nominal
lithium bank, and more consistent with a lead-acid factory or installer default
left unchanged.

## Confirmation

The vendor tool was run against the device once access to compatible hardware
became available. Every predicted value matched the tool's own readout exactly:

| Setting | Predicted | Observed (VEConfigure) | Match |
|---|---|---|---|
| Battery type | Lead-acid | Lead-acid | Yes |
| Absorption voltage | 24.00 V | 24.00 V | Yes |
| Float voltage | 24.00 V | 24.00 V | Yes |
| Storage mode | Enabled | Enabled | Yes |
| Charge current | 18 A | 18 A | Yes |
| Charge curve | Adaptive + BatterySafe | Adaptive + BatterySafe | Yes |
| Temperature compensation | -32.4 mV/degC | -32.4 mV/degC | Yes |

Seven predictions, seven exact matches, zero corrections needed.

## Root Cause

The charge setpoints were the root cause, and they explain the symptom fully
without invoking any hardware fault:

- Absorption and float were both set to 24.00 V, well below the battery bank's
  actual resting voltage of 26.28 V. A charger cannot push current into a battery
  that already sits above the charger's own target voltage; the regulation loop
  correctly commands zero current in that condition.
- Temperature compensation of -32.4 mV/degC further lowered the effective target
  under the ambient temperature observed at the time (36 degC), reducing the
  already-too-low 24.00 V target to an effective 25.46 V — pushing the target
  even further below the battery's resting voltage rather than closer to it.

The unit was not malfunctioning. It was configured, correctly and consistently
with its own settings, to deliver no charge current under these conditions. The
configuration was a leftover lead-acid profile that had never been updated for
the lithium bank actually installed.

## Outcome

The configuration was corrected to match the installed LiFePO4 chemistry:

| Setting | Before | After |
|---|---|---|
| Battery profile | Lead-acid | LiFePO4 |
| Absorption voltage | 24.00 V | 28.40 V |
| Float voltage | 24.00 V | 27.00 V |
| Storage mode | Enabled | Disabled |
| Charge curve | Adaptive + BatterySafe | Fixed |
| DC input low shutdown | 18.60 V | 24.00 V |

After correction, the unit charged at 25.1 A.

## Wider Significance

The practical value of this case extends past the single fault: reading the
saved configuration file offline allowed the fault to be identified and a
corrective plan to be prepared before any vendor tooling was available on the
operator's platform, and it produced an auditable record of exactly what a prior
commissioning engineer had configured, rather than relying on that engineer's
memory or notes.

More importantly for the specification itself, this case constitutes an
end-to-end validation that goes beyond internal self-consistency checks (Section
6.1 of `FORMAT.md`) or cross-referencing against static application data (Section
6.3). Here, the specification was used to generate a set of specific, falsifiable
predictions about a live device's behavior, committed to before the vendor's own
software was available to check them, and every one of those predictions was
independently confirmed by that vendor software once it became available. This
is the strongest form of validation available for a format that carries no
vendor-published specification: agreement between an independently-derived
decoder and the vendor's own tool, established by a blind prediction rather than
after-the-fact comparison.
