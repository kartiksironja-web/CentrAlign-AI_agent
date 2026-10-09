"""The auditor: decides whether a task was REALLY done. Plain Python, no LLM.

Purpose: after the agent says "done", compare the real state of the systems
against what the agent declared must be true, and against an independent source.
Why this design: the model's own opinion of success is the thing being tested,
so nothing the model says is trusted. The auditor
  * opens databases through its own read-only connection (no way to write),
  * takes expected values from a source system or a document, never from notes,
  * rejects assertions that are too weak (see validate.py),
  * knows nothing about any company, task or table name (they arrive as data).
How it can fail: the agent writes its own assertions, so a vague definition of
done gives weak checks; the validator forces coverage of what the written
procedure requires, but it cannot invent requirements nobody wrote down.
"""
