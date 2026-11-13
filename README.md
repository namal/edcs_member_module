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
4. Open **Lagers** to add a lager name, unique code and district. A district can have many lagers; each lager shows its registered member count with a link to those members.
5. Open **Member directory → Register member**. Department and lager are mandatory. Type a department name or code directly in the **Department** combo to filter its alphabetical dropdown, then click a result or use the arrow keys and Enter to select it. Choosing a district narrows departments to that district; choosing a zonal office narrows them further. Clear the text or location selections to broaden the list. You can also select a department first to fill its district, full province name, short name, zonal office and optional subdivision from the saved relationships. Select a lager from the department’s district. Department labels include the department name, full province name with its short name in parentheses, district, zonal office and subdivision when present.
6. Enter the member information, including the required office address. Search and filter the directory, export records, or open a profile to edit and print it.

## Payment Plan and configurable payment types

Run `python manage.py migrate` to add the Payment Plan tables and initial payment types: **Special Deposit**, **Fix Deposit**, **Share**, **Pension**, **Scholarship**, **Scholar**, **Funeral** and **Danaayojana**. Scholarship and Scholar are separate contributions.

1. In **Payment Types**, choose **Add payment type** to add another contribution name at any time. Display order controls the order of the amount fields. No code or database changes are needed for additional types.
2. Open **Payment Plan** to see the member master containing registered member numbers and names. Search by either value, then choose **Set plan**. Member identity comes directly from registration and stays current if the member is updated.
3. Enter the agreement date and monthly amount in LKR for each active payment type. Unused amounts can be blank or zero. The form displays the total monthly deposit. Save to view the agreement, or edit it later. Each member has one current payment plan.
4. When a registration user has **Payment Plan → Add**, the registration button becomes **Register & set payment plan**, carrying the saved member number and name into the next stage. Existing members are already available through the Payment Plan master. **Set payment plan** is also available on a member profile.

New active payment types appear in new plans and when editing existing plans. Mark a payment type inactive to stop offering it to new plans; previously saved agreements retain that type and amount. Used types and members with a plan are protected from deletion. Deleting a plan removes its amounts while keeping the member registered.

Administrators grant **Payment Plan** actions under **Users & access** or **Roles & permissions**, limited to the user's assigned districts. Member registration permissions do not automatically grant access to payment agreements. Add-only plan operators can select member number/name within their districts without access to the full member directory. Payment Types is a shared master: users with View permission may read it, and administrators manage its entries.

## Member opening balances

Run `python manage.py migrate` to add the Opening Balances module. It uses the shared **Payment Types** master, including any additional types you create, such as Dividends.

1. Open **Opening Balances** and select **Set opening balances**. Search registered members by member number or name, then select the member.
2. Choose **Balance as of** and enter the opening amount in LKR for each payment type. Blank amounts save as zero. The form shows the total before saving.
3. Save to view the member's balances by payment type, or return later to edit them. Each member has one opening balance record; monthly Payment Plan amounts remain separate.
4. New active payment types appear automatically in new and existing balance forms. On an existing record, a newly created type displays **Not entered** until its amount is saved. Inactive types retain previously saved balances and remain editable on those records.

Users with **Opening Balances → View** can also see the balances on the member profile and when selecting a member for a Payment Plan or viewing an agreement. Administrators grant separate View, Add, Edit and Delete actions through **Users & access** or **Roles & permissions**. District assignments restrict both member selection and all saved balance access. Member and Payment Plan permissions do not grant access to opening balances automatically.

Saved opening balances protect the member and payment types from deletion. Deleting the opening balance record removes its amounts while retaining the member and any payment plan.

## Member debit / credit entries and interest rates

Run `python manage.py migrate` to add the **Debit / Credit** module and interest settings in **Master data → Payment Types**. Existing payment types start at 0.00%; administrators can set a percentage from 0 to 100 with two decimal places. **Quarterly (every 3 months)** is the default for new types and previously unset frequencies; any frequency already chosen by the bank is preserved. The bank can select **Daily (every day)**, **Monthly (every month)**, **Quarterly (every 3 months)** or **Yearly (every year)** independently for each payment type.

Select **Custom period** for either a **Repeating interval**, such as every 45 days, 6 months or 2 years, or **Specific start and end dates** for a period chosen by the bank. A repeating interval requires a whole number from 1 to 32767 and a unit of days, months or years. A specific period requires both dates with the end on or after the start. The form shows the relevant fields and a preview of the chosen period. Switching to a standard frequency clears unused custom settings when saved. The configured period is displayed in the master list, the debit/credit entry form and the statement.

The interest rate and period are stored for reference. Automatic interest calculation and posting require agreed calculation rules and are not included.

1. Open **Debit / Credit → Add entry**. Search the member combo by number or name to see their saved balance immediately. The form shows the total across payment types and a balance breakdown; choosing a payment type also highlights that type's balance. The interest rate and the bank's chosen calculation frequency appear beside the selection.
2. Enter the transaction date, a positive debit **or** credit in LKR, and an optional reference and description. Leave the other amount blank or zero. Save the entry; each member and payment type can have many dated entries.
3. On **Debit / Credit**, choose the member and payment type, then select **Show debit / credit**. Use **Single date** for one day, or **From date / To date** for an inclusive range. With no end date, the statement runs through today. Earlier amounts are brought forward into the starting balance; changing the period start does not reset the account to zero. For a future From date, also choose an appropriate To date.
4. The statement shows the **Starting balance / brought forward**, period debits and credits, and **Final balance** as of the selected end date. **Final balance = starting balance + credits − debits**. The starting balance includes the saved opening amount and earlier transactions after its opening date. For example, a Special Deposit opening balance of LKR 10,000 plus a LKR 2,000 credit shows LKR 12,000. Running balances include the starting amount and remain correct across pages. A day without transactions still shows the account's final balance.
5. Open **Debit / Credit → Member balances** or **Member balances** on a member profile to see final amounts by member and payment type. Leave member or payment type blank to include all authorized records, or select either filter. The same single-date and date-range options apply. This report includes accounts with saved opening amounts or transactions, retains inactive payment-type history, and totals every page. **View statement** opens that member/type's dated entries with the date filters preserved.

Administrators grant independent **Debit / Credit → View, Add, Edit and Delete** permissions in **Users & access** or **Roles & permissions**. Assigned districts restrict member choices and saved entries. Existing member, payment-plan and opening-balance permissions do not automatically grant transaction access. A member's profile links to the module when the user has permission.

The entry form's **Saved member balance** updates when the member or transaction date changes. It includes opening amounts effective on or before that date, plus saved credits minus debits through that date. Opening amounts are treated as starting balances on their balance date: entries before that date for types with a saved opening amount are excluded to avoid counting historical amounts twice. A type without a saved opening amount uses its complete transaction history through the selected date. Balances before the opening date use transactions only. Unsaved amounts and the saved entry being edited are excluded from the displayed balance. An account with no saved amounts shows zero; balances can be negative.

Users with **Debit / Credit → Add**, **View** or **Edit** can read these balance summaries within their assigned districts, including the opening amounts needed for the balance. This does not grant access to the Opening Balances pages or changes to opening records. Balance lookups are read-only and are not cached.

Editing an entry allows correcting its date, amounts, reference and description while retaining its original member, payment type and creator. Inactive payment types retain their history and existing entries remain editable; new entries require an active type. Recorded transactions protect their member and payment type from deletion. Deleting an entry requires confirmation and changes its statement totals.

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

Each district belongs to a province and has many zonal offices, departments and lagers. A lager has a name, unique code and district, and can have many registered members. Department and lager selections are mandatory in member forms and model validation. Existing records are preserved by the migration; older unassigned records must receive both selections when edited. Registration limits lagers to the selected district. The Lager master supports district and name/code filters, and the member directory and CSV export support lager filtering. Lagers with members cannot change district or be deleted; inactive lagers remain available only for existing assignments during editing. Each zonal office has many subdivisions. A department belongs to a district and zonal office, with an optional subdivision. Registration only permits active records within the selected hierarchy; the member form derives district and zonal office from the selected department on the server as well, and rejects lagers from other districts. Existing inactive selections remain available when editing historical records. Zonal offices, subdivisions and departments with linked records cannot move to a conflicting parent. Linked master records are protected against deletion; mark them inactive to retain history. The existing `OfficeLocation` database model and `/offices/` URLs are retained to preserve existing references, with “Zonal office” used in the interface and exports.

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
