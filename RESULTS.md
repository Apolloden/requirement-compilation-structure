# ArcFlash results

## Best so far: ArcFlash v4

ARC-Bench run of 2026-11-14 15:53, archive `arcflash-2026-11-14-v4.zip` (commit `f64e202`, branch `arcflash-v4`), runtime `python`, billed to our own API key.

| Score | Test pass rate | Model cost | Runtime (sum of six sites) | Tokens |
|---|---|---|---|---|
| **71.51** | 56.1 % (289 / 515) | ¥10.24 | 604 min 16 s | 9.451 M |

| Website | Tests passed | Features | Score | Runtime | Tokens | Cost | v2 tests |
|---|---|---|---|---|---|---|---|
| 12306 railway travel | 45.8 % (65/142) | 41.9 % | 57.84 | 100 min 37 s | 2.770 M | ¥2.51 | 108 |
| BookStack knowledge base | **100 % (35/35)** | 100 % | 129.33 | 68 min 6 s | 0.642 M | ¥1.07 | 29 |
| Ctrip travel services | 63.2 % (96/152) | 59.8 % | 83.72 | 119 min 12 s | 2.764 M | ¥2.29 | 81 |
| Keep personal note management | 97.0 % (32/33) | 93.8 % | 128.95 | 44 min 29 s | 0.326 M | ¥0.74 | 22 |
| PrestaShop B2C storefront | 45.3 % (39/86) | 36.4 % | 56.61 | 142 min 59 s | 1.465 M | ¥1.70 | 0 |
| Stack Overflow developer knowledge sharing | 32.8 % (22/67) | 26.9 % | 38.20 | 128 min 53 s | 1.484 M | ¥1.94 | 50 |

Notes:
- The score beats v2 (68.44) at the same number of passed tests (289 against 290). The gain comes from cost: ¥10.24 against ¥16.49 gives a factor of (16.49 / 10.24)^0.1 = 1.05.
- Four sites gained 70 tests (PrestaShop +39, Ctrip +15, Keep +10, BookStack +6); 12306 (−43) and Stack Overflow (−28) lost 71. In both of those, the grader never completed the main create flow: no order or passenger was created on 12306 (v2: 11 orders) and no question on Stack Overflow (v2: 22 or more).
- Runtime grew because the endpoint was slow (11 to 17 output tokens per second against 190 to 250 in the v2 run), not because the agent did more work: v4 wrote fewer output tokens than v2 on every site except BookStack.
- Analysis and the next generation plan: `docs/6-arclaser/` in the course project folder.

## ArcFlash v2

ARC-Bench run of 2026-11-13, archive `arcflash-2026-11-13-v2.zip`, runtime `python`, billed to our own API key.

### Overall

| Score | Test pass rate | Model cost | Runtime | Tokens |
|---|---|---|---|---|
| 68.44 | 56.3 % (290 / 515) | ¥16.49 | 164 min 30 s | 14.374 M |

### By website

| Website | Tests passed | Features | Score | Runtime | Tokens | Cost |
|---|---|---|---|---|---|---|
| 12306 railway travel | 76.1 % | 72.0 % | 97.88 | 38 min 49 s | 3.985 M | ¥3.47 |
| BookStack knowledge base | 82.9 % | 83.3 % | 109.82 | 3 min 46 s | 0.415 M | ¥0.69 |
| Ctrip travel services | 53.3 % | 41.2 % | 63.65 | 55 min 0 s | 4.856 M | ¥5.48 |
| Keep personal note management | 66.7 % | 68.8 % | 82.30 | 15 min 45 s | 0.506 M | ¥1.07 |
| PrestaShop B2C storefront | 0.0 % | 0.0 % | 0.00 | 23 min 45 s | 2.364 M | ¥2.94 |
| Stack Overflow developer knowledge sharing | 74.6 % | 71.2 % | 90.71 | 27 min 25 s | 2.247 M | ¥2.84 |

### Notes

- Score = pass rate divided by a cost factor. Pass rate dominates, so the 0 % on PrestaShop costs more than any saving elsewhere.
- PrestaShop passed 0 of its tests even though the run used 24 minutes and 2.36 M tokens. Cause (found in the run download): the global navigation was rendered as a region, not a `<nav>`, and the grader checks it at the start of every test. Analysis of all three downloaded runs: `docs/4-arcflash-update/evidence.md` in the course project folder.
- The update on branch `arcflash-update` (change log `docs/3-arcflash/arcflash-changes.md`, "ArcFlash update") was checked locally with grader-style suites: BookStack 29 → 35 of 35, PrestaShop 0 → 20 of 21, Ctrip 8 → 8 of 18 (the menu chain fixed, two other spots broke on that run). v3 then scored 30.72 on ARC-Bench (slow endpoint, no repairs ran); v4 fixed that (above).
- For comparison, the unchanged upstream agent got 4 of 35 tests (11.4 %) on BookStack, and the earlier ArcFable work was only checked locally.
- Local grader-style tests on the BookStack lite task were 12 / 12 for this agent before the run, with an estimated cost of about ¥0.20 and 10 minutes.
