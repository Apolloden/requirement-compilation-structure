# ArcFlash results

ARC-Bench run of 2026-11-13, archive `arcflash-2026-11-13-v2.zip`, runtime `python`, billed to our own API key.

## Overall

| Score | Test pass rate | Model cost | Runtime | Tokens |
|---|---|---|---|---|
| 68.44 | 56.3 % (290 / 515) | ¥16.49 | 164 min 30 s | 14.374 M |

## By website

| Website | Tests passed | Features | Score | Runtime | Tokens | Cost |
|---|---|---|---|---|---|---|
| 12306 railway travel | 76.1 % | 72.0 % | 97.88 | 38 min 49 s | 3.985 M | ¥3.47 |
| BookStack knowledge base | 82.9 % | 83.3 % | 109.82 | 3 min 46 s | 0.415 M | ¥0.69 |
| Ctrip travel services | 53.3 % | 41.2 % | 63.65 | 55 min 0 s | 4.856 M | ¥5.48 |
| Keep personal note management | 66.7 % | 68.8 % | 82.30 | 15 min 45 s | 0.506 M | ¥1.07 |
| PrestaShop B2C storefront | 0.0 % | 0.0 % | 0.00 | 23 min 45 s | 2.364 M | ¥2.94 |
| Stack Overflow developer knowledge sharing | 74.6 % | 71.2 % | 90.71 | 27 min 25 s | 2.247 M | ¥2.84 |

## Notes

- Score = pass rate divided by a cost factor. Pass rate dominates, so the 0 % on PrestaShop costs more than any saving elsewhere.
- PrestaShop passed 0 of its tests even though the run used 24 minutes and 2.36 M tokens. Cause (found in the run download): the global navigation was rendered as a region, not a `<nav>`, and the grader checks it at the start of every test. Analysis of all three downloaded runs: `docs/4-arcflash-update/evidence.md` in the course project folder.
- The update on branch `arcflash-update` (change log `docs/3-arcflash/arcflash-changes.md`, "ArcFlash update") was checked locally with grader-style suites: BookStack 29 → 35 of 35, PrestaShop 0 → 20 of 21, Ctrip 8 → 8 of 18 (the menu chain fixed, two other spots broke on that run). Not yet graded on ARC-Bench.
- For comparison, the unchanged upstream agent got 4 of 35 tests (11.4 %) on BookStack, and the earlier ArcFable work was only checked locally.
- Local grader-style tests on the BookStack lite task were 12 / 12 for this agent before the run, with an estimated cost of about ¥0.20 and 10 minutes.
