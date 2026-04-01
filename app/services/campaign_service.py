"""
Campaign service — orchestrates internal database operations + Smartlead API sync.

This is the core business logic layer. Routers call this, not Smartlead directly.

Flow:
  1. create_campaign_with_sync  → creates in DB + Smartlead, stores mapping
  2. inject_leads_with_sync     → stores leads/messages in DB + pushes to Smartlead in batches
  3. configure_campaign         → sets schedule/sequences on Smartlead
  4. update_status_with_sync    → updates status in DB + Smartlead
  5. assign_sender_account      → links sender email account to campaign on Smartlead
  6. setup_sequences            → creates N sequence steps on Smartlead with per-step delays
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    CampaignDelivery,
    CampaignLeadLink,
    InternalCampaign,
    Lead,
    OutboundMessage,
    SenderAccount,
)
from app.schemas.campaign import CampaignSettingsRequest, ScheduleConfig, SequenceStepDelay
from app.schemas.lead import LeadEmailInput
from app.services.smartlead_client import SmartleadClient, get_smartlead_client

logger = logging.getLogger(__name__)


# Default delays for sequence steps when not explicitly provided.
# Step 1 is always 0 (send immediately), subsequent steps add increasing delays.
DEFAULT_STEP_DELAYS = {
    1: 0,
    2: 3,
    3: 5,
    4: 7,
    5: 7,
    6: 7,
    7: 10,
    8: 10,
    9: 10,
    10: 14,
}


class CampaignService:
    def __init__(self, db: AsyncSession):
        self.db = db

    # ------------------------------------------------------------------
    # 1. Campaign creation with Smartlead sync
    # ------------------------------------------------------------------

    async def create_campaign_with_sync(
        self,
        name: str,
        persona: str | None = None,
        segment: str | None = None,
        num_emails_per_lead: int = 1,
    ) -> dict[str, Any]:
        """
        Creates a campaign in both internal DB and Smartlead.
        Stores the provider_campaign_id mapping.

        Args:
            name: Campaign name
            persona: Optional persona metadata
            segment: Optional segment metadata
            num_emails_per_lead: Number of sequence steps (1-10). Each lead
                                must provide exactly this many emails when injected.
        """
        # 1) Create internal campaign
        campaign = InternalCampaign(
            name=name,
            persona=persona,
            segment=segment,
            status="drafted",
            num_emails_per_lead=num_emails_per_lead,
        )
        self.db.add(campaign)
        await self.db.flush()

        # 2) Create on Smartlead
        provider_campaign_id = None
        smartlead_error = None
        try:
            async with get_smartlead_client() as sl:
                sl_result = await sl.create_campaign(name)
                # Smartlead returns {"id": ..., ...} or {"campaign": {"id": ...}}
                provider_campaign_id = str(
                    sl_result.get("id")
                    or sl_result.get("campaign", {}).get("id")
                    or ""
                )
                logger.info(
                    "Smartlead campaign created: provider_id=%s", provider_campaign_id
                )
        except Exception as e:
            smartlead_error = str(e)
            logger.error("Failed to create Smartlead campaign: %s", e)

        # 3) Create delivery mapping
        delivery = CampaignDelivery(
            internal_campaign_id=campaign.id,
            provider="smartlead",
            provider_campaign_id=provider_campaign_id,
            status="created" if provider_campaign_id else "error",
        )
        self.db.add(delivery)
        await self.db.flush()

        return {
            "campaign": campaign,
            "delivery": delivery,
            "provider_campaign_id": provider_campaign_id,
            "smartlead_error": smartlead_error,
        }

    # ------------------------------------------------------------------
    # 2. Configure campaign (schedule + sequences) on Smartlead
    # ------------------------------------------------------------------

    async def configure_campaign(
        self,
        campaign_id: uuid.UUID,
        schedule: ScheduleConfig | None = None,
        sender_account_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        """
        Configures campaign schedule and sender on Smartlead.
        """
        # Get delivery record
        delivery = await self._get_delivery(campaign_id)
        if not delivery.provider_campaign_id:
            raise ValueError("Campaign has no Smartlead mapping — create it first")

        result: dict[str, Any] = {"schedule_synced": False, "sender_updated": False}

        # Update sender account locally
        if sender_account_id:
            delivery.sender_account_id = sender_account_id
            result["sender_updated"] = True

        # Sync schedule to Smartlead
        if schedule:
            async with get_smartlead_client() as sl:
                await sl.update_campaign_schedule(
                    campaign_id=delivery.provider_campaign_id,
                    timezone=schedule.timezone,
                    days_of_the_week=schedule.days_of_the_week,
                    start_hour=schedule.start_hour,
                    end_hour=schedule.end_hour,
                    min_time_btw_emails=schedule.min_time_btw_emails,
                    max_new_leads_per_day=schedule.max_new_leads_per_day,
                )
                result["schedule_synced"] = True
                logger.info("Schedule synced for campaign %s", campaign_id)

        await self.db.flush()
        return result

    # ------------------------------------------------------------------
    # 3. Assign sender email account(s) to campaign on Smartlead
    # ------------------------------------------------------------------

    async def assign_sender_account(
        self,
        campaign_id: uuid.UUID,
        email_account_ids: list[int],
    ) -> dict[str, Any]:
        """
        Links sender email account(s) to a Smartlead campaign.

        This is REQUIRED before starting a campaign. Without a linked
        sender account, Smartlead will refuse to send any emails.

        Args:
            campaign_id: Internal campaign UUID
            email_account_ids: Smartlead email account IDs (integers).
                              Get these from GET /sender-accounts/list endpoint.

        Returns:
            Dict with sync status and Smartlead response.
        """
        delivery = await self._get_delivery(campaign_id)
        if not delivery.provider_campaign_id:
            raise ValueError("Campaign has no Smartlead mapping — create it first")

        smartlead_error = None
        smartlead_response = None

        try:
            async with get_smartlead_client() as sl:
                smartlead_response = await sl.add_email_account_to_campaign(
                    campaign_id=delivery.provider_campaign_id,
                    email_account_ids=email_account_ids,
                )
                logger.info(
                    "Linked email accounts %s to campaign %s (Smartlead ID: %s)",
                    email_account_ids,
                    campaign_id,
                    delivery.provider_campaign_id,
                )
        except Exception as e:
            smartlead_error = str(e)
            logger.error("Failed to link email accounts to Smartlead campaign: %s", e)

        # Also store the first account locally for reference
        if email_account_ids and not smartlead_error:
            # Check if we have a matching local sender_account by provider_account_id
            first_id = str(email_account_ids[0])
            existing = await self.db.execute(
                select(SenderAccount).where(
                    SenderAccount.provider_account_id == first_id
                )
            )
            sender = existing.scalar_one_or_none()
            if sender:
                delivery.sender_account_id = sender.id
                await self.db.flush()

        return {
            "campaign_id": str(campaign_id),
            "provider_campaign_id": delivery.provider_campaign_id,
            "email_account_ids": email_account_ids,
            "synced": smartlead_error is None,
            "smartlead_error": smartlead_error,
            "smartlead_response": smartlead_response,
        }

    # ------------------------------------------------------------------
    # 4. Lead injection with Smartlead sync (multi-step)
    # ------------------------------------------------------------------

    async def inject_leads_with_sync(
        self,
        campaign_id: uuid.UUID,
        leads_input: list[LeadEmailInput],
    ) -> dict[str, Any]:
        """
        Full pipeline for multi-step email campaigns:
        1. Validate each lead has exactly num_emails_per_lead emails
        2. Upsert leads in DB
        3. Create campaign-lead links
        4. Store outbound_message snapshots (one per step per lead)
        5. Build Smartlead lead payload with numbered custom_fields
           (email_subject_1, email_body_1, email_subject_2, email_body_2, ...)
        6. Push to Smartlead in batches of 400
        7. Store provider_lead_id mappings
        """
        delivery = await self._get_delivery(campaign_id)
        if not delivery.provider_campaign_id:
            raise ValueError("Campaign has no Smartlead mapping — create it first")

        # Get campaign to check num_emails_per_lead
        campaign = await self._get_campaign(campaign_id)
        expected_steps = campaign.num_emails_per_lead

        created_count = 0
        skipped_count = 0
        smartlead_leads: list[dict[str, Any]] = []
        link_map: dict[str, CampaignLeadLink] = {}  # email -> link

        for lead_input in leads_input:
            email_lower = lead_input.email.lower()

            # Validate email count matches campaign configuration
            if len(lead_input.emails) != expected_steps:
                raise ValueError(
                    f"Lead {email_lower}: expected {expected_steps} emails "
                    f"(num_emails_per_lead), got {len(lead_input.emails)}. "
                    f"Each lead must provide exactly {expected_steps} emails."
                )

            # Upsert lead
            existing_lead_result = await self.db.execute(
                select(Lead).where(Lead.email == email_lower)
            )
            lead = existing_lead_result.scalar_one_or_none()

            if not lead:
                lead = Lead(
                    email=email_lower,
                    first_name=lead_input.first_name,
                    last_name=lead_input.last_name,
                    company=lead_input.company,
                    linkedin_url=lead_input.linkedin_url,
                )
                self.db.add(lead)
                await self.db.flush()

            # Dedupe: skip if already linked
            existing_link_result = await self.db.execute(
                select(CampaignLeadLink).where(
                    CampaignLeadLink.campaign_delivery_id == delivery.id,
                    CampaignLeadLink.lead_id == lead.id,
                )
            )
            if existing_link_result.scalar_one_or_none():
                skipped_count += 1
                continue

            # Create campaign-lead link
            link = CampaignLeadLink(
                campaign_delivery_id=delivery.id,
                lead_id=lead.id,
                status="pending",
            )
            self.db.add(link)
            await self.db.flush()

            # Build numbered custom_fields for Smartlead
            # Each step gets its own email_subject_N / email_body_N
            custom_fields: dict[str, str] = {}

            # Sort emails by step_number to process in order
            sorted_emails = sorted(lead_input.emails, key=lambda e: e.step_number)

            for step_email in sorted_emails:
                step_num = step_email.step_number

                # Store immutable email snapshot (one row per step)
                message = OutboundMessage(
                    campaign_link_id=link.id,
                    step_number=step_num,
                    subject=step_email.subject,
                    body_html=step_email.body_html,
                    body_text=step_email.body_text,
                    prompt_version=step_email.prompt_version,
                    model_name=step_email.model_name,
                    context_snapshot=step_email.context_snapshot,
                    custom_fields={
                        f"email_subject_{step_num}": step_email.subject,
                        f"email_body_{step_num}": step_email.body_html,
                    },
                    message_status="pending",
                    generated_at=datetime.now(timezone.utc),
                )
                self.db.add(message)

                # Add to the lead-level custom_fields dict
                custom_fields[f"email_subject_{step_num}"] = step_email.subject
                custom_fields[f"email_body_{step_num}"] = step_email.body_html

            await self.db.flush()

            # Build Smartlead lead payload
            # All step subjects/bodies are passed as numbered custom_fields
            # so each sequence template can reference {{email_subject_N}}
            sl_lead = {
                "email": email_lower,
                "first_name": lead_input.first_name or "",
                "last_name": lead_input.last_name or "",
                "company_name": lead_input.company or "",
                "custom_fields": custom_fields,
            }
            smartlead_leads.append(sl_lead)
            link_map[email_lower] = link
            created_count += 1

        await self.db.flush()

        # Push to Smartlead in batches
        smartlead_error = None
        if smartlead_leads:
            try:
                async with get_smartlead_client() as sl:
                    batch_results = await sl.add_leads_batched(
                        campaign_id=delivery.provider_campaign_id,
                        lead_list=smartlead_leads,
                    )
                    logger.info(
                        "Pushed %d leads to Smartlead campaign %s",
                        len(smartlead_leads),
                        delivery.provider_campaign_id,
                    )

                    # Try to extract provider_lead_ids from response
                    for batch_result in batch_results:
                        if isinstance(batch_result, dict):
                            # Some responses return lead IDs
                            uploaded = batch_result.get("upload_leads", [])
                            if isinstance(uploaded, list):
                                for ul in uploaded:
                                    email = (ul.get("email") or "").lower()
                                    lead_id = ul.get("id") or ul.get("lead_id")
                                    if email in link_map and lead_id:
                                        link_map[email].provider_lead_id = str(lead_id)
                                        link_map[email].status = "synced"

                    # Mark any remaining as synced (even without provider ID)
                    for email, link in link_map.items():
                        if link.status == "pending":
                            link.status = "synced"

                    await self.db.flush()

            except Exception as e:
                smartlead_error = str(e)
                logger.error("Failed to push leads to Smartlead: %s", e)
                # Mark leads as error
                for link in link_map.values():
                    link.status = "sync_error"
                await self.db.flush()

        return {
            "campaign_id": campaign_id,
            "total_received": len(leads_input),
            "total_created": created_count,
            "total_skipped_duplicate": skipped_count,
            "total_pushed_to_smartlead": len(smartlead_leads),
            "smartlead_error": smartlead_error,
        }

    # ------------------------------------------------------------------
    # 5. Set up sequences on Smartlead (multi-step)
    # ------------------------------------------------------------------

    async def setup_sequences(
        self,
        campaign_id: uuid.UUID,
        step_delays: list[SequenceStepDelay] | None = None,
    ) -> dict[str, Any]:
        """
        Creates N sequence steps on Smartlead, one per num_emails_per_lead.

        Each step uses numbered placeholder variables:
          Step 1: subject={{email_subject_1}}, body={{email_body_1}}
          Step 2: subject={{email_subject_2}}, body={{email_body_2}}
          ...

        These get filled from each lead's custom_fields when Smartlead sends.

        Args:
            campaign_id: Internal campaign UUID
            step_delays: Optional per-step delay config. If not provided,
                        defaults are used (step 1=0, step 2=3, step N=5+ days).
        """
        campaign = await self._get_campaign(campaign_id)
        delivery = await self._get_delivery(campaign_id)
        if not delivery.provider_campaign_id:
            raise ValueError("Campaign has no Smartlead mapping")

        num_steps = campaign.num_emails_per_lead

        # Build delay lookup from provided config or defaults
        delay_lookup: dict[int, int] = {}
        if step_delays:
            # Validate that the right number of delays were provided
            if len(step_delays) != num_steps:
                raise ValueError(
                    f"Expected {num_steps} step delays (matching num_emails_per_lead), "
                    f"got {len(step_delays)}."
                )
            for sd in step_delays:
                delay_lookup[sd.step_number] = sd.delay_in_days
        else:
            # Use defaults
            for step in range(1, num_steps + 1):
                delay_lookup[step] = DEFAULT_STEP_DELAYS.get(step, 7)

        # Ensure step 1 is always delay=0
        delay_lookup[1] = 0

        # Build sequence list for Smartlead
        sequences = []
        for step in range(1, num_steps + 1):
            sequences.append(
                {
                    "id": None,
                    "seq_number": step,
                    "subject": "{{" + f"email_subject_{step}" + "}}",
                    "email_body": "{{" + f"email_body_{step}" + "}}",
                    "seq_delay_details": {
                        "delay_in_days": delay_lookup[step],
                    },
                }
            )

        async with get_smartlead_client() as sl:
            result = await sl.update_sequences(
                campaign_id=delivery.provider_campaign_id,
                sequences=sequences,
            )
            logger.info(
                "Sequences set for campaign %s: %d steps, delays=%s",
                campaign_id,
                num_steps,
                {s: delay_lookup[s] for s in range(1, num_steps + 1)},
            )

        return {
            "num_steps": num_steps,
            "step_delays": {str(s): delay_lookup[s] for s in range(1, num_steps + 1)},
            "smartlead_response": result,
        }

    # ------------------------------------------------------------------
    # 6. Campaign status management with Smartlead sync
    # ------------------------------------------------------------------

    async def update_status_with_sync(
        self,
        campaign_id: uuid.UUID,
        action: str,
    ) -> dict[str, Any]:
        """
        Update campaign status in DB and on Smartlead.
        action: "start", "pause", "stop"
        """
        # Internal status mapping
        status_map = {"start": "active", "pause": "paused", "stop": "stopped"}
        # Smartlead status mapping
        sl_status_map = {"start": "START", "pause": "PAUSED", "stop": "STOPPED"}

        campaign = await self._get_campaign(campaign_id)
        delivery = await self._get_delivery(campaign_id)

        # Update Smartlead
        smartlead_error = None
        if delivery.provider_campaign_id:
            try:
                async with get_smartlead_client() as sl:
                    await sl.update_campaign_status(
                        campaign_id=delivery.provider_campaign_id,
                        status=sl_status_map[action],
                    )
                    logger.info("Campaign %s status set to %s on Smartlead", campaign_id, action)
            except Exception as e:
                smartlead_error = str(e)
                logger.error("Failed to update Smartlead campaign status: %s", e)

        # Update internal
        campaign.status = status_map[action]
        delivery.status = status_map[action]
        await self.db.flush()

        return {
            "id": str(campaign.id),
            "status": campaign.status,
            "smartlead_synced": smartlead_error is None,
            "smartlead_error": smartlead_error,
        }

    # ------------------------------------------------------------------
    # 7. List available sender accounts from Smartlead
    # ------------------------------------------------------------------

    async def list_sender_accounts(self) -> list[dict[str, Any]]:
        """Fetch all email accounts from Smartlead."""
        async with get_smartlead_client() as sl:
            return await sl.list_email_accounts()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _get_campaign(self, campaign_id: uuid.UUID) -> InternalCampaign:
        result = await self.db.execute(
            select(InternalCampaign).where(InternalCampaign.id == campaign_id)
        )
        campaign = result.scalar_one_or_none()
        if not campaign:
            raise ValueError(f"Campaign {campaign_id} not found")
        return campaign

    async def _get_delivery(self, campaign_id: uuid.UUID) -> CampaignDelivery:
        result = await self.db.execute(
            select(CampaignDelivery).where(
                CampaignDelivery.internal_campaign_id == campaign_id
            )
        )
        delivery = result.scalar_one_or_none()
        if not delivery:
            raise ValueError(f"No delivery record for campaign {campaign_id}")
        return delivery