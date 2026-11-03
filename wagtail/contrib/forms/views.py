import datetime
from collections import OrderedDict

import swapper
from django.contrib.admin.utils import quote
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.forms import CheckboxSelectMultiple
from django.http import Http404, HttpResponseRedirect, QueryDict
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils.functional import classproperty
from django.utils.translation import gettext, gettext_lazy, ngettext
from django.views.generic import TemplateView, View
from django_filters import DateFromToRangeFilter

from wagtail.admin import messages
from wagtail.admin.filters import (
    DateRangePickerWidget,
    MultipleContentTypeFilter,
    WagtailFilterSet,
)
from wagtail.admin.ui.tables import Column, TitleColumn
from wagtail.admin.utils import get_valid_next_url_from_request
from wagtail.admin.views.generic import PermissionCheckedMixin
from wagtail.admin.views.generic.base import BaseListingView
from wagtail.admin.views.mixins import SpreadsheetExportMixin
from wagtail.admin.views.pages.listing import PageFilterSet, PageListingMixin
from wagtail.contrib.forms.forms import (
    FormSubmissionFilterPresetForm,
    FormSubmissionFilterPresetRenameForm,
    validate_ordering_values,
)
from wagtail.contrib.forms.models import FormMixin, FormSubmissionFilterPreset
from wagtail.contrib.forms.utils import get_form_types, get_forms_for_user

Page = swapper.load_model("wagtailcore", "Page")


def get_submissions_list_view(request, *args, **kwargs):
    """Call the form page's list submissions view class"""
    page_id = kwargs.get("page_id")
    form_page = get_object_or_404(Page, id=page_id).specific
    if not isinstance(form_page, FormMixin):
        raise Http404
    return form_page.serve_submissions_list_view(request, *args, **kwargs)


class ContentTypeColumn(Column):
    edit_url_name = "wagtailadmin_pages:edit"
    cell_template_name = "wagtailforms/content_type_column.html"

    def get_url(self, instance):
        return reverse(self.edit_url_name, args=(quote(instance.pk),))

    def get_cell_context_data(self, instance, parent_context):
        context = super().get_cell_context_data(instance, parent_context)
        context["url"] = self.get_url(instance)
        return context


class FormPageFilterSet(PageFilterSet):
    content_type = MultipleContentTypeFilter(
        label=gettext_lazy("Page type"),
        queryset=lambda request: ContentType.objects.filter(
            pk__in=[ct.pk for ct in get_form_types()]
        ).order_by("model"),
        widget=CheckboxSelectMultiple,
    )


class FormPagesListView(PageListingMixin, PermissionCheckedMixin, BaseListingView):
    """Lists the available form pages for the current user"""

    any_permission_required = {
        "add",
        "change",
        "publish",
        "bulk_delete",
        "lock",
        "unlock",
    }
    template_name = "wagtailforms/index.html"
    results_template_name = "wagtailforms/index_results.html"
    context_object_name = "form_pages"
    paginate_by = 20
    page_kwarg = "p"
    index_url_name = "wagtailforms:index"
    index_results_url_name = "wagtailforms:index_results"
    page_title = gettext_lazy("Forms")
    header_icon = "form"
    model = Page
    is_searchable = True
    filterset_class = FormPageFilterSet

    @classproperty
    def columns(self):
        columns = [
            col
            for col in PageListingMixin.base_columns
            if col.name not in {"title", "type"}
        ]
        columns.insert(
            1,
            TitleColumn(
                "title",
                classname="title",
                label=gettext_lazy("Title"),
                url_name="wagtailforms:list_submissions",
                sort_key="title",
                width="30%",
            ),
        )
        columns.insert(
            -1,
            ContentTypeColumn(
                "content_type",
                label=gettext_lazy("Origin"),
                sort_key="content_type",
                width="20%",
            ),
        )
        return columns

    def get_breadcrumbs_items(self):
        return self.breadcrumbs_items + [
            {"url": "", "label": self.page_title, "sublabel": gettext("Pages")},
        ]

    def get_base_queryset(self):
        """Return the queryset of form pages for this view"""
        pages = get_forms_for_user(self.request.user).select_related("content_type")
        return self.annotate_queryset(pages)


class DeleteSubmissionsView(TemplateView):
    """Delete the selected submissions"""

    template_name = "wagtailforms/confirm_delete.html"
    page = None
    submissions = None
    success_url_name = "wagtailforms:list_submissions"

    def get_queryset(self):
        """Returns a queryset for the selected submissions"""
        submission_ids = self.request.GET.getlist("selected-submissions")
        return self.page.get_submissions().filter(id__in=submission_ids)

    def handle_delete(self, submissions):
        """Deletes the given queryset"""
        count = submissions.count()
        submissions.delete()
        messages.success(
            self.request,
            ngettext(
                "One submission has been deleted.",
                "%(count)d submissions have been deleted.",
                count,
            )
            % {"count": count},
        )

    def get_success_url(self):
        """Returns the success URL to redirect to after a successful deletion"""
        next_url = get_valid_next_url_from_request(self.request)
        if next_url:
            return next_url
        return reverse(self.success_url_name, args=(self.page.id,))

    def dispatch(self, request, *args, **kwargs):
        """Check permissions, set the page and submissions, handle delete"""
        page_id = kwargs.get("page_id")

        if not get_forms_for_user(self.request.user).filter(id=page_id).exists():
            raise PermissionDenied

        self.page = get_object_or_404(Page, id=page_id).specific

        self.submissions = self.get_queryset()

        if self.request.method == "POST":
            self.handle_delete(self.submissions)
            return redirect(self.get_success_url())

        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        """Get the context for this view"""
        context = super().get_context_data(**kwargs)

        context.update(
            {
                "page": self.page,
                "submissions": self.submissions,
            }
        )
        context["next_url"] = self.get_success_url()

        return context


class FormSubmissionFilterPresetViewMixin:
    """Resolve a form page and scope presets to the current user and page."""

    filter_preset = None

    def dispatch(self, request, *args, **kwargs):
        self.form_page = get_object_or_404(Page, id=kwargs["page_id"]).specific
        if not isinstance(self.form_page, FormMixin):
            raise Http404
        if not get_forms_for_user(request.user).filter(pk=self.form_page.pk).exists():
            raise PermissionDenied
        if "preset_id" in kwargs:
            self.filter_preset = get_object_or_404(
                self.get_presets(), pk=kwargs["preset_id"]
            )
        return super().dispatch(request, *args, **kwargs)

    def get_presets(self):
        return FormSubmissionFilterPreset.objects.filter(
            user=self.request.user, page=self.form_page
        )

    def get_ordering_configuration(self):
        view_class = self.form_page.get_submissions_list_view_class()
        return {
            "orderable_fields": view_class.orderable_fields,
            "default_ordering": view_class.default_ordering,
        }

    def get_success_url(self):
        return get_valid_next_url_from_request(self.request) or reverse(
            "wagtailforms:list_submissions", args=(self.form_page.pk,)
        )

    def get_form_kwargs(self):
        return {
            "user": self.request.user,
            "page": self.form_page,
            **self.get_ordering_configuration(),
        }

    def get_context_data(self, **kwargs):
        return {
            "form_page": self.form_page,
            "filter_preset": self.filter_preset,
            "next_url": self.get_success_url(),
            **kwargs,
        }

    def render_form(self, form):
        return TemplateResponse(
            self.request,
            "wagtailforms/filter_preset_form.html",
            self.get_context_data(form=form),
        )

    def save_form(self, form):
        try:
            with transaction.atomic():
                form.save()
        except IntegrityError:
            form.add_error(
                "name",
                ValidationError(
                    gettext(
                        "A saved filter with this name already exists for this form."
                    )
                ),
            )
            return self.render_form(form)
        return None


class SaveFormSubmissionFilterPresetView(FormSubmissionFilterPresetViewMixin, View):
    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        form = FormSubmissionFilterPresetForm(
            request.POST,
            ordering_values=request.POST.getlist("order_by"),
            **self.get_form_kwargs(),
        )
        if not form.is_valid():
            return self.render_form(form)
        form.instance.ordering = form.cleaned_data["ordering"]
        response = self.save_form(form)
        if response:
            return response
        messages.success(request, gettext("Saved filter created."))
        return HttpResponseRedirect(self.get_success_url(), status=303)


class ApplyFormSubmissionFilterPresetView(FormSubmissionFilterPresetViewMixin, View):
    http_method_names = ["get"]

    def get(self, request, *args, **kwargs):
        preset = self.filter_preset
        try:
            if not isinstance(preset.ordering, list):
                raise ValidationError(gettext("Invalid ordering."))
            ordering = validate_ordering_values(
                preset.ordering, **self.get_ordering_configuration()
            )
            if (
                preset.date_from
                and preset.date_to
                and preset.date_from > preset.date_to
            ):
                raise ValidationError(gettext("Invalid date range."))
        except ValidationError:
            messages.error(
                request, gettext("The saved filter is invalid and cannot be applied.")
            )
            return redirect("wagtailforms:list_submissions", page_id=self.form_page.pk)

        query = QueryDict("", mutable=True)
        if preset.date_from:
            query["date_from"] = preset.date_from.isoformat()
        if preset.date_to:
            query["date_to"] = preset.date_to.isoformat()
        query.setlist("order_by", ordering)
        url = reverse("wagtailforms:list_submissions", args=(self.form_page.pk,))
        if encoded_query := query.urlencode():
            url = f"{url}?{encoded_query}"
        return redirect(url)


class RenameFormSubmissionFilterPresetView(FormSubmissionFilterPresetViewMixin, View):
    http_method_names = ["get", "post"]

    def get_form(self):
        return FormSubmissionFilterPresetRenameForm(
            self.request.POST if self.request.method == "POST" else None,
            instance=self.filter_preset,
            **self.get_form_kwargs(),
        )

    def get(self, request, *args, **kwargs):
        return self.render_form(self.get_form())

    def post(self, request, *args, **kwargs):
        form = self.get_form()
        if not form.is_valid():
            return self.render_form(form)
        response = self.save_form(form)
        if response:
            return response
        messages.success(request, gettext("Saved filter renamed."))
        return HttpResponseRedirect(self.get_success_url(), status=303)


class DeleteFormSubmissionFilterPresetView(FormSubmissionFilterPresetViewMixin, View):
    http_method_names = ["get", "post"]

    def get(self, request, *args, **kwargs):
        return TemplateResponse(
            request,
            "wagtailforms/confirm_delete_filter_preset.html",
            self.get_context_data(),
        )

    def post(self, request, *args, **kwargs):
        self.filter_preset.delete()
        messages.success(request, gettext("Saved filter deleted."))
        return HttpResponseRedirect(self.get_success_url(), status=303)


class SubmissionsListFilterSet(WagtailFilterSet):
    date = DateFromToRangeFilter(
        label=gettext_lazy("Submission date"),
        field_name="submit_time",
        widget=DateRangePickerWidget,
    )


class SubmissionsListView(SpreadsheetExportMixin, BaseListingView):
    """Lists submissions for the provided form page"""

    template_name = "wagtailforms/submissions_index.html"
    results_template_name = "wagtailforms/list_submissions.html"
    context_object_name = "submissions"
    form_page = None
    default_ordering = ("-submit_time",)
    ordering_csv = ("submit_time",)  # keep legacy CSV ordering
    orderable_fields = (
        "id",
        "submit_time",
    )  # used to validate ordering in URL
    page_title = gettext_lazy("Form data")
    header_icon = "form"
    paginate_by = 20
    filterset_class = SubmissionsListFilterSet
    forms_index_url_name = "wagtailforms:index"
    index_url_name = "wagtailforms:list_submissions"
    index_results_url_name = "wagtailforms:list_submissions_results"
    show_export_buttons = True

    def dispatch(self, request, *args, **kwargs):
        """Check permissions and set the form page"""

        self.form_page = kwargs.get("form_page")

        if not get_forms_for_user(request.user).filter(pk=self.form_page.id).exists():
            raise PermissionDenied

        if self.is_export:
            data_fields = self.form_page.get_data_fields()
            # Set the export fields and the headings for spreadsheet export
            self.list_export = [field for field, label in data_fields]
            self.export_headings = dict(data_fields)

        return super().dispatch(request, *args, **kwargs)

    def get_filterset_kwargs(self):
        kwargs = super().get_filterset_kwargs()
        kwargs["queryset"] = self.get_base_queryset()
        return kwargs

    def get_base_queryset(self):
        """Return queryset of form submissions"""
        return self.form_page.get_submissions()

    def get_validated_ordering(self):
        """Return a dict of field names with ordering labels if ordering is valid"""
        orderable_fields = self.orderable_fields or ()
        ordering = {}
        if self.is_export:
            #  Revert to CSV order_by submit_time ascending for backwards compatibility
            default_ordering = self.ordering_csv or ()
        else:
            default_ordering = self.default_ordering or ()
        if isinstance(default_ordering, str):
            default_ordering = (default_ordering,)
        ordering_strs = self.request.GET.getlist("order_by") or list(default_ordering)
        for order in ordering_strs:
            try:
                _, prefix, field_name = order.rpartition("-")
                if field_name in orderable_fields:
                    ordering[field_name] = (
                        prefix,
                        "descending" if prefix == "-" else "ascending",
                    )
            except (IndexError, ValueError):
                continue  # invalid ordering specified, skip it
        return ordering

    def get_ordering(self):
        """Return the field or fields to use for ordering the queryset"""
        ordering = self.get_validated_ordering()
        return [values[0] + name for name, values in ordering.items()]

    def get_filename(self):
        """Returns the base filename for the generated spreadsheet data file"""
        return "{}-export-{}".format(
            self.form_page.slug, datetime.datetime.today().strftime("%Y-%m-%d")
        )

    def render_to_response(self, context, **response_kwargs):
        if self.is_export:
            return self.as_spreadsheet(
                context["submissions"], self.request.GET.get("export")
            )
        return super().render_to_response(context, **response_kwargs)

    def to_row_dict(self, item):
        """Orders the submission dictionary for spreadsheet writing"""
        row_dict = OrderedDict(
            (field, item.get_data().get(field)) for field in self.list_export
        )
        return row_dict

    def get_index_url(self):
        return reverse(self.index_url_name, args=(self.form_page.id,))

    def get_index_results_url(self):
        return reverse(self.index_results_url_name, args=(self.form_page.id,))

    def get_page_subtitle(self):
        return self.form_page.get_admin_display_title()

    def get_breadcrumbs_items(self):
        return self.breadcrumbs_items + [
            {
                "url": reverse(self.forms_index_url_name),
                "label": gettext("Forms"),
            },
            {
                "url": "",
                "label": self.get_page_title(),
                "sublabel": self.get_page_subtitle(),
            },
        ]

    def get_context_data(self, **kwargs):
        """Return context for view"""
        context = super().get_context_data(**kwargs)
        submissions = context[self.context_object_name]
        data_fields = self.form_page.get_data_fields()
        data_rows = []
        context["submissions"] = submissions
        if not self.is_export:
            # Build data_rows as list of dicts containing model_id and fields
            for submission in submissions:
                form_data = submission.get_data()
                data_row = []
                for name, label in data_fields:
                    val = form_data.get(name)
                    if isinstance(val, list):
                        val = ", ".join(val)
                    data_row.append(val)
                data_rows.append({"model_id": submission.id, "fields": data_row})
            # Build data_headings as list of dicts containing model_id and fields
            ordering_by_field = self.get_validated_ordering()
            orderable_fields = self.orderable_fields
            data_headings = []
            for name, label in data_fields:
                order_label = None
                if name in orderable_fields:
                    order = ordering_by_field.get(name)
                    if order:
                        order_label = order[1]  # 'ascending' or 'descending'
                    else:
                        order_label = "orderable"  # not ordered yet but can be
                data_headings.append(
                    {
                        "name": name,
                        "label": label,
                        "order": order_label,
                    }
                )

            context.update(
                {
                    "form_page": self.form_page,
                    "data_headings": data_headings,
                    "data_rows": data_rows,
                }
            )

        if not self.is_export and not self.results_only:
            context["filter_presets"] = FormSubmissionFilterPreset.objects.filter(
                user=self.request.user, page=self.form_page
            )
            context["filter_preset_initial"] = None
            if not self.filters or self.filters.is_valid():
                date_range = (
                    self.filters.form.cleaned_data.get("date") if self.filters else None
                )
                context["filter_preset_initial"] = {
                    "date_from": date_range.start if date_range else None,
                    "date_to": date_range.stop if date_range else None,
                    "ordering": self.get_ordering(),
                }

        context["next_url"] = f"{self.get_index_url()}?{self.request.GET.urlencode()}"
        return context
