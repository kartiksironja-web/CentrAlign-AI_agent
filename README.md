# VerifyOps — an AI worker that proves its work

> **Draft written before implementation.** Every `<<FILL: …>>` marker and every claim about behavior must be checked against the real code and `evals/RESULTS.md` before submission (Phase 9 of the build does this).

Submission for: CentrAlign AI — AI Engineering Intern ("Autonomous AI Task Worker")
Author: Kartik Sironja · Repository: https://github.com/kartiksironja-web/CentrAlign-AI_agent · Demo video: https://drive.google.com/drive/folders/1FIYwREtpOtJUvln8yVfFeRLOs4TvTdJ8?usp=sharing · Date: 9 October 2026

## 1. What this is

Give VerifyOps one sentence, such as "Enter Acme Supplies' latest invoice into the ERP and tell me when it's done." It reads the company handbook, works out what to do, operates a real browser and a real API in a sandboxed mock company, and returns a report with evidence.

The point of the project: most agents end with "Done!" as the model's own opinion. In VerifyOps, "done" is only accepted after an independent, non-LLM auditor checks the actual system state. I also measure how often an agent claims success when the result is wrong.

**Core principle: the LLM proposes, code disposes.**

The LLM decides what the request means, which tool to use next and how to recover. Plain code decides whether an action is permitted (policy gate), whether the task succeeded (auditor), when to stop (budgets) and what gets logged.

## 2. Architecture (short)

```
 request ─► Understand ─► Pick SOPs ─► Plan ─► WORKER LOOP ─► AUDITOR ─► report + run.json
             (LLM)         (LLM)       (LLM)   observe→reason→act   (plain code)
                                                 │      ▲              │ fail: hint, retry (max 2)
                            every write ─► POLICY GATE (code)           │ then escalate to human
                                                 │   + detectors (code)
                                                 ▼
                      Browser · HTTP API · Documents · Notes · Human
                                                 │
                        apps/company  (mock portal + ERP, SQLite)
                                                 ▲
                                                 └── auditor reads it READ-ONLY (separate channel)
```

| Part | Who decides | Where |
|---|---|---|
| Understand the request, pick SOPs, plan | LLM | `agent/contract.py`, `agent/loop.py` |
| Choose next tool and recover from surprises | LLM | `agent/loop.py` |
| Allowed / needs approval / forbidden | **Code** | `agent/gate.py`, `company_pack/policy.yaml` |
| Detect no-ops, errors, loops, wrong page | **Code** | `agent/detectors.py` |
| Was it actually done? | **Code** | `auditor/` |
| Company knowledge (rules, systems, limits) | **Data** | `company_pack/` |

**Handbook, not scripts.** The company knowledge is in markdown SOPs containing rules and a "definition of done", never click-paths. The agent finds its own way through the UI.

## 3. How one run works

1. **Understand:** the request becomes a structured task contract (goal, entities, ambiguities). Ambiguity means ask first, before any write.
2. **Handbook:** the LLM selects relevant SOPs from their titles and summaries.
3. **Plan:** a short checklist; every re-plan is logged with a reason.
4. **Act loop:** each step the model sees structured state (not an ever-growing chat), returns **one** tool call with a stated reason, the gate checks writes, the tool runs, detectors inspect the result.
5. **Self-check:** after writing, the agent re-reads the saved record.
6. **Declare assertions:** the agent states what must be true, as data.
7. **Audit:** the auditor compares the declared facts against the databases and against an independent source, and checks that nothing else changed. Failure goes back to the agent with a hint (max 2 rounds), then escalates to a human.
8. **Report:** outcome, evidence, anything needing attention, run id.

A real run explained step by step is in `docs/AGENT_WALKTHROUGH.md`. Design decisions, with the alternatives I rejected, are in `docs/DECISIONS_LOG.md`.

## 4. Setup and run

**Requirements:** Python 3.11+, an API key for one LLM provider (a free plan is enough; see the notes in `.env.example`). Tested on **Windows 11 with Python 3.11.9**: on 2026-10-09 a fresh clone was installed with the commands below and its 732 tests passed. Not tested on macOS or Linux.

```bash
python -m venv .venv
.venv\Scripts\activate                  # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
copy .env.example .env                  # macOS/Linux: cp .env.example .env ; then set your key and AGENT_MODEL
```

```bash
python -m apps.company.seed --reset    # create the seeded mock company database
python -m apps.company.main            # terminal 1: portal + ERP on http://localhost:8000
```

```bash
# terminal 2: run the agent
python -m agent.run --task "Enter Acme Supplies' latest invoice into the ERP and tell me when it's done." --headed
python -m agent.run --task "..." --faults DATE_TRAP     # inject a silent fault
```

```bash
python -m evals.run_evals --repeats 3     # writes evals/RESULTS.md from real runs
pytest -q                                 # deterministic tests, no LLM needed
python -m scripts.prove_generalization    # are agent/ and auditor/ byte-identical to the freeze? which data files changed?
python -m scripts.render_run runs/samples/20261009-084036-c44b/run.json   # static HTML timeline of a real run
```

Verified on a clean clone (Windows 11, 2026-10-09): venv, `pip install -r requirements.txt`, `playwright install chromium`, the seed, the app answering on its port, `agent.run --help` and `pytest -q` (732 passed). The agent and evaluation commands need a model key; they were run for real with the key in `.env`.

**Safety:** everything runs against a local mock company. No real credentials, no third-party systems. Mock logins only.

## 5. The mock company and injected faults

One small FastAPI app serves a **vendor portal** (invoice list and PDFs) and an **internal ERP** (forms and a JSON API). Faults are switchable and **off by default**. They are *test conditions I created on purpose*; the agent is never told about them.

| Fault | What it simulates |
|---|---|
| `DATE_TRAP` | ERP silently reads `03/11/2026` as 11 March instead of 3 November and still shows "Bill saved" |
| `SAVE_BROKEN` | The Save button does nothing |
| `BUTTON_RENAME` | The Save button's label and id change |
| `DUPLICATE_VENDOR` | Two similarly named vendors exist |

(A `SESSION_EXPIRY` fault was planned as a low-priority extra and is **not built**.)

## 6. Tasks (same agent code for all)

| Task | Example request |
|---|---|
| Create a bill from the latest invoice | "Enter Acme Supplies' latest invoice into the ERP…" |
| Update a bill from a revised invoice | "Acme revised INV-2033; update its due date in the ERP." |
| Create a vendor from a form file | "Onboard the new vendor in inputs/new_vendor_form_globex.pdf." |
| Mark a bill paid | "Mark Acme's INV-2039 as paid." |

`python -m scripts.prove_generalization` shows that `agent/` and `auditor/` are byte-identical to the freeze (`agent-frozen` tag and `evals/frozen_manifest.json`) after the later tasks were added, and lists the data files that changed (handbook files, `evals/scenarios.yaml`). **Honest caveat:** I designed those tasks knowing the framework, in the same fictional company, so they are easier than a stranger's tasks. See `docs/HOW_TO_ADD_A_TASK.md` for adding a new one.

## 7. Results

<<FILL: Include `evals/RESULTS.md` numbers by paste or link; do not hand-edit. Per scenario, state repeats, the model used, the exact command and the date.>>

| Scenario | Runs | Verified success | False-completion (agent claimed done, was wrong) | Self-caught | Auditor-caught | Avg steps |
|---|---|---|---|---|---|---|
| <<FILL>> | | | | | | |

Reading the table: **false-completion** counts runs where the agent's *first* "done" claim failed the audit. In `advisory` mode (auditor result hidden from the agent) that first claim would have been the final answer. <<FILL: one-sentence takeaway from the real data>>

## 8. What is autonomous and what is hard-coded

**Autonomous (decided by the model at run time):** interpreting the request; choosing which SOPs apply; planning and re-planning; choosing between UI and API; navigating pages it has never seen scripted; extracting values from documents; diagnosing and repairing a failed audit; deciding when to ask a human.

**Code-enforced (deliberately *not* autonomous):** permissions and approval limits; success verification; retry budgets and stop conditions; logging.

**Hard-coded or manually configured:** the loop and tool interfaces; the mock apps and fault flags; SOPs, policy and connector manifests (data files I wrote); the scripted "human" answers used in evals (labeled `SCRIPTED_HUMAN`); model choice via `.env`.

## 9. Models, APIs, frameworks, services

Checked against `requirements.txt` (all versions pinned) and `.env.example`.

| Item | Use |
|---|---|
| LLM: **`mistral/ministral-14b-latest`** (Mistral's free plan) via **LiteLLM** 1.104.2 | reasoning, tool calling; the model is one line in `.env` (`AGENT_MODEL`), other providers' keys are wired in `agent/llm.py` |
| **Playwright** 1.63 (Chromium) | real browser operation and request interception |
| **FastAPI**, uvicorn, Jinja2, python-multipart, SQLite (standard library) | the mock company |
| **pdfplumber**, **fpdf2** | reading and generating PDFs |
| **Pydantic**, PyYAML, python-dotenv, httpx | contracts, company-pack files, settings, HTTP |
| **pytest**, rich | tests and command-line output |
| AI coding tools | **Claude, ChatGPT and Gemini** were used while building this project, for planning, writing and debugging code and tests, and drafting documentation. Every claim in this README is meant to be checkable against the code, the tests, `docs/DECISIONS_LOG.md` and the recorded runs. |

No external company data. No pre-built agent framework: the loop (`agent/loop.py`) is about 400 lines and the whole `agent/` package about 2,800 lines of my own code; `auditor/` is about 900.

## 10. Assumptions

- The company exposes a **read-only view of the systems it operates** so verification is possible. (True in my sandbox; often not true in real enterprises.)
- SOPs are written as rules plus a "definition of done". Verification quality depends on this.
- The requester is reachable for clarification and approval. In evals this is scripted and labeled.
- Single task at a time, single user, single tenant, English-language documents.
- Dates in documents follow Indian convention (DD/MM/YYYY).
- The model supports tool calling.

## 11. Known limitations

(Details and mitigations in `docs/LIMITATIONS_AND_FUTURE.md`.)

1. **The agent writes its own assertions.** Code enforces coverage of fields the SOP requires, but a vague SOP means weak checks.
2. **The auditor needs read access to the target system.** Fine in a sandbox; not guaranteed in production.
3. **Text snapshots only.** No screenshot or vision path; canvas-heavy or unusual UIs would fail.
4. **SOP selection by LLM over a tiny corpus.** Does not scale to thousands of documents without retrieval.
5. **Run-to-run variance.** LLM output varies even at temperature 0. Results are shown over <<FILL: N>> runs per scenario, which is small.
6. **Scripted human in evals.** Real clarification and approval quality is not measured.
7. **Small, friendly world.** The mock apps are cleaner than real enterprise software.
8. <<FILL: any additional limitation discovered while building>>

## 12. What I would build next

Short version (full plan in `docs/LIMITATIONS_AND_FUTURE.md`):

1. Larger, adversarial eval set and regression tracking per model.
2. Persistent company memory with human-reviewed lessons.
3. Connector SDK and OpenAPI-based tool generation.
4. Durable execution (queue, resume after crash), idempotency keys.
5. Real approval channels (Slack/email) and role-based identities.
6. Vision fallback for UIs without usable DOM.

## 13. Repository layout

```
apps/company/   mock portal + ERP + admin (the test world, with fault flags)
agent/          task-agnostic runtime: loop, tools, gate, detectors, LLM wrapper
auditor/        assertion engine + validator (no LLM)
company_pack/   SOPs, policy.yaml, connector manifests (all company knowledge as data)
inputs/         sample documents (generated by the seed)
evals/          scenarios, runner, ground-truth check, generated RESULTS.md and the stored batch records
scripts/        prove_generalization, render_run
tests/          deterministic unit tests + FakeLLM loop tests
docs/           DECISIONS_LOG, AGENT_WALKTHROUGH, LIMITATIONS_AND_FUTURE, HOW_TO_ADD_A_TASK, PROGRESS, DEMO_SCRIPT, AUDIT_REPORT
runs/           per-run JSON audit trails (git-ignored except runs/samples/)
```
