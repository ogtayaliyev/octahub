import os

from django.shortcuts import render, redirect


def landing(request):
    if request.user.is_authenticated:
        return redirect('index')
    return render(request, 'core/landing.html')


def info(request):
    return render(request, 'core/info.html')


def terms(request):
    return render(request, 'core/terms.html')


def privacy(request):
    return render(request, 'core/privacy.html')


def feedback_index(request):
    return render(request, 'core/feedback.html', {
        'contact_email': os.environ.get('CONTACT_EMAIL_TO', 'legal@octahub.fr')
    })
