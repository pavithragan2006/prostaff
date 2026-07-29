def get_active_branch(request):
    from core.models import Branch
    user = request.user
    can_switch = user_can_switch_branch(user)
    if not can_switch:
        return getattr(user, 'branch', None)

    branch_id = request.session.get('active_branch_id')
    if branch_id:
        if user.role == 'ADMIN':
            branch = Branch.objects.filter(id=branch_id).first()
        else:
            branch = user.accessible_branches.filter(id=branch_id).first()
        if branch:
            return branch

    if user.role == 'ADMIN':
        return user.branch or Branch.objects.first()
    return user.branch or user.accessible_branches.first()

def user_can_switch_branch(user):
    if not user.is_authenticated:
        return False
    if user.role == 'ADMIN':
        return True
    if user.role == 'HR':
        if user.can_access_all_branches:
            return True
        return user.accessible_branches.exclude(id=user.branch_id).exists()
    return False
def get_manager_team(manager):
    """Everyone reporting to this manager, either via department headship
    or a direct manager link (used when a department has no head), scoped
    to the manager's branch."""
    from django.db.models import Q
    from core.models import User
    return User.objects.filter(
        Q(department__manager=manager) |
        Q(manager=manager, department__manager__isnull=True),
        branch=manager.branch,
    ).exclude(id=manager.id).distinct()