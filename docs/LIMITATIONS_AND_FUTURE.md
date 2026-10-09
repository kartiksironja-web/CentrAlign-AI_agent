# Limitations and what I would build next

Written from what the real runs showed (see `docs/PROGRESS.md` for the run-by-run record and `evals/RESULTS.md` for the numbers).

## Limitations I found, biggest first

1. **The agent writes its own assertions, and that is the hardest part for a small model.** The validator forces coverage of what the
   handbook requires and forces independent sources, but a weak model needs many attempts to write valid YAML with regexes. On the
   free 14B model it took 13 development runs, several fixes and a handbook change before one run finished and passed the audit.
   A stronger model, or a simpler way to name a value in a document (for example a `label:` option), would help; neither is built.
2. **Verification quality depends on the handbook.** The first real runs failed because nothing told the agent where the independent
   figures live. I added a "Where the truth is" section to each handbook file (document lines, tables, the path a check uses). That is
   legitimate company knowledge, but it also means the agent is not discovering its sources alone. A vague handbook gives weak checks.
3. **One small model on a free plan.** All results are for `ministral-14b-latest` on a free plan (the provider may use free-plan data for
   training; only mock data went through). Larger models on the same harness would likely do better and were not compared.
4. **The development runs overlap with scenario S1.** Between the first and the last of 14 development runs I changed prompts, state and
   validation after seeing failures on the same task S1 is made of. S1 numbers are therefore optimistic. The tasks added after the
   freeze (S6, S7, S10) were not tuned on.
5. **Small samples.** Three runs per scenario: one run moves a rate by 33 points. Run-to-run variance is visible (the same task ended
   differently in 11 development runs, for reasons that were different each time).
6. **The model over-selects rules and over-asks.** In several runs it chose an extra handbook section or listed answerable things as
   questions for a human. Wording changes reduced this; it is not eliminated.
7. **The auditor needs read access to the target system.** True in this sandbox; often not true in real companies.
8. **Text snapshots only.** No screenshots or vision. Canvas-heavy or unusual pages would fail.
9. **A redirect that follows a write can only be detected after the fact** (the browser library does not route redirect hops; GET redirect
   chains are pre-checked, written ones are not).
10. **Scripted humans in the evals.** Real clarification and approval quality is not measured. Every scripted answer is labelled.
11. **A small, friendly world.** The mock company is cleaner than real software; the scenarios were written by the person who built the system.
12. **Not built:** prompt-injection scenario (S8), "bill already exists" scenario with its own seed variant (S9), `SESSION_EXPIRY` (S11, P2).
13. **Tested on Windows 11 only** (Python 3.11).

## If I had two more weeks

1. **A model comparison.** Run the same harness on several models, including a much stronger one, to separate "the design works" from
   "this small model got there". Track results per model over time.
2. **Make assertions easier without making them weaker.** A `label:` extraction option in the assertion format, and a
   generated skeleton in the validator's error message (the exact YAML for a missing field, minus the expected source).
3. **A larger, adversarial evaluation set** written by someone else: ambiguous requests, conflicting documents, hostile PDFs
   (prompt injection), timeouts, expired sessions.
4. **Fewer wasted steps.** Many runs spent a third of their steps re-checking things already known; a "known facts" memory and a
   stricter step planner would cut cost and time.
5. **Durable execution:** a queue, resume after a crash, idempotency keys so a retried write cannot create a duplicate.
6. **Real approval channels** (chat or email) and role-based identities instead of a terminal prompt.
7. **Persistent company memory with human-reviewed lessons** (for example "this vendor's invoices always print dates as DD/MM/YYYY").
8. **A vision fallback** for pages without a usable text structure.
9. **A connector toolkit** (OpenAPI to tools) so a new system needs a manifest, not hand-written entries.
