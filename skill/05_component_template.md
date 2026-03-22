# BackendSmith Component Template Specification

## Purpose

This document defines the standard structure for every file inside:

```text
workplan/components/
````

Each component file must follow a consistent format so that:

* the project remains easy to understand
* different AI agents can continue work without confusion
* component responsibilities stay clear
* implementation does not drift
* security, performance, and testing are considered at component level

This template is mandatory for all components.

---

## Naming Rule

Each component file should be named after the responsibility it owns.

Examples:

* auth.md
* users.md
* profiles.md
* wallets.md
* transactions.md
* ledger.md
* kyc.md
* payouts.md
* webhooks.md
* notifications.md
* admin.md

Use clear singular or plural names consistently across the project.

---

## Required Structure for Every Component File

Each component file must contain the following sections in this order.

---

## 1. Component Name

State the exact name of the component.

Example:

```md
# Wallets Component
```

---

## 2. Purpose

Explain what the component exists to do in the system.

This should describe:

* the role of the component
* the business value it supports
* how it fits into the larger backend

Keep it clear and direct.

---

## 3. Scope

Define what is included and what is not included in this component.

This section should answer:

* what this component owns
* what this component does not own
* what neighboring components handle instead

This prevents overlap and confusion.

---

## 4. Responsibilities

List the key responsibilities of the component.

Examples:

* create and validate wallet records
* expose wallet balance and statement endpoints
* enforce wallet ownership rules
* coordinate ledger-backed balance reads

Responsibilities must be concrete, not vague.

---

## 5. Dependencies

List the internal and external dependencies this component relies on.

This may include:

* other components
* database models
* external APIs
* queues
* storage services
* auth mechanisms

This section must clarify what this component needs in order to work.

---

## 6. Related Models

List the database models or entities primarily owned or touched by this component.

Examples:

* User
* Wallet
* WalletTransaction
* LedgerEntry

If ownership is shared, mention how the relationship works.

---

## 7. Related Endpoints

List the API endpoints this component is expected to expose or participate in.

Examples:

* `POST /wallets/fund`
* `GET /wallets/me`
* `GET /wallets/me/transactions`

If endpoints are internal only, say so clearly.

---

## 8. Business Rules

Describe the domain rules this component must enforce.

Examples:

* a user may only view their own wallet unless elevated permission exists
* wallet balance must never be updated outside approved balance-handling rules
* funding is incomplete until external confirmation succeeds
* only approved KYC levels may withdraw above specified thresholds

This is one of the most important sections.

---

## 9. Security Considerations

Describe security concerns specific to this component.

Examples:

* ownership checks
* permission boundaries
* rate limiting
* idempotency needs
* verification of external calls
* sensitive data masking
* fraud-sensitive actions

If the component handles money, identity, admin power, or external side effects, be explicit.

---

## 10. Performance Considerations

Describe the likely performance concerns for this component.

Examples:

* pagination required for list endpoints
* avoid N+1 when loading related records
* heavy export jobs should be backgrounded
* large query filters should be indexed
* expensive external calls should not block user requests

This section should be practical, not theoretical.

---

## 11. Reliability Considerations

Describe failure and recovery concerns for this component.

Examples:

* webhook handling must be idempotent
* transaction creation must be atomic
* retries may be required for external delivery
* partial failure must not create inconsistent state

If applicable, note:

* rollback expectations
* reconciliation needs
* duplicate-request protections

---

## 12. Testing Expectations

List the kinds of tests this component needs.

Examples:

* unit tests for core business rules
* integration tests for transaction flows
* permission tests for protected endpoints
* failure-path tests for provider errors
* concurrency-sensitive tests for duplicate requests

Tests should reflect the real risk of the component.

---

## 13. Implementation Notes

Add practical implementation guidance for this component.

This may include:

* service layer expectations
* repository expectations
* recommended query patterns
* background job boundaries
* serialization notes
* integration wrapper notes

This section is where professional execution detail lives.

---

## 14. Status

Each component file must contain a current status.

Allowed values:

* not_started
* planning
* in_progress
* blocked
* complete

Example:

```md
## Status

in_progress
```

---

## 15. Pending Tasks

List remaining tasks for the component.

These must be actionable and specific.

Examples:

* add idempotency support for funding endpoint
* create integration tests for duplicate webhook delivery
* implement pagination for transaction history
* add admin-only payout review endpoint

Do not use vague tasks like:

* improve logic
* fix stuff
* make robust

---

## 16. Completion Notes

Once a component is complete, record what was finished and any important follow-up notes.

This should include:

* what has been implemented
* any limitations left for later
* any known tradeoffs
* any follow-up work outside MVP

This makes future continuation safer.

---

## Recommended Writing Standard

Component files must be:

* direct
* structured
* implementation-oriented
* free from fluff
* easy for any future agent to follow
* updated as the project evolves

They should not read like essays.
They should read like precise engineering notes.

---

## Example Skeleton

Use the following skeleton for every new component file.

```
# {Component Name} Component

## Purpose
{What this component exists to do}

## Scope
### In Scope
- ...
### Out of Scope
- ...

## Responsibilities
- ...
- ...

## Dependencies
- ...
- ...

## Related Models
- ...
- ...

## Related Endpoints
- ...
- ...

## Business Rules
- ...
- ...

## Security Considerations
- ...
- ...

## Performance Considerations
- ...
- ...

## Reliability Considerations
- ...
- ...

## Testing Expectations
- ...
- ...

## Implementation Notes
- ...
- ...

## Status
not_started

## Pending Tasks
- ...

## Completion Notes
- None yet

```

---

## Final Rule

A component file is not just documentation.
It is an execution contract.

BackendSmith must treat each component file as the authoritative reference for what that part of the system is meant to do, how it should be built, and how its progress should be tracked.

```