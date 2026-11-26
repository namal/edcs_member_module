#!/usr/bin/env python3
"""Refresh source-derived SRS appendices and build its editable Word document.

Use /usr/bin/python3 docs/tools/build_function_level_srs.py on the reviewed host.
Requires Markdown and the adjacent build_user_manual.py renderer. No Django
initialization, database access or secret-file reads are performed.

Optional --page-map accepts a JSON mapping of chapter numbers to PDF page
numbers, allowing the contents to be cached after an initial PDF conversion.
Convert the DOCX to PDF with LibreOffice using a temporary user profile.
"""

import argparse
import ast
import importlib.util
import json
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "docs/EDCS_Function_Level_SRS.md"
OUTPUT = SOURCE.with_suffix(".docx")
MARKER = "\n## 21. Data dictionary\n"
MODEL_FILES = [
    "members/models.py",
    "members/contribution_models.py",
    "members/transfer_models.py",
    "members/approval_models.py",
    "members/audit_models.py",
    "members/year_end_models.py",
]
MODEL_PURPOSES = {
    "District": "Branch and province master; holds authorized-user branch assignments.",
    "OfficeLocation": "Zonal office belonging to a branch.",
    "Subdivision": "Optional organizational subdivision of a zonal office.",
    "School": "Department or school under a branch and zonal office.",
    "Lager": "Ledger master under an office and current member-ownership assignment.",
    "Gender": "Configurable display name with a stable member gender code.",
    "MemberAuditEvent": "Immutable member before/after change evidence retaining source identity.",
    "Member": "Registered member identity, organizational placement and retirement details.",
    "PaymentType": "Shared member account/contribution category and reference rate settings.",
    "PaymentTransaction": "Member/payment-type dated journal, including opposite correction links.",
    "PaymentPlanTemplate": "Shared reusable monthly agreement template.",
    "PaymentPlanTemplateAmount": "Per-template category amount and fixed configuration flag.",
    "PaymentPlan": "One current monthly agreement for a member.",
    "PaymentPlanAmount": "Independent per-member-plan category amount and inherited lock.",
    "OpeningBalance": "One original/manual opening header per member.",
    "OpeningBalanceAmount": "Effective original opening amount for a member account category.",
    "DepositInterestYear": "Member account-year calculation, reconciliation snapshot and annual posting.",
    "Child": "Child identity under an existing member.",
    "ChildDepositType": "Shared child deposit type and age-dependent annual rate rules.",
    "ChildDeposit": "Independent referenced child account with its own original opening.",
    "ChildDepositTransaction": "Child account dated movement with saved reference rate and correction link.",
    "ChildDepositInterestYear": "Child account-year calculation, saved rate rules and annual posting.",
    "FixDepositPolicy": "Secured-deposit maturity, loan-security and withdrawal rules.",
    "FixDepositAccount": "Member/policy servicing account linked to the existing member deposit journal.",
    "FixDepositLoan": "Issued loan principal and security/rate evidence retained at issue.",
    "FixLoanEntry": "Assessed loan interest or principal/interest repayment with operation identity.",
    "FixDepositWithdrawal": "Pending, released or rejected controlled withdrawal and release evidence.",
    "PaymentReceipt": "Immutable actual contribution receipt, plan snapshot and posting identity.",
    "PaymentReceiptAllocation": "Ordered standard or remaining receipt allocation linked to a resulting credit when positive.",
    "LedgerOfficerProfile": "Employee EPF and multiple assigned offices for an existing system user.",
    "MemberLedger": "Permanent original/receiving page with identity, location and balance snapshots.",
    "MemberTransfer": "Immutable source-to-receiving-page transfer evidence.",
    "LedgerOfficerAssignment": "Permanent responsibility handover with prior/new employee snapshots.",
    "LedgerRecord": "Stable link from a business source record to its original permanent page.",
    "LedgerEvent": "Saved source values at a creation, update or permitted deletion.",
    "ApprovalHierarchy": "Versioned module/action approval policy.",
    "ApprovalLevel": "Ordered hierarchy level with designated users and rejection permission.",
    "ApprovalRequest": "Submitted operation, hierarchy/approver snapshots and application outcome.",
    "ApprovalAuditEvent": "Permanent configuration, decision, override and application evidence.",
    "AuditLog": "Read-only central business, access and authentication event record.",
    "FinancialYearClosing": "Immutable administrator-approved whole-system closing review batch.",
}

REQUIREMENT_FAMILIES = {
    "access": "FR-ADM; chapter 3",
    "admin_site": "FR-ADM; NFR-SEC",
    "approval": "FR-APR",
    "audit": "FR-AUD",
    "child": "FR-CHD",
    "child_interest": "FR-INT",
    "contribution": "FR-CON",
    "deposit_interest": "FR-INT",
    "fix_deposit": "FR-FIX",
    "interest": "FR-INT",
    "lager": "FR-MST; FR-RPT",
    "ledger": "FR-LED; FR-TRF",
    "member_audit": "FR-AUD-03",
    "member_identity": "FR-MEM-03",
    "notifications": "FR-ADM-02",
    "officers": "FR-MST-05; FR-LED",
    "opening_balance": "FR-OPN",
    "payment": "FR-PLN; FR-MST-07",
    "permissions": "Chapter 3; NFR-SEC-01",
    "posting_policies": "FR-TXN-04 to 06; FR-CHD-06",
    "retirement": "FR-MEM-07",
    "transaction": "FR-TXN",
    "transfer": "FR-LED; FR-TRF",
    "year_end": "FR-YEC",
    "bootstrap_admin": "FR-ADM-06",
    "install_interest_schedule": "FR-SCH-02",
    "process_deposit_interest": "FR-SCH-01",
}


def code(node):
    return ast.unparse(node)


def cell(value):
    return str(value).replace("|", "&#124;").replace("\n", " ")


def tree(path):
    return ast.parse((ROOT / path).read_text())


def fields(cls):
    result = []
    for node in cls.body:
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        call = node.value
        if not (
            isinstance(call.func, ast.Attribute)
            and isinstance(call.func.value, ast.Name)
            and call.func.value.id == "models"
            and call.func.attr.endswith("Field")
            or isinstance(call.func, ast.Attribute)
            and isinstance(call.func.value, ast.Name)
            and call.func.value.id == "models"
            and call.func.attr in ("ForeignKey", "OneToOneField")
        ):
            continue
        result.append((node.targets[0].id, call))
    return result


def dictionary():
    lines = [
        MARKER.strip(),
        "",
        "This dictionary lists every explicitly declared application model field in the six domain model files. All concrete models also have Django's implicit BigAutoField primary key unless otherwise declared. Framework User, Group, Permission and session tables are described separately below. Null/blank refer to storage and default model validation; the function requirements and forms can be stricter, particularly for legacy member fields. Timestamp fields populated on save and generated references are not ordinary user inputs.",
        "",
        "A ForeignKey is many-to-one; OneToOneField adds uniqueness; ManyToManyField uses a relationship table. PROTECT prevents deletion of referenced records, SET_NULL preserves a record while removing its nullable reference, and CASCADE removes dependent rows unless stronger domain protections reject the operation. Custom model/service guards continue to apply beyond declared field options.",
    ]
    model_count = field_count = 0
    for filename in MODEL_FILES:
        for cls in tree(filename).body:
            if not isinstance(cls, ast.ClassDef):
                continue
            declared = fields(cls)
            if not declared:
                continue
            model_count += 1
            field_count += len(declared)
            lines += [
                "",
                f"### 21.{model_count} {cls.name}",
                "",
                MODEL_PURPOSES[cls.name],
                "",
                f"Source: `{filename}:{cls.lineno}`. Default table naming: `members_{cls.name.lower()}`.",
                "",
                "| Field | Storage type | Storage and input options | Limits and relationships |",
                "| --- | --- | --- | --- |",
            ]
            for name, call in declared:
                kw = {k.arg: k.value for k in call.keywords}
                kind = call.func.attr
                options = []
                if kind == "ManyToManyField":
                    options.append("multiple relationships")
                else:
                    options.append(
                        "nullable"
                        if code(kw.get("null", ast.Constant(False))) == "True"
                        else "not null"
                    )
                if code(kw.get("blank", ast.Constant(False))) == "True":
                    options.append("model blank allowed")
                if "default" in kw:
                    options.append("default " + code(kw["default"]))
                for k, label in [
                    ("auto_now_add", "creation time"),
                    ("auto_now", "save time"),
                ]:
                    if k in kw and code(kw[k]) == "True":
                        options.append(label)
                if "editable" in kw and code(kw["editable"]) == "False":
                    options.append("not editable")
                rules = []
                if call.args and kind in (
                    "ForeignKey",
                    "OneToOneField",
                    "ManyToManyField",
                ):
                    rules.append("target " + code(call.args[0]))
                for k, label in [
                    ("max_length", "max length"),
                    ("max_digits", "total digits"),
                    ("decimal_places", "decimals"),
                    ("on_delete", "on delete"),
                    ("choices", "choices"),
                    ("validators", "validators"),
                ]:
                    if k in kw:
                        rules.append(label + " " + code(kw[k]).replace("models.", ""))
                if (
                    code(kw.get("unique", ast.Constant(False))) == "True"
                    or kind == "OneToOneField"
                ):
                    rules.append("unique")
                if code(kw.get("db_index", ast.Constant(False))) == "True":
                    rules.append("indexed")
                lines.append(
                    "| "
                    + " | ".join(
                        cell(v)
                        for v in [
                            f"`{name}`",
                            kind,
                            "; ".join(options),
                            "; ".join(rules) or "Domain/form validation applies",
                        ]
                    )
                    + " |"
                )
            for nested in cls.body:
                if isinstance(nested, ast.ClassDef) and nested.name != "Meta":
                    choices = []
                    for item in nested.body:
                        if isinstance(item, ast.Assign) and isinstance(
                            item.value, (ast.Tuple, ast.Constant)
                        ):
                            choices.append(code(item.value))
                    if choices:
                        lines += [
                            "",
                            f"**{nested.name} choices:** "
                            + "; ".join(f"`{v}`" for v in choices)
                            + ".",
                        ]
                if isinstance(nested, ast.ClassDef) and nested.name == "Meta":
                    for item in nested.body:
                        if isinstance(item, ast.Assign):
                            key = code(item.targets[0])
                            if key == "constraints" and isinstance(
                                item.value, (ast.List, ast.Tuple)
                            ):
                                lines += ["", "**Declared database constraints:**"]
                                for constraint in item.value.elts:
                                    lines += [
                                        "",
                                        "- `"
                                        + code(constraint).replace("models.", "")
                                        + "`",
                                    ]
                            elif key in ("ordering", "default_permissions"):
                                lines += ["", f"**{key}:** `{code(item.value)}`."]
    lines += [
        "",
        f"### 21.{model_count + 1} Framework and relationship records",
        "",
        "Django User stores unique username, hashed password, first/last name, email, active/staff/superuser flags and login/join timestamps. Group stores reusable role names. Permission links an action codename to a content type. User-group, user-permission and group-permission relationships implement effective action grants. District-authorized-user and officer-profile-office relationships implement location assignments. ApprovalLevel-approver relationships configure designated users; submitted requests retain independent snapshots.",
        "",
        "Django session storage holds session key, encoded session data and expiry; authenticated cookies identify sessions rather than store plaintext credentials. Framework administration logging supplies additional control-center activity evidence. These framework tables are governed by Django and are not redeclared as custom domain models here.",
        "",
        f"**Dictionary coverage:** {model_count} application models and {field_count} explicitly declared fields, plus implicit primary keys and generated relationship tables.",
        "",
        "**Principal cardinalities:** Branch 1:N Office; Office 1:N Lager/Subdivision/Department; Lager 1:N Member; Member 1:1 current PaymentPlan and original OpeningBalance; each header 1:N category amounts; Member 1:N PaymentTransaction/PaymentReceipt/Child/MemberLedger; Child 1:N ChildDeposit; ChildDeposit 1:N dated transactions and account-years; FixDepositAccount 1:N loans and withdrawals; Loan 1:N servicing entries; MemberLedger 0:1 predecessor and 0:1 successor; Hierarchy 1:N levels/requests/audit events; Receipt 1:N ordered allocations with 0:1 journal each; account identity plus year start uniquely identifies annual calculation records.",
    ]
    return lines, model_count, field_count


def value(node, env):
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name) and node.id in env:
        return env[node.id]
    if isinstance(node, ast.JoinedStr):
        return "".join(
            str(value(n.value, env)) if isinstance(n, ast.FormattedValue) else n.value
            for n in node.values
        )
    return code(node)


def route_calls(nodes, env=None):
    env = env or {}
    for node in nodes:
        if isinstance(node, ast.For):
            for row in ast.literal_eval(node.iter):
                names = [n.id for n in node.target.elts]
                yield from route_calls(node.body, dict(zip(names, row)))
        else:
            for call in ast.walk(node):
                if (
                    isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Name)
                    and call.func.id == "path"
                ):
                    named = {k.arg: k.value for k in call.keywords}
                    if "name" in named:
                        yield str(value(call.args[0], env)), str(
                            value(named["name"], env)
                        ), code(call.args[1]), named


def routes():
    lines = [
        "",
        "## 22. Application route inventory",
        "",
        "Routes below are the named application routes in the reviewed URL configuration, including the expanded subdivision, department and Lager master routes. Paths are relative to the site root. A registered route does not imply a supported action: audit mutation, journal deletion and permanent-history edit/delete handlers intentionally reject writes. Function requirements define the action grant, methods and safeguards. Django's generated advanced-admin model URLs are framework routes and are not individually enumerated.",
    ]
    count = 0
    for filename, prefix, title in [
        ("config/urls.py", "", "Authentication and session routes"),
        ("members/access_urls.py", "admin/", "Custom administration routes"),
        ("members/urls.py", "", "Business workspace routes"),
    ]:
        lines += [
            "",
            "### " + title,
            "",
            "| Path | URL name | Handler | Additional dispatch options |",
            "| --- | --- | --- | --- |",
        ]
        for path, name, handler, _ in route_calls(tree(filename).body):
            count += 1
            extra = []
            if "export" in name:
                extra.append("CSV export")
            if name.startswith("child_interest_"):
                extra.append("child_account=True")
            if (
                path.startswith(("subdivisions/", "schools/", "lagers/"))
                and "extra_master" in handler
            ):
                extra.append("kind=" + path.split("/")[0])
            if (
                "write_disabled" in handler
                or "permanent_history_action" in handler
                or name in ("payment_transaction_delete", "child_entry_delete")
            ):
                extra.append("action intentionally denied")
            lines.append(
                "| "
                + " | ".join(
                    cell(v)
                    for v in [
                        f"`/{prefix}{path}`",
                        f"`{name}`",
                        f"`{handler}`",
                        "; ".join(extra) or "See functional requirements",
                    ]
                )
                + " |"
            )
    lines += [
        "",
        f"**Named route coverage:** {count} application routes. Advanced administration is mounted at `/admin/django/`; custom administration at `/admin/`.",
    ]
    return lines, count


def function_index():
    lines = [
        "",
        "## 23. Implementation function index",
        "",
        "This source index connects the business functions to their concrete implementation entry points. It includes top-level functions and class methods in production Python files, excluding migrations, tests, standard admin registration declarations and empty modules. Private helpers are included where they implement shared safeguards. A signature documents an implementation reference, not a public external API or an authorization grant. Business validation and expected outcomes are specified in chapters 3–18.",
    ]
    files = sorted((ROOT / "members").glob("*.py")) + sorted(
        (ROOT / "config").glob("*.py")
    )
    files += sorted((ROOT / "members/management/commands").glob("*.py"))
    count = 0
    for path in files:
        if path.name.startswith(("test", "__")) or path.name in (
            "admin.py",
            "admin_apps.py",
            "urls.py",
            "access_urls.py",
        ):
            continue
        filename = path.relative_to(ROOT).as_posix()
        entries = []
        parsed = tree(filename)
        for node in parsed.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                entries.append((node.name, node))
            elif isinstance(node, ast.ClassDef):
                for child in node.body:
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        entries.append((f"{node.name}.{child.name}", child))
        if not entries:
            continue
        lines += [
            "",
            f"### {filename}",
            "",
            "| Function and signature | Source line and requirement family |",
            "| --- | --- |",
        ]
        for name, node in entries:
            count += 1
            signature = f"{name}({code(node.args)})"
            doc = ast.get_docstring(node)
            matches = [
                (key, family)
                for key, family in REQUIREMENT_FAMILIES.items()
                if path.stem.startswith(key)
            ]
            family = (
                max(matches, key=lambda pair: len(pair[0]))[1]
                if matches
                else (
                    "FR-AUTH; NFR-SEC"
                    if filename.startswith("config/")
                    else "Shared controls; chapters 3–18"
                )
            )
            purpose = family + (". " + doc.splitlines()[0] if doc else "")
            lines.append(
                f"| `{cell(signature)}` | Line {node.lineno}. {cell(purpose)} |"
            )
    lines += [
        "",
        f"**Function index coverage:** {count} production Python functions/methods.",
        "",
        "Browser responsibilities are implemented in `static/js/app.js` (form interactions and navigation), `static/js/session.js` (activity/session coordination) and `static/js/contribution-balances.js` (allocation/remainder previews). Browser previews do not replace server validation. Dependency pins and build/test commands are defined in `requirements.txt`, `requirements-dev.txt`, `pyproject.toml` and `package.json`.",
    ]
    return lines, count


def build_docx(page_map):
    spec = importlib.util.spec_from_file_location(
        "edcs_document_renderer", Path(__file__).with_name("build_user_manual.py")
    )
    renderer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(renderer)
    renderer.SOURCE = SOURCE
    renderer.OUTPUT = OUTPUT
    renderer.build(page_map)
    with zipfile.ZipFile(OUTPUT) as archive:
        parts = {name: archive.read(name) for name in archive.namelist()}
    parts["word/header1.xml"] = parts["word/header1.xml"].replace(
        b"USER MANUAL", b"SOFTWARE REQUIREMENTS SPECIFICATION"
    )
    parts["word/footer1.xml"] = (
        parts["word/footer1.xml"]
        .replace(b"EDCS-UM-001", b"EDCS-SRS-002")
        .replace(b"v1.1", b"v1.0")
        .replace(b"15 November 2026", b"22 November 2026")
    )
    wns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    doc_tree = ET.fromstring(parts["word/document.xml"])
    for table in doc_tree.findall(f".//{{{wns}}}tbl"):
        first = table.find(f"{{{wns}}}tr/{{{wns}}}tc")
        first_text = "".join(first.itertext()) if first is not None else ""
        if "Function and signature" in first_text:
            widths = [6600, 3260]
            grid = table.find(f"{{{wns}}}tblGrid")
            for column, width in zip(list(grid), widths):
                column.set(f"{{{wns}}}w", str(width))
            for row in table.findall(f"{{{wns}}}tr"):
                for cell_node, width in zip(row.findall(f"{{{wns}}}tc"), widths):
                    cell_node.find(f"{{{wns}}}tcPr/{{{wns}}}tcW").set(
                        f"{{{wns}}}w", str(width)
                    )
    # Give the tall workflow diagram sufficient printed height for readable text.
    namespaces = {
        "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
        "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    }
    for drawing in doc_tree.findall(".//wp:inline", namespaces):
        description = drawing.find("wp:docPr", namespaces).get("descr", "")
        if description == "Contribution allocation and approval flow":
            extent = drawing.find("wp:extent", namespaces)
            scale = 7000000 / int(extent.get("cy"))
            dimensions = {
                "cx": str(int(int(extent.get("cx")) * scale)),
                "cy": "7000000",
            }
            extent.attrib.update(dimensions)
            drawing.find(".//a:xfrm/a:ext", namespaces).attrib.update(dimensions)
    parts["word/document.xml"] = ET.tostring(
        doc_tree, encoding="utf-8", xml_declaration=True
    )
    core = ET.fromstring(parts["docProps/core.xml"])
    ns = {"dc": "http://purl.org/dc/elements/1.1/"}
    core.find("dc:title", ns).text = "EDCS Software Requirements Specification"
    core.find("dc:subject", ns).text = (
        "Detailed business and function level requirements, version 1.0"
    )
    core.find("dc:description", ns).text = (
        "Current workspace baseline through migration 0032. Prepared 22 November 2026. Draft for business review."
    )
    parts["docProps/core.xml"] = ET.tostring(
        core, encoding="utf-8", xml_declaration=True
    )
    with zipfile.ZipFile(OUTPUT, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in parts.items():
            archive.writestr(name, data)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--page-map", type=Path)
    args = parser.parse_args()
    narrative = SOURCE.read_text().split(MARKER)[0].rstrip()
    data, models, fields_count = dictionary()
    route_lines, route_count = routes()
    index, function_count = function_index()
    text = narrative + "\n\n" + "\n".join(data + route_lines + index) + "\n"
    ids = re.findall(r"^### (FR-[A-Z]+-\d+) ", text, re.M)
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate functional requirement identifiers")
    SOURCE.write_text(text)
    build_docx(json.loads(args.page_map.read_text()) if args.page_map else {})
    print(
        json.dumps(
            {
                "functional_requirements": len(ids),
                "models": models,
                "fields": fields_count,
                "routes": route_count,
                "functions": function_count,
                "words": len(text.split()),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
