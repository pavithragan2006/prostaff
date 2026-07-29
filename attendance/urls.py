from django.urls import path
from attendance import views

app_name = 'attendance'

urlpatterns = [
    path('punch/', views.punch, name='punch'),
    path('me/', views.my_attendance, name='my_attendance'),
    path('reports/', views.attendance_reports, name='reports'),
    path('reports/<int:user_id>/', views.employee_attendance, name='employee_attendance'),

    path('active-today/', views.active_attendance_today, name='active_attendance_today'),

    # Unified weekly / monthly / yearly attendance view (self-service)
    path('me/attendance/', views.my_attendance_view, name='my_attendance_view'),
    path('me/attendance/download/', views.download_my_attendance_view, name='download_my_attendance_view'),
    path('me/attendance/archive/', views.my_monthly_attendance_archive, name='my_monthly_attendance_archive'),

    # Unified weekly / monthly / yearly attendance view (HR/Admin viewing an employee)
    path('employee/<int:user_id>/attendance/', views.employee_attendance_view, name='employee_attendance_view'),
    path('employee/<int:user_id>/attendance/download/', views.download_employee_attendance_view, name='download_employee_attendance_view'),
    path('employee/<int:user_id>/attendance/archive/', views.monthly_attendance_archive_hr, name='monthly_attendance_archive_hr'),

    # Combined monthly register for ALL active employees
    path('team/monthly/', views.team_monthly_attendance, name='team_monthly_attendance'),
    path('team/monthly/<int:year>/<int:month>/download/', views.download_team_monthly_attendance, name='download_team_monthly_attendance'),

    path('overtime/', views.overtime_permissions, name='overtime_permissions'),
    path('overtime/<int:permission_id>/revoke/', views.revoke_overtime_permission, name='revoke_overtime_permission'),
    path('overtime/mine/', views.my_overtime_permissions, name='my_overtime_permissions'),
    path('employee/<int:user_id>/attendance/overrides/', views.update_monthly_overrides, name='update_monthly_overrides'),
    path('overtime-requests/<int:request_id>/approve/', views.approve_overtime_request, name='approve_overtime_request'),
    path('overtime-requests/<int:request_id>/reject/', views.reject_overtime_request, name='reject_overtime_request'),
    path('overtime-requests/<int:request_id>/approve/', views.approve_overtime_request, name='approve_overtime_request'),
    path('overtime-requests/<int:request_id>/reject/', views.reject_overtime_request, name='reject_overtime_request'),
    path('overtime-requests/<int:request_id>/approve-hr/', views.approve_overtime_request_hr, name='approve_overtime_request_hr'),
    path('overtime-requests/<int:request_id>/reject-hr/', views.reject_overtime_request_hr, name='reject_overtime_request_hr'),
]