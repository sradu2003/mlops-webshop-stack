"""Asset metadata routes.

Binary media is NOT stored in Postgres. Assets live in the object store
(S3-compatible, e.g. MinIO in dev / S3 or GCS in production); this router
manages the metadata row that points at the object and tracks the
processing state of the transcoding pipeline.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Asset, AssetKind, AssetStatus, Content, User
from app.permissions import assert_can_edit
from app.schemas import AssetIn, AssetOut
from app.security import get_current_user

router = APIRouter(prefix="/api/assets", tags=["assets"])

WRITER_ROLES = {"author", "curator", "admin", "owner"}


@router.get("", response_model=list[AssetOut])
def list_assets(
    db: Session = Depends(get_db),
    content_id: int | None = None,
) -> list[Asset]:
    stmt = select(Asset)
    if content_id is not None:
        stmt = stmt.where(Asset.content_id == content_id)
    return list(db.scalars(stmt.order_by(Asset.created_at.desc())).all())


@router.get("/{asset_id}", response_model=AssetOut)
def get_asset(asset_id: int, db: Session = Depends(get_db)) -> Asset:
    asset = db.get(Asset, asset_id)
    if asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return asset


@router.post("", response_model=AssetOut, status_code=status.HTTP_201_CREATED)
def register_asset(
    payload: AssetIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Asset:
    """Register an asset that has been uploaded to the object store.

    The client uploads the bytes directly to the object store (presigned
    PUT), then calls this endpoint to create the metadata row. This keeps
    large uploads out of the API process entirely.

    AUTHORISATION: the caller must be able to EDIT the target content.
    Previously any authenticated account could attach an asset to any
    content item, including content owned by somebody else — which let a
    stranger inject media into another author's published work.
    """
    if user.role.value not in WRITER_ROLES and user.role.value != "user":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Not permitted"
        )
    content = db.get(Content, payload.content_id)
    if content is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Content not found"
        )
    assert_can_edit(content, user)

    storage_key = f"contents/{payload.content_id}/{payload.filename}"
    asset = Asset(
        content_id=payload.content_id,
        uploaded_by_id=user.id,
        kind=AssetKind(payload.kind),
        status=AssetStatus.uploading,
        filename=payload.filename,
        storage_key=storage_key,
        mime_type=payload.mime_type,
        size_bytes=payload.size_bytes,
        sha256=payload.sha256,
    )
    db.add(asset)
    db.commit()
    db.refresh(asset)
    return asset


@router.post("/{asset_id}/complete", response_model=AssetOut)
def mark_asset_ready(
    asset_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Asset:
    """Mark an asset as processed and ready for consumption."""
    asset = db.get(Asset, asset_id)
    if asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if user.role.value not in {"admin", "owner"} and asset.uploaded_by_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Not permitted"
        )
    asset.status = AssetStatus.ready
    db.commit()
    db.refresh(asset)
    return asset


@router.delete(
    "/{asset_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    response_class=Response,
)
def delete_asset(
    asset_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> None:
    asset = db.get(Asset, asset_id)
    if asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if user.role.value not in {"admin", "owner"} and asset.uploaded_by_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Not permitted"
        )
    db.delete(asset)
    db.commit()
