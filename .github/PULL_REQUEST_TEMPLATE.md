## Checklist

- [ ] Tests pass locally (`python3 -m unittest discover tests -v`)
- [ ] No device serial numbers or real `.rvsc` files added (only
      `tests/fixture.rvsc`, which is synthetic)
- [ ] If `core/settings.json` changed, `docs/index.html` was regenerated with
      `python3 tools/build_web.py` and the diff is included
- [ ] This PR preserves the read-only invariant (no write/modify/export
      capability added — see CONTRIBUTING.md)
- [ ] No prohibited terminology ("reverse engineer", "decompile",
      "disassemble", "crack") was introduced in docs or comments

## Description

<!-- What does this PR do and why? -->
