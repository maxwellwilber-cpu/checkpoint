"""
Reading values out of nested data by path.

Checks need to say "the price field inside every item" without knowing the shape of the
document in advance. Paths do that, in the notation people already expect from JSON
tooling:

    "total"              a top-level key
    "invoice.total"      nested
    "items[0].price"     one element
    "items[].price"      EVERY element  <- the important one

The wildcard is what makes rules reusable. A rule written against "items[].price" works
on a document with three items or three hundred, and every finding it produces carries
the concrete path ("items[7].price") so the person reading the report knows exactly
which one is wrong.
"""

import re

# The -? matters: without it "items[-1]" parses as a dict key named "-1" and silently
# resolves to nothing, which looks exactly like a missing field.
_SEGMENT = re.compile(r"([^.\[\]]+)|\[(-?\d*)\]")


def _parse(path):
    """Turn 'items[].price' into ['items', ('index', None), 'price']."""
    parts = []
    for key, index in _SEGMENT.findall(path):
        if key:
            parts.append(key)
        else:
            parts.append(("index", int(index) if index not in ("", None) else None))
    return parts


def resolve(data, path):
    """
    Yield (concrete_path, value) for every location matching `path`.

    Missing keys yield nothing rather than raising. "This field is absent" is a job for
    a required-fields check, which can report it properly; a crash deep inside a path
    walker cannot.
    """
    if not path:
        yield "", data
        return
    yield from _walk(data, _parse(path), "")


def _walk(node, parts, trail):
    if not parts:
        yield trail, node
        return

    head, rest = parts[0], parts[1:]

    if isinstance(head, tuple):  # a [] or [n] segment
        _, index = head
        if not isinstance(node, (list, tuple)):
            return
        if index is None:
            for i, item in enumerate(node):
                yield from _walk(item, rest, f"{trail}[{i}]")
        elif -len(node) <= index < len(node):
            yield from _walk(node[index], rest, f"{trail}[{index}]")
        return

    if isinstance(node, dict):
        if head in node:
            new_trail = f"{trail}.{head}" if trail else head
            yield from _walk(node[head], rest, new_trail)
    return


def exists(data, path):
    """True when at least one location matches."""
    return any(True for _ in resolve(data, path))
