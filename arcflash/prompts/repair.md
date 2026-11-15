Task: the local check of the application found the problems below. Fix the cause of the failure groups shown in full, starting with the first. Each group is one shared step that many tests pass through; one change at that step can fix all of them.

- The requirement text is the authority. If a test contradicts it, correct the test (tests/scenarios-*.spec.ts) and quote the requirement sentence in a comment above the change. Otherwise change the application.
- Fix the cause. Keep everything that works, the file structure and every export.
- The problems are listed most important first: build, start, static check, page check, failure groups (largest first).
- A STATIC CHECK line about seed records comes first: add every missing record to the seed of backend/app.js with the exact values of its Seed prerequisites line.
- Write each SEARCH/REPLACE pair once. Stop when the groups shown in full are fixed.
{{previous_note}}
Edit with SEARCH/REPLACE blocks. SEARCH copies lines exactly from the current file, is as short as possible and matches one place only. One EDIT block may hold several pairs:
=== EDIT: frontend/src/pages/m1.jsx ===
<<<<<<< SEARCH
(exact current lines)
=======
(new lines)
>>>>>>> REPLACE
=== END EDIT ===
To rewrite a whole file, send a file block instead (=== FILE: path === ... === END FILE ===). Output only blocks.

Current files (only those these problems involve; the others exist and stay as they are):
{{files}}

Problems:
{{problems}}
