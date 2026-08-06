from django.urls import path
from django.contrib.auth import views as auth_views
from core import views
from core import coo_views

app_name = 'core'

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('login/', views.HRLoginView.as_view(), name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),

    path('password-reset/', auth_views.PasswordResetView.as_view(
        template_name='core/password_reset.html',
        email_template_name='core/password_reset_email.txt',
        subject_template_name='core/password_reset_subject.txt',
        success_url='/password-reset/done/',
    ), name='password_reset'),

    path('password-reset/done/', auth_views.PasswordResetDoneView.as_view(
        template_name='core/password_reset_done.html',
    ), name='password_reset_done'),

    path('reset/<uidb64>/<token>/', auth_views.PasswordResetConfirmView.as_view(
        template_name='core/password_reset_confirm.html',
        success_url='/reset/done/',
    ), name='password_reset_confirm'),

    path('reset/done/', auth_views.PasswordResetCompleteView.as_view(
        template_name='core/password_reset_complete.html',
    ), name='password_reset_complete'),

    path('set-branch/', views.set_active_branch, name='set_active_branch'),
]




urlpatterns += [
    path('coo/', coo_views.coo_dashboard, name='coo_dashboard'),
    path('coo/leave/<int:leave_id>/<str:decision>/', coo_views.coo_review_leave, name='coo_review_leave'),
    path('coo/increment/<int:increment_id>/<str:decision>/', coo_views.coo_decide_increment, name='coo_decide_increment'),
    path('coo/reports/<str:report_type>/download/', coo_views.coo_download_report, name='coo_download_report'),
]
