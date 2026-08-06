from django.db import models
from django.conf import settings
from django.utils import timezone
from core.validators import COUNTRY_CODES, phone_validator, bank_account_validator, ifsc_validator
from datetime import timedelta


class EmployeeProfile(models.Model):
    STATUS_CHOICES = [
        ('ONBOARDING', 'Onboarding'),
        ('ACTIVE', 'Active'),
        ('EXITED', 'Exited'),
    ]

    GENDER_CHOICES = [('MALE', 'Male'), ('FEMALE', 'Female')]

    MARITAL_STATUS_CHOICES = [
        ('SINGLE', 'Single'),
        ('MARRIED', 'Married'),
        ('OTHER', 'Other'),
    ]

    SHIFT_CHOICES = [
        ('GENERAL', 'General Shift'),
        ('FIRST', 'First Shift'),
        ('SECOND', 'Second Shift'),
        ('NIGHT', 'Night Shift'),
    ]

    WORK_TYPE_CHOICES = [
        ('WFH', 'Work From Home'),
        ('OFFICE', 'In Office'),
    ]

    EMPLOYMENT_STATUS_CHOICES = [
        ('PERMANENT', 'Permanent'),
        ('PROBATION', 'Probation Period'),
    ]

    BLOOD_GROUP_CHOICES = [
        ('A+', 'A+'), ('A-', 'A-'),
        ('B+', 'B+'), ('B-', 'B-'),
        ('AB+', 'AB+'), ('AB-', 'AB-'),
        ('O+', 'O+'), ('O-', 'O-'),
    ]

    REFERENCE_TYPE_CHOICES = [
        ('FACULTY', 'College Faculty'),
        ('COMPANY', 'Previous Company'),
    ]

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='profile')
    designation = models.CharField(max_length=100, blank=True)
    address = models.TextField(blank=True)
    date_of_birth = models.DateField(null=True, blank=True)
    bank_account = models.CharField(max_length=30, blank=True)
    ifsc_code = models.CharField(max_length=20, blank=True)
    pan_no = models.CharField(max_length=20, blank=True)
    aadhar_no = models.CharField(max_length=20, blank=True)
    emergency_contact_name = models.CharField(max_length=100, blank=True)
    emergency_contact_country_code = models.CharField(max_length=5, choices=COUNTRY_CODES, default='+91')
    emergency_contact_phone = models.CharField(max_length=10, blank=True)
    profile_photo = models.ImageField(upload_to='profile_photos/', null=True, blank=True)

    # ---- Identity / HR fields (HR, Admin, Manager editable only) ----
    # Branch-prefixed IDs. enrollment_id: TRB01/02/03/04-####. User.employee_id
    # (existing field) is repurposed to hold the SPSB01/02/03/04-#### id.
    enrollment_id = models.CharField(max_length=20, unique=True, null=True, blank=True)

    is_pf_applicable = models.BooleanField(default=True, blank=True, verbose_name="PF Applicable")
    id_card_received = models.BooleanField(default=False, blank=True, verbose_name="ID Card Received")
    shift = models.CharField(max_length=10, choices=SHIFT_CHOICES, blank=True)
    work_type = models.CharField(max_length=10, choices=WORK_TYPE_CHOICES, blank=True)
    gender = models.CharField(max_length=10, choices=GENDER_CHOICES, blank=True)
    marital_status = models.CharField(max_length=10, choices=MARITAL_STATUS_CHOICES, blank=True)
    qualification = models.CharField(max_length=200, blank=True)
    previous_experience = models.CharField(max_length=200, blank=True, help_text="e.g. 2 years at XYZ Corp")
    current_company_experience = models.CharField(max_length=100, blank=True, help_text="e.g. 1.5 years")
    employment_status = models.CharField(max_length=10, choices=EMPLOYMENT_STATUS_CHOICES, default='PROBATION', blank=True)
    uan_pf_number = models.CharField(max_length=30, blank=True, verbose_name="UAN / PF Number")
    esi_number = models.CharField(max_length=30, blank=True, verbose_name="ESI Number")
    blood_group = models.CharField(max_length=5, choices=BLOOD_GROUP_CHOICES, blank=True)

    referred_by_name = models.CharField(max_length=150, blank=True)
    referred_by_enrollment_id = models.CharField(max_length=20, blank=True, verbose_name="Referred By (Enrollment No.)")

    reference1_name = models.CharField(max_length=150, blank=True, verbose_name="Reference 1 Name")
    reference1_type = models.CharField(max_length=10, choices=REFERENCE_TYPE_CHOICES, blank=True, verbose_name="Reference 1 Type")
    reference1_contact = models.CharField(max_length=10, blank=True, validators=[phone_validator], verbose_name="Reference 1 Contact")

    reference2_name = models.CharField(max_length=150, blank=True, verbose_name="Reference 2 Name (Relative)")
    reference2_relation = models.CharField(max_length=100, blank=True, verbose_name="Reference 2 Relation")
    reference2_contact = models.CharField(max_length=10, blank=True, validators=[phone_validator], verbose_name="Reference 2 Contact")

    # Rejoin tracking: when someone who exited comes back, the fresh
    # date_joined_company reflects the new stint while old_joining_date
    # preserves the date they originally joined before resigning.
    old_joining_date = models.DateField(null=True, blank=True, help_text="Original joining date, preserved automatically if this employee resigned and later rejoined.")

    # Status now only ever changes via two automated workflows: HR marking
    # onboarding complete (ONBOARDING -> ACTIVE), and HR accepting a
    # resignation request (-> EXITED, see ResignationRequest.accept()), or
    # HR manually re-activating a rejoining employee.
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='ONBOARDING')

    # Only ever set by ResignationRequest.accept() below.
    exit_date = models.DateField(null=True, blank=True)
    exit_reason = models.TextField(blank=True)

    def __str__(self):
        return str(self.user)

    @property
    def aadhar_display(self):
        """Aadhar shown grouped as XXXX XXXX XXXX; stored as a plain 12-digit string."""
        digits = (self.aadhar_no or '').replace(' ', '')
        return ' '.join(digits[i:i + 4] for i in range(0, len(digits), 4)) if digits else '-'


class BankDetail(models.Model):
    """Salary account details — HR/Admin/Manager editable only, visible to
    the employee on their own profile."""
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='bank_detail')
    account_holder_name = models.CharField(max_length=150, blank=True)
    account_number = models.CharField(max_length=18, blank=True, validators=[bank_account_validator])
    ifsc_code = models.CharField(max_length=11, blank=True, validators=[ifsc_validator])
    bank_name = models.CharField(max_length=150, blank=True)
    branch_name = models.CharField(max_length=150, blank=True)

    def __str__(self):
        return f"Bank details - {self.user}"


class EmployeeDocument(models.Model):
    OFFER = 'OFFER'
    APPOINTMENT = 'APPOINTMENT'
    DEGREE = 'DEGREE'
    PAN_CARD = 'PAN_CARD'
    AADHAR_CARD = 'AADHAR_CARD'
    ID_PROOF = 'ID_PROOF'
    EXPERIENCE = 'EXPERIENCE'
    OTHER = 'OTHER'

    DOC_TYPES = [
        (OFFER, 'Offer Letter'),
        (APPOINTMENT, 'Appointment Letter'),
        (DEGREE, 'Degree Certificate'),
        (PAN_CARD, 'PAN Card'),
        (AADHAR_CARD, 'Aadhar Card'),
        (ID_PROOF, 'ID Proof'),
        (EXPERIENCE, 'Experience Letter'),
        (OTHER, 'Other'),
    ]

    # HR may only ever upload these — official, company-issued letters.
    HR_DOC_TYPES = [OFFER, APPOINTMENT]
    # Everyone uploads these to their own profile — HR cannot upload
    # personal documents on someone else's behalf.
    SELF_DOC_TYPES = [DEGREE, PAN_CARD, AADHAR_CARD, ID_PROOF, EXPERIENCE, OTHER]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='documents')
    doc_type = models.CharField(max_length=20, choices=DOC_TYPES)
    file = models.FileField(upload_to='employee_docs/')
    uploaded_at = models.DateTimeField(auto_now_add=True)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name='uploaded_documents')

    def __str__(self):
        return f"{self.get_doc_type_display()} - {self.user}"


class ResignationRequest(models.Model):
    """Employee/Manager/Admin submits just a reason. HR then approves
    (setting the notice period), rejects, or negotiates. If HR negotiates,
    the employee can accept the offer (withdrawing) or confirm they still
    want to quit, which sends it back to HR to allot a notice period."""
    STATUS_CHOICES = [
        ('PENDING', 'Pending HR Review'),
        ('NEGOTIATING', 'HR Sent an Offer'),
        ('ACCEPTED', 'Approved'),
        ('REJECTED', 'Rejected'),
        ('WITHDRAWN', 'Withdrawn (Offer Accepted)'),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='resignation_requests')
    reason = models.TextField(help_text="Reason for resigning.")
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default='PENDING')
    submitted_at = models.DateTimeField(auto_now_add=True)

    # Only ever set by HR, at approval time.
    notice_period_days = models.PositiveIntegerField(null=True, blank=True)
    exit_date = models.DateField(null=True, blank=True)

    # HR's negotiation offer.
    hr_message = models.TextField(blank=True)
    hr_message_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='resignation_offers_sent')
    hr_message_at = models.DateTimeField(null=True, blank=True)

    # Employee's reply, only used if they decline the offer and quit anyway.
    employee_reply = models.TextField(blank=True)
    employee_replied_at = models.DateTimeField(null=True, blank=True)

    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='resignations_reviewed')
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-submitted_at']

    def approve(self, hr_user, notice_period_days):
        self.notice_period_days = notice_period_days
        self.exit_date = timezone.localdate() + timedelta(days=notice_period_days)
        self.status = 'ACCEPTED'
        self.reviewed_by = hr_user
        self.reviewed_at = timezone.now()
        self.save()

        profile, _ = EmployeeProfile.objects.get_or_create(user=self.user)
        profile.status = 'EXITED'
        profile.exit_date = self.exit_date
        profile.exit_reason = self.reason
        profile.save()

    def reject(self, hr_user):
        self.status = 'REJECTED'
        self.reviewed_by = hr_user
        self.reviewed_at = timezone.now()
        self.save()

    def send_negotiation(self, hr_user, message):
        self.status = 'NEGOTIATING'
        self.hr_message = message
        self.hr_message_by = hr_user
        self.hr_message_at = timezone.now()
        self.save()

    def accept_offer(self):
        """Employee stays — resignation is withdrawn."""
        self.status = 'WITHDRAWN'
        self.employee_reply = "Accepted HR's offer and withdrew the resignation."
        self.employee_replied_at = timezone.now()
        self.save()

    def quit_anyway(self, message=''):
        """Employee still wants to leave. Goes back to HR to allot a notice period."""
        self.status = 'PENDING'
        self.employee_reply = message or "I'd still like to proceed with my resignation."
        self.employee_replied_at = timezone.now()
        self.save()

    def __str__(self):
        return f"Resignation - {self.user} ({self.status})"