"""Every constant of ArcFlash, in one place (plan Part A sections 4.6, 8 and 9)."""

MODEL = "deepseek-flash"
THINKING = False
TEMPERATURE = 0.2
RESTART_TEMPERATURE = 0.6
MAX_PARALLEL_CALLS = 4
MAX_TOKENS = {"vision": 800, "plan": 24000, "backend": 32000, "frontend": 32000, "tests": 16000, "repair": 12000}
MAX_CONTINUATIONS = 2
RETRY_WAITS = (15, 30, 60, 120)
STREAM_IDLE_SECONDS = 180
RUNAWAY_BLANK_CHARS = 2000
RUNAWAY_REPEAT_LINES = 30
RUNAWAY_NUMBER_RUN = 300     # an answer ending in this many comma-separated integers is counting, not planning
CONTINUE_PROMPT = "Continue exactly where your previous answer stopped. Output only the continuation, without repeating anything."
RUNAWAY_PROMPT = "Your previous answer turned into blank or repeated text. Answer again, briefly and completely."
MAX_REPAIR_ROUNDS = 3
MAX_ROUNDS_WITHOUT_GAIN = 2
MAX_ATTEMPTS = 2
GOOD_ENOUGH_PASS_RATIO = 0.5
MAX_RUN_SECONDS = 50 * 60
REPAIR_RESERVE_SECONDS = 4 * 60
ATTEMPT_RESERVE_SECONDS = 15 * 60
MIN_RUN_COST_CNY = 2.0
COST_PER_SCENARIO_CNY = 0.05
RESTART_COST_SHARE = 0.6
PLATFORM_COST_FACTOR = 2.0
PRICES_CNY_PER_MILLION = {"input": 1.0, "cached": 0.02, "output": 4.0}
SCENARIOS_PER_TEST_CALL = 20
MAX_PAGES_PER_MODULE = 8
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
