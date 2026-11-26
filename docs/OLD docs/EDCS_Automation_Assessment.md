# Automation Opportunities Assessment

## EDCS Member Management System

**Document ID:** EDCS-AUTO-001  
**Version:** 1.0 — Assessment draft  
**Date:** 15 November 2026  
**Purpose:** Identify practical automation opportunities across the system  
**Audience:** EDCS management, operations, finance, administrators and development team  
**Scope:** All 19 registered permission modules, administration and supporting workflows  
**Deliverable:** Assessment and recommendations only; no application changes or external actions

This assessment distinguishes existing automation from proposed improvements. Findings are based on the current working source, not observation of live staff activity or measurement of production workloads. Benefits are expected outcomes to validate through a pilot; priorities and effort bands are planning recommendations.

### Document control

| Item                             | Value                                                                                                                                                  |
| -------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| System reviewed                  | Django/PostgreSQL EDCS application, including browser behavior, financial services, approvals and scheduled commands.                                  |
| Review basis                     | Models, forms, views, services, permission checks, templates/JavaScript, management commands, migrations, documentation and relevant test definitions. |
| Coverage                         | 26 module/workstream assessments, plus shared workflows, notifications, scheduling, validation, reporting and integrations.                            |
| Status                           | Draft for business and technical review; policy-dependent recommendations require decisions before implementation.                                     |
| Business owner / technical owner | To be assigned by EDCS.                                                                                                                                |

### Contents

1. Executive assessment
2. Method, priorities and existing automation
3. Module-wise opportunities
4. Cross-module workflow automation
5. Approval and notification design
6. Scheduling, integrations and implementation architecture
7. Prioritized delivery roadmap
8. Benefits measurement and acceptance
9. Risks, decisions and next steps
10. Source evidence and coverage

## 1. Executive assessment

### 1.1 Main finding

The system already automates many calculations and integrity checks. Its largest remaining opportunity is to automate coordination around those functions: identifying work that needs attention, preparing validated drafts, routing tasks, notifying the right officer, reconciling results and detecting failed background processing.

The recommended approach is to automate preparation and verification first, then introduce controlled batch processing and external feeds. Retain explicit human authorization where actions change money, employee access, retirement status, officer responsibility or financial policy. Avoid duplicating existing services in separate automation scripts.

### 1.2 Recommended starting portfolio

| Rank | Initiative / opportunity references                                       | Why start here                                                                                                                                                                       | Principal dependency                                                   |
| ---- | ------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------- |
| 1    | Govern automatic interest posting: INT-01, OPS-01                         | The existing unattended command can post directly, while browser posting can require hierarchy approval. Establish one agreed operating policy before increasing automation.         | Finance decision on unattended posting and service authority.          |
| 2    | Reliable jobs and notification delivery: OPS-01, NOT-01                   | Shared run tracking, retry controls and durable notification records support almost every later opportunity.                                                                         | Technical owner and hosting/scheduler design.                          |
| 3    | Approval assignment reminders and aging: APR-01, APR-02                   | Existing sequential approval routing is useful, but officers must inspect the queue. Reminders can reduce avoidable waiting.                                                         | Reliable delivery and agreed reminder/escalation policy.               |
| 4    | Financial exception and reconciliation worklists: TXN-03, INT-02          | Detect omissions and inconsistent operational results before they become month/year-end rework.                                                                                      | Agreed reconciliation totals, tolerances and owners.                   |
| 5    | Guided onboarding and missing-information queues: MBR-01, MBR-02          | Registration, plans, openings and officer assignment are linked, but completion still depends on navigating several modules.                                                         | Defined required onboarding steps and separate action permissions.     |
| 6    | Safer posting submissions: TXN-02                                         | Ordinary member transactions lack the explicit operation-token pattern already present for loan entries. A common duplicate-submission control can reduce accidental double posting. | New idempotency records and request-to-operation mapping.              |
| 7    | Maturity, retirement and responsibility reminders: FIX-01, RET-01, LED-01 | Existing eligibility/date calculations can drive actionable worklists without automatically changing member status or releasing money.                                               | Agreed lead times and responsible teams.                               |
| 8    | Staged opening/transaction imports: OPN-01, TXN-01                        | Repeated financial entry is a strong candidate for batch validation and reconciliation.                                                                                              | Batch staging, external row keys, test fixtures and review controls.   |
| 9    | Scheduled authorized reporting: RPT-01, LAG-01                            | Recurring reports can be prepared consistently and delivered through secure, permission-checked links.                                                                               | Report ownership, scoped background execution and delivery foundation. |
| 10   | Reduce session heartbeat duplication: SES-01                              | The current browser sends frequent small activity requests, potentially from several tabs. Coordination can lower unnecessary traffic.                                               | Browser expiry/regression checks; agreed interval policy.              |

This order favors control foundations and operational bottlenecks. It is not a commitment to implement every item or a claim that these are the largest measured labor costs; production measurements are not available.

### 1.3 Automation boundaries

- A monthly payment agreement is not evidence that money was received. Generate expected-contribution records or drafts, not automatic deposit credits.
- A maturity date is not authorization to withdraw. Prepare eligibility checks and route a request; revalidate at release.
- A zero loan balance is not proof that all interest has been assessed. Prepare a settlement checklist and retain the required confirmation.
- An expected retirement date is planning information. Remind an officer; do not automatically retire a member or change pension eligibility.
- A reminder deadline must not silently approve a request, expand district access or rewrite submitted approver assignments.
- Source/receiving ledger carry-forward snapshots are not new money. Automated transfers must preserve existing financial identities and values.

## 2. Method, priorities and existing automation

### 2.1 Review method and confidence

The assessment follows each operational module from data entry and validation through saving, approval, financial effects, reporting and history. It also examines shared administration, notifications, session behavior and the supplied interest scheduler. Each module section states the current process, residual manual work, suggested improvements, benefits, delivery priority and prerequisites.

**Observed** describes behavior represented in reviewed code. **Proposed** describes a new workflow, data structure or integration. **Conditional** identifies a recommendation that cannot be finalized until business rules or external interfaces are agreed. An absent feature means it was not found in the reviewed application paths; it does not establish that an external system or deployment service does not provide it.

The review did not send notifications, install jobs, run financial posting commands, inspect personal production data, alter application behavior or benchmark operational throughput. Existing test definitions were reviewed as evidence of intended safeguards, not as a new full-system test certification.

### 2.2 Priority, effort and change risk

| Rating                    | Meaning                                                                                                               |
| ------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| P0 — Control prerequisite | Resolve before enabling dependent automation, especially unattended financial processing, access or recovery.         |
| P1 — Early implementation | Practical near-term work with strong operational value and manageable policy changes.                                 |
| P2 — Controlled expansion | Valuable after shared delivery, data and review controls exist.                                                       |
| P3 — Conditional / later  | Depends on external providers, substantial new business rules or proven workload demand.                              |
| Effort S                  | Local enhancement using existing records/services; limited new screens or rules.                                      |
| Effort M                  | Several components, new task/draft records, scheduled processing or a cross-module flow.                              |
| Effort L                  | Batch infrastructure, substantial data model changes or integration across several services.                          |
| Effort XL                 | External integration or broad platform change with material discovery/migration work.                                 |
| Risk Low / Medium / High  | Consequence if incorrect: informational inconvenience; operational/data exposure; or financial/access/history impact. |

Effort is relative engineering scope, not a quotation or elapsed-time estimate. Hosting readiness, provider procurement, data cleanup and business approval can dominate the calendar. Delivery columns below use **priority / effort / risk**. A P0 decision can be small even when its eventual implementation is large.

### 2.3 Automation already present — preserve and reuse

| Area               | Existing behavior                                                                                                                               | Assessment implication                                                                            |
| ------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------- |
| Access             | Action-level permissions, role/direct grant union, assigned districts and administrator restrictions are checked server-side.                   | Jobs, APIs and reports must preserve these checks.                                                |
| Member entry       | Identifier normalization/uniqueness, location derivation, linked selectors, generated short name and expected retirement date.                  | Focus on completion guidance and review queues, not rebuilding calculations.                      |
| Plans/templates    | Totals are calculated; template amounts copy independently into member agreements.                                                              | Avoid live propagation of template edits into existing agreements.                                |
| Deposit accounting | Opening-date-aware balances, dated statements and running totals are derived automatically.                                                     | Use existing calculations for imports/reconciliation; do not maintain a competing balance engine. |
| Children           | Rate selection uses age and date; entries retain rate snapshots.                                                                                | Prepare transition alerts while preserving posted/saved history.                                  |
| Interest           | Quarterly minimum month-end calculations, transition exclusions, annual posting and carry-forward; command processes member and child accounts. | Add oversight, safe approval policy and exceptions to an existing engine.                         |
| Fix Deposit        | Loan/withdrawal eligibility, limits, chronology, repayment checks, formal settlement and atomic release.                                        | Automate preparation and notification while retaining release/service checks.                     |
| Ledgers/transfers  | Initial ledger creation, saved-value events, transfer snapshots and linked receiving pages.                                                     | Add readiness checks and handover tasks; do not duplicate postings.                               |
| Approvals          | Configurable sequential levels, assignment snapshots, deduplication and final revalidation/application.                                         | Add task delivery, aging and controlled recovery rather than a second approval engine.            |
| Email              | New-user welcome message after commit; blank email skipped and failure warned.                                                                  | Add durable asynchronous delivery/retries and broader event notifications.                        |
| Scheduling         | Optional hourly cron entry with `flock`, log output and repeat-safe annual posting.                                                             | Verify deployment, add run records/alerts and define service authority.                           |
| Reporting          | Dashboard, filtered statements, balances, Lager activity and selected CSV/print outputs.                                                        | Automate recurring preparation and exception summaries, with scoped delivery.                     |
| Sessions           | Idle monitoring, trusted activity, cross-tab state sharing and throttled keepalive.                                                             | Reduce redundant senders while preserving genuine inactivity expiry.                              |

## 3. Module-wise opportunities

All opportunity rows in this section are proposals. Existing-process statements are source observations. Evidence keys E01–E15 refer to the source register in Section 10. Each section identifies a proposed operational owner; these are responsibilities to assign, not people already configured in the application.

### 3.1 User accounts, roles and district access

**Current process:** An active superuser creates or edits users, assigns roles/direct permissions and districts, records EPF/zone and resets passwords. Account/role changes are logged. Active ledger responsibility restricts officer deactivation or loss of required access. A welcome email is attempted for a new account with email.

**Manual work:** Repeated permission selection, checking whether an account has useful district access, access reviews and coordinating an officer's departure. **Owner:** System administrator and operations supervisor. **Evidence:** E01, E02.

| ID / opportunity                       | Recommended automation solution                                                                                                                                                                                                           | Expected benefit                                                    | Delivery      |
| -------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------- | ------------- |
| IAM-01 Role-based onboarding checklist | Offer approved role/district presets as editable proposals; flag no-district accounts, incompatible officer assignments and unneeded direct grants before save. Administrator confirms the exact effective changes.                       | Faster setup; fewer empty or excessive access assignments.          | P1 / M / High |
| IAM-02 Access review and expiry tasks  | Schedule an administrator-only review of active accounts and grants; add approved temporary-access expiry fields and an access-change diff. Expiry checks must handle active ledgers and pending requests as exceptions, not strand them. | Less forgotten access and fewer manual review spreadsheets.         | P2 / M / High |
| IAM-03 Offboarding case                | Automatically gather an officer's active ledgers, pending approvals and any later scheduled-report subscriptions; route reassignment tasks and show blockers before administrator deactivation. Preserve permanent identity/history.      | A traceable departure process with fewer stranded responsibilities. | P1 / M / High |

**Prerequisites / limits:** Presets never create administrator privileges or districts without confirmation. Account/role administration is outside current business hierarchy scope; any new approval for access changes requires a separate explicit design. Dependencies: LED-01, APR-02 and NOT-01.

### 3.2 Sign-in, session activity and form continuity

**Current process:** Session expiry defaults to 15 minutes; genuine input across tabs preserves activity. Each active tab can send keepalives roughly every 30 seconds. Local storage shares activity/logout but does not elect one sender. **Manual work:** Recovering unsaved long-form entry after expiry and interpreting repetitive activity requests. **Owner:** Technical owner. **Evidence:** E03.

| ID / opportunity             | Recommended automation solution                                                                                                                                                                                                                                     | Expected benefit                                                      | Delivery        |
| ---------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------- | --------------- |
| SES-01 Coordinated heartbeat | Elect one short-lived sender per authenticated browser session, with takeover when it closes. Send only after trusted activity; optionally lengthen the interval within the expiry budget. Keep server expiry authoritative and test browser sleep/storage failure. | Fewer network requests/session writes, especially with multiple tabs. | P1 / M / Medium |
| SES-02 Safe draft continuity | Add an explicit expiry warning and opt-in, per-user server-side form drafts for long non-password forms, with retention/cleanup and revalidation on restore. A restored draft is not a saved business record.                                                       | Less retyping after interruption; clearer session experience.         | P2 / M / Medium |

**Prerequisites / limits:** Preserve logout propagation and idle expiry. Do not store sensitive drafts in unprotected shared-browser storage or restore them for a different user. Draft/heartbeat traffic must be measured so the improvement does not add more traffic than it removes.

### 3.3 Member registration and profiles

**Current process:** Staff enter member identity/contact data, select a department/lager and eligible officer, and receive automatically generated location/name/retirement fields and original ledger creation. Registration can continue to a plan when separately authorized. **Manual work:** Finding incomplete onboarding and re-entering reliable source data. **Owner:** Registration supervisor. **Evidence:** E04, E05, E11.

| ID / opportunity                      | Recommended automation solution                                                                                                                                                                                                         | Expected benefit                                                 | Delivery        |
| ------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------- | --------------- |
| MBR-01 Guided onboarding case         | After registration, create a checklist for required next steps: plan, imported opening where relevant, officer completion and optional child services. Route tasks only to users permitted for each module; resume from saved progress. | Fewer incomplete cases and fewer navigation handoffs.            | P1 / M / Medium |
| MBR-02 Data-quality worklist          | Run advisory checks for missing birth dates, invalid contact formats in legacy data and suspiciously similar identities. Exact identifiers remain authoritative; near-name matches require human review and never automatic merging.    | Earlier discovery of bad data without damaging valid identities. | P1 / S / Medium |
| MBR-03 Controlled registration intake | Introduce a staged spreadsheet intake or future HR feed with schema checks, normalized identifiers, location/officer matching, duplicates and previewed row errors; commit approved rows through registration services.                 | Less repetitive data entry and fewer transcription errors.       | P2 / L / High   |

**Prerequisites / limits:** Opening balances are optional only when the business says so; do not fill unknown balances with invented zeros. Imports must preserve identity and create an original ledger correctly. Separate permissions still apply to each onboarding task.

### 3.4 Retirement and pension processing

**Current process:** Expected retirement is derived at age 60; actual retirement/pension dates are entered by staff. Processing retirement changes status while retaining history and protecting posted-interest eligibility. **Manual work:** Monitoring upcoming retirements and preparing related reviews. **Owner:** Membership/finance supervisor. **Evidence:** E04, E08.

| ID / opportunity                 | Recommended automation solution                                                                                                                                                                                         | Expected benefit                                                  | Delivery      |
| -------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------- | ------------- |
| RET-01 Retirement planning queue | Generate lead-time reminders and a district-scoped upcoming-retirement list using known birth dates, with a missing-date exception list. Proposed 90/60/30-day reminders are examples to configure, not existing rules. | Earlier preparation and fewer missed cases.                       | P1 / S / Low  |
| RET-02 Retirement review pack    | Prepare a preview of actual date, member status, affected pension quarter, plans, loan obligations and closed-year conflicts; route the existing retirement edit through its configured hierarchy.                      | Less manual cross-checking and fewer interest-eligibility errors. | P2 / M / High |

**Prerequisites / limits:** Never automatically set actual pension date, retire a member or close financial accounts when the expected date arrives. Keep early/scheduled retirement distinct and retain leap-day/date rules.

### 3.5 District master and province data

**Current process:** Districts store full/short province names and active status; district access limits operational choices. Administrators alone create districts. Linked data protects unsafe deletion/moves. **Manual work:** Reviewing province abbreviations, duplicates and effects of deactivation. **Owner:** Master-data administrator. **Evidence:** E01, E05.

| ID / opportunity                 | Recommended automation solution                                                                                                                                                                                                        | Expected benefit                                                | Delivery        |
| -------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------- | --------------- |
| DST-01 Master-data impact review | Prepare a dependency count and advisory consistency report before district/province edits or deactivation; propose standardized abbreviations for confirmation. Route approved existing-record edits through the applicable hierarchy. | More consistent labels and fewer unusable dependent selections. | P2 / S / Medium |

**Prerequisites / limits:** Do not automatically deactivate offices or members, or expand user district access. Full province names remain separately stored.

### 3.6 Zonal offices

**Current process:** Offices belong to districts and expose linked departments, lagers and members. Officers have zone assignments; protected relationships constrain moves. **Manual work:** Identifying zones without eligible officers and preparing new-zone setup. **Owner:** Zonal operations supervisor. **Evidence:** E01, E05, E11.

| ID / opportunity              | Recommended automation solution                                                                                                                                                 | Expected benefit                             | Delivery        |
| ----------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------- | --------------- |
| ZON-01 Zone readiness monitor | Flag active zones with no eligible ledger officer, no usable department/lager, or incomplete contact data; create a setup task with authorized links.                           | Prevent blocked registrations and transfers. | P1 / S / Low    |
| ZON-02 Guided zone setup      | Create a coordinated draft checklist for office, subdivisions, departments, lagers and officer assignment; validate each step and require explicit confirmation of new records. | Faster consistent setup with less re-entry.  | P2 / M / Medium |

**Prerequisites / limits:** Officer eligibility includes active account, EPF, matching zone and district access. Do not invent or silently assign responsibility.

### 3.7 Subdivisions

**Current process:** Optional subdivisions belong to zones and must agree with selected member/department relationships. **Manual work:** Reconciling inconsistent legacy labels and checking impact before deactivation. **Owner:** Master-data administrator. **Evidence:** E05.

| ID / opportunity                      | Recommended automation solution                                                                                                                                                                                      | Expected benefit                               | Delivery        |
| ------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------- | --------------- |
| SUB-01 Relationship cleanup proposals | Detect duplicate-looking names and inconsistent zone/subdivision references; produce a proposed correction list with affected departments/members. Apply confirmed edits through existing validation/approval paths. | Cleaner choices and less manual investigation. | P2 / S / Medium |

**Prerequisites / limits:** Subdivision is optional. A blank selection is not automatically an error, and records must not be merged or moved purely from a name match.

### 3.8 Departments / schools

**Current process:** Departments store district, zone and optional subdivision. Searchable selection already fills member location metadata and narrows lagers. **Manual work:** Maintaining large masters and deciding corrections when an organizational assignment changes. **Owner:** Master-data/registration supervisor. **Evidence:** E04, E05.

| ID / opportunity                       | Recommended automation solution                                                                                                                                                                                 | Expected benefit                                            | Delivery        |
| -------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------- | --------------- |
| DEP-01 Validated department intake     | Import proposed master records to staging, match approved codes/parents and show duplicates/errors before authorized creation or edit.                                                                          | Less manual master entry; fewer mismatched parents.         | P2 / M / Medium |
| DEP-02 Change impact to transfer tasks | When an office/department reorganization is approved, identify impacted current members and prepare individual transfer cases with receiving-location suggestions. Retain review and original ledger snapshots. | Faster reorganization planning without silent reassignment. | P2 / L / High   |

**Prerequisites / limits:** Existing linked location guards cannot be bypassed by bulk updates. A proposed transfer still needs both districts, an eligible officer and configured approval.

### 3.9 Lagers and consolidated activity

**Current process:** Lagers belong to zones; member counts and links are automatic. Activity combines saved records under members' current lager with module-scoped summaries and CSV export. **Manual work:** Repeated filtering and checking unusually quiet/busy or incomplete groups. **Owner:** Lager/zonal supervisor. **Evidence:** E05, E12.

| ID / opportunity                     | Recommended automation solution                                                                                                                                                | Expected benefit                                                | Delivery        |
| ------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------- | --------------- |
| LAG-01 Recurring Lager summary       | Generate scheduled activity packs for an authorized saved filter, with as-of date, totals and links to permitted source records; deliver through secure expiring links.        | Less repeated report preparation and spreadsheet consolidation. | P1 / M / Medium |
| LAG-02 Exception-focused supervision | Highlight members missing plans/openings where required, unresolved officer assignments and unusual activity changes; use configurable thresholds and an acknowledgment queue. | Directs attention to actionable exceptions.                     | P2 / M / Medium |

**Prerequisites / limits:** Current-lager reports are not historical office reports. Use permanent ledgers for earlier office snapshots; Lagers View alone must not reveal balances from another module.

### 3.10 Payment Types and rate reference settings

**Current process:** Administrators maintain contribution names, order, status, deposit rate and frequency/custom reference settings. The interest engine uses quarterly rules despite other reference frequencies. **Manual work:** Rate changes, checking effects and notifying users. **Owner:** Finance policy administrator. **Evidence:** E06, E08.

| ID / opportunity                      | Recommended automation solution                                                                                                                                                                                               | Expected benefit                                                         | Delivery        |
| ------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------ | --------------- |
| PTY-01 Effective-dated rate proposals | Add proposed rate versions with effective dates, impact preview and authorized approval. Explicitly define how effective dates map to annual/quarter calculations before activating a version.                                | Fewer mistimed rate updates and clearer future changes.                  | P2 / L / High   |
| PTY-02 Rate readiness checks          | Before period close, flag zero/unconfirmed rates, inactive types with retained balances and inconsistent reference settings; require finance acknowledgment where intentional. Notify affected operators of approved changes. | Fewer missed rate setups and less confusion about calculation frequency. | P1 / S / Medium |

**Prerequisites / limits:** Zero rates can be valid and must not be overwritten automatically. Existing posted years remain immutable; historical drafts retain their defined rate behavior. Effective dating is new functionality, not a claim of current historical rate versioning.

### 3.11 Monthly Payment Plans

**Current process:** One current member plan stores agreement date and monthly amounts. The directory already distinguishes missing/ready plans and templates can prefill amounts. Plans do not create deposits. **Manual work:** Following up missing plans and comparing expected contributions with receipts. **Owner:** Contribution/finance supervisor. **Evidence:** E06, E07.

| ID / opportunity                         | Recommended automation solution                                                                                                                                                                                         | Expected benefit                                             | Delivery        |
| ---------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------ | --------------- |
| PLN-01 Missing-plan task routing         | Turn the existing pending-plan filter into an assigned worklist and configurable reminders after registration, without granting plan access to registration-only users.                                                 | Fewer forgotten agreements and clearer ownership.            | P1 / S / Low    |
| PLN-02 Expected-versus-received matching | Introduce an expected monthly contribution register and match confirmed member/type credits to it, reporting under/over/missing amounts. Define plan effective periods, partial receipts, arrears and allocation first. | Less manual collection reconciliation; earlier exceptions.   | P2 / L / High   |
| PLN-03 Plan-change preview               | Produce a before/after breakdown and future expected-contribution impact when a plan changes; route the existing edit for approval and notify authorized staff after application.                                       | Fewer unnoticed amount changes and clearer member servicing. | P2 / M / Medium |

**Prerequisites / limits:** A current plan alone cannot reconstruct every historical expectation. Versioned agreements and receipt-allocation rules are prerequisites for reliable arrears reporting. No automatic cash credit or debit should be generated just because a plan exists.

### 3.12 Payment Plan Templates

**Current process:** Shared templates copy independent amounts into plans; staff may adjust them. Template changes/deletion do not update assigned plans. **Manual work:** Choosing a suitable template and identifying unusable/outdated ones. **Owner:** Finance master-data owner. **Evidence:** E06.

| ID / opportunity                      | Recommended automation solution                                                                                                                              | Expected benefit                          | Delivery        |
| ------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ | ----------------------------------------- | --------------- |
| TPL-01 Advisory template selection    | Suggest an approved default by a maintained policy such as department or membership category, show amounts before applying and allow an authorized override. | Faster consistent plan setup.             | P2 / M / Medium |
| TPL-02 Template health/version review | Flag templates containing only inactive/zero types; version approved revisions and show a draft comparison when explicitly reapplying to a member.           | Fewer empty plans and unintended changes. | P1 / S / Low    |

**Prerequisites / limits:** No automatic propagation into existing plans. Shared templates contain no member data; applying a suggestion still requires plan action and district rights. Any new membership category needs a defined data source.

### 3.13 Opening Balances

**Current process:** Staff save one dated opening record per member with type amounts. Forms calculate totals and lock protected values. Lists already identify pending/ready records. **Manual work:** Bulk initialization, checking source totals and resolving missing records. **Owner:** Finance migration/reconciliation lead. **Evidence:** E07, E08, E09.

| ID / opportunity                             | Recommended automation solution                                                                                                                                                                                                                      | Expected benefit                                                      | Delivery        |
| -------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------- | --------------- |
| OPN-01 Staged opening import                 | Validate file schema, unique member/type pairs, dates, cents, source totals and existing/locked openings; show dry-run errors and reconciliation before per-member atomic application. Store batch/row identities and require a designated reviewer. | Faster initial entry with fewer duplicates and wrong effective dates. | P2 / L / High   |
| OPN-02 Completeness and reconciliation queue | Compare expected imported members/types against saved openings and batch control totals; route missing or mismatched items to finance with source references.                                                                                        | Less manual spreadsheet checking and more visible exceptions.         | P1 / M / Medium |

**Prerequisites / limits:** Do not replace openings to simulate annual carry-forward or transfers. New import approval is a proposed batch control because ordinary creates are outside the current hierarchy scope. Reject closed-year/collateral/historical locks rather than override them.

### 3.14 Debit / Credit and member balance reports

**Current process:** Operators post one dated positive debit or credit; previews and statements derive balances automatically. Edits retain account identity and protected history cannot be rewritten. **Manual work:** Repeated receipt entry and reconciliation across sources. **Owner:** Finance transaction supervisor. **Evidence:** E07, E09, E11.

| ID / opportunity                 | Recommended automation solution                                                                                                                                                                                              | Expected benefit                                                                 | Delivery        |
| -------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------- | --------------- |
| TXN-01 Validated posting batches | Stage confirmed cash/payroll/bank receipt rows; match member/type, check dates and totals, present unmatched/ambiguous rows and apply reviewed rows through supported financial services.                                    | Less entry effort and fewer misallocated credits.                                | P2 / L / High   |
| TXN-02 Common operation keys     | Add per-form and per-import-row idempotency keys, server-side deduplication and a clear already-recorded response. Extend the loan-entry token principle to ordinary member/child postings.                                  | Prevent duplicate clicks, retries and repeated feed rows from duplicating money. | P1 / M / High   |
| TXN-03 Daily control report      | Reconcile openings plus credits minus debits to derived balances; compare approved import totals, linked interest/withdrawal postings and exceptional negatives/large changes. Route differences, not automatic corrections. | Earlier error detection and less end-period investigation.                       | P1 / M / Medium |

**Prerequisites / limits:** A free-text reference is not an adequate unique external key. Negative balances can be valid; flag rather than universally block them. Closed periods, Fix Deposit debit rules and original/receiving ledger ownership remain authoritative.

### 3.15 Children

**Current process:** Children are linked to a fixed parent member with name, birth date and active status; age is derived. Deposit and interest access are separate. **Manual work:** Entering family data and reviewing birth-date/contact-related inconsistencies. **Owner:** Child-services supervisor. **Evidence:** E10.

| ID / opportunity                 | Recommended automation solution                                                                                                                                                                              | Expected benefit                                                   | Delivery        |
| -------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------ | --------------- |
| CHD-01 Guided family intake      | Offer a parent-linked child intake draft with duplicate-name/birth-date warnings, explicit review and a controlled next step to open an account. Do not assume siblings with similar details are duplicates. | Fewer repeated selections and accidental duplicate registrations.  | P2 / M / Medium |
| CHD-02 Identity exception review | Schedule checks for implausible/future dates, accounts predating birth and missing legacy details; display posted-year implications before requesting corrections.                                           | Better age-based calculations and fewer blocked later corrections. | P1 / S / Medium |

**Prerequisites / limits:** Retain the saved parent relationship and posted-year identity protections. A child-only user must not see deposit or parent financial values.

### 3.16 Child Deposit Types

**Current process:** Administrators maintain age threshold and below/from-threshold annual rates; new account/date displays choose the applicable rate. **Manual work:** Preparing rate changes and identifying affected accounts. **Owner:** Child-savings policy owner. **Evidence:** E08, E10.

| ID / opportunity                  | Recommended automation solution                                                                                                                                                        | Expected benefit                                              | Delivery      |
| --------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------- | ------------- |
| CDT-01 Age-rate change simulation | Preview affected children/accounts, threshold birthdays and excluded quarters for a proposed rule; maintain approved future rule versions only after calculation semantics are agreed. | Fewer policy-entry errors and clearer impact.                 | P2 / L / High |
| CDT-02 Type readiness tasks       | Flag active types with unconfirmed settings and inactive types that block proposed account intake; notify the finance owner before closing a year.                                     | Less manual cross-checking and fewer unusable new selections. | P1 / S / Low  |

**Prerequisites / limits:** Do not rewrite saved entry rates or posted annual snapshots. Zero/equal rates can be deliberate, and equal rates do not remove the birthday-quarter exclusion.

### 3.17 Child Deposit accounts and transactions

**Current process:** Each child may hold multiple independently referenced accounts. Opening details, dated entries and rate snapshots determine balances; opening details lock after activity. **Manual work:** Repeated posting, account selection and monitoring rate transitions. **Owner:** Child-deposit operations. **Evidence:** E08, E10.

| ID / opportunity                      | Recommended automation solution                                                                                                                                                            | Expected benefit                                             | Delivery        |
| ------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------ | --------------- |
| CDP-01 Exact-reference posting intake | Extend staged receipt imports to child account references; reject ambiguous child/type matches because multiple same-type accounts are valid. Use operation keys and per-account review.   | Faster posting with fewer sibling/account allocation errors. | P2 / L / High   |
| CDP-02 Transition preparation alerts  | Prepare upcoming age-threshold and account-review tasks, including the excluded quarter and applicable later rate; retain human review of policy questions.                                | Fewer surprises in year-end interest and member enquiries.   | P1 / S / Low    |
| CDP-03 Scheduled account statements   | Produce scoped account statements at an agreed frequency and notify authorized staff via secure links. Future guardian delivery needs verified contact/consent and separate access design. | Less repetitive statement preparation.                       | P2 / M / Medium |

**Prerequisites / limits:** Never post child interest or receipts into the parent member ledger. Do not infer the intended account from name alone or silently change fixed opening details.

### 3.18 Quarterly Interest and annual carry-forward

**Current process:** Member and child calculations, exclusions, saved snapshots and annual posting are implemented. An optional cron command refreshes drafts and posts ended years directly. Posting is repeat-safe; per-account failures are collected. **Manual work:** Readiness checks, monitoring, exception resolution and policy reconciliation. **Owner:** Finance closing lead and technical operations. **Evidence:** E08, E13.

| ID / opportunity                            | Recommended automation solution                                                                                                                                                                                                        | Expected benefit                                                               | Delivery      |
| ------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------ | ------------- |
| INT-01 Govern the scheduled posting path    | Decide between approved unattended posting and calculation-only scheduling followed by human-approved posting. Enforce that decision in a shared command/service policy with job identity, authorized scope and audit.                 | Removes ambiguity between manual hierarchy controls and unattended processing. | P0 / M / High |
| INT-02 Automated close-readiness pack       | Before close, list unresolved older drafts, missing/unconfirmed rates, pension/birth-date exceptions, overlap risks and stale review fingerprints; reconcile account counts/totals and route exceptions.                               | Less closing rework and clearer sign-off.                                      | P1 / M / High |
| INT-03 Incremental preparation and recovery | After profiling, mark affected unposted account-years for recalculation on relevant changes, use bounded batches/checkpoints, and retry only retryable failures. Retain a periodic full reconciliation and fixed historical snapshots. | Less repeated full scanning and easier recovery as volumes grow.               | P2 / L / High |

**Prerequisites / limits:** Confirm quarter boundaries for non-January years; do not substitute daily/custom-reference calculations. Earlier drafts, year locks and rate snapshots remain binding. Existing cron installation and run success have not been verified by this source review.

### 3.19 Fix Deposit Rules

**Current process:** Administrators configure maturity, withdrawal percentage, collateral percentage, issue interval and loan rate within model limits. The loan rate is retained at issue; other eligibility uses configured policy. **Manual work:** Checking consequences of policy changes and locating incomplete rates. **Owner:** Deposit/credit policy owner. **Evidence:** E09.

| ID / opportunity                 | Recommended automation solution                                                                                                                                                          | Expected benefit                                        | Delivery      |
| -------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------- | ------------- |
| FDR-01 Policy change impact pack | Simulate the effect on current maturity/limits and identify unsettled accounts before an approved policy edit; define whether any new version applies to existing cycles.                | Fewer unintended product-rule changes.                  | P2 / M / High |
| FDR-02 Missing-rule exceptions   | Alert the responsible administrator when active accounts depend on a missing loan rate, inactive policy/type or unexpected policy mapping. Show the blocked action and remediation link. | Less operator investigation and fewer blocked services. | P1 / S / Low  |

**Prerequisites / limits:** Preserve confirmed 36-month/75%/90%/12-month bounds and snapshotted issued-loan rates. Changes to these bounds require a separate approved business change, not an automation shortcut.

### 3.20 Fix Deposit accounts, loans and withdrawals

**Current process:** Status/limits/maturity are calculated; staff issue loans, record assessed interest/repayments, confirm settlement, request withdrawals and review release/rejection. Release revalidates and debits atomically. **Manual work:** Identifying eligible accounts and preparing interest/settlement/release reviews. **Owner:** Deposit/loan operations and approving finance officer. **Evidence:** E09, E14.

| ID / opportunity                            | Recommended automation solution                                                                                                                                                                                                         | Expected benefit                                        | Delivery        |
| ------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------- | --------------- |
| FIX-01 Maturity and eligibility worklist    | Generate upcoming/matured-account tasks with current balance, open-loan blockers, next loan date and missing policy data. Notification is advisory and limits refresh when opened/released.                                             | Less manual date tracking and faster servicing.         | P1 / M / Medium |
| FIX-02 Interest assessment drafts           | Conditional on an approved day-count, accrual basis, compounding, rate-change and rounding policy, calculate draft loan charges for review. Record approved charges through existing entry services with unique assessment-period keys. | Less manual arithmetic; reproducible assessments.       | P3 / L / High   |
| FIX-03 Repayment and settlement preparation | Stage confirmed repayments, split principal/interest using agreed rules, prevent overpayment and prepare a zero-balance settlement checklist. Require confirmation that all interest is assessed/paid.                                  | Fewer allocation mistakes and quicker completion.       | P2 / L / High   |
| FIX-04 Withdrawal preparation and follow-up | Detect eligible cases, prepare request/review packs, remind assigned reviewers and notify after a successful release/rejection. Keep a human release decision and recheck limits/loans/chronology atomically.                           | Less follow-up effort without weakening money controls. | P2 / M / High   |

**Prerequisites / limits:** There is no defined installment schedule or due-date engine in the reviewed loan model. Do not call a loan overdue without agreed new schedule data. Do not auto-settle, auto-request or auto-release based solely on maturity/zero balance. New loans/repayments are outside current edits-only approval scope.

### 3.21 Member Ledgers and officer responsibility

**Current process:** Original/receiving ledger pages, officer snapshots and permanent events are created automatically through supported workflows. Operators assign missing original officers and change active-page responsibility with reasons. **Manual work:** Finding missing assignments and coordinating handovers. **Owner:** Ledger supervisor. **Evidence:** E11.

| ID / opportunity                     | Recommended automation solution                                                                                                                                                                       | Expected benefit                                      | Delivery        |
| ------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------- | --------------- |
| LED-01 Assignment and handover queue | Generate tasks for pending original assignments, departing officers and workload review. Suggest eligible same-zone officers but require authorized confirmation/reason and configured edit approval. | Fewer orphan responsibilities and faster offboarding. | P1 / M / High   |
| LED-02 Ledger integrity checks       | Periodically verify one active page, chain/location consistency, required actor/reference links and snapshot reconciliation; route discrepancies to controlled investigation.                         | Earlier detection of integration/migration problems.  | P1 / M / Medium |

**Prerequisites / limits:** Do not repair permanent histories using bulk updates or replace missing EPF with guessed identities. Read-only integrity checks may need administrative visibility; results still need scoped distribution.

### 3.22 Member Transfers

**Current process:** An authorized user selects destination/officer/date/reason; configured approval can defer application. Final processing freezes the source and creates the receiving ledger atomically while retaining financial IDs. **Manual work:** Readiness checking and communicating handover. **Owner:** Source/receiving operations supervisors. **Evidence:** E11, E14.

| ID / opportunity                           | Recommended automation solution                                                                                                                                                             | Expected benefit                                | Delivery        |
| ------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------- | --------------- |
| TRF-01 Preflight and handover pack         | Automatically gather source officer, latest activity, receiving eligibility and balances permitted to the reviewer; show destination mismatches and date blockers before submission.        | Fewer rejected/stale transfer attempts.         | P1 / M / Medium |
| TRF-02 Completion and acknowledgment tasks | After committed transfer, notify the source/receiving officers with permitted ledger links, create a receiving handover acknowledgment and update task/report routing for current district. | Less manual coordination and clearer ownership. | P2 / M / Medium |

**Prerequisites / limits:** Do not schedule future-dated transfers through a service that currently rejects them. A proposed future queue must revalidate on execution. Source-only recipients cannot receive later destination balances. Reminder automation must not fabricate receiving acknowledgments.

### 3.23 Approval Hierarchies and requests

**Current process:** Sequential designated users approve configured transfers/edits; requests retain submitted assignments, deduplicate pending changes, prevent ordinary self-approval and revalidate before final application. Administrators can override with reasons. **Manual work:** Inspecting queues, finding delays and handling stale/failed requests. **Owner:** Approval process owner. **Evidence:** E14.

| ID / opportunity                           | Recommended automation solution                                                                                                                                                                                         | Expected benefit                                          | Delivery        |
| ------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------- | --------------- |
| APR-01 Event-driven approval notifications | Notify designated active approvers when a request enters their current level and notify requester on decision/application outcome. Group optional digests and link to the existing authorized detail page.              | Less manual checking and shorter avoidable waiting.       | P1 / M / Medium |
| APR-02 Aging and escalation                | Add explicit step-entered timestamps, business-hour targets and configurable reminders; flag inactive/unavailable assignees to administrators. Escalation creates a task, not an approval or silent assignment rewrite. | Clearer bottleneck ownership and fewer stranded requests. | P1 / M / Medium |
| APR-03 Guided failed/stale recovery        | Explain changed fields, revoked rights and failed validations; offer a new draft based on current data for resubmission, with a link to the prior immutable request.                                                    | Less repeated entry and fewer opaque failures.            | P2 / M / High   |
| APR-04 Controlled approval-scope extension | Only if finance requests it, design explicit actions for high-risk new postings, imports/loan creation or deletions. Update scope rules, admin guards, background/API paths and tests together.                         | Consistent review across new automation channels.         | P2 / L / High   |

**Prerequisites / limits:** Current hierarchies intentionally cover transfers and existing-record edits/processes; most creates/deletes and new loan entries remain immediate. Adding an older wildcard setting is not a valid way to expand coverage. Notification messages must not grant decision rights.

### 3.24 Dashboard, reporting, exports and printing

**Current process:** Member-scoped dashboard totals and admission charts are automatic; financial statements/balance reports, Lager activity, ledger exports and profile printing are requested interactively. **Manual work:** Repeated filters, exporting and combining operational exceptions. **Owner:** Operations/reporting lead. **Evidence:** E04, E07, E12.

| ID / opportunity                              | Recommended automation solution                                                                                                                                                         | Expected benefit                                                         | Delivery        |
| --------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------ | --------------- |
| RPT-01 Saved and scheduled report definitions | Save owner/filter/as-of specifications; prepare recurring reports in the background and expose expiring authenticated downloads. Recheck current rights when generating and retrieving. | Consistent recurring packs and less manual preparation.                  | P1 / M / Medium |
| RPT-02 Role-aware action dashboard            | Show pending onboarding, due reviews, failed jobs and reconciliation exceptions using each module's View permission. Counts and drill-downs must have the same scope.                   | Staff can start with work requiring action instead of searching modules. | P1 / M / Medium |
| RPT-03 Authorized analytics feed              | Conditional on demand, publish a minimal read-only reporting dataset or scoped analytics interface, with refresh/as-of labels and reconciliation to source reports.                     | Less ad hoc spreadsheet consolidation at larger scale.                   | P3 / L / High   |

**Prerequisites / limits:** A cached report is not permission-free after access is revoked. Current-assignment Lager reports and historical-office ledger reports need separate semantics. Preserve CSV formula safety and totals across all pages.

### 3.25 Notifications and communications

**Current process:** A new user with email receives an attempted welcome email after database commit; errors are logged/warned. Operational notifications primarily appear as browser messages. No durable general-purpose delivery/reminder queue was found in reviewed paths. **Manual work:** Follow-up outside the application and retrying failed delivery. **Owner:** Operations communications owner and technical team. **Evidence:** E02, E14.

| ID / opportunity                            | Recommended automation solution                                                                                                                                                                            | Expected benefit                                              | Delivery        |
| ------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------- | --------------- |
| NOT-01 Durable notification outbox          | Save event/delivery intent in the business transaction; send asynchronously with deduplication keys, bounded retries, delivery status and failed-message worklist. Start with staff in-app/email messages. | More reliable delivery and less request-time email delay.     | P0 / M / Medium |
| NOT-02 Notification preferences and routing | Add staff channel preferences, configurable digests, mandatory process events, responsible-team routing and deduplicated reminders. Recheck recipient activity and access before sending.                  | Less alert fatigue and fewer misdirected messages.            | P1 / M / Medium |
| NOT-03 Conditional member messaging         | Add verified member/guardian contact details, consent/preference records and approved templates before integrating SMS or member email. Use minimal content and secure references.                         | Faster service updates if a provider/data policy is approved. | P3 / L / High   |

**Prerequisites / limits:** The member model has phone fields but no member email field; user-account email is a different data source. No provider, consent process or delivery contract is assumed. This document authorizes no messages to real recipients.

### 3.26 Scheduled jobs, deployment and operational support

**Current process:** The supplied installer can add an hourly locked interest job and log to a file; processing reports individual account failures. Deployment/backup guidance exists, but monitored backup/restore or job services are not established by reviewed application code. **Manual work:** Checking cron/logs, recovery and release checks. **Owner:** Technical/hosting owner. **Evidence:** E03, E13, E15.

| ID / opportunity                          | Recommended automation solution                                                                                                                                                                              | Expected benefit                                               | Delivery        |
| ----------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | -------------------------------------------------------------- | --------------- |
| OPS-01 Observable job registry            | Record job/run IDs, trigger, business date, owner, scope, timings, counts, checkpoints and outcomes; detect missed runs and surface controlled retries. Define explicit authority for global financial jobs. | Fewer silent failures and faster diagnosis.                    | P0 / M / High   |
| OPS-02 Verified backup/restore operations | Integrate approved database backup scheduling, retention, failure alerts and periodic restore drills into hosting operations, with evidence and agreed recovery targets.                                     | Less manual operational work and more dependable recovery.     | P0 / M / High   |
| OPS-03 Automated release checks           | Run formatting, system/migration checks and meaningful permission/financial regressions on candidate releases; record migration readiness and smoke-test outcomes in nonproduction before deployment.        | Fewer preventable release errors and more repeatable handover. | P1 / M / Medium |

**Prerequisites / limits:** Do not create all-powerful background workers or run the financial scheduler in every web process. Cron log presence is not proof of success. Recovery and retention targets, access to backups and actual hosting facilities require confirmation.

## 4. Cross-module workflow automation

### 4.1 Member onboarding to operational readiness

**Proposed flow:** Validated registration → original ledger creation → onboarding case → permitted plan/opening tasks → supervisor exception review → completed checklist.

Existing registration/ledger services remain authoritative. New workflow records track which steps are required, assigned, blocked or completed. A registration-only operator sees completion status suitable for that role, not hidden financial amounts. Adding children/accounts is optional unless a specific service is requested. Completion should be event-driven after commits, with a periodic scan to find missed cases.

**Controls:** A failed plan/opening task does not delete the valid registered member. Retrying a task does not create a second plan or ledger. A blank birth date produces a follow-up task rather than a guessed value. **Linked opportunities:** MBR-01, PLN-01, OPN-02, LED-01, NOT-01.

### 4.2 Monthly contribution preparation and receipt reconciliation

**Proposed flow:** Approved effective plan version → expected monthly schedule → external/entered confirmed receipts → staging/matching → exceptions/review → supported account postings → totals reconciliation → staff summary.

Introduce separate expectation and receipt-allocation records before using this workflow. Define the agreement's effective month, cut-off, partial payment, overpayment, arrears and treatment of retired/inactive members. Retain the original financial source reference. A paid receipt may cover several types or months; its allocations must reconcile to the received total.

**Controls:** Expectations never post money. Ambiguous identities, missing types, repeated external rows and control-total differences remain in an exception queue. A cancelled import does not silently delete previously posted entries; correction follows an approved adjustment process. **Linked opportunities:** PLN-02, TXN-01–03, OPN-01, CDP-01.

### 4.3 Financial-year close and interest processing

**Proposed flow:** Rate/date readiness checks → saved draft preparation → exception reconciliation → finance review → authorized posting mode → account-level results → totals/control report → failed-account worklist.

Keep the existing member and child engines and their separate account identities. A proposed run manifest records financial-year boundaries, calculation/rate snapshot references, affected accounts and approvals where required. Post one account-year atomically so one failed account does not require replaying successful credits. A durable job record and reconciliation provide operational visibility beyond the current command summary.

**Controls:** Choose calculation-only versus approved unattended posting explicitly. Do not rerun with a different historical start, recompute closed snapshots or change a pension/birth date to force a result. **Linked opportunities:** INT-01–03, PTY-02, CDT-02, OPS-01.

### 4.4 Deposit-backed loan and withdrawal service

**Proposed flow:** Maturity/loan-capacity alert → operator reviews eligibility → confirmed loan/repayment/interest records → settlement checklist → reviewed withdrawal request → current-level approval if configured → revalidated release → notification/reconciliation.

Separate notifications of eligibility from actual financial decisions. The release transaction remains the only point that debits a withdrawal. Payment collection and interest assessment sources must be defined before automating repayment allocation or charge generation.

**Controls:** Principal and interest obligations stay separate; prior settlement and current limits are checked at release. A retry cannot issue a second loan or release the same withdrawal twice. **Linked opportunities:** FDR-01–02, FIX-01–04, TXN-02, APR-01.

### 4.5 Transfer and officer handover

**Proposed flow:** Transfer preflight → configured sequential review → atomic ledger transition → committed completion event → source/receiving handover tasks → receiving acknowledgment → current-district task routing.

The core transfer already handles the financial/ledger transition; proposed automation removes coordination work around it. Pending tasks referencing a current district must refresh after transfer. Existing approval requests should be revalidated, not silently retargeted to different data or districts.

**Controls:** Preserve source-page snapshots and source-only access boundaries. Do not regenerate account openings from carried snapshots. **Linked opportunities:** TRF-01–02, LED-01, IAM-03, APR-03.

### 4.6 Master-data and policy change coordination

**Proposed flow:** Proposed master/rate change → dependency/financial impact preview → applicable approval → versioned application where designed → affected-task refresh → authorized staff notice.

Use this flow for proposed department reorganizations, future rates and product-rule updates. Most existing master creates/deletes do not enter hierarchy approval; adding review to those changes is a new policy decision. Validation remains a blocking control, while detected inconsistencies become review tasks.

**Controls:** Do not cascade destructive changes or rewrite existing plan/template copies or historical rate snapshots. **Linked opportunities:** DST-01, DEP-02, PTY-01, CDT-01, FDR-01, APR-04.

## 5. Approval and notification design

### 5.1 Preserve one decision engine

Extend the existing approval services rather than adding a competing workflow engine for the same financial action. New jobs and imports need an explicit operation boundary: a draft can be prepared automatically, but the request's actor, current permissions, district, data fingerprint and domain rules must be checked again before business application.

The current final-approval implementation replays a validated workspace operation as the requester. Before exposing that workflow to batch/API callers, define a shared application-operation interface and review the current replay contract. Do not assume a raw model save or service call automatically invokes browser approval checkpoints.

### 5.2 Recommended notification matrix

Cadences below are proposals. Events should be delivered after commit, deduplicated and cancelled/suppressed when no longer actionable.

| Event                                        | Intended audience                           | Trigger / proposed cadence                                  | Result / control                                                                                     |
| -------------------------------------------- | ------------------------------------------- | ----------------------------------------------------------- | ---------------------------------------------------------------------------------------------------- |
| Approval level entered                       | Current designated active approvers         | Immediately after entry to the level; optional daily digest | Authorized request link; no direct email approval without a separate secure design.                  |
| Approval overdue or assignee unavailable     | Process owner / administrator               | Daily policy check using explicit step-entered time         | Escalation task; no automatic approval or assignment snapshot rewrite.                               |
| Request applied/rejected/cancelled/failed    | Requester and permitted process owner       | After committed outcome                                     | Clear status, reason and safe next step; failed request links to new-draft preparation.              |
| Member onboarding incomplete                 | Assigned registration/finance team          | Daily digest after agreed grace period                      | Scoped missing-step task, without leaking inaccessible balances.                                     |
| Expected contribution unmatched              | Authorized contribution team                | After agreed period/cut-off                                 | Advisory exception until expected-plan and allocation rules are defined.                             |
| Retirement/age threshold approaching         | Membership/child-services officer           | Configurable lead-time daily scan                           | Review task; no status/rate-history mutation from notification alone.                                |
| Fix Deposit maturity / blocked service       | Authorized deposit officer                  | Daily scan plus relevant account changes                    | Current eligibility link; no automatic release.                                                      |
| Transfer completed                           | Eligible source and receiving officers      | After committed transfer                                    | Each recipient's permitted ledger reference; later receiving activity hidden from source-only users. |
| Job missed/failed or reconciliation mismatch | Technical owner and authorized finance lead | Prompt job monitor; daily unresolved digest                 | Run ID and minimal diagnostic context; controlled retry path.                                        |
| Scheduled report ready                       | Authorized report owner/subscribers         | On successful generation                                    | Authenticated expiring link; recheck download permission.                                            |
| Welcome email failure                        | System administrator                        | After bounded delivery retries                              | Delivery work item; account remains saved and password is never included.                            |

### 5.3 Notification lifecycle

Use states such as Pending → Claimed → Sent, with Retry scheduled, Suppressed or Failed review as appropriate. A provider acknowledgment and actual delivery are distinct if the provider exposes both. Store event ID, template version, recipient ID, channel, attempt count and sanitized failure details; avoid unnecessary copies of NIC, birth date and financial amounts.

Recheck recipient eligibility on send. In-app task creation and email delivery should not each create duplicate approval requests. A successful business operation must remain successful if notification delivery fails; durable delivery intent allows recovery without replaying the financial operation.

### 5.4 Approval policies requiring explicit decisions

1. Whether automated annual posting is allowed or must wait for finance approval.
2. Whether new financial/import actions need approval beyond the current transfers/edits scope.
3. Reminder timing, business hours, delegates and escalation owners; delegation must be explicitly audited and compatible with submitted snapshots.
4. Whether administrator self-approval and approval of all remaining levels remain permitted for each new action.
5. When stale requests should be flagged, cancelled by an authorized user or replaced with a new submission. Elapsed time must not itself authorize application.

## 6. Scheduling, integrations and implementation architecture

### 6.1 Recommended shared architecture

The following is a proposed logical design; the application does not currently provide all these records/services.

| Stage             | Recommended component                     | Responsibility                                                                                                                   |
| ----------------- | ----------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| 1. Detect         | Committed domain event or scheduled query | Identify completed changes or due work without changing business meaning.                                                        |
| 2. Persist intent | Durable task/outbox/job record            | Store unique event/operation ID, owner, scope and requested work in the same transaction where appropriate.                      |
| 3. Execute        | Separate background runner                | Claim bounded work, validate active authority, use existing calculations/services and record attempts.                           |
| 4. Review         | Draft/exception/approval screen           | Let an authorized officer resolve ambiguity or approve a material change.                                                        |
| 5. Apply          | Shared application-operation boundary     | Recheck approvals, permissions, district, dates, closed periods and identities; commit coordinated financial changes atomically. |
| 6. Reconcile      | Control totals and result records         | Match inputs to posted outputs, separate successes/failures and detect missing work.                                             |
| 7. Communicate    | Notification delivery/report links        | Inform only eligible recipients after commit; retain bounded retries and outcomes.                                               |

Start with database-backed tasks and a separately scheduled worker if the workload supports it. A dedicated queue/broker may be evaluated later if measured concurrency, delay or scaling needs justify it. No new vendor, package version or infrastructure purchase is assumed by this assessment.

### 6.2 Proposed scheduled-task register

| Task                               | Current status                                    | Proposed trigger                                             | Owner / essential safeguard                                                                    |
| ---------------------------------- | ------------------------------------------------- | ------------------------------------------------------------ | ---------------------------------------------------------------------------------------------- |
| Interest calculation/posting       | Command and optional hourly cron already provided | Keep current cadence only if approved; profile before change | Finance + technical owner; resolve INT-01; unique account-year results and run reconciliation. |
| Due notification dispatch          | New                                               | Frequent bounded worker polling/event processing             | Technical owner; durable claims, deduplication, retry limits and recipient access.             |
| Approval aging/escalation          | New                                               | Daily business-time check; configurable reminders            | Process owner; step-entered time, current state and no automatic decisions.                    |
| Onboarding/data-quality scan       | New                                               | Daily, plus committed events                                 | Operations owner; scoped worklist and no guessed corrections.                                  |
| Retirement/threshold/maturity scan | New                                               | Daily                                                        | Business owner; date-based advisories only, lead times configurable.                           |
| Financial control reconciliation   | New                                               | Daily and after batch/annual close                           | Finance owner; same balance services and exception handling.                                   |
| Scheduled report generation        | New                                               | Agreed daily/weekly/monthly schedule                         | Report owner; generation/download authorization and as-of labels.                              |
| Job heartbeat/missed-run monitor   | New application/hosting integration               | Frequent independent check                                   | Technical owner; detect the monitor's own failures through hosting monitoring.                 |
| Backups and restore verification   | Hosting capability unverified                     | Approved backup schedule and periodic drill                  | Hosting owner; retention/RPO/RTO and recorded restore evidence.                                |

All business dates use Asia/Colombo. Bound the query range/workload, prevent overlapping execution and define how missed runs catch up. Retry transient delivery/connectivity failures; do not repeatedly replay failed validation or stale approval as if time would make it safe.

### 6.3 Practical integration opportunities

These interfaces are proposed. Current internal JSON lookup endpoints do not constitute a secure external integration platform.

| Integration candidate         | Data / direction                                                                | Practical first solution                                                                                                                      | Dependencies and priority                                                                       |
| ----------------------------- | ------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------- |
| HR / employee directory       | Verified employee identity, EPF, zone and employment status into review staging | Begin with a signed-off exchange file; match existing officers, show changes and create reassignment/offboarding cases.                       | Provider/data contract unknown; no auto-access expansion. P3 / XL.                              |
| Payroll / contribution source | Confirmed contribution allocations into transaction staging                     | Schema-controlled file with source period, immutable external row ID, member/type/amount and reconciliation totals; review exceptions.        | Distinguish expected deduction from collected money; PLN-02/TXN-01. P2 pilot; API P3 / XL.      |
| Bank / payment receipt source | Confirmed receipt references/status into matching                               | Start with statement-file reconciliation; introduce authenticated API/webhook only after source guarantees and reversal handling are defined. | Unique external IDs, replay protection, cleared/pending states, discrepancy owner. P3 / XL.     |
| Staff email                   | Event notices to account email                                                  | Extend existing backend using durable delivery rather than new synchronous send calls in each module.                                         | NOT-01/02; verified staff addresses. P1 / M.                                                    |
| Member SMS / email            | Minimal service notices to member/guardian                                      | Pilot only after contact ownership, verified destinations, consent/preferences and provider templates are agreed.                             | Member email/guardian contact schema is new; provider choice not assumed. P3 / L.               |
| Accounting / analytics        | Approved summaries outward                                                      | Reconciled, scoped exports first; later documented read-only dataset or approved journal mapping.                                             | Application is not a full double-entry GL; mapping/tax/account codes require discovery. P3 / L. |
| Hosting monitoring / backup   | Job/backup outcomes outward; restore evidence inward                            | Connect run results and missed-heartbeat signals to the hosting team's approved monitoring channel.                                           | Actual facilities and operational owners to confirm. P0/P1 / M.                                 |

### 6.4 Common import/integration contract

Every new posting feed needs a source system, immutable source transaction and row/allocation IDs, schema version, financial date, currency, decimal amount, member/account reference, payment type and source control totals. Preserve the original source content or a governed checksum/retention reference and expose human-readable row errors.

Stage → validate → match → review → authorize → apply → reconcile. Record which reviewer authorized which row set. Replaying the same accepted row returns the prior outcome; the same key with different financial data fails for review. Do not use a mutable filename or a member name as a financial identity key.

For file/API access, define authentication, service identity, permitted districts/actions, transport protection, request limits and source authenticity. API callbacks must not apply balances directly or bypass the domain service/approval boundary. A trusted provider reference still needs financial reconciliation.

### 6.5 Data validation and correction automation

- Reuse existing form/model/service checks for unique identifiers, relationships, positive entry sides, cents, date order, loan limits and closed periods. Browser-only checks are not sufficient.
- Separate deterministic normalization from a business correction. Trimming/uppercasing an identifier is routine; merging identities, changing birth date or reallocating money needs review.
- Store batch/row-level errors with precise fields, permitted corrective actions and owner. Correct a draft and revalidate rather than altering a previously approved payload silently.
- Compare source totals, accepted totals, posted totals and rejected/unmatched totals; explain every difference. A green import status requires both posting and reconciliation to succeed.
- Preserve historical snapshots. Approved corrections must use supported servicing/adjustment paths and must not rewrite source ledgers or posted years.

## 7. Prioritized delivery roadmap

### 7.1 Wave 0 — Establish authority and operational controls

**Scope:** INT-01, OPS-01, OPS-02 and the design of NOT-01. Confirm background financial authority, approval behavior, event/task identities, recovery ownership and delivery rules. Inventory actual scheduler/backup deployment. Establish a representative nonproduction dataset and baseline measures.

**Exit gate:** Named owners; approved unattended-posting policy; worker authorization and retry design; backup/restore objectives; no job can post merely because it runs outside a browser. This gate does not require replacing functioning cron where controlled scheduling is sufficient.

### 7.2 Wave 1 — Visible tasks and early operational improvements

**Scope:** NOT-01/02, APR-01/02, MBR-01/02, PLN-01, ZON-01, RET-01, CDP-02, FDR-02, LED-01, SES-01, RPT-02 and initial TXN-03/INT-02 checks.

Begin with staff-only notifications and read-only/advisory worklists. Use existing permissions and financial services. Pilot safe duplicate-submission protection TXN-02 before accepting replayable external data. Add one recurring report only after delivery/authorization controls are demonstrated.

**Exit gate:** Each task has an owner and deep link; reminders stop when resolved; no duplicate deliveries/financial effects under retry; revoked permissions are honored; baseline and post-pilot measures are comparable.

### 7.3 Wave 2 — Controlled imports, reconciliation and workflow handoffs

**Scope:** OPN-01/02, TXN-01/02/03, CDP-01, MBR-03, IAM-03, TRF-01/02, RET-02, APR-03 and RPT-01. Add master-data intake and cleanup where confirmed volumes justify it.

Start with one file source and a small authorized district subset. Test known duplicates, missing identities, closed years, transfers during processing, changed rights, failed rows and corrected resubmissions. Expand only after source-to-posted totals reconcile and recovery behavior is demonstrable.

**Exit gate:** Complete row-to-posting traceability; no duplicate money; reviewable exception queue; correct original/receiving ledger ownership; no unrelated district data; documented batch cancellation/correction procedure.

### 7.4 Wave 3 — Policy-dependent automation and external services

**Scope:** PLN-02/03, PTY-01, CDT-01, FDR-01, FIX-02/03/04, APR-04, IAM-02, external HR/bank/messaging integrations and RPT-03. Deploy INT-03 only after profiling demonstrates need and regression coverage exists.

These items introduce new business semantics or external trust boundaries. Finance must approve expected-contribution history, loan accrual/repayment allocation and effective-rate treatment before implementation. Technical teams must prove idempotency, authorization, source authenticity and recovery.

**Exit gate:** Approved policy/data contracts, successful pilot reconciliations, trained operators, monitored runbooks and explicit acceptance of residual exceptions.

### 7.5 Dependency ordering

| Capability                                             | Implement before                                      | Reason                                                                                  |
| ------------------------------------------------------ | ----------------------------------------------------- | --------------------------------------------------------------------------------------- |
| NOT-01 + OPS-01                                        | Reminder/report/event automation                      | Durable intent, delivery/run tracking and recoverable failure are shared prerequisites. |
| TXN-02 + staging/reconciliation contract               | Financial imports, feeds and replayable workers       | Prevent duplicate amounts and explain input/output differences.                         |
| INT-01 + finance readiness rules                       | Broader unattended financial posting                  | Resolve the current browser/scheduler approval boundary first.                          |
| Effective plan history + allocation rules              | PLN-02 arrears/expected-versus-received reporting     | Current single plan cannot establish all historical expectations.                       |
| Approved accrual/repayment policy                      | FIX-02 automated charge drafts and due/overdue alerts | Annual rate alone does not define a loan schedule or interest method.                   |
| Verified member/guardian contacts + consent + provider | NOT-03 external member messaging                      | Existing user email cannot substitute for member communication data.                    |
| Authorization at generation and download               | RPT-01/03 and financial notification packs            | Access can change after scheduling or report generation.                                |

## 8. Benefits measurement and acceptance

### 8.1 Establish a measurable baseline

No manual-time, volume, error-rate, cost or response-time data were supplied. Do not attach a claimed percentage saving to these recommendations. Record a representative baseline covering an agreed period and the same role/district/task mix used in the pilot. Measure staff handling time separately from time waiting for another person or a scheduled job.

| Outcome                   | Suggested metric                                                                     | How to assess improvement                                                                   |
| ------------------------- | ------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------- |
| Faster onboarding         | Median active handling time; incomplete required cases by age                        | Compare like-for-like cases and verify completeness, not merely registration count.         |
| Faster approvals          | Median/p95 time at each level; unresolved requests over agreed target                | Use step-entered timestamps and separate unavailable-approver cases.                        |
| Less repetitive entry     | Accepted imported rows; manual corrections per 100 rows; handling time               | Include exception/reconciliation effort, not just upload time.                              |
| Fewer posting mistakes    | Duplicate operations blocked; unreconciled totals; misallocations needing correction | Investigate false positives and ensure blocked duplicates did not reach financial services. |
| Reliable scheduled work   | Expected/completed/missed runs; failure recovery time; accounts unresolved           | Include partial account failures and independently monitor missing runs.                    |
| Better notifications      | Sent/suppressed/failed/duplicate counts; action completion after reminder            | Separate provider acceptance from delivered/opened where available.                         |
| Better reporting          | Preparation time, freshness and download authorization failures                      | Match totals to the source report and measure stale/unauthorized links.                     |
| Reduced heartbeat traffic | Activity requests and session writes per active browser-hour/tab count               | Hold actual activity/timeout constant; check idle-expiry correctness.                       |
| Better data quality       | Confirmed issues found/resolved; false-positive review workload                      | Exact duplicates and advisory name similarities need separate measures.                     |
| Better continuity         | Restore drill results and achieved recovery objectives                               | Use a test restore and reconciliation, not backup-file existence alone.                     |

For an effort estimate, use: **net staff time saved = baseline handling time − automated handling time − exception/review time − added operational maintenance time**. Monetary ROI additionally needs labor rates, actual transaction volumes, provider/hosting costs and implementation/support estimates. Those inputs remain unmeasured.

### 8.2 Representative acceptance scenarios

| Scenario                                                              | Required result before rollout                                                                                            |
| --------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| Same receipt/import delivered twice or after worker restart           | One financial application; repeat returns the original outcome; changed data with the same key is rejected.               |
| Notification fails after a successful financial commit                | Money/history remain committed once; retry affects delivery only.                                                         |
| User loses district/module access between task creation and execution | Job/report/action is blocked or safely suppressed; no financial/PII leakage.                                              |
| Approval request becomes stale or requester becomes inactive          | No application; clear failed/blocked state and resubmission guidance; immutable audit retained.                           |
| Approver is inactive or target reminder deadline passes               | Administrator receives an exception; no implicit decision, override or reassignment.                                      |
| Imported row targets a transferred member or closed year              | Current domain/ledger rules revalidate; no frozen-history rewrite or wrong-office posting.                                |
| Loan appears repaid but assessed interest is incomplete               | Settlement/release stays blocked until authorized confirmation and all requirements hold.                                 |
| Expected plan contribution exists without a confirmed receipt         | Report an expectation/exception; no money is created.                                                                     |
| Child has multiple accounts of the same type                          | Exact account reference required; ambiguous allocation stays unposted.                                                    |
| Scheduler misses runs and later catches up                            | Missing runs are detected; bounded catch-up retains unique account-year results and reconciles totals.                    |
| Large report/import has partial failures                              | Successes and exceptions are explicit; restart does not duplicate successful work; totals account for every row.          |
| Member changes district after a report was prepared                   | Retrieval rechecks rights and suppresses/rebuilds according to approved report policy.                                    |
| Sender tab closes or browser sleeps during heartbeat coordination     | Another eligible tab takes over safely; no inactive session revival or permanent keepalive loop.                          |
| Zero rate/negative balance/fuzzy identity appears                     | Correct baseline calculation preserved; advisory review does not invent rates, block all valid negatives or merge people. |

### 8.3 Pilot approach

Select a representative small operational group and one workflow with clear owner/value, such as approval reminders or missing-plan tasks. Run advisory outputs alongside the current process first. Review missed/false-positive tasks, time saved, access boundaries and support burden. For money-moving automation, use an agreed test dataset before a limited production pilot and reconcile every accepted source row to its result.

Expansion requires business sign-off on behavior and measured benefit, technical sign-off on retries/authorization/recovery, and trained staff with a documented way to handle exceptions. Disable new triggers independently if an automation misbehaves; do not undo protected financial history through ad hoc deletes.

## 9. Risks, decisions and next steps

### 9.1 Main implementation risks

| Risk                                                  | Recommended treatment                                                                                                                    |
| ----------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| Background processing bypasses approval/action checks | One explicit application-operation boundary, scoped service authority and policy checks shared with browser paths.                       |
| Retry duplicates money or notifications               | Persistent event/operation keys, atomic claims/application, result lookup and input/output reconciliation.                               |
| Automation uses stale data or yesterday's permission  | Revalidate at execution/release/download; mark stale rather than silently applying.                                                      |
| Poor source data produces confident wrong outcomes    | Staging, exact identifiers, row-level exceptions, no guessed officers/birth dates/balances and no fuzzy identity merging.                |
| Effective dates change historical financial meaning   | Version approved policy/plan rules, preserve posted snapshots and test past/future boundaries.                                           |
| Alerts become noise or reveal sensitive information   | Responsible owner, minimal messages, deduplication, preferences/digests and access checks.                                               |
| Jobs fail without an operator knowing                 | Run registry, missed-run monitoring, bounded retry and visible unresolved exceptions.                                                    |
| Automated cleanup destroys audit history              | Retention policy, inactive flags and supported correction paths; prohibit bulk history rewrites.                                         |
| Integration extends scope unexpectedly                | Start with one documented source contract/pilot; separate registration, expected contribution, cleared receipt and settlement semantics. |

### 9.2 Decisions required for implementation planning

1. Assign an accountable owner for each priority workflow, notifications, exceptions, hosting and finance policy.
2. Confirm approval rules for unattended interest posting and any proposed new/import financial actions.
3. Define required onboarding steps, retirement/threshold/maturity lead times, approval response targets and escalation recipients.
4. Define contribution effective history, receipt allocation, arrears handling, source cut-offs and correction/reversal rules.
5. Define loan accrual basis, day count, compounding, payment allocation and installment/due-date data before charge/overdue automation.
6. Confirm financial-year quarter interpretation and effective-rate treatment; retain explicit member/child separation and posted-year locks.
7. Confirm available external HR/payroll/bank interfaces, immutable source IDs, data quality and legal/organizational authority for data exchange.
8. Agree staff/member/guardian communication destinations, consent/preferences, retention and provider responsibilities.
9. Set workload, performance, backup/recovery, monitoring and report-retention objectives based on actual hosting and volumes.
10. Reconcile deployed version/migrations with the reviewed working tree before treating a proposed workflow as ready for production.

### 9.3 Recommended next step

Use this document as a prioritized opportunity backlog. Select a small initial portfolio: governed interest execution, observable jobs, durable staff notifications, approval reminders and actionable onboarding/financial exceptions. Agree acceptance measures and owners, then create separate implementation specifications for the selected items. The remaining module opportunities can be refined as real usage data and business decisions become available.

This assessment introduces no implementation changes, scheduled jobs, provider connections or messages. Existing SRS artifacts remain separate.

## 10. Source evidence and coverage

### 10.1 Evidence register

Paths are relative to the repository root. Filenames listed without a repeated directory prefix in the same cell retain that prefix. Test references identify existing scenario coverage; this assessment is not a record of a new full regression run.

| Key | Reviewed source areas                                                                                                                                                                   | What they establish                                                                                                                 |
| --- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| E01 | `members/permissions.py`, `access_forms.py`, `access_views.py`, `admin_site.py`, `admin.py`, `admin_apps.py`; `test_permissions.py`, `test_district_access.py`                          | Module/action registry, role/district restrictions, account administration, officer blockers and advanced admin boundary.           |
| E02 | `members/notifications.py`, `templates/administration/emails/user_created.txt`, email configuration; `members/test_notifications.py`                                                    | Welcome email after commit, skipped blank email, failure handling and no password delivery.                                         |
| E03 | `config/settings.py`, `middleware.py`, `views.py`, `context_processors.py`; `static/js/session.js`, `config/tests.py`, `tests/session.test.cjs`                                         | Session timeout, activity batching/tab behavior, authenticated caching and environment constraints.                                 |
| E04 | Member model; `members/member_identity.py`, `retirement_views.py`, `forms.py`, member views/templates; `test_member_identity.py`, `tests.py`                                            | Registration, derived identity/date, manual actual retirement, validation, directory/dashboard and print/export.                    |
| E05 | District/OfficeLocation/Subdivision/School/Lager models; master forms/views, `static/js/app.js`, location APIs and migrations                                                           | Hierarchy, dependent choices, current assignment, active/inactive records, protected relationships and migration ambiguities.       |
| E06 | Plan/type/template models; `members/payment_forms.py`, `payment_views.py`, `payment_template_views.py`; plan/template/period tests                                                      | Current plan, independent copied templates, reference frequency settings and pending/ready selectors.                               |
| E07 | Opening/transaction models; `members/opening_balance_forms.py`, `opening_balance_views.py`, `transaction_forms.py`, `transaction_views.py`, `transaction_balances.py`; associated tests | Financial entry, preview/report calculations, opening-date semantics, validation and account identity protections.                  |
| E08 | `members/deposit_interest.py`, `child_interest.py`, `interest_forms.py`, `interest_views.py`, annual snapshot models; interest/identity tests                                           | Quarter minima, exclusions, snapshots, once-only posting, carry-forward, review fingerprints and closed-year guards.                |
| E09 | Fix Deposit models; `members/fix_deposit_forms.py`, `fix_deposit_views.py`, `fix_deposit_services.py`; `test_fix_deposits.py`                                                           | Eligibility/limits, manually assessed interest, repayment operation keys, settlement, withdrawal release and collateral protection. |
| E10 | Child/deposit/type models; `members/child_forms.py`, `child_views.py`, `child_services.py`; child deposit/interest tests                                                                | Fixed parent/account identity, multiple accounts, age-based rates, entry snapshots and separate balances.                           |
| E11 | `members/transfer_models.py`, `transfer_services.py`, `transfer_forms.py`, `transfer_views.py`, `ledger_tracking.py`, `apps.py`; transfer/migration tests                               | Eligible officer rules, original/receiving pages, immutable events, service hooks and historical access.                            |
| E12 | `members/lager_activity.py`, `lager_views.py`, report/export templates; `test_lager_activity.py`                                                                                        | Current-lager activity aggregation, scoped sections, date/filter behavior and full CSV export.                                      |
| E13 | `members/management/commands/process_deposit_interest.py`, `install_interest_schedule.py`, scheduler tests                                                                              | Direct calculation/posting path, historical start, failure reporting and optional locked hourly cron.                               |
| E14 | `members/approval_models.py`, `approval_scope.py`, `approval_forms.py`, `approval_services.py`, `approval_views.py`; approval checkpoints and `test_approvals.py`                       | Transfer/edit-only scope, assignment snapshots, decisions, final revalidation/replay, reasons, deduplication and audit.             |
| E15 | `README.md`, `requirements.txt`, `package.json`, `config/urls.py`, `members/urls.py`, `access_urls.py`, templates and development checks                                                | Stack, route/interface coverage, setup/deployment guidance and documented operational boundaries.                                   |

### 10.2 Registered-module coverage matrix

| Registered permission module    | Assessment section | Opportunity IDs      |
| ------------------------------- | ------------------ | -------------------- |
| Members                         | 3.3–3.4            | MBR-01–03, RET-01–02 |
| Member Transfers                | 3.22               | TRF-01–02            |
| Member Ledgers                  | 3.21               | LED-01–02            |
| Children                        | 3.15               | CHD-01–02            |
| Child Deposits                  | 3.17               | CDP-01–03            |
| Child Deposit Types             | 3.16               | CDT-01–02            |
| Districts                       | 3.5                | DST-01               |
| Zonal offices                   | 3.6                | ZON-01–02            |
| Subdivisions                    | 3.7                | SUB-01               |
| Departments                     | 3.8                | DEP-01–02            |
| Lagers                          | 3.9                | LAG-01–02            |
| Payment Plan                    | 3.11               | PLN-01–03            |
| Payment Plan Templates          | 3.12               | TPL-01–02            |
| Quarterly Interest              | 3.18               | INT-01–03            |
| Debit / Credit                  | 3.14               | TXN-01–03            |
| Opening Balances                | 3.13               | OPN-01–02            |
| Payment Types                   | 3.10               | PTY-01–02            |
| Fix Deposit Loans & Withdrawals | 3.20               | FIX-01–04            |
| Fix Deposit Rules               | 3.19               | FDR-01–02            |

Supporting workstreams: user/role administration (3.1), sessions (3.2), approvals (3.23), dashboard/reporting (3.24), notifications (3.25), and operational jobs (3.26). Retirement is separately assessed under the Members module. Proposed integrations are assessed in Section 6.3, rather than represented as existing modules.

### 10.3 Terms

**Outbox:** Durable intent to deliver an event/notification, saved with the relevant transaction. **Idempotency key:** A unique operation identity used to prevent duplicate financial application under retries. **Staging:** Reviewable incoming data that has not yet changed business balances. **Exception queue:** Assigned work requiring human correction or a decision. **As-of date:** Business date through which a balance/report is calculated. **EPF:** Employee identifier used for ledger responsibility. **Lager / Fix Deposit:** Existing product labels retained. **RPO/RTO:** Recovery-point/recovery-time objectives to agree with hosting owners.

**End of assessment draft.**
