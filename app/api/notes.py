"""Note endpoints: record (voice or text), review, retry, approve.

The note row is the pipeline job. Recording returns immediately; transcription
and extraction run in the background and write their results onto the row.
"""

from typing import NoReturn
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import current_user
from app.billing.entitlements import Entitlements
from app.db import get_session
from app.domain.errors import DomainError
from app.models import User
from app.schemas.api import ApproveResultView, NoteView
from app.schemas.proposal import NoteProposal
from app.services import journal as journal_service
from app.services import notes as note_service
from app.services.journal import process_note

router = APIRouter(prefix="/notes", tags=["notes"])


class TextNoteIn(BaseModel):
    text: str = Field(min_length=1, max_length=5000)
    # Recording from a trade page: the note belongs to this trade.
    trade_id: UUID | None = None


class ApproveIn(BaseModel):
    """The reviewed proposal. The trader's edits are what gets applied: the AI
    only drafts, and the review screen can change every value."""

    proposal: NoteProposal | None = None


def _raise_http(exc: DomainError) -> NoReturn:
    raise HTTPException(status_code=exc.status_code, detail=exc.message)


async def _read_audio(request: Request, file: UploadFile) -> tuple[bytes, str]:
    settings = request.app.state.settings
    data = await file.read()
    mime_type = (file.content_type or "").split(";")[0].strip().lower()
    if mime_type not in settings.allowed_audio_mime_types:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported audio type {mime_type or 'unknown'}. Allowed: {', '.join(settings.allowed_audio_mime_types)}",
        )
    if not data:
        raise HTTPException(status_code=422, detail="The recording is empty.")
    if len(data) > settings.max_audio_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"The recording is too large (limit {settings.max_audio_bytes // (1024 * 1024)} MB).",
        )
    return data, mime_type


@router.post("/voice", status_code=201, response_model=NoteView)
async def receive_voice(
    request: Request,
    background: BackgroundTasks,
    file: UploadFile = File(...),
    duration_ms: int = 0,
    trade_id: UUID | None = None,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
):
    """Record a voice note. Costs one unit of the AI voice allowance."""
    settings = request.app.state.settings
    data, mime_type = await _read_audio(request, file)
    if duration_ms < 0 or duration_ms > settings.max_duration_ms:
        raise HTTPException(status_code=422, detail="The recording length is out of range.")

    entitlements: Entitlements = request.app.state.entitlements
    try:
        view = await journal_service.receive_voice_note(
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

    background.add_task(
        process_note,
        request.app.state.session_factory,
        view.id,
        request.app.state.storage,
        request.app.state.transcriber,
        request.app.state.extractor,
    )
    return view


@router.post("/text", status_code=201, response_model=NoteView)
async def receive_text(
    request: Request,
    background: BackgroundTasks,
    payload: TextNoteIn,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
):
    """Type a note instead of speaking it. No allowance unit is used."""
    try:
        view = await journal_service.receive_text_note(
            session, user, payload.text, trade_id=payload.trade_id
        )
    except DomainError as exc:
        _raise_http(exc)

    background.add_task(
        process_note,
        request.app.state.session_factory,
        view.id,
        request.app.state.storage,
        request.app.state.transcriber,
        request.app.state.extractor,
    )
    return view


@router.get("", response_model=list[NoteView])
async def list_notes(
    limit: int = 50,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
):
    """The journal timeline, newest first."""
    return await journal_service.list_notes(session, user, limit=min(max(limit, 1), 200))


@router.get("/{note_id}", response_model=NoteView)
async def get_note(
    note_id: UUID,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
):
    try:
        note = await journal_service.get_note(session, user, note_id)
    except DomainError as exc:
        _raise_http(exc)
    return await note_service.note_view(session, note, request.app.state.storage)


@router.post("/{note_id}/retry", response_model=NoteView)
async def retry_note(
    note_id: UUID,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
):
    """Re-run a failed note from the stage that broke. Never charges again."""
    try:
        return await journal_service.retry_note(
            request.app.state.session_factory,
            session,
            user,
            note_id,
            request.app.state.storage,
            request.app.state.transcriber,
            request.app.state.extractor,
        )
    except DomainError as exc:
        _raise_http(exc)


@router.delete("/{note_id}", status_code=204)
async def discard_note(
    note_id: UUID,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
):
    """Discard a note for good: the row, its audio, and the AI reading.

    Only a note that was never approved can be discarded — an approved note is
    the trade's history.
    """
    try:
        await journal_service.discard_note(
            session, user, note_id, request.app.state.storage
        )
    except DomainError as exc:
        await session.rollback()
        _raise_http(exc)
    await session.commit()


@router.post("/{note_id}/approve", response_model=ApproveResultView)
async def approve_note(
    note_id: UUID,
    request: Request,
    payload: ApproveIn | None = None,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
):
    """Approve the reviewed note. Atomic and idempotent.

    The body carries the trader's reviewed proposal; without a body the
    stored AI proposal is used as-is.
    """
    try:
        note = await journal_service.get_note(session, user, note_id)
        if payload is not None and payload.proposal is not None:
            note.proposal = payload.proposal.model_dump(mode="json", exclude_none=False)
        result = await note_service.apply_note(
            session, user, note, entitlements=request.app.state.entitlements
        )
    except DomainError as exc:
        await session.rollback()
        _raise_http(exc)
    await session.commit()
    return result
