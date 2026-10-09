"""The agent runtime: everything that is the same for every task and every company.

Purpose: the loop, the tools, the policy gate, the detectors and the LLM wrapper
(built phase by phase). What a company's systems are, what its rules are and
what "done" means arrive as data from company_pack/, never as code here.
Why this design: if this package never mentions a particular task, adding a new
task cannot require changing it. tests/test_no_task_strings.py enforces that.
How it can fail: a task detail that sneaks into this package makes a later
"nothing changed in agent/" claim meaningless; that test is the guard.
"""
