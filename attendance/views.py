from datetime import date, datetime

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import HttpResponse
from django.utils import timezone

from core.decorators import hr_or_admin_required,hr_only_required
from core.models import User
from attendance.models import AttendanceRecord, MonthlyAttendanceSheet 
from attendance.utils import (
    normalize_year_month, build_monthly_summary, generate_monthly_excel,
    regenerate_and_save_monthly_sheet, build_team_monthly_summary,
    generate_team_monthly_excel, build_weekly_summary, build_yearly_summary,
    generate_weekly_excel, generate_yearly_excel, get_week_options,
)
from django.db.models import Q
from attendance.models import AttendanceRecord, MonthlyAttendanceSheet, OvertimePermission,  OvertimeRequest
from projects.models import Project, ProjectAssignment
from django.urls import reverse
from core.utils import get_active_branch, get_manager_team
from leaves.utils import approved_on_leave_today

@login_required
def punch(request):
    today = timezone.localdate()
    record, _ = AttendanceRecord.objects.get_or_create(user=request.user, date=today)

    if request.method == 'POST':
        action = request.POST.get('action')
        now = timezone.now()
        if action == 'in' and not record.in_time:
            record.in_time = now
            record.save()
            messages.success(request, f"Punched in at {timezone.localtime(now).strftime('%I:%M %p')}")
        elif action == 'out' and record.in_time and not record.out_time:
            record.out_time = now
            record.save()
            messages.success(request, f"Punched out at {timezone.localtime(now).strftime('%I:%M %p')}")

        regenerate_and_save_monthly_sheet(request.user, today.year, today.month)
        return redirect('attendance:punch')

    my_records = AttendanceRecord.objects.filter(user=request.user)[:30]
    return render(request, 'attendance/punch.html', {'record': record, 'my_records': my_records})


@login_required
def my_attendance(request):
    records = AttendanceRecord.objects.filter(user=request.user)
    return render(request, 'attendance/my_attendance.html', {'records': records})


@hr_or_admin_required
def attendance_reports(request):
    records = AttendanceRecord.objects.select_related('user').all()[:200]
    late_count = AttendanceRecord.objects.filter(is_late=True).count()
    return render(request, 'attendance/reports.html', {'records': records, 'late_count': late_count})


@hr_or_admin_required
def employee_attendance(request, user_id):
    emp_user = get_object_or_404(User, id=user_id)
    records = AttendanceRecord.objects.filter(user=emp_user)
    return render(request, 'attendance/employee_attendance.html', {'emp_user': emp_user, 'records': records})


from leaves.utils import approved_on_leave_today

@hr_or_admin_required
def active_attendance_today(request):
    today = timezone.localdate()
    active_branch = get_active_branch(request)

    active_users = User.objects.filter(profile__status='ACTIVE').exclude(
        id=request.user.id
    ).select_related('department', 'profile')
    if active_branch:
        active_users = active_users.filter(branch=active_branch)

    records = AttendanceRecord.objects.filter(date=today, user__in=active_users).select_related('user')
    records_by_user = {r.user_id: r for r in records}

    leave_user_ids = {r.user_id for r in approved_on_leave_today(active_branch)}

    rows = []
    for u in active_users:
        r = records_by_user.get(u.id)
        is_present = bool(r and r.in_time)
        is_on_leave = (u.id in leave_user_ids) and not is_present
        rows.append({
            'user': u,
            'in_time': r.in_time if r else None,
            'out_time': r.out_time if r else None,
            'total_hours': r.total_hours if r else 0,
            'is_late': r.is_late if r else False,
            'is_present': is_present,
            'is_on_leave': is_on_leave,
        })
    rows.sort(key=lambda row: (not row['is_present'], (row['user'].first_name or row['user'].username).lower()))

    present_count = sum(1 for row in rows if row['is_present'])
    leave_count = sum(1 for row in rows if row['is_on_leave'])
    absent_count = len(rows) - present_count - leave_count

    return render(request, 'attendance/active_attendance_today.html', {
        'rows': rows, 'today': today,
        'present_count': present_count, 'absent_count': absent_count,
        'leave_count': leave_count,
        'total_active': len(rows),
    })

# ---------------------------------------------------------------------------
# Unified weekly / monthly / yearly attendance view
# ---------------------------------------------------------------------------
def _attendance_view_params(request):
    today = timezone.localdate()
    view_type = request.GET.get('view', 'monthly')
    if view_type not in ('monthly', 'weekly', 'yearly'):
        view_type = 'monthly'
    year = int(request.GET.get('year', today.year))
    month = int(request.GET.get('month', today.month))
    week = int(request.GET.get('week', 1))
    year, month = normalize_year_month(year, month)
    return view_type, year, month, week


def _build_attendance_context(user, request, emp_user=None):
    today = timezone.localdate()
    view_type, year, month, week = _attendance_view_params(request)

    context = {
        'view_type': view_type, 'year': year, 'month': month, 'week': week,
        'year_options': list(range(today.year - 5, today.year + 2)),
        'month_options': [(i, date(2000, i, 1).strftime('%B')) for i in range(1, 13)],
        'week_options': get_week_options(year, month),
        'emp_user': emp_user,
    }

    if view_type == 'weekly':
        context['weekly_summary'] = build_weekly_summary(user, year, month, week)
    elif view_type == 'yearly':
        context['yearly_summary'] = build_yearly_summary(user, year)
    else:
        context['view_type'] = 'monthly'
        context['monthly_summary'] = build_monthly_summary(user, year, month)

    return context


@login_required
def my_attendance_view(request):
    context = _build_attendance_context(request.user, request)
    return render(request, 'attendance/attendance_view.html', context)


@login_required
def download_my_attendance_view(request):
    view_type, year, month, week = _attendance_view_params(request)
    if view_type == 'weekly':
        summary = build_weekly_summary(request.user, year, month, week)
        buffer = generate_weekly_excel(summary)
        filename = f"attendance_{request.user.username}_week{week}_{year}_{month:02d}.xlsx"
    elif view_type == 'yearly':
        summary = build_yearly_summary(request.user, year)
        buffer = generate_yearly_excel(summary)
        filename = f"attendance_{request.user.username}_{year}.xlsx"
    else:
        summary = build_monthly_summary(request.user, year, month)
        buffer = generate_monthly_excel(summary)
        filename = f"attendance_{request.user.username}_{year}_{month:02d}.xlsx"

    response = HttpResponse(
        buffer.read(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


@login_required
def my_monthly_attendance_archive(request):
    sheets = MonthlyAttendanceSheet.objects.filter(user=request.user)
    return render(request, 'attendance/monthly_archive.html', {'sheets': sheets, 'emp_user': None})


@hr_or_admin_required
def employee_attendance_view(request, user_id):
    emp_user = get_object_or_404(User, id=user_id)
    context = _build_attendance_context(emp_user, request, emp_user=emp_user)
    return render(request, 'attendance/attendance_view.html', context)


@hr_or_admin_required
def download_employee_attendance_view(request, user_id):
    emp_user = get_object_or_404(User, id=user_id)
    view_type, year, month, week = _attendance_view_params(request)
    if view_type == 'weekly':
        summary = build_weekly_summary(emp_user, year, month, week)
        buffer = generate_weekly_excel(summary)
        filename = f"attendance_{emp_user.username}_week{week}_{year}_{month:02d}.xlsx"
    elif view_type == 'yearly':
        summary = build_yearly_summary(emp_user, year)
        buffer = generate_yearly_excel(summary)
        filename = f"attendance_{emp_user.username}_{year}.xlsx"
    else:
        summary = build_monthly_summary(emp_user, year, month)
        buffer = generate_monthly_excel(summary)
        filename = f"attendance_{emp_user.username}_{year}_{month:02d}.xlsx"

    response = HttpResponse(
        buffer.read(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


@hr_or_admin_required
def monthly_attendance_archive_hr(request, user_id):
    emp_user = get_object_or_404(User, id=user_id)
    sheets = MonthlyAttendanceSheet.objects.filter(user=emp_user)
    return render(request, 'attendance/monthly_archive.html', {'sheets': sheets, 'emp_user': emp_user})


# ---------------------------------------------------------------------------
# Combined monthly register for ALL active employees
# ---------------------------------------------------------------------------
@hr_or_admin_required
def team_monthly_attendance(request):
    from django.db.models import Q
    today = timezone.localdate()
    year = int(request.GET.get('year', today.year))
    month = int(request.GET.get('month', today.month))
    year, month = normalize_year_month(year, month)

    query = request.GET.get('q', '').strip()
    role_filter = request.GET.get('role', '')
    department_filter = request.GET.get('department', '')

    active_branch = get_active_branch(request)

    active_users = User.objects.filter(profile__status='ACTIVE').exclude(
        id=request.user.id
    ).select_related('department', 'profile')
    if active_branch:
        active_users = active_users.filter(branch=active_branch)

    if query:
        active_users = active_users.filter(
            Q(first_name__icontains=query) |
            Q(last_name__icontains=query) |
            Q(username__icontains=query) |
            Q(employee_id__icontains=query)
        )
    if role_filter:
        active_users = active_users.filter(role=role_filter)
    if department_filter:
        if department_filter == 'No Department':
            active_users = active_users.filter(department__isnull=True)
        else:
            active_users = active_users.filter(department__name=department_filter)

    active_users = active_users.order_by('first_name', 'username')
    summaries = build_team_monthly_summary(active_users, year, month)

    from payroll.models import SalaryStructure
    salary_map = {
        s.user_id: s.gross for s in SalaryStructure.objects.filter(user__in=active_users)
    }
    for summary in summaries:
        summary['salary'] = salary_map.get(summary['user'].id)

    dept_base = User.objects.filter(profile__status='ACTIVE')
    if active_branch:
        dept_base = dept_base.filter(branch=active_branch)

    all_departments = sorted({
        u.department.name for u in dept_base.select_related('department') if u.department
    })
    has_no_dept = dept_base.filter(department__isnull=True).exists()
    department_options = all_departments + (['No Department'] if has_no_dept else [])
    role_options = [('EMPLOYEE', 'Employee'), ('MANAGER', 'Manager'), ('HR', 'HR'), ('ADMIN', 'Admin')]

    current_year = today.year
    year_options = list(range(current_year - 5, current_year + 2))
    month_options = [(i, date(2000, i, 1).strftime('%B')) for i in range(1, 13)]

    return render(request, 'attendance/team_monthly_attendance.html', {
        'summaries': summaries,
        'year': year, 'month': month,
        'month_name': summaries[0]['month_name'] if summaries else date(year, month, 1).strftime('%B %Y'),
        'day_labels': [d['day_label'] for d in summaries[0]['daily_rows']] if summaries else [],
        'total_active': len(summaries),
        'year_options': year_options,
        'month_options': month_options,
        'department_options': department_options,
        'role_options': role_options,
        'query': query,
        'selected_role': role_filter,
        'selected_department': department_filter,
    })

@hr_or_admin_required
def download_team_monthly_attendance(request, year, month):
    from django.db.models import Q
    year, month = normalize_year_month(year, month)

    query = request.GET.get('q', '').strip()
    role_filter = request.GET.get('role', '')
    department_filter = request.GET.get('department', '')

    active_branch = get_active_branch(request)

    active_users = User.objects.filter(profile__status='ACTIVE').exclude(
        id=request.user.id
    ).select_related('department', 'profile')
    if active_branch:
        active_users = active_users.filter(branch=active_branch)

    if query:
        active_users = active_users.filter(
            Q(first_name__icontains=query) |
            Q(last_name__icontains=query) |
            Q(username__icontains=query) |
            Q(employee_id__icontains=query)
        )
    if role_filter:
        active_users = active_users.filter(role=role_filter)
    if department_filter:
        if department_filter == 'No Department':
            active_users = active_users.filter(department__isnull=True)
        else:
            active_users = active_users.filter(department__name=department_filter)

    active_users = active_users.order_by('first_name', 'username')
    summaries = build_team_monthly_summary(active_users, year, month)

    from payroll.models import SalaryStructure
    salary_map = {
        s.user_id: s.gross for s in SalaryStructure.objects.filter(user__in=active_users)
    }
    for summary in summaries:
        summary['salary'] = salary_map.get(summary['user'].id)

    buffer = generate_team_monthly_excel(summaries, year, month)
    filename = f"team_attendance_{year}_{month:02d}.xlsx"
    response = HttpResponse(
        buffer.read(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response
def _overtime_manageable_projects(user, branch=None):
    """HR and Admin can see across every project (Admin is enforced as
    read-only inside the view below, never here). A Manager only sees
    projects they created or lead. Optionally scoped to a branch — a
    project "belongs" to a branch through its manager or its assigned
    employees."""
    qs = Project.objects.filter(status__in=['APPROVED', 'COMPLETED'])
    if user.role in ('HR', 'ADMIN'):
        if branch:
            qs = qs.filter(Q(manager__branch=branch) | Q(assignments__user__branch=branch)).distinct()
        return qs
    if user.is_manager():
        return qs.filter(Q(manager=user) | Q(lead=user)).distinct()
    return qs.none()


@login_required
def overtime_permissions(request):
    is_admin = request.user.role == 'ADMIN'
    if not (request.user.role == 'HR' or request.user.is_manager() or is_admin):
        messages.error(request, "You do not have permission to view this page.")
        return redirect('core:dashboard')

    active_branch = get_active_branch(request)
    projects = _overtime_manageable_projects(request.user, branch=active_branch)

    if request.method == 'POST':
        if is_admin:
            messages.error(request, "Admin has view-only access and cannot grant overtime permissions.")
            return redirect('attendance:overtime_permissions')

        project = projects.filter(id=request.POST.get('project')).first()
        employee_id = request.POST.get('employee')
        perm_date = request.POST.get('date')
        permission_type = request.POST.get('permission_type', OvertimePermission.TYPE_BOTH)

        if not project:
            messages.error(request, "You cannot grant overtime for that project.")
            return redirect('attendance:overtime_permissions')

        valid_ids = {str(a.user_id) for a in project.assignments.all()}
        if project.lead_id:
            valid_ids.add(str(project.lead_id))
        if employee_id not in valid_ids:
            messages.error(request, "That employee is not on this project's team.")
            return redirect('attendance:overtime_permissions')

        if not perm_date:
            messages.error(request, "Choose a date.")
            return redirect('attendance:overtime_permissions')

        OvertimePermission.objects.update_or_create(
            employee_id=employee_id, project=project, date=perm_date,
            defaults={
                'permission_type': permission_type,
                'notes': request.POST.get('notes', ''),
                'authorized_by': request.user,
            },
        )
        messages.success(request, "Overtime / Sunday work permission granted.")
        return redirect('attendance:overtime_permissions')

    projects_data = [
        {
            'id': p.id,
            'name': p.name,
            'manager': p.manager.get_full_name() or p.manager.username if p.manager else '-',
            'employees': [
                {'id': a.user_id, 'name': a.user.get_full_name() or a.user.username}
                for a in p.assignments.all()
            ] + ([{'id': p.lead_id, 'name': p.lead.get_full_name() or p.lead.username}] if p.lead_id else []),
        }
        for p in projects.prefetch_related('assignments__user').select_related('lead', 'manager')
    ]

    permissions = OvertimePermission.objects.filter(project__in=projects).select_related(
        'employee', 'project', 'project__manager', 'authorized_by'
    )
    if active_branch:
        permissions = permissions.filter(employee__branch=active_branch)
    pending_requests = OvertimeRequest.objects.none()
    pending_hr_requests = OvertimeRequest.objects.none()
    request_history = OvertimeRequest.objects.none()

    if request.user.is_manager():
        team = get_manager_team(request.user)
        pending_requests = OvertimeRequest.objects.filter(
            employee__in=team, status='PENDING_MANAGER'
        ).select_related('employee', 'project')
        request_history = OvertimeRequest.objects.filter(
            employee__in=team
        ).exclude(status='PENDING_MANAGER').select_related(
            'employee', 'project', 'reviewed_by_manager'
        )[:100]

    elif request.user.role == 'HR':
        base_qs = OvertimeRequest.objects.select_related(
            'employee', 'project', 'reviewed_by_manager', 'reviewed_by_hr'
        )
        if active_branch:
            base_qs = base_qs.filter(employee__branch=active_branch)
        pending_hr_requests = base_qs.filter(status='PENDING_HR')
        request_history = base_qs.exclude(status='PENDING_HR').order_by('-submitted_at')[:150]

    elif is_admin:
        # Admin stays fully view-only here — sees everything, acts on nothing.
        base_qs = OvertimeRequest.objects.select_related(
            'employee', 'project', 'reviewed_by_manager', 'reviewed_by_hr'
        )
        if active_branch:
            base_qs = base_qs.filter(employee__branch=active_branch)
        pending_hr_requests = base_qs.filter(status='PENDING_HR')
        request_history = base_qs.exclude(status='PENDING_HR').order_by('-submitted_at')[:150]

    return render(request, 'attendance/overtime_permissions.html', {
        'pending_requests': pending_requests,
        'pending_hr_requests': pending_hr_requests,
        'request_history': request_history,
        'is_admin': is_admin,
        'is_manager': request.user.is_manager(),
        'is_hr': request.user.role == 'HR',
    })

@login_required
def revoke_overtime_permission(request, permission_id):
    permission = get_object_or_404(OvertimePermission, id=permission_id)
    can_manage = (
        request.user.role == 'HR'
        or permission.project.manager_id == request.user.id
        or permission.project.lead_id == request.user.id
    )
    if not can_manage:
        messages.error(request, "You cannot revoke this permission.")
        return redirect('attendance:overtime_permissions')
    if request.method == 'POST':
        permission.delete()
        messages.info(request, "Permission revoked.")
    return redirect('attendance:overtime_permissions')
@login_required
def my_overtime_permissions(request):
    """Employee's own OT/Sunday/Holiday hub: submit new requests here and
    track both submitted requests and any project-based permissions HR/
    Manager has separately granted."""
    user = request.user

    assigned_project_ids = ProjectAssignment.objects.filter(user=user, is_current=True).values_list('project_id', flat=True)
    led_project_ids = Project.objects.filter(lead=user).values_list('id', flat=True)
    my_projects = Project.objects.filter(
        Q(id__in=assigned_project_ids) | Q(id__in=led_project_ids),
        status__in=['APPROVED', 'COMPLETED'],
    ).distinct()

    if request.method == 'POST':
        project_id = request.POST.get('project')
        req_date_raw = request.POST.get('date')
        request_type = request.POST.get('request_type', OvertimeRequest.TYPE_OT)
        start_time_raw = request.POST.get('start_time')
        end_time_raw = request.POST.get('end_time')
        reason = request.POST.get('reason', '')

        allowed_project_ids = set(str(pid) for pid in my_projects.values_list('id', flat=True))

        if not project_id or project_id not in allowed_project_ids:
            messages.error(request, "Select a project you're assigned to.")
            return redirect('attendance:my_overtime_permissions')
        if not req_date_raw or not start_time_raw or not end_time_raw:
            messages.error(request, "Date, start time and end time are required.")
            return redirect('attendance:my_overtime_permissions')

        try:
            req_date = datetime.strptime(req_date_raw, '%Y-%m-%d').date()
            start_time_val = datetime.strptime(start_time_raw, '%H:%M').time()
            end_time_val = datetime.strptime(end_time_raw, '%H:%M').time()
        except ValueError:
            messages.error(request, "Invalid date or time format.")
            return redirect('attendance:my_overtime_permissions')

        ot_request = OvertimeRequest(
            employee=user,
            project_id=project_id,
            department=user.department,
            date=req_date,
            request_type=request_type,
            start_time=start_time_val,
            end_time=end_time_val,
            reason=reason,
        )
        ot_request.status = 'PENDING_MANAGER' if ot_request.get_manager() else 'APPROVED'
        ot_request.save()

        if user.is_manager():
            ot_request.status = 'PENDING_HR'
        else:
            ot_request.status = 'PENDING_MANAGER' if ot_request.get_manager() else 'PENDING_HR'
        ot_request.save()

        messages.success(request, "Your OT/Sunday/Holiday request has been submitted.")
        return redirect('attendance:my_overtime_permissions')
    
    my_requests = OvertimeRequest.objects.filter(employee=user).select_related(
        'project', 'reviewed_by_manager', 'reviewed_by_hr'
    )

    permissions = OvertimePermission.objects.filter(
        employee=user
    ).select_related('project', 'project__manager', 'authorized_by').order_by('-date')

    today = timezone.localdate()
    upcoming = [p for p in permissions if p.date >= today]
    past = [p for p in permissions if p.date < today]

    return render(request, 'attendance/my_overtime_permissions.html', {
        'upcoming': upcoming, 'past': past,
        'my_projects': my_projects, 'my_requests': my_requests,
    })
@hr_only_required
def update_monthly_overrides(request, user_id):
    """HR can override an employee's CL Quota and Total (PH/Sunday) count
    for one specific month — e.g. to account for festival holidays that
    aren't automatically detected. Admin stays view-only, same as
    everywhere else."""
    emp_user = get_object_or_404(User, id=user_id)
    today = timezone.localdate()
    year, month = today.year, today.month

    if request.method == 'POST':
        try:
            year, month = normalize_year_month(
                int(request.POST.get('year', year)), int(request.POST.get('month', month))
            )
        except (TypeError, ValueError):
            messages.error(request, "Invalid month selected.")
            return redirect('attendance:employee_attendance_view', user_id=emp_user.id)

        cl_quota_raw = request.POST.get('cl_quota_override', '').strip()
        ph_sunday_raw = request.POST.get('ph_sunday_override', '').strip()

        sheet, _ = MonthlyAttendanceSheet.objects.get_or_create(user=emp_user, year=year, month=month)
        sheet.cl_quota_override = int(cl_quota_raw) if cl_quota_raw else None
        sheet.ph_sunday_override = int(ph_sunday_raw) if ph_sunday_raw else None
        sheet.save()

        regenerate_and_save_monthly_sheet(emp_user, year, month)
        messages.success(request, f"Updated overrides for {emp_user} — {year}-{month:02d}.")

    return redirect(f"{reverse('attendance:employee_attendance_view', args=[emp_user.id])}?view=monthly&year={year}&month={month}")

@login_required
def approve_overtime_request(request, request_id):
    ot_request = get_object_or_404(OvertimeRequest, id=request_id)
    user = request.user
    manager = ot_request.get_manager()

    if not (user.is_manager() and manager and manager.id == user.id and ot_request.status == 'PENDING_MANAGER'):
        messages.error(request, "You cannot act on this request.")
        return redirect('attendance:overtime_permissions')

    if request.method == 'POST':
        ot_request.approve_by_manager(user, comment=request.POST.get('comment', ''))
        messages.success(request, f"Approved OT/Sunday/Holiday request for {ot_request.employee}.")
    return redirect('attendance:overtime_permissions')


@login_required
def reject_overtime_request(request, request_id):
    ot_request = get_object_or_404(OvertimeRequest, id=request_id)
    user = request.user
    manager = ot_request.get_manager()

    if not (user.is_manager() and manager and manager.id == user.id and ot_request.status == 'PENDING_MANAGER'):
        messages.error(request, "You cannot act on this request.")
        return redirect('attendance:overtime_permissions')

    if request.method == 'POST':
        ot_request.reject_by_manager(user, comment=request.POST.get('comment', ''))
        messages.info(request, f"Rejected OT/Sunday/Holiday request for {ot_request.employee}.")
    return redirect('attendance:overtime_permissions')

@login_required
def approve_overtime_request_hr(request, request_id):
    ot_request = get_object_or_404(OvertimeRequest, id=request_id)
    active_branch = get_active_branch(request)

    can_act = (
        request.user.role == 'HR'
        and ot_request.status == 'PENDING_HR'
        and (not active_branch or ot_request.employee.branch_id == active_branch.id)
    )
    if not can_act:
        messages.error(request, "You cannot act on this request.")
        return redirect('attendance:overtime_permissions')

    if request.method == 'POST':
        ot_request.approve_by_hr(request.user, comment=request.POST.get('comment', ''))
        messages.success(request, f"Approved OT/Sunday/Holiday request for {ot_request.employee}.")
    return redirect('attendance:overtime_permissions')


@login_required
def reject_overtime_request_hr(request, request_id):
    ot_request = get_object_or_404(OvertimeRequest, id=request_id)
    active_branch = get_active_branch(request)

    can_act = (
        request.user.role == 'HR'
        and ot_request.status == 'PENDING_HR'
        and (not active_branch or ot_request.employee.branch_id == active_branch.id)
    )
    if not can_act:
        messages.error(request, "You cannot act on this request.")
        return redirect('attendance:overtime_permissions')

    if request.method == 'POST':
        ot_request.reject_by_hr(request.user, comment=request.POST.get('comment', ''))
        messages.info(request, f"Rejected OT/Sunday/Holiday request for {ot_request.employee}.")
    return redirect('attendance:overtime_permissions')
