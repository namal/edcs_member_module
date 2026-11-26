# Membership Status setup and use

This module handles initial activation, inactivation and reactivation. Reactivation keeps the **same member ledger page** and all balances, and starts a new active period at the actual application time. Requests, reasons, approval decisions, attachments and earlier active periods remain in history.

## Administration setup

1. Open **Administration → Users & access** or a role's permission matrix.
2. Grant **Membership Status → View** to officers who need the request list, and **Add** to officers who submit requests. Requests are restricted to their assigned Lagers, branch and zonal-office access.
3. Grant **Membership Status → Edit** to higher-level reviewers. In this module, Edit means permission to review a request; it does not allow rewriting status history. An assigned reviewer can review their designated request without becoming the owner of the member's Lager.
4. Open **Administration → Approval hierarchies → Add hierarchy**.
5. Choose module **Membership Status**, action **Edit existing records**, give the policy a name and make it active.
6. Add the required review levels in order, selecting authorized active reviewers. For manager approval, leave **Allow requester approval at this level** off. When the workflow allows the same user to approve, enable that setting and explicitly include the requester among the level’s approvers. The user also needs Membership Status Edit/review permission. Save with a configuration reason.

A Membership Status policy is mandatory. The general All modules policy does not replace it. Administrators also submit requests. A requester can approve only a level that explicitly permits self-approval and designates that requester. Otherwise a different reviewer is required. A requester who is an administrator cannot override later levels unless every remaining level permits self-approval and assigns that requester. Another authorized administrator can review with a recorded decision reason. Missing/incomplete policies block registration or submission with a clear message.

## Register a member

Register through **Member directory → Register member**. Membership starts as **Awaiting activation** and a higher-level activation request is created automatically. The old general member form no longer changes Active/Inactive status directly. Configure payment plans from the profile as needed; financial postings are blocked while activation is pending. Initial activation takes effect only after approval.

## Inactivation and reactivation

1. Open **Membership Status → New status request**, or the status-request button on the member profile.
2. Select the member and choose **Review member**.
3. Check membership number, name, District/Branch, zonal office, Ledger Code and current status.
4. Select the valid reason, enter a detailed explanation of at least ten characters, and select today or a future requested effective date. Backdating is not allowed.
5. Attach supporting documents where applicable: at most five PDF, JPG or PNG files, each at most 5 MB.
6. Submit for higher-level approval. Requested By, requester EPF and the request date/time are recorded automatically.
7. Follow **Approval progress**. A reviewer uses **Approval requests**, checks all details and files, and records a decision reason. All configured levels must complete before application.

Inactivation reasons include resignation, death, termination, disciplinary action, duplicate membership, eligibility issues, administrative decisions and other approved reasons. Reactivation requires its own reason and approval. A second open request for the same member is blocked. Rejected/cancelled requests preserve the original membership status.

## Effective dates and history

Today-dated requests apply at final approval. If approval arrives later, the actual application time is retained; the system does not retrospectively rewrite status. A future-dated approval remains **Approved · scheduled** until due.

Due requests are checked on authenticated workspace requests. For execution during quiet periods, run this command at least every minute with the application's environment:

```bash
.venv/bin/python manage.py apply_membership_status
```

For example, a deployment scheduler can run it once a minute. Scheduled application rechecks the requester's current permission, Lager and member status. If these changed, the request is marked **Could not apply** and retained for review.

Request details show original captured information, documents, approval/application times, full request history and active periods. The member profile and member ledger show status history; applied changes also appear in Lager activity for users with Membership Status View. Audit Logs retain request creation, decisions, actual status changes and period updates. Supporting files are private authenticated downloads; file bytes are not copied into audit JSON.

Existing member statuses and financial balances are preserved when the migration runs. Existing active records receive an explicitly imported active-period baseline. This module does not automatically settle accounts, remove child deposits, erase financial records or change interest policy. Retirement remains the separate retirement workflow.
