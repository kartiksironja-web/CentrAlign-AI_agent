# VerifyOps: progress

Spec: `text_to_pdf_Imagetotext.pdf` (kept in `C:\Users\karti\OneDrive\Attachments\Desktop\Project.ai`, not in this repo).
Repo: `C:\dev\verifyops` (outside OneDrive on purpose: sync can corrupt SQLite files). Each phase stops for the owner's "go".
Language model: Mistral `ministral-14b-latest` through LiteLLM (`AGENT_MODEL=mistral/ministral-14b-latest`, key in `.env`, free plan, 30 requests and 937,500 tokens per minute). The Gemini free key (20 calls per day per model) is too small for a run; Mistral's Medium and Small models answer HTTP 429 with a limit of 0 on this key (see "Real runs on the Mistral model").
**Where it stands (2026-10-09): no real run has finished with a passing audit yet.** 11 development runs: the work itself (the bill in the ERP) was correct in every run that got that far, but the agent runs out of its 40 steps while writing its own assertions. The next decision is about the model (see Next steps).

## Phase overview

| Phase | What | Status |
|---|---|---|
| 0 | Spec review: critique, improvements, blocking questions | Done |
| 1 | Repo scaffold, mock company (portal, ERP UI + API, admin), seed data, four fault flags | Done |
| 2 | Auditor: read-only DB access, snapshots, assertion engine, validator, feedback ladder | Done |
| 3 | Policy gate, `policy.yaml`, connector manifests | Done |
| 4 | Tools (browser, http, documents, notes, human) and detectors | Done |
| 5 | LLM wrapper, contract / rule selection / plan, the loop, finish + audit, run log, report, SOPs, CLI | Built and tested; 11 real development runs found and fixed 9 defects. No real run has passed its audit yet (the model runs out of steps writing assertions) |
| 6 | Eval harness, scenarios S1-S5, metrics, `RESULTS.md` generator, `agent-frozen` tag | Harness built and tested; the real sweep and the tag are pending (they wait for a model that can finish a run) |
| 7 | T2 revision update, T3 vendor onboarding, (T4 mark paid) via data only; `prove_generalization`; P1 scenarios (S6-S10) | Not started (waits for the tag) |
| 8 | Run viewer, recording flags, demo script | Viewer and flags done; demo script is a draft (needs real results) |
| 9 | Document audit: check every README claim against code and `RESULTS.md`, `docs/AUDIT_REPORT.md` | Not started (needs real results) |

## What exists (details are in docs/DECISIONS_LOG.md, 87 entries)

- **Mock company** (`apps/company/`): vendor portal (amount and due date only inside invoice PDFs), ERP with web forms and a JSON API, token-protected admin, faults `DATE_TRAP`, `SAVE_BROKEN`, `BUTTON_RENAME`, `DUPLICATE_VENDOR` (all off by default), deterministic seed. `SESSION_EXPIRY` (P2) is not built on purpose.
- **Auditor** (`auditor/`, plain Python, no model, no company words): own read-only SQLite connection, schema-checked SQL, before/after row-hash snapshots, six assertion kinds (expected values from a query, a document regex, or a literal only when the handbook allows), a validator that forces coverage of what the handbook requires, `no_other_changes`, a feedback ladder (round 1 names the field, round 2 adds values, then escalation).
- **Policy gate** (`agent/gate.py`): decides in code, for every request the browser or an HTTP tool makes: host allow-list, deny rules on every method including GET (admin, DELETE), writes must map to a named action, fail-closed on unreadable bodies or numbers, a lookup for fields the request does not carry, approvals that are action-keyed, single-use, and described by the gate rather than the model.
- **Tools and detectors**: real Chromium with every request gated, a redirect guard (the browser library does not route redirect hops, so GET chains are pre-checked and followed hops are detected afterwards), refused navigations answered with an empty 204 so a filled-in form survives, `http_request`, `read_document`, `note`, `ask_human`; detectors `tool_error`, `timeout`, `noop`, `unexpected_page`, `repeat` with a retry budget of 2.
- **The loop** (`agent/loop.py`): state, not transcript (request, contract, rules, plan, notes, budget, last 3 steps in full, one line per older step); one tool call per step with a required reason; budgets 40 steps / 300 s / a cost cap; `finish(done)` is accepted only with validated assertions and a passing audit (feedback rounds, then escalation to a human); advisory mode records the audit without showing it; run log `runs/<id>/run.json`; a final report whose outcome, evidence and changes are written by code.
- **Handbook** (`company_pack/`): four SOPs (rules and a definition of done, no click-paths), policy, connector manifests, the audit map.
- **Evals** (`evals/`): scenarios S1 happy path, S2 two similar vendors, S3 date trap (audit enforced) and S3-advisory, S4 broken button, S5-approve and S5-deny; every run gets a fresh data copy and its own server process; an independent ground-truth check (not the auditor) so an auditor false pass is visible; `RESULTS.md` is generated from stored records only.
- **Viewer** (`scripts/render_run.py`): one static, script-free HTML timeline of a run (why each step, gate decisions, audit table, humans scripted or real).
- **Tests**: 721 passing (before the last two prompt changes; the agent and auditor suites were re-run after them) in about 2 minutes (they start real servers and a real browser). Every major component was also checked by deliberately breaking it and confirming the tests fail.

## Gemini-specific work (done before the first real call)

- Facts taken from Google's documentation: key from `https://aistudio.google.com/apikey`, variable `GEMINI_API_KEY`, a free tier listed for `gemini-3.8-flash`, `3.7-flash`, `3.6-flash`, `3.5-flash`, `3.5-flash-lite`, `3.1-flash-lite`; free-tier content may be used by Google to improve its products (only mock data goes through); rate limits are not in the docs, only in the owner's account (`https://aistudio.google.com/rate-limit`).
- LiteLLM's own Gemini conversion was run offline on all tool schemas and showed what Gemini would reject (free-form objects, optional `anyOf`, `default`). Schemas are now conservative for every provider and a test enforces it; `http_request` takes `json` as JSON text and `form` as a list of name/value pairs.
- Free-tier handling: calls spaced by `AGENT_MIN_INTERVAL_SECONDS`, rate-limit errors retried after the provider's own wait, a daily-quota error is not retried, per-provider key check, optional `AGENT_REASONING_EFFORT`.
- Verified against the real API on 2026-10-09: a call with all 13 tool definitions was accepted and answered correctly by `gemini-3.8-flash`, `3.7-flash`, `3.5-flash`, `3.5-flash-lite` and `3.1-flash-lite`.

## First real run (2026-10-09): what it showed

Task: "Enter Acme Supplies' latest invoice into the ERP and tell me when it's done", `gemini/gemini-3.5-flash`, headless, 600 s cap. It stopped at step 16 with outcome `error` because the model's daily request quota was used up. Nothing was written to the company systems. Evidence is in `runs/20261009-055209-3db5/run.json`.
- **Behaviour (real, not scripted):** the contract had no open questions (the zero-human path of S1 is intact), the right handbook section was chosen, the plan was sensible; it found the vendor, listed the unsorted invoices, identified INV-2041 as the latest, read the PDF, checked that no bill existed, then selected the vendor and typed the invoice number and amount correctly. It was a few steps from saving. Whether it would have entered the date correctly and passed the audit is unknown.
- **Inefficiency:** 6 of its 16 steps were housekeeping or repeats (two plan updates, two notes, re-listing the invoices, re-reading the same PDF). A generic rule against that was added to the system prompt (decision #76); its effect is not yet measured.
- **Numbers (one partial real run):** 19 model calls (3 set-up + 16 steps), 89,402 tokens in, 9,451 out, list-price cost about $0.18, median 9 s per model call, 228 s wall time. So a full run is roughly 25 calls and 6-8 minutes: use `--max-seconds 600` or more, not 300.
- **Free-tier quota (the blocker):** Google's error says `GenerateRequestsPerDayPerProjectPerModel-FreeTier`, limit **20**, reset after about 23.5 hours. 20 calls per model per day is less than one run. Each model has its own counter (`gemini-3.5-flash-lite` still answered afterwards), but mixing models would make results incomparable.
- **Model comparison (same request, 2026-10-09):** 3.5-flash-lite 2-3 s, 3.5-flash about 3 s, 3.1-flash-lite 3-14 s, 3.7-flash 12-16 s, 3.8-flash 20-50 s and one HTTP 503 (a longer backoff for 503 was added).
- **Cost if billing were enabled (ESTIMATES from the partial run and Google's list prices: 3.5-flash $1.50 in / $9.00 out per million tokens, 3.5-flash-lite $0.30 / $2.50):** roughly $0.25 per full run on 3.5-flash and $0.05 on 3.5-flash-lite; the 21-run sweep about $5 and about $1. These are not measurements of a full run.

## Real runs on the Mistral model (2026-10-09)

Key facts, measured with tiny real calls: on the owner's free key `mistral-medium-*`, `mistral-small-*`, `magistral-*` and the vibe models answer HTTP 429 with `x-ratelimit-limit-req-minute: 0` (although the Limits page showed 20,000 tokens per minute for medium); `ministral-14b-latest` (30 requests, 937,500 tokens per minute), `ministral-8b`, `ministral-3b` and `codestral` work. `ministral-14b-latest` accepted all 13 tool definitions on the first try. A run costs about 250,000 tokens in / 10,000 out, about 4-5 minutes, about $0.04 at list price (free on the plan).

Task for every row: "Enter Acme Supplies' latest invoice into the ERP and tell me when it's done." (S1, no faults, no human). These are DEVELOPMENT runs on the same task as scenario S1, with fixes between them, so they are NOT evaluation results and S1 numbers from the sweep will be optimistic by that amount (disclose this in the README). Phase 7's new tasks are the held-out test.

| Run | Rules chosen | Ended as | Steps | Bill saved at step | Assertion declarations (accepted) | Audits | What went wrong (fixed in) |
|---|---|---|---|---|---|---|---|
| 062252 | invoice entry | budget used up | 40 | 16 | 22 (0) | 0 | Prompt example put the regex in double quotes, YAML rejected `\s` 22 times (#78) |
| 062941 | invoice entry | budget used up | 40 | 19 | 15 (9) | 1 | **First claim failed the audit: the independent audit caught it.** Copied example label `Total:`; re-declared accepted assertions 9 times (#78, #79) |
| 063949 | invoice entry | blocked | 10 | - | 0 | 0 | Model listed "which vendor?" as a question; closed keyboard crashed `ask_human` (#80, #85) |
| 064208 | invoice entry | blocked | 5 | - | 0 | 0 | Same questions; deadlock: finish was refused while the human was unavailable (#81) |
| 064416 | + revision update | needs a human | 15 | - | 0 | 0 | Three scattered tool slips used the whole retry budget (#82) |
| 064717 | invoice entry | budget used up | 40 | 27 | 6 (1) | 0 | Re-declared after acceptance; many steps re-checking |
| 075301 | invoice entry | needs a human | 2 | - | 0 | 0 | Pointless questions again (#85) |
| 075552 | invoice entry | budget used up | 40 | 24 | 11 (0) | 0 | Copied `<label>` with its angle brackets 11 times (#83, #84) |
| 080309 | + 2 more rules | needs a human | 13 | - | 0 | 0 | Tried local paths for a portal PDF, over-selected rules (#86, #87) |
| 080707 | + onboarding | budget used up | 40 | 20 | 13 (0) | 0 | Onboarding rule added vendor checks that could never be met (#87) |
| 081442 | invoice entry | budget used up | 40 | 19 | 11 (1) | 0 | Confuses the PDF's audit path (`portal_files/...`) with the readable URL and local `inputs/` path; not fixed |

What this shows, honestly:
- **The work is right**: in all 6 runs that saved a bill, vendor, invoice number, amount and due date were correct, and nothing else changed.
- **Nothing finished.** The model cannot write valid, independently sourced assertions within the step budget; the validator (correctly) refuses weak ones, and the audit caught the one wrong claim that reached it. That is the system working as designed on a weak model, but it is not a passing run.
- **Fixes that helped are generic** (state shows verification status, declaration-time lookup of expected values, retry budget counts consecutive errors, the human-unavailable deadlock, prompt wording). None mentions the task.
- **Still open**: the 14B model needs 10+ attempts to write assertions. Options in Next steps.

## Known gaps (honest list)

- No complete real run has passed its audit yet. The false-completion rate and whether S4 is solved are unknown. The audit has run once on a real model's work (and correctly rejected the claim).
- The agent writes its own assertions; the validator enforces coverage of what the handbook requires, nothing more. The eval harness reports any auditor false pass.
- A redirect that follows a WRITE can only be detected after the fact.
- 3 runs per scenario: one run moves a rate by 33 points. One model; scenarios written by the person who built the system; humans are scripted in the evals.
- Results will be less repeatable than with temperature 0 (Gemini 3 is left at its default temperature).
- The optional `fields:` on `rows_changed` (so "only the due date changes" is enforceable) was offered and not added.
- T2-T4 SOP texts already exist in the pack although their scenarios come in Phase 7; the agent code was not tuned for them.

## Next steps

0. **Decision needed (model).** `ministral-14b` does the work but cannot finish the verification within 40 steps. Options: (a) a stronger free model, e.g. a larger model on NVIDIA NIM (needs the owner to create a key; about 30 minutes to wire and test; most likely to help, because the weakness is writing YAML and regexes); (b) add a simpler way to name a document value (`label:` instead of a regex) to the assertion format (about 1.5 hours; an auditor change, so it needs the owner's yes; helps any model); (c) raise the step budget from 40 for the evaluation (a one-line config change; a deviation from the spec's 40 that must be reported); (d) paid Gemini or Mistral (about $5 for the sweep on 3.5-flash, estimate). Recommendation: (a), then (b) if still needed.
1. **Provider research** (done before the model was chosen; a free Gemini key allows 20 calls per day per model). Research on 2026-10-09, official pages where stated; what a run needs is about 25 calls and about 130,000 tokens, so the sweep needs about 500 calls and 2.7 million tokens:
   - **Groq**: its limits page lists, for the Developer plan base limits, 30 requests per minute, 1,000 per day, **8,000 tokens per minute, 200,000 per day** for `gpt-oss-120b` / `gpt-oss-20b` / `qwen3.8-27b` (the free-plan values are only in the account). 8,000 tokens per minute is less than two of our calls: not viable.
   - **Cerebras** (`gpt-oss-120b`, `qwen-3.8-27b`): free trial 5 requests per minute, **1 million tokens per day** (about 8 runs a day, so the sweep fits in about 3 days); but the page says new accounts get $5 of free credits "after adding a verified payment method" and API access stays inactive without one: needs a card.
   - **OpenRouter** free models: 20 per minute, **50 per day** (1,000 per day only after buying about $10 of credits): not viable free.
   - **GitHub Models**: retired.
   - **Mistral** (`mistral/mistral-medium-latest` or `mistral-small-latest`, both with function calling in LiteLLM's table): the free API "Experiment" plan needs only phone verification, no card, and the free plan may use your data for training. Third-party sites claimed about 500,000 tokens per minute and about 1 billion tokens per month; **the owner's own Limits page (2026-10-09) shows, for `mistral-medium-latest`, 20,000 tokens per minute and 1 request per second, so the third-party figures were wrong.** The monthly token cap was not in the pasted rows (still to check). What 20,000 tokens per minute means for us (arithmetic from the one partial real run, about 5,200 tokens per call): about 3-4 calls a minute, so a run of about 130,000 tokens takes at least 6.5 minutes and the 21-run sweep (about 2.7 million tokens) at least 2.3 hours, realistically 3 hours. That is slow but fits, with `AGENT_MIN_INTERVAL_SECONDS=15` and `--max-seconds 900`. Not yet tested against the real API.
   - **NVIDIA NIM** (`nvidia_nim/...`): reported (third-party, community-observed) free for developers, no card, about 40 requests per minute account-wide, no stated daily cap; tool-calling depends on the chosen model (check its model card).
   - **Cohere trial key**: 20 requests per minute, **1,000 calls per month** (official): about 40 runs, one sweep per month at most.
   - Recommendation: try Mistral first (needs the owner to create the account and key); NVIDIA NIM second. Support for both is already wired (key checks, example config); nothing has been sent to them yet.
2. Then: a complete first real run; fix whatever the real model does differently.
3. The sweep `python -m evals.run_evals --repeats 3` (21 runs), generate `RESULTS.md`, review it together.
4. Install git (explicit yes needed), commit, tag `agent-frozen`.
5. Phase 7, finish Phase 8 (demo script from real runs), Phase 9 (document audit), README filled from real data.
