# Pre-registration provenance manifest

This file records the tagged points at which the analysis plan for the binary-separation
experiment was fixed. The tags live in the project's development repository; this public
release is a squashed snapshot and carries no tags of its own, so the metadata is recorded
here instead. Nothing below was created retroactively: every value is read from the
existing annotated tags and can be checked by anyone with access to that repository.

Both tags are **annotated**, so `git rev-parse <tag>` returns the tag object rather than
the commit. Use `git rev-parse '<tag>^{commit}'` to obtain the commit identifiers below.

## `preg-class1` — Class 1 pre-registration (confirmatory)

- commit: `d8238322758cfd224dbf37c26fc678255eb17c9f`
- annotated tag object: `278aca757d6aa79df17b2a6536d7cb3d58c87140`
- commit date: `2026-05-18T19:48:25-03:00`
- commit subject: P16-B (code): killer demo setup + 3sigma baseline + reproducibility
- `analysis/killer_demo_preregistration.md` at this tag:
  - git blob: `515220765299ce44178830c9bcab338bf280ff9f`
  - SHA-256: `215a308d536a31a0389b4c5bb37a03a082f769c437467795d94c567c9fa6fcfc`
  - size: 4302 bytes

## `amend-class23` — 2026-05-19 amendment adding Classes 2 and 3 as exploratory

- commit: `46cd5986f66d189a9d63714b993f577a625fb69c`
- annotated tag object: `d5b8a9a08473b3a33e6513f64372bcbfc630e0e5`
- commit date: `2026-05-19T10:13:44-03:00`
- commit subject: P17.2 (code): bug fixes + 3-class extension across docs and code
- `analysis/killer_demo_preregistration.md` at this tag:
  - git blob: `87cf7d62d9de7519e8f7e31feee5c8e30c3353f6`
  - SHA-256: `ad21dcbecc608f105b1c4b0368de760846ffcecca4afed13f36288b964c9ff55`
  - size: 6776 bytes

## Current file in this release

- SHA-256: `ad21dcbecc608f105b1c4b0368de760846ffcecca4afed13f36288b964c9ff55`
- size: 6776 bytes

The released file is the amended version. Its digest matches the `amend-class23` entry
above when the two are identical; any later editorial change to that file would show up
as a differing digest here.
