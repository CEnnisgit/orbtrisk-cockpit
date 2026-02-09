import io
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from fpdf import FPDF
from sqlalchemy.orm import Session

from app import auth
from app import models
from app.database import get_db

router = APIRouter()


@router.get("/audit/export")
def export_audit(request: Request, format: str = "csv", db: Session = Depends(get_db)):
    auth.require_business(request)
    entries = db.query(models.AuditLog).order_by(models.AuditLog.id.asc()).all()

    if format == "csv":
        buffer = io.StringIO()
        buffer.write("id,entity_type,entity_id,hash,prev_hash,created_at\n")
        for entry in entries:
            buffer.write(
                f"{entry.id},{entry.entity_type},{entry.entity_id},{entry.hash},{entry.prev_hash or ''},{entry.created_at.isoformat()}\n"
            )
        buffer.seek(0)
        return StreamingResponse(
            io.BytesIO(buffer.getvalue().encode("utf-8")),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=audit_export.csv"},
        )

    if format == "pdf":
        pdf = FPDF()
        pdf.set_auto_page_break(auto=True, margin=15)
        pdf.add_page()
        pdf.set_font("Helvetica", size=12)
        pdf.cell(0, 10, "Audit Log Export", ln=True)
        pdf.set_font("Helvetica", size=9)
        for entry in entries:
            line = f"{entry.id} | {entry.entity_type}:{entry.entity_id} | {entry.hash[:12]} | {entry.created_at.isoformat()}"
            pdf.multi_cell(0, 6, line)
        pdf_out = pdf.output(dest="S")
        pdf_bytes = pdf_out.encode("latin-1") if isinstance(pdf_out, str) else bytes(pdf_out)
        return StreamingResponse(
            io.BytesIO(pdf_bytes),
            media_type="application/pdf",
            headers={"Content-Disposition": "attachment; filename=audit_export.pdf"},
        )

    raise HTTPException(status_code=400, detail="format must be csv or pdf")
