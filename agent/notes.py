"""Notes: facts the agent writes down so it does not have to rediscover them.

Purpose: `note(key, value)` stores a short fact ("latest_id" -> "INV-...") that the loop
shows in every later step, since the model's context is rebuilt from state each step and
does not carry the whole conversation.
Why this design: a small, capped dict. Caps keep one runaway note (a pasted page) from
filling the context; every change is kept in `events` for the run log.
How it can fail: notes are the model's own words, never evidence; the auditor does not read
them. If the key limit is reached, new keys are refused with a message.
"""
MAX_KEY = 60
MAX_VALUE = 500
MAX_NOTES = 40


class Notes:
    def __init__(self):
        self._items: dict[str, str] = {}
        self.events: list[dict] = []

    def set(self, key: str, value: str) -> str:
        key, value = key.strip(), value.strip()
        if not key:
            return "NOT SAVED: the key is empty."
        if len(key) > MAX_KEY:
            return f"NOT SAVED: keys are limited to {MAX_KEY} characters."
        if key not in self._items and len(self._items) >= MAX_NOTES:
            return f"NOT SAVED: at most {MAX_NOTES} notes; reuse a key or overwrite one."
        cut = len(value) > MAX_VALUE
        value = value[:MAX_VALUE]
        self.events.append({"key": key, "value": value, "overwrote": key in self._items})
        self._items[key] = value
        return f"Noted {key!r}." + (f" (value cut to {MAX_VALUE} characters)" if cut else "")

    def items(self) -> dict[str, str]:
        return dict(self._items)

    def render(self) -> str:
        return "\n".join(f"- {k}: {v}" for k, v in self._items.items()) or "(no notes yet)"
