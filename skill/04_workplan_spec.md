# BackendSmith Workplan Specification

## Purpose

The workplan is the persistent memory system for every backend project.

It exists to ensure that:
- project understanding is not lost between sessions
- architectural decisions remain consistent
- implementation does not drift from agreed direction
- any AI agent can resume work without confusion
- context does not depend on fragile conversation history

The workplan is mandatory for all BackendSmith-driven projects.

---

## Core Principle

Conversation is temporary.
The workplan is the source of truth.

All important knowledge must be written into the workplan.

---

## Workplan Folder Structure

Every project must contain a `workplan/` folder at the root level.

```text
workplan/
  00_agent_directive.md
  01_project_summary.md
  02_discovery_answers.md
  03_architecture_decisions.md
  04_stack_and_infra.md
  05_api_integrations.md
  06_data_and_schema_strategy.md
  07_security_and_risk.md
  08_performance_and_scaling.md
  09_coding_standards.md
  10_testing_strategy.md
  11_component_index.md
  12_progress_tracker.md
  13_open_questions.md
  14_runbook_notes.md
  components/
```

# BackendSmith Workplan Files Reference

## File Responsibilities

### 00_agent_directive.md
Contains:
- project intent
- strict instructions for any AI agent
- non-negotiable rules
- how to behave in this project

---

### 01_project_summary.md
Contains:
- product description
- actors
- core flows
- business rules
- system boundaries
- assumptions made

---

### 02_discovery_answers.md
Contains:
- all answers to discovery questions
- user preferences
- clarified constraints
- decisions made during questioning

---

### 03_architecture_decisions.md
Contains:
- chosen architecture style
- system boundaries
- module interaction patterns
- reasoning behind decisions

---

### 04_stack_and_infra.md
Contains:
- language
- framework
- database
- queue system
- caching strategy
- deployment environment
- containerization decisions

---

### 05_api_integrations.md
Contains:
- all external services used
- chosen providers
- documentation links
- auth mechanisms
- rate limits
- webhook behavior
- retry strategy
- integration notes

---

### 06_data_and_schema_strategy.md
Contains:
- data modeling approach
- key entities
- relationships
- transaction handling approach
- indexing strategy
- constraints
- pagination rules

---

### 07_security_and_risk.md
Contains:
- identified risks
- auth strategy
- permission model
- rate limiting decisions
- audit logging approach
- sensitive operations
- abuse prevention

---

### 08_performance_and_scaling.md
Contains:
- expected scale
- performance risks
- async decisions
- queue usage
- caching decisions
- query strategy
- N+1 prevention approach

---

### 09_coding_standards.md
Contains:
- code structure rules
- naming conventions
- documentation rules
- layering rules
- error handling conventions

---

### 10_testing_strategy.md
Contains:
- types of tests required
- critical flows to test
- test structure
- coverage expectations

---

### 11_component_index.md
Contains:
- list of all components
- short description of each
- ownership boundaries
- dependency overview

---

### 12_progress_tracker.md
Tracks:
- status (not_started, planning, in_progress, blocked, complete)
- last updated time
- current focus
- next steps

---

### 13_open_questions.md
Contains:
- unresolved decisions
- unclear requirements
- pending user input
- risks needing clarification

---

### 14_runbook_notes.md
Contains:
- operational notes
- deployment tips
- debugging guidance
- recovery procedures
- environment setup notes

---

## Components Folder

Each backend component must have its own file:
workplan/components/{component_name}.md

Examples:
- auth.md
- users.md
- wallets.md
- transactions.md
- ledger.md
- webhooks.md
- payouts.md

---

## Component File Requirements

Each file must include:
- purpose
- scope
- responsibilities
- dependencies
- related models
- related endpoints
- business rules
- security considerations
- performance considerations
- testing expectations
- status
- pending tasks
- completion notes

---

## Workplan Rules

### Update Rules
- after discovery
- after major decisions
- after component completion
- when risks change
- when assumptions change
- when integrations evolve

### Quality Rules
- clear and structured
- concise
- no fluff
- no repetition
- aligned with implementation

---

## Resuming Work

1. Read 00_agent_directive.md
2. Read 01_project_summary.md
3. Review decisions
4. Check 12_progress_tracker.md
5. Continue

---

## Final Rule

If knowledge is not in the workplan, it does not exist.
