# AI Outreach System — API Usage Guide

This guide walks you through the complete workflow of creating a campaign, injecting leads with personalized emails (including follow-ups), assigning sender accounts, and starting the campaign. All examples use the Swagger UI at `http://localhost:8000/docs`.

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
Step 1: Create Campaign (set how many emails per lead)
Step 2: Set Up Sequences (email templates + delays between steps)
Step 3: Inject Leads (with personalized email content for ALL steps)
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
  "segment": "Healthcare CTOs",
  "num_emails_per_lead": 3
}
```

**Required fields:** `name`

**Optional fields:** `persona`, `segment`, `num_emails_per_lead` (default: 1, max: 10)

The `num_emails_per_lead` field tells the system how many emails each lead will receive in this campaign's sequence. For example, `3` means each lead gets an initial email plus 2 follow-ups. This is fixed at campaign creation — when you inject leads later, each lead must provide exactly this many emails.

**Response (201):**

```json
{
  "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "name": "Q3 Healthcare Outreach",
  "persona": "Sales Rep",
  "segment": "Healthcare CTOs",
  "status": "drafted",
  "num_emails_per_lead": 3,
  "provider_campaign_id": "12345",
  "created_at": "2026-04-01T10:00:00Z",
  "updated_at": "2026-04-01T10:00:00Z"
}
```

**Save the `id` value** — you'll need it for every subsequent step.

**What happens behind the scenes:**
- An `internal_campaigns` record is created in your DB (with `num_emails_per_lead`)
- A campaign is created on Smartlead via `POST /campaigns/create`
- A `campaign_deliveries` record is created mapping your internal ID to the Smartlead `provider_campaign_id`

---

## Step 2: Set Up Sequences

**Endpoint:** `POST /api/v1/campaigns/{campaign_id}/sequences`

This configures the email sequence templates on Smartlead. The system automatically creates one sequence step per `num_emails_per_lead`, each using numbered placeholder variables.

**Path parameter:** Use the `id` from Step 1.

**Request body (optional):** You can provide custom delays between steps, or call with no body to use defaults.

**With custom delays:**

```json
{
  "step_delays": [
    {"step_number": 1, "delay_in_days": 0},
    {"step_number": 2, "delay_in_days": 3},
    {"step_number": 3, "delay_in_days": 7}
  ]
}
```

**With no body (default delays):** Step 1 = 0 days, Step 2 = 3 days, Step 3 = 5 days, Step 4+ = 7 days.

For a campaign with `num_emails_per_lead: 3`, this automatically sets up:

```
Sequence 1:
  Subject: {{email_subject_1}}
  Body:    {{email_body_1}}
  Delay:   0 days

Sequence 2:
  Subject: {{email_subject_2}}
  Body:    {{email_body_2}}
  Delay:   3 days

Sequence 3:
  Subject: {{email_subject_3}}
  Body:    {{email_body_3}}
  Delay:   7 days
```

**Response (200):**

```json
{
  "message": "Sequences configured",
  "num_steps": 3,
  "step_delays": {"1": 0, "2": 3, "3": 7},
  "smartlead_response": { ... }
}
```

**Why this works:** When you inject leads in Step 3, each lead's emails are passed to Smartlead as numbered `custom_fields` (`email_subject_1`, `email_body_1`, `email_subject_2`, `email_body_2`, etc.). Smartlead substitutes these per-lead values into each sequence step template, so every lead gets their unique personalized emails at each step.

**Notes:**
- Step 1 delay is always forced to 0 regardless of what you provide
- If you provide `step_delays`, you must provide exactly `num_emails_per_lead` entries

---

## Step 3: Inject Leads with Email Content

**Endpoint:** `POST /api/v1/campaigns/{campaign_id}/leads`

This is where you provide the actual email content for each lead. Each lead entry contains the recipient's info plus their personalized subject and body **for every sequence step**.

**Path parameter:** Use the `id` from Step 1.

**Request body (for a 3-email campaign):**

```json
{
  "leads": [
    {
      "email": "john.doe@acme.com",
      "first_name": "John",
      "last_name": "Doe",
      "company": "Acme Corp",
      "linkedin_url": "https://linkedin.com/in/johndoe",
      "emails": [
        {
          "step_number": 1,
          "subject": "Quick question about Acme's infrastructure",
          "body_html": "<p>Hi John,</p><p>I noticed Acme is scaling its cloud infrastructure. We help companies like yours reduce costs by 40%.</p><p>Worth a 15-min chat?</p>",
          "body_text": "Hi John, I noticed Acme is scaling its cloud infrastructure...",
          "prompt_version": "v2.1",
          "model_name": "gpt-4o",
          "context_snapshot": {
            "research_date": "2026-03-28",
            "signals": ["hiring DevOps", "Series C"]
          }
        },
        {
          "step_number": 2,
          "subject": "Following up on cloud costs, John",
          "body_html": "<p>Hi John,</p><p>Just checking back — I shared some thoughts on cloud cost reduction last week. We recently helped a similar company save 40% on AWS.</p><p>Would a quick case study be useful?</p>",
          "prompt_version": "v2.1",
          "model_name": "gpt-4o"
        },
        {
          "step_number": 3,
          "subject": "Last note from me, John",
          "body_html": "<p>Hi John,</p><p>I know things are busy. Just wanted to leave you with this — our platform takes 15 minutes to set up and shows savings on day one.</p><p>Happy to do a no-commitment walkthrough whenever works.</p>",
          "prompt_version": "v2.1",
          "model_name": "gpt-4o"
        }
      ]
    },
    {
      "email": "jane.smith@globex.com",
      "first_name": "Jane",
      "last_name": "Smith",
      "company": "Globex Inc",
      "emails": [
        {
          "step_number": 1,
          "subject": "Loved your talk at CloudConf, Jane",
          "body_html": "<p>Hi Jane,</p><p>Your CloudConf talk on microservices was great. We built a tool that addresses exactly the scaling challenges you mentioned.</p><p>Open to a quick demo?</p>"
        },
        {
          "step_number": 2,
          "subject": "Quick follow-up on microservices scaling",
          "body_html": "<p>Hi Jane,</p><p>Wanted to share a case study from a team that faced the same scaling issues you described at CloudConf.</p><p>Worth a look?</p>"
        },
        {
          "step_number": 3,
          "subject": "One more resource, Jane",
          "body_html": "<p>Hi Jane,</p><p>Last one from me — we published a guide on microservices scaling patterns that I think you'd find useful given your CloudConf talk.</p><p>Happy to chat if any of it resonates.</p>"
        }
      ]
    }
  ]
}
```

**Required fields per lead:** `email`, `emails` (array with exactly `num_emails_per_lead` entries)

**Required fields per email step:** `step_number`, `subject`, `body_html`

**Optional fields per lead:** `first_name`, `last_name`, `company`, `linkedin_url`

**Optional fields per email step:** `body_text`, `prompt_version`, `model_name`, `context_snapshot`

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
- One `outbound_messages` record is created **per step per lead** (e.g., 3 emails × 2 leads = 6 outbound_message rows), each storing the immutable email snapshot
- Leads are pushed to Smartlead in batches of 400 with `custom_fields` containing all numbered keys (`email_subject_1`, `email_body_1`, `email_subject_2`, `email_body_2`, `email_subject_3`, `email_body_3`)

**Notes:**
- You can send up to 5,000 leads per request (they're batched to Smartlead in groups of 400)
- Duplicate leads (same email already linked to this campaign) are silently skipped
- Each lead must provide exactly `num_emails_per_lead` emails with sequential step numbers starting from 1 — missing or extra steps are rejected
- The `prompt_version`, `model_name`, and `context_snapshot` fields are per-step and optional — they're stored for your audit trail but not sent to Smartlead

**Validation rules:**
- `emails` array must have exactly `num_emails_per_lead` entries
- `step_number` values must be sequential: 1, 2, 3, ... (no gaps, no duplicates)
- If any lead fails validation, the entire request is rejected

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

If `smartlead_synced` is `true`, the campaign is now live and Smartlead will begin sending emails according to the schedule. Follow-up emails are sent automatically based on the delays configured in Step 2.

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
  "num_emails_per_lead": 3,
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

Returns all campaigns ordered by creation date (newest first). Each campaign includes `num_emails_per_lead` so you know how many emails to provide when injecting leads.

### Health Check

**Endpoint:** `GET /health`

Verifies database and Redis connectivity.

---

## Complete cURL Example (3-Email Campaign)

For reference, here's the entire workflow as cURL commands for a campaign with 3 emails per lead:

```bash
BASE_URL="http://localhost:8000/api/v1"

# Step 1: Create campaign with 3 emails per lead
CAMPAIGN=$(curl -s -X POST "$BASE_URL/campaigns" \
  -H "Content-Type: application/json" \
  -d '{"name": "Q3 Healthcare Outreach", "num_emails_per_lead": 3}')
CAMPAIGN_ID=$(echo $CAMPAIGN | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
echo "Campaign ID: $CAMPAIGN_ID"

# Step 2: Set up sequences with custom delays
curl -s -X POST "$BASE_URL/campaigns/$CAMPAIGN_ID/sequences" \
  -H "Content-Type: application/json" \
  -d '{
    "step_delays": [
      {"step_number": 1, "delay_in_days": 0},
      {"step_number": 2, "delay_in_days": 3},
      {"step_number": 3, "delay_in_days": 7}
    ]
  }'

# Step 3: Inject leads with all 3 emails per lead
curl -s -X POST "$BASE_URL/campaigns/$CAMPAIGN_ID/leads" \
  -H "Content-Type: application/json" \
  -d '{
    "leads": [
      {
        "email": "john@acme.com",
        "first_name": "John",
        "company": "Acme Corp",
        "emails": [
          {
            "step_number": 1,
            "subject": "Quick question about Acme",
            "body_html": "<p>Hi John, worth a quick chat about cloud costs?</p>"
          },
          {
            "step_number": 2,
            "subject": "Following up, John",
            "body_html": "<p>Hi John, just checking back on my note...</p>"
          },
          {
            "step_number": 3,
            "subject": "Last note from me",
            "body_html": "<p>Hi John, one final thought...</p>"
          }
        ]
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

## Single-Email Campaign Example

If you only need one email per lead (no follow-ups), set `num_emails_per_lead: 1` or omit it entirely:

```bash
# Step 1: Create single-email campaign
curl -s -X POST "$BASE_URL/campaigns" \
  -H "Content-Type: application/json" \
  -d '{"name": "Quick One-Off Outreach"}'

# Step 2: Set up sequences (no body needed, defaults work)
curl -s -X POST "$BASE_URL/campaigns/$CAMPAIGN_ID/sequences"

# Step 3: Inject leads (one email per lead)
curl -s -X POST "$BASE_URL/campaigns/$CAMPAIGN_ID/leads" \
  -H "Content-Type: application/json" \
  -d '{
    "leads": [
      {
        "email": "john@acme.com",
        "first_name": "John",
        "company": "Acme Corp",
        "emails": [
          {
            "step_number": 1,
            "subject": "Quick question about Acme",
            "body_html": "<p>Hi John, worth a quick chat?</p>"
          }
        ]
      }
    ]
  }'
```

---

## Troubleshooting

**Campaign won't start — "no sender account"**
You skipped Step 5. Smartlead requires at least one sender email account linked to the campaign. Call `GET /campaigns/sender-accounts/list`, pick an ID, and call `POST /campaigns/{id}/sender`.

**Campaign won't start — "no sequences"**
You skipped Step 2. Call `POST /campaigns/{id}/sequences` to set up the email templates.

**Lead injection fails — "expected N emails, got M"**
Each lead must provide exactly `num_emails_per_lead` emails. Check the campaign's `num_emails_per_lead` value with `GET /campaigns/{id}` and make sure each lead's `emails` array has that many entries.

**Lead injection fails — "step numbers must be sequential"**
The `step_number` values in each lead's `emails` array must be 1, 2, 3, ... with no gaps or duplicates.

**Sequence setup fails — "expected N step delays, got M"**
If you provide `step_delays`, you must provide exactly `num_emails_per_lead` entries. Either provide all of them or omit `step_delays` entirely to use defaults.

**Leads show `sync_error` status**
The push to Smartlead failed. Check your `SMARTLEAD_API_KEY` in `.env` and ensure the Smartlead campaign was created successfully (check `provider_campaign_id` in the campaign response).

**Duplicate leads are skipped**
This is expected. If a lead with the same email is already linked to the same campaign, the system skips it and increments `total_skipped_duplicate`. This prevents accidental double-sends.

**`502` error on sender account endpoints**
Your server can't reach Smartlead's API. Check your network connection and that `SMARTLEAD_BASE_URL` is correct in `.env`.

**Schedule not taking effect**
Make sure `schedule_synced: true` in the settings response. If it's `false`, the Smartlead API call failed — check logs for the specific error.

**Follow-up emails not sending**
Make sure you configured delays in Step 2. Check `step_delays` in the sequences response — if step 2 has `delay_in_days: 3`, the follow-up won't send until 3 days after the first email. Also verify the campaign status is `active`.