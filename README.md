# AI Outreach System

Campaign automation backend that uses Smartlead as the sending engine while maintaining your own system as the source of truth.

## Core Concept

```
You provide: (email, subject, body) per lead — per sequence step
System handles: storage → Smartlead push → event tracking → full audit trail
```

## Project Structure

```
outreach-backend/
├── app/
│   ├── api/routers/
│   │   ├── campaigns.py      # Campaign CRUD + status + sequences + sender
│   │   ├── leads.py          # Lead injection with multi-step email content
│   │   ├── webhooks.py       # Smartlead webhook receiver (stub — Phase 4)
│   │   └── health.py         # Health check endpoint
│   ├── core/
│   │   └── config.py         # Environment-based settings (Pydantic)
│   ├── db/
│   │   ├── base.py           # SQLAlchemy base + mixins (UUID PK, timestamps)
│   │   ├── session.py        # Async database session + get_db dependency
│   │   └── redis.py          # Redis async connection
│   ├── models/               # SQLAlchemy ORM models (8 tables)
│   │   ├── __init__.py        # Re-exports all models
│   │   ├── lead.py
│   │   ├── internal_campaign.py
│   │   ├── sender_account.py
│   │   ├── campaign_delivery.py
│   │   ├── campaign_lead_link.py
│   │   ├── outbound_message.py   # ⭐ Core table — immutable email snapshots
│   │   ├── message_event.py
│   │   └── webhook_receipt.py
│   ├── schemas/              # Pydantic request/response models
│   │   ├── campaign.py       # Create, status, settings, sequences, sender assignment
│   │   ├── lead.py           # Multi-step lead injection + interaction responses
│   │   ├── webhook.py        # Webhook receipt response
│   │   └── common.py         # Health, pagination, error responses
│   ├── services/
│   │   ├── smartlead_client.py  # Async httpx wrapper with retries + rate-limit handling
│   │   └── campaign_service.py  # Orchestration layer (DB ↔ Smartlead sync)
│   └── main.py               # FastAPI app entrypoint + lifespan
├── alembic/
│   ├── env.py                # Async migration runner
│   ├── script.py.mako
│   └── versions/
│       └── 537a85a77311_initial_schema.py
├── alembic.ini
├── requirements.txt
└── .env.example
```

## Setup

### 1. Prerequisites
- Python 3.11+
- PostgreSQL (running)
- Redis (running)

### 2. Install dependencies
```bash
pip install -r requirements.txt
```

### 3. Configure environment
```bash
cp .env.example .env
# Edit .env with your database, Redis, and Smartlead credentials
```

### 4. Create the database
```bash
docker run -d \
  --name outreach-postgres \
  -e POSTGRES_USER=postgres \
  -e POSTGRES_PASSWORD=postgres \
  -e POSTGRES_DB=outreach \
  -p 5432:5432 \
  postgres:16
```

### 5. Run migrations
```bash
alembic upgrade head
```

### 6. Start the server
```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### 7. Open API docs
Visit `http://localhost:8000/docs` for the interactive Swagger UI.

## API Endpoints

### Health
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Database + Redis connectivity check |

### Campaigns
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v1/campaigns` | Create a campaign (syncs to Smartlead) |
| GET | `/api/v1/campaigns` | List all campaigns |
| GET | `/api/v1/campaigns/{id}` | Get campaign details + aggregate stats |
| POST | `/api/v1/campaigns/{id}/status` | Start / pause / stop campaign |
| POST | `/api/v1/campaigns/{id}/settings` | Update schedule, sender, limits |
| POST | `/api/v1/campaigns/{id}/sequences` | Set up sequence templates on Smartlead |
| POST | `/api/v1/campaigns/{id}/sender` | Assign sender email account(s) to campaign |
| GET | `/api/v1/campaigns/sender-accounts/list` | List available Smartlead sender accounts |

### Leads
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v1/campaigns/{id}/leads` | Inject leads with multi-step email content |

### Webhooks
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v1/webhooks/smartlead` | Webhook receiver (stub — Phase 4) |

## Campaign Workflow

The typical end-to-end flow for sending a campaign:

```
1. POST /campaigns                      → Create campaign (num_emails_per_lead=N)
2. POST /campaigns/{id}/sequences       → Set up N sequence step templates
3. POST /campaigns/{id}/sender          → Link sender email account(s)
4. POST /campaigns/{id}/settings        → Configure schedule + rate limits
5. POST /campaigns/{id}/leads           → Inject leads with N emails each
6. POST /campaigns/{id}/status          → Start the campaign
```

### Multi-Step Email Support

Campaigns support 1–10 sequence steps per lead. At creation time, `num_emails_per_lead` sets the number of steps. When injecting leads, each lead must provide exactly that many emails with sequential step numbers. Each step's subject and body are passed to Smartlead as numbered custom fields (`email_subject_1`, `email_body_1`, etc.) that match the sequence templates.

## Database Schema

8 tables with the following relationship chain:

```
internal_campaigns
  └── campaign_deliveries (maps to Smartlead campaign)
        └── campaign_lead_links (lead ↔ campaign association)
              └── outbound_messages (immutable email snapshot per step)
                    └── message_events (sent, opened, clicked, replied, bounced...)

leads (deduplicated by email)
sender_accounts (email sending accounts)
webhook_receipts (raw webhook debug log)
```

### Key Design Decisions
- **Immutable snapshots**: Every email body/subject is stored permanently in `outbound_messages` — never modified after creation.
- **LLM provenance**: Each outbound message can optionally store `prompt_version`, `model_name`, and `context_snapshot` for generation tracking.
- **Deduplication**: Unique constraints prevent duplicate lead-campaign links and duplicate step numbers per link.
- **Idempotent events**: `message_events` has a partial unique index on `provider_event_id` to prevent duplicate webhook processing.

## Services Architecture

### SmartleadClient (`app/services/smartlead_client.py`)
Async httpx wrapper with automatic retry on 429 (rate limit) and 5xx errors using exponential backoff. Covers all needed Smartlead endpoints: campaign create, sequences, leads (batched at 400), schedule, status, email accounts, and test email.

### CampaignService (`app/services/campaign_service.py`)
Orchestration layer that coordinates internal DB operations with Smartlead API sync. Routers call this service — never Smartlead directly. Handles: campaign creation with provider mapping, multi-step sequence setup, sender assignment, lead injection with batch push, and status updates.

## Configuration

Environment variables (see `.env.example`):

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `postgresql+asyncpg://...` | Async PostgreSQL connection |
| `DATABASE_URL_SYNC` | `postgresql+psycopg2://...` | Sync connection (for Alembic) |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection |
| `SMARTLEAD_API_KEY` | — | Your Smartlead API key |
| `SMARTLEAD_BASE_URL` | `https://server.smartlead.ai/api/v1` | Smartlead API base URL |
| `APP_ENV` | `development` | Environment (enables SQL echo in dev) |
| `LOG_LEVEL` | `INFO` | Logging level |
| `LEAD_BATCH_SIZE` | `400` | Leads per Smartlead API call (max 400) |

## Implementation Phases

- [x] **Phase 1**: Project scaffold + DB schema + basic APIs
- [x] **Phase 2**: Smartlead API client (async httpx wrapper with retries)
- [x] **Phase 3**: Campaign + lead management with Smartlead sync
- [ ] **Phase 4**: Webhook receiver + event tracking
- [ ] **Phase 5**: Retrieval + debug APIs
- [ ] **Phase 6**: Operational hardening