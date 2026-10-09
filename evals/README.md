# Evaluations

`python -m evals.run_evals --repeats 3` runs every scenario in [scenarios.yaml](scenarios.yaml) three times with a real model and
writes [RESULTS.md](RESULTS.md) (generated, never edited by hand) and the raw records to `results/<batch>.json`.
`python -m evals.run_evals --results-from results/<batch>.json` regenerates RESULTS.md from a stored batch.

## What one run is

1. A fresh copy of the seeded company data (deterministic seed; nothing is shared between runs).
2. A fresh mock company server on a free port with the scenario's fault flags switched on (the agent is never told about them).
3. One agent run with a **SCRIPTED_HUMAN**: pre-written answers from the scenario file, used in order. Real clarification and
   approval quality is therefore NOT measured, and every human interaction in the records is marked scripted.
4. A **ground-truth check** of the final database, written by hand in the scenario (not derived from the run, not the auditor).
5. One record of metrics, also stored inside the run's own `runs/<id>/run.json` under `eval`.

`scenarios.yaml` holds S1-S5 (S3 runs in both audit modes, S5 as approve and deny), S6 (revised invoice), S7 (vendor onboarding)
and S10 (mark a bill paid). S6, S7 and S10 were written as data before the agent was frozen but first run after it, on unchanged
code; their results are in `RESULTS_PHASE7.md`, with two further labelled batches (`RESULTS_PHASE7_S6_AFTER_HANDBOOK_FIX.md` and
`RESULTS_S3_RERUN_OF_ARTIFACT.md`). **Not built:** S8 (prompt injection), S9 (bill already exists, needs a seed variant) and S11
(session expiry).

## The two questions

* **Does the agent claim success wrongly?** That is *false completion*.
* **Does the auditor ever accept a wrong result?** That is an *auditor false pass*. It can only be seen because the ground
  truth is checked independently of the auditor. Any non-zero value here is a bug in the auditor or in the assertions it
  was given, and is listed prominently.

## Metric definitions

| Metric | Definition |
|---|---|
| **Runs** | Runs that were started and finished (successfully or not). Runs skipped by the spend limit are listed separately and not counted. |
| **Meets expectations** | All of: the final outcome is one of the scenario's acceptable outcomes; the ground truth matches; the number of (scripted) human interactions is within the scenario's bounds; at least the expected number of gate blocks happened; and, where required, no write reached the systems before the first human answer. |
| **Auditor-verified success** | The run's outcome is `verified_done` or `verified_no_action`: the agent claimed completion and the independent audit passed. |
| **Ground truth OK** | The final database matches the scenario's hand-written expectation: expected row delta per table, no content change in tables not allowed to change, and the listed rows exist the stated number of times. |
| **First claim correct** | Among runs where the agent claimed `done` / `no_action_needed` at least once: the audit of its FIRST claim passed. Runs that never claimed completion (handed over or ran out of budget) are left out of this rate. |
| **False completion** | The complement among the same runs: the audit of the FIRST claim failed. In enforce mode the agent may then repair; in advisory mode that first claim would have been final. |
| **Self-caught repair** | The run was verified AND the agent recorded at least one failed `self_check` (it noticed a mismatch itself). |
| **Auditor-caught repair** | The run was verified AND at least one audit failure was shown to the agent first (enforce mode): the audit found what the agent had missed, and the agent then fixed it. A run can be both. |
| **Auditor false pass** | The run was auditor-verified but the ground truth is wrong. |
| **Steps** | Number of loop steps (each is one tool call, one nudge, or one refused call). |
| **Retries** | Detector firings (all classes) plus audit failures shown to the agent. |
| **Detector firings** | How many times each detector class fired (`tool_error`, `timeout`, `noop`, `unexpected_page`, `repeat`). |
| **Human interactions** | Times the agent consulted a human (all scripted in these runs). |
| **Gate blocks** | Gate decisions that were not "allow" (denied or approval required), including blocked sub-requests of a page. |
| **Tokens, cost** | From the model responses. Cost is tokens times the provider's LIST price from LiteLLM's table ("unknown" if the model is not in it). On a free tier nothing is charged; the column then says what the same runs would cost at list price. |
| **Wall time** (free tiers) | Includes the pauses the client inserts to stay under a free tier's requests-per-minute limit (`AGENT_MIN_INTERVAL_SECONDS`) and any waits for rate-limit errors, so it is longer than the model's own thinking time. |
| **Wall time** | Seconds from the start of the run to its end. |
| **Free-tier limits** | Not a metric but a constraint: a free tier has a requests-per-minute and a requests-per-DAY limit. The sweep (about 21 runs of 15-30 model calls each) may not fit in one day; the harness stops cleanly if the daily quota is hit and the batch can be completed on another day. |

## Honest limits

* Three runs per scenario: one run moves a rate by 33 points. These are indications, not statistics.
* One model, one temperature setting, one machine. Model output varies between runs even at temperature 0.
* Faults are test conditions created for this project; the mock world is cleaner than real enterprise software.
* The scenarios were written by the person who built the system.
* Scenarios are not tuned to make the auditor look good: every outcome, including failed runs, is reported.
