from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Q
from django.utils import timezone
from django.urls import reverse

from core.decorators import admin_only_required, hr_or_admin_required, hr_only_required, hr_admin_or_manager_required, role_required
from core.models import User
from core.utils import get_active_branch, user_can_switch_branch

from attendance.models import AttendanceRecord
from employees.models import EmployeeProfile, EmployeeDocument, ResignationRequest, BankDetail
from employees.forms import (
    EmployeeSelfEditForm, NewEmployeeForm, HRDocumentForm, SelfDocumentForm,
    RoleChangeForm, ResignationRequestForm, HREmployeeEditForm,
    EmployeeIdentityForm, BankDetailForm, OnboardHRForm,
)
from employees.id_utils import (
    generate_enrollment_id, generate_employee_id,
    enrollment_prefix_for_branch, employee_id_prefix_for_branch, split_id,
)
from payroll.models import SalaryStructure
from payroll.forms import SalaryStructureForm
from increments.models import IncrementRequest


def _present_user_ids_today():
    today = timezone.localdate()
    return set(
        AttendanceRecord.objects.filter(date=today, in_time__isnull=False).values_list('user_id', flat=True)
    )


def _can_apply_resignation(user):
    # HR can resign too — but their resignation is reviewed by Admin, not
    # by another HR colleague. Admin itself never applies for resignation.
    return user.role in ('EMPLOYEE', 'MANAGER', 'HR', 'ADMIN')


def _can_review_resignation(acting_user, resignation):
    """HR reviews resignations from Employees/Managers. Admin reviews
    resignations from HR — an HR resignation never goes to another HR."""
    if resignation.user.role == 'HR':
        return acting_user.role == 'ADMIN'
    return acting_user.role == 'HR'


def _can_edit_identity(acting_user, emp_user):
    """HR and Admin can edit anyone. A Manager can only edit members of
    their own department, and never themselves."""
    if acting_user.role in ('HR', 'ADMIN'):
        return True
    if acting_user.is_manager():
        return emp_user.department_id is not None and emp_user.department_id == acting_user.department_id and emp_user.id != acting_user.id
    return False


def _department_sort_key(dept_name):
    """Data Entry departments first, then Software, then everything else
    alphabetically."""
    lower = dept_name.lower()
    if 'data entry' in lower:
        priority = 0
    elif 'software' in lower:
        priority = 1
    else:
        priority = 2
    return (priority, lower)


ROLE_WITHIN_DEPT_PRIORITY = {'MANAGER': 0, 'EMPLOYEE': 1}


def _directory_sort_key(emp):
    """HR first (alphabetical, no department grouping). Everyone else is
    grouped by department first (Data Entry, then Software, then the rest
    alphabetically) — and within each department, the Manager appears
    before that department's Employees, alphabetical within each group."""
    name = (emp.first_name or emp.username).lower()

    if emp.role == 'HR':
        return (0, (0, ''), 0, name)

    dept_key = _department_sort_key(emp.department.name if emp.department else 'zzz_no_department')
    role_within_dept = ROLE_WITHIN_DEPT_PRIORITY.get(emp.role, 2)
    return (1, dept_key, role_within_dept, name)


BRANCH_ORDER = ['B01', 'B02', 'B03', 'B04']


def _primary_branch(branches):
    """Picks the 'home' branch for an HR/Admin/COO user out of their
    selected accessible branches — lowest branch code wins (Chennai first)."""
    branches = list(branches)
    if not branches:
        return None
    branches.sort(key=lambda b: BRANCH_ORDER.index(b.code) if b.code in BRANCH_ORDER else 99)
    return branches[0]


@login_required
def my_profile(request):
    profile, _ = EmployeeProfile.objects.get_or_create(user=request.user)
    bank_detail, _ = BankDetail.objects.get_or_create(user=request.user)
    documents = request.user.documents.all()
    pending_resignation = ResignationRequest.objects.filter(
        user=request.user, status__in=['PENDING', 'NEGOTIATING']
    ).order_by('-submitted_at').first()

    return render(request, 'employees/my_profile.html', {
        'profile': profile, 'bank_detail': bank_detail, 'documents': documents,
        'can_apply_resignation': _can_apply_resignation(request.user),
        'pending_resignation': pending_resignation,
    })


@login_required
def edit_my_profile(request):
    """Employee's own edit page — separate from the read-only my_profile
    view. Photo, the handful of self-editable fields, bank details, and
    document upload/delete all live here."""
    profile, _ = EmployeeProfile.objects.get_or_create(user=request.user)
    bank_detail, _ = BankDetail.objects.get_or_create(user=request.user)

    if request.method == 'POST':
        form = EmployeeSelfEditForm(request.POST, request.FILES, instance=profile, user_instance=request.user)
        bank_form = BankDetailForm(request.POST, instance=bank_detail)

        saved_anything = False
        if form.is_valid():
            form.save()
            saved_anything = True
        else:
            messages.error(request, 'Please fix the highlighted profile fields below.')

        if bank_form.is_valid():
            bank_form.save()
            saved_anything = True
        else:
            messages.error(request, 'Please fix the highlighted bank detail fields below.')

        if saved_anything:
            messages.success(request, 'Your profile has been updated.')
        return redirect('employees:edit_my_profile')
    else:
        form = EmployeeSelfEditForm(instance=profile, user_instance=request.user)
        bank_form = BankDetailForm(instance=bank_detail)

    documents = request.user.documents.all()
    doc_form = SelfDocumentForm()

    return render(request, 'employees/edit_my_profile.html', {
        'form': form,
        'profile': profile,
        'documents': documents,
        'doc_form': doc_form,
        'bank_form': bank_form,
        'bank_detail': bank_detail,
    })


@login_required
def upload_own_document(request):
    if request.method == 'POST':
        form = SelfDocumentForm(request.POST, request.FILES)
        if form.is_valid():
            doc = form.save(commit=False)
            doc.user = request.user
            doc.uploaded_by = request.user
            doc.save()
            messages.success(request, 'Document uploaded.')
        else:
            messages.error(request, 'Please choose a valid document type and file.')
    return redirect('employees:my_profile')


@login_required
def delete_own_document(request, doc_id):
    """An employee can remove a document they uploaded to their own
    profile. Scoped to request.user so nobody can delete someone else's
    document by guessing an id."""
    doc = get_object_or_404(EmployeeDocument, id=doc_id, user=request.user)
    if request.method == 'POST':
        doc.delete()
        messages.success(request, 'Document deleted.')
    return redirect('employees:my_profile')


@login_required
def apply_resignation(request):
    if not _can_apply_resignation(request.user):
        messages.error(request, "You do not have permission to view this page.")
        return redirect('core:dashboard')

    if ResignationRequest.objects.filter(user=request.user, status='PENDING').exists():
        messages.info(request, "You already have a resignation request pending HR's review.")
        return redirect('employees:my_profile')

    if request.method == 'POST':
        form = ResignationRequestForm(request.POST)
        if form.is_valid():
            resignation = form.save(commit=False)
            resignation.user = request.user
            resignation.save()
            messages.success(request, "Your resignation request has been submitted to HR.")
            return redirect('employees:my_profile')
    else:
        form = ResignationRequestForm()

    return render(request, 'employees/apply_resignation.html', {'form': form})


@hr_or_admin_required
def resignation_list(request):
    resignations = ResignationRequest.objects.select_related('user').all()
    return render(request, 'employees/resignation_list.html', {'resignations': resignations})


@login_required
def approve_resignation(request, resignation_id):
    resignation = get_object_or_404(ResignationRequest, id=resignation_id)
    if resignation.status != 'PENDING':
        messages.error(request, "This resignation cannot be approved at this stage.")
        return redirect('employees:resignation_list')

    if request.method == 'POST':
        custom_days = request.POST.get('custom_days')
        try:
            days = int(custom_days) if custom_days else int(request.POST.get('notice_period_days', 30))
        except ValueError:
            messages.error(request, "Enter a valid number of days.")
            return redirect('employees:resignation_list')

        if days < 0:
            messages.error(request, "Notice period cannot be negative.")
            return redirect('employees:resignation_list')

        # approve() sets profile.status='EXITED', exit_date and exit_reason.
        resignation.approve(hr_user=request.user, notice_period_days=days)
        messages.success(
            request,
            f"Resignation approved for {resignation.user}. Notice period: {days} days. Exit date: {resignation.exit_date}."
        )
    return redirect('employees:resignation_list')


@login_required
def reject_resignation(request, resignation_id):
    resignation = get_object_or_404(ResignationRequest, id=resignation_id)
    if resignation.status != 'PENDING':
        messages.error(request, "This resignation cannot be rejected at this stage.")
        return redirect('employees:resignation_list')

    if request.method == 'POST':
        resignation.reject(hr_user=request.user)
        messages.info(request, f"Resignation rejected for {resignation.user}.")
    return redirect('employees:resignation_list')


@login_required
def negotiate_resignation(request, resignation_id):
    resignation = get_object_or_404(ResignationRequest, id=resignation_id)
    if resignation.status != 'PENDING':
        messages.error(request, "You can only negotiate while the request is pending.")
        return redirect('employees:resignation_list')

    if request.method == 'POST':
        message = request.POST.get('message', '').strip()
        if not message:
            messages.error(request, "Write a message before sending.")
            return redirect('employees:resignation_list')
        resignation.send_negotiation(hr_user=request.user, message=message)
        messages.success(request, f"Your message has been sent to {resignation.user}.")
    return redirect('employees:resignation_list')


@login_required
def accept_resignation_offer(request, resignation_id):
    resignation = get_object_or_404(ResignationRequest, id=resignation_id, user=request.user)
    if resignation.status != 'NEGOTIATING':
        messages.error(request, "There is no active offer to accept.")
        return redirect('employees:my_profile')

    if request.method == 'POST':
        resignation.accept_offer()
        messages.success(request, "You've accepted HR's offer. Your resignation has been withdrawn.")
    return redirect('employees:my_profile')


@login_required
def quit_after_negotiation(request, resignation_id):
    resignation = get_object_or_404(ResignationRequest, id=resignation_id, user=request.user)
    if resignation.status != 'NEGOTIATING':
        messages.error(request, "There is no active offer to respond to.")
        return redirect('employees:my_profile')

    if request.method == 'POST':
        resignation.quit_anyway(message=request.POST.get('message', ''))
        messages.success(request, "Your response has been sent to HR. They'll confirm your notice period shortly.")
    return redirect('employees:my_profile')


@hr_only_required
def onboard_employee(request):
    active_branch = get_active_branch(request)
    if request.method == 'POST':
        form = NewEmployeeForm(request.POST, acting_user=request.user)
        if form.is_valid():
            user = form.save(commit=False)
            if not user_can_switch_branch(request.user):
                user.branch = request.user.branch
            user.set_password(form.cleaned_data['password'])
            department = form.cleaned_data.get('department')
            if department and department.manager_id and user.role != User.ROLE_MANAGER:
                user.manager = department.manager
            else:
                user.manager = None
            user.employee_id = generate_employee_id(user.branch)
            user.save()
            profile = EmployeeProfile.objects.create(user=user, status='ONBOARDING')
            profile.enrollment_id = generate_enrollment_id(user.branch)
            profile.save()
            BankDetail.objects.get_or_create(user=user)
            messages.success(request, f'{user} onboarded successfully (Employee ID {user.employee_id}, Enrollment ID {profile.enrollment_id}). Share their login credentials securely.')
            return redirect('employees:employee_detail', user_id=user.id)
    else:
        initial = {}
        if active_branch and not user_can_switch_branch(request.user):
            initial['branch'] = active_branch.id
        form = NewEmployeeForm(initial=initial, acting_user=request.user)

    return render(request, 'employees/onboard.html', {'form': form, 'locked_branch': not user_can_switch_branch(request.user)})


@role_required('ADMIN')
def toggle_branch_admin_access(request, user_id):
    emp_user = get_object_or_404(User, id=user_id, role='HR')
    if request.method == 'POST':
        emp_user.can_access_all_branches = not emp_user.can_access_all_branches
        emp_user.save()
        state = "granted" if emp_user.can_access_all_branches else "revoked"
        messages.success(request, f"All-branch access {state} for {emp_user}.")
    return redirect('employees:edit_employee_profile', user_id=emp_user.id)


@hr_or_admin_required
def employee_directory(request):
    query = request.GET.get('q', '')
    department_filter = request.GET.get('department', '')
    status_filter = request.GET.get('status', '')

    employees = User.objects.exclude(id=request.user.id).select_related('department', 'profile')

    active_branch = get_active_branch(request)

    if request.user.role == 'HR':
        employees = employees.exclude(role='ADMIN')

    if active_branch:
        employees = employees.filter(Q(branch=active_branch) | Q(role='ADMIN')).distinct()

    if query:
        employees = employees.filter(
            Q(first_name__icontains=query) |
            Q(username__icontains=query) |
            Q(employee_id__icontains=query)
        )

    if department_filter:
        if department_filter == 'No Department':
            employees = employees.filter(department__isnull=True)
        else:
            employees = employees.filter(department__name=department_filter)

    employees = list(employees)

    base_qs = User.objects.exclude(id=request.user.id).exclude(profile__status='ONBOARDING')
    if request.user.role == 'HR':
        base_qs = base_qs.exclude(role__in=['HR', 'ADMIN'])
    if active_branch:
        base_qs = base_qs.filter(branch=active_branch)
    dept_qs = base_qs.select_related('department')
    dept_names = sorted(
        {emp.department.name for emp in dept_qs if emp.department},
        key=_department_sort_key,
    )
    has_no_dept = base_qs.filter(department__isnull=True).exists()
    departments = dept_names + (['No Department'] if has_no_dept else [])

    employee_rows = []
    for emp in employees:
        profile = getattr(emp, 'profile', None)
        if profile and profile.status == 'EXITED':
            emp_status = 'EXITED'
        elif profile and profile.status == 'ONBOARDING':
            emp_status = 'ONBOARD'
        else:
            emp_status = 'ACTIVE'

        if status_filter and status_filter != emp_status:
            continue
        emp.display_status = emp_status
        employee_rows.append(emp)

    employee_rows.sort(key=_directory_sort_key)

    return render(request, 'employees/directory.html', {
        'employees': employee_rows,
        'query': query,
        'departments': departments,
        'selected_department': department_filter,
        'selected_status': status_filter,
    })


@hr_admin_or_manager_required
def employee_detail(request, user_id):
    """HR/Admin/Manager view. Read-only by default. HR/Admin/the employee's
    Manager can toggle Edit mode (?edit=1)."""
    emp_user = get_object_or_404(User, id=user_id)

    if request.user.role == 'HR' and emp_user.role == 'ADMIN':
        messages.error(request, "You do not have permission to view this account.")
        return redirect('employees:directory')

    if request.user.is_manager() and request.user.role not in ('HR', 'ADMIN'):
        if emp_user.department_id != request.user.department_id:
            messages.error(request, "You can only view profiles of your own department's staff.")
            return redirect('employees:my_department')

    profile, _ = EmployeeProfile.objects.get_or_create(user=emp_user)
    bank_detail, _ = BankDetail.objects.get_or_create(user=emp_user)
    documents = emp_user.documents.all()
    doc_form = HRDocumentForm()
    role_form = RoleChangeForm(instance=emp_user, acting_user=request.user)
    latest_resignation = ResignationRequest.objects.filter(user=emp_user).first()
    can_review_resignation = _can_review_resignation(request.user, latest_resignation) if latest_resignation else False

    salary_structure = SalaryStructure.objects.filter(user=emp_user).first()

    increments_qs = IncrementRequest.objects.filter(user=emp_user, status='APPROVED').order_by('-effective_date')
    increment_count = increments_qs.count()
    latest_increment = increments_qs.first()

    can_edit_identity = _can_edit_identity(request.user, emp_user)
    edit_mode = can_edit_identity and request.GET.get('edit') == '1'

    return render(request, 'employees/employee_detail.html', {
        'emp_user': emp_user, 'profile': profile, 'bank_detail': bank_detail,
        'documents': documents, 'doc_form': doc_form,
        'role_form': role_form, 'latest_resignation': latest_resignation,
        'can_review_resignation': can_review_resignation,
        'salary_structure': salary_structure,
        'increment_count': increment_count, 'latest_increment': latest_increment,
        'edit_mode': edit_mode, 'can_edit_identity': can_edit_identity,
    })


@login_required
def update_employee_status(request, user_id):
    """HR moves any employee between Onboarding, Active, and Exited.
    Admin can only do this for HR accounts specifically. Moving
    EXITED -> ACTIVE (a rejoin) automatically preserves their prior
    joining date in old_joining_date."""
    emp_user = get_object_or_404(User, id=user_id)

    is_authorized = (
        request.user.role == 'HR' or
        (request.user.role == 'ADMIN' and emp_user.role == 'HR')
    )
    if not is_authorized:
        messages.error(request, "You do not have permission to perform this action.")
        return redirect('employees:edit_employee_profile', user_id=emp_user.id)

    profile, _ = EmployeeProfile.objects.get_or_create(user=emp_user)

    if request.method == 'POST':
        new_status = request.POST.get('status')
        if new_status in ('ONBOARDING', 'ACTIVE', 'EXITED'):
            if new_status == 'ACTIVE' and profile.status == 'EXITED' and not profile.old_joining_date:
                profile.old_joining_date = emp_user.date_joined_company
            profile.status = new_status
            profile.save()
            messages.success(request, f"{emp_user}'s status changed to {profile.get_status_display()}.")
        else:
            messages.error(request, "Invalid status selected.")
    return redirect('employees:edit_employee_profile', user_id=emp_user.id)


@hr_only_required
def upload_document(request, user_id):
    emp_user = get_object_or_404(User, id=user_id)
    if request.method == 'POST':
        form = HRDocumentForm(request.POST, request.FILES)
        if form.is_valid():
            doc = form.save(commit=False)
            doc.user = emp_user
            doc.uploaded_by = request.user
            doc.save()
            messages.success(request, 'Document uploaded.')
    return redirect('employees:edit_employee_profile', user_id=emp_user.id)


@hr_only_required
def delete_document(request, user_id, doc_id):
    """HR removes a document from an employee's profile."""
    emp_user = get_object_or_404(User, id=user_id)
    doc = get_object_or_404(EmployeeDocument, id=doc_id, user=emp_user)
    if request.method == 'POST':
        doc.delete()
        messages.success(request, 'Document deleted.')
    return redirect('employees:edit_employee_profile', user_id=emp_user.id)


@hr_admin_or_manager_required
def edit_employee_profile(request, user_id):
    emp_user = get_object_or_404(User, id=user_id)

    if not _can_edit_identity(request.user, emp_user):
        messages.error(request, "You do not have permission to edit this profile.")
        return redirect('employees:employee_detail', user_id=emp_user.id)

    profile, _ = EmployeeProfile.objects.get_or_create(user=emp_user)
    bank_detail, _ = BankDetail.objects.get_or_create(user=emp_user)

    employee_id_prefix = employee_id_prefix_for_branch(emp_user.branch)
    enrollment_prefix = enrollment_prefix_for_branch(emp_user.branch)

    if request.method == 'POST':
        form = HREmployeeEditForm(request.POST, instance=emp_user)
        identity_form = EmployeeIdentityForm(request.POST, request.FILES, instance=profile)
        bank_form = BankDetailForm(request.POST, instance=bank_detail)

        enrollment_suffix = request.POST.get('enrollment_suffix', '').strip()
        employee_id_suffix = request.POST.get('employee_id_suffix', '').strip()

        saved_anything = False

        # Each form is validated and saved on its own — one section having
        # no changes (or being left blank) never blocks another section
        # from saving.
        if form.is_valid():
            user_obj = form.save(commit=False)
            if emp_user.role == 'HR':
                user_obj.branch = emp_user.branch  # HR branch is admin-set, not editable here

            current_employee_id_prefix = employee_id_prefix_for_branch(user_obj.branch)
            current_enrollment_prefix = enrollment_prefix_for_branch(user_obj.branch)

            if employee_id_suffix:
                user_obj.employee_id = f"{current_employee_id_prefix}{employee_id_suffix}"

            if user_obj.role != User.ROLE_MANAGER:
                dept = form.cleaned_data.get('department')
                user_obj.manager = dept.manager if (dept and dept.manager_id) else None

            user_obj.save()
            if emp_user.role == 'HR':
                user_obj.accessible_branches.set(form.cleaned_data.get('accessible_branches'))

            employee_id_prefix = current_employee_id_prefix
            enrollment_prefix = current_enrollment_prefix
            saved_anything = True
        else:
            messages.error(request, "Some basic profile fields need fixing — those weren't saved.")

        if identity_form.is_valid():
            profile_obj = identity_form.save(commit=False)
            if enrollment_suffix:
                profile_obj.enrollment_id = f"{enrollment_prefix}{enrollment_suffix}"
            profile_obj.save()
            saved_anything = True
        else:
            messages.error(request, "Some identity fields need fixing — those weren't saved.")

        if bank_form.is_valid():
            bank_form.save()
            saved_anything = True
        else:
            messages.error(request, "Some bank details need fixing — those weren't saved.")

        if saved_anything:
            messages.success(request, f"{emp_user}'s profile has been updated.")

        return redirect('employees:edit_employee_profile', user_id=emp_user.id)
    else:
        form = HREmployeeEditForm(instance=emp_user)
        identity_form = EmployeeIdentityForm(instance=profile)
        bank_form = BankDetailForm(instance=bank_detail)

    documents = emp_user.documents.all()
    doc_form = HRDocumentForm()
    role_form = RoleChangeForm(instance=emp_user, acting_user=request.user)
    salary_structure, _ = SalaryStructure.objects.get_or_create(user=emp_user, defaults={'basic': 0})
    salary_form = SalaryStructureForm(instance=salary_structure)

    return render(request, 'employees/edit_employee_profile.html', {
        'emp_user': emp_user, 'form': form, 'identity_form': identity_form, 'bank_form': bank_form,
        'documents': documents, 'doc_form': doc_form, 'role_form': role_form,
        'profile': profile, 'bank_detail': bank_detail, 'salary_form': salary_form,
        'is_admin_viewer': request.user.role == 'ADMIN',
        # FIXED: was hardcoded to 'HR' only in the duplicate definition that
        # used to win — Admin now correctly sees Role & Access too.
        'can_change_role': request.user.role in ('HR', 'ADMIN'),
        'enrollment_prefix': enrollment_prefix,
        'employee_id_prefix': employee_id_prefix,
        'enrollment_suffix': request.POST.get('enrollment_suffix', split_id(profile.enrollment_id, enrollment_prefix)),
        'employee_id_suffix': request.POST.get('employee_id_suffix', split_id(emp_user.employee_id, employee_id_prefix)),
    })


@hr_or_admin_required
def change_role(request, user_id):
    """FIXED: this used to be defined twice. The version that was actually
    running blocked Admin entirely ('Admin has view-only access and cannot
    change roles') — which contradicted the Role & Access dropdown we built
    for Admin. That block has been removed: both HR and Admin can now
    change roles, with RoleChangeForm itself restricting which roles HR
    (vs Admin) is allowed to pick from."""
    emp_user = get_object_or_404(User, id=user_id)

    if emp_user == request.user:
        messages.error(request, "You cannot change your own role.")
        return redirect('employees:edit_employee_profile', user_id=emp_user.id)

    if request.user.role == 'HR' and emp_user.role in ('HR', 'ADMIN'):
        messages.error(request, "HR cannot change the role of an HR or Admin account.")
        return redirect('employees:edit_employee_profile', user_id=emp_user.id)

    if request.method == 'POST':
        form = RoleChangeForm(request.POST, instance=emp_user, acting_user=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, f"{emp_user}'s role changed to {emp_user.get_role_display()}.")
        else:
            messages.error(request, "Invalid role selection.")
    return redirect('employees:edit_employee_profile', user_id=emp_user.id)


@hr_only_required
def delete_employee(request, user_id):
    emp_user = get_object_or_404(User, id=user_id)
    if emp_user == request.user:
        messages.error(request, "You cannot delete your own account.")
        return redirect('employees:directory')

    profile, _ = EmployeeProfile.objects.get_or_create(user=emp_user)
    if profile.status != 'EXITED':
        messages.error(request, "This employee can only be deleted after their resignation has been accepted.")
        return redirect('employees:employee_detail', user_id=emp_user.id)

    if request.method == 'POST':
        name = str(emp_user)
        emp_user.delete()
        messages.success(request, f"{name} has been removed from the system")
        return redirect('employees:directory')
    return render(request, 'employees/confirm_delete.html', {'emp_user': emp_user})


@hr_only_required
def update_salary_structure(request, user_id):
    emp_user = get_object_or_404(User, id=user_id)
    structure, _ = SalaryStructure.objects.get_or_create(user=emp_user, defaults={'basic': 0})
    if request.method == 'POST':
        form = SalaryStructureForm(request.POST, instance=structure)
        if form.is_valid():
            form.save()
            messages.success(request, 'Salary structure saved.')
        else:
            messages.error(request, 'Please fix the highlighted salary fields.')
    return redirect('employees:edit_employee_profile', user_id=emp_user.id)


@login_required
def my_department(request):
    if not request.user.is_manager():
        messages.error(request, "Only managers can view this page.")
        return redirect('core:dashboard')

    staff = list(
        User.objects.filter(
            department=request.user.department,
            branch=request.user.branch,
        ).exclude(id=request.user.id).select_related('profile')
    )
    present_user_ids = _present_user_ids_today()
    for s in staff:
        s.is_present_today = s.id in present_user_ids
    return render(request, 'employees/my_department.html', {'staff': staff, 'department': request.user.department})


@login_required
def team_member_detail(request, user_id):
    if not request.user.is_manager():
        messages.error(request, "Only managers can view this page.")
        return redirect('core:dashboard')

    emp_user = get_object_or_404(User, id=user_id, department=request.user.department, branch=request.user.branch)
    if emp_user.id == request.user.id:
        messages.error(request, "You cannot view your own team member page.")
        return redirect('employees:my_department')

    profile, _ = EmployeeProfile.objects.get_or_create(user=emp_user)
    records = AttendanceRecord.objects.filter(user=emp_user)[:30]

    today = timezone.localdate()
    is_onboarding = profile.status == 'ONBOARDING'
    is_present_today = AttendanceRecord.objects.filter(
        user=emp_user, date=today, in_time__isnull=False
    ).exists()

    return render(request, 'employees/team_member_detail.html', {
        'emp_user': emp_user, 'profile': profile, 'records': records,
        'is_onboarding': is_onboarding, 'is_present_today': is_present_today,
    })


@login_required
def my_resignation(request):
    if not _can_apply_resignation(request.user):
        messages.error(request, "You do not have permission to view this page.")
        return redirect('core:dashboard')

    resignations = ResignationRequest.objects.filter(user=request.user).order_by('-submitted_at')
    pending_resignation = resignations.filter(status__in=['PENDING', 'NEGOTIATING']).first()

    return render(request, 'employees/my_resignation.html', {
        'resignations': resignations,
        'pending_resignation': pending_resignation,
    })


@admin_only_required
def onboard_hr(request):
    """FIXED: the role-dispatch block used to be indented at the same
    level as `if form.is_valid():` instead of inside it — meaning it ran
    even on an invalid form (crashing with UnboundLocalError on `role`).
    The Employee/Manager branch was also missing a `return redirect(...)`,
    which meant a successful onboard of an Employee/Manager here would
    fall through and implicitly return None, crashing with
    'view didn't return an HttpResponse'. Both are fixed below."""
    if request.method == 'POST':
        form = OnboardHRForm(request.POST, acting_user=request.user)
        if form.is_valid():
            role = form.cleaned_data['role']
            user = form.save(commit=False)
            user.role = role
            user.set_password(form.cleaned_data['password'])

            if role in (User.ROLE_EMPLOYEE, User.ROLE_MANAGER):
                branch = form.cleaned_data['branch']
                department = form.cleaned_data.get('department')
                user.branch = branch
                if department and department.manager_id and role != User.ROLE_MANAGER:
                    user.manager = department.manager
                else:
                    user.manager = None
                user.employee_id = generate_employee_id(user.branch)
                user.save()

                profile = EmployeeProfile.objects.create(user=user, status='ONBOARDING')
                profile.enrollment_id = generate_enrollment_id(user.branch)
                profile.save()
                BankDetail.objects.get_or_create(user=user)

                role_label = 'Manager' if role == User.ROLE_MANAGER else 'Employee'
                messages.success(
                    request,
                    f'{user} onboarded as {role_label} successfully (Employee ID {user.employee_id}, Enrollment ID {profile.enrollment_id}).'
                )
                return redirect('employees:employee_detail', user_id=user.id)

            else:  # HR, Admin, or COO
                user.manager = None
                accessible = form.cleaned_data['accessible_branches']
                user.branch = _primary_branch(accessible)

                if role == User.ROLE_HR:
                    user.employee_id = generate_employee_id(user.branch)

                user.save()
                user.accessible_branches.set(accessible)

                profile = EmployeeProfile.objects.create(user=user, status='ONBOARDING')
                if role == User.ROLE_HR:
                    profile.enrollment_id = generate_enrollment_id(user.branch)
                    profile.save()

                if role == User.ROLE_HR:
                    messages.success(
                        request,
                        f'{user} onboarded as HR successfully (Employee ID {user.employee_id}, Enrollment ID {profile.enrollment_id}).'
                    )
                elif role == User.ROLE_COO:
                    messages.success(request, f'{user} onboarded as COO successfully.')
                else:
                    messages.success(request, f'{user} onboarded as Admin successfully.')

                return redirect('employees:employee_detail', user_id=user.id)
        # form invalid — fall through to re-render with errors below
    else:
        form = OnboardHRForm(acting_user=request.user)

    return render(request, 'employees/onboard_hr.html', {'form': form})


@login_required
def complete_onboarding(request, user_id):
    emp_user = get_object_or_404(User, id=user_id)
    user = request.user

    can_complete = (
        (user.role == 'HR' and emp_user.role in ('EMPLOYEE', 'MANAGER')) or
        (user.role == 'ADMIN' and emp_user.role in ('HR', 'ADMIN'))
    )
    if not can_complete:
        messages.error(request, "You do not have permission to activate this user.")
        return redirect('employees:employee_detail', user_id=emp_user.id)

    profile, _ = EmployeeProfile.objects.get_or_create(user=emp_user)
    if request.method == 'POST':
        profile.status = 'ACTIVE'
        profile.save()
        messages.success(request, f"{emp_user} has completed onboarding and is now Active.")
    return redirect('employees:employee_detail', user_id=emp_user.id)