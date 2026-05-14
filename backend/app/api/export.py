from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy.orm import Session

from ..db import get_db
from ..services import export as export_svc

router = APIRouter(prefix="/export", tags=["export"])


@router.get("/bulk-upload")
def export_bulk_upload(db: Session = Depends(get_db)):
    data = export_svc.export_workbook(db)
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="aegis_bulk_upload.xlsx"'},
    )
