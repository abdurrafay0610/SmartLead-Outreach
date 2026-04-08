"""
Google Sheets Poller API — start/stop/monitor background pollers.

Each poller watches a Google Sheet for lead JSON in Column A,
validates it, pushes to Smartlead, and writes status to Column B.
"""

from fastapi import APIRouter, HTTPException

from app.schemas.sheets_poller import (
    PollerInfo,
    PollerListResponse,
    PollerStartRequest,
    PollerStartResponse,
    PollerStopResponse,
)
from app.services.sheets_poller_service import get_poller_manager

router = APIRouter(prefix="/sheet-pollers", tags=["sheet-pollers"])


@router.post("", response_model=PollerStartResponse, status_code=201)
async def start_poller(request: PollerStartRequest):
    """
    Start a background poller for a Google Sheet.

    The poller will continuously check the specified sheet for new rows
    (Column A = JSON, Column B = empty) and process them.

    **Sheet layout:**
    - **Column A**: JSON pasted by your team (one lead per row)
    - **Column B**: Status written by the poller (OK / ERROR)

    **Expected JSON format in Column A:**
    ```json
    {
        "campaign_id": 12345,
        "email": "jane@acme.com",
        "emails": [
            {"step_number": 1, "subject": "Hey Jane", "body": "<p>...</p>"},
            {"step_number": 2, "subject": "Following up", "body": "<p>...</p>"},
            {"step_number": 3, "subject": "Quick note", "body": "<p>...</p>"},
            {"step_number": 4, "subject": "One more thing", "body": "<p>...</p>"},
            {"step_number": 5, "subject": "Last touch", "body": "<p>...</p>"}
        ],
        "first_name": "Jane",
        "last_name": "Doe",
        "company_name": "Acme Corp"
    }
    ```

    Required fields: `campaign_id`, `email`, `emails` (exactly 5 steps).
    Optional fields: `first_name`, `last_name`, `company_name`.

    **Notes:**
    - Each spreadsheet+sheet combination can only have one active poller.
    - The poller runs as a background task — this endpoint returns immediately.
    - Rows already processed (Column B not empty) are skipped.
    - Make sure the service account has Editor access to the spreadsheet.
    """
    manager = get_poller_manager()
    try:
        info = manager.start_poller(
            spreadsheet_id=request.spreadsheet_id,
            sheet_name=request.sheet_name,
            poll_interval=request.poll_interval_seconds,
        )
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))

    return PollerStartResponse(
        message=f"Poller started. Checking every {request.poll_interval_seconds}s.",
        poller=info,
    )


@router.get("", response_model=PollerListResponse)
async def list_pollers(include_stopped: bool = False):
    """
    List all active sheet pollers.

    Use `?include_stopped=true` to also see stopped pollers.
    """
    manager = get_poller_manager()
    pollers = manager.list_pollers(include_stopped=include_stopped)
    return PollerListResponse(total=len(pollers), pollers=pollers)


@router.get("/{poller_id}", response_model=PollerInfo)
async def get_poller(poller_id: str):
    """
    Get status and stats for a specific poller.
    """
    manager = get_poller_manager()
    info = manager.get_poller(poller_id)
    if not info:
        raise HTTPException(status_code=404, detail=f"Poller '{poller_id}' not found")
    return info


@router.post("/{poller_id}/stop", response_model=PollerStopResponse)
async def stop_poller(poller_id: str):
    """
    Stop a running poller.

    The poller will finish processing its current row (if any) and then stop.
    This is a graceful shutdown — it won't interrupt mid-row processing.
    """
    manager = get_poller_manager()
    try:
        info = manager.stop_poller(poller_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Poller '{poller_id}' not found")

    return PollerStopResponse(
        message=f"Poller '{poller_id}' stop requested. It will finish current work and stop.",
        poller=info,
    )