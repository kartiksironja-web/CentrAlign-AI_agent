# Document audit (Phase 9)

What I did: I checked every factual claim in the README (and the docs it points to) against the code, the tests, `evals/RESULTS*.md` and
real runs, on 9 October 2026. Anything wrong, outdated or unprovable was fixed or removed; every `<<FILL>>` marker was filled with real data
or removed. The table lists each discrepancy: the claim, the reality, and the fix.

## Discrepancies found and fixed

| # | Claim (where) | Reality | Fix made |
|---|---|---|---|
| 1 | README: "Draft written before implementation" banner | The system is built and evaluated | Replaced with a status block that says what the results are, including the weak spots |
| 2 | README links `docs/DECISIONS.md` | The file is `docs/DECISIONS_LOG.md` | Link corrected |
| 3 | README links `docs/AGENT_WALKTHROUGH.md`, `docs/LIMITATIONS_AND_FUTURE.md`, `docs/HOW_TO_ADD_A_TASK.md` | None of the three existed | Written (the walkthrough from a real sweep run, the other two from what the runs showed) |
| 4 | README setup is bash-only (`source`, `cp`) | The project was built and tested on Windows | Windows commands added; the commands and the full test suite were run on a fresh clone (the final run, on the last commit: 761 tests passed) |
| 5 | README fault table lists `SESSION_EXPIRY` "keep only if built" | Not built (P2) | Marked "not built" |
| 6 | README tasks table: "keep only if built" for mark-a-bill-paid | Built (handbook file, scenario S10) | Kept; results are in `evals/RESULTS_PHASE7.md` |
| 7 | README: `prove_generalization` shows a "diff since the frozen tag" | It compares SHA-256 hashes of `agent/` and `auditor/` with a freeze record and also shows `git diff` when the tag exists | Description corrected, with the caveat that the T2-T4 handbook files were written before the freeze (so the claim is "the same frozen code ran four tasks", not "data was added after the freeze"). Actual output on 9 Oct: code unchanged (34 files); two data files changed since the freeze: `company_pack/sops/invoice_revision_update.md` (the S6 handbook fix) and `evals/scenarios.yaml` (an out-of-date comment corrected; no scenario changed) |
| 8 | README step 1: "Ambiguity means ask first, before any write" | Code enforces it only once the model reports an ambiguity; in S2 the model never did (0 of 3 asked) | Reworded and the S2 result stated |
| 9 | README: results table `<<FILL>>` | No results existed when it was drafted | Filled by a script from the generated files, not by hand; the first S6 batch, its handbook-only fix and the S3 artifact re-run are all shown |
| 10 | README limitation 5: "LLM output varies even at temperature 0" | Temperature was not set to 0 (the provider default was used) | Reworded: "temperature was left at the provider's default" |
| 11 | README models row `<<FILL: AGENT_MODEL>>` | | `mistral/ministral-14b-latest` via LiteLLM 1.104.2, every dependency checked against `requirements.txt` |
| 12 | README: "the loop is ~`<<FILL>>` lines" | `agent/loop.py` is 405 non-blank lines; `agent/` 2,844; `auditor/` 869 (counted on 2026-10-09) | Filled ("about 400", "about 2,800", "about 900") |
| 13 | README autonomy list: "diagnosing and repairing a failed audit" | The sweep recorded no repair caused by the audit's feedback (0 of 21 runs) | Replaced by what was actually observed (self-caught repair 1 of 3 in S3; API switch in S4 2 of 3; S2 never asked) |
| 14 | README autonomy list: "deciding when to ask a human" | True only for approvals the gate forces; for ambiguity the model did not ask in S2 | Stated as the weak spot |
| 15 | `docs/PROGRESS.md` (earlier versions): "703 tests", "40 steps / 300 s", "75 decisions" | Out of date | Rewritten; the budgets are 60 steps / 600 s / $1 per run (decisions #93 and #97) |
| 16 | `.env.example`: Gemini shown as the active option; unverified Mistral limits | Every result used `mistral/ministral-14b-latest`; Gemini's free key allows 20 calls per day per model | Rewritten with what was actually measured |
| 17 | `docs/DEMO_SCRIPT.md` (first draft): live runs on the command line, placeholders for results | The console exists and results exist | Rewritten for the console with only things that were observed |
| 18 | `docs/AGENT_WALKTHROUGH.md` (first version) described a run made while the handbook contained real example values | That run is not trustworthy evidence | Deleted the run; the walkthrough now describes a clean sweep run (`20261009-102834-ea71`); one sentence of mine overclaimed (it said the audit checked that the *latest* invoice was chosen; the query filters by the chosen invoice number) and was corrected |
| 19 | README: the example timeline command pointed at sample `20261009-084036-c44b` | That sample was deleted (recorded while the handbook held real example values), so the command failed | Now points at `runs/samples/20261009-102834-ea71` |
| 20 | `evals/README.md` and a comment in `evals/scenarios.yaml` said S6-S10 "are added in Phase 7" | S6, S7 and S10 exist and were run; S8 and S9 are not built | Both corrected (the scenario file change is a comment only) |
| 21 | Docs quoted "732 tests", "12 console tests", "97 entries" / "101 entries" in the decision log | Counts moved on after those lines were written | Updated to the current counts (761 tests, 102 decisions) |
| 22 | A fresh-clone run of the full suite failed one console test (about one run in eight) | A real race in the console, not only a flaky test: reading the company tables while the runner resets the database gave "no such table" and an HTTP 500 | Reader retries and shows empty tables during a reset; two tests added; 0 failures in 30 repeats (decision #102) |

## Claims checked and found true (with how)

| Claim | How it was checked |
|---|---|
| The auditor opens the databases read-only and is separate from the agent | `auditor/readonly.py` (`mode=ro`, `query_only`); `agent/runner.py` is the only place they meet (via `AuditPort`); tests in `tests/auditor` and `tests/agent/test_runner_cli.py` |
| `agent/` and `auditor/` contain no task or company words | `tests/test_no_task_strings.py` passes; plus `tests/test_handbook_has_no_answers.py` for the handbook |
| The agent never has the admin token, and `/admin/*` is denied to it | `company_pack/policy.yaml` rule `admin_off_limits`; the token is read only in `agent/runner.py` and `agent/run.py`; gate tests |
| Failure goes back with a hint, "max 2 rounds", then escalates | `RunConfig.max_audit_rounds = 2`; `auditor/feedback.py` (round 1 names fields, round 2 adds values); `tests/agent/test_loop.py` |
| The gate decides in code for every request, writes need a named action, approvals are single-use | `agent/gate.py` and its tests; S5 runs in `evals/RESULTS.md` (3 of 3 denials respected, nothing written) |
| `agent/` and `auditor/` did not change after the freeze | `python -m scripts.prove_generalization` (exit code 0); tag `agent-frozen` at commit `a4fca22` |
| Results are generated by code from stored records, not edited by hand | `evals/run_evals.py`, `evals/results.py`; each results file starts with "Generated ... Do not edit by hand" and lists its exact command |
| The setup instructions work | A fresh clone was installed and `pytest -q` run on it (761 tests passed on the final commit; an earlier fresh-clone run caught a console race, fixed in decision #102); the console and agent were then run for real |
| No secrets in the repository | `.env` is ignored; a search of all tracked files for the real key values found none (only the public mock admin token); `runs/` is ignored except `runs/samples/` |

## Things this audit could not make true

- **S2 (ask before writing when two vendors look alike):** the agent did not do it in any run. This is documented as a limitation, not hidden.
- **The auditor's false passes** (S5-approve 1 of 3; S6 first batch 3 of 3) are real findings about the design and are in the results and limitations.
- **The "AI coding tools" row** (README section 9) is the author's own statement of which tools were used; nothing was added about what was reviewed by whom.

## What was run, and when

Official sweep (frozen code): 21 runs, 10:28 to 11:32 on 2026-10-09, `evals/RESULTS.md`. New tasks on the same code: 9 runs, `evals/RESULTS_PHASE7.md`. S6 after a handbook-only fix: 3 runs. One S3 run died from a Windows file-lock collision caused by my own monitoring script (listed in `evals/RESULTS.md` as a harness error) and was re-run once, labelled.
