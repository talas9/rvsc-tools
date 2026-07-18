# Legal Notice & Disclaimer

## Not affiliated with Victron Energy

This is an independent, community project. It is **not** affiliated with,
endorsed by, sponsored by, or connected to Victron Energy B.V. in any way.

"Victron", "Victron Energy", "VEConfigure", "VE.Bus", "MultiPlus", "Quattro",
"Cerbo GX" and "VRM" are trademarks or registered trademarks of Victron Energy
B.V. They are used here solely for identification and descriptive purposes
(nominative fair use), to state what file format this software reads.

## Purpose: interoperability

This software exists to let owners of Victron equipment **read their own
configuration files on their own computers**, including on platforms where the
vendor's tooling is not available (macOS, Linux).

This format analysis was carried out for the sole purpose of achieving
interoperability with an independently created program, as permitted under
Article 6 of EU Directive 2009/24/EC (the Software Directive), the equivalent
provisions of applicable national law, and 17 U.S.C. Section 1201(f) in the
United States.

No technological protection measure was circumvented. The file format contains
no encryption, no digital signature, and no access control. It is a plain
binary settings file.

## What this project does NOT contain

- No Victron Energy source code
- No Victron Energy binaries, libraries, or installers
- No Victron Energy documentation, artwork, or other copyrighted assets
- No means of bypassing licensing, authentication, or access control

Descriptive setting identifiers (e.g. `EPROM_UBatAbsorption`) are derived from
publicly distributed software, for the interoperability purpose stated above. Such functional identifiers are, in the authors' understanding, not
protectable expression. They are included so that decoded values can be
labelled meaningfully rather than as opaque indices.

## Read-only by design

This tool **does not write** configuration files. It opens files in read-only
mode. Writing configuration to Victron hardware should be done with
VEConfigure and VRM Remote VEConfigure — the vendor's own tools, which perform
validation this project does not attempt to replicate.

## No warranty — and a safety warning

This software is provided "as is", without warranty of any kind, express or
implied. See LICENSE (MIT).

**Battery systems are dangerous.** Incorrect configuration of an
inverter/charger can damage batteries, destroy equipment, or cause fire.
Decoded values from this tool may be wrong. Do not rely on this software for
any safety-critical decision. Always verify against the vendor's own tools and
the battery manufacturer's documentation before changing anything.

The authors accept no liability for any loss or damage arising from use of
this software or reliance on its output.

## Takedown

If Victron Energy B.V. believes this project infringes their rights, please
open an issue or contact the repository owner. We will engage in good faith
and remove or modify anything that is a legitimate concern.
