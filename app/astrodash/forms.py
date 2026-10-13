from django import forms
from django.core.validators import FileExtensionValidator
import json
import ast
from typing import Any, Dict, List, Optional, Tuple

from astrodash.infrastructure.wiserep.selection import normalize_designation
from astrodash.infrastructure.ml.model_registry import (
    REDSHIFT_INPUT_NONE,
    REDSHIFT_INPUT_OPTIONAL,
    REDSHIFT_INPUT_REQUIRED,
    default_definition,
    get_definition,
    listed_definitions,
)

# The message both redshift gates (this form and the batch view's own check)
# raise when a model that requires a redshift is submitted without one. It
# names no model: the requirement comes from the selected model's declared
# policy, so any model can carry it.
REDSHIFT_REQUIRED_MESSAGE = "Redshift is required for the selected model."


def redshift_input_policy(model_id: Optional[str]) -> str:
    """Resolve a model selection to its declared redshift input policy.

    Args:
        model_id: The selected ``model_type``, or ``None`` when no model is
            selected.

    Returns:
        One of :data:`REDSHIFT_INPUT_REQUIRED`, :data:`REDSHIFT_INPUT_OPTIONAL`
        or :data:`REDSHIFT_INPUT_NONE`. A selection the registry cannot resolve
        -- a user-uploaded model, or no selection at all -- falls back to
        :data:`REDSHIFT_INPUT_OPTIONAL`, which is the behavior that path has
        always had.
    """
    definition = get_definition(model_id) if model_id else None
    if definition is None:
        return REDSHIFT_INPUT_OPTIONAL
    return definition.redshift_input


def takes_redshift_input(model_id: Optional[str]) -> bool:
    """Whether a model selection takes a redshift as an input at all.

    Drives whether the redshift field and the Known Redshift checkbox render:
    a model that declines redshift shows neither control.

    Args:
        model_id: The selected ``model_type``, or ``None`` when no model is
            selected.

    Returns:
        True unless the selected model declares :data:`REDSHIFT_INPUT_NONE`.
    """
    return redshift_input_policy(model_id) != REDSHIFT_INPUT_NONE


def _builtin_model_choices():
    """Return (id, title) choices for the listed built-in models, in registry order.

    Listing, not lifecycle status, decides what a choice control offers: an
    unlisted model must appear in no control an ordinary visitor can reach.
    Because a ``ChoiceField`` also validates against its choices, dropping an
    unlisted model here is what makes a hand-crafted POST naming it invalid.

    Returns:
        A list of ``(id, title)`` pairs in registry order.
    """
    return [(d.id, d.title) for d in listed_definitions()]


def _classify_model_choices():
    """Built-in model choices plus the user_uploaded entry used by the classify form."""
    return _builtin_model_choices() + [('user_uploaded', 'User uploaded model')]


def _selection_model_choices():
    """Built-in model choices plus the user_model/upload entries for the selection page."""
    return _builtin_model_choices() + [
        ('user_model', 'Use Uploaded Model'),
        ('upload', 'Upload Your Model'),
    ]


def candidate_records_for_name(object_name: Optional[str]) -> List[Dict[str, Any]]:
    """Read the candidate set already resolved for a submitted designation.

    The durably stored set is read rather than the cache in front of it: the
    cache holds the same records under a lifetime, so reading the stored set
    is what keeps a researcher's own epoch choice from being rejected because
    a cache entry aged out.

    Args:
        object_name: The designation as submitted, in any accepted form, or
            ``None``/empty when no designation is in play.

    Returns:
        The ranked candidate records for that designation, earliest first, or
        an empty list when no set has been resolved for it.
    """
    normalized = normalize_designation(object_name or "")
    if not normalized:
        return []
    from astrodash.services import get_candidate_set_repo

    stored = get_candidate_set_repo().get(normalized)
    records = (stored or {}).get("candidates") or []
    return [record for record in records if record.get("source_spectrum_id")]


def epoch_choice_label(record: Dict[str, Any]) -> str:
    """Describe one eligible spectrum for the epoch control.

    Args:
        record: One candidate record, carrying ``observation_date``,
            ``instrument`` and ``quality``.

    Returns:
        A label reading observation date, instrument and quality flag, with
        the flag named as unstated when WISeREP left it empty -- it is empty
        on most rows.
    """
    quality = (record.get("quality") or "").strip()
    return "{date} - {instrument} - quality {quality}".format(
        date=(record.get("observation_date") or "").strip() or "date unknown",
        instrument=(record.get("instrument") or "").strip() or "instrument unknown",
        quality=quality or "not stated",
    )


class ClassifyForm(forms.Form):
    supernova_name = forms.CharField(
        label="Supernova Name",
        required=False,
        max_length=100,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. SN1998bw'})
    )

    file = forms.FileField(
        label="Upload Spectrum",
        required=False,
        validators=[FileExtensionValidator(allowed_extensions=['txt', 'dat', 'ascii', 'csv', 'lnw', 'fits', 'flm', 'spec'])],
        help_text="Upload a spectrum file (text format, two columns: wavelength and flux)"
    )
    
    # Analysis Options. Built-in choices derive from the model registry; the
    # user_uploaded entry is appended (used when the session has a selected user
    # model; not shown in the dropdown). Choices and default are set in __init__
    # so they always reflect the live registry.
    model = forms.ChoiceField(
        choices=[],
        widget=forms.Select(attrs={'class': 'form-control'})
    )
    
    smoothing = forms.IntegerField(
        initial=0,
        min_value=0,
        max_value=20,
        widget=forms.NumberInput(attrs={'class': 'form-control'})
    )
    
    min_wave = forms.IntegerField(
        label="Min Wavelength",
        initial=3500,
        widget=forms.NumberInput(attrs={'class': 'form-control'})
    )
    
    max_wave = forms.IntegerField(
        label="Max Wavelength",
        initial=10000,
        widget=forms.NumberInput(attrs={'class': 'form-control'})
    )
    
    known_z = forms.BooleanField(
        label="Known Redshift",
        required=False,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'})
    )
    
    redshift = forms.FloatField(
        required=False,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': 'any'})
    )

    # Which epoch of a resolved designation to classify (R8). The choices are
    # the eligible spectra of whichever designation is in play, so they are
    # built in __init__ and re-derived on every submission -- a ChoiceField
    # validates against its choices, and choices built only for the unbound
    # render would reject the researcher's own selection.
    source_spectrum_id = forms.ChoiceField(
        label="Spectrum epoch",
        required=False,
        choices=[],
        widget=forms.Select(attrs={"class": "form-control"}),
    )

    def __init__(self, *args, effective_model=None, object_name=None, **kwargs):
        """Build the form, optionally bound to the model that will actually run.

        Args:
            *args: Standard form positional arguments (data, files).
            effective_model: The model the view will run -- the scoped model in
                a scoped session, otherwise the session's selection. It becomes
                the field's initial value, it is added to the choices when the
                registry knows it (a gated model is unlisted, so it is in no
                listed choice set, and a scoped submission would otherwise fail
                validation before the view consulted the scope), and it decides
                the redshift policy this form validates against.
            object_name: The designation whose eligible spectra the epoch
                control offers, when one is in play -- the submitted name on a
                POST, the last classified name on a re-render. The choices are
                derived from the candidate set already resolved for it.
            **kwargs: Standard form keyword arguments.
        """
        super().__init__(*args, **kwargs)
        self.effective_model = effective_model
        choices = _classify_model_choices()
        definition = get_definition(effective_model) if effective_model else None
        if definition is not None and definition.id not in [c[0] for c in choices]:
            choices.append((definition.id, definition.title))
        self.fields['model'].choices = choices
        self.fields['model'].initial = effective_model or default_definition().id

        # Whether the required-redshift gate was left for the view to apply
        # after resolution; set in clean().
        self.redshift_required_deferred = False

        # Whether this submission carried the redshift controls at all. Read
        # from the raw submission rather than a cleaned value, because what is
        # being distinguished is a field that came back empty from a page that
        # showed it (a value the researcher removed) from a field that was
        # never submitted (no statement about the redshift either way). The
        # two clean to the same ``None``.
        self.redshift_controls_submitted = self.is_bound and "redshift" in self.data
        # Set in clean(), and only meaningful when the controls were submitted.
        self.redshift_cleared = False
        self.known_redshift_unchecked = False
        self.object_name = object_name
        self.epoch_records = candidate_records_for_name(object_name)
        epoch_choices = [
            (str(record["source_spectrum_id"]), epoch_choice_label(record))
            for record in self.epoch_records
        ]
        if not epoch_choices and self.is_bound:
            # No set was derived for this designation -- a first lookup, or a
            # name whose resolution refused -- so there is nothing to judge a
            # submitted choice against. Accepting it leaves the seam to fall
            # back to the automatic selection; rejecting it would refuse a
            # lookup for a reason the researcher cannot see.
            submitted = (self.data.get("source_spectrum_id") or "").strip()
            if submitted:
                epoch_choices = [(submitted, submitted)]
        self.fields["source_spectrum_id"].choices = epoch_choices

    def clean(self):
        cleaned_data = super().clean()
        file = cleaned_data.get('file')
        supernova_name = cleaned_data.get('supernova_name')
        known_z = cleaned_data.get('known_z')
        redshift = cleaned_data.get('redshift')
        # The redshift policy follows the model that will actually run, not the
        # submitted field: inside a scoped flow the field is not authoritative.
        model = self.effective_model or cleaned_data.get('model')

        if not file and not supernova_name:
            raise forms.ValidationError("Please provide either a spectrum file or a Supernova Name.")

        # An empty redshift box that was on the page is a value the researcher
        # removed; a box that was never submitted says nothing. A checkbox is
        # only present when it is checked, so its absence reads as "unchecked"
        # only when the submission carried the controls at all. Zero is a real
        # redshift, so neither reads off the value's truthiness.
        if self.redshift_controls_submitted:
            self.redshift_cleared = redshift is None
            self.known_redshift_unchecked = not known_z

        if known_z and redshift is None:
            self.add_error('redshift', "Redshift is required when 'Known Redshift' is checked.")

        # Require a redshift only when the selected model's declared policy
        # says it is a required input; an optional-input or unresolvable
        # selection (user-uploaded models use 0.0 if missing) passes without one.
        #
        # A name-only submission is the exception: WISeREP carries the object's
        # redshift and populating it is what makes a name a complete
        # classification in one step (R9). The field is therefore judged
        # against the resolved value rather than the empty submitted one, and
        # the view applies the same gate once resolution has run (R11).
        if redshift_input_policy(model) == REDSHIFT_INPUT_REQUIRED and redshift is None:
            if file or not supernova_name:
                self.add_error("redshift", REDSHIFT_REQUIRED_MESSAGE)
            else:
                self.redshift_required_deferred = True

        return cleaned_data


class ModelSelectionForm(forms.Form):
    """
    Form for model selection page - allows choosing between dash/transformer or uploading a custom model.
    """
    # Built-in choices derive from the model registry; the user_model and upload
    # entries are appended. Set in __init__ to reflect the live registry.
    model_type = forms.ChoiceField(
        choices=[],
        widget=forms.HiddenInput(),  # We'll handle selection via JavaScript/cards
        required=False
    )

    existing_model_id = forms.ChoiceField(
        label="Select an uploaded model",
        required=False,
        choices=[],
        widget=forms.Select(attrs={'class': 'form-control'})
    )
    
    # Fields for model upload
    model_file = forms.FileField(
        label="Model File",
        required=False,
        validators=[FileExtensionValidator(allowed_extensions=['pth', 'pt'])],
        help_text="Upload a PyTorch .pth/.pt file"
    )
    
    model_name = forms.CharField(
        label="Model Name",
        required=False,
        max_length=200,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Enter model name'})
    )
    
    model_description = forms.CharField(
        label="Description",
        required=False,
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 3, 'placeholder': 'Enter model description'})
    )
    
    class_mapping = forms.CharField(
        label="Class Mapping (JSON)",
        required=False,
        help_text='Map class names to indices. Use double quotes: {"Ia": 0, "IIn": 1, "II": 3}. Python style with single quotes is also accepted.',
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 4, 'placeholder': '{"Ia": 0, "IIn": 1, "SLSNe-I": 2, "II": 3, "Ib/c": 4}'})
    )
    
    input_shape = forms.CharField(
        label="Input Shape (JSON)",
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': '[1, 1, 1000]'})
    )
    
    # Hidden field to track which action (classify or batch)
    action_type = forms.CharField(
        widget=forms.HiddenInput(),
        required=False
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['model_type'].choices = _selection_model_choices()

    def clean(self):
        cleaned_data = super().clean()
        model_type = cleaned_data.get('model_type')
        
        if model_type == 'user_model':
            existing_model_id = cleaned_data.get('existing_model_id')
            if not existing_model_id:
                self.add_error('existing_model_id', 'Please select an uploaded model.')

        if model_type == 'upload':
            model_file = cleaned_data.get('model_file')
            class_mapping = cleaned_data.get('class_mapping')
            input_shape = cleaned_data.get('input_shape')
            model_name = cleaned_data.get('model_name')
            
            if not model_file:
                self.add_error('model_file', 'Model file is required when uploading a custom model.')
            
            if not class_mapping:
                self.add_error('class_mapping', 'Class mapping is required when uploading a custom model.')
            else:
                # Accept JSON or Python dict literal (e.g. {'Ia': 0} vs {"Ia": 0})
                try:
                    json.loads(class_mapping)
                except json.JSONDecodeError:
                    try:
                        parsed = ast.literal_eval(class_mapping.strip())
                        if isinstance(parsed, dict) and all(isinstance(v, int) for v in parsed.values()):
                            # Store as JSON string so downstream gets valid JSON
                            cleaned_data['class_mapping'] = json.dumps(parsed)
                        else:
                            self.add_error(
                                'class_mapping',
                                'Class mapping must be a JSON object with string keys and integer values. '
                                'Use double quotes for keys, e.g. {"Ia": 0, "IIn": 1, "II": 3}.'
                            )
                    except (ValueError, SyntaxError):
                        self.add_error(
                            'class_mapping',
                            'Class mapping must be valid JSON. Use double quotes for keys and strings, '
                            'e.g. {"Ia": 0, "IIn": 1, "SLSNe-I": 2, "II": 3, "Ib/c": 4}.'
                        )
            
            if not input_shape:
                self.add_error('input_shape', 'Input shape is required when uploading a custom model.')
            else:
                # Accept JSON array or Python list literal
                try:
                    json.loads(input_shape)
                except json.JSONDecodeError:
                    try:
                        parsed = ast.literal_eval(input_shape.strip())
                        if isinstance(parsed, list) and all(isinstance(x, int) for x in parsed):
                            cleaned_data['input_shape'] = json.dumps(parsed)
                        else:
                            self.add_error(
                                'input_shape',
                                'Input shape must be a JSON array of integers, e.g. [1, 1, 1000].'
                            )
                    except (ValueError, SyntaxError):
                        self.add_error(
                            'input_shape',
                            'Input shape must be valid JSON, e.g. [1, 1, 1000].'
                        )
            
            if not model_name:
                self.add_error('model_name', 'Model name is required when uploading a custom model.')
        
        return cleaned_data


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True

class MultipleFileField(forms.FileField):
    """
    Django's built-in FileField expects a single UploadedFile instance.
    When using a widget with allow_multiple_selected=True, Django provides a
    list of UploadedFile objects, which triggers FileField's "invalid" error:
    "No file was submitted. Check the encoding type on the form."
    """

    def clean(self, data, initial=None):
        # When no files are provided, normalize to empty list for required=False.
        if not data:
            return [] if not self.required else super().clean(data, initial)

        # Widget returns a list/tuple when multiple files are selected.
        if isinstance(data, (list, tuple)):
            cleaned_files = []
            errors = []
            for item in data:
                try:
                    cleaned_files.append(super().clean(item, initial))
                except forms.ValidationError as e:
                    errors.extend(e.error_list)
            if errors:
                raise forms.ValidationError(errors)
            return cleaned_files

        # If a single file slips through, still return a list for consistency.
        return [super().clean(data, initial)]


BATCH_REDSHIFT_HELP = (
    "One redshift for the whole batch (0.05), or one per file keyed by "
    "filename (sn_a.dat=0.01, sn_b.dat=0.02)."
)

BATCH_REDSHIFT_FORMAT_ERROR = (
    "Enter a single redshift for the whole batch, e.g. 0.05, or one per file "
    "keyed by filename, e.g. sn_a.dat=0.01, sn_b.dat=0.02."
)


def parse_batch_redshifts(value) -> Tuple[Optional[float], Dict[str, float]]:
    """Parse the batch redshift field into a broadcast value and a per-file map.

    Returns ``(broadcast, by_filename)``. Exactly one side is populated:

    - ``""``/``None`` -> ``(None, {})``, no redshift supplied.
    - ``0.05`` -> ``(0.05, {})``, applied to every spectrum in the batch.
    - ``sn_a.dat=0.01, sn_b.dat=0.02`` -> ``(None, {...})``, keyed by filename.
      A JSON object spelling is accepted too.

    Keying by filename rather than by position is deliberate. Position is
    ambiguous: a zip yields its files in ``ZipFile.namelist()`` order, which is
    the order they were written into the archive, not the alphabetical order a
    file manager shows, and the sequence also skips directories, unsupported
    extensions and unreadable members. Matching a list against that order
    silently attaches a redshift to the wrong spectrum, which produces a
    confidently wrong classification rather than an error.

    Raises:
        ValueError: If the text is neither a single number nor a filename map.
    """
    if value is None:
        return None, {}
    text = str(value).strip()
    if text == "":
        return None, {}

    try:
        return float(text), {}
    except ValueError:
        pass

    if text.startswith("{"):
        try:
            parsed = ast.literal_eval(text)
        except (ValueError, SyntaxError, TypeError) as exc:
            raise ValueError(BATCH_REDSHIFT_FORMAT_ERROR) from exc
        if not isinstance(parsed, dict):
            raise ValueError(BATCH_REDSHIFT_FORMAT_ERROR)
        pairs = parsed.items()
    else:
        pairs = []
        for chunk in text.split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            name, sep, raw = chunk.partition("=")
            if not sep:
                raise ValueError(BATCH_REDSHIFT_FORMAT_ERROR)
            pairs.append((name, raw))

    by_filename: Dict[str, float] = {}
    for name, raw in pairs:
        key = str(name).strip()
        if not key:
            raise ValueError(BATCH_REDSHIFT_FORMAT_ERROR)
        try:
            by_filename[key] = float(str(raw).strip())
        except (TypeError, ValueError) as exc:
            raise ValueError(BATCH_REDSHIFT_FORMAT_ERROR) from exc

    if not by_filename:
        raise ValueError(BATCH_REDSHIFT_FORMAT_ERROR)
    return None, by_filename


class BatchForm(forms.Form):
    # Support for both zip and multiple files
    zip_file = forms.FileField(
        label="Upload Zip File",
        required=False,
        validators=[FileExtensionValidator(allowed_extensions=['zip'])],
        help_text="Upload a ZIP file containing spectrum files."
    )
    
    files = MultipleFileField(
        label="Upload Multiple Files",
        required=False,
        validators=[FileExtensionValidator(allowed_extensions=['txt', 'dat', 'ascii', 'csv', 'lnw', 'fits', 'flm', 'spec'])],
        widget=MultipleFileInput(attrs={'multiple': True}),
        help_text="Select multiple spectrum files to upload."
    )

    # Analysis Options (model is taken from session on model selection page)
    smoothing = forms.IntegerField(
        initial=0,
        min_value=0,
        max_value=20,
        widget=forms.NumberInput(attrs={'class': 'form-control'})
    )
    
    min_wave = forms.IntegerField(
        label="Min Wavelength",
        initial=3500,
        widget=forms.NumberInput(attrs={'class': 'form-control'})
    )
    
    max_wave = forms.IntegerField(
        label="Max Wavelength",
        initial=10000,
        widget=forms.NumberInput(attrs={'class': 'form-control'})
    )
    
    known_z = forms.BooleanField(
        label="Known Redshift",
        required=False,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'})
    )
    
    redshift = forms.CharField(
        required=False,
        widget=forms.TextInput(
            attrs={'class': 'form-control', 'placeholder': '0.05 or sn_a.dat=0.01, sn_b.dat=0.02'}
        ),
        help_text=BATCH_REDSHIFT_HELP,
    )

    calculate_rlap = forms.BooleanField(
        label="Calculate RLAP",
        required=False,
        initial=False,
        help_text="Only available for Dash model",
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'})
    )

    def clean_redshift(self):
        """Store the parsed pair; ``clean`` and the view read both halves."""
        raw = self.cleaned_data.get('redshift')
        try:
            broadcast, by_filename = parse_batch_redshifts(raw)
        except (ValueError, SyntaxError, TypeError) as exc:
            raise forms.ValidationError(str(exc) or BATCH_REDSHIFT_FORMAT_ERROR)
        self.redshift_broadcast = broadcast
        self.redshift_by_filename = by_filename
        return raw

    def clean(self):
        cleaned_data = super().clean()
        zip_file = cleaned_data.get('zip_file')
        files = self.files.getlist('files') if hasattr(self, 'files') else []
        
        # Note: In Django forms, file field cleaning for multiple files is tricky 
        # because cleaned_data['files'] might only contain the last file if not handled specifically.
        # We'll handle the 'files' check in the view or assume valid if provided in request.FILES
        
        if not zip_file and not files:
             # This validation might need to be relaxed here and strictly checked in view 
             # or we need to ensure we can access request.FILES len
             pass 

        known_z = cleaned_data.get('known_z')
        has_redshift = (
            getattr(self, 'redshift_broadcast', None) is not None
            or bool(getattr(self, 'redshift_by_filename', None))
        )

        if known_z and not has_redshift:
            self.add_error('redshift', "Redshift is required when 'Known Redshift' is checked.")

        return cleaned_data
