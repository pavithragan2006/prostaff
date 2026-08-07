from django import forms
from core.validators import (
    name_validator, phone_validator, alnum_id_validator,
    bank_account_validator, ifsc_validator, pan_validator, aadhar_validator,
    COUNTRY_CODES,
)
from employees.models import EmployeeProfile, EmployeeDocument, ResignationRequest, BankDetail
from core.models import User
from core.models import Branch , Department 
from core.utils import user_can_switch_branch


NAME_ATTRS = {
    'pattern': "[A-Za-z0-9' -]*", 'title': "Letters, numbers, spaces, hyphens and apostrophes only.",
    'maxlength': 150, 'oninput': "this.value = this.value.replace(/[^A-Za-z0-9' -]/g, '')",
}
PHONE_ATTRS = {
    'inputmode': 'numeric', 'pattern': '[0-9]{10}', 'title': "Enter exactly 10 digits.",
    'maxlength': 10, 'oninput': "this.value = this.value.replace(/[^0-9]/g, '').slice(0, 10)",
}
COUNTRY_CODE_ATTRS = {'class': 'form-control', 'style': 'max-width:150px; flex-shrink:0;'}
ID_ATTRS = {
    'pattern': '[A-Za-z0-9-]*', 'title': "Letters, numbers and hyphens only.",
    'maxlength': 20, 'oninput': "this.value = this.value.replace(/[^A-Za-z0-9-]/g, '')",
}
BANK_ATTRS = {
    'inputmode': 'numeric', 'pattern': '[0-9]{9,18}', 'title': "9 to 18 digits only.",
    'maxlength': 18, 'oninput': "this.value = this.value.replace(/[^0-9]/g, '')",
}
IFSC_ATTRS = {
    'pattern': '[A-Za-z]{4}0[A-Za-z0-9]{6}', 'title': "Format: 4 letters + 0 + 6 alphanumeric (e.g. HDFC0001234).",
    'maxlength': 11, 'style': 'text-transform:uppercase;',
    'oninput': "this.value = this.value.replace(/[^A-Za-z0-9]/g, '').toUpperCase()",
}
PAN_ATTRS = {
    'pattern': '[A-Za-z]{5}[0-9]{4}[A-Za-z]{1}', 'title': "Format: 5 letters + 4 digits + 1 letter (e.g. ABCDE1234F).",
    'maxlength': 10, 'style': 'text-transform:uppercase;',
    'oninput': "this.value = this.value.replace(/[^A-Za-z0-9]/g, '').toUpperCase()",
}
# Aadhar is displayed grouped as "XXXX XXXX XXXX" but stored as a plain
# 12-digit string — the form strips spaces on clean before validation/save.
AADHAR_ATTRS = {
    'inputmode': 'numeric', 'title': "12 digits, shown as 4-4-4.",
    'maxlength': 14,  # 12 digits + 2 inserted spaces
    'oninput': (
        "this.value = this.value.replace(/[^0-9]/g, '').slice(0,12)"
        ".replace(/(.{4})(?=.)/g, '$1 ')"
    ),
}
NUMERIC_ATTRS = {
    'inputmode': 'numeric',
    'oninput': "this.value = this.value.replace(/[^0-9]/g, '')",
}


class EmployeeSelfEditForm(forms.ModelForm):
    """Fields an employee can edit on their own profile — basic info plus
    the extended identity fields (Aadhar, PAN, emergency contact, etc.)
    they fill in themselves. Bank details are handled by a separate
    BankDetailForm on the same page. Status, enrollment ID, employee ID
    and a few other administrative fields stay HR/Admin/Manager-only —
    see EmployeeIdentityForm."""
    first_name = forms.CharField(max_length=150, required=False, validators=[name_validator], widget=forms.TextInput(attrs=NAME_ATTRS))
    last_name = forms.CharField(max_length=150, required=False, validators=[name_validator], widget=forms.TextInput(attrs=NAME_ATTRS))
    email = forms.EmailField(required=False, widget=forms.EmailInput())
    phone_country_code = forms.ChoiceField(choices=COUNTRY_CODES, required=False, widget=forms.Select(attrs=COUNTRY_CODE_ATTRS))
    phone = forms.CharField(max_length=10, required=False, validators=[phone_validator], widget=forms.TextInput(attrs=PHONE_ATTRS))

    aadhar_no = forms.CharField(max_length=14, required=False, validators=[aadhar_validator], widget=forms.TextInput(attrs=AADHAR_ATTRS))
    pan_no = forms.CharField(max_length=10, required=False, validators=[pan_validator], widget=forms.TextInput(attrs=PAN_ATTRS))
    emergency_contact_name = forms.CharField(max_length=100, required=False, validators=[name_validator], widget=forms.TextInput(attrs=NAME_ATTRS))
    emergency_contact_country_code = forms.ChoiceField(choices=COUNTRY_CODES, required=False, widget=forms.Select(attrs=COUNTRY_CODE_ATTRS))
    emergency_contact_phone = forms.CharField(max_length=10, required=False, validators=[phone_validator], widget=forms.TextInput(attrs=PHONE_ATTRS))

    class Meta:
        model = EmployeeProfile
        fields = [
            'address', 'profile_photo',
            'gender', 'marital_status', 'date_of_birth',
            'qualification', 'previous_experience', 'current_company_experience',
            'blood_group', 'uan_pf_number', 'esi_number',
            'aadhar_no', 'pan_no',
            'emergency_contact_name', 'emergency_contact_country_code', 'emergency_contact_phone',
        ]
        widgets = {
            'address': forms.Textarea(attrs={'rows': 3}),
            'date_of_birth': forms.DateInput(attrs={'type': 'date'}),
            'previous_experience': forms.TextInput(attrs={'placeholder': 'e.g. 2 years at XYZ Corp'}),
            'current_company_experience': forms.TextInput(attrs={'placeholder': 'e.g. 1.5 years'}),
        }

    def __init__(self, *args, **kwargs):
        self.user_instance = kwargs.pop('user_instance', None)
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if not isinstance(field.widget, forms.ClearableFileInput):
                field.widget.attrs.setdefault('class', 'form-control')
        if self.user_instance:
            self.fields['first_name'].initial = self.user_instance.first_name
            self.fields['last_name'].initial = self.user_instance.last_name
            self.fields['email'].initial = self.user_instance.email
            self.fields['phone'].initial = self.user_instance.phone
            self.fields['phone_country_code'].initial = self.user_instance.phone_country_code
        # Show Aadhar pre-formatted with spaces even though it's stored raw.
        if self.instance and self.instance.pk and self.instance.aadhar_no:
            digits = self.instance.aadhar_no
            self.initial['aadhar_no'] = ' '.join(digits[i:i + 4] for i in range(0, len(digits), 4))

    def clean_aadhar_no(self):
        return self.cleaned_data.get('aadhar_no', '').replace(' ', '')

    def clean_pan_no(self):
        return self.cleaned_data.get('pan_no', '').upper()

    def save(self, commit=True):
        profile = super().save(commit=commit)
        if self.user_instance:
            self.user_instance.first_name = self.cleaned_data.get('first_name', '')
            self.user_instance.last_name = self.cleaned_data.get('last_name', '')
            self.user_instance.email = self.cleaned_data.get('email', '')
            self.user_instance.phone = self.cleaned_data.get('phone', '')
            self.user_instance.phone_country_code = self.cleaned_data.get('phone_country_code') or '+91'
            if commit:
                self.user_instance.save()
        return profile




class DepartmentSelect(forms.Select):
    """Tags each <option> with data-branch=<branch id> so the edit-profile
    page's JS can show only departments belonging to whichever Branch is
    currently selected in the Branch dropdown on the same form."""
    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex, attrs)
        if value:
            dept = Department.objects.filter(pk=value.value if hasattr(value, 'value') else value).select_related('branch').first()
            if dept and dept.branch_id:
                option['attrs']['data-branch'] = str(dept.branch_id)
        return option


class BranchAwareDepartmentField(forms.ModelChoiceField):
    """Displays only the department name — no branch code suffix. The
    branch code was only there because department names repeat across
    branches (unique_together = ('name','branch')); JS-based filtering by
    the selected Branch handles disambiguation instead."""
    def label_from_instance(self, obj):
        return obj.name

class NewEmployeeForm(forms.ModelForm):
    """Used by HR to onboard a new employee (creates the User).
    Employee ID / Enrollment ID are auto-generated from the branch at save
    time (see views.onboard_employee) — not collected here."""
    password = forms.CharField(widget=forms.PasswordInput, help_text="Temporary password for the employee")
    first_name = forms.CharField(max_length=150, validators=[name_validator], widget=forms.TextInput(attrs=NAME_ATTRS))
    last_name = forms.CharField(max_length=150, required=False, validators=[name_validator], widget=forms.TextInput(attrs=NAME_ATTRS))
    phone_country_code = forms.ChoiceField(choices=COUNTRY_CODES, required=False, widget=forms.Select(attrs=COUNTRY_CODE_ATTRS))
    phone = forms.CharField(max_length=10, required=False, validators=[phone_validator], widget=forms.TextInput(attrs=PHONE_ATTRS))
    department = BranchAwareDepartmentField(
        queryset=Department.objects.all().select_related('branch').order_by('branch__code', 'name'),
        required=False,
        widget=DepartmentSelect(attrs={'class': 'form-control'}),
    )
    branch = forms.ModelChoiceField(queryset=Branch.objects.all().order_by('code'), required=False)

    class Meta:
        model = User
        fields = ['username', 'first_name', 'last_name', 'email',
                  'role', 'department', 'branch', 'date_joined_company', 'phone_country_code', 'phone', 'password']
        widgets = {'date_joined_company': forms.DateInput(attrs={'type': 'date'})}

    def __init__(self, *args, **kwargs):
        acting_user = kwargs.pop('acting_user', None)
        super().__init__(*args, **kwargs)

        if acting_user and not user_can_switch_branch(acting_user):
            # This HR user is restricted to their own branch — only show
            # that branch, and only departments belonging to it.
            allowed_branches = Branch.objects.filter(id=acting_user.branch_id) if acting_user.branch_id else Branch.objects.none()
            self.fields['branch'].queryset = allowed_branches
            self.fields['department'].queryset = Department.objects.filter(
                branch__in=allowed_branches
            ).select_related('branch').order_by('branch__code', 'name')
        # else: HR/Admin who can switch branches keeps the full, unrestricted querysets.

        for field in self.fields.values():
            field.widget.attrs.setdefault('class', 'form-control')

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data['password'])
        department = self.cleaned_data.get('department')
        if department and department.manager_id and user.role != User.ROLE_MANAGER:
            user.manager = department.manager
        else:
            user.manager = None
        if commit:
            user.save()
        return user


class HREmployeeEditForm(forms.ModelForm):
    first_name = forms.CharField(max_length=150, required=False, validators=[name_validator], widget=forms.TextInput(attrs=NAME_ATTRS))
    last_name = forms.CharField(max_length=150, required=False, validators=[name_validator], widget=forms.TextInput(attrs=NAME_ATTRS))
    email = forms.EmailField(required=False, widget=forms.EmailInput(attrs={'class': 'form-control'}))
    phone_country_code = forms.ChoiceField(choices=COUNTRY_CODES, required=False, widget=forms.Select(attrs=COUNTRY_CODE_ATTRS))
    phone = forms.CharField(max_length=10, required=False, validators=[phone_validator], widget=forms.TextInput(attrs=PHONE_ATTRS))
    employee_id = forms.CharField(max_length=20, required=False, validators=[alnum_id_validator], widget=forms.TextInput(attrs={**ID_ATTRS, 'readonly': 'readonly'}))
    date_joined_company = forms.DateField(required=False, widget=forms.DateInput(attrs={'type': 'date'}))
    branch = forms.ModelChoiceField(queryset=Branch.objects.all().order_by('code'), required=False)
    accessible_branches = forms.ModelMultipleChoiceField(
        queryset=Branch.objects.all().order_by('code'), required=False,
        widget=forms.CheckboxSelectMultiple, label="Branch Access",
        help_text="Select every branch this HR user can view and manage.",
    )
    department = BranchAwareDepartmentField(
        queryset=Department.objects.all().select_related('branch').order_by('branch__code', 'name'),
        required=False, widget=DepartmentSelect(attrs={'class': 'form-control'}),
    )

    class Meta:
        model = User
        fields = ['first_name', 'last_name', 'email', 'employee_id', 'department', 'branch', 'date_joined_company', 'phone_country_code', 'phone']
        widgets = {'date_joined_company': forms.DateInput(attrs={'type': 'date'})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if name != 'accessible_branches':
                field.widget.attrs.setdefault('class', 'form-control')
        if self.instance and self.instance.pk:
            self.fields['accessible_branches'].initial = self.instance.accessible_branches.all()

            
class EmployeeIdentityForm(forms.ModelForm):
    designation = forms.CharField(max_length=100, required=False, widget=forms.TextInput(attrs={'class': 'form-control'}))
    gender = forms.ChoiceField(choices=EmployeeProfile.GENDER_CHOICES, required=False, widget=forms.Select(attrs={'class': 'form-control'}))
    marital_status = forms.ChoiceField(choices=EmployeeProfile.MARITAL_STATUS_CHOICES, required=False, widget=forms.Select(attrs={'class': 'form-control'}))
    date_of_birth = forms.DateField(required=False, widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}))
    qualification = forms.CharField(max_length=200, required=False, widget=forms.TextInput(attrs={'class': 'form-control'}))
    aadhar_no = forms.CharField(max_length=14, required=False, validators=[aadhar_validator], widget=forms.TextInput(attrs=AADHAR_ATTRS))
    pan_no = forms.CharField(max_length=10, required=False, validators=[pan_validator], widget=forms.TextInput(attrs=PAN_ATTRS))
    uan_pf_number = forms.CharField(max_length=30, required=False, widget=forms.TextInput(attrs=NUMERIC_ATTRS))
    esi_number = forms.CharField(max_length=30, required=False, widget=forms.TextInput(attrs=NUMERIC_ATTRS))
    emergency_contact_name = forms.CharField(max_length=100, required=False, validators=[name_validator], widget=forms.TextInput(attrs=NAME_ATTRS))
    emergency_contact_country_code = forms.ChoiceField(choices=COUNTRY_CODES, required=False, widget=forms.Select(attrs=COUNTRY_CODE_ATTRS))
    emergency_contact_phone = forms.CharField(max_length=10, required=False, validators=[phone_validator], widget=forms.TextInput(attrs=PHONE_ATTRS))
    reference1_contact = forms.CharField(max_length=10, required=False, validators=[phone_validator], widget=forms.TextInput(attrs=PHONE_ATTRS))
    reference2_contact = forms.CharField(max_length=10, required=False, validators=[phone_validator], widget=forms.TextInput(attrs=PHONE_ATTRS))
    old_joining_date = forms.DateField(required=False, widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}))
    is_pf_applicable = forms.BooleanField(required=False, widget=forms.CheckboxInput())
    id_card_received = forms.BooleanField(required=False, widget=forms.CheckboxInput())

    class Meta:
        model = EmployeeProfile
        fields = [
            'profile_photo',
            'designation', 'gender', 'marital_status', 'date_of_birth',
            'qualification', 'previous_experience', 'current_company_experience',
            'employment_status', 'shift', 'work_type', 'is_pf_applicable',
            'id_card_received', 'blood_group', 'uan_pf_number', 'esi_number',
            'aadhar_no', 'pan_no',
            'emergency_contact_name', 'emergency_contact_country_code', 'emergency_contact_phone',
            'referred_by_name', 'referred_by_enrollment_id',
            'reference1_name', 'reference1_type', 'reference1_contact',
            'reference2_name', 'reference2_relation', 'reference2_contact',
            'old_joining_date',
        ]
        widgets = {
            'address': forms.Textarea(attrs={'rows': 3}),
            'date_of_birth': forms.DateInput(attrs={'type': 'date'}),
            'old_joining_date': forms.DateInput(attrs={'type': 'date'}),
            'previous_experience': forms.TextInput(attrs={'placeholder': 'e.g. 2 years at XYZ Corp'}),
            'current_company_experience': forms.TextInput(attrs={'placeholder': 'e.g. 1.5 years'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if not isinstance(field.widget, (forms.CheckboxInput, forms.ClearableFileInput)):
                field.widget.attrs.setdefault('class', 'form-control')
        # Show Aadhar pre-formatted with spaces even though it's stored raw.
        if self.instance and self.instance.pk and self.instance.aadhar_no:
            digits = self.instance.aadhar_no
            self.initial['aadhar_no'] = ' '.join(digits[i:i + 4] for i in range(0, len(digits), 4))

    def clean_aadhar_no(self):
        return self.cleaned_data.get('aadhar_no', '').replace(' ', '')

    def clean_pan_no(self):
        return self.cleaned_data.get('pan_no', '').upper()


class BankDetailForm(forms.ModelForm):
    account_number = forms.CharField(max_length=18, required=False, validators=[bank_account_validator], widget=forms.TextInput(attrs=BANK_ATTRS))
    ifsc_code = forms.CharField(max_length=11, required=False, validators=[ifsc_validator], widget=forms.TextInput(attrs=IFSC_ATTRS))
    account_holder_name = forms.CharField(max_length=150, required=False, validators=[name_validator], widget=forms.TextInput(attrs=NAME_ATTRS))
    bank_name = forms.CharField(max_length=150, required=False, widget=forms.TextInput(attrs={'class': 'form-control'}))
    branch_name = forms.CharField(max_length=150, required=False, widget=forms.TextInput(attrs={'class': 'form-control'}))

    class Meta:
        model = BankDetail
        fields = ['account_holder_name', 'account_number', 'ifsc_code', 'bank_name', 'branch_name']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault('class', 'form-control')

    def clean_ifsc_code(self):
        return self.cleaned_data.get('ifsc_code', '').upper()

class HRDocumentForm(forms.ModelForm):
    """HR can only upload official, company-issued letters — never an
    employee's personal documents."""
    doc_type = forms.ChoiceField(
        choices=[c for c in EmployeeDocument.DOC_TYPES if c[0] in EmployeeDocument.HR_DOC_TYPES],
        label="Document Type",
    )

    class Meta:
        model = EmployeeDocument
        fields = ['doc_type', 'file']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault('class', 'form-control')


class SelfDocumentForm(forms.ModelForm):
    """Everyone uploads their own personal documents here."""
    doc_type = forms.ChoiceField(
        choices=[c for c in EmployeeDocument.DOC_TYPES if c[0] in EmployeeDocument.SELF_DOC_TYPES],
        label="Document Type",
    )

    class Meta:
        model = EmployeeDocument
        fields = ['doc_type', 'file']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault('class', 'form-control')


class RoleChangeForm(forms.ModelForm):
    is_senior_hr = forms.BooleanField(
        required=False, label="Senior HR",
        help_text="Elevates this HR user to Senior HR — used when an HR is promoted to a senior position."
    )

    class Meta:
        model = User
        fields = ['role']

    def __init__(self, *args, **kwargs):
        acting_user = kwargs.pop('acting_user', None)
        super().__init__(*args, **kwargs)
        self.fields['role'].widget.attrs.setdefault('class', 'form-control')
        self.fields['is_senior_hr'].widget.attrs.setdefault('class', 'form-check-input')

        if self.instance and self.instance.pk:
            self.fields['is_senior_hr'].initial = self.instance.is_senior_hr

        if acting_user and acting_user.role == 'ADMIN':
            # Admin sees every role, including COO.
            self.fields['role'].choices = User.ROLE_CHOICES

        if acting_user and acting_user.role == 'HR':
            self.fields['role'].choices = [
                (User.ROLE_EMPLOYEE, 'Employee'),
                (User.ROLE_MANAGER, 'Manager'),
                (User.ROLE_PROJECT_MANAGER, 'Project Manager'),
                (User.ROLE_GENERAL_MANAGER, 'General Manager'),
                (User.ROLE_COO, 'COO'),
                (User.ROLE_HR, 'HR'),
                
            ]

        def save(self, commit=True):
            user = super().save(commit=False)
            user.is_senior_hr = self.cleaned_data.get('is_senior_hr', False) if user.role == 'HR' else False
            if commit:
                user.save()
            return user


class ResignationRequestForm(forms.ModelForm):
    class Meta:
        model = ResignationRequest
        fields = ['reason']
        widgets = {
            'reason': forms.Textarea(attrs={'rows': 5, 'placeholder': 'Tell HR why you are resigning...'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault('class', 'form-control')
  
class OnboardHRForm(forms.ModelForm):
    password = forms.CharField(widget=forms.PasswordInput, help_text="Temporary password for this account")
    first_name = forms.CharField(max_length=150, validators=[name_validator], widget=forms.TextInput(attrs=NAME_ATTRS))
    last_name = forms.CharField(max_length=150, required=False, validators=[name_validator], widget=forms.TextInput(attrs=NAME_ATTRS))
    phone_country_code = forms.ChoiceField(choices=COUNTRY_CODES, required=False, widget=forms.Select(attrs=COUNTRY_CODE_ATTRS))
    phone = forms.CharField(max_length=10, required=False, validators=[phone_validator], widget=forms.TextInput(attrs=PHONE_ATTRS))

    role = forms.ChoiceField(
        choices=[
            (User.ROLE_EMPLOYEE, 'Employee'),
            (User.ROLE_MANAGER, 'manager'),
            (User.ROLE_HR, 'HR'),
            (User.ROLE_ADMIN, 'Admin'),
            (User.ROLE_COO, 'COO'),
            (User.ROLE_SENIOR_HR, 'Senior HR'),
        ],
        required=True,
    )

    department = forms.ModelChoiceField(
        queryset=Department.objects.none(), required=False,
        widget=forms.Select(attrs={'class': 'form-control'})
    )
    branch = forms.ModelChoiceField(queryset=Branch.objects.none(), required=False)

    accessible_branches = forms.ModelMultipleChoiceField(
        queryset=Branch.objects.all().order_by('code'),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label="Branch Access",
        help_text="Select every branch this user should be able to view and manage.",
    )

    class Meta:
        model = User
        fields = ['username', 'first_name', 'last_name', 'email',
                  'role', 'department', 'branch', 'date_joined_company', 'phone_country_code', 'phone', 'password']
        widgets = {
            'date_joined_company': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
        }

    def __init__(self, *args, **kwargs):
        acting_user = kwargs.pop('acting_user', None)
        super().__init__(*args, **kwargs)

        if acting_user and acting_user.role == 'HR':
            # An HR user onboarding another HR can only create HR
            # accounts — Admin/COO/Manager/Employee creation stays out of
            # this path for an HR actor.
            self.fields['role'].choices = [(User.ROLE_HR, 'HR')]

            allowed_branches = acting_user.accessible_branches.all()
            if not allowed_branches.exists() and acting_user.branch_id:
                allowed_branches = Branch.objects.filter(id=acting_user.branch_id)

            self.fields['branch'].queryset = allowed_branches.order_by('code')
            self.fields['department'].queryset = Department.objects.filter(
                branch__in=allowed_branches
            ).select_related('branch').order_by('branch__code', 'name')

            # An HR actor can only grant branch access to branches she
            # herself can access — she can't hand out access she doesn't
            # have. Admin is not involved in this decision at all.
            if acting_user.can_access_all_branches:
                self.fields['accessible_branches'].queryset = Branch.objects.all().order_by('code')
            else:
                self.fields['accessible_branches'].queryset = allowed_branches.order_by('code')
        else:
            self.fields['branch'].queryset = Branch.objects.all().order_by('code')
            self.fields['department'].queryset = Department.objects.all().select_related('branch').order_by('branch__code', 'name')
            self.fields['accessible_branches'].queryset = Branch.objects.all().order_by('code')

        for field in self.fields.values():
            field.widget.attrs.setdefault('class', 'form-control')

    def clean(self):
        cleaned = super().clean()
        role = cleaned.get('role')
        if role in (User.ROLE_EMPLOYEE, User.ROLE_MANAGER):
            if not cleaned.get('branch'):
                self.add_error('branch', "Select a branch for this employee.")
        elif role in (User.ROLE_HR, User.ROLE_ADMIN):
            if not cleaned.get('accessible_branches'):
                self.add_error('accessible_branches', "Select at least one branch this user can access.")
        return cleaned