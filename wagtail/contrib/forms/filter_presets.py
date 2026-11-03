import swapper
from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

swapper.set_app_prefix("wagtailcore", "wagtail")


class FormSubmissionFilterPreset(models.Model):
    """A named submission filter owned by a user for a specific form page."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    page = models.ForeignKey(
        swapper.get_model_name("wagtailcore", "Page"), on_delete=models.CASCADE
    )
    name = models.CharField(verbose_name=_("name"), max_length=255)
    date_from = models.DateField(verbose_name=_("date from"), null=True, blank=True)
    date_to = models.DateField(verbose_name=_("date to"), null=True, blank=True)
    ordering = models.JSONField(default=list)

    def __str__(self):
        return self.name

    class Meta:
        ordering = ["name", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=("user", "page", "name"),
                name="wagtailforms_unique_submission_filter_preset",
            )
        ]
