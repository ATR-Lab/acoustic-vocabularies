# Synthetic examples (DEMO, not study material)

## `package-demo/`

The JSON files of the sealed synthetic Study A package `DEMO-BOOK-P1`
([`docs/interfaces/package-format.md`](../../docs/interfaces/package-format.md)):
`manifest.json`, `answers.json`, `audio.json`, `permutation.json` (the DEMO permutation
A-C01 of the schedules stack) and `allocation.json`. The book uses the synthetic recipes
of `av_sound.synthetic` for P1 with the meanings of that permutation. It is a format
example and must never be used with participants (`"demo": true`).

The 34 WAV files listed in `manifest.json` are not committed (no binary files in git).
Rebuild the complete package, and a synthetic dyad package, with:

```bash
uv run --project sound python sound/tools/build_example_package.py --out OUT --dyad
uv run --project sound python sound/tools/build_example_package.py --check  # committed JSON
```

CI uploads the complete packages as the `package-demo` artifact. `--write` rewrites
the committed JSON after an intended format change; say why in the pull request.
