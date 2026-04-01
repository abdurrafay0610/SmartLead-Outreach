"""
Async Smartlead API client.

Wraps all Smartlead REST endpoints needed for campaign automation.
Uses httpx for async HTTP, with retry logic and rate-limit handling.

Endpoint reference:
  - POST /campaigns/create
  - POST /campaigns/{id}/sequences
  - POST /campaigns/{id}/leads  (max 400 per call)
  - POST /campaigns/{id}/schedule
  - POST /campaigns/{id}/status
  - POST /campaigns/{id}/settings
  - POST /campaigns/{id}/email-accounts  (link sender to campaign)
  - GET  /campaigns/{id}
  - GET  /email-accounts
  - POST /campaigns/{id}/send-test-email
"""

import asyncio
import logging
from typing import Any, Optional

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class SmartleadAPIError(Exception):
    """Base exception for Smartlead API errors."""

    def __init__(self, message: str, status_code: int | None = None, response_body: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body


class SmartleadAuthError(SmartleadAPIError):
    """401 — invalid or missing API key."""
    pass


class SmartleadValidationError(SmartleadAPIError):
    """400/422 — bad request payload."""
    pass


class SmartleadRateLimitError(SmartleadAPIError):
    """429 — rate limit exceeded (will be retried automatically)."""
    pass


class SmartleadNotFoundError(SmartleadAPIError):
    """404 — resource not found."""
    pass


class SmartleadServerError(SmartleadAPIError):
    """500/503 — Smartlead-side failure."""
    pass


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class SmartleadClient:
    """
    Async HTTP client for the Smartlead API.

    Usage:
        async with SmartleadClient(api_key="...") as sl:
            campaign = await sl.create_campaign("My Campaign")
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 30.0,
        max_retries: int = 5,
    ):
        self.api_key = api_key or settings.SMARTLEAD_API_KEY
        self.base_url = (base_url or settings.SMARTLEAD_BASE_URL).rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self):
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=self.timeout,
            headers={"Content-Type": "application/json"},
        )
        return self

    async def __aexit__(self, *exc):
        if self._client:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("SmartleadClient must be used as async context manager")
        return self._client

    # ------------------------------------------------------------------
    # Core HTTP layer with retries
    # ------------------------------------------------------------------

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        """
        Make an authenticated request with automatic retry on 429 and 5xx.
        """
        url = f"/{path.lstrip('/')}"
        params = dict(params or {})
        params["api_key"] = self.api_key

        backoff = 1.0
        last_exc: Exception | None = None

        for attempt in range(1, self.max_retries + 1):
            try:
                resp = await self.client.request(
                    method, url, params=params, json=json_body
                )

                # --- Rate limit: retry with backoff ---
                if resp.status_code == 429:
                    logger.warning(
                        "Smartlead rate limit hit (attempt %d/%d), backing off %.1fs",
                        attempt, self.max_retries, backoff,
                    )
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 30)
                    continue

                # --- Server errors: retry ---
                if resp.status_code >= 500:
                    logger.warning(
                        "Smartlead server error %d (attempt %d/%d), retrying...",
                        resp.status_code, attempt, self.max_retries,
                    )
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 30)
                    continue

                # --- Client errors: raise immediately ---
                if resp.status_code == 401:
                    raise SmartleadAuthError(
                        f"Unauthorized: {resp.text}",
                        status_code=401,
                        response_body=self._safe_json(resp),
                    )
                if resp.status_code == 404:
                    raise SmartleadNotFoundError(
                        f"Not found: {url}",
                        status_code=404,
                        response_body=self._safe_json(resp),
                    )
                if resp.status_code in (400, 422):
                    raise SmartleadValidationError(
                        f"Validation error ({resp.status_code}): {resp.text}",
                        status_code=resp.status_code,
                        response_body=self._safe_json(resp),
                    )
                if resp.status_code >= 400:
                    raise SmartleadAPIError(
                        f"HTTP {resp.status_code}: {resp.text}",
                        status_code=resp.status_code,
                        response_body=self._safe_json(resp),
                    )

                # --- Success ---
                return self._safe_json(resp)

            except (httpx.RequestError, SmartleadRateLimitError, SmartleadServerError) as e:
                last_exc = e
                logger.warning(
                    "Smartlead request error (attempt %d/%d): %s",
                    attempt, self.max_retries, e,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)

        raise SmartleadAPIError(
            f"Request to {url} failed after {self.max_retries} attempts: {last_exc}"
        )

    @staticmethod
    def _safe_json(resp: httpx.Response) -> Any:
        """Parse JSON response, return raw text if not JSON."""
        content_type = resp.headers.get("content-type", "")
        if "application/json" in content_type:
            return resp.json()
        return resp.text

    # ------------------------------------------------------------------
    # Campaign endpoints
    # ------------------------------------------------------------------

    async def create_campaign(self, name: str) -> dict[str, Any]:
        """
        POST /campaigns/create
        Creates a campaign in DRAFTED status.
        Returns: {"id": ..., "name": ..., "created_at": ..., ...}
        """
        result = await self._request("POST", "/campaigns/create", json_body={"name": name})
        logger.info("Created Smartlead campaign: %s", result)
        return result

    async def update_campaign_status(self, campaign_id: str | int, status: str) -> dict[str, Any]:
        """
        POST /campaigns/{id}/status
        status: "START", "PAUSED", "STOPPED"
        Note: API reference says use START not ACTIVE.
        """
        result = await self._request(
            "POST",
            f"/campaigns/{campaign_id}/status",
            json_body={"status": status},
        )
        logger.info("Updated campaign %s status to %s: %s", campaign_id, status, result)
        return result

    async def update_campaign_schedule(
            self,
            campaign_id: str | int,
            timezone: str,
            days_of_the_week: list[int],
            start_hour: str,
            end_hour: str,
            min_time_btw_emails: int,
            max_new_leads_per_day: int | None = None,
    ) -> dict[str, Any]:
        payload = {
            "timezone": timezone,
            "days_of_the_week": days_of_the_week,
            "start_hour": start_hour,
            "end_hour": end_hour,
            "min_time_btw_emails": min_time_btw_emails,
        }
        if max_new_leads_per_day is not None:
            payload["max_new_leads_per_day"] = max_new_leads_per_day

        return await self._request(
            "POST",
            f"/campaigns/{campaign_id}/schedule",
            json_body=payload,
        )

    async def update_campaign_settings(
        self, campaign_id: str | int, settings_payload: dict[str, Any]
    ) -> dict[str, Any]:
        """
        POST /campaigns/{id}/settings
        Accepts tracking, limits, stop rules, unsubscribe text, etc.
        """
        return await self._request(
            "POST",
            f"/campaigns/{campaign_id}/settings",
            json_body=settings_payload,
        )

    async def get_campaign(self, campaign_id: str | int) -> dict[str, Any]:
        """
        GET /campaigns/{id}
        """
        return await self._request("GET", f"/campaigns/{campaign_id}")

    # ------------------------------------------------------------------
    # Sequence endpoints
    # ------------------------------------------------------------------

    async def update_sequences(
        self,
        campaign_id: str | int,
        sequences: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """
        POST /campaigns/{id}/sequences

        Each sequence item:
        {
            "id": None,           # None for new, int for update
            "seq_number": 1,
            "subject": "...",
            "email_body": "<p>...</p>",
            "seq_delay_details": {"delay_in_days": 0}
        }
        """
        return await self._request(
            "POST",
            f"/campaigns/{campaign_id}/sequences",
            json_body={"sequences": sequences},
        )

    # ------------------------------------------------------------------
    # Lead endpoints
    # ------------------------------------------------------------------

    async def add_leads(
        self,
        campaign_id: str | int,
        lead_list: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """
        POST /campaigns/{id}/leads
        Max 400 leads per call. Each lead:
        {
            "email": "...",
            "first_name": "...",
            "last_name": "...",
            "company_name": "...",
            "custom_fields": {"subject": "...", "body": "..."}
        }
        """
        if len(lead_list) > 400:
            raise ValueError(
                f"Smartlead allows max 400 leads per request, got {len(lead_list)}. "
                "Use add_leads_batched() instead."
            )
        return await self._request(
            "POST",
            f"/campaigns/{campaign_id}/leads",
            json_body={"lead_list": lead_list},
        )

    async def add_leads_batched(
        self,
        campaign_id: str | int,
        lead_list: list[dict[str, Any]],
        batch_size: int = 400,
    ) -> list[dict[str, Any]]:
        """
        Add leads in batches of up to 400 (Smartlead's max per request).
        Returns list of responses, one per batch.
        """
        results = []
        for i in range(0, len(lead_list), batch_size):
            batch = lead_list[i : i + batch_size]
            logger.info(
                "Adding leads batch %d-%d of %d to campaign %s",
                i + 1, min(i + batch_size, len(lead_list)), len(lead_list), campaign_id,
            )
            result = await self.add_leads(campaign_id, batch)
            results.append(result)
            # Small delay between batches to be nice to rate limits
            if i + batch_size < len(lead_list):
                await asyncio.sleep(1.0)
        return results

    async def get_campaign_leads(
        self,
        campaign_id: str | int,
        offset: int = 0,
        limit: int = 100,
    ) -> dict[str, Any]:
        """
        GET /campaigns/{id}/leads
        """
        return await self._request(
            "GET",
            f"/campaigns/{campaign_id}/leads",
            params={"offset": offset, "limit": limit},
        )

    # ------------------------------------------------------------------
    # Email account endpoints
    # ------------------------------------------------------------------

    async def list_email_accounts(self) -> list[dict[str, Any]]:
        """
        GET /email-accounts
        Returns all connected sender email accounts.
        """
        result = await self._request("GET", "/email-accounts")
        # Result might be a list directly or wrapped in an object
        if isinstance(result, list):
            return result
        return result.get("data", result.get("email_accounts", []))

    async def add_email_account_to_campaign(
        self,
        campaign_id: str | int,
        email_account_ids: list[int],
    ) -> dict[str, Any]:
        """
        POST /campaigns/{campaign_id}/email-accounts

        Links one or more sender email accounts to a campaign.
        This is REQUIRED before starting a campaign — Smartlead won't
        send emails without at least one sender account linked.

        Args:
            campaign_id: Smartlead campaign ID
            email_account_ids: List of Smartlead email account IDs (integers)
                              Get these from list_email_accounts()

        Returns:
            Smartlead API response confirming the accounts were linked.
        """
        result = await self._request(
            "POST",
            f"/campaigns/{campaign_id}/email-accounts",
            json_body={"email_account_ids": email_account_ids},
        )
        logger.info(
            "Linked email accounts %s to campaign %s: %s",
            email_account_ids, campaign_id, result,
        )
        return result

    # ------------------------------------------------------------------
    # Test email
    # ------------------------------------------------------------------

    async def send_test_email(
        self,
        campaign_id: str | int,
        lead_id: str | int,
        sequence_number: int = 1,
        custom_email_address: str | None = None,
    ) -> dict[str, Any]:
        """
        POST /campaigns/{id}/send-test-email
        Sends a test email for a specific sequence step using a lead's data.
        """
        payload: dict[str, Any] = {
            "leadId": lead_id,
            "sequenceNumber": sequence_number,
        }
        if custom_email_address:
            payload["customEmailAddress"] = custom_email_address

        return await self._request(
            "POST",
            f"/campaigns/{campaign_id}/send-test-email",
            json_body=payload,
        )


# ---------------------------------------------------------------------------
# Convenience factory
# ---------------------------------------------------------------------------

def get_smartlead_client(
    api_key: str | None = None,
    base_url: str | None = None,
) -> SmartleadClient:
    """Create a SmartleadClient instance. Use as async context manager."""
    return SmartleadClient(api_key=api_key, base_url=base_url)