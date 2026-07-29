from django.contrib.auth.models import AbstractUser
from django.db import models
from core.validators import COUNTRY_CODES


class Branch(models.Model):
    """A physical office location. Like Department, a Branch can have a
    designated Manager overseeing it."""
    name = models.CharField(max_length=100, unique=True)
    code = models.CharField(max_length=10, unique=True)
    manager = models.ForeignKey(
        'core.User', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='managed_branches'
    )

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.code})"


class Department(models.Model):
    name = models.CharField(max_length=100)
    branch = models.ForeignKey(
        Branch, null=True, blank=True, on_delete=models.CASCADE, related_name='departments'
    )
    manager = models.ForeignKey(
        'core.User', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='managed_departments'
    )

    class Meta:
        ordering = ['branch__name', 'name']
        unique_together = ('name', 'branch')

    def __str__(self):
        if self.branch:
            return f"{self.name} ({self.branch.code})"
        return self.name


class User(AbstractUser):
    ROLE_ADMIN = 'ADMIN'
    ROLE_HR = 'HR'
    ROLE_MANAGER = 'MANAGER'
    ROLE_EMPLOYEE = 'EMPLOYEE'
    ROLE_CHOICES = [
        (ROLE_ADMIN, 'Admin'),
        (ROLE_HR, 'HR'),
        (ROLE_MANAGER, 'Manager'),
        (ROLE_EMPLOYEE, 'Employee'),
    ]

    role = models.CharField(max_length=10, choices=ROLE_CHOICES, default=ROLE_EMPLOYEE)
    employee_id = models.CharField(max_length=20, unique=True, null=True, blank=True)
    department = models.ForeignKey(Department, null=True, blank=True, on_delete=models.SET_NULL, related_name='employees')
    manager = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL, related_name='team_members')
    date_joined_company = models.DateField(null=True, blank=True)

    branch = models.ForeignKey(
        Branch, null=True, blank=True, on_delete=models.SET_NULL, related_name='staff'
    )
    accessible_branches = models.ManyToManyField(
        'core.Branch', blank=True, related_name='hr_admins',
        help_text="Branches this HR user can view/manage. Ignored for Admin — Admin always has access to every branch."
    )

    # Only ever set by Admin (see toggle_branch_admin_access). An HR user
    # with this True can switch between and see every branch. Every other
    # HR is locked to their own assigned branch everywhere branch
    # filtering applies — they never see a switcher at all.
    can_access_all_branches = models.BooleanField(default=False)

    phone_country_code = models.CharField(max_length=5, choices=COUNTRY_CODES, default='+91')
    phone = models.CharField(max_length=10, blank=True)

    def is_hr_or_admin(self):
        return self.role in (self.ROLE_ADMIN, self.ROLE_HR)

    def is_manager(self):
        return self.role == self.ROLE_MANAGER

    def __str__(self):
        return self.get_full_name() or self.username