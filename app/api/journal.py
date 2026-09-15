"""Journal endpoints: capture, inspect, retry, approve.

The receive endpoint is intentionally the ONLY place allowance is consumed:
manual journaling and review/approval cost nothing (monetization spec).
"""

from uuid import UUID
from typing import NoReturn

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Request, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import current_user
from app.billing.entitlements import Entitlements
from app.config import settings
from app.db import SessionLocal, get_session
from app.domain.errors import DomainError, JournalEntryNotFoundError
from app.models import User
from app.schemas.actions import ActionsPayload
from app.schemas.api import ApprovalResultView, JournalEntryView
from app.services.actions import approve_journal_entry
from app.services.journal import (
    process_entry,
    receive_journal,
    retry_journal,
)

router = APIRouter(prefix="/journal", tags=["journal"])


def _raise_http(exc: DomainError) -> NoReturn:
    raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.post("", status_code=201, response_model=JournalEntryView)
async def receive(
    request: Request,
    background: BackgroundTasks,
    file: UploadFile = File(...),
    duration_ms: int = Form(...),
    # "Recording from an active Trade screen" context (spec: resolving later
    # notes). Ownership is verified by the service; the extractor uses this id
    # verbatim instead of inventing or sentinel-marking it.
    trade_id: UUID | None = Form(default=None),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
):
    mime_type = file.content_type or ""
    if mime_type not in settings.allowed_audio_mime_types:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported audio type '{mime_type}'; allowed: "
            f"{', '.join(settings.allowed_audio_mime_types)}",
        )
    if not 0 < duration_ms <= settings.max_duration_ms:
        raise HTTPException(
            status_code=422, detail="duration_ms must be between 1 and "
            f"{settings.max_duration_ms}"
        )
    data = await file.read()
    if len(data) > settings.max_audio_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"Recording too large ({len(data)} bytes; max "
            f"{settings.max_audio_bytes})",
        )
    if len(data) == 0:
        raise HTTPException(status_code=422, detail="Empty recording")

    entitlements: Entitlements = request.app.state.entitlements
    try:
        view = await receive_journal(
            session,
            user,
            data,
            mime_type,
            duration_ms,
            request.app.state.storage,
            entitlements,
            trade_id=trade_id,
        )
    except DomainError as exc:
        _raise_http(exc)

    # Transcribe + extract after the response; progress is durable in the
    # entry's status, and retry resumes from the stalled stage.
    background.add_task(
        process_entry,
        view.id,
        request.app.state.storage,
        request.app.state.transcriber,
        request.app.state.extractor,
        entitlements,
        SessionLocal,
    )
    return view


@router.get("/{entry_id}", response_model=JournalEntryView)
async def get_entry(
    request: Request,
    entry_id: UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
):
    from app.services.journal import journal_view

    try:
        return await journal_view(session, user.id, entry_id, request.app.state.storage)
    except JournalEntryNotFoundError as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")  # keeps control flow explicit


@router.post("/{entry_id}/retry", response_model=JournalEntryView)
async def retry(
    request: Request,
    entry_id: UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
):
    try:
        return await retry_journal(
            session,
            user,
            entry_id,
            request.app.state.storage,
            request.app.state.transcriber,
            request.app.state.extractor,
            request.app.state.entitlements,
        )
    except DomainError as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")  # keeps control flow explicit


@router.post("/{entry_id}/approve", response_model=ApprovalResultView)
async def approve(
    entry_id: UUID,
    payload: ActionsPayload,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
):
    try:
        return await approve_journal_entry(session, user, entry_id, payload)
    except DomainError as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")  # keeps control flow explicit