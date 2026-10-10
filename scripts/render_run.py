"""Turn a run's run.json into one static HTML page: `python -m scripts.render_run runs/<id>/run.json`.

Purpose: answer "why did the agent do that?" live. One card per step shows the reason the model
gave, the call, what the gate decided, what the detectors said, and what came back; around the
steps are the contract, the plan versions, the self-checks, every version of the declared
assertions, each audit attempt check by check, the human interactions (marked scripted or
real), what actually changed in the systems, and the final report.
Why this design: no server, no JavaScript, no network: one self-contained file that opens
anywhere. A run log contains text that came from web pages and documents, which is untrusted,
so EVERYTHING is HTML-escaped and a content security policy forbids scripts anyway.
How it can fail: it shows what the log recorded; a field missing from an old or partial log
is shown as absent rather than guessed (for example a run that stopped with an error has no
steps).
"""
import argparse
import html
import json
import sys
from pathlib import Path

CSS = """
body{font-family:system-ui,Segoe UI,sans-serif;margin:0;background:#f4f5f7;color:#1d2433;line-height:1.45}
main{max-width:980px;margin:0 auto;padding:24px}
h1{margin:0 0 4px}h2{margin:32px 0 8px;border-bottom:2px solid #d5d9e0;padding-bottom:4px}
.card{background:#fff;border:1px solid #d5d9e0;border-radius:6px;padding:12px 16px;margin:10px 0}
.badge{display:inline-block;padding:1px 8px;border-radius:10px;font-size:12px;font-weight:600;margin-left:6px}
.good{background:#d9f5e1;color:#14532d}.bad{background:#fde2e2;color:#7f1d1d}.warn{background:#fef3c7;color:#78350f}
.info{background:#e0e7ff;color:#1e3a8a}.mute{background:#e5e7eb;color:#374151}
.why{margin:6px 0;font-weight:600}.det{color:#92400e;margin:4px 0}.meta{color:#6b7280;font-size:12px;margin:6px 0 0}
code,pre{font-family:Consolas,monospace;font-size:12.5px}pre{background:#f1f3f7;padding:8px;overflow:auto;white-space:pre-wrap;border-radius:4px}
table{border-collapse:collapse;width:100%;font-size:13px}th,td{border:1px solid #d5d9e0;padding:4px 8px;text-align:left;vertical-align:top}
th{background:#f1f3f7}summary{cursor:pointer}ul{margin:4px 0;padding-left:20px}.kind-refused,.kind-invalid,.kind-nudge{border-left:4px solid #f59e0b}
.kind-control{border-left:4px solid #6366f1}.kind-tool{border-left:4px solid #9ca3af}
"""
OUTCOME_BADGE = {"verified_done": "good", "verified_no_action": "good", "needs_human": "warn", "blocked": "warn",
                 "audit_failed": "bad", "false_completion": "bad", "budget_exhausted": "bad", "error": "bad"}
READS = ("GET", "HEAD", "OPTIONS")


def e(value) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def pre(value) -> str:
    text = value if isinstance(value, str) else json.dumps(value, indent=2, ensure_ascii=False, default=str)
    return f"<pre>{e(text)}</pre>"


def badge(text: str, kind: str) -> str:
    return f'<span class="badge {kind}">{e(text)}</span>'


def gate_html(entries: list[dict]) -> str:
    shown = [g for g in entries if g["method"] not in READS or g["effect"] != "allow"]
    reads = len(entries) - len(shown)
    if not shown and not reads:
        return ""
    items = []
    for g in shown:
        kind = {"allow": "good", "deny": "bad", "require_approval": "warn", "require_clarification": "warn"}[g["effect"]]
        label = {"allow": "allowed", "deny": "denied", "require_approval": "approval required",
                 "require_clarification": "clarification required"}[g["effect"]] + (
            f" by {g['rule_id']}" if g.get("rule_id") else "")
        action = f" ({g['action']})" if g.get("action") else ""
        note = f" [{g['note']}]" if g.get("note") else ""
        items.append(f"<li>{badge(label, kind)} <code>{e(g['method'])} {e(g['url'])}</code>{e(action)}{e(note)}</li>")
    if reads:
        items.append(f"<li>{reads} read request(s) allowed</li>")
    return "<p><b>Gate</b></p><ul>" + "".join(items) + "</ul>"


def call_html(args: dict) -> str:
    """Short arguments inline as JSON; multi-line text (such as declared assertions) as a readable block."""
    inline = {k: v for k, v in args.items() if not (isinstance(v, str) and "\n" in v)}
    blocks = {k: v for k, v in args.items() if isinstance(v, str) and "\n" in v}
    out = f"<p>Call: <code>{e(json.dumps(inline, ensure_ascii=False, default=str))}</code></p>"
    return out + "".join(f"<p>{e(k)}:</p>{pre(v)}" for k, v in blocks.items())


def step_html(s: dict) -> str:
    worst = "allow"
    for g in s["gate"]:
        if g["effect"] == "deny" or (g["effect"] in ("require_approval", "require_clarification") and worst == "allow"):
            worst = g["effect"]
    marks = [badge(s["kind"], "mute")]
    if s["error"]:
        marks.append(badge("error", "bad"))
    if worst != "allow":
        marks.append(badge({"deny": "blocked", "require_clarification": "clarification needed"}.get(worst, "approval needed"),
                           "bad" if worst == "deny" else "warn"))
    cost = f" · ${s['cost_usd']:.4f}" if s.get("cost_usd") is not None else ""
    detectors = "".join(f'<p class="det">{e(d)}</p>' for d in s["detectors"])
    return (f'<section class="card kind-{e(s["kind"])}"><b>Step {e(s["n"])}</b> <code>{e(s["tool"])}</code>{"".join(marks)}'
            f'<p class="why">Why: {e(s["reason"] or "(no reason given)")}</p>'
            f"{call_html(s['args'])}{gate_html(s['gate'])}{detectors}"
            f"<details><summary>Result: {e(s['summary'])}</summary>{pre(s['result_text'])}</details>"
            f'<p class="meta">{e(s["tokens_in"])} tokens in · {e(s["tokens_out"])} out{cost} · {e(s["ms"])} ms</p></section>')


def checks_table(checks: list[dict]) -> str:
    rows = "".join(
        f"<tr><td>{e(c['id'])}</td><td>{e(c['kind'])}</td><td>{badge('PASS', 'good') if c['passed'] else badge('FAIL', 'bad')}</td>"
        f"<td>{e((c.get('table') or '') + ('.' + c['field'] if c.get('field') else ''))}</td>"
        f"<td>{e(c.get('expected'))}</td><td>{e(c.get('actual'))}</td><td>{e(c.get('detail'))}</td></tr>" for c in checks)
    return ("<table><tr><th>check</th><th>kind</th><th></th><th>where</th><th>expected</th><th>actual</th><th>detail</th></tr>"
            + rows + "</table>")


def changes_html(changes: dict) -> str:
    if not changes:
        return "<p>Nothing was added, changed or removed in the company systems.</p>"
    parts = []
    for table, c in changes.items():
        parts.append(f"<p><b>{e(table)}</b>: {len(c['added'])} added, {len(c['changed'])} changed, {len(c['removed'])} removed</p>")
        parts += [f"<p>+ <code>{e(json.dumps(r, ensure_ascii=False, default=str))}</code></p>" for r in c["added"]]
        parts += [f"<p>~ was <code>{e(json.dumps(x['before'], ensure_ascii=False, default=str))}</code><br>now <code>"
                  f"{e(json.dumps(x['after'], ensure_ascii=False, default=str))}</code></p>" for x in c["changed"]]
        parts += [f"<p>- <code>{e(json.dumps(r, ensure_ascii=False, default=str))}</code></p>" for r in c["removed"]]
    return "".join(parts)


def section(title: str, body: str) -> str:
    return f"<h2>{e(title)}</h2>{body}"


def render_html(run: dict) -> str:
    outcome = run.get("outcome") or {}
    name = outcome.get("outcome", "unknown")
    metrics = run.get("metrics") or {}
    config = run.get("config") or {}
    cost = metrics.get("cost_usd")
    head = (f"<h1>Run {e(run.get('run_id'))} {badge(name, OUTCOME_BADGE.get(name, 'mute'))}</h1>"
            f"<p><b>Request:</b> {e(run.get('request'))}</p>"
            f"<p class='meta'>model {e(config.get('model'))} · audit mode {e(config.get('audit_mode'))} · human {e(config.get('human'))}"
            f" · faults {e(', '.join(config.get('faults') or []) or 'none')}"
            + (f" · scenario {e(config['scenario'])} #{e(config.get('repeat'))}" if config.get("scenario") else "") + "</p>"
            f"<p class='meta'>{e(metrics.get('steps'))} steps · {e(metrics.get('tokens_in'))} tokens in / {e(metrics.get('tokens_out'))} out · "
            f"{'about $%.3f' % cost if cost is not None else 'cost unknown'} · {e(round(metrics.get('wall_seconds') or 0))} s</p>")
    if outcome.get("detail"):
        head += f"<p>{e(outcome['detail'])}</p>"

    body = [head]
    if run.get("report"):
        body.append(section("Final report (outcome, evidence and changes are written by code)", pre(run["report"])))
    contract = run.get("contract")
    if contract:
        body.append(section("Understanding", pre(contract)))
    body.append(section("Handbook sections selected", f"<p>{e(', '.join(run.get('sop_ids') or []) or 'none')}</p>"))
    plans = "".join(f'<div class="card"><b>Plan version {e(p["version"])}</b> (step {e(p["step"])}): {e(p["reason"])}<ul>'
                    + "".join(f"<li>{'[x]' if i['done'] else '[ ]'} {e(i['text'])}</li>" for i in p["items"]) + "</ul></div>"
                    for p in run.get("plans") or [])
    body.append(section("Plan versions", plans or "<p>none</p>"))
    body.append(section("Steps: what the agent did and why", "".join(step_html(s) for s in run.get("steps") or []) or "<p>no steps</p>"))
    checks = "".join(f'<p>Step {e(c["step"])}: {badge("matched", "good") if c["passed"] else badge("MISMATCH", "bad")} {e(c["detail"])}</p>'
                     for c in run.get("self_checks") or [])
    body.append(section("Self-checks (the agent's own opinion)", checks or "<p>none</p>"))
    versions = "".join(f"<details><summary>Declared at step {e(a['step'])}</summary>{pre(a['yaml'])}</details>" for a in run.get("assertions") or [])
    body.append(section("Declared assertions (every version)", versions or "<p>none declared</p>"))
    audits = "".join(
        f'<div class="card"><b>Audit at step {e(a["step"])}</b> (round {e(a["round"])}) '
        f'{badge("passed", "good") if a["passed"] else badge("FAILED", "bad")} '
        f'{badge("shown to the agent", "info") if a["shown_to_agent"] else badge("recorded only, not shown", "mute")}'
        + (f"<p>Declared checks were rejected: {e('; '.join(a['detail']['validation_errors']))}</p>" if a["detail"].get("validation_errors")
           else checks_table(a["detail"].get("checks", []))) + "</div>" for a in run.get("audit_attempts") or [])
    body.append(section("Independent audit attempts", audits or "<p>no audit was run</p>"))
    humans = "".join(f'<div class="card">{badge("SCRIPTED" if i["scripted"] else "real person", "warn" if i["scripted"] else "good")} '
                     f'<b>{e(i["kind"])}</b>: {e(i["question"])}<br>Reply: <code>{e(i["reply"])}</code>'
                     + ("".join(f"<br><small>{e(c)}</small>" for c in i.get("context") or [])) + "</div>" for i in run.get("interactions") or [])
    body.append(section("Human interactions", humans or "<p>none</p>"))
    body.append(section("Changes observed in the company systems", changes_html(run.get("changes") or {})))
    notes = run.get("notes") or {}
    body.append(section("The agent's notes", "<ul>" + "".join(f"<li><b>{e(k)}</b>: {e(v)}</li>" for k, v in notes.items()) + "</ul>" if notes else "<p>none</p>"))
    if run.get("eval"):
        body.append(section("Evaluation record", pre(run["eval"])))
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'">'
            f"<title>Run {e(run.get('run_id'))}</title><style>{CSS}</style></head><body><main>" + "".join(body) + "</main></body></html>")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render a run.json as a static HTML timeline.")
    parser.add_argument("run_json", type=Path)
    parser.add_argument("--out", type=Path, help="default: timeline.html next to run.json")
    args = parser.parse_args(argv)
    run = json.loads(args.run_json.read_text(encoding="utf-8"))
    out = args.out or args.run_json.with_name("timeline.html")
    out.write_text(render_html(run), encoding="utf-8")
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
