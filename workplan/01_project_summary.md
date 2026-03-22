# Project Summary — PayCore

## Product Description

PayCore is a digital wallet and merchant payment backend API.
It models the internal infrastructure behind platforms like PalmPay, OPay, or Moniepoint wallets.

Users fund wallets from outside the platform via Paystack, move money internally between wallets,
pay merchants, and withdraw to bank accounts. Merchants receive payments, webhooks, and use API keys
for integration. Admins review KYC, monitor transactions, and can flag suspicious activity.

---

## Actors

| Actor | Description |
|---|---|
| User | Registers, completes KYC, manages wallet, transfers money, pays merchants, withdraws |
| Merchant | Receives wallet payments, uses API keys, receives webhooks, views payment history |
| Admin | Reviews KYC, approves/rejects, monitors transactions, views audit logs, flags activity |

---

## Core Flows

### Flow 1: Wallet Funding (Inflow)
1. User initiates wallet funding
2. System calls Paystack `initialize` → returns payment URL
3. Transaction record created as `pending`
4. Paystack sends webhook on payment success
5. System verifies webhook signature + idempotency
6. System credits wallet balance (in transaction with ledger entry)
7. Transaction status → `completed`
8. Audit log written

### Flow 2: Wallet-to-Wallet Transfer (Internal)
1. Sender initiates transfer with recipient ID and amount
2. System validates sender balance ≥ amount
3. System checks sender KYC tier limits
4. System runs fraud checks (daily volume, frequency)
5. DB transaction: debit sender ledger, credit recipient ledger, update both balances, create transaction record
6. Transaction status → `completed`
7. Audit log written

### Flow 3: Merchant Payment (Internal)
1. User initiates payment to merchant (by merchant ID or reference)
2. System validates user wallet and merchant existence
3. System checks KYC and fraud rules
4. DB transaction: debit user wallet, credit merchant wallet, ledger entries, transaction record
5. Transaction → `completed`
6. Merchant webhook queued in Celery
7. Audit log written

### Flow 4: Withdrawal to Bank Account (Outflow)
1. User adds and optionally verifies bank account
2. User initiates withdrawal with amount
3. System validates KYC tier (must be Tier 2 for withdrawal), balance, and limits
4. System creates pending withdrawal transaction
5. Celery worker picks up job
6. Worker calls Paystack Transfer API (sandbox)
7. On success webhook: ledger debit finalized, transaction → `completed`
8. On failure: transaction → `failed`, balance unfrozen, reversal if needed
9. Audit log written at each stage

### Flow 5: KYC Submission and Approval
1. User submits KYC details and uploads ID document to S3
2. System stores KYC record with status `pending`
3. Admin reviews submission
4. Admin approves → user KYC tier upgraded
5. Admin rejects → user notified, can resubmit
6. Limits automatically reflect new KYC tier
7. Audit log written for submission and admin decision

---

## Business Rules

- A user starts at KYC Tier 0 (email only) with lowest limits
- KYC Tier 1: basic profile + phone → moderate limits
- KYC Tier 2: ID document + admin approval → full limits including withdrawal
- Wallet balance must never go negative
- Only one active wallet per user (NGN, MVP)
- Merchants get a wallet upon merchant profile creation
- Merchant API keys are hashed before storage; shown once on creation
- Transfers below the sender's daily limit and KYC tier limit are allowed
- Withdrawals require KYC Tier 2
- Paystack webhook must be signature-verified before any credit action

---

## System Boundaries

**Inside platform:**
- Wallet balances and ledger
- User-to-user transfers
- User-to-merchant payments
- KYC management
- Audit trails
- Merchant API keys and webhooks

**External rail (Paystack):**
- Wallet funding initialization
- Payment verification
- Payout/withdrawal processing (sandbox)

**Not in scope (MVP):**
- Card issuing
- Multi-currency
- Interbank settlement complexity
- Loans or investments
- Full compliance engine

---

## Assumptions

- Single currency: NGN throughout MVP
- Paystack sandbox credentials available via environment variables
- S3 bucket configured for KYC document storage
- One wallet per user; one wallet per merchant
- Admin users are seeded or promoted manually (no admin self-registration)
- Email notifications are out of MVP scope
