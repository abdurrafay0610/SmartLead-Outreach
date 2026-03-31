"""
Campaign service — orchestrates internal database operations + Smartlead API sync.

This is the core business logic layer. Routers call this, not Smartlead directly.

Flow:
  1. create_campaign_with_sync  → creates in DB + Smartlead, stores mapping
  2. inject_leads_with_sync     → stores leads/messages in DB + pushes to Smartlead in batches
  3. configure_campaign         → sets schedule/sequences on Smartlead
  4. update_status_with_sync    → updates status in DB + Smartlead
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
from app.schemas.campaign import CampaignSettingsRequest, ScheduleConfig
from app.schemas.lead import LeadEmailInput
from app.services.smartlead_client import SmartleadClient, get_smartlead_client

logger = logging.getLogger(__name__)


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
    ) -> dict[str, Any]:
        """
        Creates a campaign in both internal DB and Smartlead.
        Stores the provider_campaign_id mapping.
        """
        # 1) Create internal campaign
        campaign = InternalCampaign(
            name=name,
            persona=persona,
            segment=segment,
            status="drafted",
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
                    days=schedule.days,
                    start_hour=schedule.start_hour,
                    end_hour=schedule.end_hour,
                    min_time_btw_emails=schedule.min_time_btw_emails,
                )
                result["schedule_synced"] = True
                logger.info("Schedule synced for campaign %s", campaign_id)

        await self.db.flush()
        return result

    # ------------------------------------------------------------------
    # 3. Lead injection with Smartlead sync
    # ------------------------------------------------------------------

    async def inject_leads_with_sync(
        self,
        campaign_id: uuid.UUID,
        leads_input: list[LeadEmailInput],
    ) -> dict[str, Any]:
        """
        Full pipeline:
        1. Upsert leads in DB
        2. Create campaign-lead links
        3. Store outbound_message snapshots
        4. Build Smartlead lead payload with custom_fields (subject/body)
        5. Push to Smartlead in batches of 400
        6. Store provider_lead_id mappings
        """
        delivery = await self._get_delivery(campaign_id)
        if not delivery.provider_campaign_id:
            raise ValueError("Campaign has no Smartlead mapping — create it first")

        created_count = 0
        skipped_count = 0
        smartlead_leads: list[dict[str, Any]] = []
        link_map: dict[str, CampaignLeadLink] = {}  # email -> link

        for lead_input in leads_input:
            email_lower = lead_input.email.lower()

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

            # Store immutable email snapshot
            message = OutboundMessage(
                campaign_link_id=link.id,
                step_number=1,
                subject=lead_input.subject,
                body_html=lead_input.body_html,
                body_text=lead_input.body_text,
                prompt_version=lead_input.prompt_version,
                model_name=lead_input.model_name,
                context_snapshot=lead_input.context_snapshot,
                custom_fields={
                    "email_subject": lead_input.subject,
                    "email_body": lead_input.body_html,
                },
                message_status="pending",
                generated_at=datetime.now(timezone.utc),
            )
            self.db.add(message)
            await self.db.flush()

            # Build Smartlead lead payload
            # We pass subject+body as custom_fields so the sequence template
            # can use {{email_subject}} and {{email_body}}
            sl_lead = {
                "email": email_lower,
                "first_name": lead_input.first_name or "",
                "last_name": lead_input.last_name or "",
                "company_name": lead_input.company or "",
                "custom_fields": {
                    "email_subject": lead_input.subject,
                    "email_body": lead_input.body_html,
                },
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
    # 4. Set up sequences on Smartlead
    # ------------------------------------------------------------------

    async def setup_sequences(
        self,
        campaign_id: uuid.UUID,
        subject_template: str = "{{email_subject}}",
        body_template: str = "{{email_body}}",
    ) -> dict[str, Any]:
        """
        Creates sequence step(s) on Smartlead.

        Since we pass the actual subject/body as custom_fields per lead,
        the sequence template just references those variables.
        Default: subject={{email_subject}}, body={{email_body}}
        """
        delivery = await self._get_delivery(campaign_id)
        if not delivery.provider_campaign_id:
            raise ValueError("Campaign has no Smartlead mapping")

        sequences = [
            {
                "id": None,
                "seq_number": 1,
                "subject": subject_template,
                "email_body": body_template,
                "seq_delay_details": {"delay_in_days": 0},
            }
        ]

        async with get_smartlead_client() as sl:
            result = await sl.update_sequences(
                campaign_id=delivery.provider_campaign_id,
                sequences=sequences,
            )
            logger.info("Sequences set for campaign %s", campaign_id)

        return result

    # ------------------------------------------------------------------
    # 5. Campaign status management with Smartlead sync
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
    # 6. List available sender accounts from Smartlead
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
