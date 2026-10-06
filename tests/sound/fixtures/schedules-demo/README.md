# DEMO schedules fixtures (synthetic)

Byte copies of DEMO outputs of the schedules package (seed `DEMO-o4.4.1-example`; every
file has `"demo": true`): `permutation.json` of units A-C01 and B-C01 (#29, format 2) and
visit schedules of the person slots A-C01-L01 (D0, D7) and B-C01-M1 (V1, W1, W4) (#30,
format 1). `tests/sound/test_package.py` seals synthetic packages with them and checks the
permutation matrix against `av_sound.grammar`. Not study material. Refresh them when the
schedules formats change (the visit schedules carry the SHA-256 of their
`permutation.json`).
