"""Authenticated aggregate view of visible chapter runs and recorded costs."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..db import get_db
from ..services import auth, run_dashboard

router = APIRouter(prefix="/run-dashboard", tags=["run-dashboard"])


@router.get("")
def get_dashboard(
    board: str = "", grade: str = "", subject: str = "", q: str = "",
    state: str = "", initiator: str = "",
    page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100),
    db: Session = Depends(get_db), user: auth.Principal = Depends(auth.require_user),
):
    return run_dashboard.snapshot(db, owner_sub=user.sub, board=board, grade=grade,
                                  subject=subject, q=q, state=state, initiator=initiator,
                                  page=page, page_size=page_size)
