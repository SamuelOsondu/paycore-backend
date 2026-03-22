# Component Index — PayCore

## Overview

PayCore is broken into 16 components.
Each has a dedicated file in `workplan/components/`.

---

## Component List

| # | Component | File | Primary Domain | Depends On |
|---|---|---|---|---|
| 1 | Auth | auth.md | Registration, login, JWT, refresh tokens | users |
| 2 | Users | users.md | User model, profile, role management | — |
| 3 | KYC | kyc.md | KYC tiers, submission, S3 doc upload, admin review | users, S3 |
| 4 | Wallets | wallets.md | Wallet model, balance, statement | users, ledger |
| 5 | Ledger | ledger.md | Double-entry accounting engine | wallets, transactions |
| 6 | Transactions | transactions.md | Transaction records, state machine | wallets |
| 7 | Transfers | transfers.md | Internal wallet-to-wallet money movement | wallets, ledger, transactions, fraud |
| 8 | Merchants | merchants.md | Merchant profiles, API keys, webhook config | users, wallets |
| 9 | Merchant Payments | merchant_payments.md | User-to-merchant payment flow | wallets, ledger, transactions, merchants, fraud |
| 10 | Paystack | paystack.md | Paystack client, funding init, verification, webhook ingestion | transactions, wallets, ledger |
| 11 | Withdrawals | withdrawals.md | Bank accounts, withdrawal request, payout processing | wallets, ledger, transactions, paystack |
| 12 | Outgoing Webhooks | outgoing_webhooks.md | Merchant webhook delivery with retries | merchants, transactions |
| 13 | Fraud | fraud.md | Risk checks, KYC limits, daily volume enforcement | wallets, transactions, users |
| 14 | Audit | audit.md | Audit trail logging and retrieval | all components |
| 15 | Workers | workers.md | Celery task definitions and scheduling | withdrawals, outgoing_webhooks, paystack |
| 16 | Admin | admin.md | Admin KYC review, transaction monitoring, reconciliation | kyc, transactions, audit, fraud |

---

## Recommended Implementation Order

Build foundation-up to avoid forward dependencies:

1. Users (model foundation)
2. Auth (login/JWT, depends on users)
3. Wallets (depends on users)
4. Transactions (depends on wallets)
5. Ledger (depends on transactions + wallets)
6. Fraud (depends on transactions + wallets)
7. KYC (depends on users, S3)
8. Transfers (depends on wallets + ledger + transactions + fraud)
9. Merchants (depends on users + wallets)
10. Merchant Payments (depends on merchants + transfers foundation)
11. Paystack (depends on transactions + wallets + ledger)
12. Withdrawals (depends on paystack + wallets + ledger)
13. Outgoing Webhooks (depends on merchants + transactions)
14. Audit (depends on all — wire in last)
15. Workers (Celery task wrappers — wire after services exist)
16. Admin (depends on everything — build last)
