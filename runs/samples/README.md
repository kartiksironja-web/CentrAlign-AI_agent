# Sample runs (real, kept on purpose)

Each folder is one real run: `run.json` is the full audit trail, `timeline.html` the readable version (open it in a browser).

| Run | Scenario | Why it is kept |
|---|---|---|
| 20261009-102834-ea71 | S1 | A successful run from the official sweep, walked through in docs/AGENT_WALKTHROUGH.md |
| 20261009-062941-0bb0 | S1 (early development run, older code) | The independent audit rejects the agent's first claim of success |
| 20261009-105347-58ee | S3-advisory | False completion: the agent claimed success with a wrong date and, with the audit hidden from it, the wrong result stood |
| 20261009-112244-bbb7 | S5-approve | Auditor false pass: the agent chose an older invoice and its checks agreed with its own choice; only the harness's ground truth shows it |
| 20261009-102859-bb5e | S6 (first batch) | Auditor false pass: the agent changed nothing, cited the original document, and the audit passed |
| 20261009-103705-f2e7 | S2 | Right data, but the agent wrote before asking which of two similar vendors was meant |
