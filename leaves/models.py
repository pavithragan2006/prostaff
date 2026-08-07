from django.db import models
from django.conf import settings


class LeaveBalance(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='leave_balance')
    cl_balance = models.DecimalField(max_digits=4, decimal_places=1, default=12)
    el_balance = models.DecimalField(max_digits=4, decimal_places=1, default=15)
    sick_balance = models.DecimalField(max_digits=4, decimal_places=1, default=8)

    def __str__(self):
        return f"Leave balance - {self.user}"


class LeaveRequest(models.Model):
    """Covers both short 'Permission' requests (a few hours on a given day)
    and full-day 'Leave' requests. Both follow the same approval pipeline;
    only the fields captured on submission differ."""

    REQUEST_TYPES = [('PERMISSION', 'Permission'), ('LEAVE', 'Leave')]
    LEAVE_TYPES = [('CL', 'Casual Leave'), ('EL', 'Earned Leave'), ('SICK', 'Sick Leave')]

    STATUS_CHOICES = [
            ('PENDING_MANAGER', 'Pending Manager Approval'),
            ('PENDING_HR', 'Pending HR Approval'),
            ('PENDING_COO', 'Pending COO Approval'),
            ('APPROVED', 'Approved'),
            ('REJECTED', 'Rejected'),
            ('HR_REJECTED_PENDING_MANAGER', 'HR Declined - Awaiting Manager'),
        ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='leave_requests')
    request_type = models.CharField(max_length=10, choices=REQUEST_TYPES, default='PERMISSION')

    # ---- Leave-only fields ----
    leave_type = models.CharField(max_length=10, choices=LEAVE_TYPES, blank=True)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)

    # ---- Permission-only fields ----
    permission_date = models.DateField(null=True, blank=True)
    from_time = models.TimeField(null=True, blank=True)
    to_time = models.TimeField(null=True, blank=True)
    num_hours = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)

    reason = models.TextField(blank=True)
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default='PENDING_MANAGER')
    applied_at = models.DateTimeField(auto_now_add=True)

    reviewed_by_manager = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='leaves_manager_reviewed'
    )
    manager_reviewed_at = models.DateTimeField(null=True, blank=True)

    reviewed_by_hr = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='leaves_hr_reviewed'
    )
    hr_reviewed_at = models.DateTimeField(null=True, blank=True)

    # Only set when an HR user submits their own request — which OTHER HR
    # colleague should review it. Never the requester themselves.
    target_hr = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='leaves_targeted_as_hr'
    )

    class Meta:
        ordering = ['-applied_at']

    @property
    def num_days(self):
        if self.request_type == 'LEAVE' and self.start_date and self.end_date:
            return (self.end_date - self.start_date).days + 1
        return None

    @property
    def requires_manager_finalization(self):
        """True only when an actual manager approval happened before this
        reached HR — i.e. a regular employee's request that passed through
        the manager stage. Manager's-own and HR's-own requests skip the
        manager stage entirely, so this stays False for them."""
        return self.reviewed_by_manager_id is not None
    
    def get_manager(self):
        """The manager who should review this. A Project Manager assignment
        takes priority: if this employee is currently on a project led by a
        Project Manager, their leave/permission requests go straight to that
        PM. Otherwise falls back to the department manager or direct manager
        link, same as before. Manager-level requests always skip straight to
        HR (see initial_status), so this only matters for Employees."""
        from projects.models import ProjectAssignment

        assignment = ProjectAssignment.objects.filter(
            user=self.user, is_current=True,
            project__manager__role='PROJECT_MANAGER',
            project__status__in=['APPROVED', 'COMPLETED'],
        ).select_related('project__manager').order_by('-project__start_date').first()

        if assignment and assignment.project.manager and assignment.project.manager_id != self.user_id \
                and assignment.project.manager.branch_id == self.user.branch_id:
            return assignment.project.manager

        dept = self.user.department
        candidate = None
        if dept and dept.manager_id and dept.manager_id != self.user_id:
            candidate = dept.manager
        elif self.user.manager_id and self.user.manager.role == 'MANAGER':
            candidate = self.user.manager

        if candidate and candidate.branch_id == self.user.branch_id:
            return candidate
        return None
    
    def initial_status(self):
        if self.user.role == 'COO':
            return 'APPROVED'  # nobody above the COO to review
        if self.user.role == 'PROJECT_MANAGER':
            # PMs report up through HR for their own leave, not the COO.
            return 'PENDING_HR'
        if self.user.role in ('GENERAL_MANAGER', 'MANAGER', 'HR', 'ADMIN'):
            return 'PENDING_COO'
        return 'PENDING_MANAGER'


    def get_request_type_display_full(self):
                return self.get_request_type_display()
        
    def __str__(self):
                return f"{self.user} - {self.get_request_type_display()} ({self.status})"

class LeaveNotification(models.Model):
    """A simple inbox entry telling a user about a decision made on a
    permission/leave request — used to satisfy the various 'notify the
    employee' / 'notify the manager' requirements in the approval flow."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='leave_notifications')
    leave_request = models.ForeignKey(LeaveRequest, on_delete=models.CASCADE, related_name='notifications')
    message = models.CharField(max_length=255)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Notification for {self.user}: {self.message}"

