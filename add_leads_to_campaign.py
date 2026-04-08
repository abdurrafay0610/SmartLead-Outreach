"""
Utility: Add leads (with per-step subject/body combos) to an existing Smartlead campaign.

Usage:
    The campaign must already exist and have its sequences configured
    (with {{email_subject_N}} / {{email_body_N}} placeholders).

    This function ONLY pushes leads to Smartlead — it does NOT touch the
    internal database. Use it when you've fetched data from an external
    source and just need to push it into a running campaign.

Example:
    import asyncio
    from add_leads_to_campaign import add_leads_to_campaign

    leads = [
        {
            "email": "jane@acme.com",
            "emails": [
                {"step_number": 1, "subject": "Hey Jane", "body": "<p>First touch...</p>"},
                {"step_number": 2, "subject": "Following up", "body": "<p>Just checking in...</p>"},
            ],
            # optional fields:
            "first_name": "Jane",
            "last_name": "Doe",
            "company_name": "Acme Corp",
        },
        {
            "email": "bob@example.com",
            "emails": [
                {"step_number": 1, "subject": "Hi Bob", "body": "<p>Reaching out...</p>"},
                {"step_number": 2, "subject": "Quick follow-up", "body": "<p>Wanted to...</p>"},
            ],
        },
    ]

    result = asyncio.run(add_leads_to_campaign(
        smartlead_campaign_id=12345,
        leads=leads,
    ))
    print(result)
"""

import logging
from dataclasses import dataclass, field
from typing import Any

from app.services.smartlead_client import get_smartlead_client

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class StepEmail:
    """One email (subject + body) for a specific sequence step."""
    step_number: int
    subject: str
    body: str  # HTML body


@dataclass
class LeadInput:
    """A lead to add, with all their per-step emails."""
    email: str
    emails: list[StepEmail]
    first_name: str = ""
    last_name: str = ""
    company_name: str = ""
    extra_custom_fields: dict[str, str] = field(default_factory=dict)


@dataclass
class AddLeadsResult:
    """Summary of what happened."""
    campaign_id: int | str
    total_submitted: int
    total_accepted: int
    total_skipped: int
    skipped_reasons: dict[str, str]  # email -> reason
    smartlead_responses: list[dict[str, Any]]
    errors: list[str]


# ---------------------------------------------------------------------------
# Core function
# ---------------------------------------------------------------------------

async def add_leads_to_campaign(
    smartlead_campaign_id: int | str,
    leads: list[dict[str, Any]],
    *,
    api_key: str | None = None,
) -> AddLeadsResult:
    """
    Add leads with per-step subject/body combos to an existing Smartlead campaign.

    Args:
        smartlead_campaign_id: The Smartlead campaign ID (integer from their API).
        leads: List of lead dicts, each containing:
            - "email" (str, required): Lead's email address
            - "emails" (list, required): Per-step email content, each with:
                - "step_number" (int): Which sequence step (1-based)
                - "subject" (str): Email subject for this step
                - "body" (str): HTML email body for this step
            - "first_name" (str, optional)
            - "last_name" (str, optional)
            - "company_name" (str, optional)
            - "extra_custom_fields" (dict, optional): Any additional custom fields
        api_key: Override API key (uses env default if None).

    Returns:
        AddLeadsResult with counts and any errors.

    Notes:
        - Sequences must already be configured on the campaign with
          {{email_subject_N}} / {{email_body_N}} placeholder templates.
        - Smartlead allows max 400 leads per API call; this function
          auto-batches via add_leads_batched().
        - Leads with missing email or empty emails list are skipped.
        - Duplicate emails within the input are skipped (first wins).
    """
    seen_emails: set[str] = set()
    smartlead_leads: list[dict[str, Any]] = []
    skipped: dict[str, str] = {}

    for raw_lead in leads:
        # --- Parse input (accept both dict and LeadInput) ---
        if isinstance(raw_lead, LeadInput):
            lead = raw_lead
        else:
            # Parse from dict
            email = (raw_lead.get("email") or "").strip().lower()
            if not email:
                skipped[raw_lead.get("email", "<missing>")] = "missing or empty email"
                continue

            raw_emails = raw_lead.get("emails", [])
            if not raw_emails:
                skipped[email] = "no emails/steps provided"
                continue

            step_emails = [
                StepEmail(
                    step_number=e["step_number"],
                    subject=e["subject"],
                    body=e["body"],
                )
                for e in raw_emails
            ]

            lead = LeadInput(
                email=email,
                emails=step_emails,
                first_name=raw_lead.get("first_name", ""),
                last_name=raw_lead.get("last_name", ""),
                company_name=raw_lead.get("company_name", ""),
                extra_custom_fields=raw_lead.get("extra_custom_fields", {}),
            )

        # --- Normalize email ---
        email_lower = lead.email.strip().lower()

        # --- Dedupe within this batch ---
        if email_lower in seen_emails:
            skipped[email_lower] = "duplicate in input"
            continue
        seen_emails.add(email_lower)

        # --- Build custom_fields with numbered subject/body pairs ---
        # This matches the sequence templates: {{email_subject_1}}, {{email_body_1}}, etc.
        custom_fields: dict[str, str] = {}

        for step_email in sorted(lead.emails, key=lambda e: e.step_number):
            n = step_email.step_number
            custom_fields[f"email_subject_{n}"] = step_email.subject
            custom_fields[f"email_body_{n}"] = step_email.body

        # Merge any extra custom fields (won't overwrite email_subject/body keys)
        for k, v in lead.extra_custom_fields.items():
            if k not in custom_fields:
                custom_fields[k] = v

        # --- Build Smartlead lead payload ---
        sl_lead = {
            "email": email_lower,
            "first_name": lead.first_name or "",
            "last_name": lead.last_name or "",
            "company_name": lead.company_name or "",
            "custom_fields": custom_fields,
        }
        smartlead_leads.append(sl_lead)

    # --- Push to Smartlead ---
    errors: list[str] = []
    responses: list[dict[str, Any]] = []

    if smartlead_leads:
        try:
            async with get_smartlead_client(api_key=api_key) as sl:
                batch_results = await sl.add_leads_batched(
                    campaign_id=smartlead_campaign_id,
                    lead_list=smartlead_leads,
                )
                responses = batch_results
                logger.info(
                    "Pushed %d leads to Smartlead campaign %s (%d batches)",
                    len(smartlead_leads),
                    smartlead_campaign_id,
                    len(batch_results),
                )
        except Exception as e:
            errors.append(str(e))
            logger.error(
                "Failed to push leads to Smartlead campaign %s: %s",
                smartlead_campaign_id,
                e,
            )

    return AddLeadsResult(
        campaign_id=smartlead_campaign_id,
        total_submitted=len(leads),
        total_accepted=len(smartlead_leads),
        total_skipped=len(skipped),
        skipped_reasons=skipped,
        smartlead_responses=responses,
        errors=errors,
    )