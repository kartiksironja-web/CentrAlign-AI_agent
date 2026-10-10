"""When are two names similar enough that a human must say which one is meant?

Purpose: one tiny function, `similar(a, b, ignore_words, min_shared)`, used by the gate before a write
that points at a named record (see `Entity` in connectors.py).
The rule, in words:
  1. Lower-case both names and cut them into words (letters and digits only).
  2. Drop the words the company says carry no identity (for example legal suffixes). That list is
     company knowledge, so it comes from the connector manifest, not from this file.
  3. The names are similar if the shorter one has at least `min_shared` (default one half) of its words
     in the other one.
Examples with the suffix words dropped: "Foo Bar Co" and "Foo Bar Ltd" -> both "foo bar": similar.
"Foo Bar" and "Foo Baz": one of two words shared (0.5): similar. "Foo Bar" and "Qux": nothing shared: not similar.
Why this design: a person can check it by eye, it needs no model and no library. Being too eager only costs
one question to the requester; being too shy costs a wrong write, so the threshold errs on asking.
How it can fail: it compares words, not spellings, so "Foo" and "Fooo" are NOT similar, and two
different companies that share a first word ARE. Both are accepted trade-offs and are listed in the docs.
"""
import re


def name_words(name: str, ignore_words: tuple[str, ...] = ()) -> set[str]:
    ignored = {w.lower() for w in ignore_words}
    return {w for w in re.findall(r"[a-z0-9]+", str(name).lower()) if w not in ignored}


def similar(a: str, b: str, ignore_words: tuple[str, ...] = (), min_shared: float = 0.5) -> bool:
    words_a, words_b = name_words(a, ignore_words), name_words(b, ignore_words)
    if not words_a or not words_b:
        return False
    return len(words_a & words_b) / min(len(words_a), len(words_b)) >= min_shared
