from functools import wraps
from django.core.exceptions import PermissionDenied
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect
from django.contrib import messages


def role_required(*roles):
    """Restrict a view to users whose .role is in `roles`."""
    def decorator(view_func):
        @wraps(view_func)
        @login_required
        def _wrapped(request, *args, **kwargs):
            if request.user.role not in roles:
                raise PermissionDenied("You do not have permission to view this page.")
            return view_func(request, *args, **kwargs)
        return _wrapped
    return decorator


def hr_or_admin_required(view_func):
    """View-only pages: HR and Admin can both open these."""
    return role_required('ADMIN', 'HR')(view_func)


def hr_only_required(view_func):
    """Real actions (approve/reject/onboard/etc). Admin is view-only in this
    system, so Admin gets redirected back with a friendly message instead of
    a hard 403."""
    @wraps(view_func)
    @login_required
    def _wrapped(request, *args, **kwargs):
        if request.user.role != 'HR':
            messages.error(request, "Admin has view-only access. Only HR can perform this action.")
            return redirect('core:dashboard')
        return view_func(request, *args, **kwargs)
    return _wrapped
def hr_admin_or_manager_required(view_func):
    """Extended-profile identity fields (enrollment ID, aadhar, bank
    details, references, etc.) are editable/viewable in detail by HR,
    Admin, or the employee's Manager — per the HR identity-data policy.
    This is deliberately separate from hr_only_required, which governs
    workflow *actions* (approvals, onboarding, payroll)."""
    @wraps(view_func)
    @login_required
    def _wrapped(request, *args, **kwargs):
        if not (request.user.role in ('HR', 'ADMIN') or request.user.is_manager()):
            messages.error(request, "You do not have permission to view this page.")
            return redirect('core:dashboard')
        return view_func(request, *args, **kwargs)
    return _wrapped

def admin_only_required(view_func):
    @wraps(view_func)
    @login_required
    def _wrapped(request, *args, **kwargs):
        if request.user.role != 'ADMIN':
            raise PermissionDenied("Only Admin can perform this action.")
        return view_func(request, *args, **kwargs)
    return _wrapped

def coo_required(view_func):   # was ceo_required
    @wraps(view_func)
    @login_required
    def _wrapped(request, *args, **kwargs):
        if request.user.role != 'COO':   # was 'CEO'
            raise PermissionDenied("Only the COO can view this page.")
        return view_func(request, *args, **kwargs)
    return _wrapped