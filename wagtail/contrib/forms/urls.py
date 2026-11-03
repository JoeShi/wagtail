from django.urls import path

from wagtail.contrib.forms.views import (
    ApplyFormSubmissionFilterPresetView,
    DeleteFormSubmissionFilterPresetView,
    DeleteSubmissionsView,
    FormPagesListView,
    RenameFormSubmissionFilterPresetView,
    SaveFormSubmissionFilterPresetView,
    get_submissions_list_view,
)

app_name = "wagtailforms"
urlpatterns = [
    path("", FormPagesListView.as_view(), name="index"),
    path(
        "results/", FormPagesListView.as_view(results_only=True), name="index_results"
    ),
    path(
        "submissions/<int:page_id>/", get_submissions_list_view, name="list_submissions"
    ),
    path(
        "submissions/<int:page_id>/results/",
        get_submissions_list_view,
        {"results_only": True},
        name="list_submissions_results",
    ),
    path(
        "submissions/<int:page_id>/filter-presets/create/",
        SaveFormSubmissionFilterPresetView.as_view(),
        name="save_filter_preset",
    ),
    path(
        "submissions/<int:page_id>/filter-presets/<int:preset_id>/apply/",
        ApplyFormSubmissionFilterPresetView.as_view(),
        name="apply_filter_preset",
    ),
    path(
        "submissions/<int:page_id>/filter-presets/<int:preset_id>/rename/",
        RenameFormSubmissionFilterPresetView.as_view(),
        name="rename_filter_preset",
    ),
    path(
        "submissions/<int:page_id>/filter-presets/<int:preset_id>/delete/",
        DeleteFormSubmissionFilterPresetView.as_view(),
        name="delete_filter_preset",
    ),
    path(
        "submissions/<int:page_id>/delete/",
        DeleteSubmissionsView.as_view(),
        name="delete_submissions",
    ),
]
