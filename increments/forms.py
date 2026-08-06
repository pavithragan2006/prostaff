from django import forms
from core.models import User
from increments.models import IncrementFeedback

DECIMAL_ONINPUT = (
    "this.value = this.value.replace(/[^0-9.]/g, '')"
    ".replace(/(\\..*)\\./g, '$1')"
)

MANAGER_TIER_ROLES = ['MANAGER', 'PROJECT_MANAGER', 'GENERAL_MANAGER']


class IncrementRequestForm(forms.Form):
    user = forms.ModelChoiceField(queryset=User.objects.none(), label="Employee Name")
    feedback_manager = forms.ModelChoiceField(
        queryset=User.objects.filter(role='PROJECT_MANAGER'), required=False,
        label="Project Manager (for Feedback)"
    )
    current_basic = forms.DecimalField(
        max_digits=10, decimal_places=2, min_value=0, label="Current Basic Salary",
        widget=forms.NumberInput(attrs={'step': '0.01', 'min': '0', 'inputmode': 'decimal', 'oninput': DECIMAL_ONINPUT})
    )
    effective_date = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}))
    reason = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 2}))

    def __init__(self, *args, **kwargs):
        acting_user = kwargs.pop('acting_user', None)
        branch = kwargs.pop('branch', None)
        super().__init__(*args, **kwargs)
        qs = User.objects.filter(role='EMPLOYEE')
        if acting_user:
            qs = qs.exclude(id=acting_user.id)
        if branch:
            qs = qs.filter(branch=branch)
        self.fields['user'].queryset = qs

        manager_qs = User.objects.filter(role='PROJECT_MANAGER')
        if branch:
            manager_qs = manager_qs.filter(branch=branch)
        self.fields['feedback_manager'].queryset = manager_qs
        for field in self.fields.values():
            field.widget.attrs.setdefault('class', 'form-control')

    def clean(self):
        cleaned = super().clean()
        emp_user = cleaned.get('user')
        feedback_manager = cleaned.get('feedback_manager')
        if emp_user and emp_user.role == 'EMPLOYEE' and not feedback_manager:
            self.add_error('feedback_manager', "Select the employee's assigned Project Manager to send this increment for feedback.")
        return cleaned


class ManagerIncrementRequestForm(forms.Form):
    """HR uses this to request an increment directly for a Manager /
    Project Manager / General Manager. No peer-feedback step — this goes
    straight to the COO along with the uploaded performance report, since
    managers report to the COO, not to another manager."""
    user = forms.ModelChoiceField(queryset=User.objects.none(), label="Name")
    current_basic = forms.DecimalField(
        max_digits=10, decimal_places=2, min_value=0, label="Current Basic Salary",
        widget=forms.NumberInput(attrs={'step': '0.01', 'min': '0', 'inputmode': 'decimal', 'oninput': DECIMAL_ONINPUT})
    )
    increment_percent = forms.DecimalField(
        max_digits=6, decimal_places=2, min_value=0, label="Increment Percentage (%)",
        widget=forms.NumberInput(attrs={'step': '0.01', 'min': '0', 'inputmode': 'decimal', 'oninput': DECIMAL_ONINPUT})
    )
    effective_date = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}))
    report_file = forms.FileField(required=True, label="Upload Performance Report")
    reason = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 2}), label="Notes for COO")

    def __init__(self, *args, **kwargs):
        acting_user = kwargs.pop('acting_user', None)
        branch = kwargs.pop('branch', None)
        super().__init__(*args, **kwargs)
        qs = User.objects.filter(role__in=MANAGER_TIER_ROLES)
        if acting_user:
            qs = qs.exclude(id=acting_user.id)
        if branch:
            qs = qs.filter(branch=branch)
        self.fields['user'].queryset = qs
        for field in self.fields.values():
            field.widget.attrs.setdefault('class', 'form-control')


class IncrementFeedbackForm(forms.ModelForm):
    suggested_percent = forms.DecimalField(
        max_digits=6, decimal_places=2, min_value=0, required=True,
        label="Suggested Increment (%)",
        widget=forms.NumberInput(attrs={'step': '0.01', 'min': '0', 'inputmode': 'decimal', 'oninput': DECIMAL_ONINPUT, 'class': 'form-control'})
    )
    report_file = forms.FileField(
        required=True, label="Upload Performance Report",
        widget=forms.ClearableFileInput(attrs={'class': 'form-control'})
    )

    class Meta:
        model = IncrementFeedback
        fields = ['report_file', 'suggested_percent', 'description']
        widgets = {
            'description': forms.Textarea(attrs={
                'rows': 3, 'class': 'form-control',
                'placeholder': "Any additional comments about this employee's overall performance...",
            }),
        }