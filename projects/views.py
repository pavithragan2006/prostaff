from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from django.db.models import Q
from core.utils import get_active_branch

from core.decorators import hr_or_admin_required, hr_only_required
from core.models import User
from projects.models import Project, ProjectAssignment, ProjectSubmission


def _can_manage_project(user):
    """HR, Admin, and any Manager can update or delete a project.
    Employees can never manage a project."""
    return user.is_hr_or_admin() or user.is_manager()


@login_required
def project_list(request):
    active_branch = get_active_branch(request)

    if request.user.is_hr_or_admin():
        projects = Project.objects.filter(status__in=['APPROVED', 'COMPLETED']).prefetch_related('assignments__user')
        if active_branch:
            projects = projects.filter(
                Q(manager__branch=active_branch) |
                Q(created_by__branch=active_branch) |
                Q(assignments__user__branch=active_branch)
            ).distinct()
    else:
        projects = Project.objects.filter(status__in=['APPROVED', 'COMPLETED']).filter(
            Q(assignments__user=request.user) |
            Q(lead=request.user) |
            Q(manager=request.user)
        ).distinct().prefetch_related('assignments__user')

    completed_projects, active_projects, upcoming_projects = [], [], []
    for project in projects:
        if project.phase == 'COMPLETED':
            completed_projects.append(project)
        elif project.phase == 'UPCOMING':
            upcoming_projects.append(project)
        else:
            active_projects.append(project)

    return render(request, 'projects/list.html', {
        'completed_projects': completed_projects,
        'active_projects': active_projects,
        'upcoming_projects': upcoming_projects,
    })


@login_required
def create_project(request):
    """Only Managers can create/publish projects. HR, Admin and Employees
    cannot — Admin gets view-only access to projects and their updates.
    A General Manager additionally picks which Project Manager owns the
    project before choosing the Project Leader."""
    if not request.user.is_manager():
        messages.error(request, "Only Managers can create projects.")
        return redirect('projects:list')

    is_general_manager = request.user.role == User.ROLE_GENERAL_MANAGER
    project_managers = User.objects.none()
    if is_general_manager:
        project_managers = User.objects.filter(
            role=User.ROLE_PROJECT_MANAGER, branch=request.user.branch
        ).order_by('first_name', 'username')

    if request.method == 'POST':
        name = request.POST.get('name')
        employee_ids = request.POST.getlist('employees')
        lead_id = request.POST.get('lead')
        project_manager_id = request.POST.get('project_manager')

        if not name:
            messages.error(request, 'Project name is required.')
            return redirect('projects:create')

        # A General Manager must assign a Project Manager before anything
        # else — the Project Leader can only be chosen after that.
        assigned_manager = request.user
        if is_general_manager:
            valid_pm_ids = set(str(uid) for uid in project_managers.values_list('id', flat=True))
            if not project_manager_id or project_manager_id not in valid_pm_ids:
                messages.error(request, 'Select a Project Manager for this project.')
                return redirect('projects:create')
            assigned_manager = User.objects.get(id=project_manager_id)

        if not employee_ids:
            messages.error(request, 'Select at least one employee to assign to this project.')
            return redirect('projects:create')
        if not lead_id or lead_id not in employee_ids:
            messages.error(request, 'Choose a project lead from among the assigned employees.')
            return redirect('projects:create')

        # Safety: only role=EMPLOYEE users can ever be assigned or lead.
        valid_employee_ids = set(
            str(uid) for uid in User.objects.filter(id__in=employee_ids, role='EMPLOYEE').values_list('id', flat=True)
        )
        employee_ids = [uid for uid in employee_ids if uid in valid_employee_ids]
        if not employee_ids:
            messages.error(request, 'Select at least one valid employee.')
            return redirect('projects:create')
        if lead_id not in valid_employee_ids:
            messages.error(request, 'The project lead must be one of the assigned employees.')
            return redirect('projects:create')

        # Projects are published directly by the Manager who creates them —
        # no separate approval step. They immediately become visible to
        # Admin and HR (view-only) as well as the assigned team.
        project = Project.objects.create(
            name=name,
            description=request.POST.get('description', ''),
            requirements_file=request.FILES.get('requirements_file'),
            start_date=request.POST.get('start_date') or None,
            end_date=request.POST.get('end_date') or None,
            created_by=request.user,
            manager=assigned_manager,
            lead_id=lead_id,
            status='APPROVED',
            reviewed_by=request.user,
            reviewed_at=timezone.now(),
        )
        for emp_id in employee_ids:
            ProjectAssignment.objects.create(project=project, user_id=emp_id, is_current=True)

        messages.success(request, f'"{project.name}" has been published. Assigned employees can now see it.')
        return redirect('projects:project_detail', project_id=project.id)

    employees = User.objects.filter(role='EMPLOYEE', branch=request.user.branch).select_related('department').order_by('first_name', 'username')
    dept_names = sorted({emp.department.name for emp in employees if emp.department})
    has_no_dept = any(emp.department is None for emp in employees)
    departments = dept_names + (['No Department'] if has_no_dept else [])
    employees_data = [
        {
            'id': emp.id,
            'name': emp.get_full_name() or emp.username,
            'empId': emp.employee_id or '-',
            'dept': emp.department.name if emp.department else 'No Department',
        }
        for emp in employees
    ]
    return render(request, 'projects/create.html', {
        'departments': departments,
        'employees_json': employees_data,
        'is_general_manager': is_general_manager,
        'project_managers': project_managers,
    })

@hr_or_admin_required
def project_approvals(request):
    """Legacy approval queue — kept for compatibility but effectively idle
    now that projects are published directly by Managers."""
    pending = Project.objects.filter(status='PENDING').select_related('created_by')
    return render(request, 'projects/approvals.html', {'projects': pending})


@hr_only_required
def approve_project(request, project_id):
    project = get_object_or_404(Project, id=project_id)
    project.status = 'APPROVED'
    project.reviewed_by = request.user
    project.reviewed_at = timezone.now()
    project.save()
    messages.success(request, f'"{project.name}" approved.')
    return redirect('projects:approvals')


@hr_only_required
def reject_project(request, project_id):
    project = get_object_or_404(Project, id=project_id)
    project.status = 'REJECTED'
    project.reviewed_by = request.user
    project.reviewed_at = timezone.now()
    project.save()
    messages.info(request, f'"{project.name}" rejected.')
    return redirect('projects:approvals')


@login_required
def project_detail(request, project_id):
    project = get_object_or_404(Project, id=project_id)
    assignments = project.assignments.select_related('user')

    is_assigned = assignments.filter(user_id=request.user.id).exists()
    is_lead = project.lead_id == request.user.id
    is_project_manager = project.manager_id == request.user.id
    can_view = (
        request.user.is_hr_or_admin() or
        project.created_by_id == request.user.id or
        is_project_manager or
        is_assigned
    )
    if not can_view:
        messages.error(request, "You do not have access to this project.")
        return redirect('projects:list')
    # Project-level management (edit name/description/dates, delete the
    # project outright): HR, Admin, or any Manager can do this. Employees
    # can never manage a project.
    can_edit_team = _can_manage_project(request.user)
    is_creator_manager = project.created_by_id == request.user.id
    can_manage_project = _can_manage_project(request.user)

    # Marking a project as successfully completed is a Manager action,
    # available while the project isn't already completed.
    can_complete_project = request.user.is_manager() and project.status != 'COMPLETED'

    # Submission review permissions:
    # - The creating Manager can fully approve an update OR request corrections.
    # - HR can request corrections (to flag issues) but can never give final
    #   approval — that stays with the Manager who owns the project.
    # - Admin is always view-only here, same as everywhere else on projects.
    can_approve_submissions = is_creator_manager
    can_request_corrections = is_creator_manager or request.user.role == 'HR'

    if request.method == 'POST' and can_edit_team:
        target_user_id = request.POST.get('user')
        target_user = get_object_or_404(User, id=target_user_id)
        if target_user.role != 'EMPLOYEE':
            messages.error(request, 'Only Employees can be assigned to a project.')
            return redirect('projects:project_detail', project_id=project.id)
        ProjectAssignment.objects.create(
            project=project,
            user=target_user,
            role_on_project=request.POST.get('role_on_project', ''),
            start_date=request.POST.get('start_date') or None,
        )
        messages.success(request, 'Team member assigned to project.')
        return redirect('projects:project_detail', project_id=project.id)

    employees = User.objects.filter(role='EMPLOYEE').exclude(id__in=assignments.values_list('user_id', flat=True))
    submissions = project.submissions.select_related('submitted_by', 'reviewed_by')

    return render(request, 'projects/detail.html', {
        'project': project, 'assignments': assignments, 'employees': employees, 'can_edit': can_edit_team,
        'can_manage_project': can_manage_project, 'can_complete_project': can_complete_project,
        'is_lead': is_lead, 'can_approve_submissions': can_approve_submissions,
        'can_request_corrections': can_request_corrections, 'submissions': submissions,
    })


@login_required
def edit_project(request, project_id):
    """HR, Admin, or any Manager can update a project's core details.
    Employees cannot reach this view."""
    project = get_object_or_404(Project, id=project_id)
    if not _can_manage_project(request.user):
        messages.error(request, "You do not have permission to edit this project.")
        return redirect('projects:project_detail', project_id=project.id)

    if request.method == 'POST':
        name = request.POST.get('name')
        if not name:
            messages.error(request, 'Project name is required.')
            return redirect('projects:edit_project', project_id=project.id)
        project.name = name
        project.description = request.POST.get('description', '')
        project.start_date = request.POST.get('start_date') or None
        project.end_date = request.POST.get('end_date') or None
        if request.FILES.get('requirements_file'):
            project.requirements_file = request.FILES['requirements_file']
        project.save()
        messages.success(request, f'"{project.name}" has been updated.')
        return redirect('projects:project_detail', project_id=project.id)

    return render(request, 'projects/edit_project.html', {'project': project})


@login_required
def delete_project(request, project_id):
    """HR, Admin, or any Manager can delete a project outright.
    Employees cannot reach this view."""
    project = get_object_or_404(Project, id=project_id)
    if not _can_manage_project(request.user):
        messages.error(request, "You do not have permission to delete this project.")
        return redirect('projects:project_detail', project_id=project.id)

    if request.method == 'POST':
        name = project.name
        project.delete()
        messages.success(request, f'"{name}" has been deleted.')
        return redirect('projects:list')

    return render(request, 'projects/confirm_delete_project.html', {'project': project})


@login_required
def complete_project(request, project_id):
    """A Manager marks the project as successfully completed. Once
    completed, it moves into the "Successfully Completed" section on the
    projects list."""
    project = get_object_or_404(Project, id=project_id)
    if not request.user.is_manager():
        messages.error(request, "Only Managers can mark a project as completed.")
        return redirect('projects:project_detail', project_id=project.id)

    if request.method == 'POST':
        project.status = 'COMPLETED'
        project.completed_by = request.user
        project.completed_at = timezone.now()
        project.save()
        messages.success(request, f'"{project.name}" marked as successfully completed.')
    return redirect('projects:project_detail', project_id=project.id)


@login_required
def submit_project_update(request, project_id):
    project = get_object_or_404(Project, id=project_id)
    if project.lead_id != request.user.id:
        messages.error(request, "Only the project lead can submit updates for review.")
        return redirect('projects:project_detail', project_id=project.id)

    if request.method == 'POST':
        notes = request.POST.get('notes', '')
        if not notes and not request.FILES.get('file'):
            messages.error(request, "Add notes or attach a file before submitting.")
            return redirect('projects:project_detail', project_id=project.id)
        ProjectSubmission.objects.create(
            project=project,
            submitted_by=request.user,
            notes=notes,
            file=request.FILES.get('file'),
            status='PENDING_REVIEW',
        )
        messages.success(request, 'Update submitted for review.')
    return redirect('projects:project_detail', project_id=project.id)


@login_required
def review_project_submission(request, submission_id, decision):
    """The project's creating Manager can fully approve an update or send it
    back for corrections. HR can also send updates back for corrections
    (since HR can see every project's updates) but can never give final
    approval — that stays with the owning Manager. Admin never reaches this
    view successfully; Admin is view-only on projects everywhere."""
    submission = get_object_or_404(ProjectSubmission, id=submission_id)
    project = submission.project
    user = request.user

    is_creator_manager = project.created_by_id == user.id
    is_hr = user.role == 'HR'

    if not (is_creator_manager or is_hr):
        messages.error(request, "You cannot review this submission.")
        return redirect('projects:project_detail', project_id=project.id)

    if decision == 'approve' and not is_creator_manager:
        messages.error(request, "Only the project's Manager can approve an update. HR can request corrections instead.")
        return redirect('projects:project_detail', project_id=project.id)

    if request.method == 'POST':
        submission.feedback = request.POST.get('feedback', '')
        submission.reviewed_by = user
        submission.reviewed_at = timezone.now()
        submission.status = 'APPROVED' if decision == 'approve' else 'NEEDS_CORRECTION'
        submission.save()
        messages.success(
            request,
            f"Submission {'approved' if decision == 'approve' else 'sent back for corrections'}."
        )
    return redirect('projects:project_detail', project_id=project.id)


@login_required
def my_project_history(request):
    assignments = ProjectAssignment.objects.filter(user=request.user).select_related('project')
    return render(request, 'projects/my_history.html', {'assignments': assignments})