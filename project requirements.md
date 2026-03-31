
---

# 📄 AI Outreach System – Technical Design Report

## 1. High-Level Requirements

### Core Functional Requirements

The system should:

1. **Campaign Management**

   * Create campaigns
   * Configure campaign rules:

     * sending schedule
     * rate limits
     * sender accounts
     * follow-up logic
   * Start / pause / stop campaigns

2. **Lead & Email Injection**

   * Accept per-lead input:

     * email
     * subject
     * body
   * Support hyper-personalized emails (LLM-generated)
   * Associate each lead with a campaign

3. **Email Sending (via Smartlead)**

   * Push campaigns and leads via API
   * Map internal campaigns to Smartlead campaigns
   * Track provider IDs (campaign, lead, message)

4. **Event Tracking**

   * Track lifecycle events:

     * sent
     * opened
     * clicked
     * replied
     * bounced
     * unsubscribed
   * Store timestamps for each event
   * Store raw webhook payloads

5. **Audit & Debugging**

   * Retrieve all interactions for a given email
   * Retrieve full campaign data
   * View exact content sent
   * View event timelines
   * Maintain immutable message snapshots

6. **Data Persistence**

   * Store:

     * campaigns
     * leads
     * messages
     * events
     * mappings to Smartlead
   * Ensure all records are timestamped

---

### Non-Functional Requirements

* **Traceability**: Every email must be reconstructable
* **Idempotency**: Webhooks and retries must not duplicate data
* **Scalability**: Support thousands of leads per campaign
* **Performance**: Fast retrieval by email and campaign
* **Extensibility**: Support multiple providers in future
* **Reliability**: No data loss on webhook failures

---

### Additional Requirements (Important Additions)

You implicitly need:

* **LLM metadata tracking**

  * prompt version
  * model used
  * context snapshot

* **Deduplication logic**

  * avoid sending multiple emails unintentionally

* **Internal vs Provider separation**

  * Your system = source of truth
  * Smartlead = execution layer

* **Monitoring capability**

  * basic dashboard or debug API

---

# 2. Low-Level Requirements

## 2.1 Data Storage

Use:

* **Primary DB**: PostgreSQL
* **Optional**:

  * Redis (queue/caching)
  * S3 (large context snapshots if needed)

---

## 2.2 Database Schema (Detailed)

### Tables

#### 1. `leads`

Stores unique prospects.

Fields:

* id (UUID, PK)
* email (CITEXT, indexed)
* first_name
* last_name
* company
* linkedin_url
* created_at
* updated_at

---

#### 2. `internal_campaigns`

Business-level campaigns.

Fields:

* id
* name
* persona
* segment
* status
* created_at
* updated_at

---

#### 3. `sender_accounts`

Email sending accounts.

Fields:

* id
* email
* provider_account_id
* warmup_status
* created_at
* updated_at

---

#### 4. `campaign_deliveries`

Mapping to Smartlead campaigns.

Fields:

* id
* internal_campaign_id (FK)
* provider ("smartlead")
* provider_campaign_id
* sender_account_id
* status
* created_at
* updated_at

---

#### 5. `campaign_lead_links`

Many-to-many mapping between leads and campaigns.

Fields:

* id
* campaign_delivery_id (FK)
* lead_id (FK)
* provider_lead_id
* status
* added_at
* updated_at

---

#### 6. `outbound_messages` ⭐ (Core Table)

Stores exact email snapshot.

Fields:

* id
* campaign_link_id (FK)
* step_number
* subject
* body_html
* body_text
* prompt_version
* model_name
* context_snapshot (JSONB)
* custom_fields (JSONB)
* provider_message_id
* message_status
* generated_at
* sent_at
* created_at
* updated_at

---

#### 7. `message_events`

Stores all events.

Fields:

* id
* outbound_message_id (FK)
* event_type
* provider_event_id
* event_time
* received_at
* payload_json (JSONB)
* created_at

---

#### 8. `webhook_receipts` (Debug Table)

Stores raw webhook logs.

Fields:

* id
* provider
* payload_json
* headers_json
* dedupe_key
* processing_status
* error_message
* received_at

---

## 2.3 Mapping Diagram

```text
┌──────────────────────┐
│   internal_campaigns │
│----------------------│
│ id (PK)              │
│ name                 │
│ persona              │
│ segment              │
│ status               │
│ created_at           │
│ updated_at           │
└─────────┬────────────┘
          │ 1-to-many
          │
          ▼
┌──────────────────────┐
│  campaign_deliveries │
│----------------------│
│ id (PK)              │
│ internal_campaign_id │ FK
│ provider             │  (smartlead)
│ provider_campaign_id │
│ sender_account_id FK │
│ status               │
│ created_at           │
│ updated_at           │
└──────┬─────────┬─────┘
       │         │
       │         │ 1-to-many
       │         ▼
       │   ┌──────────────────────┐
       │   │ campaign_lead_links  │
       │   │----------------------│
       │   │ id (PK)              │
       │   │ campaign_delivery_id │ FK
       │   │ lead_id              │ FK
       │   │ provider_lead_id     │
       │   │ status               │
       │   │ added_at             │
       │   │ updated_at           │
       │   └──────┬───────────────┘
       │          │
       │          │ 1-to-many
       │          ▼
       │   ┌──────────────────────┐
       │   │  outbound_messages   │
       │   │----------------------│
       │   │ id (PK)              │
       │   │ campaign_link_id FK  │
       │   │ step_number          │
       │   │ subject              │
       │   │ body_html            │
       │   │ body_text            │
       │   │ prompt_version       │
       │   │ context_snapshot     │ JSONB
       │   │ custom_fields        │ JSONB
       │   │ provider_message_id  │
       │   │ sent_at              │
       │   │ created_at           │
       │   │ updated_at           │
       │   └──────┬───────────────┘
       │          │
       │          │ 1-to-many
       │          ▼
       │   ┌──────────────────────┐
       │   │   message_events     │
       │   │----------------------│
       │   │ id (PK)              │
       │   │ outbound_message_id  │ FK
       │   │ event_type           │
       │   │ provider_event_id    │
       │   │ event_time           │
       │   │ received_at          │
       │   │ payload_json         │ JSONB
       │   │ created_at           │
       │   └──────────────────────┘
       │
       │
       │ many-to-1
       ▼
┌──────────────────────┐
│        leads         │
│----------------------│
│ id (PK)              │
│ email (UNIQUE-ish*)  │
│ first_name           │
│ last_name            │
│ company              │
│ linkedin_url         │
│ created_at           │
│ updated_at           │
└──────────────────────┘

┌──────────────────────┐
│   sender_accounts    │
│----------------------│
│ id (PK)              │
│ email                │
│ provider_account_id  │
│ warmup_status        │
│ created_at           │
│ updated_at           │
└──────────────────────┘
```



---

## 2.4 Data Flow

### Step 1: Campaign Creation

* Create internal campaign
* Create Smartlead campaign
* Store mapping

---

### Step 2: Lead Injection

* Insert leads into DB
* Create campaign_lead_links

---

### Step 3: Message Generation

* LLM generates subject/body
* Store snapshot in `outbound_messages`

---

### Step 4: Send via Smartlead

* Push lead + custom fields
* Store provider IDs

---

### Step 5: Webhook Processing

* Receive webhook
* Store raw payload
* Map to message
* Insert into `message_events`
* Update message status

---

## 2.5 Retrieval Logic

### A. Email-Based Retrieval

Input:

* email

Process:

1. Find lead
2. Fetch campaign links
3. Fetch messages
4. Fetch events

Output:

* full timeline across campaigns

---

### B. Campaign-Based Retrieval

Input:

* campaign_id

Process:

1. Fetch deliveries
2. Fetch leads
3. Fetch messages
4. Fetch events

Output:

* full campaign report

---

## 2.6 API Design

### Core APIs

#### Campaign

* `POST /campaigns`
* `POST /campaigns/{id}/start`
* `POST /campaigns/{id}/pause`

#### Leads

* `POST /campaigns/{id}/leads`

#### Webhooks

* `POST /webhooks/smartlead`

#### Retrieval

* `GET /leads/{email}/interactions`
* `GET /campaigns/{id}/details`

---

## 2.7 Event Handling

### Required Events

* sent
* opened
* clicked
* replied
* bounced
* unsubscribed

### Idempotency

* dedupe using:

  * provider_event_id OR
  * hash(payload)

---

## 2.8 Indexing Strategy

Critical indexes:

* `leads(email)`
* `campaign_lead_links(lead_id)`
* `campaign_deliveries(internal_campaign_id)`
* `outbound_messages(campaign_link_id)`
* `message_events(outbound_message_id, event_time)`

---

# 3. Debugging & Observability

## Message Timeline View

For any email:

```text
Lead: john@acme.com

Campaign: Healthcare Outreach
  Message:
    Subject: ...
    Sent: ...
    Opened: ...
    Replied: ...
```

---

## Debug Capabilities

* Search by email
* Search by campaign
* View message content
* View event timeline
* View raw webhook payload

---

## Why This Design Works

### 1. Full Auditability

Every email is stored permanently.

### 2. Easy Debugging

No need to rely on Smartlead UI.

### 3. Flexibility

Supports:

* multiple providers
* multiple campaigns
* multiple sends per lead

### 4. Scalable

Handles:

* large campaigns
* multiple events per message

---

# 4. Additional Recommendations

## 4.1 LLM Tracking (Important for You)

Store:

* prompt_version
* model_name
* context_snapshot
* generation_id

---

## 4.2 Hybrid Personalization Strategy

Use:

* templates for bulk
* full custom body for high-value leads

---

## 4.3 Internal Dashboard (Optional but Recommended)

Start with:

* FastAPI + simple UI (Streamlit or admin panel)

Later:

* React dashboard if needed

---

## 4.4 Future Enhancements

* campaign analytics aggregation
* bounce rate monitoring
* auto-pause campaigns
* reply classification (AI)
* lead scoring

---

# 🚀 Final Summary

This system is designed to:

* Treat Smartlead as a **sending engine**
* Treat your backend as the **source of truth**
* Store **immutable message snapshots**
* Track **full event timelines**
* Enable **email-based and campaign-based debugging**

---


