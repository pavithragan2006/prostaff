from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.forms import UserChangeForm
from django.utils.safestring import mark_safe
from core.models import User, Department, Branch

admin.site.register(Branch)


class SimpleUserChangeForm(UserChangeForm):
    class Meta(UserChangeForm.Meta):
        model = User

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if 'password' in self.fields:
            del self.fields['password']

@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ('name', 'branch', 'manager')
    list_filter = ('branch',)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        # When a manager is appointed/removed on a department, push that
        # change onto every employee in it, so leave-request routing
        # (LeaveRequest.get_manager) always sees the current manager.
        # Scoped to the SAME branch as this department, so touching
        # Kancheepuram's Data Entry never reassigns Chennai's Data Entry staff.
        User.objects.filter(department=obj, role='EMPLOYEE', branch=obj.branch).update(manager=obj.manager)
        
@admin.register(User)
class CustomUserAdmin(UserAdmin):
    form = SimpleUserChangeForm

    fieldsets = (
        (None, {'fields': ('username', 'reset_password_link')}),
        ('Personal Info', {'fields': ('first_name', 'last_name', 'email')}),
        ('HRMS Info', {'fields': ('role', 'employee_id', 'department', 'manager', 'branch', 'accessible_branches', 'can_access_all_branches', 'date_joined_company', 'phone')}),
        ('Access', {'fields': ('is_active',)}),
    )
    

    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('username', 'password1', 'password2', 'role', 'employee_id'),
        }),
    )

    list_display = ('username', 'first_name', 'last_name', 'role', 'department', 'is_active')
    list_filter = ('role', 'department', 'is_active')
    search_fields = ('username', 'first_name', 'last_name', 'employee_id')

    def get_readonly_fields(self, request, obj=None):
        # Admin accounts are top priority — their role can never be changed,
        # from here or anywhere else in the app.
        if obj and obj.role == 'ADMIN':
            return ('reset_password_link', 'role')
        return ('reset_password_link',)

    def reset_password_link(self, obj):
        if obj and obj.pk:
            return mark_safe('<a href="../password/">Reset this user\'s password</a>')
        return "-"

    reset_password_link.short_description = "Password"