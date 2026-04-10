from django.db import models
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.dispatch import receiver

class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    phone = models.CharField(max_length=20, blank=True, null=True, verbose_name="Numéro de téléphone")
    address = models.CharField(max_length=255, blank=True, null=True, verbose_name="Adresse")
    city = models.CharField(max_length=100, blank=True, null=True, verbose_name="Ville")
    postal_code = models.CharField(max_length=10, blank=True, null=True, verbose_name="Code postal")
    country = models.CharField(max_length=100, blank=True, null=True, default="France", verbose_name="Pays")
    bio = models.TextField(blank=True, null=True, verbose_name="Bio")
    company = models.CharField(max_length=150, blank=True, null=True, verbose_name="Entreprise")
    job_title = models.CharField(max_length=100, blank=True, null=True, verbose_name="Poste")
    website = models.URLField(blank=True, null=True, verbose_name="Site web")
    
    def __str__(self):
        return f"Profil de {self.user.username}"

@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        UserProfile.objects.create(user=instance)

@receiver(post_save, sender=User)
def save_user_profile(sender, instance, **kwargs):
    if hasattr(instance, 'profile'):
        instance.profile.save()
