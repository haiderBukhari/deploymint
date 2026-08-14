from fastapi import APIRouter

from deploymint import __version__
from deploymint.core.doctor import run_checks

router = APIRouter()


@router.get("/health")
def health():
    return {"status": "ok", "version": __version__}


@router.get("/api/doctor")
async def doctor():
    checks = await run_checks()
    return {"checks": checks, "ok": all(c["status"] != "fail" for c in checks)}
