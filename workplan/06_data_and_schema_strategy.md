# Data and Schema Strategy — PayCore

## Modeling Approach

- Relational PostgreSQL
- UUID primary keys on all tables (not serial integers) — avoids enumerable IDs, better for distributed future
- `created_at`, `updated_at` on all tables (server_default via DB)
- **Soft deletes** on user-adjacent records — see below
- Financial records (transactions, ledger entries, audit_logs, kyc_submissions) are NEVER deleted or soft-deleted — they are immutable

---

## Soft Delete Strategy

Financial platform decision: records are never truly destroyed.

### Tables with `deleted_at TIMESTAMPTZ NULL`:
- `users` — can be "closed" without data loss
- `wallets` — deactivated wallet keeps its history
- `merchants` — merchant profile can be removed without losing transaction records
- `bank_accounts` — user can remove a saved account

### Tables WITHOUT soft delete (immutable):
- `transactions` — financial record, never deleted
- `ledger_entries` — immutable accounting entries
- `audit_logs` — compliance trail, never deleted
- `kyc_submissions` — regulatory document, never deleted
- `refresh_tokens` — use `is_revoked` flag
- `webhook_deliveries` — tracked via `status` field

### Implementation:
- `SoftDeleteMixin` adds `deleted_at: Mapped[Optional[datetime]]`
- `BaseRepository.soft_delete(id)` sets `deleted_at = now()`
- All repository read methods explicitly filter `WHERE deleted_at IS NULL`
- Hard deletes are forbidden at the repository layer for soft-delete tables

---

## Core Entities

### users
| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| email | VARCHAR UNIQUE NOT NULL | |
| phone | VARCHAR UNIQUE | nullable until Tier 1 |
| hashed_password | VARCHAR NOT NULL | |
| full_name | VARCHAR NOT NULL | |
| role | ENUM('user','merchant','admin') | default 'user' |
| kyc_tier | SMALLINT | default 0 |
| is_active | BOOLEAN | default true |
| is_email_verified | BOOLEAN | default false |
| created_at | TIMESTAMPTZ | |
| updated_at | TIMESTAMPTZ | |

Index: `email`, `phone`, `role`

---

### wallets
| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| user_id | UUID FK → users | UNIQUE (one wallet per user) |
| currency | VARCHAR(3) | default 'NGN' |
| balance | NUMERIC(20,2) | NOT NULL, default 0, CHECK >= 0 |
| is_active | BOOLEAN | default true |
| created_at | TIMESTAMPTZ | |
| updated_at | TIMESTAMPTZ | |

Index: `user_id`
Constraint: `balance >= 0` enforced at DB level

**Balance update rule:** Only updated inside a DB transaction alongside ledger entry writes.

---

### transactions
| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| reference | VARCHAR UNIQUE NOT NULL | Platform-generated UUID |
| type | ENUM | funding, transfer, merchant_payment, withdrawal, reversal |
| status | ENUM | pending, processing, completed, failed, reversed |
| amount | NUMERIC(20,2) NOT NULL | |
| currency | VARCHAR(3) | default 'NGN' |
| source_wallet_id | UUID FK → wallets | nullable (e.g. external funding) |
| destination_wallet_id | UUID FK → wallets | nullable (e.g. withdrawal) |
| initiated_by_user_id | UUID FK → users | |
| provider_reference | VARCHAR | Paystack reference |
| idempotency_key | VARCHAR UNIQUE | caller-provided or system-generated |
| metadata | JSONB | extra data (e.g. bank details, merchant info) |
| failure_reason | TEXT | |
| created_at | TIMESTAMPTZ | |
| updated_at | TIMESTAMPTZ | |

Index: `reference`, `provider_reference`, `idempotency_key`, `source_wallet_id`, `destination_wallet_id`, `status`, `type`, `created_at`

---

### ledger_entries
| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| transaction_id | UUID FK → transactions | |
| wallet_id | UUID FK → wallets | |
| entry_type | ENUM('debit','credit') | |
| amount | NUMERIC(20,2) NOT NULL | |
| balance_after | NUMERIC(20,2) NOT NULL | snapshot of wallet balance post-entry |
| created_at | TIMESTAMPTZ | |

Index: `transaction_id`, `wallet_id`, `created_at`
No updates or deletes ever on this table.

---

### kyc_submissions
| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| user_id | UUID FK → users | |
| tier_requested | SMALLINT | 1 or 2 |
| status | ENUM | pending, approved, rejected |
| full_name | VARCHAR | |
| date_of_birth | DATE | |
| phone | VARCHAR | |
| id_type | VARCHAR | e.g. 'national_id', 'passport' |
| document_key | VARCHAR | S3 object key |
| rejection_reason | TEXT | |
| reviewed_by | UUID FK → users | admin user id |
| reviewed_at | TIMESTAMPTZ | |
| created_at | TIMESTAMPTZ | |
| updated_at | TIMESTAMPTZ | |

Index: `user_id`, `status`

---

### merchants
| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| user_id | UUID FK → users | UNIQUE |
| business_name | VARCHAR NOT NULL | |
| wallet_id | UUID FK → wallets | created on merchant creation |
| api_key_hash | VARCHAR NOT NULL | bcrypt hash |
| api_key_prefix | VARCHAR(8) | shown in list for identification |
| webhook_url | VARCHAR | optional |
| webhook_secret | VARCHAR | HMAC secret for outgoing webhook signing |
| is_active | BOOLEAN | default true |
| created_at | TIMESTAMPTZ | |
| updated_at | TIMESTAMPTZ | |

Index: `user_id`, `api_key_prefix`

---

### bank_accounts
| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| user_id | UUID FK → users | |
| bank_code | VARCHAR NOT NULL | Paystack bank code |
| account_number | VARCHAR NOT NULL | |
| account_name | VARCHAR | from Paystack verification or user-entered |
| paystack_recipient_code | VARCHAR | from Paystack transferrecipient creation |
| is_default | BOOLEAN | default false |
| is_verified | BOOLEAN | default false |
| created_at | TIMESTAMPTZ | |

Index: `user_id`
Unique constraint: `(user_id, account_number, bank_code)`

---

### refresh_tokens
| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| user_id | UUID FK → users | |
| token_hash | VARCHAR NOT NULL | |
| expires_at | TIMESTAMPTZ | |
| is_revoked | BOOLEAN | default false |
| created_at | TIMESTAMPTZ | |

Index: `user_id`, `token_hash`

---

### audit_logs
| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| actor_id | UUID | user or system performing the action |
| actor_type | ENUM('user','system','admin') | |
| action | VARCHAR NOT NULL | e.g. 'kyc.submitted', 'transfer.completed' |
| target_type | VARCHAR | e.g. 'transaction', 'kyc_submission' |
| target_id | UUID | |
| metadata | JSONB | |
| ip_address | VARCHAR | |
| created_at | TIMESTAMPTZ | |

Index: `actor_id`, `action`, `target_id`, `created_at`
Never deleted.

---

### webhook_deliveries
| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| merchant_id | UUID FK → merchants | |
| transaction_id | UUID FK → transactions | |
| event_type | VARCHAR NOT NULL | |
| payload | JSONB NOT NULL | |
| status | ENUM | pending, delivered, failed |
| attempts | SMALLINT | default 0 |
| next_retry_at | TIMESTAMPTZ | |
| last_response_code | SMALLINT | |
| last_error | TEXT | |
| created_at | TIMESTAMPTZ | |
| updated_at | TIMESTAMPTZ | |

Index: `merchant_id`, `status`, `next_retry_at`

---

## Transaction Handling

- All money-moving operations: `async with session.begin()` scope
- Row-level locking: `SELECT ... FOR UPDATE` on wallet rows during balance operations
- Prevents race conditions in concurrent transfer/payment requests for the same wallet

---

## Indexing Strategy

- UUID PKs automatically indexed
- All FK columns indexed
- `status` + `type` on transactions (common filter combination)
- `created_at` on transactions and ledger_entries (time-range queries)
- `reference` and `provider_reference` on transactions (lookup by Paystack event)

---

## Pagination

- Offset pagination: `limit` (default 20, max 100) + `offset`
- Applied to: transactions list, ledger entries, audit logs, KYC submissions (admin), webhook deliveries
- Response envelope: `{ data: [...], total: N, limit: N, offset: N }`
