from datetime import date, timedelta
from decimal import Decimal
from django.utils import timezone as dj_timezone

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.urls import reverse
from django.utils import timezone
from core.utils import get_active_branch

from core.decorators import hr_or_admin_required, hr_only_required
from core.models import User
from employees.models import EmployeeProfile
from increments.models import IncrementRequest,  IncrementCycleSkip, IncrementFeedback, CooReport
from increments.forms import IncrementRequestForm, IncrementFeedbackForm
from payroll.models import SalaryStructure
from projects.models import ProjectAssignment
from increments.forms import IncrementRequestForm, ManagerIncrementRequestForm, IncrementFeedbackForm


from core.decorators import coo_required

def _add_years(d, years):
    try:
        return d.replace(year=d.year + years)
    except ValueError:
        # Feb 29 landing on a non-leap year
        return d.replace(month=2, day=28, year=d.year + years)


def _last_increment_date(user):
    """The date their pay was last actually increased, or their joining
    date if they've never had one — this is the baseline for the next
    one-year anniversary."""
    last = IncrementRequest.objects.filter(user=user, status='APPROVED').order_by('-effective_date').first()
    if last:
        return last.effective_date
    return user.date_joined_company


@hr_or_admin_required
def increment_list(request):
    today = timezone.localdate()

    all_requests = IncrementRequest.objects.select_related(
        'user', 'feedback_manager', 'approved_by'
    ).prefetch_related('feedback').order_by('-created_at')

    # ---------- Section 1: Active Increment Requests ----------
    seen_users = set()
    in_progress = []
    for inc in all_requests.filter(status='PENDING'):
        if inc.user_id in seen_users:
            continue
        seen_users.add(inc.user_id)
        in_progress.append(inc)
    pending_user_ids = {inc.user_id for inc in in_progress}

    # ---------- Section 2: Due for Annual Increment ----------
    window_end = today + timedelta(days=30)
    active_profiles = EmployeeProfile.objects.filter(
        status='ACTIVE'
    ).exclude(user_id__in=pending_user_ids).select_related('user')

    due_soon = []
    for profile in active_profiles:
        user = profile.user
        # Guard: skip any profile whose linked user is missing or has no pk.
        if not user or not user.pk:
            continue

        base_date = _last_increment_date(user)
        if not base_date:
            continue
        anniversary = _add_years(base_date, 1)
        if anniversary > window_end:
            continue
        if IncrementCycleSkip.objects.filter(user=user, anniversary_date=anniversary).exists():
            continue
        try:
            current_basic = user.salary_structure.basic
        except SalaryStructure.DoesNotExist:
            current_basic = 0
        due_soon.append({
            'user': user,
            'anniversary_date': anniversary,
            'days_left': (anniversary - today).days,
            'last_increment_date': base_date,
            'current_basic': current_basic,
        })
    due_soon.sort(key=lambda d: d['anniversary_date'])

    # ---------- Section 3: Increment History ----------
    history_by_user = {}
    for inc in all_requests.exclude(status='PENDING'):
        if not inc.user_id:
            continue
        entry = history_by_user.setdefault(inc.user_id, {'user': inc.user, 'increments': [], 'latest': inc})
        entry['increments'].append(inc)
    history_cards = sorted(history_by_user.values(), key=lambda h: h['latest'].created_at, reverse=True)

    just_completed = None
    confirm_id = request.GET.get('confirm')
    if confirm_id:
        just_completed = IncrementRequest.objects.filter(id=confirm_id).select_related('user').first()

    return render(request, 'increments/list.html', {
        'in_progress': in_progress,
        'due_soon': due_soon,
        'history_cards': history_cards,
        'just_completed': just_completed,
    })

def _current_pm_for(user):
    """The Project Manager currently assigned to this employee, via their
    current project assignment on a project led by a Project Manager.
    Mirrors the same lookup used to route leave requests to a PM."""
    assignment = ProjectAssignment.objects.filter(
        user=user, is_current=True,
        project__manager__role='PROJECT_MANAGER',
        project__status__in=['APPROVED', 'COMPLETED'],
    ).select_related('project__manager').order_by('-project__start_date').first()
    return assignment.project.manager if assignment else None

@hr_only_required
def create_increment(request):
    preset_user_id = request.GET.get('user')
    active_branch = get_active_branch(request)
    active_tab = request.GET.get('tab', 'employee')

    employee_form = None
    manager_form = None

    if request.method == 'POST':
        form_type = request.POST.get('form_type', 'employee')
        active_tab = form_type

        if form_type == 'manager':
            manager_form = ManagerIncrementRequestForm(
                request.POST, request.FILES, acting_user=request.user, branch=active_branch
            )
            employee_form = IncrementRequestForm(acting_user=request.user, branch=active_branch)

            if manager_form.is_valid():
                mgr_user = manager_form.cleaned_data['user']
                percent = manager_form.cleaned_data['increment_percent']
                current_basic = manager_form.cleaned_data['current_basic']
                requested_basic = round(float(current_basic) * (1 + float(percent) / 100), 2)
                reason = manager_form.cleaned_data.get('reason', '')

                increment = IncrementRequest.objects.create(
                    user=mgr_user,
                    current_basic=current_basic,
                    requested_basic=requested_basic,
                    effective_date=manager_form.cleaned_data['effective_date'],
                    reason=reason,
                    requested_by=request.user,
                    forwarded_by=request.user,
                    forwarded_at=dj_timezone.now(),
                    hr_notes=reason,
                    status='PENDING_COO',
                )
                IncrementFeedback.objects.create(
                    increment_request=increment,
                    manager=None,
                    suggested_percent=percent,
                    report_file=manager_form.cleaned_data['report_file'],
                    description=reason,
                )
                messages.success(request, f"Increment request for {mgr_user} sent to the COO for approval.")
                return redirect('increments:list')
        else:
            employee_form = IncrementRequestForm(request.POST, acting_user=request.user, branch=active_branch)
            manager_form = ManagerIncrementRequestForm(acting_user=request.user, branch=active_branch)

            if employee_form.is_valid():
                emp_user = employee_form.cleaned_data['user']
                feedback_manager = employee_form.cleaned_data.get('feedback_manager') if emp_user.role == 'EMPLOYEE' else None

                IncrementRequest.objects.create(
                    user=emp_user,
                    current_basic=employee_form.cleaned_data['current_basic'],
                    effective_date=employee_form.cleaned_data['effective_date'],
                    reason=employee_form.cleaned_data.get('reason', ''),
                    requested_by=request.user,
                    feedback_manager=feedback_manager,
                    status='PENDING',
                )
                if feedback_manager:
                    messages.success(request, f"Increment request sent to {feedback_manager} for feedback.")
                else:
                    messages.success(request, f"Increment request created for {emp_user}.")
                return redirect('increments:list')
    else:
        initial = {}
        if preset_user_id:
            initial['user'] = preset_user_id
        employee_form = IncrementRequestForm(acting_user=request.user, branch=active_branch, initial=initial)
        manager_form = ManagerIncrementRequestForm(acting_user=request.user, branch=active_branch)

    # ---------- Employee tab: dropdown auto-fill data ----------
    users_qs = User.objects.filter(role='EMPLOYEE').exclude(id=request.user.id)
    if active_branch:
        users_qs = users_qs.filter(branch=active_branch)
    users_qs = users_qs.select_related('department__manager', 'manager', 'salary_structure')

    user_roles_json = {}
    user_basic_json = {}
    user_manager_json = {}
    for u in users_qs:
        user_roles_json[str(u.id)] = u.role
        try:
            user_basic_json[str(u.id)] = float(u.salary_structure.basic)
        except SalaryStructure.DoesNotExist:
            user_basic_json[str(u.id)] = 0

        pm = _current_pm_for(u)
        user_manager_json[str(u.id)] = pm.id if pm else None

    # ---------- Manager tab: dropdown auto-fill data ----------
    manager_qs = User.objects.filter(role__in=['MANAGER', 'PROJECT_MANAGER', 'GENERAL_MANAGER']).exclude(id=request.user.id)
    if active_branch:
        manager_qs = manager_qs.filter(branch=active_branch)
    manager_qs = manager_qs.select_related('salary_structure')

    manager_role_json = {}
    manager_basic_json = {}
    for m in manager_qs:
        manager_role_json[str(m.id)] = m.get_role_display()
        try:
            manager_basic_json[str(m.id)] = float(m.salary_structure.basic)
        except SalaryStructure.DoesNotExist:
            manager_basic_json[str(m.id)] = 0

    return render(request, 'increments/create.html', {
        'form': employee_form,
        'manager_form': manager_form,
        'active_tab': active_tab,
        'user_roles_json': user_roles_json,
        'user_basic_json': user_basic_json,
        'user_manager_json': user_manager_json,
        'manager_role_json': manager_role_json,
        'manager_basic_json': manager_basic_json,
    })

@hr_only_required
def approve_increment(request, increment_id):
    increment = get_object_or_404(IncrementRequest, id=increment_id)
    if increment.needs_feedback:
        messages.error(request, "This increment must be approved or rejected by the assigned manager, not HR.")
        return redirect('increments:list')

    increment.status = 'APPROVED'
    increment.approved_by = request.user
    increment.save()

    structure, _ = SalaryStructure.objects.get_or_create(user=increment.user, defaults={'basic': 0})
    structure.basic = increment.requested_basic
    structure.save()

    IncrementCycleSkip.objects.filter(user=increment.user).delete()
    return redirect(f"{reverse('increments:list')}?confirm={increment.id}")

@hr_only_required
def reject_increment(request, increment_id):
    increment = get_object_or_404(IncrementRequest, id=increment_id)
    if increment.needs_feedback:
        messages.error(request, "This increment must be approved or rejected by the assigned manager, not HR.")
        return redirect('increments:list')

    increment.status = 'REJECTED'
    increment.approved_by = request.user
    increment.save()
    messages.info(request, f"Increment rejected for {increment.user}.")
    return redirect('increments:list')


@hr_only_required
def dismiss_due_increment(request, user_id):
    emp_user = get_object_or_404(User, id=user_id)
    if request.method == 'POST':
        anniversary_str = request.POST.get('anniversary_date', '')
        try:
            y, m, d = (int(p) for p in anniversary_str.split('-'))
            IncrementCycleSkip.objects.get_or_create(
                user=emp_user, anniversary_date=date(y, m, d),
                defaults={'skipped_by': request.user},
            )
            messages.info(request, f"Dismissed the increment reminder for {emp_user} this cycle.")
        except (ValueError, TypeError):
            messages.error(request, "Something went wrong dismissing that reminder.")
    return redirect('increments:list')


@hr_or_admin_required
def increment_history_detail(request, user_id):
    emp_user = get_object_or_404(User, id=user_id)
    increments = IncrementRequest.objects.filter(
        user=emp_user
    ).exclude(status='PENDING').select_related(
        'approved_by', 'feedback_manager', 'requested_by'
    ).prefetch_related('feedback').order_by('-effective_date')
    return render(request, 'increments/history_detail.html', {'emp_user': emp_user, 'increments': increments})


@login_required
def manager_feedback_list(request):
    if not request.user.is_manager():
        messages.error(request, "Only Managers can view this page.")
        return redirect('core:dashboard')

    increments = IncrementRequest.objects.filter(
        status='PENDING', feedback_manager=request.user
    ).select_related('user').order_by('-created_at')
    return render(request, 'increments/manager_feedback_list.html', {'increments': increments})

@login_required
def submit_increment_feedback(request, increment_id):
    increment = get_object_or_404(IncrementRequest, id=increment_id)

    if not request.user.is_manager() or increment.feedback_manager_id != request.user.id:
        messages.error(request, "You cannot act on this employee's increment.")
        return redirect('increments:manager_feedback_list')

    if increment.status != 'PENDING':
        messages.info(request, "This increment request has already moved past the feedback stage.")
        return redirect('increments:manager_feedback_list')

    existing_feedback = IncrementFeedback.objects.filter(increment_request=increment).first()

    if request.method == 'POST':
        form = IncrementFeedbackForm(request.POST, request.FILES, instance=existing_feedback)
        if form.is_valid():
            feedback = form.save(commit=False)
            feedback.increment_request = increment
            feedback.manager = request.user
            feedback.save()

            increment.status = 'REPORT_SUBMITTED'
            increment.report_submitted_at = dj_timezone.now()
            increment.save()

            messages.success(request, f"Report and feedback submitted for {increment.user}. HR will review it next.")
            return redirect('increments:manager_feedback_list')
    else:
        form = IncrementFeedbackForm(instance=existing_feedback)

    return render(request, 'increments/submit_feedback.html', {'form': form, 'increment': increment})



# ---------- Step 1: HR requests a report ----------
@hr_only_required
def request_performance_report(request, user_id):
    emp_user = get_object_or_404(User, id=user_id)
    dept = emp_user.department
    manager = dept.manager if (dept and dept.manager_id) else emp_user.manager

    if not manager:
        messages.error(request, f"{emp_user} has no manager assigned — cannot request a report.")
        return redirect('increments:list')

    try:
        current_basic = emp_user.salary_structure.basic
    except SalaryStructure.DoesNotExist:
        current_basic = 0

    IncrementRequest.objects.create(
        user=emp_user,
        current_basic=current_basic,
        requested_by=request.user,
        feedback_manager=manager,
        status='PENDING_REPORT',
        report_requested_at=dj_timezone.now(),
    )
    messages.success(request, f"Performance report requested from {manager} for {emp_user}.")
    return redirect('increments:list')


# ---------- Step 2: Manager writes and submits the report ----------
@login_required
def manager_report_inbox(request):
    if not request.user.is_manager():
        messages.error(request, "Only Managers can view this page.")
        return redirect('core:dashboard')

    pending = IncrementRequest.objects.filter(
        status='PENDING_REPORT', feedback_manager=request.user
    ).select_related('user').order_by('-report_requested_at')
    return render(request, 'increments/manager_report_inbox.html', {'requests': pending})


@login_required
def submit_performance_report(request, increment_id):
    increment = get_object_or_404(IncrementRequest, id=increment_id, status='PENDING_REPORT')
    if not request.user.is_manager() or increment.feedback_manager_id != request.user.id:
        messages.error(request, "You cannot submit a report for this employee.")
        return redirect('increments:manager_report_inbox')

    if request.method == 'POST':
        report_text = request.POST.get('performance_report', '').strip()
        if not report_text:
            messages.error(request, "Write the performance report before submitting.")
            return redirect('increments:submit_performance_report', increment_id=increment.id)
        increment.performance_report = report_text
        increment.report_submitted_at = dj_timezone.now()
        increment.status = 'REPORT_SUBMITTED'
        increment.save()
        messages.success(request, f"Report submitted for {increment.user}. HR will review it next.")
        return redirect('increments:manager_report_inbox')

    return render(request, 'increments/submit_performance_report.html', {'increment': increment})


# ---------- Step 3: HR reviews and forwards to CEO ----------
@hr_only_required
def hr_review_reports(request):
    pending = IncrementRequest.objects.filter(
        status='REPORT_SUBMITTED'
    ).select_related('user', 'feedback_manager').order_by('-report_submitted_at')
    return render(request, 'increments/hr_review_reports.html', {'requests': pending})


@hr_only_required
def hr_forward_to_coo(request, increment_id):
    increment = get_object_or_404(IncrementRequest, id=increment_id, status='REPORT_SUBMITTED')

    if request.method == 'POST':
        increment.hr_notes = request.POST.get('hr_notes', '').strip()
        increment.forwarded_by = request.user
        increment.forwarded_at = dj_timezone.now()
        increment.status = 'PENDING_COO'
        increment.save()
        messages.success(request, f"Forwarded {increment.user}'s report to the COO for a decision.")

    return redirect('increments:hr_review_reports')


# ---------- Step 4: COO analyzes and decides the percentage ----------
# (lives in core/coo_views.py — see below)


# ---------- Step 5: HR applies the COO's percentage to the salary ----------
@hr_only_required
def hr_pending_salary_updates(request):
    pending = IncrementRequest.objects.filter(
        status='COO_DECIDED'
    ).select_related('user', 'coo_decided_by').order_by('-coo_decided_at')
    return render(request, 'increments/hr_pending_salary_updates.html', {'requests': pending})


@hr_only_required
def hr_apply_increment(request, increment_id):
    increment = get_object_or_404(IncrementRequest, id=increment_id, status='COO_DECIDED')

    if request.method == 'POST':
        effective_date_raw = request.POST.get('effective_date', '')
        percent = float(increment.coo_percent or 0)

        increment.requested_basic = round(float(increment.current_basic) * (1 + percent / 100), 2)
        increment.effective_date = effective_date_raw or dj_timezone.localdate()
        increment.applied_by = request.user
        increment.applied_at = dj_timezone.now()
        increment.status = 'APPLIED'
        increment.save()

        structure, _ = SalaryStructure.objects.get_or_create(user=increment.user, defaults={'basic': 0})
        structure.basic = increment.requested_basic
        structure.save()

        IncrementCycleSkip.objects.filter(user=increment.user).delete()
        messages.success(request, f"{increment.user}'s salary updated to Rs. {increment.requested_basic} ({percent}% increment).")

    return redirect('increments:hr_pending_salary_updates')

@hr_only_required
def hr_feedback_inbox(request):
    """Every increment request where the assigned manager has submitted
    feedback — HR reviews it here and decides."""
    requests = IncrementRequest.objects.filter(
        status='PENDING', feedback__isnull=False
    ).select_related('user', 'feedback_manager', 'feedback').order_by('-feedback__submitted_at')
    return render(request, 'increments/hr_feedback_inbox.html', {'requests': requests})

@hr_only_required
def hr_reports_inbox(request):
    """Reports tab: every request where the manager has uploaded a report
    and given a percentage suggestion, not yet shared with the COO."""
    requests = IncrementRequest.objects.filter(
        status='REPORT_SUBMITTED'
    ).select_related('user', 'feedback_manager', 'feedback').order_by('-report_submitted_at')
    return render(request, 'increments/hr_reports_inbox.html', {'requests': requests})



@hr_only_required
def hr_coo_approval(request):
    eligible_requests = IncrementRequest.objects.exclude(
        status__in=['PENDING_COO', 'COO_DECIDED', 'APPLIED', 'REJECTED']
    ).select_related('user', 'feedback_manager', 'feedback').order_by('-created_at')

    sent = IncrementRequest.objects.filter(
        forwarded_by__isnull=False
    ).select_related('user', 'feedback', 'coo_decided_by').order_by('-forwarded_at')

    reports = CooReport.objects.select_related('employee', 'uploaded_by').order_by('-uploaded_at')

    if request.method == 'POST' and 'report_file' in request.FILES:
        employee_id = request.POST.get('employee_id') or None
        CooReport.objects.create(
            employee_id=employee_id,
            report_file=request.FILES['report_file'],
            notes=request.POST.get('notes', '').strip(),
            uploaded_by=request.user,
        )
        messages.success(request, "Report sent to the COO for review.")
        return redirect('increments:hr_coo_approval')

    return render(request, 'increments/hr_coo_approval.html', {
        'eligible_requests': eligible_requests,
        'sent': sent,
        'reports': reports,
    })

@hr_only_required
def hr_send_to_coo(request, increment_id):
    increment = get_object_or_404(IncrementRequest, id=increment_id)

    if increment.status in ('PENDING_COO', 'COO_DECIDED', 'APPLIED', 'REJECTED'):
        messages.error(request, "This request has already been sent to the COO or finalized.")
        return redirect('increments:hr_coo_approval')

    if request.method == 'POST':
        feedback, _ = IncrementFeedback.objects.get_or_create(
            increment_request=increment,
            defaults={'manager': increment.feedback_manager}
        )

        uploaded_file = request.FILES.get('report_file')
        if uploaded_file:
            feedback.report_file = uploaded_file
            feedback.save()

        increment.hr_notes = request.POST.get('hr_notes', '').strip()
        increment.forwarded_by = request.user
        increment.forwarded_at = dj_timezone.now()
        increment.status = 'PENDING_COO'
        increment.save()

        if uploaded_file:
            messages.success(request, f"Sent {increment.user}'s report (with file) to the COO for approval.")
        else:
            messages.success(request, f"Sent {increment.user}'s request to the COO (no file attached).")

    return redirect('increments:hr_coo_approval')