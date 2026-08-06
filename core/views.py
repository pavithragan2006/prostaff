from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from django.utils import timezone
from django.db.models import Q

from core.forms import StyledAuthenticationForm
from attendance.models import AttendanceRecord
from leaves.models import LeaveRequest
from employees.models import EmployeeProfile
from django.contrib import messages
from core.utils import get_active_branch, user_can_switch_branch, get_manager_team
from core.models import Branch


class HRLoginView(auth_views.LoginView):
    template_name = 'core/login.html'
    authentication_form = StyledAuthenticationForm
    redirect_authenticated_user = True


@login_required
def set_active_branch(request):
    if request.method == 'POST':
        user = request.user
        can_switch = user_can_switch_branch(user)
        if not can_switch:
            messages.error(request, "You do not have permission to switch branches.")
            return redirect('core:dashboard')
        branch_id = request.POST.get('branch_id')
        if branch_id:
            allowed = Branch.objects.all() if user.role == 'ADMIN' else user.accessible_branches.all()
            branch = allowed.filter(id=branch_id).first()
            if branch:
                request.session['active_branch_id'] = branch_id
                messages.success(request, f"You are now viewing {branch.name} ({branch.code}).")
            else:
                messages.error(request, "You don't have access to that branch.")
        return redirect(request.POST.get('next') or 'core:dashboard')
    return redirect('core:dashboard')


@login_required
def dashboard(request):
    user = request.user
    context = {'user': user}

    today = timezone.localdate()
    context['today_record'] = AttendanceRecord.objects.filter(user=user, date=today).first()

    active_branch = get_active_branch(request)   # <-- moved above the if/elif, computed once for every role

    if user.role == 'ADMIN':
        base_qs = EmployeeProfile.objects.select_related('user')
        if active_branch:
            base_qs = base_qs.filter(user__branch=active_branch)
        # "Total" counts — every HR/Manager/Employee regardless of
        # Active/Onboarding/Exited status, scoped to the active branch.
        context['hr_count'] = base_qs.filter(user__role='HR').count()
        context['manager_count'] = base_qs.filter(user__role='MANAGER').count()
        context['employee_count'] = base_qs.filter(user__role='EMPLOYEE').count()
    elif user.role == 'HR':
        from leaves.utils import approved_on_leave_today

        base_qs = EmployeeProfile.objects.exclude(user__role__in=['HR', 'ADMIN'])
        if active_branch:
            base_qs = base_qs.filter(user__branch=active_branch)

        context['total_employees'] = base_qs.count()
        active_profiles = base_qs.filter(status='ACTIVE')
        context['active_employees'] = active_profiles.count()
        context['inactive_employees'] = context['total_employees'] - context['active_employees']

        active_user_ids = set(active_profiles.values_list('user_id', flat=True))

        present_today_count = AttendanceRecord.objects.filter(
            date=today, in_time__isnull=False, user_id__in=active_user_ids
        ).values('user_id').distinct().count()

        on_leave_today_count = sum(
            1 for r in approved_on_leave_today(active_branch) if r.user_id in active_user_ids
        )

        context['present_today'] = present_today_count
        context['on_leave_today'] = on_leave_today_count
        context['absent_today'] = max(0, context['active_employees'] - present_today_count - on_leave_today_count)

    else:
        month_start = today.replace(day=1)
        context['month_attendance'] = AttendanceRecord.objects.filter(
            user=user, date__gte=month_start, date__lte=today
        ).count()
        # 'PENDING' is not a valid status (choices are PENDING_MANAGER /
        # PENDING_HR / etc.) — this used to always evaluate to 0.
        context['my_pending_leaves'] = LeaveRequest.objects.filter(
            user=user, status__in=['PENDING_MANAGER', 'PENDING_HR']
        ).count()
        if user.is_manager():
            # Use the same team definition as the approvals queue and
            # notification badge (department-headed reports + direct
            # reports, scoped to the manager's branch) instead of the raw
            # `manager` FK, so this number always matches what the manager
            # actually sees in their approvals list.
            context['team_size'] = get_manager_team(user).count()

    return render(request, 'core/dashboard.html', context)