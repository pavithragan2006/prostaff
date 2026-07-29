from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from django.db.models import Q
from core.utils import get_active_branch, get_manager_team
from leaves.utils import not_expired_leaves, approved_on_leave_today

from core.models import User
from leaves.models import LeaveRequest, LeaveBalance, LeaveNotification


def _notify(user, leave, message):
    if user is None:
        return
    LeaveNotification.objects.create(user=user, leave_request=leave, message=message)


def _apply_balance_deduction(leave):
    """Only full-day Leave requests draw down a balance. Permission
    requests are hour-based and don't touch the leave balances."""
    if leave.request_type != 'LEAVE':
        return
    balance, _ = LeaveBalance.objects.get_or_create(user=leave.user)
    if leave.leave_type == 'CL':
        balance.cl_balance = max(0, balance.cl_balance - leave.num_days)
    elif leave.leave_type == 'EL':
        balance.el_balance = max(0, balance.el_balance - leave.num_days)
    elif leave.leave_type == 'SICK':
        balance.sick_balance = max(0, balance.sick_balance - leave.num_days)
    balance.save()


@login_required
def my_leaves(request):
    LeaveNotification.objects.filter(user=request.user, is_read=False).update(is_read=True)
    balance, _ = LeaveBalance.objects.get_or_create(user=request.user)

    other_hrs = None
    if request.user.role == 'HR':
        other_hrs = User.objects.filter(role='HR').exclude(id=request.user.id)

    if request.method == 'POST':
        request_type = request.POST.get('request_type', 'PERMISSION')
        if request_type not in ('PERMISSION', 'LEAVE'):
            request_type = 'PERMISSION'

        leave = LeaveRequest(
            user=request.user,
            request_type=request_type,
            reason=request.POST.get('reason', ''),
        )

        if request_type == 'PERMISSION':
            leave.permission_date = request.POST.get('permission_date') or None
            leave.from_time = request.POST.get('from_time') or None
            leave.to_time = request.POST.get('to_time') or None
            leave.num_hours = request.POST.get('num_hours') or None
            if not leave.permission_date:
                messages.error(request, "Please choose a date for the permission request.")
                return redirect('leaves:my_leaves')
        else:
            leave.leave_type = request.POST.get('leave_type')
            leave.start_date = request.POST.get('start_date') or None
            leave.end_date = request.POST.get('end_date') or None
            if not leave.start_date or not leave.end_date:
                messages.error(request, "Please choose both a start and end date for the leave request.")
                return redirect('leaves:my_leaves')

        if request.user.role == 'HR':
            target_hr_id = request.POST.get('target_hr')
            if not target_hr_id:
                messages.error(request, "Select which HR colleague should review your request.")
                return redirect('leaves:my_leaves')
            leave.target_hr_id = target_hr_id

        leave.status = leave.initial_status()
        leave.save()
        messages.success(request, f"{leave.get_request_type_display()} request submitted.")
        return redirect('leaves:my_leaves')

    requests_qs = LeaveRequest.objects.filter(user=request.user)
    return render(request, 'leaves/my_leaves.html', {
        'balance': balance, 'requests': requests_qs, 'other_hrs': other_hrs,
    })


def _team_managed_by(manager):
    return get_manager_team(manager)


@login_required
def leave_approvals(request):
    user = request.user
    today = timezone.localdate()
    context = {'hr_rejected_pending': None, 'on_leave_today': None}

    if user.role == 'HR':
        active_branch = get_active_branch(request)

        # HR's actionable queue: Manager's own requests routed straight
        # to HR, plus HR-self requests targeted at this HR colleague.
        # Employee requests never land here anymore.
        requests_qs = LeaveRequest.objects.filter(
            status='PENDING_HR'
        ).exclude(user=user).filter(
            Q(target_hr__isnull=True) | Q(target_hr=user)
        ).select_related('user', 'user__profile', 'user__department', 'reviewed_by_manager')
        if active_branch:
            requests_qs = requests_qs.filter(user__branch=active_branch)
        context['requests'] = requests_qs

        # INFORMATIONAL ONLY: requests stuck at PENDING_MANAGER because no
        # manager could be resolved (e.g. the department has no manager
        # assigned). HR sees these so they know to fix the department's
        # manager assignment, but per policy cannot approve/reject them —
        # review_leave() now enforces that only the resolved manager can.
      
        # Approved leaves currently in effect today — this is where HR
        # sees the outcome of a Manager's approval, scoped to their branch.
        context['on_leave_today'] = approved_on_leave_today(active_branch)
        
    elif user.role == 'ADMIN':
        active_branch = get_active_branch(request)
        requests_qs = LeaveRequest.objects.select_related(
            'user', 'user__profile', 'user__department', 'reviewed_by_manager', 'reviewed_by_hr', 'target_hr'
        ).all()
        if active_branch:
            requests_qs = requests_qs.filter(user__branch=active_branch)
        context['requests'] = requests_qs
        context['on_leave_today'] = approved_on_leave_today(active_branch)

    elif user.is_manager():
        # A Manager's queue is exclusively their own department's
        # Employees, same branch — Manager's own leave requests never
        # appear here since those route straight to HR.
        team = _team_managed_by(user)
        requests_qs = LeaveRequest.objects.filter(
            user__in=team, status='PENDING_MANAGER'
        ).select_related('user', 'user__profile', 'user__department')
        context['requests'] = requests_qs

    else:
        messages.error(request, "You do not have permission to view this page.")
        return redirect('core:dashboard')

    return render(request, 'leaves/approvals.html', context)


@login_required
def review_leave(request, leave_id, decision):
    user = request.user
    leave = get_object_or_404(LeaveRequest, id=leave_id)
    label = leave.get_request_type_display()

    if user.role == 'ADMIN':
        messages.error(request, "Admin has view-only access and cannot approve or reject requests.")
        return redirect('leaves:approvals')

    # ---- Stage: Manager review (Employee's request) — FINAL decision.
    # Only the employee's own Department Manager (same branch) can act
    # here. HR can no longer stand in — a leave request must be
    # approved/rejected by that specific manager. ----
    if leave.status == 'PENDING_MANAGER':
        approving_manager = leave.get_manager()
        if not (user.is_manager() and approving_manager and approving_manager.id == user.id):
            messages.error(request, "You cannot review this request.")
            return redirect('leaves:approvals')

        leave.reviewed_by_manager = user
        leave.manager_reviewed_at = timezone.now()

        if decision == 'approve':
            leave.status = 'APPROVED'
            leave.save()
            _apply_balance_deduction(leave)
            _notify(leave.user, leave, f"Your {label.lower()} request was approved by your manager.")
            messages.success(request, f"{label} approved for {leave.user}.")
        else:
            leave.status = 'REJECTED'
            leave.save()
            _notify(leave.user, leave, f"Your {label.lower()} request was rejected by your manager.")
            messages.success(request, f"{label} rejected for {leave.user}.")
        return redirect('leaves:approvals')

    # ---- Stage: HR review (Manager's own request, or an HR-self request
    # targeted at this HR colleague) — FINAL decision. ----
    if user.role == 'HR' and leave.status == 'PENDING_HR':
        if leave.user_id == user.id:
            messages.error(request, "You cannot approve or reject your own request.")
            return redirect('leaves:approvals')
        if leave.target_hr_id and leave.target_hr_id != user.id:
            messages.error(request, "This request was routed to a different HR colleague.")
            return redirect('leaves:approvals')

        leave.reviewed_by_hr = user
        leave.hr_reviewed_at = timezone.now()

        if decision == 'approve':
            leave.status = 'APPROVED'
            leave.save()
            _apply_balance_deduction(leave)
            _notify(leave.user, leave, f"Your {label.lower()} request was approved by HR.")
            messages.success(request, f"{label} approved for {leave.user}.")
        else:
            leave.status = 'REJECTED'
            leave.save()
            _notify(leave.user, leave, f"Your {label.lower()} request was rejected by HR.")
            messages.success(request, f"{label} rejected for {leave.user}.")
        return redirect('leaves:approvals')

    messages.error(request, "You cannot review this request.")
    return redirect('core:dashboard')

@login_required
def notifications(request):
    notes = list(
        LeaveNotification.objects.filter(user=request.user)
        .select_related('leave_request', 'leave_request__user')[:50]
    )
    LeaveNotification.objects.filter(user=request.user, is_read=False).update(is_read=True)
    return render(request, 'leaves/notifications.html', {'notes': notes})