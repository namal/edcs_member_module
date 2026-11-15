# System Requirements Specification

## EDCS Member Management System

**Document ID:** EDCS-SRS-001  
**Version:** 1.0 — Draft for stakeholder review  
**Date:** 15 November 2026  
**Basis:** Current application source, configuration, templates, migrations and test definitions  
**System:** EDCS Member Management, including member financial services and administration  
**Document owner:** To be assigned by EDCS  
**Prepared for:** EDCS management, system administrators, operational officers, developers and testers

This document specifies the behavior represented by the reviewed application. It is a source-based requirements baseline, not a statement that the deployed system has passed acceptance, performance, security or compliance testing. Organizational policies and proposed service targets needing confirmation are identified separately.

### Document control

| Version | Date             | Description                                                                                                   | Approval status                                 |
| ------- | ---------------- | ------------------------------------------------------------------------------------------------------------- | ----------------------------------------------- |
| 1.0     | 15 November 2026 | Initial SRS derived from the current working source, including transfers, ledgers and configurable approvals. | Draft; business and technical approval pending. |

| Responsibility                         | Name / designation | Approval / date |
| -------------------------------------- | ------------------ | --------------- |
| Business owner                         | To be assigned     | Pending         |
| Finance / operations representative    | To be assigned     | Pending         |
| System administrator / technical owner | To be assigned     | Pending         |
| Acceptance-test representative         | To be assigned     | Pending         |

### Contents

1. Purpose, scope and conventions
2. System overview and operating environment
3. Users and authorization model
4. Functional requirements
5. Business and calculation rules
6. Principal use cases
7. Data requirements
8. External and user interfaces
9. Nonfunctional requirements
10. Verification and acceptance
11. Assumptions, limitations and open decisions
12. Source traceability and glossary

## 1. Purpose, scope and conventions

### 1.1 Purpose

The EDCS Member Management System shall provide an authenticated browser workspace for maintaining member records and location masters, administering access, recording member and child financial activity, calculating deposit interest, servicing Fix Deposit loans and withdrawals, and preserving member ledger continuity when members transfer between offices.

This SRS provides a common baseline for stakeholder review, development, maintenance, testing and handover. It describes required observable behavior rather than reproducing the program code or installation manual.

### 1.2 Objectives

- Maintain consistent member identities and organizational assignments.
- Restrict each operator to authorized actions and districts.
- Separate monthly contribution agreements from actual deposited money.
- Produce consistent dated balances, statements and interest calculations.
- Preserve financial and officer responsibility history across transfers.
- Route configured business changes through traceable, sequential approvals.
- Provide searchable records, controlled exports and operational reports.

### 1.3 Included scope

The baseline includes authentication and session management; users, roles and district assignments; districts, zonal offices, subdivisions, departments and lagers; member registration, identity, retirement and reporting; payment types, plans and reusable templates; opening balances and debit/credit entries; quarterly interest and annual posting; children and child deposits; Fix Deposit policies, accounts, loans and withdrawals; permanent member ledgers and transfers; configurable approval hierarchies; dashboard, Lager activity, printing and supported CSV exports; welcome email; and interest-processing management commands.

### 1.4 Scope boundaries

The reviewed application does not establish requirements for a public member self-service portal, online payment gateway, direct bank settlement, payroll deductions/imports, SMS delivery, mobile applications, biometric identification, general-ledger double-entry accounting, tax computation, or regulatory returns. These are outside this baseline unless separately approved. The financial ledger records application transactions; it does not establish that money has moved through a banking network.

The system has a member registration workflow, not public user-account registration. User accounts are administered by authorized administrators. Deposit interest automation does not imply automatic assessment of loan interest.

### 1.5 Requirement conventions

- **FR-xxx:** Functional requirement supported by reviewed source behavior.
- **BR-xxx:** Business rule or calculation constraint supported by reviewed source behavior.
- **NFR-xxx:** Nonfunctional or operational requirement. Each is labelled **Baseline** or **Proposed**.
- **AC-xxx:** Acceptance scenario and expected outcome.
- **OD-xxx:** Open decision requiring stakeholder confirmation.
- **Shall:** Mandatory behavior within the stated baseline; not evidence of a completed test.
- **Proposed:** Suggested future acceptance or operating requirement, not a verified existing capability.

All functional requirements are baseline requirements. Permission and district conditions apply throughout, even where individual requirements do not repeat them. Standard CRUD permissions mean View, Add, Edit and Delete; Edit maps to Django's `change` permission. Restrictions on permanent history, closed years, linked records and approval processing override ordinary CRUD availability.

## 2. System overview and operating environment

### 2.1 Product perspective

EDCS is a server-rendered Django application using HTML templates, locally built Tailwind CSS and JavaScript, and a PostgreSQL relational database. It offers a custom administration workspace and a restricted advanced Django administration interface. It is an internal operational application; browser users act on behalf of EDCS members and their children.

| Component / actor          | Interaction                                                                                                                |
| -------------------------- | -------------------------------------------------------------------------------------------------------------------------- |
| Authorized browser user    | Opens pages, submits forms, requests reports, prints profiles and downloads authorized exports.                            |
| Django application         | Authenticates users, checks permissions and districts, validates forms, executes financial services and applies approvals. |
| PostgreSQL                 | Stores operational records, relations, constraints, calculation snapshots and permanent history.                           |
| SMTP provider              | Receives new-user welcome messages when configured.                                                                        |
| Operating-system scheduler | Invokes the interest management command; execution does not depend on a browser session.                                   |
| Deployment infrastructure  | Supplies HTTPS, application hosting, static-file delivery, secrets, backups and monitoring.                                |

Conceptual flow: **Browser → Web application → PostgreSQL**. The application also sends welcome email to SMTP. Cron invokes the application's interest command against the same database. This is a logical context model, not a network deployment diagram.

### 2.2 Environment and dependencies

| Item                 | Reviewed baseline / constraint                                                                                                                                                   |
| -------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Backend              | Python and Django; `requirements.txt` pins Django 6.1.3. Use a Python runtime compatible with the pinned dependencies.                                                           |
| Database             | PostgreSQL via psycopg; the configured default database name is `member_module`. A SQLite file in the repository does not change the configured backend.                         |
| Frontend             | Django HTML templates, Tailwind CSS and browser JavaScript; no runtime external font or script CDN is required by the documented interface.                                      |
| Build tooling        | Node.js and npm for CSS compilation and JavaScript checks; not a separate application server.                                                                                    |
| Locale               | English interface; LKR monetary values; Asia/Colombo application timezone.                                                                                                       |
| Financial year       | Configurable starting month, default January; saved calculations retain actual start/end dates.                                                                                  |
| Browser              | A modern browser with cookies and JavaScript for enhanced selection, balance previews and automatic idle redirection. Server authorization remains mandatory without JavaScript. |
| Email                | Optional configured Django email backend; SMTP is the default backend.                                                                                                           |
| Scheduled processing | Linux cron and `flock` for the supplied schedule installer; another managed scheduler may invoke the processing command.                                                         |

The README lists Python 3.12+, PostgreSQL 14+ and Node.js 20+ as setup prerequisites. Runtime compatibility and supported browser versions shall be confirmed for the deployment; this document does not certify a platform compatibility matrix.

### 2.3 Preconditions and dependencies

The deployment needs migrated database tables, configured secrets and database connectivity, at least one administrator, and valid master records. New member registration also requires an eligible ledger maintenance officer for the selected zone. Financial processing depends on accurate opening balances, dated transactions, rates and retirement/birth dates. Automatic interest posting requires a configured scheduler and a running host.

## 3. Users and authorization model

### 3.1 User classes

The following are operational user classes, not fixed hard-coded role names. Administrators can implement business roles using groups and direct permissions.

| User class                 | Responsibilities / access                                                                                                                                                                                  |
| -------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| System administrator       | Active superuser; manages accounts, roles, district assignments, shared financial masters and approval configuration; can access all districts and perform audited approval overrides.                     |
| Registration officer       | Uses granted member actions in assigned districts; may register a member and proceed to a payment plan when separately authorized.                                                                         |
| Financial operator         | Uses separately granted opening-balance, payment-plan, transaction, child-deposit, Fix Deposit or interest actions within assigned districts.                                                              |
| Ledger / transfer operator | Creates authorized transfers and maintains current ledger officer assignments using separate module permissions.                                                                                           |
| Ledger maintenance officer | Active employee identity assigned to a zonal office and district, with a unique EPF number; selected as the person responsible for a ledger. Assignment is not a grant of unrelated financial permissions. |
| Approving officer          | Named active user in a submitted hierarchy level; reviews and decides assigned requests. Ordinary approvers cannot approve their own submissions.                                                          |
| Report viewer              | Reads permitted records and reports in assigned districts; does not receive mutation rights automatically.                                                                                                 |
| Member / child             | Subject of system records; no member or child login is established by this baseline.                                                                                                                       |

### 3.2 Access principles

Effective actions are the union of direct permissions and permissions granted by all assigned roles. For ordinary operational records, an allowed action also requires access to the record's district. Roles never enlarge district assignments. An ordinary user with no assigned districts has no district-record access. Active superusers have all supported actions across all existing and future districts.

Shared masters do not all follow the same rule. Payment Types, Child Deposit Types and Fix Deposit Rules are administrator-managed; authorized ordinary users may view them. Payment Plan Templates are shared across districts and have separately assignable CRUD permissions. Only administrators may create new districts. Other location-master actions follow their respective module permissions and district restrictions.

### 3.3 Special action mappings

| Module / operation        | Access rule                                                                                                                                                                 |
| ------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Member Transfers          | View and Add only; history cannot be edited or deleted. Transfer execution requires source and receiving district access.                                                   |
| Member Ledgers            | View, Add and Edit; Edit records an officer change on an active ledger. No delete action.                                                                                   |
| Quarterly Interest        | View reports; Add calculate/save; Edit annual posting; Delete unposted drafts only. Covers member and child interest.                                                       |
| Children / Child Deposits | Separate permissions; Child Deposits covers accounts and entries. Parent-member access does not automatically grant either module.                                          |
| Fix Deposit               | Add covers account creation, new loan issues and withdrawal requests; Edit covers account changes, loan entries, settlement and withdrawal review, subject to domain rules. |
| Approval decisions        | Submitted level assignments and administrator status govern decisions; operational module permissions are rechecked for the requester before final application.             |
| Administration            | Active superusers only, including advanced Django administration. Staff status alone is insufficient.                                                                       |

Historical ledgers have a deliberate access exception: a source district retains its historical pages; a receiving/current district can follow preceding pages for continuity. Financial sections still require their own View permission. Source-only users do not gain access to subsequent receiving-page financial events.

## 4. Functional requirements

### 4.1 Authentication and sessions

- **FR-001:** The system shall require authentication before access to operational pages and data endpoints, and redirect unauthenticated browser users to sign-in where appropriate.
- **FR-002:** The system shall authenticate active accounts using stored password hashes and reject inactive accounts, including requests from an existing session after deactivation.
- **FR-003:** Successful sign-in shall send administrators to the custom control center and ordinary users to their workspace, while honoring permitted local return destinations.
- **FR-004:** The system shall support POST logout and require CSRF protection for state-changing browser requests.
- **FR-005:** Sessions shall expire after the configured inactivity timeout, default 900 seconds. Genuine keyboard, pointer, touch and scroll activity shall keep an authenticated working session alive across open tabs; a page left without further activity shall expire.
- **FR-006:** The browser shall coordinate idle expiry and logout between tabs when local storage is available, retain single-tab expiry when storage is unavailable, and avoid reviving an expired session after device sleep.
- **FR-007:** The activity endpoint shall accept POST and return 204 for an authenticated live session or 401 when unauthenticated. The browser shall redirect to sign-in on session rejection. Browser polling alone shall not keep an unattended session alive.
- **FR-008:** Authenticated responses shall prevent browser/shared caching. Session cookies shall be HTTP-only, SameSite=Lax and configured to expire at browser close; secure cookies shall be enabled outside debug mode. Browser session-restore behavior remains a browser constraint.

### 4.2 Account, role and permission administration

- **FR-009:** Active superusers shall be able to create/edit users, activate/deactivate eligible users, reset passwords, assign roles, assign one or more districts and set direct module permissions.
- **FR-010:** The permission editor shall show module action choices and effective access, combining role and direct grants. Saved permission, role and district changes shall apply on subsequent requests.
- **FR-011:** The system shall support creation, modification, assignment and deletion of roles implemented as permission groups.
- **FR-012:** Server checks shall enforce permissions independently for each supported action, including direct URLs, submitted identifiers, lookup endpoints and exports. Navigation shall expose permitted modules and actions.
- **FR-013:** The custom control center shall record account and role administration changes in its activity feed, require POST and confirmation for deletion, and block deletion of superusers or the acting administrator's own account and deactivation of superusers.
- **FR-014:** The system shall store an employee's optional unique EPF number and ledger officer zonal office. Active-ledger responsibilities shall be reassigned before deactivating an officer, changing their zone or removing required district access.
- **FR-015:** After creation of a user with an email address commits, the system shall attempt a welcome email containing username and sign-in link, excluding the password. Blank email shall skip delivery; delivery failure shall warn without undoing the account. Editing an account shall not resend it.

### 4.3 Location and organizational masters

- **FR-016:** The system shall maintain districts with name, code, full province name, province short name and active status, retaining full province names separately from abbreviations.
- **FR-017:** The system shall maintain zonal offices under districts, optional subdivisions under zones, and departments with district, zone and optional subdivision relationships.
- **FR-018:** The system shall maintain lagers with a name, globally unique normalized code, active status and required zonal office. The district shall derive from the zone; lager names shall be unique within a zone.
- **FR-019:** Master lists shall support their implemented search/filter controls, relationship counts and links to associated records, subject to authorization.
- **FR-020:** Member location selection shall narrow dependent choices. Department selection shall derive district/zone and associated province information. The selected lager shall belong to the same zone; any chosen subdivision shall belong to the selected zone.
- **FR-021:** The department selector shall support search by department name/code, alphabetical results and keyboard selection. Eligible department choices shall be available before a district is chosen and narrow as location selections are made.
- **FR-022:** New assignments shall use eligible active masters. Editing shall retain a record's existing inactive selections where supported, without allowing unrelated inactive assignments.
- **FR-023:** Linked location masters shall be protected from deletion and conflicting parent changes. Deactivation shall preserve historical relationships.

### 4.4 Member registration, profiles and identity

- **FR-024:** Authorized users shall register members with required district, zone, department, lager, member number, full name, salary, office/private addresses, mobile phone, admission date, gender, NIC and status, plus an eligible ledger maintenance officer for a new workspace registration.
- **FR-025:** The system shall support optional computer number, title, birth date, office/home phones, subdivision and recorded retirement/pension date. Full field rules are specified in Section 7.
- **FR-026:** The server shall normalize and validate identifiers, dates, salary, phone format and location consistency, and reject duplicate member, nonblank computer or NIC numbers.
- **FR-027:** The system shall derive a read-only Short Name from title/full name and a read-only expected retirement date from birth date at the default retirement age of 60. Form submissions shall not override generated values.
- **FR-028:** Member profiles shall show identity, contacts, location, status and permitted linked financial information/actions. Print formatting shall support profile printing through the browser.
- **FR-029:** The directory shall support search by full/short name, member number, NIC, computer number and mobile phone; district, zone, department, lager, status, gender and admission-date filters; supported sorting; and pagination.
- **FR-030:** Authorized member CSV export shall include all matching authorized records, preserve applicable directory filters and neutralize spreadsheet formula prefixes.
- **FR-031:** Authorized users shall edit member details subject to approvals, posted-interest restrictions and transfer controls. Existing ledger-backed location moves shall use the transfer workflow rather than direct office reassignment.
- **FR-032:** Deletion shall require the appropriate permission, confirmation and POST, and shall be refused when protected financial or permanent ledger relations exist. Inactivation/retirement shall preserve related records.

### 4.5 Retirement

- **FR-033:** The system shall calculate expected retirement as planning information only; reaching that date shall not automatically retire a member or set the pension date.
- **FR-034:** An operator with Members Edit and district access shall be able to process retirement using an actual date on/before today and after birth date when known. Successful processing shall set status to Retired and record the pension date, retaining accounts, plans and history.
- **FR-035:** The member editor shall support a recorded actual or scheduled pension date. A retired member shall require a recorded date. Changing retirement/pension information shall not change eligibility for an already posted interest year.

### 4.6 Payment types and monthly plans

- **FR-036:** Administrators shall maintain shared payment types with name, active status, order, annual deposit interest rate from 0% to 100%, and frequency/custom-period reference settings. Frequencies shall include daily, monthly, quarterly, yearly and custom. Custom reference periods shall support a repeating integer interval of 1–32,767 days/months/years, or a valid inclusive start/end date pair. Irrelevant custom fields shall clear when switching settings. Adding a payment type shall not require a code change; these reference frequencies shall not replace the quarterly calculation rule.
- **FR-037:** The system shall support one current payment plan per member, containing an agreement date and monthly amounts by payment type, with a displayed total.
- **FR-038:** New active types shall become available on relevant plan forms. Previously saved inactive-type amounts shall remain available in existing agreements. Used types shall be protected from deletion.
- **FR-039:** The payment-plan member selector shall use registered member identity, search by number/name and enforce district access. Payment-plan permissions shall be independent of member registration permissions.
- **FR-040:** Where the operator has Payment Plan Add, registration shall support proceeding to plan setup for the newly saved member. An add-only plan operator shall be able to select authorized member identity without full directory access.
- **FR-041:** Deleting a permitted plan shall remove its detail amounts and retain the member, subject to permanent-history protections. A monthly plan shall not create deposit credits automatically.

### 4.7 Reusable payment plan templates

- **FR-042:** Authorized template users shall maintain shared named templates containing a description, active status and payment-type amounts. A usable template shall contain at least one positive contribution.
- **FR-043:** Selecting an active template on a member plan shall load draft amounts that the operator may change before saving. Applying the template shall require the appropriate member plan action and district access.
- **FR-044:** Saving a template-based plan shall copy independent amounts and retain the source template name. Later edits/deletion of a template shall not change member agreements. Existing agreements shall open from their own saved amounts.
- **FR-045:** The system shall reject newly applying inactive templates or unusable copies containing only inactive/zero contributions. Template permissions shall not grant member financial permissions.

### 4.8 Opening balances

- **FR-046:** Authorized operators shall maintain one opening-balance record per member, with a balance date and amounts by shared payment type, independently from the monthly plan.
- **FR-047:** Opening-balance forms shall show relevant types, total amounts, saved amounts and not-entered distinctions where applicable. Blank submitted amounts shall save as zero; newly added types shall become available.
- **FR-048:** Saved inactive-type balances shall remain available in existing records. Opening balances shall appear on member/plan screens only with the required opening-balance access.
- **FR-049:** Changing/deleting openings shall respect closed-year, Fix Deposit collateral and historical-ledger protections. Permitted deletion shall retain the member and plan.

### 4.9 Member debit/credit and balances

- **FR-050:** Authorized operators shall record a member/payment-type transaction with date, exactly one positive debit or credit, optional reference/description and original creator identity.
- **FR-051:** Entry forms shall show saved balances as of the selected transaction date, including a total and type breakdown. The preview shall exclude unsaved values and the saved entry under edit.
- **FR-052:** Statements shall support a member/type selection, single date or inclusive date range, brought-forward amount, period debits/credits, running balances and final balance. Default end date shall be today.
- **FR-053:** Member balance reports shall support authorized member/type/date filters, include accounts with openings or transactions, retain inactive-type history, and total all matching rows across pages.
- **FR-054:** Statement and report calculations shall use the effective opening-date rules in BR-007, include earlier movements in brought-forward values, and allow negative balances where no product-specific rule prohibits the action.
- **FR-055:** Ordinary entry editing shall retain original member, payment type and creator. Inactive-type historical entries may remain editable, while new entries require an active type, subject to posted-year and historical-ledger locks.
- **FR-056:** Financial filters and balance endpoints shall reject invalid/unauthorized selections without exposing an unrestricted result set. Balance lookups shall be read-only and non-cacheable.
- **FR-057:** Deleting a permitted ordinary entry shall require confirmation and POST and recalculate derived balances. System-managed interest/withdrawal entries and protected historical entries shall not be editable/deletable through generic transaction screens.

### 4.10 Children and child deposits

- **FR-058:** Authorized users shall maintain child records with parent member, name, birth date and active status. A member may have multiple children; the saved parent relationship shall remain fixed.
- **FR-059:** Administrators shall maintain child deposit types with an age threshold from 1 to 150, annual rates from 0% to 100% below/from the threshold, order and active status.
- **FR-060:** Authorized users shall open multiple deposit accounts for a child, including multiple of the same type. Each shall have a unique reference, opening date, opening amount and active status, separately from parent member balances.
- **FR-061:** The system shall preview the age-dependent rate for a chosen date, and shall prevent entries before account opening. A child account opening shall not predate the child's birth or be in the future, and its opening amount shall be nonnegative. Account opening details shall be fixed after transactions, and protected when an interest year is posted.
- **FR-062:** Authorized users shall record child account entries using exactly one positive debit or credit, retain creator/reference/description and store the configured rate for the transaction date.
- **FR-063:** Editing an entry without changing its date shall retain its stored rate. A date change in an open year shall refresh the rate under current rules. Later master-rate changes shall not rewrite saved entry rates.
- **FR-064:** Child deposit lists and statements shall support as-of/date-range views and authorized parent/child/type filters, with brought-forward amounts, period movements, running balances and totals across pages.
- **FR-065:** Child and child-deposit permissions shall remain separate. All child records, accounts, transactions and lookups shall follow the parent member's district; child viewing alone shall not reveal deposit balances.
- **FR-066:** Linked children, types and accounts shall be protected from unsafe deletion. Inactive selections shall prevent applicable new account/entry creation while retaining authorized historical access and permitted corrections.

### 4.11 Quarterly deposit interest and annual posting

- **FR-067:** The system shall provide read-only interest previews for member payment accounts and separate child accounts, with financial year, account-group and supported member/type/child/reference filters.
- **FR-068:** Each account's quarterly interest shall use the minimum of its three month-end balances and annual rate divided by four, with the eligibility and rounding rules in Section 5. Incomplete quarters shall remain pending.
- **FR-069:** Authorized users shall save draft account-year calculations containing dates, month-end balances, rates/rate rules, quarter results, exclusions, annual total, closing principal and next opening amount.
- **FR-070:** Open-year drafts shall use current applicable master rates; after year-end a saved calculation shall retain its saved annual rate or child rate rules. Posted calculations shall display their fixed snapshots.
- **FR-071:** The quarter containing a member's recorded pension date shall earn no member deposit interest. A child's age-threshold birthday quarter shall earn no interest for that child account; the parent's pension date shall not exclude child interest.
- **FR-072:** After financial year-end, authorized users shall review and post the sum of eligible quarters once to the relevant member or child account, dated at that financial year's end, and retain carry-forward information without replacing the original opening record.
- **FR-073:** Annual posting shall be atomic, detect a changed calculation between review and posting, avoid duplicate credits on repeated execution and close zero-interest years without inserting a zero-value transaction.
- **FR-074:** Posted years, generated credits and affected historical financial inputs shall be protected from changes that would rewrite the closed period. Overlapping posted years shall be rejected; earlier draft years shall be resolved before later years close.
- **FR-075:** The processing command shall calculate member and child account-years, refresh current drafts and optionally post ended years in chronological order. Without a prior saved year or explicit starting year it shall start in the current financial year.
- **FR-076:** The supplied schedule installer shall show the schedule without installing by default and install only with its explicit option, preserving unrelated cron jobs. The schedule shall run hourly at minute 15 with overlap protection and log output; command account failures shall identify affected accounts and return failure while allowing unaffected accounts to continue.

The scheduler calls financial posting services directly; it does not submit a browser approval request. Whether unattended posting should require approval is OD-005.

### 4.12 Fix Deposit accounts, loans and withdrawals

- **FR-077:** Administrators shall configure a Fix Deposit policy linked to one payment type, including maturity/withdrawal cycle, withdrawal percentage, loan security percentage, loan interval, annual loan interest rate and active status, within BR-014 limits.
- **FR-078:** The system shall maintain at most one Fix Deposit account for each member/policy pair, with an actual cycle start, active status and notes. Saved member/policy shall remain fixed; activity shall protect the cycle start from arbitrary editing.
- **FR-079:** The status view shall show as-of deposit balance, principal and assessed interest due, open loans, maturity, next eligible loan date, loan capacity, withdrawal limit and reasons preventing an action.
- **FR-080:** Loan issuance shall require eligible active records, a configured positive loan rate, the required issue interval and sufficient available collateral capacity. It shall retain issue-date principal, rate and balance/limit snapshots.
- **FR-081:** Loan principal shall be tracked separately from the deposit balance; issuing or repaying a loan shall not itself reduce/increase the underlying deposit collateral ledger.
- **FR-082:** Authorized operators shall record bank-assessed interest charges and principal/interest repayments separately. Repayment shall not exceed remaining principal or assessed interest. Replaying the same operation token shall not duplicate the posting; a conflicting replay shall fail.
- **FR-083:** Settlement shall require zero principal and assessed interest outstanding and explicit confirmation that all interest through settlement has been assessed and paid. Zero balance without formal settlement shall not satisfy the withdrawal rule.
- **FR-084:** A withdrawal request shall require maturity, no unsettled loans and an amount within the configured limit. There shall be no more than one pending request per account. Requesting shall not debit the deposit.
- **FR-085:** Withdrawal review shall release or reject a pending request once. Release shall recheck current eligibility/limits, create exactly one deposit debit, store before/after balances and restart the maturity cycle from release. Rejection shall not debit funds or restart the cycle.
- **FR-086:** Financial action dates shall not be future-dated, precede the account cycle start or conflict with the latest relevant posted activity. Review dates shall not precede their request.
- **FR-087:** Generic debit entry shall not bypass controlled Fix Deposit withdrawals. Loan/withdrawal activity shall protect affected opening collateral and historical amount/date changes, while supported top-up credits and note/reference corrections remain possible.

### 4.13 Permanent member ledgers and officer responsibility

- **FR-088:** A new workspace member registration shall create an original permanent ledger reference with recorded location, responsible eligible officer and officer name/EPF snapshots.
- **FR-089:** Legacy records shall retain migrated references and known data without invented officer identities. Unknown responsibility shall be marked for assignment by an authorized operator.
- **FR-090:** Each member shall have at most one active ledger. Ledger pages shall retain location, previous-page link, opening/closing dates, status, registration snapshot and carried/closing balance snapshots.
- **FR-091:** Supported financial saves/deletions shall append ledger events with saved-value snapshots, financial date, recorded time and actor name/EPF when known. These events shall distinguish the originating page and subsequent servicing page.
- **FR-092:** Ledger screens shall provide sequence, current/historical status, officer responsibility history, permitted balances, record history and supported CSV export, with optional inclusion of preceding linked ledgers.
- **FR-093:** Historical source pages and frozen original transactions/openings shall not be rewritten or deleted. Existing account/loan servicing may continue through the active ledger while earlier snapshots remain available.
- **FR-094:** An authorized officer change shall require an active ledger, eligible same-zone officer and reason, preserving old/new names and EPF numbers, actor and date. Historical pages shall not be reassigned.
- **FR-095:** Ledger viewing alone shall not expose all financial or member details; each section shall enforce its own module View permission. Persons/records protected by permanent history shall be retained rather than deleted through ordinary operations.

### 4.14 Member transfers

- **FR-096:** An authorized transfer operator shall select a saved member/current source ledger, receiving district, zone, department, lager, optional subdivision, eligible officer, effective date and reason, including supported department changes within the same zone.
- **FR-097:** Transfer execution shall require both districts, valid receiving relationships/officer, a changed supported assignment and a current source ledger; stale submissions shall be rejected.
- **FR-098:** Transfer dates shall default to today, reject future dates, respect the current page opening/latest activity and be later than a previous transfer date. Safe earlier dates may be accepted under these constraints.
- **FR-099:** Completing a transfer shall atomically lock the member, close/freeze the source page, create a linked receiving active page, record new responsibility, retain transfer history and update current member location.
- **FR-100:** Transfers shall preserve member identifiers and existing child/account/loan/plan/transaction IDs and values. Carried balances shall be snapshots, not duplicate transactions or replacement opening balances.
- **FR-101:** Subsequent postings shall belong to the receiving ledger; existing same-day activity shall retain its original page. New ordinary postings shall not be backdated before the current transfer date. Supported late annual interest shall post on the current page without rewriting historical closing snapshots.
- **FR-102:** The source district shall retain its historical pages without seeing later receiving-page transactions; receiving/current district users may follow predecessor pages, subject to section permissions. Transfer and ledger histories shall have no ordinary edit/delete workflow.

### 4.15 Configurable approval hierarchies

- **FR-103:** Administrators shall configure active hierarchies for Member Transfers or existing-record edits, with module-specific or global edits-only scope, one or more uniquely ordered levels up to 20, designated active approvers and per-level rejection settings.
- **FR-104:** A matching module-specific edit hierarchy shall take precedence over the global edit hierarchy. Without a matching active hierarchy, the existing immediate processing shall continue.
- **FR-105:** Approval configuration changes shall require a reason and preserve versioned before/after configuration and approver identities. Pending requests shall retain the hierarchy version/assignments captured at submission.
- **FR-106:** A covered, valid form submission shall create an approval request before the business change is applied. The request shall preserve the submitted values, target fingerprint, human-readable summary and requester identity. Duplicate identical pending submissions shall reuse the pending request.
- **FR-107:** Only an assigned current-level active approver or administrator shall decide a pending level. Any one designated approver shall complete that level; ordinary users shall not approve their own requests or skip levels.
- **FR-108:** Rejection shall require level permission unless the actor is an administrator. Rejection/cancellation and every administrator decision shall require a reason. Requesters may cancel their pending submissions; administrators may approve all remaining levels with a recorded reason.
- **FR-109:** Request lists shall show ordinary users their submissions and assigned requests; administrators shall see all. Audit history shall retain actor identity/EPF where available, level, decision, time, reason, override flag and application outcome.
- **FR-110:** Final approval shall recheck the requester's active status, current permissions/district access, target/related data and domain validation, then apply the change once atomically. Changed/missing data, revoked access or invalid inputs shall produce a failed outcome without partial business application.
- **FR-111:** Rejected or cancelled requests shall leave business data unchanged. Approved, rejected, cancelled and failed outcomes shall remain traceable, and permanent audit events shall not be edited/deleted through normal workflows.
- **FR-112:** The scope shall cover transfers and edits/processes on existing workspace records, including retirement, annual posting, loan settlement, withdrawal review and officer changes. Other creates/deletes, new repayments/interest entries and user/role administration shall retain their normal processing. Legacy broad policies shall not enlarge this scope.
- **FR-113:** Submitted template defaults shall be copied into the request so later template changes do not change the requested amounts. Financial authorship shall retain the requester/original posting identity while approval audit identifies decision makers.
- **FR-114:** Advanced Django administration shall not directly bypass configured covered edit approvals and shall expose permanent approval/transfer/ledger audit records read-only. Privileged direct database or bulk writes are outside the workspace approval boundary.

### 4.16 Dashboard, Lager activity and common reporting

- **FR-115:** Users with Members View shall receive a district-scoped dashboard showing membership totals, active members, relevant district/office counts, current-month admissions, recent members and a six-month admission chart. Other authenticated users shall receive their permitted workspace.
- **FR-116:** Lager activity shall consolidate authorized saved records for currently assigned members and children, including inactive members, with member/activity/date filters and full matching CSV export.
- **FR-117:** Lager summaries shall use the existing module balance rules, keep loan balances separate and avoid double-counting interest snapshots or withdrawal requests as extra ledger movements. Activity-type filtering shall affect the event table; summaries shall cover permitted modules for the selected members.
- **FR-118:** Lager activity shall follow the member's current lager and shall not represent an immutable audit of previous assignments/deletions. Historical office continuity shall be provided through permanent ledgers.
- **FR-119:** Lager activity shall require Lagers View plus district access and each displayed section's own View permission. Add/Edit alone shall not grant financial report access.
- **FR-120:** Common pages shall preserve applicable filters during pagination, display form errors and success/failure messages, provide branded access-denied/not-found pages and show only authorized data/actions.

## 5. Business and calculation rules

| ID     | Rule                                                                                                                                                                                                                                                                                                                                                               |
| ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| BR-001 | Member number, NIC and nonblank computer number are unique. Identifiers are trimmed/normalized to uppercase. A blank optional computer number is not a duplicate identity.                                                                                                                                                                                         |
| BR-002 | NIC accepts 12 digits or 9 digits followed by V/X. Phone fields accept 7–15 digits with supported formatting. Salary is nonnegative; admission/birth dates cannot be future-dated.                                                                                                                                                                                 |
| BR-003 | Department and lager are required for registration/normal editing. Their zone/district relationships must agree. Province values derive from the saved district and cannot be overridden by a forged form value.                                                                                                                                                   |
| BR-004 | Short Name keeps the final name and abbreviates preceding names to initials, with optional title: Mr. + Krishantha Namal Spencer becomes Mr. K. N. Spencer. A single name remains complete.                                                                                                                                                                        |
| BR-005 | Expected retirement is birth date plus 60 years. A leap-day birth uses the last valid February day in a non-leap retirement year. Expected retirement does not automatically change status.                                                                                                                                                                        |
| BR-006 | A payment plan is a monthly agreement, not a posted deposit. Member plan amounts and opening-balance amounts are separate data.                                                                                                                                                                                                                                    |
| BR-007 | When an opening amount is effective on/before the balance date, it is the starting amount on its opening date; transactions before that date for that member/type are excluded. Without an effective saved amount for that type, applicable transaction history is used. Statement starting balance includes movements before the requested start.                 |
| BR-008 | Final deposit balance = brought-forward balance + period credits − period debits. Child final balance derives from its own account opening and transactions. Balances and running totals remain consistent across pages.                                                                                                                                           |
| BR-009 | A debit/credit entry has one positive side and the other zero; both positive, both zero and negative values are invalid. Monetary values use decimal cents rather than binary floating-point arithmetic.                                                                                                                                                           |
| BR-010 | Quarterly interest = max(minimum of the three month-end balances, 0) × annual rate / 400, rounded to cents using half-up rounding. A month before account existence contributes zero; incomplete quarters do not accrue a completed amount.                                                                                                                        |
| BR-011 | Eligible quarterly interest accumulates without quarterly capitalization. After the financial year ends, its annual sum is credited once with the year-end financial date. The next opening is closing principal plus annual interest.                                                                                                                             |
| BR-012 | A member pension-date quarter is excluded for member payment accounts only. A child threshold-birthday quarter is excluded entirely, even when its two configured rates are equal. Other quarters use the applicable below/from age rate. The implementation groups quarters from the configured financial-year start; non-calendar policy confirmation is OD-004. |
| BR-013 | For child age/rate changes, a leap-day birthday becomes 1 March in a non-leap threshold year. This is distinct from the expected-retirement date rule. Child interest never credits the parent account.                                                                                                                                                            |
| BR-014 | Fix Deposit policy: cycle defaults to 36 months and cannot be below 36; withdrawal percentage is positive and at most 75%; loan security is positive and at most 90%; loan interval defaults to and cannot be below 12 months. Cycle/interval maxima are 1,200 months. Loan rate must be positive and at most 100% before issue.                                   |
| BR-015 | Additional loan capacity = max(floor-to-cents(max(deposit balance, 0) × configured loan percentage / 100) − outstanding principal, 0). All outstanding loan principal shares the collateral limit. Loan rate is snapshotted at issue; assessed interest is recorded separately.                                                                                    |
| BR-016 | Withdrawal limit = floor-to-cents(max(deposit balance, 0) × configured withdrawal percentage / 100). Maturity and formal settlement of every open loan are required. Release restarts the common account cycle; recent top-ups share that cycle.                                                                                                                   |
| BR-017 | Permanent transfer carry-forward snapshots do not create money. Existing financial IDs, balances and loan cycles continue. Closed-office snapshots do not change when subsequent servicing or late interest occurs.                                                                                                                                                |
| BR-018 | Ordinary permissions do not override posted-year locks, immutable history, active hierarchy processing or protected relations. Approving users do not replace the original requester's permissions at final application.                                                                                                                                           |

### 5.1 Worked calculation examples

**Member statement:** Effective opening LKR 10,000.00, credits LKR 2,000.00 and debits LKR 500.00 produce LKR 11,500.00. Selecting a later reporting start moves earlier applicable movements into brought forward; it does not discard them.

**Quarterly deposit interest:** Month-end balances LKR 2,000.00, 3,000.00 and 1,500.00 at 8% annual interest yield 1,500 × 8 / 400 = LKR 30.00. A subsequent eligible quarter with a LKR 1,300.00 minimum yields LKR 26.00; accrued total is LKR 56.00 before later quarters.

**Loan capacity:** At a LKR 100,000.00 deposit, a 90% loan limit and LKR 30,000.00 outstanding principal, additional capacity is LKR 60,000.00. Issuing that loan does not debit the deposit balance. A loan interest charge is entered separately.

**Withdrawal:** With a mature LKR 100,000.00 balance, no unsettled loans and a 75% withdrawal setting, maximum release is LKR 75,000.00 and at least LKR 25,000.00 remains. Creating the request alone leaves the deposit unchanged.

**Age transition, January financial year:** A threshold birthday on 15 May excludes April–June. January–March uses the below-threshold rate; eligible later quarters use the from-threshold rate. The excluded quarter is not prorated.

## 6. Principal use cases

### UC-01 Register a member

**Actor:** Registration officer. **Preconditions:** Active login, Members Add, assigned district, valid active location masters and eligible officer.

1. Open registration and search/select the department; review derived district/zone/province.
2. Select a same-zone lager and maintenance officer; enter required identity/contact details.
3. Submit; the server checks permissions, district, identifiers, dates and relationships.
4. Save the member and original ledger, then show the permitted next destination. With Payment Plan Add, continue to plan setup.

**Outcome:** One member and original ledger are saved. **Exceptions:** Duplicate identity, missing officer, wrong-zone lager or invalid inputs return errors without partial registration. New registrations are outside edits-only hierarchy approval.

### UC-02 Record a contribution and review the statement

**Actor:** Financial operator. **Preconditions:** Appropriate transaction actions and district access.

1. Select member, type and date; review the saved balance preview.
2. Enter one positive credit or debit and optional reference/description.
3. Submit and receive validation/save outcome.
4. With View, select statement dates and review brought-forward, movements and final balance.

**Outcome:** One authorized entry changes derived totals. **Exceptions:** Invalid sides, unauthorized member, controlled Fix Deposit debit or closed/historical period is rejected. Editing an existing covered entry queues approval when configured.

### UC-03 Open and service a child deposit

**Actor:** Authorized child/deposit operator. **Preconditions:** Appropriate Children/Child Deposits actions and parent district.

1. Create/select a child and choose a deposit type.
2. Enter opening date/amount and review the age-dependent rate.
3. Save the referenced account and record subsequent dated debits/credits.
4. Review its own statement and balances with Child Deposits View.

**Outcome:** The child's money remains separate from parent/member accounts. **Exceptions:** Predating opening, invalid parent selection or protected opening changes fail.

### UC-04 Calculate and post annual interest

**Actor:** Interest operator and, when configured, approving officer(s). **Preconditions:** Assigned district, relevant interest actions, accurate financial inputs and an ended year for posting.

1. Choose financial year/account filters and preview quarter minima, rates and exclusions.
2. Calculate/save and review annual values.
3. Request posting after year-end. If a matching hierarchy exists, leave financial data unchanged while pending.
4. Complete approval where applicable; revalidate calculation, prior drafts and locks, then post the annual total once.

**Outcome:** A fixed account-year, any nonzero credit and carry-forward are recorded atomically. **Exceptions:** Changed review data, overlapping posted year, earlier unresolved draft or revoked access blocks posting. The separate scheduled command is subject to OD-005.

### UC-05 Service a Fix Deposit loan and withdrawal

**Actor:** Authorized Fix Deposit operator. **Preconditions:** Active account/policy, district/action permissions and current eligibility.

1. Review capacity/rules and issue a permissible loan, retaining the issue rate.
2. Record assessed interest and principal/interest repayments.
3. Settle only after both balances are zero and interest completion is confirmed.
4. After maturity, submit a permissible withdrawal request.
5. Review/release after revalidation, with approval checkpoints where configured.

**Outcome:** Loan records remain separate; release creates one controlled deposit debit and resets the cycle. **Exceptions:** Unsettled loans, exceeded limits, stale/repeated release or invalid chronology fails without partial posting.

### UC-06 Transfer a member between ledgers

**Actor:** Transfer operator and configured approver(s). **Preconditions:** Member Transfers Add, both districts, valid current ledger and eligible receiving relationships/officer.

1. Select member and review source ledger/officer.
2. Enter receiving assignment, effective date and reason.
3. Submit; queue for configured transfer approval or process immediately when none matches.
4. Revalidate and atomically freeze/close the source, create the linked receiving page and update current location.
5. Review the retained chain and permitted carried balances.

**Outcome:** One member identity continues with a new active ledger and permanent history. **Exceptions:** Stale ledger, invalid backdate, wrong-zone officer or missing district access rejects the change.

### UC-07 Decide an approval request

**Actor:** Assigned current-level approver or administrator. **Preconditions:** Active account, pending request and decision rights.

1. Open the assigned request and review submitted changes and audit.
2. Approve the current level, reject if allowed, or perform an administrator override with reason.
3. Advance to the next level or revalidate/apply once after final approval.

**Outcome:** Pending next level, approved/applied, rejected, cancelled or failed. **Exceptions:** Self-approval by an ordinary user, out-of-sequence decision, missing required reason or duplicate decision is refused. Final validation failure is audited without partial business changes.

### UC-08 Review and export Lager activity

**Actor:** Authorized report viewer. **Preconditions:** Lagers View, assigned district and relevant section View permissions.

1. Open a lager and optionally select member, activity type and dates.
2. Review permitted summary balances and saved activity.
3. Export all matching authorized records, including beyond the visible page.

**Outcome:** A current-assignment operational report. **Exceptions:** Missing section permissions suppress those data; unauthorized member/date filters do not expose unrelated finances. Use permanent ledgers for previous-office history.

## 7. Data requirements

### 7.1 Core entities and relationships

| Entity group                            | Required logical relationships / contents                                                                                                             |
| --------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| User / Group / permissions              | User has roles and direct permissions; user has zero or more authorized districts. Passwords are stored as hashes.                                    |
| District / OfficeLocation               | District stores province fields; district has many zonal offices. `OfficeLocation` is the retained internal name for Zonal office.                    |
| Subdivision / School / Lager            | Zone has many subdivisions and lagers. Department (`School`) has a district/zone and optional subdivision; lager district derives from zone.          |
| Member                                  | Belongs to district/zone/department/lager and optional subdivision; stores identity/contact, status, generated fields and pension date.               |
| PaymentType / PaymentPlan / amounts     | Shared type has rate/reference settings. Member has at most one current plan and one amount per applicable plan/type.                                 |
| PaymentPlanTemplate / amounts           | Shared template has amounts by type; applying copies values into a member plan and retains source name.                                               |
| OpeningBalance / amounts                | Member has at most one dated opening record; each saved amount identifies a payment type.                                                             |
| PaymentTransaction                      | Many dated debits/credits per member/type; creator, reference, description and timestamps retained.                                                   |
| DepositInterestYear                     | Account-year snapshot per member/type/start date; quarter details, rates, totals and optional unique annual-credit link.                              |
| Child / ChildDepositType                | Child belongs to one member. Shared type stores age threshold and two annual rates.                                                                   |
| ChildDeposit / entries / interest years | Child has many referenced accounts; each account has opening details, dated entries and independent annual interest snapshots.                        |
| FixDepositPolicy / Account              | Policy links one payment type; each member/policy pair has at most one account.                                                                       |
| FixDepositLoan / FixLoanEntry           | Account has loans; each loan has issue snapshots and assessed-interest/repayment entries, with operation token and optional settlement.               |
| FixDepositWithdrawal                    | Account has requests with pending/released/rejected state and optional linked controlled ledger debit.                                                |
| LedgerOfficerProfile                    | User has at most one profile with unique nonblank EPF and assigned zone.                                                                              |
| MemberLedger / MemberTransfer           | Member has an ordered linked ledger chain and at most one active page; transfer links one source and receiving page.                                  |
| LedgerRecord / LedgerEvent              | Logical tracked record has originating ledger; append-only events retain saved snapshots and event ledger/actor/date.                                 |
| LedgerOfficerAssignment                 | Permanent old/new officer responsibility snapshots and reasons per active ledger change.                                                              |
| ApprovalHierarchy / Level               | Configurable policy has ordered levels and approver sets; active scope uniqueness applies.                                                            |
| ApprovalRequest / AuditEvent            | Request retains payload, target fingerprint, hierarchy snapshot, progress/state and outcome; events retain permanent decisions/configuration history. |

### 7.2 Member data dictionary

Requiredness below describes normal workspace entry. Legacy nullable location columns do not waive required department/lager validation on registration/editing.

| Field                          | Requiredness / format                   | Validation / derivation                                                                                                                             |
| ------------------------------ | --------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| Member number                  | Required; up to 30 characters           | Unique; trim/uppercase.                                                                                                                             |
| Computer number                | Optional; up to 30 characters           | Unique when supplied; blank permitted for many members.                                                                                             |
| Title                          | Optional                                | Mr., Miss, Mrs., Ms. or Rev.                                                                                                                        |
| Full name                      | Required; up to 200 characters          | Stored separately from title and generated short name.                                                                                              |
| Short Name                     | Generated; read-only                    | Initials plus final name and optional title.                                                                                                        |
| Date of birth                  | Optional date                           | Not future; drives retirement planning.                                                                                                             |
| Retirement age / expected date | Generated; read-only                    | Default 60; expected date absent without known birth date.                                                                                          |
| Salary                         | Required decimal LKR, 2 places          | Nonnegative; model precision 12 total digits.                                                                                                       |
| Office / private addresses     | Required multiline text                 | Entered manually; department selection does not overwrite contact details.                                                                          |
| Mobile phone                   | Required                                | 7–15 digits with supported formatting.                                                                                                              |
| Office / home phones           | Optional                                | Same format validation when supplied.                                                                                                               |
| Admission date                 | Required date; default today            | Not future.                                                                                                                                         |
| Gender                         | Required choice                         | Male, Female, Other, Prefer not to say.                                                                                                             |
| NIC                            | Required; up to 12 characters           | Unique; 12 digits or 9 digits plus V/X; uppercase.                                                                                                  |
| Status                         | Required; default Active                | Active, Inactive or Retired; Retired requires pension date.                                                                                         |
| Pension date                   | Optional except Retired                 | Actual or scheduled date in editor; actual retirement processing rejects future date. Must follow birth when known; closed-year protection applies. |
| District / zone                | Required relationships                  | Authorized and consistent with department and lager.                                                                                                |
| Department / lager             | Required relationships                  | Valid active selection or retained historical selection during editing.                                                                             |
| Subdivision                    | Optional relationship                   | Must match selected zone when supplied.                                                                                                             |
| Province full / short name     | Derived display                         | Comes from district; not independently entered on member form.                                                                                      |
| Maintenance officer            | Required for new workspace registration | Active user with EPF, matching zone and authorized district; later change uses ledger workflow.                                                     |

### 7.3 Integrity, retention and migration

The database shall enforce applicable uniqueness, foreign-key, amount-side and state constraints in addition to server validation. Financial workflows shall use atomic transactions and appropriate record locks where coordinated writes are required. Application-authorized integrations shall use supported model/service workflows so validation and ledger bookkeeping run; bulk/direct database writes do not carry these guarantees.

Protected records shall not be deleted merely to remove them from selection. Inactive flags retain history. Posted interest snapshots, transfer histories and permanent audit events have stronger immutability rules than ordinary editable data. The system does not establish a complete organization-wide retention period or purge policy; OD-008 records that decision.

Existing migration logic shall preserve member and financial identities/amounts when introducing ledgers or splitting a legacy lager across zones. Ambiguous legacy lager-to-zone assignments require an explicit mapping and shall not be guessed. Unknown officer identities, titles and birth dates shall remain unknown until supplied. Application migrations are not a general-purpose external bulk-import interface.

### 7.4 Initial reference data

Migration seeds provide 25 Sri Lankan district records with provinces. Initial member payment types are Special Deposit, Fix Deposit, Share, Pension, Scholarship, Scholar, Funeral and Danaayojana; Scholarship and Scholar are distinct. These records remain configurable under their master-data permissions.

The initial Child Savings type uses age 18 as the transition threshold, 8% below that age and 5% from it. Initial Fix Deposit Rules use a 36-month cycle, 75% withdrawal maximum, 90% loan security and 12-month loan interval; the loan interest rate must be supplied before issuing a loan. Member payment-type deposit rates default to 0% until configured. Seed values describe initial setup, not verified current production settings or final policy approval.

## 8. External and user interfaces

### 8.1 User interface

The interface shall provide the branded EDCS workspace with responsive navigation, maroon/gold styling, readable forms, search controls, tables, pagination and browser print views. Generated location/identity fields shall be visibly read-only. Form errors shall identify required or invalid values; unauthorized actions shall be hidden and rejected by the server.

The current design uses 44px standard action buttons, 52px single-line inputs and multiline fields starting at 120px, with visible keyboard outlines and textual required/error indicators. These describe the reviewed UI, not certification against an accessibility standard.

### 8.2 Browser-facing interface inventory

| Area                    | Representative paths                                                                                          | Purpose                                                                   |
| ----------------------- | ------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------- |
| Authentication          | `/login/`, `/logout/`, `/session/activity/`                                                                   | Sign-in/out and activity keepalive.                                       |
| Administration          | `/admin/`, `/admin/django/`                                                                                   | Custom control center and restricted advanced administration.             |
| Members / masters       | `/members/`, `/districts/`, `/offices/`, `/subdivisions/`, `/schools/`, `/lagers/`                            | Operational records; department routes retain the internal school naming. |
| Plans / templates       | `/payment-plans/`, `/payment-plan-templates/`, `/payment-types/`                                              | Agreements, shared templates and payment-type settings.                   |
| Openings / transactions | `/opening-balances/`, `/debit-credit/`, `/debit-credit/balances/`                                             | Starting balances, dated entries and member balance reports.              |
| Children                | `/children/`, `/child-deposits/`, `/child-deposit-types/`                                                     | Child identities, savings accounts and age-rate settings.                 |
| Interest                | `/deposit-interest/` and child-account subpaths                                                               | Preview, saved years and annual posting.                                  |
| Fix Deposit             | `/fix-deposits/`, `/fix-deposit-loans/`, `/fix-deposit-withdrawals/`, `/fix-deposit-rules/`                   | Account, loan, withdrawal and policy workflows.                           |
| Permanent records       | `/member-ledgers/`, `/member-transfers/`                                                                      | Linked ledger pages, responsibility and transfer history.                 |
| Approvals               | `/approvals/`, `/approval-hierarchies/`                                                                       | Submission/decision queue and configuration/history.                      |
| Lookup/status endpoints | `/api/locations/`, `/api/offices/`, `/api/debit-credit/balances/`, child balance and Fix Deposit status paths | Authorized dynamic choices and as-of previews.                            |

Paths are representative route families; some are detail/action prefixes rather than standalone list pages. These are internal browser endpoints, not a published third-party REST contract.

### 8.3 Email and file interfaces

Welcome email shall use the configured sender, SMTP host/port and optional TLS/SSL mode, or another configured Django backend. A failed delivery shall not roll back a committed user account. Password distribution is a separate administrator responsibility.

CSV exports shall use the relevant report filters and permission scopes and neutralize spreadsheet formula injection prefixes. Browser profile printing is supported; this baseline does not assume built-in PDF export for every financial report or a general file-import facility.

### 8.4 Scheduling and deployment interfaces

`process_deposit_interest --post-ended` shall invoke calculation/posting without a browser. `--from-year` permits an explicit historical start. `install_interest_schedule` displays the managed cron entry; `--install` installs it. Installed entries require a running scheduler/host, and project relocation requires schedule maintenance.

Production deployment shall use configured environment variables, a production WSGI/ASGI server, PostgreSQL and collected static assets. Secrets and administrator bootstrap credentials shall remain outside committed documentation. Application code does not itself establish a complete hosting, backup or monitoring service.

## 9. Nonfunctional requirements

### 9.1 Baseline security and correctness

| ID / status      | Requirement                                                                                                                                                                              | Verification approach                                                 |
| ---------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------- |
| NFR-001 Baseline | Authorization shall be enforced on the server for pages, forms, identifiers, lookup data and exports, including assigned-district boundaries.                                            | Negative access tests with multiple districts and direct requests.    |
| NFR-002 Baseline | Passwords shall be hashed; framework password validators and CSRF validation shall be applied to applicable account and mutation flows.                                                  | Account/password tests and missing/invalid-CSRF requests.             |
| NFR-003 Baseline | Authenticated pages shall be non-cacheable; secure cookie flags shall be enabled in non-debug configuration, with HTTP-only session cookies and frame denial.                            | Response/header and production-configuration inspection.              |
| NFR-004 Baseline | Financial calculations shall use decimal arithmetic, defined cent rounding and consistent as-of rules; coordinated posting/transfer/approval changes shall be atomic.                    | Boundary-value, duplicate-operation, rollback and concurrency tests.  |
| NFR-005 Baseline | Permanent ledger/approval histories and posted annual results shall resist ordinary edits/deletes, with snapshots preserving past actor/officer identity.                                | History mutation and closed-year tests; admin inspection.             |
| NFR-006 Baseline | Changes to user status, permission grants and district assignments shall affect subsequent requests without requiring a fresh login.                                                     | Existing-session access-change tests.                                 |
| NFR-007 Baseline | CSV values capable of spreadsheet formula interpretation shall be neutralized in supported exports.                                                                                      | Export hostile-prefix fixture values.                                 |
| NFR-008 Baseline | Genuine activity shall be batched rather than sent per keystroke; current heartbeat interval is min(30 seconds, timeout / 3). No new interaction shall mean no ongoing keepalive stream. | Browser timer/activity tests and request observation.                 |
| NFR-009 Baseline | Account welcome-email failure shall preserve the saved account; isolated scheduler account errors shall not prevent unaffected account processing.                                       | Failure-path tests and controlled backend errors.                     |
| NFR-010 Baseline | Application dates shall use Asia/Colombo business time; saved financial years shall retain explicit dates despite later configuration changes.                                           | Date-boundary and saved-period tests.                                 |
| NFR-011 Baseline | The UI shall offer keyboard-visible focus, explicit labels/errors and responsive/print layouts, without requiring an external CDN at runtime.                                            | Browser inspection at agreed viewport sizes and keyboard walkthrough. |

### 9.2 Proposed operational acceptance requirements

These are requirements to agree before production acceptance. No capacity, latency, uptime, recovery or compliance level has been demonstrated by this source review.

| ID / status      | Proposed requirement                                                                                                                                   | Decision / evidence needed                                                                                                   |
| ---------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------- |
| NFR-012 Proposed | The deployment shall define supported browsers, operating environments and mobile viewport sizes, then pass principal workflow checks on them.         | Approve compatibility matrix; run browser checks.                                                                            |
| NFR-013 Proposed | Interactive pages/lookups and financial reports shall meet agreed percentile response-time limits under an agreed workload.                            | Define active users, member/account/transaction volumes, report ranges, hardware and latency targets; load test.             |
| NFR-014 Proposed | Service availability, maintenance windows and incident responsibilities shall be documented and monitored.                                             | Agree service hours, availability objective and escalation owner.                                                            |
| NFR-015 Proposed | Automated database backups and restoration drills shall meet agreed recovery-point and recovery-time objectives, including retained ledger/audit data. | Specify backup frequency, retention, encryption, RPO/RTO and restoration evidence.                                           |
| NFR-016 Proposed | Scheduler failure, repeated posting errors and absent scheduled runs shall be detectable by the technical owner.                                       | Define alert destination, expected completion window and log retention. The supplied log file alone is not an alert service. |
| NFR-017 Proposed | Production shall use HTTPS, restricted database/network access, protected secrets and deployment review with debug disabled.                           | Verify actual hosting configuration and deployment checks.                                                                   |
| NFR-018 Proposed | Sensitive member/child data access, retention and any lawful disclosure/deletion process shall follow approved organizational policy.                  | Confirm data owner and applicable obligations; no compliance certification is implied.                                       |
| NFR-019 Proposed | A released version shall have reviewed migrations, successful required regression checks and a documented rollback/recovery approach.                  | Release checklist and test/migration results for the exact candidate.                                                        |

### 9.3 Session traffic characteristic

With the default timeout and continuous activity, the current implementation sends up to approximately 120 activity requests per hour per open tab. Cross-tab activity may keep several tabs sending; it does not currently elect one sender. A 204 response has no body but still transfers headers/cookies and performs session work. The local one-second timer is not itself a network request.

Reducing traffic through a longer interval or cross-tab sender coordination is a possible future change under OD-006. This SRS documents current behavior and does not change it.

## 10. Verification and acceptance

### 10.1 Verification approach

Each FR/BR is an acceptance expectation, supported by the source/test mapping in Section 12. Existing test definitions provide evidence of intended coverage, not proof of a current passing run. Acceptance shall combine functional tests, negative authorization tests, financial examples, concurrency/rollback checks and stakeholder user acceptance on a representative nonproduction dataset.

### 10.2 Acceptance scenarios

| ID / related requirements | Scenario                                                                                                                                      | Expected result                                                                                                                                                 |
| ------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| AC-001 / FR-001–008       | Sign in, work continuously on a form, then leave all tabs idle for the configured timeout; repeat across tabs and after device sleep.         | Activity preserves the working session; unattended/expired sessions return to sign-in and cannot be revived by a late event.                                    |
| AC-002 / FR-009–014       | Give an ordinary user Members View/Add in district A only; try edits, administration and district B URLs/forms/exports.                       | Only granted actions in A succeed; staff status or role permissions do not bypass district/administration controls.                                             |
| AC-003 / FR-014–015       | Create a user with/without email; simulate SMTP failure; attempt removal of an officer responsible for active ledgers.                        | Email behavior matches conditions; saved user survives failure; responsibility must be reassigned before disqualifying the officer.                             |
| AC-004 / FR-016–032       | Register a valid member, then attempt duplicate NIC/member number, wrong-zone lager, negative salary and missing officer.                     | Valid registration creates identity/original ledger; invalid cases have field errors and no partial records.                                                    |
| AC-005 / FR-027,033–035   | Change title/name/birth date; test leap-day expected retirement; process early retirement and try altering a closed year's pension exclusion. | Generated identity/date are correct; actual retirement preserves accounts; posted eligibility cannot be rewritten.                                              |
| AC-006 / FR-036–045       | Apply a plan template to two members, customize one, then change/delete the template.                                                         | Each member retains independent amounts and source name; no deposit is created from a plan.                                                                     |
| AC-007 / FR-046–057       | Save opening 10,000, credit 2,000 and debit 500, then vary date range and pagination; include pre-opening history.                            | Final 11,500 under the stated dates; brought-forward/running values stay consistent and pre-opening amounts are not double-counted.                             |
| AC-008 / FR-050–057       | Submit both debit/credit positive, both zero, a foreign-district identity and an attempted member/type move on edit.                          | Each invalid/unauthorized operation fails without exposing other account data or moving an existing entry.                                                      |
| AC-009 / FR-058–066       | Open sibling accounts and two accounts of one type; add entries and change master rates.                                                      | Accounts and parent finances remain separate; saved rates remain unless an allowed entry date change refreshes them.                                            |
| AC-010 / FR-067–071       | Use quarter balances 2,000/3,000/1,500 at 8%; test incomplete and negative quarters, pension date and child threshold birthday.               | Eligible quarter gives 30.00; pending/nonpositive and exclusion outcomes match BR-010–013.                                                                      |
| AC-011 / FR-072–076       | Post an ended year twice, alter data after review, attempt a closed-year edit and run the processor repeatedly.                               | One annual credit maximum; stale/closed changes are blocked; zero year closes without a zero credit; repeated processing does not duplicate credits.            |
| AC-012 / FR-077–083       | At balance 100,000 and outstanding principal 30,000, request another 60,000 then excess; repay beyond due; replay a posting token.            | Capacity is 60,000 at 90%; excess fails; deposit remains unchanged by loans; replay posts once and conflicting replay fails.                                    |
| AC-013 / FR-084–087       | Request/release a 75% withdrawal before/after maturity, with unsettled loans, and twice; simulate failure during release.                     | Only eligible release debits once and resets cycle; rejection/request alone does not debit; failure rolls back coordinated changes.                             |
| AC-014 / FR-088–102       | Transfer a member with child accounts, plans, loans and transactions; inspect IDs, carried amounts and both office views.                     | IDs/financial values persist, source is frozen, receiving page is active, later postings use receiving ledger, and visibility follows historical/current rules. |
| AC-015 / FR-094–102       | Change officer, edit the officer's name, try unsafe backdating and rewrite/delete a source record.                                            | Handover snapshots retain original identities; unsafe dates and permanent-history rewrites fail.                                                                |
| AC-016 / FR-103–109       | Configure two approval levels, submit an edit, try self/out-of-order approval, then approve each level.                                       | No early business update; only eligible decisions succeed; request progresses and audits identity/reason/version.                                               |
| AC-017 / FR-106–114       | Submit duplicate edits, revoke requester rights/change target while pending and make concurrent final decisions.                              | One pending identical request; stale/revoked application fails cleanly; final business write occurs at most once.                                               |
| AC-018 / FR-104–114       | Exercise global/module-specific policies, create/delete, new repayment and administrator override.                                            | Module override wins; creates/deletes/new repayment remain normal; covered existing-record processing queues; override needs reason and is audited.             |
| AC-019 / FR-115–120       | Open Lager activity with partial module View rights, filter dates and export beyond one page; move a member's current lager.                  | Only permitted sections appear, totals avoid duplicate money, export matches filters, and current-assignment activity follows the member.                       |
| AC-020 / NFR-001–011      | Attempt missing-CSRF mutations, formula-prefixed exports, cached authenticated pages and direct audit editing.                                | Requests/exports/headers/history protections follow the baseline; no unauthorized data is returned.                                                             |

### 10.3 Acceptance exit conditions

The business owner shall approve scope, financial rules and access behavior; the technical owner shall approve deployment and operational decisions. Release acceptance requires documented outcomes for applicable scenarios, no unresolved defect that corrupts balances or bypasses authorization, and an agreed treatment of all open decisions. Proposed service targets cannot be marked met until their values and test conditions are agreed and measured.

## 11. Assumptions, limitations and open decisions

### 11.1 Assumptions and observed boundaries

- The current working tree is the source baseline, including uncommitted additions. It may differ from any deployed version or applied database migrations.
- EDCS is the name shown by the product. Its full legal organization name, project sponsor and document signatories were not supplied and are not invented here.
- Officers enter authoritative identity, money, dates and rates. No integration independently verifies a NIC or confirms bank settlement.
- Deposit rates/frequencies are configurable, but the implemented interest engine uses quarterly minimum month-end balances. Other frequency/custom-period settings are reference metadata, not alternative automatic interest engines.
- Loan interest is assessed by the bank/operator and entered as charges; the recorded annual loan rate does not establish an automatic daily/amortization calculation.
- Permanent history is protected in supported application workflows, not a cryptographically sealed or independently immutable database. Privileged database/bulk writes can bypass application hooks.
- Lager activity reports saved records by current assignment; it is not a complete audit of all past changes/deletions. Dedicated ledger and approval histories have their stated scope.
- Session cookies may be restored by browser session restoration. Browser close is not a guarantee of explicit server-side logout; inactivity limits remain relevant.
- No automatic backup, off-site restore, availability guarantee, MFA, SSO or comprehensive security/compliance certification is established by the reviewed sources.

### 11.2 Decision register

| ID     | Decision / clarification needed                                                                                                                                                                                                                                                    | Effect / proposed owner                                                                                                                         |
| ------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| OD-001 | Confirm official system/organization name, sponsor, document owner and signatories.                                                                                                                                                                                                | Final cover and approval; business owner.                                                                                                       |
| OD-002 | Confirm operational role names, district assignments and whether administrator self-approval/override is acceptable.                                                                                                                                                               | Access and separation of duties; business/security owner.                                                                                       |
| OD-003 | Approve contribution types, annual rates, Fix Deposit limits, age thresholds and retirement/pension rules.                                                                                                                                                                         | Financial correctness; finance owner. Seed/default values are not a substitute for policy approval.                                             |
| OD-004 | Confirm whether transition exclusions follow financial-year quarters when the configured start is not January. Source groups quarters from that start, while README uses calendar-quarter wording. Child year-end posting is the saved financial-year end, not always 31 December. | Avoid inconsistent non-calendar-year interpretation; finance owner.                                                                             |
| OD-005 | Decide whether unattended annual posting may bypass manual approval hierarchies. Current command posts through services directly, outside browser approval checkpoints.                                                                                                            | Production scheduler policy; finance and technical owners. If approval is mandatory, coordinate the schedule before enabling automatic posting. |
| OD-006 | Decide whether to reduce heartbeat traffic through interval tuning or one sender across tabs. Current baseline remains about every 30 seconds per active tab.                                                                                                                      | Network/session tradeoff; technical owner.                                                                                                      |
| OD-007 | Set supported browsers, expected data volumes, concurrent users, response-time and availability targets.                                                                                                                                                                           | Measurable performance/compatibility acceptance; product and hosting owners.                                                                    |
| OD-008 | Define member/child data retention, financial-history retention, backup retention and lawful correction/disposal processes.                                                                                                                                                        | Governance and storage policy; business/data owner.                                                                                             |
| OD-009 | Assign backup/restore ownership and approve RPO/RTO, alerting and disaster recovery.                                                                                                                                                                                               | Operational readiness; hosting owner.                                                                                                           |
| OD-010 | Confirm deployed release, applied migrations, legacy lager mappings and outstanding original officer assignments.                                                                                                                                                                  | Migration/readiness reconciliation; technical and operations owners.                                                                            |
| OD-011 | Decide whether the separate optional future integrations listed in Section 1.4 are required for a later phase.                                                                                                                                                                     | Scope/change control; sponsor.                                                                                                                  |

Open decisions do not indicate that application changes were made while preparing this document.

## 12. Source traceability and glossary

### 12.1 Reviewed evidence

This document was derived from local repository material. Primary evidence includes model fields/constraints, forms, permission/scoping helpers, routed views, domain services, templates and management commands. README statements were cross-checked against these sources for the principal rules. Test definitions were inspected for acceptance intent. No production records, private credentials or personal member data are reproduced.

| Requirements                    | Primary implementation sources                                                                                                                                                                                                         | Existing verification sources                                                            |
| ------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------- |
| FR-001–008; NFR-003,008         | `config/settings.py`, `config/views.py`, `config/middleware.py`, `config/context_processors.py`, `static/js/session.js`                                                                                                                | `config/tests.py`, `tests/session.test.cjs`                                              |
| FR-009–015; NFR-001,002,006,009 | `members/access_views.py`, `access_forms.py`, `permissions.py`, `admin_site.py`, `notifications.py`                                                                                                                                    | `members/test_permissions.py`, `test_district_access.py`, `test_notifications.py`        |
| FR-016–032; BR-001–004          | `members/models.py`, `forms.py`, `views.py`, member templates                                                                                                                                                                          | `members/tests.py`, `test_district_access.py`, `test_member_identity.py`                 |
| FR-033–035; BR-005              | `members/member_identity.py`, `retirement_views.py`, member model/forms                                                                                                                                                                | `members/test_member_identity.py`                                                        |
| FR-036–045; BR-006              | `members/payment_views.py`, `payment_forms.py`, `payment_template_views.py`, plan/template models                                                                                                                                      | `members/test_payment_plans.py`, `test_payment_templates.py`, `test_interest_periods.py` |
| FR-046–057; BR-007–009          | `members/opening_balance_views.py`, `opening_balance_forms.py`, `transaction_views.py`, `transaction_forms.py`, `transaction_balances.py`                                                                                              | `members/test_opening_balances.py`, `test_transactions.py`                               |
| FR-058–066; BR-013              | Child models; `members/child_views.py`, `child_forms.py`, `child_services.py`                                                                                                                                                          | `members/test_child_deposits.py`                                                         |
| FR-067–076; BR-010–013          | `members/deposit_interest.py`, `child_interest.py`, `interest_views.py`, `management/commands/process_deposit_interest.py`, `install_interest_schedule.py`                                                                             | `members/test_deposit_interest.py`, `test_child_interest.py`, `test_interest_periods.py` |
| FR-077–087; BR-014–016          | Fix Deposit models; `members/fix_deposit_services.py`, `fix_deposit_forms.py`, `fix_deposit_views.py`                                                                                                                                  | `members/test_fix_deposits.py`                                                           |
| FR-088–102; BR-017              | `members/transfer_models.py`, `transfer_services.py`, `transfer_forms.py`, `transfer_views.py`, `ledger_tracking.py`                                                                                                                   | `members/test_member_transfers.py`, `test_transfer_migration.py`                         |
| FR-103–114; BR-018              | `members/approval_models.py`, `approval_forms.py`, `approval_scope.py`, `approval_services.py`, `approval_views.py`, approval checkpoints/admin guards                                                                                 | `members/test_approvals.py`                                                              |
| FR-115–120                      | `members/views.py`, `lager_activity.py`, `lager_views.py`, common templates and CSS                                                                                                                                                    | `members/test_lager_activity.py`, `tests.py`, `config/test_error_pages.py`               |
| Data migration requirements     | `members/migrations/0020_lager_zonal_office.py`, `0021_paymentplantemplate_member_date_of_birth_and_more.py`, `0022_ledgerofficerprofile_memberledger_ledgerrecord_and_more.py`, `0023_approval_hierarchies.py`, `_ledger_backfill.py` | `members/test_lager_migration.py`, `test_transfer_migration.py`                          |
| Operational constraints         | `README.md`, `requirements.txt`, `package.json`, `config/settings.py`, scheduler commands                                                                                                                                              | Deployment/restore/load testing remains separate acceptance work.                        |

Where a table omits the `members/` prefix after its first filename, subsequent filenames in the same cell belong to that directory. Sources are relative to the repository root. This is group-level traceability; individual requirement IDs and acceptance scenarios remain stable review references.

### 12.2 Available verification commands

The following are documented project checks, to be run against an appropriate development/test environment:

```text
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test members
python manage.py test config
npm run test:session
```

Integration tests need a separate PostgreSQL test database and the required database privileges. This document does not represent these commands as having passed for the release. Do not run the interest posting or schedule installation commands on production merely to validate this document.

### 12.3 Glossary

| Term              | Meaning in this document                                                                           |
| ----------------- | -------------------------------------------------------------------------------------------------- |
| EDCS              | Product/organization identifier used by the application; full legal expansion awaits confirmation. |
| SRS               | System Requirements Specification.                                                                 |
| LKR               | Sri Lankan rupees.                                                                                 |
| NIC               | Member national identity card identifier.                                                          |
| EPF               | Employee identifier recorded as EPF number for ledger responsibility and audit identity.           |
| Zonal office      | Organizational office under a district; internal model `OfficeLocation`.                           |
| Department        | Department/school organizational master; internal model and some routes use `School`.              |
| Lager             | Named member grouping under a zonal office; product spelling retained.                             |
| Fix Deposit       | Product label for the configured deposit-backed loan/withdrawal module; spelling retained.         |
| Payment type      | Configurable member contribution/account category, such as Share, Pension or Special Deposit.      |
| Payment plan      | Monthly contribution agreement; not proof of an actual deposit.                                    |
| Opening balance   | Dated starting amount per member/payment type, separate from monthly plans.                        |
| Brought forward   | Effective starting amount plus applicable movements before the chosen statement period.            |
| Active ledger     | Current permanent office responsibility page for one member.                                       |
| Historical ledger | Frozen source page retained after transfer, with predecessor/successor continuity.                 |
| Posting           | Recording a financial result in the appropriate account ledger.                                    |
| Idempotent        | Repeating the same supported operation does not create duplicate financial application.            |
| Atomic            | Coordinated database changes succeed together or roll back together.                               |
| CSRF              | Cross-site request forgery; protected browser mutations require a valid token.                     |
| RPO / RTO         | Recovery point objective / recovery time objective; values await agreement.                        |

**End of draft — stakeholder review and approval pending.**
