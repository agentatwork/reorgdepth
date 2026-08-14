#!/usr/bin/env python3
"""Boundary cases for the CloseConfsForCapacity transcription. Run: python3 test.py"""
import sys
from reorgdepth import close_confs, scale_num_confs

CASES = [
    (1, 3), (1_000_000, 3), (5_000_000, 3),
    (11_184_809, 3), (11_184_810, 4),          # 3 -> 4
    (13_981_012, 4), (13_981_013, 5),          # 4 -> 5
    (16_777_214, 5), (16_777_215, 6),          # 5 -> 6, at maxChannelSize
    (16_777_216, 6),                           # wumbo short-circuit
    (500_000_000, 6),                          # the disclosure's 5 BTC example
]

fails = 0
for cap, want in CASES:
    got = close_confs(cap)
    if got != want:
        print(f"FAIL {cap:>12,} -> {got}, want {want}")
        fails += 1

# The floor is what makes close confs differ from funding confs.
assert scale_num_confs(1_000_000) == 1, "raw scaling should be 1 for a small channel"
assert close_confs(1_000_000) == 3, "close path must apply the floor of 3"

print(f"{len(CASES)} cases, {fails} failures")
sys.exit(1 if fails else 0)
