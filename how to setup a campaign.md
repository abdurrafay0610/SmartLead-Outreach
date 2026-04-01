# AI Outreach System — API Usage Guide

This guide walks you through the complete workflow of creating a campaign, injecting leads with personalized emails, assigning sender accounts, and starting the campaign. All examples use the Swagger UI at `http://localhost:8000/docs`.

---

## Prerequisites

Before you begin, make sure:

- The FastAPI server is running (`uvicorn app.main:app --reload --host 0.0.0.0 --port 8000`)
- PostgreSQL and Redis are running
- Migrations have been applied (`alembic upgrade head`)
- Your `.env` has a valid `SMARTLEAD_API_KEY`
- You have at least one sender email account configured in Smartlead (via their dashboard or API)

---

## Workflow Overview

```
Step 1: Create Campaign
Step 2: Set Up Sequences (email templates)
Step 3: Inject Leads (with personalized email content)
Step 4: List Sender Accounts (find available email accounts)
Step 5: Assign Sender Account to Campaign
Step 6: Configure Schedule
Step 7: Start Campaign
```

Every step below must be completed in order. Smartlead will reject a campaign start if sequences, leads, or sender accounts are missing.

---

## Step 1: Create a Campaign

**Endpoint:** `POST /api/v1/campaigns`

This creates a campaign in both your internal database and on Smartlead. The Smartlead campaign is created in `DRAFTED` status.

**Request body:**

```json
{
  "name": "Q3 Healthcare Outreach",
  "persona": "Sales Rep",
  "segment": "Healthcare CTOs"
}
```

Only `name` is required. `persona` and `segment` are optional metadata for your own tracking.

**Response (201):**

```json
{
  "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "name": "Q3 Healthcare Outreach",
  "persona": "Sales Rep",
  "segment": "Healthcare CTOs",
  "status": "drafted",
  "provider_campaign_id": "12345",
  "created_at": "2026-04-01T10:00:00Z",
  "updated_at": "2026-04-01T10:00:00Z"
}
```

**Save the `id` value** — you'll need it for every subsequent step.

**What happens behind the scenes:**
- An `internal_campaigns` record is created in your DB
- A campaign is created on Smartlead via `POST /campaigns/create`
- A `campaign_deliveries` record is created mapping your internal ID to the Smartlead `provider_campaign_id`

---

## Step 2: Set Up Sequences

**Endpoint:** `POST /api/v1/campaigns/{campaign_id}/sequences`

This configures the email template on Smartlead. Since each lead gets a unique subject and body (passed via `custom_fields`), the sequence template uses placeholder variables `{{email_subject}}` and `{{email_body}}`.

**Path parameter:** Use the `id` from Step 1.

**Request body:** None — this endpoint takes no body. It automatically sets up:

```
Sequence 1:
  Subject: {{email_subject}}
  Body:    {{email_body}}
  Delay:   0 days
```

**Response (200):**

```json
{
  "message": "Sequences configured",
  "smartlead_response": { ... }
}
```

**Why this works:** When you inject leads in Step 3, each lead's `subject` and `body_html` are passed to Smartlead as `custom_fields` named `email_subject` and `email_body`. Smartlead substitutes these per-lead values into the sequence template, so every lead gets their unique personalized email.

---

## Step 3: Inject Leads with Email Content

**Endpoint:** `POST /api/v1/campaigns/{campaign_id}/leads`

This is where you provide the actual email content for each lead. Each lead entry contains the recipient's info plus their personalized subject and body.

**Path parameter:** Use the `id` from Step 1.

**Request body:**

```json
{
  "leads": [
    {
      "email": "john.doe@acme.com",
      "subject": "Quick question about Acme's infrastructure",
      "body_html": "<p>Hi John,</p><p>I noticed Acme is scaling its cloud infrastructure. We help companies like yours reduce costs by 40%.</p><p>Worth a 15-min chat?</p>",
      "body_text": "Hi John, I noticed Acme is scaling its cloud infrastructure...",
      "first_name": "John",
      "last_name": "Doe",
      "company": "Acme Corp",
      "linkedin_url": "https://linkedin.com/in/johndoe",
      "prompt_version": "v2.1",
      "model_name": "gpt-4o",
      "context_snapshot": {
        "research_date": "2026-03-28",
        "signals": ["hiring DevOps", "Series C"]
      }
    },
    {
      "email": "jane.smith@globex.com",
      "subject": "Loved your talk at CloudConf, Jane",
      "body_html": "<p>Hi Jane,</p><p>Your CloudConf talk on microservices was great. We built a tool that addresses exactly the scaling challenges you mentioned.</p><p>Open to a quick demo?</p>",
      "first_name": "Jane",
      "last_name": "Smith",
      "company": "Globex Inc"
    }
  ]
}
```

**Required fields per lead:** `email`, `subject`, `body_html`

**Optional fields per lead:** `body_text`, `first_name`, `last_name`, `company`, `linkedin_url`, `prompt_version`, `model_name`, `context_snapshot`

**Response (201):**

```json
{
  "campaign_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "total_received": 2,
  "total_created": 2,
  "total_skipped_duplicate": 0,
  "message": "2 leads injected, 0 duplicates skipped. 2 pushed to Smartlead."
}
```

**What happens behind the scenes:**
- Each lead is upserted in the `leads` table (deduplicated by email)
- A `campaign_lead_links` record connects the lead to this campaign
- An `outbound_messages` record stores the immutable email snapshot (subject, body, LLM metadata)
- Leads are pushed to Smartlead in batches of 400 with `custom_fields` containing `email_subject` and `email_body`

**Notes:**
- You can send up to 5,000 leads per request (they're batched to Smartlead in groups of 400)
- Duplicate leads (same email already linked to this campaign) are silently skipped
- The `prompt_version`, `model_name`, and `context_snapshot` fields are for your audit trail — they're stored but not sent to Smartlead

---

## Step 4: List Available Sender Accounts

**Endpoint:** `GET /api/v1/campaigns/sender-accounts/list`

Before you can assign a sender, you need to know which email accounts are available in your Smartlead account.

**Request body:** None.

**Response (200):**

```json
{
  "accounts": [
    {
      "id": 98765,
      "from_email": "sarah@outreach.yourcompany.com",
      "from_name": "Sarah Johnson",
      "type": "SMTP",
      "warmup_status": "completed",
      "max_email_per_day": 50,
      ...
    },
    {
      "id": 98766,
      "from_email": "mike@outreach.yourcompany.com",
      "from_name": "Mike Chen",
      "type": "GMAIL",
      "warmup_status": "in_progress",
      "max_email_per_day": 30,
      ...
    }
  ]
}
```

**Save the `id` value(s)** of the account(s) you want to use. These are Smartlead's integer IDs, not UUIDs.

**Tips:**
- Choose accounts with `warmup_status: "completed"` for best deliverability
- You can assign multiple accounts for sender rotation (recommended)

---

## Step 5: Assign Sender Account to Campaign

**Endpoint:** `POST /api/v1/campaigns/{campaign_id}/sender`

This links one or more sender email accounts to your campaign on Smartlead. **This step is mandatory** — Smartlead will not send any emails without at least one linked sender account.

**Path parameter:** Use the `id` from Step 1.

**Request body:**

```json
{
  "email_account_ids": [98765]
}
```

To assign multiple sender accounts for rotation:

```json
{
  "email_account_ids": [98765, 98766]
}
```

**Response (200):**

```json
{
  "campaign_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "provider_campaign_id": "12345",
  "email_account_ids": [98765],
  "synced": true,
  "smartlead_error": null,
  "smartlead_response": { ... }
}
```

If `synced` is `true`, the sender account is linked and you're ready to proceed.

---

## Step 6: Configure Schedule

**Endpoint:** `POST /api/v1/campaigns/{campaign_id}/settings`

Set the sending window — which days and hours Smartlead should send emails.

**Path parameter:** Use the `id` from Step 1.

**Request body:**

```json
{
  "schedule": {
    "timezone": "America/New_York",
    "days_of_the_week": [1, 2, 3, 4, 5],
    "start_hour": "09:00",
    "end_hour": "17:00",
    "min_time_btw_emails": 120,
    "max_new_leads_per_day": 50
  }
}
```

**Schedule fields:**
- `timezone` — IANA timezone string (e.g., `"America/New_York"`, `"Europe/London"`, `"Asia/Karachi"`)
- `days_of_the_week` — 0=Sunday, 1=Monday, ..., 6=Saturday. The example above sends Monday through Friday.
- `start_hour` / `end_hour` — sending window in the specified timezone
- `min_time_btw_emails` — minimum gap in minutes between consecutive emails
- `max_new_leads_per_day` — optional daily cap on new leads to email

**Response (200):**

```json
{
  "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "message": "Settings updated",
  "schedule_synced": true,
  "sender_updated": false
}
```

---

## Step 7: Start the Campaign

**Endpoint:** `POST /api/v1/campaigns/{campaign_id}/status`

Once sequences, leads, sender, and schedule are all configured, you can start the campaign.

**Path parameter:** Use the `id` from Step 1.

**Request body:**

```json
{
  "status": "start"
}
```

**Response (200):**

```json
{
  "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "status": "active",
  "smartlead_synced": true,
  "smartlead_error": null
}
```

If `smartlead_synced` is `true`, the campaign is now live and Smartlead will begin sending emails according to the schedule.

**Other status actions:**
- `{"status": "pause"}` — temporarily stop sending (can resume later)
- `{"status": "stop"}` — permanently stop the campaign

---

## Monitoring Your Campaign

### Get Campaign Details

**Endpoint:** `GET /api/v1/campaigns/{campaign_id}`

Returns campaign info with aggregate lead count.

```json
{
  "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "name": "Q3 Healthcare Outreach",
  "status": "active",
  "provider_campaign_id": "12345",
  "total_leads": 2,
  "total_sent": 0,
  "total_opened": 0,
  "total_replied": 0,
  "total_bounced": 0,
  ...
}
```

### List All Campaigns

**Endpoint:** `GET /api/v1/campaigns`

Returns all campaigns ordered by creation date (newest first).

### Health Check

**Endpoint:** `GET /health`

Verifies database and Redis connectivity.

---

## Complete cURL Example

For reference, here's the entire workflow as cURL commands:

```bash
BASE_URL="http://localhost:8000/api/v1"

# Step 1: Create campaign
CAMPAIGN=$(curl -s -X POST "$BASE_URL/campaigns" \
  -H "Content-Type: application/json" \
  -d '{"name": "Q3 Healthcare Outreach"}')
CAMPAIGN_ID=$(echo $CAMPAIGN | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
echo "Campaign ID: $CAMPAIGN_ID"

# Step 2: Set up sequences
curl -s -X POST "$BASE_URL/campaigns/$CAMPAIGN_ID/sequences"

# Step 3: Inject leads
curl -s -X POST "$BASE_URL/campaigns/$CAMPAIGN_ID/leads" \
  -H "Content-Type: application/json" \
  -d '{
    "leads": [
      {
        "email": "john@acme.com",
        "subject": "Quick question about Acme",
        "body_html": "<p>Hi John, worth a quick chat?</p>",
        "first_name": "John",
        "company": "Acme Corp"
      }
    ]
  }'

# Step 4: List sender accounts
curl -s "$BASE_URL/campaigns/sender-accounts/list"
# Note the "id" field from the response

# Step 5: Assign sender (replace 98765 with actual ID)
curl -s -X POST "$BASE_URL/campaigns/$CAMPAIGN_ID/sender" \
  -H "Content-Type: application/json" \
  -d '{"email_account_ids": [98765]}'

# Step 6: Configure schedule
curl -s -X POST "$BASE_URL/campaigns/$CAMPAIGN_ID/settings" \
  -H "Content-Type: application/json" \
  -d '{
    "schedule": {
      "timezone": "America/New_York",
      "days_of_the_week": [1,2,3,4,5],
      "start_hour": "09:00",
      "end_hour": "17:00",
      "min_time_btw_emails": 120
    }
  }'

# Step 7: Start campaign
curl -s -X POST "$BASE_URL/campaigns/$CAMPAIGN_ID/status" \
  -H "Content-Type: application/json" \
  -d '{"status": "start"}'
```

---

## Troubleshooting

**Campaign won't start — "no sender account"**
You skipped Step 5. Smartlead requires at least one sender email account linked to the campaign. Call `GET /campaigns/sender-accounts/list`, pick an ID, and call `POST /campaigns/{id}/sender`.

**Campaign won't start — "no sequences"**
You skipped Step 2. Call `POST /campaigns/{id}/sequences` to set up the email template.

**Leads show `sync_error` status**
The push to Smartlead failed. Check your `SMARTLEAD_API_KEY` in `.env` and ensure the Smartlead campaign was created successfully (check `provider_campaign_id` in the campaign response).

**Duplicate leads are skipped**
This is expected. If a lead with the same email is already linked to the same campaign, the system skips it and increments `total_skipped_duplicate`. This prevents accidental double-sends.

**`502` error on sender account endpoints**
Your server can't reach Smartlead's API. Check your network connection and that `SMARTLEAD_BASE_URL` is correct in `.env`.

**Schedule not taking effect**
Make sure `schedule_synced: true` in the settings response. If it's `false`, the Smartlead API call failed — check logs for the specific error.