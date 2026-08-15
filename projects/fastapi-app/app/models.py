from pydantic import BaseModel

from app.db import list_items


class Item(BaseModel):
    id: int
    name: str


def all_items() -> list[Item]:
    return [Item(**i) for i in list_items()]
