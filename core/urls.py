from django.urls import path, include
from . import views
from django.contrib.auth import views as auth_views

urlpatterns = [
    path('', views.landing, name='landing'),
    path('app/', views.index, name='index'),
    path('info/', views.info, name='info'),
    path('terms/', views.terms, name='terms'),
    path('privacy/', views.privacy, name='privacy'),
    path('feedback/', views.feedback_index, name='feedback_index'),
    path('seo/', views.seo_index, name='seo_index'),
    path('crawler/', views.crawler_index, name='crawler_index'),
    path('vmap/', views.vmap_index, name='vmap_index'),
    path('audit/', views.audit_index, name='audit_index'),
    path('audit-scrape/', views.audit_scrape, name='audit_scrape'),
    path('forms/', views.forms_index, name='forms_index'),
    path('forms-scrape/', views.forms_scrape, name='forms_scrape'),
    path('keywords/', views.keywords_index, name='keywords_index'),
    path('profile/', views.profile, name='profile'),
    path('admin-users/', views.admin_users, name='admin_users'),
    path('scrape/', views.scrape, name='scrape'),
    path('seo-scrape/', views.seo_scrape, name='seo_scrape'),
    path('crawler-scrape/', views.crawler_scrape, name='crawler_scrape'),
    path('vmap-scrape/', views.vmap_scrape, name='vmap_scrape'),
    path('keywords-generate/', views.keywords_generate, name='keywords_generate'),
    path('download-zip/', views.download_zip, name='download_zip'),
    
    # Auth
    path('signup/', views.signup, name='signup'),
    path('login/', auth_views.LoginView.as_view(template_name='core/login.html'), name='login'),
    path('logout/', views.custom_logout, name='logout'),
    
    # Password Reset
    path('password-reset/', auth_views.PasswordResetView.as_view(template_name='core/password_reset.html'), name='password_reset'),
    path('password-reset/done/', auth_views.PasswordResetDoneView.as_view(template_name='core/password_reset_done.html'), name='password_reset_done'),
    path('password-reset-confirm/<uidb64>/<token>/', auth_views.PasswordResetConfirmView.as_view(template_name='core/password_reset_confirm.html'), name='password_reset_confirm'),
    path('password-reset-complete/', auth_views.PasswordResetCompleteView.as_view(template_name='core/password_reset_complete.html'), name='password_reset_complete'),
]
