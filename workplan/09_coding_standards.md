# Coding Standards — PayCore

## Layer Rules

### Routers (`app/api/v1/`)
- Thin. Parse request, call service, return response.
- No business logic. No DB queries. No direct model access.
- Validate input via Pydantic schema (FastAPI handles this automatically).
- Inject dependencies: `current_user`, `db_session`, `merchant` via FastAPI Depends.

### Services (`app/services/`)
- Own all business logic and transaction coordination.
- Call repositories for DB access.
- Call integration clients for external APIs.
- Raise typed exceptions for known failure cases.
- Manage DB transaction scope: `async with session.begin()` where needed.

### Repositories (`app/repositories/`)
- Pure database access. No business logic.
- Return ORM model instances or None.
- Accept `session` as a parameter (injected by service).
- All queries here — never scatter DB calls in services.

### Integrations (`app/integrations/`)
- Thin wrappers around external APIs.
- Raise `PaystackError`, `StorageError` with meaningful messages.
- No business logic. No DB calls.
- Return typed Python dataclasses or dicts.

### Workers (`app/workers/`)
- Celery task functions. Thin. Delegate to services.
- Handle retry logic via Celery `autoretry_for` or manual `self.retry(...)`.
- Use sync DB session (separate from async app session).

### Models (`app/models/`)
- SQLAlchemy ORM models only. No methods. No business logic.
- Relationships defined clearly with `relationship()` and appropriate `lazy=` setting.

### Schemas (`app/schemas/`)
- Pydantic v2 models.
- Separate request schemas (Input) from response schemas (Output).
- `ConfigDict(extra='forbid')` on all request schemas.
- Do not use ORM model instances as response — always serialize through schema.

---

## Naming Conventions

- Files: `snake_case.py`
- Classes: `PascalCase`
- Functions/methods: `snake_case`
- Constants: `UPPER_SNAKE_CASE`
- DB column names: `snake_case`
- Pydantic field names: `snake_case` (JSON output uses snake_case)
- Service classes: `{Domain}Service` e.g. `WalletService`, `TransferService`
- Repository classes: `{Domain}Repository` e.g. `WalletRepository`
- Exception classes: `{Domain}Error` e.g. `InsufficientBalanceError`, `KYCTierError`

---

## Standard Response Format

Every endpoint must return this envelope — no exceptions.

**Success:**
```json
{ "success": true, "message": "Profile retrieved.", "data": { ... } }
```

**Error:**
```json
{ "success": false, "message": "User not found.", "error": "USER_NOT_FOUND", "data": null }
```

**Paginaged data goes inside `data`:**
```json
{ "success": true, "message": "Transactions retrieved.", "data": { "items": [...], "total": 42, "limit": 20, "offset": 0 } }
```

Use `app/core/response.py` helpers:
- `success_response(data, message)` → returns the dict
- `error_response(message, error_code)` → returns the dict

Use `ApiResponse[T]` from `app/schemas/common.py` as `response_model` on all routes.
FastAPI validation errors are also transformed to this shape via the global exception handler.

---

## Error Handling

- Define custom exceptions in `app/core/exceptions.py`
- FastAPI exception handlers registered in `app/main.py`
- Pattern: service raises `AppError` subclass → handler converts to standard error envelope
- Never expose stack traces to clients
- Always log full exception with traceback at ERROR level internally

---

## No AI Noise

Do not write:
- Obvious comments: `# Get the user by ID`
- Redundant docstrings on simple functions
- Decorative file headers
- Type annotations on already-obvious variables

Do write:
- Docstrings on non-obvious service methods
- Comments explaining business rule rationale (not mechanics)
- Type hints on all function signatures

---

## Type Hints

- All function signatures must have type hints for parameters and return values
- Use `Optional[X]` from `typing` or `X | None` (Python 3.10+ style)
- Use `UUID` type from `uuid` module for IDs
- Use `Decimal` for monetary amounts (not `float`)

---

## Money Amounts

- Always use `Decimal` for monetary values in Python code
- Always use `NUMERIC(20,2)` in PostgreSQL
- Never use `float` for money
- Pydantic schema fields for amounts: use `Decimal` with constraints

---

## Import Order

Follow isort conventions:
1. Standard library
2. Third-party
3. Internal (`app/...`)

---

## File Length

- Keep files under 300 lines
- If a file grows beyond that, split into focused sub-modules

---

## Tests

- Test file mirrors source: `tests/services/test_transfer_service.py` for `app/services/transfer.py`
- Use `pytest` fixtures for DB session, test user, test wallet
- No production business logic in test files
- Assert on meaningful outcomes: transaction status, balance change, audit log creation
