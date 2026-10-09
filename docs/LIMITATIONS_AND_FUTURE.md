# Limitations and what I would build next

Written from what the real runs showed. Numbers are in `evals/RESULTS.md`, `evals/RESULTS_PHASE7.md` and the two re-run files; the history is in `docs/PROGRESS.md`.

## Limitations, biggest first

1. **The auditor checks the record against the source the agent cites, so it can agree with a wrong choice.** This is the main weakness of the design,
   and the evaluation measured it (the "Auditor false pass" column, using a ground-truth check that is independent of the auditor):
   - S5-approve, 1 of 3 runs: the agent chose Zenith's older invoice instead of the latest one; its checks compared the bill with that older
     document, and the audit passed.
   - S6 (revised invoice), first batch, 3 of 3 runs: the agent changed nothing, cited the *original* invoice document, and the audit passed.
   The mitigation that works is data: the handbook names the source precisely and requires a "something changed" check (decision #100). It
   made the bill correct in 3 of 3 S6 runs, but the stricter checks then exceeded what the small model could write within its budget. A
   stronger fix would be code (a rule such as "this value must be checked against that table"), which the frozen auditor does not have.
2. **The model does not reliably ask when it should.** In S2 (two vendors with similar names) the agent chose the right vendor in 3 of 3 runs
   but never asked the human first (0 of 3 meet the expectation "ask, then write"). Prompt and handbook wording did not change this. "Ask
   when unsure" is enforced by code only after the model itself reports an ambiguity.
3. **The agent writes its own assertions, and that is the hardest part for a small model.** The validator forces coverage of what the
   handbook requires and forces independent sources, but a 14B model needed many attempts (and a handbook note naming the sources) before a
   run could finish. A stronger model, or a simpler way to name a value in a document (for example a `label:` option), would help; neither is built.
4. **One small model on a free plan, three runs per scenario.** Every result is for `ministral-14b-latest`; one run moves a rate by 33 points;
   the same task ends differently from run to run (S3, the silent date fault, ended verified, handed over or out of budget in different runs).
   Larger models on the same harness would likely do better and were not compared. The provider may use free-plan data for training; only mock data went through.
5. **The evaluation is not independent of my tuning.** Fourteen single development runs and five pilot batches (26 runs, about 40 in all) used the same tasks as
   the scenarios, with fixes between them; the new tasks (S6, S7, S10) were not run before the freeze. A handbook example once leaked real
   values to the model; the affected runs were discarded and a test now guards it (`docs/PROGRESS.md`, decision #94).
6. **Verification quality depends on the handbook.** Each procedure has a "Where the truth is" section naming the document lines and tables
   that are the independent sources. That is legitimate company knowledge, but it also means the agent is not discovering its sources alone,
   and a vague handbook gives weak checks.
7. **The auditor needs read access to the target system.** True in this sandbox, often not in real companies.
8. **Text snapshots only.** No screenshots or vision; canvas-heavy or unusual pages would fail.
9. **A redirect that follows a write can only be detected after the fact** (the browser library does not route redirect hops; GET redirect
   chains are pre-checked, written ones are not).
10. **Scripted humans in the evals.** Real clarification and approval quality is not measured (the web console lets a real person answer, but that is a demo, not a measurement).
11. **A small, friendly world.** The mock company is cleaner than real software; the scenarios were written by the person who built the system.
12. **Not built:** prompt-injection scenario (S8), "bill already exists" scenario with its own seed variant (S9), `SESSION_EXPIRY` (S11, P2).
13. **Windows only was tested** (Windows 11, Python 3.11). One Windows-specific bug was found and fixed in the console (a file-replace lock, decision #99).
14. **The web console is a demo tool, not a product.** Local only (127.0.0.1), no login, one run at a time, written after the freeze (outside the evaluated code).

## If I had two more weeks

1. **A model comparison** on the same harness, including a much stronger model, to separate "the design works" from "this small model got there".
2. **Close the false-pass gap in code:** let a procedure say which table or document is the independent source of a field (and that a change
   must have happened), and make the validator enforce it, so the auditor checks the agent's *choice* and not only its consistency.
3. **A pre-write checkpoint:** before the first write, a short structured self-review ("which other records resemble the one you chose?") that
   forces a question when the answer is not "none"; measure it on S2.
4. **Make assertions easier without making them weaker:** a `label:` extraction option and a generated skeleton in the validator's message.
5. **A larger, adversarial evaluation set written by someone else:** ambiguous requests, conflicting documents, hostile PDFs (prompt
   injection), timeouts, expired sessions; with the development set kept apart from the test set from the start.
6. **Fewer wasted steps:** many runs spent a third of their steps re-checking known facts; a "known facts" memory would cut cost and time.
7. **Durable execution:** a queue, resume after a crash, idempotency keys so a retried write cannot create a duplicate.
8. **Real approval channels** (chat or email) and role-based identities instead of a browser prompt.
9. **Persistent company memory with human-reviewed lessons.**
10. **A vision fallback** for pages without a usable text structure, and **a connector toolkit** (OpenAPI to tools).
