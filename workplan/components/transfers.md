# Transfers Component

## Purpose
Handles internal wallet-to-wallet money transfers between platform users.
This is the core internal money movement flow — no external APIs involved.

## Scope
### In Scope
- User-to-user transfer initiation
- Balance validation
- KYC tier limit checks
- Fraud rule enforcement (synchronous)
- Atomic DB transaction: debit sender, credit recipient, ledger entries, transaction record
- Transfer status tracking

### Out of Scope
- User-to-merchant payments → Merchant Payments component
- Withdrawal to bank → Withdrawals component
- External payment rail → Paystack component

## Responsibilities
- `TransferService`: initiate_transfer — orchestrates the full transfer flow
- Validates sender identity and wallet ownership
- Calls FraudService checks before executing
- Executes atomic DB transaction for balance update + ledger write
- Creates transaction record

## Dependencies
- Wallets component (WalletRepository, lock_for_update)
- Ledger component (LedgerService.post_double_entry)
- Transactions component (TransactionRepository)
- Fraud component (FraudService.check_transfer)
- Users component (resolve recipient by ID or email)
- Audit component (emit transfer.completed or transfer.failed)

## Related Models
- `Transaction`
- `LedgerEntry`
- `Wallet`

## Related Endpoints
- `POST /api/v1/transfers` — initiate transfer

## Business Rules
- Sender cannot transfer to themselves
- Sender wallet balance must be >= amount (checked with locked row)
- Amount must be > 0 and <= single-transaction limit for sender's KYC tier
- Daily transfer volume check: total outgoing transfers today must not exceed KYC tier daily limit
- Duplicate detection: same sender + recipient + amount within 60 seconds → reject
- Recipient must be an active user with an active wallet
- Transfer is atomic: either all DB writes succeed or none

## Security Considerations
- Sender resolved from JWT — never from request body (prevent sender spoofing)
- Row-level lock on sender wallet before balance check and debit
- Row-level lock on recipient wallet before credit (lock both in consistent order by wallet_id to prevent deadlock)
- Idempotency key: optional client-provided or generated from sender+recipient+amount+timestamp

## Performance Considerations
- Lock both wallets using consistent ordering (smaller UUID first) to prevent deadlock
- Transaction is fast (pure DB) — no external API calls
- No async needed; runs synchronously within request

## Reliability Considerations
- Full atomicity: SQLAlchemy `async with session.begin()` wraps all writes
- If any step fails, rollback — no partial state
- Idempotency key prevents retry double-spend

## Testing Expectations
- Integration: transfer completes, both balances updated correctly, two ledger entries exist
- Integration: insufficient balance → rejected, no DB changes
- Integration: KYC tier limit exceeded → rejected
- Integration: duplicate transfer within 60s → rejected
- Integration: concurrent transfers from same wallet (race condition test)
- API: unauthenticated access rejected
- API: sender cannot spoof source wallet

## Implementation Notes
- Wallet locking order: always lock the wallet with the lexicographically smaller UUID first
- `TransferService.initiate_transfer(sender_user_id, recipient_user_id, amount, idempotency_key)`
- Flow: resolve wallets → check idempotency → fraud checks → lock wallets → check balance → begin DB transaction → write ledger → update balances → create transaction → commit → emit audit

## Status
complete

## Pending Tasks
- None

## Completion Notes
- `app/schemas/transfer.py` — `TransferRequest` (recipient_user_id XOR recipient_email, amount gt=0 decimal_places=2, optional idempotency_key; model_validator enforces exactly one recipient field); `TransferOut` (mirrors TransactionOut sans extra_data; use_enum_values=True)
- `app/services/transfer.py` — `TransferService.initiate_transfer`: resolves sender wallet → resolves recipient user (by ID or email) → self-transfer guard → recipient wallet resolution → idempotency early-return → FraudService.check_transfer (synchronous) → lock both wallets in UUID string order (deadlock prevention) → balance check on locked row → create transaction (COMPLETED) + LedgerService.post_double_entry + WalletRepository.update_balance × 2 → session.commit() → FraudService.maybe_flag_rapid_transfers (post-commit, async, non-blocking)
- `app/api/v1/transfers.py` — `POST /transfers`: sender resolved from JWT only; calls TransferService.initiate_transfer; returns 201 with TransferOut
- `app/api/v1/router.py` — transfers router included
- `tests/api/test_transfer_api.py` — 16 API tests: unauthenticated (401), both/no recipient fields (422), zero/negative amount (422), self-transfer (422 SELF_TRANSFER), tier-0 blocked (403 KYC_TIER_INSUFFICIENT), amount exceeds tier-1 single limit (403), recipient not found (404), inactive recipient (404), inactive recipient wallet (403), inactive sender wallet (403), insufficient balance (422 + no DB changes), success by user_id (201 + balance delta + response fields), success by email (201), two ledger entries with correct wallet IDs and amounts (DEBIT/CREDIT), idempotency returns same transaction (same ID + no double debit), duplicate within 60s window (429 DUPLICATE_TRANSFER), running balance across three sequential transfers
