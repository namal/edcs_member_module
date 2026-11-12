# EDCS — Member Management

A complete Django, HTML and Tailwind CSS application backed by PostgreSQL. Includes a responsive dashboard, member registration and profiles, district, zonal office, subdivision, school and lager masters, search, filters, CSV exports, print profiles and authenticated access.

## Local application

The local PostgreSQL database is `member_module`. Credentials are stored in `.env`, which is excluded from version control. The provided password has been verified against PostgreSQL.

```bash
source .venv/bin/activate
python manage.py runserver
```

Open **http://127.0.0.1:8000/**. Sign in with the initial administrator credentials saved in the local `.admin-credentials` file. Change the initial password at `/admin/password_change/`. Django admin at `/admin/` also lets you manage authorized user accounts. All authenticated accounts can manage the workspace; only staff accounts can use Django admin.

All 25 Sri Lankan districts are provided as editable master records with provinces. Run `python manage.py migrate` after updating to apply the location hierarchy migration; it preserves existing districts, offices and members.

1. In **Districts**, create or edit each district with its required full province name and a separate editable province short name (for example, Western and WP).
2. Open **Zonal offices → Add zonal office** and select its district. Add optional subdivisions under **Subdivisions**.
3. Open **Schools** to add a school code and name, district, zonal office and optional subdivision. Filter schools by district or search by name/code. The District master links to school counts per district.
4. Open **Lagers** to add a lager name, unique code and district. A district can have many lagers; each lager shows its registered member count with a link to those members.
5. Open **Member directory → Register member**. School and lager are mandatory. Select a school first to fill its district, full province name, short name, zonal office and optional subdivision from the saved relationships. Select a lager from the school’s district. School labels include the school name, full province name with its short name in parentheses, district, zonal office and subdivision when present.
6. Enter the member information, including the required office address. Search and filter the directory, export records, or open a profile to edit and print it.

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

District, derived province, zonal office, required school and lager, optional subdivision, member number, computer number, full name, monthly salary in LKR, office and private addresses, office/mobile/home phone numbers, admission date, gender, NIC number, and active/inactive status are supported. Office address is required. School and lager are required for member registration and updates. Computer number, office/home phones and subdivision are optional. A school with a subdivision can be selected even when the member’s subdivision is left blank.

Each district belongs to a province and has many zonal offices, schools and lagers. A lager has a name, unique code and district, and can have many registered members. School and lager selections are mandatory in member forms and model validation. Existing records are preserved by the migration; older unassigned records must receive both selections when edited. Registration limits lagers to the selected district. The Lager master supports district and name/code filters, and the member directory and CSV export support lager filtering. Lagers with members cannot change district or be deleted; inactive lagers remain available only for existing assignments during editing. Each zonal office has many subdivisions. A school belongs to a district and zonal office, with an optional subdivision. Registration only permits active records within the selected hierarchy; the member form derives district and zonal office from the selected school on the server as well, and rejects lagers from other districts. Existing inactive selections remain available when editing historical records. Zonal offices, subdivisions and schools with linked records cannot move to a conflicting parent. Linked master records are protected against deletion; mark them inactive to retain history. The existing `OfficeLocation` database model and `/offices/` URLs are retained to preserve existing references, with “Zonal office” used in the interface and exports.

Member, computer and NIC numbers must be unique. NIC accepts either 12 digits or 9 digits followed by V/X. Phone numbers accept 7–15 digits with common formatting. Monthly salaries cannot be negative. Admission dates cannot be in the future. Number identifiers are normalized to uppercase. CSV exports respect directory filters and escape spreadsheet formula prefixes.

## Development and checks

```bash
npm run watch:css            # Rebuild Tailwind when templates/styles change
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test members
```

The integration suite uses a separate `test_member_module` PostgreSQL database, which Django creates and removes. The configured database user must have permission to create test databases. Tests cover registrations, identifier uniqueness, district/office matching, inactive records, protected deletions, CSRF, authentication, templates, filtering, exports and master record management.

Compiled CSS is included at `static/css/tailwind.css`; the interface needs no external CDN, fonts or JavaScript framework.

## Deployment

Set `DJANGO_DEBUG=false`, a strong `DJANGO_SECRET_KEY`, the actual `DJANGO_ALLOWED_HOSTS`, and PostgreSQL credentials in environment variables. Use HTTPS and a production WSGI/ASGI server. Secure cookies are enabled when debug is disabled. Run `python manage.py collectstatic` and serve `staticfiles/` through your web server. Run `python manage.py check --deploy` with production configuration before deployment. Back up PostgreSQL regularly. `.env` and `.admin-credentials` must stay private.

Province names remain stored in full. A separate `province_short_name` field is available in the District master, automatic registration and school fields, master lists, member profiles and CSV exports. The migration fills editable suggested short names for existing recognized provinces without changing their full names.

The interface uses the supplied EDCS logo with maroon and gold accents. Fields use dark labels and entered text, 2px contrasting borders, larger input text, visible keyboard outlines, and textual required/optional/error indicators. Automatically filled province fields use a dashed border and explicit labels. School appears immediately after Subdivision and continues to fill its related location fields when selected.
