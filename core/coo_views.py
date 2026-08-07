import io
from datetime import date
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.utils import timezone
from django.http import FileResponse
from django.db.models import Q

from core.decorators import  coo_required
from core.models import User
from core.utils import get_active_branch
from leaves.models import LeaveRequest, LeaveNotification
from increments.models import IncrementRequest
from payroll.models import SalaryStructure
from employees.models import EmployeeProfile
from leaves.utils import not_expired_leaves


def _notify(user, leave, message):
    if user is None:
        return
    LeaveNotification.objects.create(user=user, leave_request=leave, message=message)


# Roles the COO oversees directly: HR, every Manager variant, and Admin.
COO_DIRECTORY_ROLES = ['ADMIN', 'HR', 'MANAGER', 'PROJECT_MANAGER', 'GENERAL_MANAGER']

# Admin first, then HR, then every Manager variant grouped together
# (dept-wise ordering is applied on top of this in _coo_directory_sort_key).
_ROLE_TIER = {'ADMIN': 0, 'HR': 1}


def _coo_directory_sort_key(u):
    name = (u.first_name or u.username).lower()
    tier = _ROLE_TIER.get(u.role, 2)  # 2 = any manager variant
    if tier != 2:
        return (tier, '', name)
    dept_name = u.department.name if u.department else 'zzz_no_department'
    return (tier, dept_name.lower(), name)


def _notify(user, leave, message):
    if user is None:
        return
    LeaveNotification.objects.create(user=user, leave_request=leave, message=message)


@coo_required
def coo_dashboard(request):
    tab = request.GET.get('tab', 'directory')
    context = {'tab': tab}

    # The COO only ever sees their own branch (Chennai). request.user.branch
    # must be set correctly on the COO account for this to work — if it
    # isn't, nothing shows up rather than silently showing every branch.
    coo_branch = request.user.branch

    if tab == 'directory':
        leaders = list(
            User.objects.filter(role__in=COO_DIRECTORY_ROLES, branch=coo_branch)
            .select_related('department', 'profile')
        )
        for u in leaders:
            u.dept_display = u.department.name if u.department else '-'
        leaders.sort(key=_coo_directory_sort_key)
        context['leaders'] = leaders

    elif tab == 'leave_approval':
        # PM's own leave now goes to HR, not the COO — so PMs are excluded
        # from the COO's leave history here, even though they still appear
        # in COO_DIRECTORY_ROLES for the Directory tab.
        coo_reviewable_users = User.objects.filter(
            role__in=COO_DIRECTORY_ROLES, branch=coo_branch
        ).exclude(role='PROJECT_MANAGER')

        context['requests'] = LeaveRequest.objects.filter(
            status='PENDING_COO', user__branch=coo_branch
        ).select_related('user', 'user__department').order_by('-applied_at')

        history_qs = LeaveRequest.objects.filter(
            user__in=coo_reviewable_users
        ).exclude(status='PENDING_COO').select_related('user').order_by('-applied_at')
        today = timezone.localdate()
        context['history'] = not_expired_leaves(history_qs, today)[:100]
    elif tab == 'increment_approval':
        context['pending_coo'] = IncrementRequest.objects.filter(
            status='PENDING_COO'
        ).select_related('user', 'feedback_manager', 'feedback', 'forwarded_by').order_by('-forwarded_at')
        context['decided'] = IncrementRequest.objects.filter(
            coo_decided_by__isnull=False
        ).select_related('user').order_by('-coo_decided_at')[:50]

    return render(request, 'core/coo_dashboard.html', context)

@coo_required
def coo_review_leave(request, leave_id, decision):
    leave = get_object_or_404(LeaveRequest, id=leave_id, status='PENDING_COO')
    label = leave.get_request_type_display()

    if request.method == 'POST':
        leave.reviewed_by_hr = request.user  # reuse this field to record the final approver
        leave.hr_reviewed_at = timezone.now()

        if decision == 'approve':
            leave.status = 'APPROVED'
            leave.save()
            _notify(leave.user, leave, f"Your {label.lower()} request was approved by the CEO.")
            messages.success(request, f"{label} approved for {leave.user}.")
        else:
            leave.status = 'REJECTED'
            leave.save()
            _notify(leave.user, leave, f"Your {label.lower()} request was rejected by the CEO.")
            messages.success(request, f"{label} rejected for {leave.user}.")

    return redirect(f"/coo/?tab=leave_approval")


@coo_required
def coo_decide_increment(request, increment_id, decision):
    increment = get_object_or_404(IncrementRequest, id=increment_id, status='PENDING_COO')

    if request.method == 'POST':
        increment.coo_decided_by = request.user
        increment.coo_decided_at = timezone.now()

        if decision == 'approve':
            percent_raw = request.POST.get('coo_percent', '').strip()
            try:
                percent = float(percent_raw)
            except ValueError:
                messages.error(request, "Enter a valid increment percentage.")
                return redirect('/coo/?tab=increment_approval')

            increment.coo_percent = percent
            increment.coo_comment = request.POST.get('coo_comment', '').strip()
            increment.status = 'COO_DECIDED'
            increment.save()
            messages.success(request, f"Decision sent to HR: {percent}% increment for {increment.user}.")
        else:
            reason = request.POST.get('coo_rejection_reason', '').strip()
            if not reason:
                messages.error(request, "A rejection reason is required.")
                return redirect('/coo/?tab=increment_approval')
            increment.coo_rejection_reason = reason
            increment.status = 'REJECTED'
            increment.save()
            messages.info(request, f"Increment rejected for {increment.user}.")

    return redirect('/coo/?tab=increment_approval')

@coo_required
def coo_download_report(request, report_type):
    """Generates a simple PDF report for the COO to download."""
    from reportlab.pdfgen import canvas

    buffer = io.BytesIO()
    p = canvas.Canvas(buffer)
    p.setFont("Helvetica-Bold", 16)
    today = timezone.localdate()

    if report_type == 'directory':
        p.drawString(50, 800, "Leadership Directory Report")
        p.setFont("Helvetica", 10)
        y = 770
        leaders = User.objects.filter(
            Q(role__in=['GENERAL_MANAGER', 'PROJECT_MANAGER']) | Q(role='HR', is_senior_hr=True)
        ).select_related('department')
        for u in leaders:
            p.drawString(50, y, f"{u.get_full_name() or u.username} — {u.get_role_display()} — {u.department or '-'}")
            y -= 16
            if y < 50:
                p.showPage(); y = 800

    elif report_type == 'leave_summary':
        p.drawString(50, 800, "Leadership Leave Summary")
        p.setFont("Helvetica", 10)
        y = 770
        requests = LeaveRequest.objects.filter(
            user__in=User.objects.filter(Q(role='GENERAL_MANAGER') | Q(role='HR', is_senior_hr=True))
        ).select_related('user').order_by('-applied_at')[:100]
        for r in requests:
            p.drawString(50, y, f"{r.user} — {r.get_request_type_display()} — {r.status} — {r.applied_at.date()}")
            y -= 16
            if y < 50:
                p.showPage(); y = 800

    elif report_type == 'increment_summary':
        p.drawString(50, 800, "Increment Approvals Report")
        p.setFont("Helvetica", 10)
        y = 770
        increments = IncrementRequest.objects.filter(
            coo_decided_by__isnull=False
        ).select_related('user').order_by('-coo_decided_at')
        for i in increments:
            p.drawString(50, y, f"{i.user} — {i.status} — {i.coo_final_percent or '-'}% — {i.coo_decided_at.date() if i.coo_decided_at else '-'}")
            y -= 16
            if y < 50:
                p.showPage(); y = 800

    else:
        p.drawString(50, 800, "Unknown report type")

    p.showPage()
    p.save()
    buffer.seek(0)
    filename = f"{report_type}_report_{today}.pdf"
    return FileResponse(buffer, as_attachment=True, filename=filename)