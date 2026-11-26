# EDCS Microservices Feasibility Assessment

EDCS Member Management System

Architecture feasibility, proposed service boundaries and migration considerations

Document identifier: EDCS-ARCH-001  
Version: 1.0  
Prepared: 22 November 2026  
Status: Architecture proposal for review  
Scope: Documentation only

This assessment answers whether the existing EDCS application can be separated into microservices, which boundaries are practical, and what must be preserved during any future transition. It is based on the current repository's structure and business services. All proposed services, APIs, events, infrastructure and migration activities are designs for later consideration; none is implemented or authorized by this document.

### Contents

1. Feasibility decision
2. Current architecture and source evidence
3. Readiness and suitability
4. Proposed logical service boundaries
5. Recommended deployment architecture
6. Financial transactions that constrain decomposition
7. Data ownership and migration design
8. Proposed APIs and integration events
9. Authentication and authorization across services
10. Reporting, audit and background processing
11. Reliability, security and operational requirements
12. Possible migration stages and decision gates
13. Risks, alternatives and tradeoffs
14. Future verification and acceptance criteria
15. Decisions and information still required
16. Scope confirmation and source reference register

## 1. Feasibility decision

### 1.1 Can this application become microservices

**Yes. The application is technically capable of being decomposed into independently deployed services, but the current modules cannot safely be turned into separate services simply by moving their files or assigning each group of tables a new database.** The existing system relies on a shared PostgreSQL transaction boundary, common Django identity/permissions, direct model relationships and in-process business services.

The strongest recommendation is a gradual approach: establish explicit internal module interfaces, retain a coherent financial core, and extract notification delivery and selected reporting capabilities first when there is a clear operational reason. Keep highly coupled financial posting, transfer and closing operations together until their consistency requirements have an agreed replacement design.

This conclusion establishes technical feasibility, not that microservices are automatically the most economical choice. Team capacity, actual traffic, release bottlenecks, availability requirements and deployment support have not been supplied. A well-structured single Django application may remain appropriate if those factors do not justify independent services.

### 1.2 Recommended first target

The first practical distributed target would have three independently deployed business components: the existing staff workspace and authoritative financial core; a notification delivery service; and an authorized reporting service with its own derived read store. This is a migration target, not a description of the current application.

The financial core would continue to own account writes, financial eligibility checks, permanent financial history and system year-end closing. A reporting store would contain derived data rather than become the source of truth. A notification service would consume committed notification requests and own delivery attempts. Shared infrastructure such as a gateway or message broker would support these components; it would not be counted as another business service.

### 1.3 Main reasons for the recommendation

The code already separates many business operations into named service modules, providing useful starting points for internal boundaries. Notification delivery has a clear external responsibility and does not need to participate in financial balance calculations. Reporting is read-heavy and can use projections when access control and freshness are preserved.

In contrast, contribution posting must atomically create a receipt, allocations and journal credits; a transfer changes member placement while closing/opening permanent pages; released withdrawals create protected deposit debits; and whole-system financial closing currently rolls back its entire batch on failure. Splitting these operations prematurely would change important guarantees.

### 1.4 What this assessment does not approve

This document does not approve a rewrite, a new service, a database split, an API deployment, a message broker installation or any change to application behavior. No application tests, migrations, production commands, SMTP sends or database queries were run for this architecture assessment. The deliverables are documents describing possible future work.

## 2. Current architecture and source evidence

### 2.1 Present structure

The application is a Django project with server-rendered HTML, local JavaScript/CSS and a PostgreSQL database. The project routes authentication and the custom/advanced administration interfaces, then includes business routes from the members application. The main business application contains the member, location, agreement, deposit, receipt, loan, ledger, approval, audit and closing models.

The domain models are declared across six files: `members/models.py`, `contribution_models.py`, `transfer_models.py`, `approval_models.py`, `audit_models.py` and `year_end_models.py`. These declare 41 application models. File separation improves readability but does not establish independent deployment or data ownership. The models still use direct foreign keys, shared user references and a common database.

The requirements file lists Django, psycopg and python-dotenv. The inspected application does not define independently deployed business services, a general service-to-service API contract, a durable message outbox or a broker-backed consumer architecture. Internal JSON lookup routes exist for browser forms, but they are session-authenticated UI support endpoints rather than complete microservice contracts.

### 2.2 Main dependency structure

| Current component                    | Direct dependencies and behavior                                                                        | Effect on service extraction                                                                |
| ------------------------------------ | ------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------- |
| Browser workspace and administration | Django views/forms/templates, session, messages, URL reversing and shared permission helpers.           | Retain a stable user-facing entry point while changing internal implementation gradually.   |
| Member ownership                     | User active state, module action, branch, office, Lager maintenance user and EPF.                       | Remote services need authoritative scope checks, not just a signed user identity.           |
| Contribution services                | Member, plan/category rules, current ledger, receipt, allocation rows, journals and approval context.   | Receipt posting belongs within one transactional owner initially.                           |
| Fix Deposit services                 | Member journal balance, policy, loans, assessed interest, repayments, withdrawals and locks.            | Collateral checks and deposit debits cannot rely on stale independent balances.             |
| Member and child interest            | Openings, dated journals, child identity/rate rules, pension date, annual snapshots and posting guards. | Calculation can become a worker; authoritative posting should remain close to balances.     |
| Transfers and ledgers                | Member placement, source/destination masters, officers, account snapshots and ledger history.           | Transfer consistency spans identity, authorization and financial history.                   |
| Approvals                            | Stored model identifiers, target fingerprints, form data and replay of Django view functions.           | Existing replay implementation cannot simply operate across remote services.                |
| Central audit and ledger tracking    | Signals, model save/delete hooks, request transaction and context variables.                            | Remote activity needs explicit durable attribution and event delivery.                      |
| Whole-system closing                 | Member and child account-year services under one transaction and shared locks.                          | Independent financial databases would break the current all-or-nothing batch guarantee.     |
| Welcome email                        | Django template, on-commit callback, SMTP send and request messages.                                    | A durable delivery boundary is possible after separating user feedback from delivery state. |

### 2.3 Shared transaction and actor context

`AuditTransactionMiddleware` wraps modifying HTTP requests in a database transaction and rolls back on error responses. Financial service functions additionally use atomic transactions and row locking. `LedgerActorMiddleware` stores the actor and request information in context variables used by model and audit code.

These mechanisms operate inside one Python request/process and database connection. An HTTP call or message to another process does not inherit the transaction, locks, actor context or Django request automatically. Any future service extraction must replace these assumptions explicitly.

### 2.4 Existing advantages for a gradual transition

Named service functions already centralize important workflows. Posted financial calculations retain snapshots. Receipt operations use keys for supported duplicate prevention. Approval and closing operations use fingerprints to reject stale state. Existing source contains tests for access boundaries, immutable postings, concurrency and reconciliation.

These are useful assets for future extraction. Their presence is not proof that a distributed implementation would preserve the same guarantees. The future acceptance scenarios in this document would need to be implemented and executed after any approved architectural change.

## 3. Readiness and suitability

### 3.1 Technical readiness assessment

| Area                               | Current readiness                                    | Required improvement before extraction                                                        |
| ---------------------------------- | ---------------------------------------------------- | --------------------------------------------------------------------------------------------- |
| Business capability identification | Reasonably clear modules and workflows.              | Assign one owner to each command and data category.                                           |
| Internal service functions         | Present for several financial capabilities.          | Remove direct view/form dependencies from business execution boundaries.                      |
| Independent data ownership         | Not established; common relational model.            | Define ownership, references, data contracts and migration responsibilities.                  |
| Durable integration delivery       | Not established by current on-commit email callback. | Introduce an approved outbox/consumer design if asynchronous extraction proceeds.             |
| Externalized authorization         | Not established; local ORM helpers enforce scope.    | Define current-policy checks and revocation behavior across processes.                        |
| Distributed financial consistency  | Not established.                                     | Retain the financial transaction boundary or approve a replacement protocol.                  |
| Contract versioning                | No general remote command/event contract.            | Specify schemas, compatibility policy and duplicate handling.                                 |
| Operations and observability       | Independent-service operations not demonstrated.     | Assign ownership for deployment, monitoring, incident response, restore and message recovery. |
| Automated behavioral evidence      | Existing repository suites are relevant.             | Add contract, failure, lag, replay and ownership-change scenarios after implementation.       |

### 3.2 When microservices would be justified

Independent services can be justified when particular capabilities need different release schedules, workloads, availability targets or team ownership. Examples include a growing reporting workload affecting interactive posting, multiple delivery channels with independent retries, or a dedicated team responsible for a stable capability.

Those benefits require evidence. This assessment does not establish the current number of users, transactions per day, peak concurrent sessions, report latency, production incidents or deployment bottlenecks. It therefore does not claim that the existing monolith has reached a measured capacity limit.

### 3.3 When to retain a modular monolith

If a small team owns the whole application, workloads are moderate and operational simplicity is valuable, an internally modular Django application can preserve current transactions while improving maintainability. Modules can expose explicit command/query interfaces without becoming network services. This provides a useful intermediate design and may be a sufficient long-term outcome.

Adding multiple processes that all directly modify the same tables would create deployment complexity without true data ownership. It could be a temporary migration arrangement with strict single-writer rules, but it should not be mistaken for a completed microservice architecture.

## 4. Proposed logical service boundaries

### 4.1 Eight logical capabilities

The following eight boundaries are candidate responsibilities. They do not imply that eight deployables should be created immediately. Several should initially remain internal modules within the authoritative core.

| Candidate                      | Responsibility and current source                                                                                                                         | Extraction position                                                                                    |
| ------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------ |
| C1 Identity and organization   | Users/groups, roles, EPF, branch/office assignments and organizational masters; access forms/views, permissions and officer helpers.                      | Keep local initially; externalize only after policy ownership and revocation are designed.             |
| C2 Membership                  | Member/child identity, contact details, registration and retirement; member/child forms and views.                                                        | Logical module first; physical extraction is constrained by transfers and posted-interest eligibility. |
| C3 Financial core              | Plans/templates, categories, original openings, journals, receipts, child accounts, Fix Deposit servicing, ledger continuity, annual posting and closing. | Retain as one transactional owner in the first distributed target.                                     |
| C4 Approval workflow           | Hierarchies, request/level state, decisions, audit and approved-operation execution.                                                                      | Internal module initially; replace local HTTP replay before considering a standalone service.          |
| C5 Notifications               | Delivery requests, templates or approved rendered messages, SMTP dispatch, attempts and outcomes.                                                         | First candidate for independent extraction with durable handoff.                                       |
| C6 Reporting                   | Authorized dashboards, account summaries, activity read models and export jobs.                                                                           | Early candidate after projections and authorization freshness are designed.                            |
| C7 Audit collection and search | Searchable cross-service event projection and independent retention.                                                                                      | Add as a downstream projection; retain authoritative local evidence with each business transaction.    |
| C8 Interest computation        | Deterministic calculation jobs from approved account snapshots and rate versions.                                                                         | Worker first; independent calculator only when authoritative posting remains protected.                |

### 4.2 C1 Identity and organization

This boundary would supply authenticated identity, current user status, role/action grants and organizational scope. It would own the authoritative assignment/version information needed by other services. Branch, office, Lager and officer changes directly affect member access, so they cannot be treated as harmless configuration events that may arrive whenever convenient.

The current staff browser session can remain in the workspace while later services use scoped delegated identity. An identity provider is a possible future component, not a required replacement for Django in the first stage. A central policy API or carefully versioned local policy projection would need a defined maximum revocation delay and a stronger path for sensitive financial writes.

### 4.3 C2 Membership

Membership would handle personal identity, contacts, child-parent relationships and retirement workflows. It should reference stable identifiers and avoid taking ownership of account balances. It must preserve member and child historical identity used by posted calculations and ledger snapshots.

Physical extraction requires a decision about member placement. Today a transfer updates Member branch, office, Lager, department and subdivision in the same transaction that changes permanent ledger pages. Separating those fields into a remote service while leaving financial ledgers local would introduce a partial-transfer risk. The first target therefore keeps this boundary co-located with financial transfer handling. A later split needs an approved ownership/protocol design, not two competing copies of current placement.

### 4.4 C3 Financial core

This boundary would own financial writes and their invariants. It would accept commands for receipts, journal entries/corrections, openings, account servicing and annual posting. It would maintain authoritative amounts and return committed outcomes. Plans and templates should remain with contribution allocation initially because effective agreement dates, active categories, fixed rows and current plan values are validated at posting/approval time.

Member payment accounts and child deposit accounts are separate account types, but they participate in common transfer snapshots, financial reporting and system closing. Logical separation inside the financial service is sensible. Independent financial services are a later option only after business acceptance of their consistency model.

### 4.5 C4 Approval workflow

The approval boundary would own ordered decisions and durable workflow history. The business-owning component would still decide whether the approved operation remains valid and commit it once. A workflow service must not gain permission to write arbitrary financial tables or treat an old requester grant as current authorization.

The present approval application code reconstructs an HttpRequest and resolves a stored Django route to apply an operation. A future boundary should instead use a versioned business command with an operation identity, requester identity, expected resource version and approved payload hash. These are design proposals; the current working behavior would need to be preserved during any later refactor.

### 4.6 C5 Notifications

A notification service would own delivery attempts and provider outcomes after a business transaction commits a durable notification intent. User creation should not fail merely because SMTP is down. Conversely, a process crash after user creation should not silently lose the intent to send a welcome message.

This is a favorable early boundary because it can accept a small, explicit message contract and does not calculate money. It still needs access-controlled queues/storage, duplicate management, retention, template versioning and a decision about what recipient data may be copied. SMTP credentials would belong to its deployment boundary if extraction proceeds.

### 4.7 C6 Reporting

A reporting service would own derived read models and export-job state. It would consume committed domain changes or approved incremental snapshots. It would not insert journal entries or become an alternative financial writer. Reports should identify the as-of watermark and any freshness delay.

Reporting is not permission-free. The current system filters data by action grants and current branch/office/Lager ownership, with specific retained historical-page rules. A projection containing an old member assignment cannot authorize a current user from that stale assignment. Authorization and projection freshness need separate treatment.

### 4.8 C7 Audit and C8 computation

Audit collection can support independent search, monitoring and retention, but original transactional audit evidence should remain with its business owner. An unavailable audit-search projection must not erase source evidence. Whether a financial operation must fail when local audit persistence fails should retain the current committed-evidence guarantee.

Interest calculation can run in an independently scaled worker using a frozen input snapshot, calculation version and rate rules. The authoritative financial service should verify and post the resulting annual amounts, respecting the review fingerprint, closed periods and current account state. Splitting computation from posting is a smaller, safer responsibility boundary than letting an external calculator write balances directly.

## 5. Recommended deployment architecture

### 5.1 Present deployment

The current conceptual flow is browser to Django workspace to shared PostgreSQL. Service functions, approval execution, ledger/audit hooks and account-year processing use the same application model. The scheduler invokes a Django management command; welcome delivery invokes SMTP after commit.

This is a single application architecture even if a production deployment runs multiple web workers or moves the scheduled command to another host. Process count alone does not establish service ownership.

### 5.2 First distributed target

| Component                              | Owns                                                                                                    | Communicates through                                                                                                   |
| -------------------------------------- | ------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| Staff workspace and authoritative core | Browser session, operational commands, scoped queries and authoritative transactional business records. | Internal domain interfaces; explicit notification events; reporting feed; approved external contracts if later needed. |
| Notification service                   | Delivery jobs, attempt history and provider interaction.                                                | Durable notification requests and delivery outcome events.                                                             |
| Reporting service                      | Derived authorized read models, report generation and export jobs.                                      | Committed business events/snapshots; current authorization check; read APIs.                                           |
| Messaging/dispatch infrastructure      | Transport, retention and redelivery according to an approved design.                                    | Producer outbox relay and idempotent consumers.                                                                        |
| Shared observability                   | Operational correlation, metrics and traces within agreed privacy rules.                                | Service health and bounded event/trace metadata.                                                                       |

The initial user interface can remain server-rendered Django. It can request a report job or show notification status without becoming a new single-page application. Browser tokens and cross-origin complexity do not need to be introduced simply to extract a backend capability.

### 5.3 Later target if justified

Later candidates include independent identity/policy, membership, approval workflow and deterministic calculation services. Each should pass its own contract, consistency and operational gate. Some capabilities may remain internal permanently. The architecture need not reach a predetermined service count to be successful.

Keep authoritative financial commands with one writer while the team learns how to operate messaging and read projections. A later data split should solve a demonstrated ownership/scaling problem and explicitly address system-wide closing, not just mirror the current navigation menu.

### 5.4 Deployment independence criteria

A capability is ready to be called an independent service when its code and data have a clear owner, consumers use a versioned contract, it can be deployed without coordinated direct table edits in other services, its failures have defined behavior, and its recovery/replay is observable. Shared libraries may carry technical utilities, but they should not reintroduce every service's database models into every process.

Infrastructure choices remain open. Containers, an orchestrator, a broker, a task system or a particular API framework should be selected against actual needs and team expertise. None is a prerequisite imposed by this document or a component installed by this assessment.

## 6. Financial transactions that constrain decomposition

### 6.1 Contribution receipt posting

Today the contribution service validates current member access, plan, payment categories, receipt date, current ledger, manual/remaining allocations and preview fingerprint. It creates the receipt, allocation evidence, positive journal credits and their history atomically. A below-plan receipt waits as a whole; categories are not credited early.

If plan validation, receipt storage and journal writes were moved to separate databases, a failure could leave a receipt without money, partial category credits or a changed plan applied to an old approval. A remote transaction.atomic wrapper would not roll those remote commits back.

**Recommended boundary:** keep allocation validation, receipt identity and all receipt credits in one financial transaction. Emit a committed receipt event afterward through a durable handoff. Notification and reporting consumers can catch up without creating extra money.

### 6.2 Fix Deposit loans and withdrawal release

Loan capacity depends on the authoritative deposit balance, outstanding principal, policy percentage, servicing dates and configured rate. Withdrawal release requires maturity, settled loans, current limit and a protected debit. These checks currently lock shared records before committing.

An eventually consistent balance projection is insufficient for authorizing a secured loan or withdrawal. Two services could independently approve activity against the same collateral. A stale settlement event could also release funds before the authoritative loan state permits it.

**Recommended boundary:** retain eligibility checks, relevant balances, servicing state and released withdrawal debit under one authority. If future separation is required, design explicit reservations and an invariant-preserving commit protocol before introducing remote writes.

### 6.3 Member transfer and ledger continuity

The transfer service locks the member and relevant masters/pages, validates source and destination, freezes source balances/location, updates member placement, creates a linked receiving page, records officer responsibility and saves permanent transfer history. Existing IDs and financial records continue.

A split could expose current destination ownership before the receiving page exists or close the source page without updating member placement. A projection delay could show a source-only user later receiving transactions. Replaying a transfer after an unknown response could create another page unless the command is idempotent.

**Recommended boundary:** keep the transfer workflow and authoritative current placement with the financial/ledger core in the first target. Future membership extraction should separate personal profile ownership carefully and agree how transfer placement is reserved, committed and exposed.

### 6.4 Approval decision and business application

Current final approval rechecks the original requester's access and target fingerprint, then applies the local operation. Approved means the requested change actually applied. Rejected/cancelled requests do not change business data; an application failure is retained as Failed.

A remote workflow could record final approval while the destination service is unavailable or rejects a stale account. The design must distinguish decision completion from business execution internally without telling users a payment has posted when it has not. Terminal-state semantics and any additional pending-execution state require business agreement.

**Recommended boundary:** initially retain approval application locally. Before extraction, introduce an explicit domain-command interface and durable execution/outcome identities. The business owner remains responsible for current validation, exactly-once business effects and failure reporting.

### 6.5 Annual interest posting

Annual interest uses reviewed month-end/rate snapshots, account-year uniqueness, closed-period protection and a one-time journal credit. Final closing becomes next opening without replacing original imported balances or posting principal a second time. Child interest credits its own account rather than the parent journal.

An external calculation worker should return a proposal tied to immutable input identity and calculation version. It must not trust a report-store balance as permission to post. The financial owner revalidates review state before accepting the annual command.

**Recommended boundary:** calculation may be asynchronous; posting, immutable year state and the interest journal should remain one transaction. Retries should resolve the original account-year operation rather than create another credit.

### 6.6 Whole-system financial closing

`close_financial_year` locks shared member/payment-type records, verifies the reviewed account set, invokes member and child account-year services, and records the batch in one transaction. Failure rolls back newly saved calculations, journals, closing record and associated audit writes together. This guarantee is stronger than completing several independent account requests eventually.

If member and child financial records are owned by independent databases, ordinary HTTP calls and message delivery cannot preserve this current all-or-nothing guarantee. A saga can track partial progress and recovery, but it changes the consistency/visibility model unless a stronger distributed transaction protocol is implemented. Financial reversals also cannot be treated as deletion of already-posted history.

**Recommended boundary:** keep all account classes participating in the current closing batch under one financial transaction owner. Do not split them physically until the finance owner has approved how global review, posting, partial failures, reconciliation and historical permanence will behave.

### 6.7 Business invariants that must survive

| Invariant                                                             | Future architecture obligation                                                       |
| --------------------------------------------------------------------- | ------------------------------------------------------------------------------------ |
| Receipt allocation sum equals actual received amount.                 | One owner validates exact decimal amounts and commits every related credit once.     |
| No partial credit while a below-plan receipt waits.                   | Approval and posting state cannot be inferred from an early event.                   |
| Final balance uses original effective opening plus eligible journals. | Carry snapshots and projections must not create duplicate principal.                 |
| Loan security and withdrawal checks use current balances.             | Financial decisions cannot authorize against a lagging report projection.            |
| Source transfer page stays frozen and linked to its receiver.         | Page chain, current placement and access transition have a defined atomic authority. |
| Posted years and protected entries retain history.                    | Retries, compensation and repair do not overwrite or delete evidence.                |
| Final closing equals next opening.                                    | Account-year and batch reconciliation remain explicit and testable.                  |
| Access changes affect current financial operations.                   | Delegated service calls cannot reuse indefinitely stale grants.                      |

## 7. Data ownership and migration design

### 7.1 Candidate ownership of the 41 domain models

This register describes logical ownership. A shared physical database can remain during modularization, but each table should have one owning module. It is not a database migration instruction.

| Logical owner                               | Existing application models                                                                                                                                                                      | Ownership qualification                                                                                                      |
| ------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------- |
| Identity and organization                   | District, OfficeLocation, Subdivision, School, Lager, Gender, LedgerOfficerProfile.                                                                                                              | Includes branch/office/Lager/EPF policy inputs; framework User, Group and Permission also belong to this capability.         |
| Membership                                  | Member, Child, MemberAuditEvent.                                                                                                                                                                 | Current placement and posted-year identity constraints require co-location/explicit coordination before physical extraction. |
| Financial core: agreements and member money | PaymentType, PaymentPlanTemplate, PaymentPlanTemplateAmount, PaymentPlan, PaymentPlanAmount, OpeningBalance, OpeningBalanceAmount, PaymentTransaction, PaymentReceipt, PaymentReceiptAllocation. | One authority for plan eligibility and receipt/journal writes initially.                                                     |
| Financial core: child accounts              | ChildDepositType, ChildDeposit, ChildDepositTransaction.                                                                                                                                         | Separate account domain inside the same financial transaction owner.                                                         |
| Financial core: Fix Deposit servicing       | FixDepositPolicy, FixDepositAccount, FixDepositLoan, FixLoanEntry, FixDepositWithdrawal.                                                                                                         | Must coordinate collateral and protected deposit movements atomically.                                                       |
| Financial core: interest and closing        | DepositInterestYear, ChildDepositInterestYear, FinancialYearClosing.                                                                                                                             | Calculation worker may assist; authoritative posting and whole-system close remain here.                                     |
| Financial core: ledger continuity           | MemberLedger, MemberTransfer, LedgerOfficerAssignment, LedgerRecord, LedgerEvent.                                                                                                                | Permanent snapshots, transfer chain and current placement transition are tightly coupled.                                    |
| Approval workflow                           | ApprovalHierarchy, ApprovalLevel, ApprovalRequest, ApprovalAuditEvent.                                                                                                                           | Logical ownership can be defined before replacing local operation replay.                                                    |
| Transactional audit                         | AuditLog.                                                                                                                                                                                        | Current source evidence remains with local business transactions; a remote search store is a projection.                     |

The proposed notification delivery state and reporting projection tables do not exist in this ownership register. They would be new models owned by extracted components only if implementation is later approved.

### 7.2 Cross-service references

A true cross-service reference should use a stable opaque identifier and a contract, not an ORM foreign key that assumes another service's live database is locally writable. Preserve current IDs during migration where possible; do not renumber members, accounts, journals or historical links unnecessarily. If new global identifiers are introduced, retain an explicit mapping and all historical references.

Local foreign keys remain useful inside each owning service. Cross-service referential integrity requires explicit validation, retained snapshots and lifecycle events. A deleted/deactivated remote user must not destroy permanent actor name/EPF snapshots or make historical financial records unreadable.

### 7.3 Avoid dual ownership

Copying member data into reporting is a projection; allowing reporting to update the member is a second writer. Similarly, copying plan details into a receipt is permanent evidence, not a second current plan. Each copied field should be classified as authoritative value, immutable snapshot, derived projection or temporary migration copy.

Current member placement and current authorization must have a single authority. Services may retain versions for validation, but conflicting writable copies would create transfer and access bugs. Shared database credentials that allow every service to update all tables would weaken the proposed boundaries.

### 7.4 Future migration sequence

For each approved extraction, first define the owner and contract, then take a consistent snapshot with a known source watermark. Capture committed changes occurring after that watermark. Build the destination read/write model as appropriate, verify IDs/counts/totals, and reconcile updates until caught up.

For derived reporting, cut over reads after parity and freshness checks. For authoritative data, enforce a single writer at cutover, stop or fence the old write path, verify command routing and retain an auditable rollback boundary. Avoid uncoordinated application dual writes: a database commit followed by a failed remote write can leave inconsistent state.

### 7.5 Financial migration reconciliation

Future financial movement should reconcile member/payment-type balances, child account balances, loan principal and assessed interest separately, opening effective dates, receipt totals/allocations, annual interest, next openings and ledger/transfer references. Counts alone do not establish monetary correctness.

Use explicit comparison rules for dates, decimal rounding, immutable snapshots and inactive historical categories. A new reporting projection must reproduce the same opening-date and retained-history behavior. A cutover should not silently repair historical data by changing original openings.

### 7.6 Deletion and retention

The current model protects used entities and permanent history. A future API should retain business deactivation/tombstone semantics where required. A remote delete command must not cascade through independent services or remove audit snapshots. Personal-data retention and deletion obligations require a separate approved policy consistent with necessary financial record retention.

## 8. Proposed APIs and integration events

### 8.1 Interface principles

Future commands should describe business intent rather than expose arbitrary table CRUD. The owner should validate the operation, current requester access, expected versions, relevant financial dates and operation identity. The caller should receive a stable operation/result identity and a way to distinguish accepted work from committed completion.

Money should travel as decimal strings with a currency code; business dates and timestamps should have explicit formats/timezones. Protocol-level authentication should be distinct from delegated user authorization. A failed or timed-out network request should not cause the caller to invent a new operation identity for the same intended payment.

The endpoints below are illustrative contract names. They are not existing EDCS routes and have not been implemented.

| Proposed command or query | Owner and request essentials                                                                                                   | Required result semantics                                                                                            |
| ------------------------- | ------------------------------------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------- |
| ReceiveContribution       | Financial core; member ID, received date, decimal amount, allocations, operation key, preview/version and delegated requester. | Either committed receipt/credits, approval-required outcome, or controlled rejection; never partial category credit. |
| CreateJournalEntry        | Financial core; account identity, date, one positive side, reason/reference and operation key.                                 | Same operation retry returns the original outcome; conflicting reuse is rejected.                                    |
| CorrectJournalEntry       | Financial core; original entry ID, date, reason and operation key.                                                             | Linked exact opposite entry subject to historical/servicing protections.                                             |
| TransferMember            | Financial/ledger core; current page version, receiving location/officer, date, reason and operation key.                       | One complete transfer or no placement/page change; clear committed transfer identity.                                |
| ReleaseWithdrawal         | Financial core; withdrawal ID/version, release date, reason/approval proof and operation key.                                  | Authoritative eligibility recheck and one protected debit.                                                           |
| PostAccountYear           | Financial core; account/year identity, calculation fingerprint, approval context and operation key.                            | Posted immutable result or explicit stale/closed/unauthorized rejection.                                             |
| SubmitDecision            | Approval module/service; request/version, current level, decision, reason and actor context.                                   | Decision recorded once; final execution outcome remains distinct until owner confirms application.                   |
| RequestReport             | Reporting; filter/as-of request, current user context and scope policy version.                                                | Job ID or authorized current result with source watermark and expiry.                                                |
| GetOperationStatus        | Business owner; stable operation ID and authorized caller.                                                                     | Durable pending/committed/failed outcome sufficient for safe retry after an unknown response.                        |

### 8.2 Proposed event envelope

An event envelope should include an event ID, event type/schema version, source service, aggregate ID and sequence/version, occurrence time, operation ID, correlation/causation IDs, appropriate actor/requester identity and a minimal payload. These fields let consumers deduplicate, order an aggregate's changes, correlate a business action and avoid trusting arrival order alone.

The following is a conceptual documentation example, not a payload produced by the current application:

```json
{
  "event_id": "opaque-event-id",
  "event_type": "ContributionPosted",
  "schema_version": 1,
  "source": "financial-core",
  "aggregate_id": "stable-receipt-id",
  "aggregate_version": 1,
  "operation_id": "stable-business-operation-id",
  "occurred_at": "2026-11-22T10:00:00+05:30",
  "correlation_id": "request-correlation-id",
  "payload": {
    "member_id": "stable-member-id",
    "received_on": "2026-11-22",
    "amount": "1500.00",
    "currency": "LKR",
    "allocation_count": 5
  }
}
```

Detailed account values should be sent only when the consumer is entitled to them. A notification consumer does not automatically need NIC, birth date, full financial history or raw submitted forms. An event is not an authorization token simply because it contains a requester ID.

### 8.3 Candidate events and consumers

| Proposed event                                        | Source owner                              | Intended consumer and caution                                                                    |
| ----------------------------------------------------- | ----------------------------------------- | ------------------------------------------------------------------------------------------------ |
| UserCreated or WelcomeNotificationRequested           | Identity/workspace transaction.           | Notification delivery; include only the necessary recipient/template data and never a password.  |
| AccessAssignmentChanged                               | Identity/organization policy authority.   | Policy projections and reporting; revocation must not wait indefinitely for consumer lag.        |
| MemberProfileChanged                                  | Membership authority.                     | Authorized directory/report projections; protect personal data and historical snapshots.         |
| ContributionPosted                                    | Financial core after complete commit.     | Reporting and optional notification; not an instruction to post the money again.                 |
| JournalEntryPosted                                    | Financial core.                           | Balance/report projections; identify corrections and preserve effective versus recorded date.    |
| TransferCompleted                                     | Ledger/placement owner.                   | Reporting and authorized projections; old scope must not grant later destination access.         |
| WithdrawalReleased                                    | Financial core after protected debit.     | Reporting/notification; do not count it in addition to its linked journal debit.                 |
| InterestYearPosted                                    | Financial core.                           | Year reports and notifications; avoid double counting its journal credit.                        |
| ClosingBatchCompleted                                 | Financial core.                           | Closing summaries and audit search; publish only after the authoritative complete batch commits. |
| ApprovalDecisionRecorded and ApprovedOperationApplied | Workflow and business owner respectively. | Workflow UI/search; separate a decision from completed financial application.                    |

These examples intentionally distinguish business facts from derived displays. A consumer must know whether a field is a monetary movement, a snapshot or status evidence so reports do not add the same money twice.

### 8.4 Durable handoff and delivery semantics

An outbox design would save a business change and its integration intent in the same owning database transaction. A separate relay would publish committed outbox rows. Consumers would keep inbox/event identities and apply their own changes transactionally so redelivery does not duplicate business effects.

Delivery should be assumed to occur at least once unless the selected transport and complete processing design establish a stronger guarantee. Exactly-once financial effects should be enforced by owner-side operation identities, uniqueness and transactional logic. A network timeout after commit is an unknown result, not proof that the operation failed.

SMTP introduces an additional uncertainty: a provider may accept a message before an acknowledgment is lost. An application delivery log alone cannot promise exactly-once email delivery. The notification design should specify acceptable duplicate behavior and distinguish queued, attempted, provider-accepted and failed states honestly.

### 8.5 Ordering, compatibility and recovery

Events for one account/member aggregate need a monotonic version or comparable ordering rule. Consumers should reject or defer out-of-order state transitions and identify missing sequences. Event schemas should evolve with explicit compatibility rules; old pending messages and requests must remain interpretable during rollout.

Poison messages need bounded retries, a recoverable dead-letter process and an operator identity for replay. Replay should rebuild projections without reissuing real payments or welcome messages unintentionally. Separate historical replay from live business commands. The owner must define what happens when retained event history is shorter than the required reconstruction window.

## 9. Authentication and authorization across services

### 9.1 Preserve the current access boundary

For ordinary member/financial access, the existing system combines active identity, module action permission, branch, assigned zonal office, owned Lager and valid EPF. Some historical ledger views have retained source/receiving rules. These controls are business invariants, not optional presentation filters.

A future service should not accept a user ID and assume it authorizes the request. Every money-changing command needs validated service identity, delegated requester identity and current action/resource scope. The business owner must recheck ownership and relevant policy before commit.

### 9.2 Browser and service identity

The staff UI can retain its existing session with a backend acting on behalf of the signed-in user. Later service-to-service authentication may use a mutually authenticated channel or scoped signed credentials selected by the deployment design. Short-lived credentials should have an intended audience and minimum service scope.

This document does not prescribe a new browser JWT architecture, a shared secret across every service or direct browser access to internal services. Shared Django session storage across independently owned services would retain coupling and should be an explicit transitional decision if used.

### 9.3 Policy freshness and revocation

Current assignment changes affect subsequent requests. If authorization is copied into a token or projection for a long period, a user could retain access after their Lager/office changes. A service design therefore needs an authoritative policy version or a live check for sensitive writes, plus a documented maximum delay for less-sensitive reads.

When a report projection is stale, the service may still filter it using current permitted member/page identities from the policy authority. It must not use stale ownership data as the sole grant. If the required authorization source is unavailable and there is no approved bounded cache policy, sensitive operations should deny or defer rather than widen scope.

### 9.4 Approval and administrator policy

The current rules permit active administrators to bypass ordinary business approval queues while retaining validation/history safeguards. Ordinary users cannot self-approve and cross-ledger request visibility remains constrained. Splitting a workflow service does not authorize changing those rules.

An approval decision should include evidence of who decided and which version was approved. The receiving business service should verify decision validity and the original requester's current authority, not merely trust an arbitrary approval_id field supplied by a client. Changes to terminal approval semantics or administrator policy require separate business approval.

### 9.5 Actor attribution and secrets

Context variables currently supply actor/EPF/request identity inside a local process. Remote commands and events need an explicit, validated attribution envelope. Record the initiating requester, deciding officer where applicable and executing service separately so audit does not mislabel every background write as an administrator.

Each deployment should use its own least-privilege credentials and secret configuration. SMTP credentials belong only where delivery executes. Database runtime roles should not automatically have another service's schema privileges. No credential values are included in this document and no configuration change was made during its preparation.

## 10. Reporting, audit and background processing

### 10.1 Reporting projection design

Start with read-only derived views for selected reports, not every operational screen. Identify each report's source of truth, filters, permissible delay, opening-date rules, ownership policy and required historical interpretation. Store source sequence/watermark so operators can distinguish a current authoritative balance from a delayed report.

Interactive financial decisions, collateral eligibility and posting previews should continue to use the financial owner. A report total is not a reservation or permission to withdraw. Exports should preserve the current scope and spreadsheet formula-prefix protections.

### 10.2 Audit retention design

Authoritative audit evidence should commit with the source business transaction. A downstream audit-search service can receive a durable copy through the outbox, retain source event identity and indicate collection lag. It should not become a single unavailable dependency that silently discards business evidence.

The current audit signals and local request transactions do not automatically observe writes inside a remote service. Each owner would need equivalent local audit guarantees and explicit redaction. Cross-service correlation should avoid storing raw passwords, session cookies, unbounded request payloads or unnecessary member data.

### 10.3 Notification delivery design

The present welcome email callback runs after account commit and catches SMTP failure while preserving the account. Extraction would replace the transient callback as the reliability boundary with durable delivery intent and attempt state. A user-facing confirmation should say queued when only queued; it should not claim delivery merely because an event was published.

Template/version ownership should be decided: the origin may create a rendered approved message or the delivery service may render a named version using a limited parameter schema. Public links should use the approved application origin. Retries should not send passwords or disclose additional account data.

### 10.4 Interest jobs and scheduling

Moving the interest management command into a worker deployment is possible without making the worker a new data owner. It can still execute the financial owner's supported commands. Jobs need a stable run/account-year identity, single-execution fencing where necessary, progress reporting and recovery after partial work.

Unattended annual posting and browser approval have distinct authority paths in the current design. Their governance should be confirmed before distributing the scheduler. A worker must not acquire unrestricted administrator authority merely because it runs outside a web request.

### 10.5 Projections and rebuilds

Reporting/audit projections should be reproducibly rebuildable from retained authoritative records/events under an approved snapshot-and-change protocol. Rebuilds need scope controls, progress watermarks and validation. Repairing a projection should not mutate original openings, posted interest or permanent ledger snapshots.

## 11. Reliability, security and operational requirements

### 11.1 Required capabilities before independent deployment

| Area                        | Minimum design requirement                                                                         |
| --------------------------- | -------------------------------------------------------------------------------------------------- |
| Business operation identity | Stable command key, payload conflict detection and durable outcome lookup.                         |
| Data ownership              | One authoritative writer; service-specific database privileges and migration ownership.            |
| Message delivery            | Durable handoff, deduplication, bounded retries, ordering rules and controlled replay.             |
| Failure behavior            | Explicit timeouts and pending/failed states; no assumption that timeout means rollback.            |
| Authorization               | Current scope checks and agreed revocation delay; validated service/user delegation.               |
| Audit                       | Local transactional evidence, protected actor attribution and redacted correlation.                |
| Observability               | Request/operation correlation, queue lag, error rates, job age and reconciliation alerts.          |
| Deployment                  | Health/readiness checks, compatible releases, rollback boundaries and owned runbooks.              |
| Recovery                    | Backup/restore for authoritative stores, replay/rebuild for projections and tested recovery order. |
| Financial controls          | Opening/movement/closing equations, immutable history and duplicate-effect prevention.             |

### 11.2 Failure isolation and retries

Notification provider failure should delay notification rather than undo a committed member or payment. Reporting failure should not authorize stale financial actions or block core posting unnecessarily. An unavailable authorization/financial owner should produce an explicit deferred/unavailable response rather than a fabricated success.

Retries belong at carefully selected boundaries. Blind retries of a payment after a timeout are unsafe without a stable operation identity. Automatic retries of validation failures or unauthorized commands are generally inappropriate; those require a changed valid request or operator action. Backoff and maximum attempts should be agreed with operational limits.

### 11.3 Performance and capacity

Network calls add latency and new failure modes. Replacing every ORM relationship with an individual remote lookup would create chatty workflows and fragile screens. Use capability-level commands and appropriately batched queries rather than a network API for every field.

No load tests were performed for this assessment, so this document does not claim specific throughput gains, response times or hardware savings. Future targets should be based on measured workflow demand. Report generation and notifications may scale separately while the financial owner retains its appropriate serialization points.

### 11.4 Security implications

Service decomposition introduces internal endpoints, tokens, message permissions and more deployment secrets. It does not automatically remedy existing authentication, transport, input-validation or database-privilege weaknesses. Previous security assessments describe their own dated baseline; current fixes and open issues should be rechecked before implementation rather than copied as unchanged facts.

Financial events and reporting stores may replicate sensitive member/account data. Limit payloads and retention, enforce current access on exports, separate service credentials and review trust in forwarded identities. Internal network placement alone is not authorization.

### 11.5 Technology choice

Continuing with Django for extracted services is feasible; a language rewrite is not required by this design. A separate framework could be chosen for a justified capability, but it adds skills and operational requirements. A message broker/task system, deployment platform and tracing stack should be evaluated later against reliability, support and cost constraints.

No particular cloud provider, orchestrator or broker is selected here. The document defines required behavior so implementation choices can be evaluated fairly without committing to infrastructure before requirements and ownership are known.

## 12. Possible migration stages and decision gates

### 12.1 Stage 0 Architecture review

Current authorized scope: produce this assessment. Confirm why independent deployment is needed, choose the first capability, agree data/transaction boundaries and nominate owners. The output is an accepted architectural decision or a decision to retain the current deployment. No implementation follows automatically from document acceptance.

Gate: the business and technical owners agree on objectives, constraints, accountable owners and explicit authorization for any next stage.

### 12.2 Stage 1 Internal modularization

Possible later work: make command/query boundaries explicit within the existing application, reduce direct cross-module writes, isolate approval execution from forms/routes and document single-writer ownership. Keep the existing financial transactions and UI behavior. Extracting internal modules does not require a new database or network hop.

Gate: approved behavioral invariants still pass, dependencies are known, and a proposed external boundary can be tested through a stable internal interface. Existing IDs, balances and historical protections remain intact.

### 12.3 Stage 2 Durable notification boundary

Possible later work: add transactional notification intent, relay/consumer behavior, delivery state and an independently deployed notification component. Begin with welcome email because it has a narrow responsibility. Demonstrate crash recovery and honest queued/accepted/failure status.

Gate: committed requests are not silently lost, retries are bounded/observable, duplicate handling is agreed, credentials are isolated and notifications cannot alter financial data. Reverting delivery deployment does not erase durable intents.

### 12.4 Stage 3 Reporting read models

Possible later work: choose one report, build an incremental projection, compare results in shadow mode and enforce current scope before any user-visible cutover. Maintain authoritative operational queries where freshness is essential.

Gate: report counts/totals/date rules match, ownership changes revoke access according to policy, projection lag is visible and the read path can revert safely. A read-store rebuild is demonstrated without reposting money.

### 12.5 Stage 4 Workflow command boundary

Possible later work: replace local approval HTTP replay with explicit versioned commands while still co-located. After that behavior is stable, evaluate an independent workflow deployment. Preserve original-requester authorization, sequential decisions, failure evidence and Approved-means-applied semantics.

Gate: stale/revoked/duplicate/out-of-order commands fail correctly, network-unknown outcomes recover safely and every decision/application is traceable. Any new user-visible execution state is approved by the business.

### 12.6 Stage 5 Selective identity or membership extraction

Possible later work: extract only after current policy evaluation, revocation, member placement ownership and historical identity constraints are resolved. Keep financial transfer placement atomic or use an explicitly approved replacement protocol.

Gate: no cross-ledger information exposure, no partial transfers, no stale permission writes and no duplicate authoritative current-member records. Migration and rollback preserve all stable identifiers and permanent references.

### 12.7 Stage 6 Further financial decomposition only if justified

Possible later work: examine whether child accounts, lending, calculation or other financial capabilities have genuinely independent operational needs. Compare keeping one core against the complexity of reservations, distributed execution and revised closing semantics.

Gate: finance explicitly approves the consistency model and failure/reconciliation behavior, including whole-system closing. If those guarantees cannot be preserved acceptably, retain the financial core together. Reaching this stage is optional.

### 12.8 Rollback rules for every stage

Define the last safe point before an authoritative writer changes. Prefer reverting read routing or stopping a consumer over attempting to delete committed financial work. A new owner that has accepted writes cannot be replaced by an old writer simply by switching a URL back; versions, operation identities and data changes must first be reconciled.

Keep contracts backward compatible during controlled rollout. Retain enough event history to replay approved projections, and avoid two schedulers or writers executing the same financial operation under different identities.

## 13. Risks, alternatives and tradeoffs

### 13.1 Principal risks

| Risk                        | Application-specific consequence                                    | Design response                                                                            |
| --------------------------- | ------------------------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| Financial consistency split | Partial receipts, collateral races or partially closed years.       | Keep one financial owner initially; approve any future replacement protocol explicitly.    |
| Stale scope information     | Source user sees receiving-Lager data or writes after reassignment. | Current policy checks, authoritative versions and strict read filtering.                   |
| Duplicate commands/events   | Repeated credits, transfers or notifications.                       | Stable operation/event identities and local transactional deduplication.                   |
| Approval/execution mismatch | UI says Approved while business operation has not applied.          | Distinguish internal decision/execution outcomes and preserve truthful terminal semantics. |
| Lost integration event      | Business record committed but email/report never catches up.        | Transactional outbox, observable relay and recovery.                                       |
| Distributed monolith        | Coordinated releases and shared tables despite many processes.      | Real data ownership, capability-level interfaces and independent recovery.                 |
| Audit gaps                  | Remote changes lack original actor or committed evidence.           | Explicit validated attribution and local audit plus durable projection.                    |
| Increased operational work  | More deployments, queues, secrets, restores and incident paths.     | Start with few services and assign operational ownership before extraction.                |
| Reporting double counting   | Snapshot/status plus journal movement counted as two amounts.       | Preserve event meaning, source references and reconciliation equations.                    |
| Unsafe rollback             | Old/new writers diverge or historical evidence is rewritten.        | Single-writer cutover, replay discipline and financial reconciliation.                     |

### 13.2 Alternative comparison

| Option                                                     | Benefits                                                                               | Costs and suitability                                                                                         |
| ---------------------------------------------------------- | -------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------- |
| Retain current application and improve internal boundaries | Preserves local transactions and a simple deployment model.                            | Independent workload/release separation remains limited; useful when team and demand do not justify services. |
| Modular monolith plus notification/reporting services      | Establishes practical independent capabilities while preserving financial consistency. | Requires durable integration, policy freshness and operational support; recommended first distributed target. |
| Broad microservice decomposition                           | Can provide stronger team/data ownership and independent release boundaries.           | Highest migration and consistency complexity; not currently justified by measured operational evidence.       |
| Immediate rewrite by module/menu                           | Apparent clean split and new infrastructure.                                           | High risk of losing transaction, approval and historical guarantees; not recommended for this baseline.       |

### 13.3 Expected benefits and unproven assumptions

Potential benefits include independently retryable delivery, isolated reporting workload, clearer ownership and smaller capability-specific releases. These are architectural possibilities, not measured improvements. There is no evidence here supporting a specific percentage cost reduction, latency reduction, availability increase or staffing saving.

Microservices should be evaluated against the actual objective. If maintainability is the objective, internal boundaries may achieve much of the benefit. If reporting load is the problem, a read store may help without splitting account posting. If independent teams need release ownership, wider extraction may become justified after those teams and contracts exist.

## 14. Future verification and acceptance criteria

### 14.1 Verification status

The following are proposed acceptance scenarios for future approved work. They were not executed during preparation of this document. Existing test modules provide useful behavioral references but no current pass claim is made by this architecture assessment.

### 14.2 Capability and failure scenarios

| Scenario                                          | Required observable outcome                                                                                     |
| ------------------------------------------------- | --------------------------------------------------------------------------------------------------------------- |
| V-01 Receipt retry after an unknown response      | Same operation resolves to one complete receipt and one set of credits.                                         |
| V-02 Failure during a category posting            | No partial receipt, allocation or journal set commits.                                                          |
| V-03 Below-plan approval pending                  | No category balance increases before successful final application.                                              |
| V-04 Plan changes while approval waits            | Stale execution is rejected; approved amounts are not silently replaced.                                        |
| V-05 Withdrawal and collateral concurrency        | Authoritative limits cannot be consumed twice through competing services.                                       |
| V-06 Transfer interruption                        | Current placement and permanent page chain cannot be exposed in a half-complete state.                          |
| V-07 Same-day transfer activity                   | Earlier activity remains on its original page; later activity uses the receiver.                                |
| V-08 Ownership revocation during a report/export  | Unauthorized current access is removed according to the agreed policy despite projection lag.                   |
| V-09 Approval service unavailable                 | No fabricated approval or uncontrolled financial bypass occurs.                                                 |
| V-10 Approved operation times out after commit    | Status lookup/retry returns the committed result without applying again.                                        |
| V-11 Interest calculator returns stale results    | Financial owner rejects a changed input/review version before posting.                                          |
| V-12 Annual account-year retry                    | Interest and carry-forward remain unique; original principal is not duplicated.                                 |
| V-13 Whole-system close fails mid-batch           | Current all-or-nothing behavior is preserved or an explicitly approved replacement is demonstrated.             |
| V-14 Notification process crashes around dispatch | Durable intent survives; provider-acceptance uncertainty and allowed duplicates are visible.                    |
| V-15 Broker or reporting consumer is down         | Source business facts remain safe and projections catch up without financial side effects.                      |
| V-16 Duplicate/out-of-order events                | Projection versioning/inbox handling prevents duplicate or regressed state.                                     |
| V-17 Audit-search projection unavailable          | Source transactional audit is retained and later collection is reconcilable.                                    |
| V-18 Projection rebuild                           | Account/report totals and scope match the authoritative state; live notifications are not resent inadvertently. |
| V-19 Contract version rollout                     | Old pending requests/events remain interpretable during compatible deployment.                                  |
| V-20 Cutover/rollback                             | Exactly one authoritative writer remains; IDs, balances, histories and operation identities reconcile.          |

### 14.3 Financial and access acceptance

Require explicit account-level reconciliation for openings, credits, debits, annual interest and carried final balances. Require separate loan principal/assessed-interest reconciliation. Compare member and child accounts independently and retain correct effective/recorded date treatment.

Repeat ordinary versus administrator access, missing branch/office/Lager/EPF, foreign IDs, inactive users, historic source-page views and stale approval tests. Gateway-level authentication is not enough if the business owner accepts unauthorized resources internally.

### 14.4 Operational acceptance

Before a service becomes responsible for live work, demonstrate independent deployment, health checks, restore/replay, log correlation, lag alerts and ownership of incidents. Set actual response-time, availability, queue-age and recovery targets with the system owner. Targets should be measured against representative data; none is claimed as achieved here.

## 15. Decisions and information still required

### 15.1 Business and operating questions

| Decision                                     | Why it changes the architecture                                                                      |
| -------------------------------------------- | ---------------------------------------------------------------------------------------------------- |
| Primary objective for microservices          | Maintenance, reporting load, organizational ownership and availability lead to different boundaries. |
| Current/expected user and transaction volume | Determines whether operational splitting solves a real capacity problem.                             |
| Team size and service ownership              | Independent services require clear engineering and support responsibility.                           |
| Deployment platform and operational budget   | Determines feasible isolation, messaging, secrets and recovery options.                              |
| Report freshness requirements                | Determines whether asynchronous projections are acceptable for each use case.                        |
| Maximum authorization revocation delay       | Determines live policy lookup versus bounded cached policy design.                                   |
| Whole-system closing guarantee               | Determines whether financial records can ever be split physically without changed semantics.         |
| Transfer placement authority                 | Determines the safe membership/ledger ownership boundary.                                            |
| Approval execution state policy              | Determines whether remote decision and application can be separated visibly.                         |
| Historical retention and event replay window | Determines audit, projection rebuild and recovery design.                                            |
| Notification duplicate tolerance             | Determines delivery tracking and recovery expectations around SMTP uncertainty.                      |
| Migration downtime and rollback allowance    | Determines snapshot/catch-up/cutover approach and required tooling.                                  |

### 15.2 Effort and cost interpretation

Notification extraction has a narrower business boundary than financial separation, but durable delivery and operations still require work. Reporting is a moderate integration boundary with significant scope/freshness requirements. Identity, membership, approvals and financial decomposition have higher coordination complexity.

No fixed implementation timeline or financial estimate is given because team capacity, environment, data volume and acceptance requirements are unknown. Estimation should follow a chosen first capability and written design, not a generic service count.

### 15.3 Proposed architecture decisions

The recommended decisions for review are: retain one financial transaction owner; define internal modules before network boundaries; consider notifications first and reporting second; keep current permission/ledger/approval semantics; use durable integration and idempotent owners; and defer broader data separation until its operational value and consistency model are approved.

These decisions are proposals. The user's current instruction authorizes document creation only. It does not authorize starting any of the migration stages described above.

## 16. Scope confirmation and source reference register

### 16.1 Work performed for this assessment

Work was limited to reading existing application/source documents, statically inspecting model declarations and preparing this assessment in Markdown, Word and PDF. No application modules, settings, environment configuration, schema, user records, financial data, scheduled jobs or email delivery behavior were changed for this task. No application test command or database access was needed to reach the feasibility conclusion.

The design describes current code behavior where supported by source and labels proposed architecture separately. It does not assume that a service boundary already exists because there is a service-named Python file. No production topology, current load or deployment cost was measured.

### 16.2 Source evidence register

| Source                                                                                      | Evidence and architectural implication                                                                                                 |
| ------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| `config/urls.py`, `members/urls.py`, `members/access_urls.py`                               | One workspace route structure and local business/admin handlers; stable UI can remain during migration.                                |
| `requirements.txt`                                                                          | Current Django/PostgreSQL adapter stack; no framework rewrite is required by feasibility.                                              |
| `members/models.py` and five additional domain model files                                  | Forty-one domain models with shared user/member references and direct relationships.                                                   |
| `members/permissions.py`                                                                    | Branch, office, Lager/EPF and module intersections; every service must preserve current access.                                        |
| `members/contribution_services.py`                                                          | Validated allocations, operation identity and atomic receipt/journal creation.                                                         |
| `members/contribution_models.py`                                                            | Receipt snapshots, ordered allocations, protected references and unique operation identity.                                            |
| `members/fix_deposit_services.py`                                                           | Shared authoritative balances, locks, collateral and controlled withdrawal debit.                                                      |
| `members/transfer_services.py`                                                              | Transfer performs member placement/page/history changes under one transaction.                                                         |
| `members/transfer_models.py`                                                                | Permanent references, officer snapshots, page chain and protected history.                                                             |
| `members/approval_services.py`                                                              | Current final application reconstructs/resolves a local Django request and rechecks target/requester state.                            |
| `members/approval_models.py`                                                                | Approved means applied; permanent request/decision evidence and hierarchy snapshots matter during extraction.                          |
| `members/deposit_interest.py`, `members/child_interest.py`                                  | Calculation/posting/guard separation, historical rules and unique annual account state.                                                |
| `members/year_end_services.py`                                                              | Whole-system financial closing uses one atomic batch and shared record locks.                                                          |
| `members/year_end_models.py`                                                                | Immutable closing batch and review identity.                                                                                           |
| `members/audit_tracking.py`, `members/ledger_tracking.py`, `members/apps.py`                | Local request transaction, context variables, signals and permanent-history protections do not cross process boundaries automatically. |
| `members/notifications.py`                                                                  | Current post-commit SMTP callback; clear candidate for future durable delivery separation.                                             |
| `members/transaction_balances.py`, `members/child_services.py`, `members/lager_activity.py` | Authoritative balance rules and report aggregation need parity in projections.                                                         |
| `members/management/commands/process_deposit_interest.py`                                   | Background command invokes financial services directly; worker deployment is distinct from data ownership.                             |
| Existing security, approval, contribution, transfer, interest and year-end test modules     | Future behavioral regression references; not executed for this documentation-only assessment.                                          |

### 16.3 Deliverable interpretation

The application can evolve toward microservices. The practical first architecture is a coherent financial core with carefully extracted supporting capabilities. Any future implementation should be separately instructed, designed and reviewed against the financial, access and historical guarantees documented here.
