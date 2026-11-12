Task: the local check of the application found the problems below. Fix them.

- The requirement text is the authority. If a test contradicts it, correct the test (tests/scenarios-*.spec.ts) and quote the requirement sentence in a comment above the change. Otherwise change the application.
- Fix the cause. Keep everything that works, the file structure and every export.
- The problems are listed most important first: build, start, static check, page check, scenario tests.
{{previous_note}}
Edit with SEARCH/REPLACE blocks. SEARCH copies lines exactly from the current file, is as short as possible and matches one place only. One EDIT block may hold several pairs:
=== EDIT: frontend/src/pages/main.jsx ===
<<<<<<< SEARCH
(exact current lines)
=======
(new lines)
>>>>>>> REPLACE
=== END EDIT ===
To rewrite a whole file, send a file block instead (=== FILE: path === ... === END FILE ===). Output only blocks.

Current files:
{{files}}

Problems:
{{problems}}
