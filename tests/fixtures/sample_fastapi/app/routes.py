from fastapi import APIRouter

from app.db import create_item
from app.models import Item, all_items

router = APIRouter()


@router.get("/items", response_model=list[Item])
def get_items():
    return all_items()


@router.post("/items", response_model=Item)
def post_item(name: str):
    return create_item(name)
