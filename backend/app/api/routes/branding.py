"""Company branding API — active-company letterhead identity + logo.

Every endpoint is scoped to the authenticated **active company** (resolved by
``get_current_user`` from the validated ``X-Company-Id`` header and routed to that
company's database by ``get_company_db``), so a user only ever reads or writes the
branding of a company they are a member of, and never another company's logo.

Retrieval (GET) is open to any authenticated member; mutation (upload / update /
delete) requires the ``admin.manage`` permission.
"""
from __future__ import annotations

import base64

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.context import acting_factory_id
from app.core.deps import get_company_db, get_current_user, require_permission
from app.models.core import Branch, Company, User
from app.services import branding as svc

router = APIRouter(prefix="/api/me/branding", tags=["branding"])


def _logo_data_url(row) -> str | None:
    if row is None or not row.logo_bytes:
        return None
    b64 = base64.b64encode(row.logo_bytes).decode("ascii")
    return f"data:{row.logo_mime_type or 'image/png'};base64,{b64}"


def _identity(db: Session, user: User) -> dict:
    """Active company identity from core.Company (reused, not duplicated)."""
    company = db.get(Company, user.company_id)
    factory = None
    fid = acting_factory_id(user)
    if fid is not None:
        b = db.get(Branch, fid)
        if b is not None and b.company_id == user.company_id:
            factory = {"id": str(b.id), "code": b.code, "name": b.name}
    return {
        "company_id": str(user.company_id) if user.company_id else None,
        "legal_name": (company.legal_name or company.name) if company else None,
        "name": company.name if company else None,
        "gstin": company.gstin if company else None,
        "state_code": company.state_code if company else None,
        "address": company.address if company else None,
        "currency": company.currency if company else None,
        "factory": factory,
    }


@router.get("")
def get_my_branding(db: Session = Depends(get_company_db),
                    user: User = Depends(get_current_user)) -> dict:
    """Active company's letterhead identity + contact + logo (as a data URL).

    The frontend renders the sidebar (legal name + logo, with the selected factory
    shown separately) and print letterheads from this payload.
    """
    row = svc.get_branding(db, user.company_id)
    ident = _identity(db, user)
    ident.update({
        "cin": row.cin if row else None,
        "phone": row.phone if row else None,
        "email": row.email if row else None,
        "website": row.website if row else None,
        "has_logo": bool(row and row.logo_bytes),
        "logo_filename": row.logo_filename if row else None,
        "logo_mime_type": row.logo_mime_type if row else None,
        "logo_size_bytes": row.logo_size_bytes if row else None,
        "logo_data_url": _logo_data_url(row),
    })
    return ident


@router.get("/logo")
def get_my_logo(db: Session = Depends(get_company_db),
                user: User = Depends(get_current_user)) -> Response:
    """Stream the active company's logo bytes (for server-side PDF/letterhead)."""
    row = svc.get_branding(db, user.company_id)
    if row is None or not row.logo_bytes:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No logo for this company")
    return Response(content=row.logo_bytes, media_type=row.logo_mime_type or "image/png",
                    headers={"Content-Disposition": 'inline; filename="logo"',
                             "Cache-Control": "no-store"})


class ContactIn(BaseModel):
    cin: str | None = Field(default=None, max_length=30)
    phone: str | None = Field(default=None, max_length=40)
    email: str | None = Field(default=None, max_length=120)
    website: str | None = Field(default=None, max_length=120)


@router.put("")
def update_my_branding(payload: ContactIn, db: Session = Depends(get_company_db),
                       user: User = Depends(require_permission("admin.manage"))) -> dict:
    """Upsert the letterhead contact fields for the active company (admin only)."""
    svc.update_contact(db, user.company_id, cin=payload.cin, phone=payload.phone,
                       email=payload.email, website=payload.website, updated_by=user.id)
    db.commit()
    return get_my_branding(db=db, user=user)


@router.post("/logo", status_code=status.HTTP_201_CREATED)
async def upload_my_logo(file: UploadFile = File(...), db: Session = Depends(get_company_db),
                         user: User = Depends(require_permission("admin.manage"))) -> dict:
    """Upload/replace the active company's logo (PNG/JPEG/WebP, <= 512 KB; admin)."""
    data = await file.read()
    try:
        row = svc.set_logo(db, user.company_id, data=data, filename=file.filename,
                           content_type=file.content_type, updated_by=user.id)
    except svc.BrandingError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    db.commit()
    return {"has_logo": True, "logo_filename": row.logo_filename,
            "logo_mime_type": row.logo_mime_type, "logo_size_bytes": row.logo_size_bytes}


@router.delete("/logo")
def delete_my_logo(db: Session = Depends(get_company_db),
                   user: User = Depends(require_permission("admin.manage"))) -> dict:
    """Remove the active company's logo (keeps contact fields; admin only)."""
    svc.clear_logo(db, user.company_id, updated_by=user.id)
    db.commit()
    return {"has_logo": False}
