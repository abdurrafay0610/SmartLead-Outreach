# AI Outreach System

Campaign automation backend that uses Smartlead as the sending engine while maintaining your own system as the source of truth.

## Core Concept

```
You provide: (email, subject, body) per lead
System handles: storage → Smartlead push → event tracking → full audit trail
```

## Project Structure

```
outreach-backend/
├── app/
│   ├── api/routers/          # FastAPI route handlers
│   │   ├── campaigns.py      # Campaign CRUD + status management
│   │   ├── leads.py          # Lead injection with email content
│   │   ├── webhooks.py       # Smartlead webhook receiver (Phase 4)
│   │   └── health.py         # Health check endpoint
│   ├── core/
│   │   └── config.py         # Environment-based settings
│   ├── db/
│   │   ├── base.py           # SQLAlchemy base + mixins
│   │   ├── session.py        # Async database session
│   │   └── redis.py          # Redis connection
│   ├── models/               # SQLAlchemy ORM models (8 tables)
│   │   ├── lead.py
│   │   ├── internal_campaign.py
│   │   ├── sender_account.py
│   │   ├── campaign_delivery.py
│   │   ├── campaign_lead_link.py
│   │   ├── outbound_message.py   # ⭐ Core table — immutable email snapshots
│   │   ├── message_event.py
│   │   └── webhook_receipt.py
│   ├── schemas/              # Pydantic request/response models
│   │   ├── campaign.py
│   │   ├── lead.py
│   │   ├── webhook.py
│   │   └── common.py
│   ├── services/             # Business logic (Phase 2+)
│   └── main.py               # FastAPI app entrypoint
├── alembic/                  # Database migrations
│   ├── env.py
│   ├── script.py.mako
│   └── versions/
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
createdb outreach
```

### 5. Run migrations
```bash
# Generate initial migration from models
alembic revision --autogenerate -m "initial_schema"

# Apply migration
alembic upgrade head
```

### 6. Start the server
```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### 7. Open API docs
Visit `http://localhost:8000/docs` for the interactive Swagger UI.

## API Endpoints (Phase 1)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Database + Redis health check |
| POST | `/api/v1/campaigns` | Create a campaign |
| GET | `/api/v1/campaigns` | List all campaigns |
| GET | `/api/v1/campaigns/{id}` | Get campaign details + stats |
| POST | `/api/v1/campaigns/{id}/status` | Start / pause / stop campaign |
| POST | `/api/v1/campaigns/{id}/settings` | Update schedule, sender, limits |
| POST | `/api/v1/campaigns/{id}/leads` | Inject leads with email content |
| POST | `/api/v1/webhooks/smartlead` | Webhook receiver (stub) |

## Database Schema

8 tables with the following relationship chain:

```
internal_campaigns
  └── campaign_deliveries (maps to Smartlead campaign)
        └── campaign_lead_links (lead ↔ campaign association)
              └── outbound_messages (immutable email snapshot)
                    └── message_events (sent, opened, clicked, replied, bounced...)

leads (deduplicated by email)
sender_accounts (email sending accounts)
webhook_receipts (raw webhook debug log)
```

## Implementation Phases

- [x] **Phase 1**: Project scaffold + DB schema + basic APIs
- [ ] **Phase 2**: Smartlead API client (async httpx wrapper with retries)
- [ ] **Phase 3**: Campaign + lead management with Smartlead sync
- [ ] **Phase 4**: Webhook receiver + event tracking
- [ ] **Phase 5**: Retrieval + debug APIs
- [ ] **Phase 6**: Operational hardening
