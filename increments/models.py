from django.db import models
from django.conf import settings


class IncrementRequest(models.Model):
    STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('PENDING_REPORT', 'Report Requested from Manager'),
        ('REPORT_SUBMITTED', 'Report Submitted — Pending HR Review'),
        ('PENDING_COO', 'Forwarded to COO for Decision'),
        ('COO_DECIDED', 'COO Gave Percentage — Pending Salary Update'),
        ('APPLIED', 'Salary Updated'),
        ('REJECTED', 'Rejected'),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='increment_requests')
    current_basic = models.DecimalField(max_digits=10, decimal_places=2)
    requested_basic = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    effective_date = models.DateField(null=True, blank=True)
    reason = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name='increments_requested')
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='increments_approved')
    created_at = models.DateTimeField(auto_now_add=True)

    feedback_manager = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='increments_feedback_requested'
    )
    manager_rejection_reason = models.TextField(blank=True)

    # ---- Step 1: HR requests a report ----
    report_requested_at = models.DateTimeField(null=True, blank=True)

    # ---- Step 2: Manager writes and submits the report ----
    performance_report = models.TextField(blank=True, help_text="Manager's written performance report.")
    report_submitted_at = models.DateTimeField(null=True, blank=True)

    # ---- Step 3: HR reviews and forwards to CEO ----
    hr_notes = models.TextField(blank=True, help_text="HR's notes/analysis sent along to the CEO.")
    forwarded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='increments_forwarded')
    forwarded_at = models.DateTimeField(null=True, blank=True)

    # ---- Step 4: COO analyzes and decides the percentage ----
    coo_decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='increments_coo_decided')
    coo_decided_at = models.DateTimeField(null=True, blank=True)
    coo_percent = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    coo_comment = models.TextField(blank=True)
    coo_rejection_reason = models.TextField(blank=True)

    # ---- Step 5: HR applies the percentage to the employee's salary ----
    applied_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='increments_applied')
    applied_at = models.DateTimeField(null=True, blank=True)

    @property
    def increment_percent(self):
        if self.current_basic and self.requested_basic:
            return round(((self.requested_basic - self.current_basic) / self.current_basic) * 100, 2)
        return self.coo_percent or 0

    @property
    def needs_feedback(self):
        return self.user.role == 'EMPLOYEE' and self.status == 'PENDING'

    @property
    def can_be_decided(self):
        return not self.needs_feedback

    def __str__(self):
        return f"Increment for {self.user} ({self.status})"


class IncrementFeedback(models.Model):
    SUGGEST = 'SUGGEST'
    NEUTRAL = 'NEUTRAL'
    NOT_SUGGEST = 'NOT_SUGGEST'
    SUGGESTION_CHOICES = [
        (SUGGEST, "Yeah! I suggest increment for this employee"),
        (NEUTRAL, "My suggestion is neutral, this is working normal"),
        (NOT_SUGGEST, "No, I don't suggest — not working hard"),
    ]

    increment_request = models.OneToOneField(IncrementRequest, on_delete=models.CASCADE, related_name='feedback')
    manager = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name='increment_feedbacks_given')
    # Kept for backward compatibility, no longer required/shown on the form.
    suggestion = models.CharField(max_length=15, choices=SUGGESTION_CHOICES, blank=True)
    suggested_percent = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, help_text="Manager's suggested increment percentage.")
    report_file = models.FileField(upload_to='increment_reports/', null=True, blank=True, help_text="Performance report (docx/xlsx/pdf) uploaded by the manager.")
    description = models.TextField(blank=True, help_text="Why do you think so?")
    submitted_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Feedback on {self.increment_request} by {self.manager}"
    
class IncrementCycleSkip(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='increment_skips')
    anniversary_date = models.DateField()
    skipped_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name='increment_skips_made')
    skipped_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'anniversary_date')

    def __str__(self):
        return f"Skipped increment reminder - {self.user} ({self.anniversary_date})"
    
class CooReport(models.Model):
    """A report HR sends up to the COO — optionally tied to a specific
    employee's increment, or just a general report."""
    employee = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='coo_reports'
    )
    report_file = models.FileField(upload_to='coo_reports/')
    notes = models.CharField(max_length=255, blank=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL,
        related_name='coo_reports_uploaded'
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-uploaded_at']

    def __str__(self):
        return f"COO Report - {self.employee or 'General'} ({self.uploaded_at.date()})"