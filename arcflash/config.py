"""Every constant of ArcFlash, in one place (plan Part A sections 4.6, 8 and 9)."""

MODEL = "deepseek-flash"
THINKING = False
TEMPERATURE = 0.2
RESTART_TEMPERATURE = 0.6
# Repair rounds after every failure group was tried and the rotation started over (the prompt repeats): at 0.2 the
# model answered the same prompt with the same failed fix (ArcZenith BookStack lane: 4 of 10 repair answers
# byte-identical to earlier ones). Other rounds stay at TEMPERATURE.
RETRY_TEMPERATURE = 0.7
MAX_PARALLEL_CALLS = 8           # backend and every page module at once (Task 10; modules hold at most 6 pages)
NOTES_PARALLEL_CALLS = 4         # screenshot notes, beside the plan: they leave slots free for it (Task 9)
MAX_BACKGROUND_CALLS = 2     # the test writer's own slots: it never delays notes, plan, code or repairs
MAX_TOKENS = {"vision": 800, "plan": 24000, "backend": 32000, "frontend": 32000, "tests": 16000, "repair": 8000,
              "quick_fix": 2000}
MAX_CONTINUATIONS = 2
REPEAT_GUARD_PAIRS = 8          # a repair answer stops after this many no-op or repeated SEARCH/REPLACE pairs in a row
RETRY_WAITS = (15, 30, 60, 120)
STREAM_IDLE_SECONDS = 180
RUNAWAY_BLANK_CHARS = 2000
RUNAWAY_REPEAT_LINES = 30
RUNAWAY_NUMBER_RUN = 300     # an answer ending in this many comma-separated integers is counting, not planning
RUNAWAY_WORD_CHARS = 400     # an answer ending in one word this long (letters, digits, _) has run away; real ones stay under 150
CACHE_KEY_CHARS = 20_000     # a call without a usage record is booked with the cache hit of the last call that began the same way
CONTINUE_PROMPT = ("Your previous answer was cut off; it ends with the last complete line shown above. Continue with the next "
                   "line: output only the rest of the answer, starting at the beginning of a new line, without repeating anything.")
# A cut answer is continued from its last complete line: continued mid-string, the model dropped or repeated a few
# characters at every seam (ArcLaser 12306: the plan failed to parse at the seam after 68 min). Only when that line
# ends within this many characters of the cut; a one-line answer (minified JSON) is continued as it is.
CONTINUE_LINE_CHARS = 4000
RUNAWAY_PROMPT = "Your previous answer turned into blank or repeated text. Answer again, briefly and completely."
# After the last attempt the best version is repaired until the time or cost budget ends, or until every scenario
# passes (ArcLaser v2 graded: four sites stopped at 106 to 141 of 180 min on round counts; ArcLaser v3 graded: Ctrip's
# continued repairs went 83 → 129 own tests, 123 graded against v2's 81). This only bounds a fast endpoint.
MAX_FINAL_ROUNDS = 40
TEST_FIX_TRIES = 3              # small fix calls for a generated test file that does not load (ArcLaser v3 12306)
MAX_REPAIR_ROUNDS = 14          # short aimed rounds (8k tokens, three failure groups each); ArcLaser graded runs stopped at 37 to 118 of 180 min
QUICK_FIX_LOG_LINES = 40         # a build or start failure: the log tail and this many lines on each side of a named line
FOCUS_GROUPS = 3                # failure groups a repair round shows in full, largest first
MAX_ROUNDS_WITHOUT_GAIN = 3     # in a row; each such round sets its failure groups aside, the next aims at others
# The final repairs (until the budget) end after this many rounds in a row without gain. At about 410 passed tests and
# ¥15, ¥1 weighs as much as 2.4 tests in the score (dS/S = 1.1 dN/N - 0.1 dC/C). ArcLaser v3 graded Ctrip gained after
# at most 2 rounds without gain; the ArcZenith BookStack lane sat at 32/35 for 14 final rounds (one test it ran cannot pass).
MAX_FINAL_ROUNDS_WITHOUT_GAIN = 6
MAX_ATTEMPTS = 2
GOOD_ENOUGH_PASS_RATIO = 0.5
MAX_RUN_SECONDS = 200 * 60       # no new repair round or restart after this (runtime is not scored; runs of 370 and 615 min were accepted)
RESCUE_UNTIL_SECONDS = 215 * 60  # an app that does not start still gets repair rounds until this
# No new model request after this; one in flight finishes and the best version ships. Worst case about 271 min:
# 225 + a request in flight (the endpoint ends every request by 1,201 s, 20 min) + a check (build 300 s, start 30 s,
# browser 1,200 s; about 25 min). Repair answers are capped at 8k tokens, so rounds are short.
HARD_STOP_SECONDS = 225 * 60
REPAIR_RESERVE_SECONDS = 4 * 60
NOTES_WAIT_SECONDS = 300         # code generation waits at most this long (from the notes' start) for screenshot notes
ATTEMPT_RESERVE_SECONDS = 15 * 60
# Caps only (score ÷ 1.07 per doubled cost): the repairs now run until the time budget. ArcLaser v2 graded spent
# ¥4.56 of Stack Overflow's ¥5.28 (estimated) with fewer rounds.
MIN_RUN_COST_CNY = 6.0
COST_PER_SCENARIO_CNY = 0.12
RESTART_COST_SHARE = 0.6
# Billed ÷ our list-price estimate: ArcLaser v2 graded ¥13.11 billed for ¥9.54 at list prices (1.37); was 2.0.
PLATFORM_COST_FACTOR = 1.4
CHARS_PER_TOKEN = 3.6         # measured on v3 Ctrip and PrestaShop calls (3.56 to 3.69); used only when a call has no usage
PRICES_CNY_PER_MILLION = {"input": 1.0, "cached": 0.02, "output": 4.0}
SCENARIOS_PER_TEST_CALL = 20
MAX_PAGES_PER_MODULE = 6         # modules are cut by requirement group, then to this size (Task 8)
REPAIR_CONTEXT_CHARS = 240_000
REPORT_CHARS = 30_000
IMAGE_MAX_SIDE = 1600
PLAYWRIGHT_VERSION = "1.57.0"   # the ARC-Bench runner's version (preflight.json); its Chromium is preinstalled there
# Playwright version → the Chromium build it drives. The harness uses the first one whose browser is installed.
PLAYWRIGHT_CHROMIUM = {"1.57.0": "1200", "1.64.1": "1248"}
BROWSER_INSTALL_TIMEOUT_SECONDS = 300
CHECK_WORKERS = 4
BOOT_TIMEOUT_SECONDS = 30
BUILD_TIMEOUT_SECONDS = 300
INSTALL_TIMEOUT_SECONDS = 900
BROWSER_RUN_TIMEOUT_SECONDS = 1200
SMOKE_ROLES = frozenset({"link", "button", "textbox", "combobox", "checkbox", "radiogroup", "heading", "region", "tab"})
ARIA_ROLES = frozenset({  # the roles Playwright's getByRole accepts
    "alert", "alertdialog", "application", "article", "banner", "blockquote", "button", "caption", "cell",
    "checkbox", "code", "columnheader", "combobox", "complementary", "contentinfo", "definition", "deletion",
    "dialog", "directory", "document", "emphasis", "feed", "figure", "form", "generic", "grid", "gridcell",
    "group", "heading", "img", "insertion", "link", "list", "listbox", "listitem", "log", "main", "marquee",
    "math", "meter", "menu", "menubar", "menuitem", "menuitemcheckbox", "menuitemradio", "navigation", "none",
    "note", "option", "paragraph", "presentation", "progressbar", "radio", "radiogroup", "region", "row",
    "rowgroup", "rowheader", "scrollbar", "search", "searchbox", "separator", "slider", "spinbutton", "status",
    "strong", "subscript", "superscript", "switch", "tab", "table", "tablist", "tabpanel", "term", "textbox",
    "time", "timer", "toolbar", "tooltip", "tree", "treegrid", "treeitem"})
BACKEND_PACKAGES = frozenset({"express", "better-sqlite3"})
FRONTEND_PACKAGES = frozenset({"react", "react-dom", "react-dom/client", "react-router-dom"})
NODE_BUILTINS = frozenset({"assert", "buffer", "crypto", "events", "fs", "fs/promises", "os", "path", "querystring", "url", "util"})
