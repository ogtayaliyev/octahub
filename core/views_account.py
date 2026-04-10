from django.contrib.auth import login, logout as auth_logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse

from .forms import AdminUserCreateForm, AdminUserEditForm, UserProfileForm, UserUpdateForm
from .models import UserProfile


def is_admin_user(user):
    return user.is_authenticated and user.is_superuser


def signup(request):
    if request.method == 'POST':
        form = UserCreationForm(request.POST)
        accept_terms = request.POST.get('accept_terms')

        if not accept_terms:
            form.add_error(None, "Vous devez accepter les conditions d'utilisation pour continuer.")
        elif form.is_valid():
            user = form.save()
            login(request, user)
            return redirect('index')
    else:
        form = UserCreationForm()
    return render(request, 'core/signup.html', {'form': form})


def custom_logout(request):
    auth_logout(request)
    return redirect('landing')


@login_required
def index(request):
    return render(request, 'core/index.html')


@login_required
def profile(request):
    if not hasattr(request.user, 'profile'):
        UserProfile.objects.create(user=request.user)

    if request.method == 'POST':
        user_form = UserUpdateForm(request.POST, instance=request.user)
        profile_form = UserProfileForm(request.POST, instance=request.user.profile)

        if user_form.is_valid() and profile_form.is_valid():
            user_form.save()
            profile_form.save()
            return redirect('profile')
    else:
        user_form = UserUpdateForm(instance=request.user)
        profile_form = UserProfileForm(instance=request.user.profile)

    return render(request, 'core/profile.html', {
        'user_form': user_form,
        'profile_form': profile_form
    })


@user_passes_test(is_admin_user, login_url='login')
def admin_users(request):
    users = User.objects.all().order_by('-date_joined')
    for account in users:
        UserProfile.objects.get_or_create(user=account)

    status_messages = {
        'created': 'Utilisateur ajouté avec succès.',
        'updated': 'Informations mises à jour avec succès.',
        'deleted': 'Utilisateur supprimé avec succès.',
        'self_delete': 'Vous ne pouvez pas supprimer votre propre compte admin.'
    }

    selected_user = request.user
    selected_user_id = request.GET.get('edit')
    if selected_user_id:
        selected_user = get_object_or_404(User, pk=selected_user_id)

    selected_profile, _ = UserProfile.objects.get_or_create(user=selected_user)

    create_form = AdminUserCreateForm()
    create_profile_form = UserProfileForm(prefix='create')
    edit_form = AdminUserEditForm(instance=selected_user)
    edit_profile_form = UserProfileForm(instance=selected_profile, prefix='edit')

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'create':
            create_form = AdminUserCreateForm(request.POST)
            create_profile_form = UserProfileForm(request.POST, prefix='create')

            if create_form.is_valid() and create_profile_form.is_valid():
                new_user = create_form.save()
                profile, _ = UserProfile.objects.get_or_create(user=new_user)
                for field, value in create_profile_form.cleaned_data.items():
                    setattr(profile, field, value)
                profile.save()
                return redirect(f"{reverse('admin_users')}?status=created&edit={new_user.id}")

        elif action == 'update':
            user_id = request.POST.get('user_id')
            selected_user = get_object_or_404(User, pk=user_id)
            selected_profile, _ = UserProfile.objects.get_or_create(user=selected_user)
            edit_form = AdminUserEditForm(request.POST, instance=selected_user)
            edit_profile_form = UserProfileForm(request.POST, instance=selected_profile, prefix='edit')

            if edit_form.is_valid() and edit_profile_form.is_valid():
                updated_user = edit_form.save(commit=False)
                new_password = edit_form.cleaned_data.get('new_password')
                updated_user.save()

                if new_password:
                    updated_user.set_password(new_password)
                    updated_user.save()
                    if updated_user.pk == request.user.pk:
                        update_session_auth_hash(request, updated_user)

                edit_profile_form.save()
                return redirect(f"{reverse('admin_users')}?status=updated&edit={updated_user.id}")

        elif action == 'delete':
            user_id = request.POST.get('user_id')
            target_user = get_object_or_404(User, pk=user_id)

            if target_user.pk == request.user.pk:
                return redirect(f"{reverse('admin_users')}?status=self_delete&edit={request.user.pk}")

            target_user.delete()
            return redirect(f"{reverse('admin_users')}?status=deleted")

    context = {
        'users': users,
        'selected_user': selected_user,
        'create_form': create_form,
        'create_profile_form': create_profile_form,
        'edit_form': edit_form,
        'edit_profile_form': edit_profile_form,
        'status_message': status_messages.get(request.GET.get('status'), ''),
        'total_users': users.count(),
        'active_users': users.filter(is_active=True).count(),
        'staff_users': users.filter(is_staff=True).count(),
        'superusers': users.filter(is_superuser=True).count(),
    }
    return render(request, 'core/admin_users.html', context)
