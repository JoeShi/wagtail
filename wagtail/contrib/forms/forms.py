from collections import OrderedDict

import django.forms
from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils.html import conditional_escape
from django.utils.translation import gettext_lazy as _

from wagtail.admin.forms import WagtailAdminPageForm
from wagtail.compat import URLField

from .filter_presets import FormSubmissionFilterPreset


def _split_by_newline_or_comma(value):
    """
    Split the given string either by new lines, or if no new line is present then
    by comma.
    """
    if len(lines := value.strip().splitlines()) > 1:
        return [line.strip().rstrip(",").strip() for line in lines]
    else:
        return list(map(str.strip, value.split(",")))


class BaseForm(django.forms.Form):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("label_suffix", "")

        self.user = kwargs.pop("user", None)
        self.page = kwargs.pop("page", None)

        super().__init__(*args, **kwargs)


class FormBuilder:
    def __init__(self, fields):
        self.fields = fields

    def create_singleline_field(self, field, options):
        # TODO: This is a default value - it may need to be changed
        options["max_length"] = 255
        return django.forms.CharField(**options)

    def create_multiline_field(self, field, options):
        options.setdefault("widget", django.forms.Textarea)
        return django.forms.CharField(**options)

    def create_date_field(self, field, options):
        return django.forms.DateField(**options)

    def create_datetime_field(self, field, options):
        return django.forms.DateTimeField(**options)

    def create_email_field(self, field, options):
        return django.forms.EmailField(**options)

    def create_url_field(self, field, options):
        return URLField(**options)

    def create_number_field(self, field, options):
        return django.forms.DecimalField(**options)

    def create_dropdown_field(self, field, options):
        options["choices"] = self.get_formatted_field_choices(field)
        return django.forms.ChoiceField(**options)

    def create_multiselect_field(self, field, options):
        options["choices"] = self.get_formatted_field_choices(field)
        return django.forms.MultipleChoiceField(**options)

    def create_radio_field(self, field, options):
        options["choices"] = self.get_formatted_field_choices(field)
        options.setdefault("widget", django.forms.RadioSelect)
        return django.forms.ChoiceField(**options)

    def create_checkboxes_field(self, field, options):
        options["choices"] = self.get_formatted_field_choices(field)
        options["initial"] = self.get_formatted_field_initial(field)
        options.setdefault("widget", django.forms.CheckboxSelectMultiple)
        return django.forms.MultipleChoiceField(**options)

    def create_checkbox_field(self, field, options):
        return django.forms.BooleanField(**options)

    def create_hidden_field(self, field, options):
        options.setdefault("widget", django.forms.HiddenInput)
        return django.forms.CharField(**options)

    def get_create_field_function(self, type):
        """
        Takes string of field type and returns a Django Form Field Instance.
        Assumes form field creation functions are in the format:
        'create_fieldtype_field'
        """
        create_field_function = getattr(self, "create_%s_field" % type, None)
        if create_field_function:
            return create_field_function
        else:
            import inspect

            method_list = [
                f[0]
                for f in inspect.getmembers(self.__class__, inspect.isfunction)
                if f[0].startswith("create_") and f[0].endswith("_field")
            ]
            raise AttributeError(
                "Could not find function matching format \
                create_<fieldname>_field for type: "
                + type,
                "Must be one of: " + ", ".join(method_list),
            )

    def get_formatted_field_choices(self, field):
        """
        Returns a list of choices [(string, string),] for the field.
        Split the provided choices into a list, separated by new lines.
        If no new lines in the provided choices, split by commas.
        """
        return [(x, x) for x in _split_by_newline_or_comma(field.choices)]

    def get_formatted_field_initial(self, field):
        """
        Returns a list of initial values [string,] for the field.
        Split the supplied default values into a list, separated by new lines.
        If no new lines in the provided default values, split by commas.
        """
        return _split_by_newline_or_comma(field.default_value)

    @property
    def formfields(self):
        formfields = OrderedDict()

        for field in self.fields:
            options = self.get_field_options(field)
            create_field = self.get_create_field_function(field.field_type)

            # If the field hasn't been saved to the database yet (e.g. we are previewing
            # a FormPage with unsaved changes) it won't have a clean_name as this is
            # set in FormField.save.
            clean_name = field.clean_name or field.get_field_clean_name()
            formfields[clean_name] = create_field(field, options)

        return formfields

    def get_field_options(self, field):
        options = {"label": field.label}
        if getattr(settings, "WAGTAILFORMS_HELP_TEXT_ALLOW_HTML", False):
            options["help_text"] = field.help_text
        else:
            options["help_text"] = conditional_escape(field.help_text)
        options["required"] = field.required
        options["initial"] = field.default_value
        return options

    def get_form_class(self):
        return type("WagtailForm", (BaseForm,), self.formfields)


class SelectDateForm(django.forms.Form):
    date_from = django.forms.DateTimeField(
        required=False,
        widget=django.forms.DateInput(attrs={"placeholder": _("Date from")}),
    )
    date_to = django.forms.DateTimeField(
        required=False,
        widget=django.forms.DateInput(attrs={"placeholder": _("Date to")}),
    )


def validate_ordering_values(values, *, orderable_fields, default_ordering):
    """Validate ordering strings against a submissions list view's allowlist."""
    if not values:
        values = default_ordering
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, (list, tuple)) or not values:
        raise ValidationError(_("Enter a valid ordering."))

    valid_values = {
        value for field in orderable_fields or () for value in (field, f"-{field}")
    }
    ordering = list(values)
    if any(
        not isinstance(value, str) or value not in valid_values for value in ordering
    ):
        raise ValidationError(_("Enter a valid ordering."))
    return ordering


class BaseFormSubmissionFilterPresetForm(django.forms.ModelForm):
    def __init__(
        self,
        *args,
        user,
        page,
        orderable_fields,
        default_ordering,
        ordering_values=None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.user = user
        self.page = page
        self.orderable_fields = orderable_fields
        self.default_ordering = default_ordering
        self.ordering_values = ordering_values
        self.instance.user = user
        self.instance.page = page

    def clean_name(self):
        name = self.cleaned_data["name"]
        presets = self._meta.model.objects.filter(
            user=self.user, page=self.page, name=name
        )
        if self.instance.pk:
            presets = presets.exclude(pk=self.instance.pk)
        if presets.exists():
            raise ValidationError(
                _("A saved filter with this name already exists for this form.")
            )
        return name


class FormSubmissionFilterPresetForm(BaseFormSubmissionFilterPresetForm):
    class Meta:
        model = FormSubmissionFilterPreset
        fields = ["name", "date_from", "date_to"]
        widgets = {
            "date_from": django.forms.DateInput(attrs={"placeholder": _("Date from")}),
            "date_to": django.forms.DateInput(attrs={"placeholder": _("Date to")}),
        }

    def clean(self):
        cleaned_data = super().clean()
        date_from = cleaned_data.get("date_from")
        date_to = cleaned_data.get("date_to")
        if date_from and date_to and date_from > date_to:
            self.add_error(
                "date_to", _("The end date must not be before the start date.")
            )
        try:
            cleaned_data["ordering"] = validate_ordering_values(
                self.ordering_values,
                orderable_fields=self.orderable_fields,
                default_ordering=self.default_ordering,
            )
        except ValidationError as error:
            self.add_error(None, error)
        return cleaned_data


class FormSubmissionFilterPresetRenameForm(BaseFormSubmissionFilterPresetForm):
    class Meta:
        model = FormSubmissionFilterPreset
        fields = ["name"]


class WagtailAdminFormPageForm(WagtailAdminPageForm):
    def clean(self):
        """
        Dynamically detect all related AbstractFormField subclasses to ensure
        validation is applied regardless of the related_name or if there are multiple
        AbstractFormField subclasses related to this page.
        """

        from .models import AbstractFormField

        cleaned_data = super().clean()

        form_fields_related_names = [
            related_object.related_name
            for related_object in self.instance._meta.related_objects
            if issubclass(related_object.related_model, AbstractFormField)
        ]

        for related_name in form_fields_related_names:
            if related_name not in self.formsets:
                continue

            forms = self.formsets[related_name].forms
            for form in forms:
                form.is_valid()

            seen_names = set()
            duplicate_names = set()

            # Find duplicates
            for form in forms:
                if form.cleaned_data.get("DELETE", False):
                    continue

                # Use existing clean_name or generate for new fields,
                # raise an error if there are duplicate resolved clean names.
                # Note: `clean_name` is set in `FormField.save`.
                clean_name = (
                    form.instance.clean_name or form.instance.get_field_clean_name()
                )

                if clean_name in seen_names:
                    duplicate_names.add(clean_name)
                else:
                    seen_names.add(clean_name)

            # Add validation errors to forms with duplicate clean names
            if duplicate_names:
                for form in forms:
                    if form.cleaned_data.get("DELETE", False):
                        continue

                    clean_name = (
                        form.instance.clean_name or form.instance.get_field_clean_name()
                    )

                    if clean_name in duplicate_names:
                        if form.instance.clean_name:
                            form.add_error(
                                "label",
                                django.forms.ValidationError(
                                    _(
                                        "The name '%(duplicate_clean_name)s' is "
                                        "already assigned to this field and cannot be "
                                        "changed. Please enter a different label for "
                                        "the conflicting field."
                                    )
                                    % {"duplicate_clean_name": clean_name}
                                ),
                            )
                        else:
                            form.add_error(
                                "label",
                                django.forms.ValidationError(
                                    _(
                                        "Field name '%(duplicate_clean_name)s' "
                                        "conflicts with another field. Please "
                                        "change the label."
                                    )
                                    % {"duplicate_clean_name": clean_name}
                                ),
                            )

        return cleaned_data
