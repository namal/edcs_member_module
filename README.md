# EDCS — Member Management

A complete Django, HTML and Tailwind CSS application backed by PostgreSQL. Includes a responsive dashboard, member registration and profiles, district, zonal office, subdivision, department and lager masters, search, filters, CSV exports, print profiles and authenticated access.

## Local application

The local PostgreSQL database is `member_module`. Credentials are stored in `.env`, which is excluded from version control. The provided password has been verified against PostgreSQL.

```bash
source .venv/bin/activate
python manage.py runserver
```

Open **http://127.0.0.1:8000/**. Sign in with the initial administrator credentials saved in the local `.admin-credentials` file. Administrators land in the custom Tailwind control center at `/admin/`; other users land in their workspace. The panel uses the EDCS logo, burgundy and gold palette, and responsive navigation. Advanced Django administration is available at `/admin/django/`, including password changes at `/admin/django/password_change/`.

Sessions expire after 15 minutes of inactivity and use cookies that expire when the browser closes. Set `SESSION_IDLE_TIMEOUT` in `.env` to change the inactivity limit in seconds, then restart Django. Keyboard, pointer, touch and scroll activity across open tabs keeps the session alive, including while filling in a form. Idle pages return to sign-in automatically; the server also rejects expired sessions when JavaScript is unavailable. Authenticated responses are marked as non-cacheable. Browsers configured to restore their previous session may restore session cookies after closing; the inactivity limit still applies. Closing a single tab does not sign out other open tabs.

## Users, roles and permissions

Only active superusers can open the control center, **Users & access**, and **Roles & permissions**. Staff status alone does not grant workspace access. Existing users, passwords and assigned permissions are retained.

In **Users & access**, create or edit accounts, reset passwords, activate/deactivate users, assign roles, select districts, and select direct permissions. Each module has independent **View**, **Add**, **Edit** and **Delete** checkboxes, including Payment Plan. Edit uses Django's `change` permission. Server checks apply to both page visits and submitted requests; unavailable navigation and actions are hidden. Registration users can use location lookups without gaining access to master-data management. Member viewing also includes the dashboard, CSV export and profile printing.

District assignments restrict the records a user can work with. An administrator can select one or many districts in the user's **District access** section. For example, Colombo with **Members → View, Add, Edit and Delete** allows those actions only on Colombo records. Selecting Colombo and Kandy extends the same module actions to both districts. Roles and direct permissions grant actions; they never expand the assigned districts. With no districts assigned, an ordinary user has no district record access. Assign districts to existing non-administrator accounts after applying the district access migration; none are assigned automatically.

District restrictions cover members, districts, zonal offices, subdivisions, departments, lagers and member payment plans, including forms, embedded dropdown data, searches, dashboard totals, CSV exports and location lookups. Out-of-district record URLs and forged submissions cannot bypass the restriction. Assignment changes take effect on the next request in an existing session.

Active superusers have all actions in every district, including future districts, without assignments. Only administrators can create new districts or change the shared Payment Types master. Advanced Django administration at `/admin/django/` is also reserved for administrators, preventing staff accounts from bypassing district restrictions or changing their own access.

For the `edcs` account, select its authorized districts and keep only **Members → View** and **Members → Add** selected, with no additional grants from roles. This allows browsing and registration in those districts while blocking editing, deletion, master modules and administration.

To share this access, create a **Member registration** role with the same two permissions, then assign it to users. Roles use Django groups, and effective access combines all roles with direct permissions. Remove a permission from every source to revoke it. Saved changes apply on the next request; inactive accounts lose access even with an existing session. The user editor shows current effective access beside the direct-permission matrix.

User and role changes are recorded in the control center's activity feed. Deletion requires confirmation and a POST request. The panel prevents deleting superusers, deactivating superusers and deleting your own account. New accounts are ordinary workspace users; staff/superuser flags and advanced permissions are preserved and managed through Django administration.

New users receive a welcome email after the account and permissions are saved when an email address is provided. Blank email addresses skip delivery. This applies to both **Users & access** and the advanced Django user administration. The message contains the username and sign-in link; provide passwords separately. Editing an existing account does not send another welcome email. Delivery failures show a warning while preserving the saved account.

Configure `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD` and `DEFAULT_FROM_EMAIL` in `.env` using your SMTP provider's settings. Use `EMAIL_USE_TLS=true` for STARTTLS (typically port 587), or `EMAIL_USE_SSL=true` for implicit TLS (typically port 465); enable only the option your provider requires. `.env.example` lists all supported settings. Restart Django after changing them. For local email previews without delivery, set `EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend` to print messages to the server console.

## Workspace setup

All 25 Sri Lankan districts are provided as editable master records with provinces. Run `python manage.py migrate` after updating to apply the location hierarchy migration; it preserves existing districts, offices and members.

1. In **Districts**, create or edit each district with its required full province name and a separate editable province short name (for example, Western and WP).
2. Open **Zonal offices → Add zonal office** and select its district. Add optional subdivisions under **Subdivisions**.
3. Open **Departments** to add a department code and name, district, zonal office and optional subdivision. Filter departments by district or search by name/code. The District master links to department counts per district.
4. Open **Lagers** and select a district, then one of its zonal offices. Add a lager name and unique code. A zonal office can have many lagers; each lager shows its registered member count with a link to those members. Zonal offices also link to their lager lists and the Add lager form.
5. Open **Member directory → Register member**. Department and lager are mandatory. Type a department name or code directly in the **Department** combo to filter its alphabetical dropdown, then click a result or use the arrow keys and Enter to select it. Choosing a district narrows departments to that district; choosing a zonal office narrows them further. Clear the text or location selections to broaden the list. You can also select a department first to fill its district, full province name, short name, zonal office and optional subdivision from the saved relationships. Select a lager from the department’s zonal office. Changing the zone clears a lager from another zone. Department labels include the department name, full province name with its short name in parentheses, district, zonal office and subdivision when present.
6. Enter the member information, including the required office address. Search and filter the directory, export records, or open a profile to edit and print it.

## Configurable approval hierarchies

Apply migration `0023_approval_hierarchies`, then open **Approval hierarchies** as a system administrator. Approvals apply only to **Member Transfers** and **editing existing records across workspace modules**. Choose **Member Transfers → Member transfer**, or **All modules — edits only → Edit existing records** to use one hierarchy throughout the workspace. A module-specific edit hierarchy takes priority over the global edit hierarchy. Add numbered levels, designate active users, and choose whether each level may reject. Any one designated user completes a level; requests advance in order. Up to 20 levels are supported. Configuration changes require a reason and retain before/after details and approver identities in permanent history.

Configured transfer and edit forms validate their inputs and submit an **Approval request** before changing saved data. Edits include existing member, child, deposit, debit/credit, opening-balance, payment-plan/template, ledger-officer and master records, plus processing existing records such as retirement, loan settlement, withdrawal review and annual interest posting. Other create and delete actions follow the existing process without hierarchy approval, including registration, new deposits, loan issuance, new repayment/interest entries and saving new interest calculations. This restriction is enforced on the server even for older broad or create/delete hierarchy settings. Modules without an active matching hierarchy retain immediate processing. Passwords, login and user/role administration remain under the administrator's existing security controls rather than entering the business approval queue.

**Approvals** shows users their own submissions and requests assigned to them. Only the current level's designated users may approve; rejection must also be enabled for that level. Ordinary users cannot approve their own submissions. Administrators can approve or reject at any level, including their own requests, and **Approve all remaining levels** to override the sequence. Every administrator decision, rejection and cancellation requires a reason. The permanent audit retains the employee's name/EPF when recorded, level, decision, time, reason, override status and final application outcome. Requesters may cancel pending submissions; rejected/cancelled requests do not change business data.

Final approval applies the validated submission once in a database transaction using the original requester's current permissions and the existing domain rules. Concurrent/double decisions cannot apply it twice. Changed target records, related account policies, missing records, revoked permissions or failed validations stop application and preserve a failure audit; review current data and submit a new request. Template defaults are copied at submission so later template edits cannot change the approved amounts. Financial entries retain their original posting author; ledger update events identify the requester and the approval audit identifies the approving officer. Requests keep the submitted hierarchy version and assignments after configuration changes; deactivate a user to revoke their approval access immediately, and use administrator rejection/override to resolve affected requests. Advanced Django administration exposes approval records read-only and blocks direct edits covered by a hierarchy, while other creates/deletes keep their existing permissions and safeguards. Live integrations must use the workspace approval checkpoints; privileged database/bulk writes do not implement business approval.

All standard action buttons use a common 44px height and 14px label, including ledger page actions, forms and pagination. Buttons do not stretch with taller adjacent content. Single-line inputs, dropdowns and searchable/disabled fields use a consistent 52px height; multiline text boxes start at 120px and can be resized vertically. Member Transfers appears directly below Child Deposits in the workspace navigation.

## Member Transfers and permanent ledgers

Run `python manage.py migrate` to apply `0022_ledgerofficerprofile_memberledger_ledgerrecord_and_more`. Existing members receive original ledger references at their currently recorded zonal offices. Existing member and financial rows retain their IDs and values; ledger records archive their saved details. Previous unrecorded transfers cannot be reconstructed. No employee EPF or original officer is guessed: those ledger pages are marked **Officer assignment required** until an administrator records the responsible employee.

1. In **Users & access**, enter the employee's **EPF number** and **Ledger officer zonal office**, and grant access to that district. EPF numbers are unique. Only active users with an EPF and the matching office/district can be chosen as maintenance officers. Administrators grant **Member Ledgers** View/Add/Edit and **Member Transfers** View/Add independently. Permanent ledger/transfer histories have no Edit/Delete actions; ledger Edit means a recorded officer responsibility change.
2. For an existing member, use **Member Ledgers → Assign original ledger officer**, or **Change officer**, to complete the original responsibility record. New workspace registrations require a zone-matched **Ledger Maintenance Officer** and automatically create their original ledger. Direct imports with no known officer remain flagged for assignment.
3. Open **Member Transfers → Transfer member**, or use the member profile's **Transfer member** action. Select the existing member, review their current ledger/officer, then select the receiving district, zonal office, department/school, lager, optional subdivision, maintenance officer, effective date and reason. The operator needs assigned access to both source and destination districts. School changes within the same zone can also use this workflow.
4. Transfers default to today. Earlier dates are allowed only on/after the current page's opening and latest recorded activity; future dates and conflicting/stale submissions are rejected. Later transfers must be after the previous transfer date. Existing same-day activity stays on its original page; subsequent postings use the new page, distinguished by permanent references and recorded timestamps.
5. Completing a transfer locks the member while closing the original page, freezing its balances and member information, creating the linked receiving page, recording the new officer and saving the transfer history atomically. Member/child/account/loan IDs, registration identifiers, plans, transactions, loan cycles and financial rules remain intact. Carried amounts are ledger snapshots, not duplicate transactions or replacement member opening-balance records.
6. **Member Ledgers** shows original/receiving references, current/historical status, office and school, officer name/EPF at assignment, the complete ledger sequence, carried/closing balances, permanent record snapshots and officer handover history. Select **Include previous linked ledgers** to inspect predecessor activity; export permitted records to CSV. Source offices retain their own historical pages and frozen balances. The receiving district can follow the member's preceding pages for continuity; source-only users cannot see later receiving-page transactions.

Financial sections keep their existing View permissions (Debit / Credit, Opening Balances, Payment Plan, Child Deposits, Fix Deposit, Quarterly Interest). Ledger access alone does not reveal financial values. Members → View additionally permits retained registration details. Current member lists and ordinary financial screens follow the member's current district; historical-office reporting uses the permanent ledger pages instead. Debit/credit, child statements and loan repayments show their ledger references. Historical transaction edit/delete actions are hidden and blocked on the server. Posted-interest protections continue to apply.

Financial saves and deletions append ledger events with their saved values, financial date, recorded time and actor identity/EPF when known. Frozen original transactions/openings cannot be rewritten or deleted. Existing plans and account/loan servicing can continue; their earlier ledger snapshots remain available. Late annual interest credits are posted to the current ledger while retaining their financial-year date; this does not rewrite the source ledger's frozen closing snapshot. Normal new postings cannot be backdated before the current transfer date. Live integrations must use the model save/service workflows; bulk SQL updates or `bulk_create` bypass model bookkeeping and must not be used for live postings.

Changing the maintenance officer requires an active ledger, a matching receiving-office user, and a reason. The previous name/EPF, new name/EPF, actor and date are retained. Original historical pages cannot be reassigned. User-profile corrections do not rewrite earlier assignment snapshots; use **Change officer** to record a refreshed assignment when needed. Active ledgers must be reassigned before an officer is deactivated, moved to another office, or loses required district access. Users and members referenced by permanent ledger history cannot be deleted; deactivate accounts/members to retain traceability. Advanced Django administration exposes ledger/transfer audit records read-only.

## Payment Plan and configurable payment types

Run `python manage.py migrate` to add the Payment Plan tables and initial payment types: **Special Deposit**, **Fix Deposit**, **Share**, **Pension**, **Scholarship**, **Scholar**, **Funeral** and **Danaayojana**. Scholarship and Scholar are separate contributions.

1. In **Payment Types**, choose **Add payment type** to add another contribution name at any time. Display order controls the order of the amount fields. No code or database changes are needed for additional types.
2. Open **Payment Plan** to see the member master containing registered member numbers and names. Search by either value, then choose **Set plan**. Member identity comes directly from registration and stays current if the member is updated.
3. Enter the agreement date and monthly amount in LKR for each active payment type. Unused amounts can be blank or zero. The form displays the total monthly deposit. Save to view the agreement, or edit it later. Each member has one current payment plan.
4. When a registration user has **Payment Plan → Add**, the registration button becomes **Register & set payment plan**, carrying the saved member number and name into the next stage. Existing members are already available through the Payment Plan master. **Set payment plan** is also available on a member profile.

New active payment types appear in new plans and when editing existing plans. Mark a payment type inactive to stop offering it to new plans; previously saved agreements retain that type and amount. Used types and members with a plan are protected from deletion. Deleting a plan removes its amounts while keeping the member registered.

Administrators grant **Payment Plan** actions under **Users & access** or **Roles & permissions**, limited to the user's assigned districts. Member registration permissions do not automatically grant access to payment agreements. Add-only plan operators can select member number/name within their districts without access to the full member directory. Payment Types is a shared master: users with View permission may read it, and administrators manage its entries.

## Reusable payment plan templates

Open **Payment Plan → Plan templates**, or **Payment Plan Templates** under Master data, to create a reusable name, description and monthly amounts from the existing payment types. Blank/zero amounts leave a type out. Administrators grant this shared module's separate View/Add/Edit/Delete permissions in Users & access. Templates contain no member data and are shared across districts; assigning and editing member plans still requires the existing Payment Plan action and member district access.

On a member's **Set payment plan** or **Edit payment plan** form, select an active template. Its amounts load into the draft; add or remove payment types using amounts, and adjust any amounts before saving. Assignment copies independent `PaymentPlanAmount` records. Changing a member agreement never changes the template or another member. Later template edits do not update assigned plans; existing member plans open with their own saved amounts. Selecting a template again explicitly replaces the draft amounts. Deleting a template retains member plans, their amounts and the recorded source template name. Inactive templates cannot be newly applied; inactive payment types are excluded from new template copies while existing member amounts remain available.

Run `python manage.py migrate` for migration `0021_paymentplantemplate_member_date_of_birth_and_more`. It adds template tables and member identity/retirement fields without replacing existing plans or financial entries.

## Member identity and retirement

Registration and profile editing include a separate **Title / Salutation** dropdown: Mr., Miss, Mrs., Ms. and Rev. Title is optional for existing/unspecified records. Full name remains separately stored. **Short Name** is generated automatically, including the selected title, by converting every name except the final name to initials: `Mr.` + `Krishantha Namal Spencer` → `Mr. K. N. Spencer`; a single name remains complete. Name/title changes update it in the browser and on the server, including ordinary model saves. Short Name is read-only and appears in profiles, directory identity/search and CSV export. Migration backfills existing names without inventing titles.

**Retirement age** defaults to 60 and **Expected retirement date** is calculated from **Date of birth**. Both generated fields are read-only; birth-date changes recalculate the expected date. Leap-day birthdays use the last valid day of February if the retirement year is not a leap year. Existing birth dates remain unknown until entered; future birth dates are rejected.

The expected date is planning information. It does not automatically change the member's status or pension date. Use **Member profile → Process retirement** to record actual retirement, including early retirement, and mark the member **Retired**. This requires Members → Edit and assigned-district access. Actual retirement must be today or earlier and after the recorded birth date. Accounts, plans and financial history are retained. **Recorded retirement / pension date** remains editable in the member form for existing or scheduled dates and controls the existing pension-quarter interest exclusion. Posted interest protection still applies: retirement cannot rewrite a posted year's eligibility. Profiles, Lager activity and CSV exports reflect the recorded retirement.

## Member opening balances

Run `python manage.py migrate` to add the Opening Balances module. It uses the shared **Payment Types** master, including any additional types you create, such as Dividends.

1. Open **Opening Balances**, choose a member in **Member number / name**, and select **Show balances** to see every payment type and its saved opening amount. Members without a record show **Not entered** and a button to set their opening balances.
2. Choose **Balance as of** and enter the opening amount in LKR for each payment type. Blank amounts save as zero. The form shows the total before saving.
3. Save to view the member's balances by payment type, or return later to edit them. **Set opening balances** includes members who already have balances; selecting an existing member opens their saved record for editing or viewing, according to your permissions. Each member has one opening balance record; monthly Payment Plan amounts remain separate.
4. New active payment types appear automatically in new and existing balance forms. On an existing record, a newly created type displays **Not entered** until its amount is saved. Inactive types retain previously saved balances and remain editable on those records.

Users with **Opening Balances → View** can also see the balances on the member profile and when selecting a member for a Payment Plan or viewing an agreement. Administrators grant separate View, Add, Edit and Delete actions through **Users & access** or **Roles & permissions**. District assignments restrict both member selection and all saved balance access. Member and Payment Plan permissions do not grant access to opening balances automatically.

Saved opening balances protect the member and payment types from deletion. Deleting the opening balance record removes its amounts while retaining the member and any payment plan.

## Member debit / credit entries and interest rates

Run `python manage.py migrate` to add the **Debit / Credit** module and interest settings in **Master data → Payment Types**. Existing payment types start at 0.00%; administrators can set a percentage from 0 to 100 with two decimal places. **Quarterly (every 3 months)** is the default for new types and previously unset frequencies; any frequency already chosen by the bank is preserved. The bank can select **Daily (every day)**, **Monthly (every month)**, **Quarterly (every 3 months)** or **Yearly (every year)** independently for each payment type.

Select **Custom period** for either a **Repeating interval**, such as every 45 days, 6 months or 2 years, or **Specific start and end dates** for a period chosen by the bank. A repeating interval requires a whole number from 1 to 32767 and a unit of days, months or years. A specific period requires both dates with the end on or after the start. The form shows the relevant fields and a preview of the chosen period. Switching to a standard frequency clears unused custom settings when saved. The configured period is displayed in the master list, the debit/credit entry form and the statement.

Member deposit interest uses the quarterly minimum month-end method described below. The configured rate is an annual percentage. The other frequency and custom-period fields remain available as bank reference settings; the Quarterly Interest module follows the quarterly rule for all member Payment Types.

1. Open **Debit / Credit → Add entry**. Search the member combo by number or name to see their saved balance immediately. The form shows the total across payment types and a balance breakdown; choosing a payment type also highlights that type's balance. The interest rate and the bank's chosen calculation frequency appear beside the selection.
2. Enter the transaction date, a positive debit **or** credit in LKR, and an optional reference and description. Leave the other amount blank or zero. Save the entry; each member and payment type can have many dated entries.
3. On **Debit / Credit**, choose the member and payment type, then select **Show debit / credit**. Use **Single date** for one day, or **From date / To date** for an inclusive range. With no end date, the statement runs through today. Earlier amounts are brought forward into the starting balance; changing the period start does not reset the account to zero. For a future From date, also choose an appropriate To date.
4. The statement shows the **Starting balance / brought forward**, period debits and credits, and **Final balance** as of the selected end date. **Final balance = starting balance + credits − debits**. The starting balance includes the saved opening amount and earlier transactions after its opening date. For example, a Special Deposit opening balance of LKR 10,000 plus a LKR 2,000 credit shows LKR 12,000. Running balances include the starting amount and remain correct across pages. A day without transactions still shows the account's final balance.
5. Open **Debit / Credit → Member balances** or **Member balances** on a member profile to see final amounts by member and payment type. Leave member or payment type blank to include all authorized records, or select either filter. The same single-date and date-range options apply. This report includes accounts with saved opening amounts or transactions, retains inactive payment-type history, and totals every page. **View statement** opens that member/type's dated entries with the date filters preserved.

Administrators grant independent **Debit / Credit → View, Add, Edit and Delete** permissions in **Users & access** or **Roles & permissions**. Assigned districts restrict member choices and saved entries. Existing member, payment-plan and opening-balance permissions do not automatically grant transaction access. A member's profile links to the module when the user has permission.

The entry form's **Saved member balance** updates when the member or transaction date changes. It includes opening amounts effective on or before that date, plus saved credits minus debits through that date. Opening amounts are treated as starting balances on their balance date: entries before that date for types with a saved opening amount are excluded to avoid counting historical amounts twice. A type without a saved opening amount uses its complete transaction history through the selected date. Balances before the opening date use transactions only. Unsaved amounts and the saved entry being edited are excluded from the displayed balance. An account with no saved amounts shows zero; balances can be negative.

Users with **Debit / Credit → Add**, **View** or **Edit** can read these balance summaries within their assigned districts, including the opening amounts needed for the balance. This does not grant access to the Opening Balances pages or changes to opening records. Balance lookups are read-only and are not cached.

Editing an entry allows correcting its date, amounts, reference and description while retaining its original member, payment type and creator. Inactive payment types retain their history and existing entries remain editable; new entries require an active type. Recorded transactions protect their member and payment type from deletion. Deleting an entry requires confirmation and changes its statement totals.

## Quarterly deposit interest and annual carry-forward

**Quarterly Interest** covers both member Payment Types and separate Child Deposits. Each member/type or child/deposit account is calculated independently from its own opening amount and dated deposits/withdrawals. Member calculations include Fix Deposit top-ups and released withdrawals. Monthly Payment Plan agreements are not deposits until the contributions are recorded in the ledger. Select both account groups or filter by member Payment Type, child, child deposit type or deposit reference.

The default financial year is January–December. Set `FINANCIAL_YEAR_START_MONTH` in `.env` to a value from 1 to 12 before starting a financial year to use a different first month. Saved years retain their actual start and end dates; overlapping posted periods are rejected.

For each quarter, the system finds the three month-end closing balances after all saved deposits and withdrawals. It uses their minimum and calculates **quarterly interest = minimum balance × annual rate ÷ 100 ÷ 4**. At 8%, balances of 2,000.00, 3,000.00 and 1,500.00 produce LKR 30.00. A following quarter with a 1,300.00 minimum produces LKR 26.00; accrued interest for those two quarters is LKR 56.00. Quarterly interest is rounded to cents using half-up rounding. A month before an account exists has a zero balance. A zero or negative minimum earns zero interest; incomplete quarters remain pending.

Open **Quarterly Interest**, select the financial year and optional member/type filters, and choose **Show quarterly interest**. This preview is read-only. **Calculate & save** stores each account's calculation with its month-end balances and rate. Drafts update from current bank rates while the year is open; after year-end, the last saved annual rate is retained so next year's rate cannot revalue last year's interest. If a historical year has never been saved, set the applicable bank rate before calculating it.

Interest accumulates without quarterly capitalization. After the last day of the financial year, **Review year-end posting** shows the calculation and allows **Post annual interest & carry forward**. This credits the annual sum once, dated on the last day of that year. The closing ledger balance plus annual interest becomes the next year's brought-forward opening balance. The original imported opening balance and all transactions are retained. **Opening Balances → select member → Show balances** also displays the annual carried-forward amounts. Posting is atomic and repeatable without duplicate credits; zero-interest years close without inserting a zero-value transaction.

Posted years and their journal entries cannot be edited or deleted. Opening amounts, opening dates and transactions on or before a closed account year are protected; new-year deposits and withdrawals remain available. A changed balance or rate between review and posting requires another review. Earlier draft years must be posted or removed before a later year is closed.

**Exclude transition quarters:** the whole calendar quarter containing a child's configured age-threshold birthday earns no interest. Other completed quarters remain eligible: earlier quarters use the below-threshold rate and later complete quarters use the from-threshold rate. The excluded quarter is not prorated and is marked **Excluded** with the birthday date. A birthday on the first or last day still belongs to that calendar quarter. Leap-day children reach the rate-change age on 1 March in non-leap years, matching the existing age calculation. This bank rule replaces the earlier selectable birthday methods; unposted drafts recalculate under the confirmed rule, while posted history remains fixed.

For member Payment Types, enter **Pension date** when registering or editing the member. The calendar quarter containing that date earns no interest for that member's payment accounts; all other completed quarters are calculated normally. For example, a pension date of 15 May excludes Q2 (April–June); Q1, Q3 and Q4 still contribute when complete. A parent's pension date does not exclude the child's separate deposit accounts. A pension date cannot be changed if doing so would alter an already posted interest year. The annual total and next-year opening balance include only eligible quarters.

Annual child interest is credited to the child deposit's own ledger on 31 December and becomes that account's next-year brought-forward opening balance. It is not credited to the parent member's ledger. The child statement also shows saved annual interest and carried opening balances. Posted child years protect the child's member/date of birth, the account's opening details and historical amounts. Each account's old rate rules remain saved when the bank changes rates for the next year.

Grant **Quarterly Interest → View** to read reports, **Add** to calculate/save, **Edit** to post a year-end calculation, and **Delete** to remove an unposted draft. District assignments apply throughout. Debit / Credit permissions do not grant these permissions automatically.

The automatic processor can run unattended:

```bash
python manage.py process_deposit_interest --post-ended
```

It processes both member Payment Types and Child Deposits, refreshes saved current-year calculations and posts ended years once, oldest years first. The first run starts in the current financial year; subsequent runs retain that starting year. Use `--from-year 2025` to explicitly include earlier years. Configure annual rates and record members' pension dates before closing a year. Birthday and pension quarters are automatically excluded. Run this command hourly with the operating system scheduler; it uses the application's Asia/Colombo date and will carry annual interest forward on the first run after year-end. Scheduler failures return a nonzero exit code and identify affected accounts without stopping unaffected accounts.

On a Linux host with cron and `flock`, run `python manage.py install_interest_schedule` to display the exact schedule, or `python manage.py install_interest_schedule --install` to install it for the current operating-system user while preserving other jobs. It runs at minute 15 of every hour, including while the web app is stopped, and logs to `.interest-scheduler.log`. The host and cron service must be running. Reinstall the entry after moving the project to a different directory or deploying to another server.

## Child Deposits

Run `python manage.py migrate` to add **Children**, **Child Deposits** and **Child Deposit Types**. A member can have any number of children, and each child can have any number of deposit accounts, including multiple accounts of the same type. Child deposits have their own balances and do not change the parent's separate member payment balances.

1. Open a member profile and choose **Children**, then **Add child**, or use **Children → Add child** in the workspace. Select the parent member, enter the child's **Name** and **Date of birth**, and save. Member selection stays fixed on a saved child record.
2. An administrator can configure **Master data → Child Deposit Types**, including a separate **Rate changes at age**, **Rate below this age (%)**, and **Rate from this age (%)** for each deposit type. The initial **Child Savings** type uses **8% below age 18** and **5% from the 18th birthday**. These values can all be changed; for example, another type can use 9.5% below age 21 and 6.25% from age 21.
3. On a child profile, choose **Open deposit for this child**. Select the child deposit type, opening date and opening amount. The form previews the rate for the child's age on that date. Each account receives its own deposit reference. The opening date and amount stay fixed after transactions are recorded.
4. Open an account and select **Add debit / credit**. Enter the transaction date and a positive credit or debit, leaving the other side blank or zero. The form shows the saved balance before this entry and the configured rate for that date. A transaction cannot predate the account's opening date.
5. **Child Deposits** lists each account's balance and configured rate as of a chosen date, with optional member, child and deposit-type filters. The total includes all filtered accounts across pages. Each account's statement supports single-date and inclusive date-range filters, carries forward earlier amounts, and shows dated debits, credits and running balances. Final balance equals the opening amount plus saved credits minus debits through the selected date.

Rates follow the child's completed age on the selected date, changing automatically at the type's configured birthday. Every dated entry retains the configured rate at the time it was saved. Editing an entry's note or amount preserves that rate; changing its transaction date refreshes the rate using the current type rules when the year is open. Later bank rule changes affect current rate displays and new entries while retaining existing entry rates. The Quarterly Interest module calculates and posts child deposit interest using the method above.

Administrators grant independent **Children** and **Child Deposits** View, Add, Edit and Delete permissions through **Users & access** or **Roles & permissions**. Child Deposits permissions cover both accounts and dated entries. Parent member, opening-balance and member payment permissions do not grant these permissions automatically. All child and deposit records, forms, balances and lookups follow the parent member's assigned district. Child-only viewers cannot see deposit balances without Child Deposits View permission. Add-only operators can select authorized identities and save records without viewing statements. **Child Deposit Types** is a shared master managed by administrators; operators with View can read its rules.

Used children, deposit types, members and accounts are protected from deletion. Mark them inactive to preserve their history and stop new accounts or entries. Historical entries remain editable with the appropriate permission. Deleting a dated entry requires confirmation and updates its account's balance.

## Setup on another machine

Python 3.12+, PostgreSQL 14+ and Node.js 20+ are required. Use a Python version supported by the Django release in `requirements.txt`.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Set your PostgreSQL connection and a new DJANGO_SECRET_KEY in .env.
# Create the member_module PostgreSQL database using your PostgreSQL tools.
npm ci
npm run build:css
python manage.py migrate
python manage.py bootstrap_admin
python manage.py runserver
```

`bootstrap_admin` creates a random initial password only when the `admin` account does not already exist. It saves credentials to an ignored, owner-readable `.admin-credentials` file. It does not reset existing passwords.

## Member information

District, derived province, zonal office, required department and lager, optional subdivision, member number, computer number, full name, monthly salary in LKR, office and private addresses, office/mobile/home phone numbers, admission date, gender, NIC number, and active/inactive status are supported. Office address is required. Department and lager are required for member registration and updates. Computer number, office/home phones and subdivision are optional. A department with a subdivision can be selected even when the member’s subdivision is left blank.

The hierarchy is **District → Zonal office → Lager → Members**. A lager has a name, globally unique code and a required zonal office; its district is derived from that office. Lager names must be unique within a zone and may repeat in different zones. Department and lager selections are mandatory in member forms and model validation. Registration limits lagers to the selected zonal office and rejects mismatched assignments on the server. The Lager master supports district, zone and name/code filters. Member directory zone filters narrow the lager choices, and CSV exports follow the same member filters. Lagers with members cannot change zone or be deleted. Inactive lagers remain available only for existing assignments during editing. Each zonal office also has subdivisions. A department belongs to a district and zonal office, with an optional subdivision. The member form derives district and zonal office from the selected department. Existing inactive selections remain available when editing historical records. Zonal offices, subdivisions and departments with linked records cannot move to a conflicting parent. Linked master records are protected against deletion; mark them inactive to retain history. The existing `OfficeLocation` database model and `/offices/` URLs are retained to preserve existing references, with “Zonal office” used in the interface and exports.

Run `python manage.py migrate` to move existing district lagers to zonal offices. Migration `0020_lager_zonal_office` infers zones from their registered members. A lager used across multiple zones is split by zone, preserving member IDs and all their financial records; additional copies retain the name and receive unique codes. An unused lager is assigned automatically only if its district has exactly one zone. Ambiguous unused lagers stop the migration before any data changes: supply an explicit mapping such as `LAGER_ZONAL_MIGRATION_MAP='{"12": 4}' python manage.py migrate` (lager ID 12 → zonal office ID 4 in its existing district). Historical unassigned members still need a department and lager when edited. Rolling back requires resolving lager names repeated across zones in the same district first.

Open a lager's name or **Activity** link in **Lagers** to see its members' saved activity together: opening balances by payment type, monthly payment plans, deposit debit/credit entries, Fix Deposit accounts, loan issues/repayments/assessed interest/settlements, withdrawal requests and reviews, children and their deposit accounts/entries, and saved member/child quarterly interest calculations. Filter by member, activity type and date range; export all matching records to CSV, including records beyond the displayed page. Member profiles also link to their Lager activity.

Activity follows each member's **current Lager assignment**, including inactive members and their children. Moving a member to another valid lager carries all their existing activity into that lager's report, without changing financial records. This is a report of saved records, not an audit log of deleted records or previous edits. Account summaries use the existing opening-date and transaction balance rules: closing balances are as of the end date, debit/credit totals cover the selected dates, and loan balances remain separate. Interest snapshots and withdrawal requests/reviews do not add a second amount to the posted deposit ledger. The activity-type filter affects the records table; summaries cover all modules the user can view for the selected member(s).

Lager activity and CSV export require **Lagers → View** and assigned-district access. Each section additionally requires its existing module's **View** permission; Lager access alone grants no financial or member-record access. Both member and child interest calculations follow **Quarterly Interest → View**, as in the existing interest module. Member and child deposit summary access follows **Debit / Credit → View** and **Child Deposits → View** respectively. Add/Edit permissions do not grant report access. No separate Lager financial records are stored; all activity is selected through the authorized members currently in the lager.

Member, computer and NIC numbers must be unique. NIC accepts either 12 digits or 9 digits followed by V/X. Phone numbers accept 7–15 digits with common formatting. Monthly salaries cannot be negative. Admission dates cannot be in the future. Number identifiers are normalized to uppercase. CSV exports respect directory filters and escape spreadsheet formula prefixes.

## Development and checks

Activate the virtual environment and install the development tools once:

```bash
source .venv/bin/activate
pip install -r requirements-dev.txt
npm ci
```

Format all source files and check their formatting with:

```bash
npm run format
npm run format:check
```

Black formats Python, djLint formats Django templates, and Prettier formats JavaScript, source CSS, JSON, Markdown and SVG. Editor defaults are in `.editorconfig`. Generated CSS and lockfiles are excluded from formatting; rebuild Tailwind with `npm run build:css` after changing templates or source CSS.

```bash
npm run watch:css            # Rebuild Tailwind when templates/styles change
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test members
python manage.py test config # Session security integration tests
npm run test:session         # Browser inactivity and tab coordination tests
```

The integration suite uses a separate `test_member_module` PostgreSQL database, which Django creates and removes. The configured database user must have permission to create test databases. Tests cover registrations, identifier uniqueness, district/office matching, inactive records, protected deletions, CSRF, authentication, templates, filtering, exports, master record management, per-action permission checks, role grants/revocations, and custom account administration.

Compiled CSS is included at `static/css/tailwind.css`; the interface needs no external CDN, fonts or JavaScript framework.

## Deployment

Set `DJANGO_DEBUG=false`, a strong `DJANGO_SECRET_KEY`, the actual `DJANGO_ALLOWED_HOSTS`, and PostgreSQL credentials in environment variables. Use HTTPS and a production WSGI/ASGI server. Secure cookies are enabled when debug is disabled. Run `python manage.py collectstatic` and serve `staticfiles/` through your web server. Run `python manage.py check --deploy` with production configuration before deployment. Back up PostgreSQL regularly. `.env` and `.admin-credentials` must stay private.

Province names remain stored in full. A separate `province_short_name` field is available in the District master, automatic registration and department fields, master lists, member profiles and CSV exports. The migration fills editable suggested short names for existing recognized provinces without changing their full names.

The interface uses the supplied EDCS logo with maroon and gold accents. Fields use dark labels and entered text, 2px contrasting borders, larger input text, visible keyboard outlines, and textual required/optional/error indicators. Automatically filled province fields use a dashed border and explicit labels. Department appears immediately after Subdivision and continues to fill its related location fields when selected.

The member’s office address and office phone are entered manually and remain blank on new registrations until supplied. Selecting a district, zonal office or department does not fill these contact fields. Existing saved addresses and phone numbers are preserved when editing.
