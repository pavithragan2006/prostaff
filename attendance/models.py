from datetime import datetime, time, date, timedelta
from django.db import models
from django.conf import settings
from django.utils import timezone as dj_timezone

SHIFT_START = time(9, 0)  # 9:30 AM shift start used for late detection
NIGHT_START = time(22, 0)  # 10:00 PM
NIGHT_END = time(6, 0)     # 6:00 AM


def _falls_in_night_window(t):
    """True if a local time-of-day falls within the 10 PM - 6 AM night window."""
    if t is None:
        return False
    return t >= NIGHT_START or t <= NIGHT_END


class AttendanceRecord(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='attendance_records')
    date = models.DateField()
    in_time = models.DateTimeField(null=True, blank=True)
    out_time = models.DateTimeField(null=True, blank=True)
    total_hours = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    is_late = models.BooleanField(default=False)
    late_minutes = models.IntegerField(default=0)

    # Auto-detected on save: true if the in/out punch falls inside the
    # 10 PM - 6 AM window (local time).
    is_night_duty = models.BooleanField(default=False)

    class Meta:
        unique_together = ('user', 'date')
        ordering = ['-date']

    def save(self, *args, **kwargs):
        if self.in_time and self.out_time:
            delta = self.out_time - self.in_time
            self.total_hours = round(delta.total_seconds() / 3600, 2)
        if self.in_time:
            shift_start_dt = datetime.combine(self.date, SHIFT_START)
            in_time_local = dj_timezone.localtime(self.in_time).replace(tzinfo=None)
            if in_time_local > shift_start_dt:
                self.is_late = True
                self.late_minutes = int((in_time_local - shift_start_dt).total_seconds() / 60)
            else:
                self.is_late = False
                self.late_minutes = 0

        # Night duty check uses actual local wall-clock time (converted from
        # the stored UTC-aware datetime), not the naive stripped value above.
        in_t = dj_timezone.localtime(self.in_time).time() if self.in_time else None
        out_t = dj_timezone.localtime(self.out_time).time() if self.out_time else None
        self.is_night_duty = _falls_in_night_window(in_t) or _falls_in_night_window(out_t)

        super().save(*args, **kwargs)

    @property
    def payroll_cycle_label(self):
        """Payroll cycle runs 26th (prev month) to 25th (this month)."""
        if self.date.day >= 26:
            start_month = self.date.month
        else:
            start_month = self.date.month - 1 or 12
        return f"Cycle starting {start_month}/26"

    def __str__(self):
        return f"{self.user} - {self.date}"


class MonthlyAttendanceSheet(models.Model):
    """One row per employee per month. Stores the generated Excel snapshot
    so historical months stay available even after the underlying daily
    records age out of casual view. Regenerated automatically whenever the
    employee punches in/out during that month."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='monthly_sheets')
    year = models.IntegerField()
    month = models.IntegerField()
    excel_file = models.FileField(upload_to='monthly_attendance/', null=True, blank=True)
    generated_at = models.DateTimeField(auto_now=True)
    cl_quota_override = models.PositiveIntegerField(null=True, blank=True)
    ph_sunday_override = models.PositiveIntegerField(null=True, blank=True)
    class Meta:
        unique_together = ('user', 'year', 'month')
        ordering = ['-year', '-month']

    def __str__(self):
        return f"Monthly sheet - {self.user} ({self.year}-{self.month:02d})"

class OvertimePermission(models.Model):
    """Granted by HR, or by the Manager/Lead of a specific project, to let
    one employee's Sunday work and/or overtime (hours beyond the standard
    8/day) actually count for that date. Without a matching permission,
    those hours are excluded everywhere — daily rows, monthly/weekly/yearly
    summaries, team register, and payroll."""

    TYPE_OVERTIME = 'OVERTIME'
    TYPE_SUNDAY = 'SUNDAY'
    TYPE_BOTH = 'BOTH'
    TYPE_CHOICES = [
        (TYPE_OVERTIME, 'Overtime (extra hours on a working day)'),
        (TYPE_SUNDAY, 'Sunday Work'),
        (TYPE_BOTH, 'Overtime + Sunday Work'),
    ]

    employee = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='overtime_permissions')
    project = models.ForeignKey('projects.Project', on_delete=models.CASCADE, related_name='overtime_permissions')
    date = models.DateField()
    permission_type = models.CharField(max_length=10, choices=TYPE_CHOICES, default=TYPE_BOTH)
    notes = models.CharField(max_length=255, blank=True)

    authorized_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name='overtime_permissions_granted'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('employee', 'project', 'date')
        ordering = ['-date']

    def covers_sunday(self):
        return self.permission_type in (self.TYPE_SUNDAY, self.TYPE_BOTH)

    def covers_overtime(self):
        return self.permission_type in (self.TYPE_OVERTIME, self.TYPE_BOTH)

    def __str__(self):
        return f"{self.employee} — {self.project} — {self.date} ({self.get_permission_type_display()})"


class OvertimeRequest(models.Model):
    """Employee-initiated OT / Sunday / Holiday request. Routed to the
    employee's department Manager first, then to HR for final verification."""

    TYPE_OT = 'OT'
    TYPE_SUNDAY = 'SUNDAY'
    TYPE_HOLIDAY = 'HOLIDAY'
    TYPE_CHOICES = [
        (TYPE_OT, 'Overtime'),
        (TYPE_SUNDAY, 'Sunday'),
        (TYPE_HOLIDAY, 'Holiday'),
    ]

    STATUS_CHOICES = [
        ('PENDING_MANAGER', 'Pending Manager Approval'),
        ('PENDING_HR', 'Pending HR Approval'),
        ('APPROVED', 'Approved'),
        ('REJECTED_MANAGER', 'Rejected by Manager'),
        ('REJECTED_HR', 'Rejected by HR'),
    ]

    employee = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='overtime_requests')
    project = models.ForeignKey('projects.Project', on_delete=models.CASCADE, related_name='overtime_requests')
    department = models.ForeignKey('core.Department', null=True, blank=True, on_delete=models.SET_NULL, related_name='overtime_requests')
    date = models.DateField()
    request_type = models.CharField(max_length=10, choices=TYPE_CHOICES, default=TYPE_OT)
    start_time = models.TimeField()
    end_time = models.TimeField()
    total_hours = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    reason = models.TextField(blank=True)

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING_MANAGER')
    submitted_at = models.DateTimeField(auto_now_add=True)

    reviewed_by_manager = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='overtime_requests_manager_reviewed')
    manager_reviewed_at = models.DateTimeField(null=True, blank=True)
    manager_comment = models.CharField(max_length=255, blank=True)

    reviewed_by_hr = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='overtime_requests_hr_reviewed')
    hr_reviewed_at = models.DateTimeField(null=True, blank=True)
    hr_comment = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ['-submitted_at']

    def save(self, *args, **kwargs):
        if self.start_time and self.end_time:
            base_date = self.date or date.today()
            start_dt = datetime.combine(base_date, self.start_time)
            end_dt = datetime.combine(base_date, self.end_time)
            if end_dt <= start_dt:
                end_dt += timedelta(days=1)  # crosses midnight
            self.total_hours = round((end_dt - start_dt).total_seconds() / 3600, 2)
        super().save(*args, **kwargs)

    def get_manager(self):
        dept = self.department or self.employee.department
        if not dept or not dept.manager_id or dept.manager_id == self.employee_id:
            return None
        manager = dept.manager
        if manager.branch_id != self.employee.branch_id:
            return None
        return manager

    def approve_by_manager(self, manager, comment=''):
        self.status = 'APPROVED'
        self.reviewed_by_manager = manager
        self.manager_reviewed_at = dj_timezone.now()
        self.manager_comment = comment
        self.save()

    def reject_by_manager(self, manager, comment=''):
        self.status = 'REJECTED_MANAGER'
        self.reviewed_by_manager = manager
        self.manager_reviewed_at = dj_timezone.now()
        self.manager_comment = comment
        self.save()

    def approve_by_hr(self, hr_user, comment=''):
        self.status = 'APPROVED'
        self.reviewed_by_hr = hr_user
        self.hr_reviewed_at = dj_timezone.now()
        self.hr_comment = comment
        self.save()

    def reject_by_hr(self, hr_user, comment=''):
        self.status = 'REJECTED_HR'
        self.reviewed_by_hr = hr_user
        self.hr_reviewed_at = dj_timezone.now()
        self.hr_comment = comment
        self.save()

    def __str__(self):
        return f"{self.employee} - {self.date} ({self.get_request_type_display()}, {self.status})"