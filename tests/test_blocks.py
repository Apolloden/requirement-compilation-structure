import unittest

from arcflash import config
from arcflash.blocks import Edit, RepeatGuard, apply_edits, is_allowed_path, parse_edits, parse_file_blocks, render_files

APP = "frontend/src/App.jsx"


def edit_block(path, *pairs):
    body = "".join(f"<<<<<<< SEARCH\n{s}=======\n{r}>>>>>>> REPLACE\n" for s, r in pairs)
    return f"=== EDIT: {path} ===\n{body}=== END EDIT ===\n"


class BlockTests(unittest.TestCase):
    def test_two_file_blocks(self):
        files = parse_file_blocks("=== FILE: backend/app.js ===\na\n=== END FILE ===\n=== FILE: frontend/src/App.jsx ===\nb\n=== END FILE ===\n")
        self.assertEqual(files, {"backend/app.js": "a\n", "frontend/src/App.jsx": "b\n"})

    def test_prose_and_fences_around_blocks_are_ignored(self):
        text = "Here it is:\n=== FILE: backend/app.js ===\n```js\na\n```\n=== END FILE ===\nDone."
        self.assertEqual(parse_file_blocks(text), {"backend/app.js": "a\n"})

    def test_cut_off_block_is_dropped(self):
        self.assertEqual(parse_file_blocks("=== FILE: backend/app.js ===\na\n"), {})

    def test_cut_off_block_before_the_next_one_is_dropped(self):
        text = "=== FILE: backend/app.js ===\na\n=== FILE: frontend/src/App.jsx ===\nb\n\n\n=== END FILE ===\n"
        self.assertEqual(parse_file_blocks(text), {"frontend/src/App.jsx": "b\n"})

    def test_an_edit_block_without_end_is_closed_by_the_next_header(self):
        # v4 graded runs: 12306 a1.1 wrote 0 END lines in 259 blocks and Stack Overflow a1.2 3 in 191; both rounds were
        # dropped whole (docs/6-arclaser/evidence.md 2.5).
        other = "frontend/src/pages/m1.jsx"
        text = edit_block(APP, ("a\n", "A\n")).replace("=== END EDIT ===\n", "") + edit_block(other, ("b\n", "B\n"))
        self.assertEqual(parse_edits(text), [Edit(APP, "a\n", "A\n"), Edit(other, "b\n", "B\n")])

    def test_an_edit_block_without_end_at_the_end_of_the_answer_keeps_its_complete_pairs(self):
        text = f"=== EDIT: {APP} ===\n<<<<<<< SEARCH\na\n=======\nA\n>>>>>>> REPLACE\n<<<<<<< SEARCH\nb\n=======\nB"
        self.assertEqual(parse_edits(text), [Edit(APP, "a\n", "A\n")])

    def test_a_file_block_without_end_is_still_dropped(self):
        text = "=== FILE: backend/app.js ===\nhalf\n" + edit_block(APP, ("a\n", "A\n"))
        self.assertEqual(parse_edits(text), [Edit(APP, "a\n", "A\n")])
        self.assertEqual(parse_file_blocks("=== FILE: backend/app.js ===\nhalf"), {})

    def test_repeat_guard_stops_after_eight_bad_pairs_in_a_row(self):
        # Replay of the v4 rounds: stopping after 8 bad pairs in a row keeps 83% of the real changes with 34% of the
        # text (docs/6-arclaser/evidence.md 2.5, appendix A.2).
        self.assertEqual(config.REPEAT_GUARD_PAIRS, 8)
        guard = RepeatGuard()
        real = lambda i: f"<<<<<<< SEARCH\nold {i}\n=======\nnew {i}\n>>>>>>> REPLACE\n"
        noop = lambda i: f"<<<<<<< SEARCH\nsame {i}\n=======\nsame {i}\n>>>>>>> REPLACE\n"
        text = f"=== EDIT: {APP} ===\n" + real(0) + "".join(noop(i) for i in range(7))
        self.assertFalse(guard(text))
        self.assertTrue(guard(text + noop(7)))
        self.assertFalse(guard(text + real(1) + noop(8)))   # a real pair resets the run
        self.assertTrue(guard(text + real(0)))               # a repeat of an earlier pair is bad too

    def test_duplicate_and_no_op_pairs_are_skipped(self):
        # A duplicate of an applied edit fails ("SEARCH text not found") and costs an edit_fix call for nothing.
        text = edit_block(APP, ("a\n", "A\n"), ("b\n", "b\n"), ("a\n", "A\n"), (" c\n", "c \n"), ("d\n", "D\n"))
        self.assertEqual(parse_edits(text), [Edit(APP, "a\n", "A\n"), Edit(APP, "d\n", "D\n")])

    def test_only_generated_paths_are_allowed(self):
        for p in ["backend/app.js", "frontend/src/shared.jsx", "frontend/src/App.jsx", "frontend/src/pages/m1.jsx", "tests/scenarios-2.spec.ts"]:
            self.assertTrue(is_allowed_path(p), p)
        for p in ["../x.js", "/etc/passwd", "backend/server.js", "frontend/src/api.js", "frontend/src/pages/Main.jsx", "tests/helpers.ts", "deploy.sh"]:
            self.assertFalse(is_allowed_path(p), p)

    def test_edit_exact(self):
        files = {APP: "const x = 1;\nconst y = 2;\n"}
        new, problems = apply_edits(files, parse_edits(edit_block(APP, ("const x = 1;\n", "const x = 3;\n"))))
        self.assertEqual((new[APP], problems), ("const x = 3;\nconst y = 2;\n", []))
        self.assertEqual(files[APP], "const x = 1;\nconst y = 2;\n")  # the input is not changed

    def test_edit_ignores_trailing_spaces(self):
        files = {APP: "  a();   \n  b();\n"}
        new, problems = apply_edits(files, parse_edits(edit_block(APP, ("  a();\n  b();  \n", "  c();\n"))))
        self.assertEqual((new[APP], problems), ("  c();\n", []))

    def test_ambiguous_edit_is_refused(self):
        files = {APP: "x();\ny();\nx();\n"}
        new, problems = apply_edits(files, parse_edits(edit_block(APP, ("x();\n", "z();\n"))))
        self.assertEqual(problems, ["frontend/src/App.jsx: SEARCH text matches 2 places; add lines to make it unique"])
        self.assertEqual(new[APP], files[APP])

    def test_missing_search_is_refused(self):
        files = {APP: "const y = 2;\n"}
        new, problems = apply_edits(files, parse_edits(edit_block(APP, ("const x = 1;\nmore\n", "z\n"))))
        self.assertEqual(problems, ["frontend/src/App.jsx: SEARCH text not found: const x = 1;"])

    def test_several_pairs_in_one_block(self):
        files = {APP: "a\nb\nc\n"}
        edits = parse_edits(edit_block(APP, ("a\n", "A\n"), ("c\n", "C\n")))
        self.assertEqual(edits, [Edit(APP, "a\n", "A\n"), Edit(APP, "c\n", "C\n")])
        self.assertEqual(apply_edits(files, edits), ({APP: "A\nb\nC\n"}, []))

    def test_edits_and_file_blocks_keep_answer_order(self):
        text = edit_block(APP, ("a\n", "b\n")) + "=== FILE: backend/app.js ===\nnew\n=== END FILE ===\n"
        self.assertEqual([e.path for e in parse_edits(text)], [APP, "backend/app.js"])

    def test_replace_can_delete_lines(self):
        new, problems = apply_edits({APP: "a\nb\n"}, parse_edits(edit_block(APP, ("b\n", ""))))
        self.assertEqual((new[APP], problems), ("a\n", []))

    def test_file_block_in_a_repair_replaces_the_file(self):
        file_block = "=== FILE: backend/app.js ===\nnew\n=== END FILE ===\n"
        self.assertEqual(parse_edits(file_block), [Edit("backend/app.js", None, "new\n")])
        self.assertEqual(apply_edits({"backend/app.js": "old\n"}, parse_edits(file_block)), ({"backend/app.js": "new\n"}, []))

    def test_edit_to_a_missing_file_is_refused(self):
        _, problems = apply_edits({}, parse_edits(edit_block("frontend/src/pages/extra.jsx", ("a\n", "b\n"))))
        self.assertEqual(problems, ["frontend/src/pages/extra.jsx: file does not exist; send a file block"])

    def test_disallowed_edit_is_refused(self):
        _, problems = apply_edits({"deploy.sh": "x\n"}, parse_edits("=== FILE: deploy.sh ===\nrm -rf /\n=== END FILE ===\n"))
        self.assertEqual(problems, ["deploy.sh: path not allowed"])

    def test_render_files(self):
        self.assertEqual(render_files({"backend/app.js": "a\n", APP: "b"}), "=== backend/app.js ===\na\n=== frontend/src/App.jsx ===\nb\n")


if __name__ == "__main__":
    unittest.main()
