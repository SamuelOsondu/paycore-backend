# Runbook Notes — PayCore

## Local Development Setup

```bash
# 1. Clone and enter project
git clone <repo>
cd paycore-backend

# 2. Copy env
cp .env.example .env
# Fill in: DATABASE_URL, REDIS_URL, SECRET_KEY, PAYSTACK_SECRET_KEY, AWS creds

# 3. Start services
docker-compose up -d postgres redis

# 4. Create virtualenv and install
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 5. Run migrations
alembic upgrade head

# 6. Start API
uvicorn app.main:app --reload --port 8000

# 7. Start Celery worker (separate terminal)
celery -A app.workers.celery_app worker --loglevel=info

# 8. (Optional) Start Celery Beat for scheduled jobs
celery -A app.workers.celery_app beat --loglevel=info
```

## Docker Compose Full Stack

```bash
docker-compose up --build
# API: http://localhost:8000
# Docs: http://localhost:8000/docs
# Flower: http://localhost:5555
```

---

## Running Tests

```bash
# All tests
pytest

# With coverage
pytest --cov=app --cov-report=term-missing

# Specific module
pytest tests/integration/test_transfer_flow.py -v
```

Test database: set `TEST_DATABASE_URL` in environment or `.env.test` file.

---

## Environment Variables Reference

See `04_stack_and_infra.md` for full variable list.
Required for startup: `DATABASE_URL`, `REDIS_URL`, `SECRET_KEY`, `PAYSTACK_SECRET_KEY`

---

## Database Migrations

```bash
# Create new migration
alembic revision --autogenerate -m "description"

# Apply migrations
alembic upgrade head

# Rollback one step
alembic downgrade -1
```

---

## Creating First Admin User

```bash
python scripts/create_admin.py --email admin@paycore.com --password <secure_password>
```

---

## Paystack Webhook Local Testing

Use ngrok to expose local endpoint:
```bash
ngrok http 8000
# Set ngrok URL in Paystack dashboard webhook settings
# Endpoint: https://<ngrok-id>.ngrok.io/api/v1/webhooks/paystack
```

---

## Monitoring and Observability

- Application logs: structured JSON, stdout
- Celery task monitoring: Flower at `http://localhost:5555`
- Health check: `GET /health` → returns `{"status": "ok"}`
- Database health: included in health check response

---

## Common Failure Scenarios

### Paystack Webhook Not Received
1. Check ngrok tunnel is alive
2. Verify webhook URL is set correctly in Paystack dashboard
3. Run reconciliation job manually: `celery -A app.workers.celery_app call app.workers.reconciliation.check_stale_transactions`

### Celery Worker Not Processing
1. Check Redis is running: `redis-cli ping`
2. Check worker logs for errors
3. Inspect queue: `celery -A app.workers.celery_app inspect active`

### Transaction Stuck in Pending
1. Check `transactions` table for `status=pending` older than 30 minutes
2. If Paystack reference exists, verify manually via Paystack dashboard
3. Use admin endpoint `POST /api/v1/admin/transactions/{id}/resolve` (if implemented)

---

## Key API Docs

- Local: `http://localhost:8000/docs` (Swagger UI)
- Redoc: `http://localhost:8000/redoc`
- Paystack API: https://paystack.com/docs/api/
