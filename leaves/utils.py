from django.db.models import Q
from django.utils import timezone


def not_expired_leaves(qs, today):
    """Excludes permission/leave requests whose date (or end date, for
    multi-day leave) has already passed. Used by BOTH the approvals list
    and the notification-badge count so they always agree — otherwise a
    request can inflate the badge while being hidden from the list itself,
    making it impossible to ever clear."""
    return qs.filter(
        Q(request_type='PERMISSION', permission_date__gte=today) |
        Q(request_type='LEAVE', end_date__gte=today)
    )


def approved_on_leave_today(active_branch=None):
    """Every APPROVED leave/permission request that is actively in effect
    TODAY, scoped to a branch. Used by the HR Dashboard's 'Employees on
    Leave Today' section — this is purely informational for HR, distinct
    from HR's own PENDING_HR approval queue. A Manager's rejection never
    reaches APPROVED status, so rejected requests are naturally absent
    here without any extra filtering."""
    from leaves.models import LeaveRequest

    today = timezone.localdate()
    now_time = timezone.localtime().time()

    qs = LeaveRequest.objects.filter(status='APPROVED').select_related(
        'user', 'user__profile', 'user__department',
        'reviewed_by_manager', 'reviewed_by_hr',
    )
    if active_branch:
        qs = qs.filter(user__branch=active_branch)

    results = []
    for r in qs:
        if r.request_type == 'PERMISSION':
            if not r.permission_date or r.permission_date != today:
                continue
            if r.to_time and now_time > r.to_time:
                continue
        else:  # LEAVE
            if not r.start_date or not r.end_date:
                continue
            if not (r.start_date <= today <= r.end_date):
                continue
        results.append(r)
    return results