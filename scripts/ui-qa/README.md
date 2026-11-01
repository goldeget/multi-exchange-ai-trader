# Reference image comparison

Run `python scripts/ui-qa/compare_references.py --captures <absolute browser-evidence directory> --prefix final`. Requires Pillow. The manifest maps all 24 originals to routes, fixtures and target CSS viewport sizes. Input naming is `final-pc-01_overview-viewport.jpg` or `final-pc-01_overview.jpg`, equivalently mobile and the other page names. Viewport captures are preferred; a full-page capture is cropped to its first viewport without resizing.

The tool verifies original SHA-256, scales only the reference proportionally to 1440×1024 or 390×844, centers any 1px crop/pad vertically, and creates side-by-side, 50% overlay and difference evidence in a new directory. It refuses to overwrite existing output. Incorrect capture widths are reported rather than stretched. JPEG differences include compression noise. Metrics are descriptive, never a screenshot test pass or evidence of 2px boundary accuracy.

Review common shell, typography, units, warning/CTA, order fields, chart dimensions and mobile reflow against `docs/ui-reference/visual-exceptions.json`. Preserve example-data labels. Keep actual private-account evidence local. Only independently reviewed actual DOM PNG captures in the same OS/browser/font/DPR may become later Playwright regression baselines. Never automatically update baselines to dismiss failures.
