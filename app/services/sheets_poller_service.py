"""
Google Sheets Poller Service — manages background polling tasks.

Each poller instance watches one spreadsheet/sheet tab, processing rows
where Column A has JSON and Column B is empty.

Architecture:
    - PollerManager: singleton that tracks all running pollers
    - Each poller runs as an asyncio.Task in the FastAPI event loop
    - Start/stop via the router endpoints
    - No database involvement — purely Smartlead push

Campaign validation:
    - On first encounter of a campaign_id, fetches the campaign from Smartlead
      to determine how many sequences (email steps) it has.
    - Caches the result so subsequent rows with the same campaign_id don't
      re-fetch.
    - If the campaign doesn't exist on Smartlead, the row gets an error.
    - If the number of emails in the JSON doesn't match the campaign's
      sequence count, the row gets a descriptive error.
"""

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

import gspread
from google.oauth2.service_account import Credentials
from pydantic import ValidationError

from app.core.config import get_settings
from app.schemas.sheets_poller import PollerInfo, SheetLeadJSON
from app.services.smartlead_client import (
    SmartleadAPIError,
    SmartleadNotFoundError,
    get_smartlead_client,
)

logger = logging.getLogger(__name__)

settings = get_settings()

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


# ---------------------------------------------------------------------------
# Google Sheets auth
# ---------------------------------------------------------------------------

def _get_gspread_client() -> gspread.Client:
    """Create an authenticated gspread client using service account."""
    creds = Credentials.from_service_account_file(
        settings.GOOGLE_SERVICE_ACCOUNT_FILE,
        scopes=SCOPES,
    )
    return gspread.authorize(creds)


# ---------------------------------------------------------------------------
# Campaign sequence cache
# ---------------------------------------------------------------------------

class _CampaignCache:
    """
    Caches campaign sequence counts fetched from Smartlead.

    Stores either:
        campaign_id -> sequence_count (int)  for valid campaigns
        campaign_id -> error_string (str)    for invalid/errored campaigns
    """

    def __init__(self):
        self._cache: dict[str, int | str] = {}

    async def get_sequence_count(self, campaign_id: int | str) -> int | str:
        """
        Get the number of sequences for a campaign.

        Returns:
            int: sequence count (success)
            str: error message (failure — campaign not found, API error, etc.)
        """
        key = str(campaign_id)
        if key in self._cache:
            return self._cache[key]

        # Fetch from Smartlead
        try:
            async with get_smartlead_client() as sl:
                campaign_data = await sl.get_campaign(campaign_id)
        except SmartleadNotFoundError:
            error = f"Campaign {campaign_id} not found on Smartlead"
            self._cache[key] = error
            return error
        except SmartleadAPIError as e:
            error = f"Failed to fetch campaign {campaign_id}: {e}"
            self._cache[key] = error
            return error
        except Exception as e:
            error = f"Unexpected error fetching campaign {campaign_id}: {e}"
            self._cache[key] = error
            return error

        # Extract sequence count from the campaign response
        # Smartlead returns sequences as a list in the campaign object
        sequences = campaign_data.get("sequences") or []
        if isinstance(sequences, list):
            count = len(sequences)
        else:
            count = 0

        if count == 0:
            error = (
                f"Campaign {campaign_id} has no sequences configured on Smartlead. "
                f"Set up sequences first before adding leads."
            )
            self._cache[key] = error
            return error

        self._cache[key] = count
        logger.info("Cached campaign %s: %d sequences", campaign_id, count)
        return count

    def invalidate(self, campaign_id: int | str):
        """Remove a campaign from cache (e.g., if we suspect stale data)."""
        self._cache.pop(str(campaign_id), None)

    def clear(self):
        """Clear entire cache."""
        self._cache.clear()


# ---------------------------------------------------------------------------
# Single-row processing
# ---------------------------------------------------------------------------

def validate_row_json(raw_text: str) -> tuple[SheetLeadJSON | None, str | None]:
    """
    Parse and validate JSON from a sheet cell using the Pydantic schema.

    This does basic structural validation only (required fields, types).
    Campaign-specific validation (email count vs sequence count) is done
    separately after this passes.

    Returns:
        (parsed_model, None) on success
        (None, error_string) on failure
    """
    # Step 1: parse raw JSON
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as e:
        return None, f"Invalid JSON: {e}"

    if not isinstance(data, dict):
        return None, "JSON must be an object, not array/string/number"

    # Step 2: validate with Pydantic
    try:
        model = SheetLeadJSON(**data)
    except ValidationError as e:
        # Flatten Pydantic errors into a readable string
        error_parts = []
        for err in e.errors():
            loc = " -> ".join(str(x) for x in err["loc"])
            error_parts.append(f"{loc}: {err['msg']}")
        return None, "; ".join(error_parts)

    # Step 3: check email format (basic)
    if "@" not in model.email:
        return None, "'email' must be a valid email address"

    # Step 4: check step_numbers are sequential 1..N with no gaps
    step_numbers = sorted(e.step_number for e in model.emails)
    expected = list(range(1, len(model.emails) + 1))
    if step_numbers != expected:
        return None, f"Step numbers must be sequential {expected}, got {step_numbers}"

    return model, None


async def validate_against_campaign(
    lead: SheetLeadJSON,
    campaign_cache: _CampaignCache,
) -> str | None:
    """
    Validate that the lead's email count matches the campaign's sequence count.

    Returns:
        None if valid
        Error string if there's a mismatch or the campaign is invalid
    """
    result = await campaign_cache.get_sequence_count(lead.campaign_id)

    # If result is a string, it's an error message
    if isinstance(result, str):
        return result

    # result is an int — the expected sequence count
    expected_count = result
    actual_count = len(lead.emails)

    if actual_count != expected_count:
        return (
            f"Email count mismatch: campaign {lead.campaign_id} has "
            f"{expected_count} sequence(s), but you provided {actual_count} email(s). "
            f"Provide exactly {expected_count} email(s) with step_numbers 1 through {expected_count}."
        )

    return None


async def push_lead_to_smartlead(lead: SheetLeadJSON) -> str:
    """
    Push a single validated lead to Smartlead.

    Returns:
        Status string for Column B.

    Raises:
        Any Smartlead exception — caller is responsible for catching and
        writing the error to the sheet.
    """
    # Build the lead payload matching add_leads_to_campaign format
    custom_fields: dict[str, str] = {}
    for step_email in lead.emails:
        n = step_email.step_number
        custom_fields[f"email_subject_{n}"] = step_email.subject
        custom_fields[f"email_body_{n}"] = step_email.body

    sl_lead = {
        "email": lead.email.strip().lower(),
        "first_name": lead.first_name,
        "last_name": lead.last_name,
        "company_name": lead.company_name,
        "custom_fields": custom_fields,
    }

    async with get_smartlead_client() as sl:
        result = await sl.add_leads(
            campaign_id=lead.campaign_id,
            lead_list=[sl_lead],
        )

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    return f"OK - pushed to campaign {lead.campaign_id} at {timestamp}"


# ---------------------------------------------------------------------------
# Poller state
# ---------------------------------------------------------------------------

class _PollerState:
    """Internal state for a single poller instance."""

    def __init__(
        self,
        poller_id: str,
        spreadsheet_id: str,
        sheet_name: str,
        poll_interval: int,
    ):
        self.poller_id = poller_id
        self.spreadsheet_id = spreadsheet_id
        self.sheet_name = sheet_name
        self.poll_interval = poll_interval
        self.status = "running"
        self.started_at = datetime.now(timezone.utc)
        self.rows_processed = 0
        self.rows_succeeded = 0
        self.rows_failed = 0
        self.last_poll_at: datetime | None = None
        self.last_error: str | None = None
        self.task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        # Per-poller campaign cache — avoids re-fetching campaign info
        # for every single row with the same campaign_id
        self.campaign_cache = _CampaignCache()

    def request_stop(self):
        self._stop_event.set()

    @property
    def should_stop(self) -> bool:
        return self._stop_event.is_set()

    def to_info(self) -> PollerInfo:
        return PollerInfo(
            poller_id=self.poller_id,
            spreadsheet_id=self.spreadsheet_id,
            sheet_name=self.sheet_name,
            poll_interval_seconds=self.poll_interval,
            status=self.status,
            started_at=self.started_at,
            rows_processed=self.rows_processed,
            rows_succeeded=self.rows_succeeded,
            rows_failed=self.rows_failed,
            last_poll_at=self.last_poll_at,
            last_error=self.last_error,
        )


# ---------------------------------------------------------------------------
# Poll loop (runs as background asyncio.Task)
# ---------------------------------------------------------------------------

async def _poll_loop(state: _PollerState):
    """Background task that polls the sheet until stopped."""
    logger.info(
        "Poller %s started: spreadsheet=%s, sheet=%s, interval=%ds",
        state.poller_id,
        state.spreadsheet_id,
        state.sheet_name,
        state.poll_interval,
    )

    try:
        gc = _get_gspread_client()
        spreadsheet = gc.open_by_key(state.spreadsheet_id)
        sheet = spreadsheet.worksheet(state.sheet_name)
        logger.info("Poller %s connected to sheet '%s'", state.poller_id, state.sheet_name)
    except Exception as e:
        state.status = "stopped"
        state.last_error = f"Failed to connect: {e}"
        logger.error("Poller %s failed to connect: %s", state.poller_id, e)
        return

    while not state.should_stop:
        try:
            await _poll_once(state, sheet)
            state.last_poll_at = datetime.now(timezone.utc)
            state.last_error = None
        except gspread.exceptions.APIError as e:
            state.last_error = f"Google Sheets API error: {e}"
            logger.error("Poller %s Sheets API error: %s", state.poller_id, e)
        except Exception as e:
            state.last_error = f"Unexpected error: {e}"
            logger.exception("Poller %s unexpected error", state.poller_id)

        # Wait for interval or stop signal
        try:
            await asyncio.wait_for(
                state._stop_event.wait(),
                timeout=state.poll_interval,
            )
            # If we reach here, stop was requested
            break
        except asyncio.TimeoutError:
            # Normal — interval elapsed, loop again
            continue

    state.status = "stopped"
    logger.info(
        "Poller %s stopped. Processed %d rows (%d ok, %d failed)",
        state.poller_id,
        state.rows_processed,
        state.rows_succeeded,
        state.rows_failed,
    )


async def _poll_once(state: _PollerState, sheet: gspread.Worksheet):
    """Process all unhandled rows in one poll cycle."""
    # Run the blocking gspread call in a thread to avoid blocking the event loop
    all_values = await asyncio.to_thread(sheet.get_all_values)

    for row_idx, row in enumerate(all_values):
        if state.should_stop:
            break

        row_num = row_idx + 1  # 1-based

        col_a = row[0].strip() if len(row) > 0 else ""
        col_b = row[1].strip() if len(row) > 1 else ""

        # Skip empty or already-processed rows
        if not col_a or col_b:
            continue

        logger.info("Poller %s processing row %d", state.poller_id, row_num)

        # --- Phase 1: Basic JSON/schema validation ---
        lead, error = validate_row_json(col_a)

        if error:
            status_text = f"ERROR - {error}"
            state.rows_failed += 1
            logger.warning("Poller %s row %d validation error: %s", state.poller_id, row_num, error)

        else:
            # --- Phase 2: Campaign validation (exists? email count matches?) ---
            campaign_error = await validate_against_campaign(lead, state.campaign_cache)

            if campaign_error:
                status_text = f"ERROR - {campaign_error}"
                state.rows_failed += 1
                logger.warning(
                    "Poller %s row %d campaign validation error: %s",
                    state.poller_id, row_num, campaign_error,
                )

            else:
                # --- Phase 3: Push to Smartlead ---
                try:
                    status_text = await push_lead_to_smartlead(lead)
                    state.rows_succeeded += 1
                    logger.info("Poller %s row %d: %s", state.poller_id, row_num, status_text)

                except SmartleadNotFoundError as e:
                    # Campaign was valid when cached but now returns 404
                    # Invalidate cache so next row re-checks
                    state.campaign_cache.invalidate(lead.campaign_id)
                    status_text = f"ERROR - Campaign {lead.campaign_id} not found on Smartlead: {e}"
                    state.rows_failed += 1
                    logger.error("Poller %s row %d: %s", state.poller_id, row_num, status_text)

                except SmartleadAPIError as e:
                    status_text = f"ERROR - Smartlead API error: {e}"
                    state.rows_failed += 1
                    logger.error("Poller %s row %d Smartlead error: %s", state.poller_id, row_num, e)

                except Exception as e:
                    status_text = f"ERROR - Unexpected: {e}"
                    state.rows_failed += 1
                    logger.error("Poller %s row %d unexpected error: %s", state.poller_id, row_num, e)

        state.rows_processed += 1

        # Write status to Column B (in thread to avoid blocking)
        await asyncio.to_thread(sheet.update_cell, row_num, 2, status_text)


# ---------------------------------------------------------------------------
# Poller Manager (singleton)
# ---------------------------------------------------------------------------

class PollerManager:
    """
    Manages all active sheet poller instances.

    Usage (from router):
        manager = get_poller_manager()
        info = manager.start_poller(spreadsheet_id, sheet_name, interval)
        manager.stop_poller(poller_id)
        all_pollers = manager.list_pollers()
    """

    def __init__(self):
        self._pollers: dict[str, _PollerState] = {}

    def start_poller(
        self,
        spreadsheet_id: str,
        sheet_name: str,
        poll_interval: int = 60,
    ) -> PollerInfo:
        """
        Start a new background poller for the given spreadsheet/sheet.

        Returns PollerInfo with the assigned poller_id.
        Raises ValueError if this exact spreadsheet+sheet combo is already being polled.
        """
        # Check for duplicate
        for state in self._pollers.values():
            if (
                state.spreadsheet_id == spreadsheet_id
                and state.sheet_name == sheet_name
                and state.status == "running"
            ):
                raise ValueError(
                    f"Already polling spreadsheet={spreadsheet_id} "
                    f"sheet={sheet_name} (poller_id={state.poller_id}). "
                    f"Stop it first before starting a new one."
                )

        poller_id = str(uuid.uuid4())[:8]  # short ID for convenience
        state = _PollerState(
            poller_id=poller_id,
            spreadsheet_id=spreadsheet_id,
            sheet_name=sheet_name,
            poll_interval=poll_interval,
        )

        # Launch as asyncio background task
        task = asyncio.create_task(
            _poll_loop(state),
            name=f"poller-{poller_id}",
        )
        state.task = task

        # Clean up from dict when task finishes
        task.add_done_callback(lambda _t: self._on_task_done(poller_id))

        self._pollers[poller_id] = state
        logger.info("Started poller %s for %s/%s", poller_id, spreadsheet_id, sheet_name)

        return state.to_info()

    def stop_poller(self, poller_id: str) -> PollerInfo:
        """
        Stop a running poller by its ID.

        Returns the final PollerInfo.
        Raises KeyError if poller_id not found.
        """
        state = self._pollers.get(poller_id)
        if not state:
            raise KeyError(f"Poller '{poller_id}' not found")

        if state.status != "running":
            return state.to_info()

        state.request_stop()
        logger.info("Requested stop for poller %s", poller_id)

        return state.to_info()

    def stop_all(self):
        """Stop all running pollers. Called during app shutdown."""
        for state in self._pollers.values():
            if state.status == "running":
                state.request_stop()
        logger.info("Requested stop for all %d pollers", len(self._pollers))

    def get_poller(self, poller_id: str) -> PollerInfo | None:
        """Get info about a specific poller."""
        state = self._pollers.get(poller_id)
        return state.to_info() if state else None

    def list_pollers(self, include_stopped: bool = False) -> list[PollerInfo]:
        """List all pollers (optionally including stopped ones)."""
        results = []
        for state in self._pollers.values():
            if include_stopped or state.status == "running":
                results.append(state.to_info())
        return results

    def _on_task_done(self, poller_id: str):
        """Callback when a poller task finishes."""
        state = self._pollers.get(poller_id)
        if state:
            state.status = "stopped"
            logger.info("Poller %s task completed", poller_id)


# ---------------------------------------------------------------------------
# Singleton access
# ---------------------------------------------------------------------------

_manager: PollerManager | None = None


def get_poller_manager() -> PollerManager:
    """Get or create the global PollerManager singleton."""
    global _manager
    if _manager is None:
        _manager = PollerManager()
    return _manager