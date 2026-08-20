"""On-demand fleet health recheck for the Monitoring page. See
docs/36-monitoring.md — deliberately a button, not a background poller."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from deploymint.core import monitoring
from deploymint.db.database import get_db

router = APIRouter(tags=["monitoring"])


@router.post("/api/monitoring/recheck")
async def recheck_fleet(db: Session = Depends(get_db)):
    fleet = monitoring.fleet_status(db)
    results = await monitoring.recheck_health(fleet)
    return {"results": results}
