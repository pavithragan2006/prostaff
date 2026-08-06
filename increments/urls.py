from django.urls import path
from increments import views

app_name = 'increments'

urlpatterns = [
    path('', views.increment_list, name='list'),
    path('create/', views.create_increment, name='create'),
    path('<int:increment_id>/approve/', views.approve_increment, name='approve'),
    path('<int:increment_id>/reject/', views.reject_increment, name='reject'),
    path('feedback/', views.manager_feedback_list, name='manager_feedback_list'),
    path('<int:increment_id>/feedback/', views.submit_increment_feedback, name='submit_feedback'),
    path('due/<int:user_id>/dismiss/', views.dismiss_due_increment, name='dismiss_due_increment'),
    path('history/<int:user_id>/', views.increment_history_detail, name='history_detail'),
]

urlpatterns += [
    path('request-report/<int:user_id>/', views.request_performance_report, name='request_performance_report'),
    path('manager-report-inbox/', views.manager_report_inbox, name='manager_report_inbox'),
    path('submit-report/<int:increment_id>/', views.submit_performance_report, name='submit_performance_report'),
    path('hr-review-reports/', views.hr_review_reports, name='hr_review_reports'),
    path('hr-reports/', views.hr_reports_inbox, name='hr_reports_inbox'),
    path('hr-coo-approval/', views.hr_coo_approval, name='hr_coo_approval'),
    path('hr-send-to-coo/<int:increment_id>/', views.hr_send_to_coo, name='hr_send_to_coo'),
   
]