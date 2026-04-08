"""
Google Sheets Poller — watches Column A for lead JSON, pushes to Smartlead,
writes status/errors to Column B.

Sheet layout:
    Column A: JSON (one per row) — pasted by teammates
    Column B: Status — written by this script ("OK - pushed to Smartlead" or error details)

JSON format expected in each cell:
{
    "campaign_id": 12345,              # required (Smartlead campaign ID)
    "email": "jane@acme.com",          # required
    "emails": [                        # required, exactly 5 step emails
        {"step_number": 1, "subject": "...", "body": "<p>...</p>"},
        {"step_number": 2, "subject": "...", "body": "<p>...</p>"},
        {"step_number": 3, "subject": "...", "body": "<p>...</p>"},
        {"step_number": 4, "subject": "...", "body": "<p>...</p>"},
        {"step_number": 5, "subject": "...", "body": "<p>...</p>"}
    ],
    "first_name": "Jane",              # optional
    "last_name": "Doe",                # optional
    "company_name": "Acme Corp"        # optional
}

Usage:
    1. Set environment variables:
        SMARTLEAD_API_KEY=your_key
        GOOGLE_SHEETS_SPREADSHEET_ID=your_spreadsheet_id
        GOOGLE_SERVICE_ACCOUNT_FILE=path/to/service-account.json

    2. Optional env vars:
        POLL_INTERVAL_SECONDS=60        (default: 60)
        SHEET_NAME=Sheet1               (default: Sheet1)

    3. Run:
        python sheets_poller.py
"""

import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone

import gspread
from google.oauth2.service_account import Credentials

from add_leads_to_campaign import add_leads_to_campaign
from app.core.config import get_settings

settings = get_settings()

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

SPREADSHEET_ID = settings.GOOGLE_SHEETS_SPREADSHEET_ID
SERVICE_ACCOUNT_FILE = settings.GOOGLE_SERVICE_ACCOUNT_FILE
SHEET_NAME = os.environ.get("SHEET_NAME", "Sheet1")
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL_SECONDS", "60"))

REQUIRED_EMAIL_STEPS = 5

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Google Sheets client
# ---------------------------------------------------------------------------

def get_sheets_client() -> gspread.Spreadsheet:
    """Authenticate and return the spreadsheet."""
    creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    gc = gspread.authorize(creds)
    return gc.open_by_key(SPREADSHEET_ID)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_lead_json(raw_text: str) -> tuple[dict | None, str | None]:
    """
    Parse and validate a JSON string from a sheet cell.

    Returns:
        (parsed_dict, None) on success
        (None, error_message) on failure
    """
    # --- Parse JSON ---
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as e:
        return None, f"Invalid JSON: {e}"

    if not isinstance(data, dict):
        return None, "JSON must be an object (not array/string/number)"

    errors: list[str] = []

    # --- Required: campaign_id ---
    campaign_id = data.get("campaign_id")
    if campaign_id is None:
        errors.append("Missing required field: 'campaign_id'")
    elif not isinstance(campaign_id, (int, str)):
        errors.append("'campaign_id' must be a number or string")

    # --- Required: email ---
    email = data.get("email")
    if not email:
        errors.append("Missing required field: 'email'")
    elif not isinstance(email, str) or "@" not in email:
        errors.append("'email' must be a valid email address")

    # --- Required: emails (list of 5 step emails) ---
    emails = data.get("emails")
    if emails is None:
        errors.append("Missing required field: 'emails'")
    elif not isinstance(emails, list):
        errors.append("'emails' must be a list")
    else:
        if len(emails) != REQUIRED_EMAIL_STEPS:
            errors.append(
                f"'emails' must have exactly {REQUIRED_EMAIL_STEPS} items, got {len(emails)}"
            )

        seen_steps: set[int] = set()
        for i, step in enumerate(emails):
            prefix = f"emails[{i}]"

            if not isinstance(step, dict):
                errors.append(f"{prefix}: must be an object")
                continue

            # step_number
            sn = step.get("step_number")
            if sn is None:
                errors.append(f"{prefix}: missing 'step_number'")
            elif not isinstance(sn, int) or sn < 1:
                errors.append(f"{prefix}: 'step_number' must be a positive integer")
            else:
                if sn in seen_steps:
                    errors.append(f"{prefix}: duplicate step_number {sn}")
                seen_steps.add(sn)

            # subject
            subj = step.get("subject")
            if not subj or not isinstance(subj, str):
                errors.append(f"{prefix}: missing or empty 'subject'")

            # body
            body = step.get("body")
            if not body or not isinstance(body, str):
                errors.append(f"{prefix}: missing or empty 'body'")

        # Check that step numbers are exactly 1-5
        if not errors or all("step_number" not in e for e in errors):
            expected_steps = set(range(1, REQUIRED_EMAIL_STEPS + 1))
            if seen_steps and seen_steps != expected_steps:
                missing = expected_steps - seen_steps
                if missing:
                    errors.append(f"Missing step_numbers: {sorted(missing)}")

    if errors:
        return None, "; ".join(errors)

    return data, None


# ---------------------------------------------------------------------------
# Process a single row
# ---------------------------------------------------------------------------

async def process_row(data: dict) -> str:
    """
    Push a validated lead dict to Smartlead.

    Returns:
        Status string to write into Column B.
    """
    campaign_id = data["campaign_id"]
    lead_payload = {
        "email": data["email"],
        "emails": data["emails"],
        "first_name": data.get("first_name", ""),
        "last_name": data.get("last_name", ""),
        "company_name": data.get("company_name", ""),
    }

    result = await add_leads_to_campaign(
        smartlead_campaign_id=campaign_id,
        leads=[lead_payload],
    )

    if result.errors:
        return f"ERROR - Smartlead API: {'; '.join(result.errors)}"

    if result.total_skipped > 0:
        reasons = "; ".join(f"{k}: {v}" for k, v in result.skipped_reasons.items())
        return f"SKIPPED - {reasons}"

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    return f"OK - pushed to campaign {campaign_id} at {timestamp}"


# ---------------------------------------------------------------------------
# Poll loop
# ---------------------------------------------------------------------------

async def poll_once(sheet: gspread.Worksheet) -> int:
    """
    Scan all rows. Process any row where Column A has content and Column B is empty.

    Returns:
        Number of rows processed.
    """
    # Get all values (includes empty cells)
    all_values = sheet.get_all_values()
    processed = 0

    for row_idx, row in enumerate(all_values):
        row_num = row_idx + 1  # 1-based for Sheets API

        # Column A = index 0, Column B = index 1
        col_a = row[0].strip() if len(row) > 0 else ""
        col_b = row[1].strip() if len(row) > 1 else ""

        # Skip empty rows or already-processed rows
        if not col_a:
            continue
        if col_b:
            # Already has a status — skip
            continue

        logger.info("Processing row %d...", row_num)

        # Validate
        data, error = validate_lead_json(col_a)

        if error:
            status = f"ERROR - {error}"
            logger.warning("Row %d validation failed: %s", row_num, error)
        else:
            # Push to Smartlead
            try:
                status = await process_row(data)
                logger.info("Row %d result: %s", row_num, status)
            except Exception as e:
                status = f"ERROR - Unexpected: {e}"
                logger.exception("Row %d unexpected error", row_num)

        # Write status to Column B
        sheet.update_cell(row_num, 2, status)
        processed += 1

    return processed


async def run_poller():
    """Main polling loop."""
    if not SPREADSHEET_ID:
        logger.error("GOOGLE_SHEETS_SPREADSHEET_ID not set")
        sys.exit(1)

    logger.info(
        "Starting poller: spreadsheet=%s, sheet=%s, interval=%ds",
        SPREADSHEET_ID,
        SHEET_NAME,
        POLL_INTERVAL,
    )

    spreadsheet = get_sheets_client()
    sheet = spreadsheet.worksheet(SHEET_NAME)
    logger.info("Connected to sheet: %s", sheet.title)

    while True:
        try:
            processed = await poll_once(sheet)
            if processed > 0:
                logger.info("Processed %d row(s) this cycle", processed)
        except gspread.exceptions.APIError as e:
            logger.error("Google Sheets API error: %s", e)
        except Exception as e:
            logger.exception("Unexpected error in poll cycle: %s", e)

        await asyncio.sleep(POLL_INTERVAL)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    asyncio.run(run_poller())