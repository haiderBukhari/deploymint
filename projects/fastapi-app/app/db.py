"""Shared, in-memory 'database' — imported by both routes.py and models.py so
the dependency graph has a genuinely critical, heavily-imported module."""

_items: dict[int, dict] = {}
_next_id = 1


def create_item(name: str) -> dict:
    global _next_id
    item = {"id": _next_id, "name": name}
    _items[_next_id] = item
    _next_id += 1
    return item


def get_item(item_id: int) -> dict | None:
    return _items.get(item_id)


def list_items() -> list[dict]:
    return list(_items.values())
