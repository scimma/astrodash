from django.shortcuts import render
from django.contrib import messages
from django.http import (
    HttpResponseRedirect,
    FileResponse,
    Http404,
    JsonResponse,
)
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.http import require_POST
from django.urls import reverse
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from astrodash.forms import (
    ClassifyForm,
    BatchForm,
    ModelSelectionForm,
    REDSHIFT_REQUIRED_MESSAGE,
    redshift_input_policy,
    takes_redshift_input,
)
from astrodash.infrastructure.ml.model_registry import (
    REDSHIFT_INPUT_REQUIRED,
    SURFACE_DASH_TWINS,
    get_definition,
    listed_definitions,
)
from astrodash.core.model_access import (
    EntryLinkRefused,
    GateNotConfigured,
    begin_scope,
    clear_selection,
    credential_matches,
    end_scope,
    live_scope_model_id,
    model_access_required,
    redeem_entry_link,
    render_refusal,
    revalidate_session_model,
    scoped_flow_refusal,
)
from astrodash.surfaces import declared_surfaces
from astrodash.services import (
    get_config,
    get_spectrum_processing_service,
    get_classification_service,
    get_spectrum_service,
    get_model_service,
    get_batch_processing_service,
    get_line_list_service,
    get_template_analysis_service,
    get_twins_search_service,
)
from astrodash.core.exceptions import AppException
from astrodash.config.logging import get_logger
from asgiref.sync import async_to_sync
from bokeh.embed import components
from bokeh.plotting import figure
from bokeh.models import ColumnDataSource, HoverTool, Span, Label
import json
import base64
import hashlib
from django.core.files.uploadedfile import SimpleUploadedFile

logger = get_logger(__name__)

# Directory containing logo, favicon, etc. (app/astrodash/static/images/)
APP_STATIC_IMAGES_DIR = Path(__file__).resolve().parent / "static" / "images"

# File containing team member information.
TEAM_MEMBERS_JSON = Path(__file__).resolve().parent / "data" / "team_members.json"

# Safe MIME types for known extensions in that directory
APP_STATIC_MIME = {
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
}


def serve_app_static_image(request, path):
    """
    Serve static image files from app/astrodash/static/images/.
    Used for logo and favicon so they work without collectstatic/nginx volume.
    """
    if ".." in path or "/" in path or "\\" in path:
        raise Http404("Invalid path")
    file_path = (APP_STATIC_IMAGES_DIR / path).resolve()
    try:
        if not file_path.is_file():
            raise Http404("Not found")
        # Ensure we don't escape the app static images dir
        if not str(file_path).startswith(str(APP_STATIC_IMAGES_DIR.resolve())):
            raise Http404("Invalid path")
    except (OSError, ValueError):
        raise Http404("Not found")
    content_type = APP_STATIC_MIME.get(file_path.suffix.lower(), "application/octet-stream")
    return FileResponse(file_path.open("rb"), content_type=content_type)


def landing_page(request):
    """
    Renders the Astrodash landing page.
    """
    return render(request, 'astrodash/index.html')


def _team_affiliations_from_json():
    """Build affiliation/member objects for the template from data/team_members.json."""
    raw = json.loads(TEAM_MEMBERS_JSON.read_text(encoding="utf-8"))
    rows = raw.get("affiliations") or []
    rows = sorted(rows, key=lambda a: (a.get("order", 0), a.get("name") or ""))
    out = []
    for aff in rows:
        name = aff.get("name") or ""
        aff_ns = SimpleNamespace(name=name)
        members_in = sorted(
            aff.get("members") or [],
            key=lambda m: (m.get("order", 0), m.get("name") or ""),
        )
        aff_ns.members = [
            SimpleNamespace(
                name=m.get("name") or "",
                description=(m.get("description") or "").strip(),
                image=(m.get("image") or "").strip(),
                affiliation=aff_ns,
            )
            for m in members_in
        ]
        out.append(aff_ns)
    return out


def team_members(request):
    """
    Renders the Team Members page: affiliations (labs/universities) with
    members (picture, name, description).

    If ``data/team_members.json`` exists, it is the source of truth (good for
    prod when admin is unavailable). Otherwise rows come from the database.
    """
    if TEAM_MEMBERS_JSON.is_file():
        affiliations = _team_affiliations_from_json()
    return render(
        request,
        "astrodash/team_members.html",
        {"affiliations": affiliations},
    )


def leaderboard(request):
    """
    Renders the Model Leaderboard page (UI sketch with mock rankings).

    Models are ranked on a monthly blind set of newly uploaded spectra.
    Backend evaluation is not wired yet — this view serves placeholder data
    so the layout can be reviewed.
    """
    challenge = {
        "month_label": "July 2026",
        "status": "Finalized",
        "spectra_count": 142,
        "eval_window": "Jul 1 – Jul 31, 2026",
        "next_challenge": "August 2026",
        "blind_set_note": "Models scored on spectra newly uploaded to WISeREP during the eval window.",
    }
    available_months = [
        "July 2026",
        "June 2026",
        "May 2026",
        "April 2026",
    ]
    # Mock standings — mix of built-in models and placeholder community entries.
    rankings = [
        {
            "rank": 1,
            "model": "Dash Model",
            "color": "#28a745",
            "micro_f1": 0.912,
            "roc": 0.964,
            "accuracy": 91.2,
            "delta": 1.4,
        },
        {
            "rank": 2,
            "model": "Transformer Model",
            "color": "#ff8c00",
            "micro_f1": 0.887,
            "roc": 0.951,
            "accuracy": 88.7,
            "delta": -0.6,
        },
        {
            "rank": 3,
            "model": "SNNet-v2 (community)",
            "color": "#6f42c1",
            "micro_f1": 0.863,
            "roc": 0.938,
            "accuracy": 86.3,
            "delta": 2.1,
        },
        {
            "rank": 4,
            "model": "SpecFormer-lite",
            "color": "#17a2b8",
            "micro_f1": 0.839,
            "roc": 0.921,
            "accuracy": 83.9,
            "delta": 0.3,
        },
        {
            "rank": 5,
            "model": "CNN-baseline-2024",
            "color": "#6c757d",
            "micro_f1": 0.794,
            "roc": 0.887,
            "accuracy": 79.4,
            "delta": -1.8,
        },
    ]
    return render(
        request,
        "astrodash/leaderboard.html",
        {
            "challenge": challenge,
            "available_months": available_months,
            "selected_month": challenge["month_label"],
            "rankings": rankings,
            "is_mock": True,
        },
    )


@xframe_options_sameorigin
@model_access_required("dash_twins")
def dash_twins(request):
    """
    Renders the DASH Twins Explorer (embedding visualization).
    UI is in astrodash/explorer/dash_twins.html; data is loaded via dash_twins_data.

    Authorized from the *selected* model per KTD5: the page shows a
    model-agnostic global payload and reads no classification artifact, so it
    stays browsable before any classification has run, exactly as today.
    """
    return render(request, "astrodash/explorer/dash_twins.html")


@model_access_required("dash_twins_data", as_json=True)
def dash_twins_data(request):
    """
    Serves the DASH Twins payload JSON from {data_dir}/explorer (same as models, templates).
    Generate with extract_payload.py --build-artifacts (optionally --out-dir to data_dir/explorer).

    Authorized from the *selected* model, for the same reason as the page it
    feeds: the payload is global and carries nothing from a classification.
    """
    path = Path(get_config().data_dir) / "explorer" / "dash_twins_payload.json"
    if not path.is_file():
        raise Http404("DASH Twins data not found. Run extract_payload.py --build-artifacts "
                      "and ensure files are in ASTRODASH_DATA_DIR/explorer/.")
    return FileResponse(
        open(path, "rb"),
        content_type="application/json",
        as_attachment=False,
    )


@model_access_required("twins_search", from_classified=True, as_json=True)
def twins_search(request):
    """
    POST or GET: run twins search using the DASH embedding stored in session
    (set after classifying a spectrum with DASH). Returns JSON with query_umap,
    query_pca, twin_indices, twin_similarities, and optionally user_spectrum.

    Authorized from the *classified* model per KTD5: this is the one twins
    route that consumes a classification artifact (the stashed embedding), so
    the model that produced that artifact decides whether it may be searched.
    """
    import numpy as np
    embedding = request.session.get('classify_dash_embedding')
    if not embedding or not isinstance(embedding, list) or len(embedding) != 1024:
        return JsonResponse(
            {'error': 'No DASH embedding in session. Classify a spectrum with DASH first.'},
            status=400,
        )
    try:
        k = 20
        if request.method == 'POST' and request.POST.get('k'):
            k = int(request.POST.get('k'))
        elif request.method == 'GET' and request.GET.get('k'):
            k = int(request.GET.get('k'))
        k = max(1, min(50, k))
        svc = get_twins_search_service()
        result = svc.find_twins(np.array(embedding, dtype=np.float32), k=k)
        data = dict(result)
        if request.session.get('classify_processed'):
            stored = request.session['classify_processed']
            data['user_spectrum'] = {
                'wave': stored.get('x', []),
                'flux': stored.get('y', []),
            }
        return JsonResponse(data)
    except Exception as e:
        logger.exception("twins_search failed")
        return JsonResponse({'error': str(e)}, status=500)


# Shown when a valid, unexpired link is presented with the wrong credential.
# Generic by design: it confirms nothing about the link, the model, or how close
# the attempt was.
GATE_CREDENTIAL_ERROR = "That access code was not accepted. Please try again."


def model_gate(request, token):
    """Redeem a model-scoped entry link and prompt for the shared credential.

    The only way into a gated model. A GET renders the prompt; a POST checks the
    submitted credential and, when it matches, establishes a session scoped to
    the link's model. The link's deadline is re-checked on both, so a link that
    lapses between the prompt and the submit is refused rather than honored.

    Every refusal -- expired, tampered, malformed -- renders the same page with
    the same wording, so the response discloses nothing about which models
    exist. A link whose model has since been published redirects into the normal
    public flow instead of refusing, so a reviewer is never locked out of a
    model that is now public.

    Args:
        request: The current request.
        token: The signed entry-link token from the path.

    Returns:
        HttpResponse: The credential prompt, a redirect into the classification
        flow, or the refusal page.
    """
    try:
        link = redeem_entry_link(token)
    except EntryLinkRefused:
        return render_refusal(request)
    except GateNotConfigured:
        # The route exists in every deployment, gated model or not, so a visitor
        # can reach it while the gate is unconfigured -- which is the shipped
        # default, since the roster carries no gated model and the startup check
        # therefore never runs. Fail closed onto the refusal page rather than a
        # framework error page: this may be the first thing a reviewer sees.
        logger.error("Model gate: link presented while the gate is unconfigured")
        return render_refusal(request)

    definition = get_definition(link.model_id)
    if definition is None or not definition.requires_credential:
        # The model has been published (or left the registry) since the link was
        # minted: there is nothing to gate, so the link becomes an ordinary way
        # in rather than a refusal.
        end_scope(request.session)
        if definition is None:
            return HttpResponseRedirect(
                reverse("astrodash:model_selection") + "?action=classify"
            )
        request.session["selected_model_type"] = link.model_id
        return HttpResponseRedirect(reverse("astrodash:classify"))

    context = {}
    if request.method == "POST":
        try:
            accepted = credential_matches(request.POST.get("credential"))
        except GateNotConfigured:
            logger.error("Model gate: credential submitted while the gate is unconfigured")
            return render_refusal(request)
        if accepted:
            begin_scope(request.session, link)
            logger.info("Model gate: scope established for model_type=%s", link.model_id)
            return HttpResponseRedirect(reverse("astrodash:classify"))
        # A wrong credential on a still-valid link is retryable: redisplay the
        # prompt with a generic error rather than burning the link.
        logger.warning("Model gate: credential rejected")
        context["credential_error"] = GATE_CREDENTIAL_ERROR

    return render(request, "astrodash/model_gate.html", context)


@require_POST
def end_model_scope(request):
    """End a model-scoped session explicitly, discarding everything it produced.

    A POST, because it changes server state; idempotent, so an unscoped visitor
    (or a second click) simply lands back on the public picker. Navigating away
    is deliberately not treated as an implicit end -- only this is.

    Args:
        request: The current request.

    Returns:
        HttpResponse: A redirect to the public model-selection page.
    """
    was_scoped = live_scope_model_id(request.session) is not None
    end_scope(request.session)
    if was_scoped:
        messages.success(request, "Session ended. Choose a model to continue.")
    return HttpResponseRedirect(
        reverse('astrodash:model_selection') + '?action=classify'
    )


def model_selection(request):
    """
    Handles model selection page - allows choosing between dash/transformer or uploading a custom model.
    """
    # A scoped session picks nothing: it is locked to one model, and its way out
    # is the explicit end action, not this page. Refused before the POST handler
    # so a hand-crafted selection cannot move a scope onto another model.
    refusal = scoped_flow_refusal(request)
    if refusal is not None:
        return refusal

    action_type = request.GET.get('action', 'classify')  # 'classify' or 'batch'
    # Listed model definitions drive the selection cards (title, description,
    # color, feature tags, icon, recommended badge, and order). An unlisted
    # model renders no card here, for the classify action and the batch action
    # alike; the selection form's choices are drawn from the same listing, so
    # a POST naming an unlisted model is refused rather than silently accepted.
    model_definitions = listed_definitions()
    form = ModelSelectionForm(request.POST or None, request.FILES or None)

    # Populate existing user model options (must be done before is_valid() on POST too)
    try:
        model_service = get_model_service()
        existing_models = async_to_sync(model_service.list_models)()
    except Exception:
        logger.exception("Failed to list existing user models")
        existing_models = []

    existing_model_choices = [("", "— Select a model —")]
    for m in existing_models:
        display_name = (m.name or "").strip() or m.id
        owner = (m.owner or "").strip()
        label = f"{display_name} ({owner})" if owner else display_name
        existing_model_choices.append((m.id, label))

    form.fields["existing_model_id"].choices = existing_model_choices

    show_upload_section = False
    if request.method == 'POST':
        logger.info(
            "Model selection POST: FILES keys=%s, POST model_type=%s",
            list(request.FILES.keys()) if request.FILES else [],
            request.POST.get("model_type"),
        )
        form = ModelSelectionForm(request.POST, request.FILES)
        form.fields["existing_model_id"].choices = existing_model_choices
        if form.is_valid():
            model_type = form.cleaned_data.get('model_type')
            action_type = form.cleaned_data.get('action_type') or action_type
            logger.info("Model selection form valid: model_type=%s, action_type=%s", model_type, action_type)

            # Handle model upload
            if model_type == 'upload':
                model_file = request.FILES.get('model_file')
                if not model_file:
                    logger.warning("Model upload attempted but no 'model_file' in request.FILES")
                    messages.error(request, "No file was received. Ensure the form uses enctype='multipart/form-data' "
                                   "and you selected a file.")
                    return render(
                        request,
                        'astrodash/model_selection.html',
                        {
                            'form': form,
                            'model_definitions': model_definitions,
                            'action_type': action_type,
                            'existing_models_count': len(existing_models),
                            'show_upload_section': True,
                        },
                    )
                class_mapping = form.cleaned_data.get('class_mapping')
                input_shape = form.cleaned_data.get('input_shape')
                model_name = form.cleaned_data.get('model_name')
                model_description = form.cleaned_data.get('model_description')

                try:
                    model_service = get_model_service()
                    model_content = model_file.read()
                    logger.info(
                        "Model upload: filename=%s, size=%s bytes, saving to storage then DB",
                        model_file.name,
                        len(model_content),
                    )
                    user_model, model_info = async_to_sync(model_service.upload_model)(
                        model_content=model_content,
                        filename=model_file.name,
                        class_mapping_str=class_mapping,
                        input_shape_str=input_shape,
                        name=model_name,
                        description=model_description,
                        owner=request.user.username if request.user.is_authenticated else None,
                    )
                    logger.info("Model upload succeeded: model_id=%s, name=%s", user_model.id, user_model.name)

                    messages.success(request, f"Model '{model_name}' saved successfully.")
                    if model_info.get("validation_passed") is False:
                        messages.warning(
                            request,
                            "Forward-pass validation failed (dummy run had a shape mismatch). You can still use this "
                            "model for classification—real inputs may work."
                        )

                    # After upload, refresh the list of existing models and stay on this page
                    try:
                        existing_models = async_to_sync(model_service.list_models)()
                    except Exception:
                        logger.exception("Failed to refresh existing user models after upload")
                        existing_models = []

                    # Ensure the model we just saved appears in the list (avoids transaction/visibility quirks)
                    existing_ids = [str(m.id) for m in existing_models]
                    if str(user_model.id) not in existing_ids:
                        logger.info("Newly uploaded model %s not in list_models() yet; prepending", user_model.id)
                        existing_models = [user_model] + list(existing_models)

                    logger.info("Model selection after upload: %d models in list", len(existing_models))

                    existing_model_choices = [("", "— Select a model —")]
                    for m in existing_models:
                        display_name = (m.name or "").strip() or m.id
                        owner = (m.owner or "").strip()
                        label = f"{display_name} ({owner})" if owner else display_name
                        existing_model_choices.append((str(m.id), label))

                    form = ModelSelectionForm()
                    form.fields["existing_model_id"].choices = existing_model_choices
                    form.fields["action_type"].initial = action_type

                    return render(
                        request,
                        'astrodash/model_selection.html',
                        {
                            'form': form,
                            'model_definitions': model_definitions,
                            'action_type': action_type,
                            'existing_models_count': len(existing_models),
                            'show_upload_section': False,
                        },
                    )

                except AppException as e:
                    logger.warning("Model upload AppException: %s", e.message)
                    messages.error(request, f"Model upload error: {e.message}")
                    return render(
                        request,
                        'astrodash/model_selection.html',
                        {
                            'form': form,
                            'model_definitions': model_definitions,
                            'action_type': action_type,
                            'existing_models_count': len(existing_models),
                            'show_upload_section': True,
                        },
                    )
                except Exception as e:
                    logger.exception("Model upload failed")
                    messages.error(request, f"An unexpected error occurred during model upload: {str(e)}")
                    return render(
                        request,
                        'astrodash/model_selection.html',
                        {
                            'form': form,
                            'model_definitions': model_definitions,
                            'action_type': action_type,
                            'existing_models_count': len(existing_models),
                            'show_upload_section': True,
                        },
                    )
            elif model_type == "user_model":
                # Use an existing uploaded model
                selected_id = (form.cleaned_data.get("existing_model_id") or "").strip()
                if not selected_id:
                    messages.error(request, "Please select an uploaded model from the list.")
                    return render(
                        request,
                        'astrodash/model_selection.html',
                        {
                            'form': form,
                            'model_definitions': model_definitions,
                            'action_type': action_type,
                            'existing_models_count': len(existing_models),
                            'show_upload_section': True,
                        },
                    )
                request.session['selected_model_id'] = selected_id
                request.session['selected_model_type'] = 'user_uploaded'
                logger.info("Session set: selected_model_type=user_uploaded, selected_model_id=%s", selected_id)
            else:
                # Store selected model type in session
                request.session['selected_model_type'] = model_type
                request.session.pop('selected_model_id', None)  # Clear any previous user model

            # Redirect to the appropriate page
            if action_type == 'batch':
                return HttpResponseRedirect(reverse('astrodash:batch_process_ui'))
            else:
                return HttpResponseRedirect(reverse('astrodash:classify'))
        else:
            # Form invalid: log errors and show upload section if they were trying to upload
            logger.warning("Model selection form invalid: errors=%s", form.errors.as_json() if form.errors else None)
            upload_fields = {'model_file', 'model_name', 'class_mapping', 'input_shape', 'existing_model_id'}
            has_upload_error = any(f in form.errors for f in upload_fields)
            show_upload_section = (request.POST.get('model_type') == 'upload') or has_upload_error

    # Pre-populate action_type in form
    form.fields['action_type'].initial = action_type
    context = {
        'form': form,
        'model_definitions': model_definitions,
        'action_type': action_type,
        'existing_models_count': len(existing_models),
        'show_upload_section': show_upload_section,
    }
    return render(request, 'astrodash/model_selection.html', context)


def _build_classify_form(effective_model, data=None, files=None, initial=None):
    """Build the classification form bound to the model that will actually run.

    The effective model drives both the form's choices and its redshift policy
    (KTD10). Without it the two disagree in a scoped session: a gated model is
    unlisted, so its id is in no listed choice set, and the submission would
    fail validation before the view ever consulted the scope.

    Args:
        effective_model: The model that will run -- the scoped model when a
            scope is live, otherwise the session's selection.
        data: Bound POST data, or ``None`` for an unbound form.
        files: Bound files, or ``None``.
        initial: Initial field values, or ``None``.

    Returns:
        ClassifyForm: The form, with the user-uploaded entry hidden from the
        dropdown unless it is what was selected (it travels as a hidden input).
    """
    form = ClassifyForm(data, files, initial=initial, effective_model=effective_model)
    if effective_model != 'user_uploaded':
        form.fields['model'].choices = [
            c for c in form.fields['model'].choices if c[0] != 'user_uploaded'
        ]
    return form


@model_access_required()
def classify(request):
    """
    Handles spectrum classification via the UI.
    """
    # A scope whose model has been published dissolves here, and a public
    # session whose model stopped being selectable is returned to the picker
    # (R35), before any of it reaches a classification.
    stale = revalidate_session_model(request, action='classify')
    if stale is not None:
        return stale

    # The model that will actually run: the scope first, then the session's
    # selection (KTD10). Inside a scope the selection cannot influence which
    # model executes, so a stale user-model id is dropped with it.
    scoped_model_type = live_scope_model_id(request.session)
    selected_model_type = request.session.get('selected_model_type')
    selected_model_id = request.session.get('selected_model_id') or None
    if selected_model_id == '':
        selected_model_id = None
    if scoped_model_type is not None:
        selected_model_type = scoped_model_type
        selected_model_id = None

    # If no model selected, redirect to model selection
    if selected_model_type is None:
        return HttpResponseRedirect(reverse('astrodash:model_selection') + '?action=classify')

    # User chose "Use uploaded model" but no id in session → redirect to re-pick
    if selected_model_type == 'user_uploaded' and not selected_model_id:
        clear_selection(request.session)
        messages.warning(request, "Please select an uploaded model again.")
        return HttpResponseRedirect(reverse('astrodash:model_selection') + '?action=classify')

    form_files = request.FILES or None
    injected_cached_file = False
    if request.method == 'POST':
        posted_supernova_name = (request.POST.get('supernova_name') or '').strip()
        posted_file = request.FILES.get('file')
        cached_file = request.session.get('classify_uploaded_file')
        # Browsers clear file inputs after submit; if the user submits again with no new
        # source selected, reuse the previously uploaded file from session.
        if not posted_file and not posted_supernova_name and isinstance(cached_file, dict):
            try:
                raw_content = base64.b64decode(cached_file.get('content_b64', ''), validate=True)
                restored_file = SimpleUploadedFile(
                    name=cached_file.get('name') or 'uploaded_spectrum.dat',
                    content=raw_content,
                    content_type=cached_file.get('content_type') or 'application/octet-stream',
                )
                form_files = request.FILES.copy()
                form_files['file'] = restored_file
                injected_cached_file = True
            except Exception:
                # If cached file restore fails, continue with original request files.
                form_files = request.FILES or None

    form = _build_classify_form(selected_model_type, request.POST or None, form_files)
    # Display label for "Model Used" when a user model is selected
    selected_model_display = None
    if selected_model_type == 'user_uploaded' and selected_model_id:
        try:
            model_service = get_model_service()
            um = async_to_sync(model_service.get_model)(selected_model_id)
            selected_model_display = (um.name or "").strip() or selected_model_id
        except Exception:
            selected_model_display = "User uploaded model"
    # In a scoped session the model control is not a choice: it renders
    # disabled, naming the one model the scope allows.
    scoped_model_display = None
    if scoped_model_type is not None:
        scoped_definition = get_definition(scoped_model_type)
        scoped_model_display = (
            scoped_definition.title if scoped_definition else scoped_model_type
        )
    # Result surfaces (the tab strip and its panes) come from the *selected*
    # model's declared list per KTD5, so a fresh page load already shows every
    # tab the chosen model offers, before any classification has run.
    result_surfaces = declared_surfaces(selected_model_type)
    context = {
        'form': form,
        'selected_model_type': selected_model_type,
        'selected_model_id': selected_model_id,
        'selected_model_display': selected_model_display,
        'scoped_model_type': scoped_model_type,
        'scoped_model_display': scoped_model_display,
        'result_surfaces': result_surfaces,
        'result_surface_ids': tuple(surface.id for surface in result_surfaces),
        # Whether the model that will actually run takes a redshift as an input;
        # a model that declines it renders neither redshift control.
        'show_redshift_controls': takes_redshift_input(selected_model_type),
        'persisted_file_name': (request.session.get('classify_uploaded_file') or {}).get('name'),
    }

    # Fresh page entry should not keep a previously persisted file/params.
    if request.method == 'GET':
        has_overlay_qs = (
            request.GET.get('overlay_apply') == '1'
            or bool(request.GET.getlist('overlay_elements'))
            or bool(request.GET.getlist('overlay_templates'))
        )
        if not has_overlay_qs:
            request.session.pop('classify_uploaded_file', None)
            request.session.pop('classify_last_params', None)
            request.session.pop('classify_input_source_key', None)
            context['persisted_file_name'] = None

    # GET with overlay params: re-render plot from session with new overlays (no re-classification)
    if request.method == 'GET' and request.session.get('classify_processed'):
        overlay_elements_get = request.GET.getlist('overlay_elements')
        overlay_templates_get = request.GET.getlist('overlay_templates')
        overlay_apply_get = request.GET.get('overlay_apply') == '1'
        if overlay_apply_get or overlay_elements_get or overlay_templates_get:
            stored = request.session['classify_processed']
            try:
                from types import SimpleNamespace
                processed = SimpleNamespace(x=stored['x'], y=stored['y'])
            except (KeyError, TypeError):
                processed = None
            if processed:
                plot_wave_min = request.session.get('classify_plot_wave_min')
                plot_wave_max = request.session.get('classify_plot_wave_max')
                show_templates_section = request.session.get('classify_show_templates_section', False)
                formatted_results = request.session.get('classify_results', {'best_matches': []})
                _annotate_best_match_template_variant_counts(formatted_results, show_templates_section)
                request.session['classify_results'] = formatted_results
                request.session.modified = True
                display_model_type = request.session.get('classify_model_type', '')
                element_lines_data = []
                template_spectra_data = []
                if overlay_elements_get and plot_wave_min is not None and plot_wave_max is not None:
                    line_svc = get_line_list_service()
                    filtered = line_svc.filter_wavelengths_by_range(plot_wave_min, plot_wave_max)
                    for el in overlay_elements_get:
                        if el and el in filtered:
                            element_lines_data.append((el, filtered[el]))
                if overlay_templates_get and show_templates_section:
                    template_svc = get_template_analysis_service()
                    for spec in overlay_templates_get:
                        _append_template_overlay_from_spec(template_svc, spec, template_spectra_data)
                plot_script, plot_div = _create_bokeh_plot(
                    processed,
                    element_lines=element_lines_data if element_lines_data else None,
                    template_spectra=template_spectra_data if template_spectra_data else None,
                    wave_min=plot_wave_min,
                    wave_max=plot_wave_max,
                )
                # API base for plot customization (relative path so fetch is same-origin)
                plot_api_base = reverse('astrodash_api:line_list_elements').rsplit('/', 1)[0]
                try:
                    available_elements = get_line_list_service().get_available_elements()
                except Exception:
                    available_elements = []
                context.update({
                    'results': formatted_results,
                    'plot_script': plot_script,
                    'plot_div': plot_div,
                    'model_type': display_model_type,
                    'success': True,
                    'has_dash_embedding': bool(request.session.get('classify_dash_embedding')),
                    'plot_wave_min': plot_wave_min,
                    'plot_wave_max': plot_wave_max,
                    'show_templates_section': show_templates_section,
                    'plot_api_base': plot_api_base,
                    'overlay_elements': overlay_elements_get,
                    'overlay_templates': overlay_templates_get,
                    'available_elements': available_elements,
                    'persisted_file_name': (request.session.get('classify_uploaded_file') or {}).get('name'),
                })
                last_params = request.session.get('classify_last_params') or {}
                if last_params:
                    context['form'] = _build_classify_form(
                        selected_model_type, initial=last_params
                    )
                return render(request, 'astrodash/classify.html', context)
            # Fall through to normal render if no overlays or restore failed

    if request.method == 'POST':
        if form.is_valid():
            uploaded_file = form_files.get('file') if form_files else None
            supernova_name = form.cleaned_data.get('supernova_name')

            # Use model from session, not form (form model field only affects validation)
            model_type = selected_model_type
            logger.info(
                "Classify: using model_type=%s, user_model_id=%s",
                model_type,
                selected_model_id,
            )

            # Prepare params for services
            params = {
                'smoothing': form.cleaned_data['smoothing'],
                'minWave': form.cleaned_data['min_wave'],
                'maxWave': form.cleaned_data['max_wave'],
                'knownZ': form.cleaned_data['known_z'],
                'zValue': form.cleaned_data['redshift'],
                'modelType': model_type if model_type != 'user_uploaded' else 'transformer',  # Fallback for display
            }
            default_form = ClassifyForm()
            default_params = {
                'smoothing': default_form.fields['smoothing'].initial,
                'minWave': default_form.fields['min_wave'].initial,
                'maxWave': default_form.fields['max_wave'].initial,
                'knownZ': bool(default_form.fields['known_z'].initial),
                'zValue': default_form.fields['redshift'].initial,
            }

            current_source_key = None
            if supernova_name:
                current_source_key = f"object:{supernova_name.strip().lower()}"
                # Source switched to object lookup; clear cached uploaded-file payload.
                request.session.pop('classify_uploaded_file', None)
            elif uploaded_file:
                if injected_cached_file:
                    current_source_key = (request.session.get('classify_uploaded_file') or {}).get('source_key')
                else:
                    upload_name = getattr(uploaded_file, 'name', 'uploaded_spectrum.dat')
                    upload_type = getattr(uploaded_file, 'content_type', None) or 'application/octet-stream'
                    file_bytes = uploaded_file.read()
                    uploaded_file.seek(0)
                    file_hash = hashlib.sha256(file_bytes).hexdigest()
                    current_source_key = f"file:{file_hash}"
                    request.session['classify_uploaded_file'] = {
                        'name': upload_name,
                        'content_type': upload_type,
                        'content_b64': base64.b64encode(file_bytes).decode('ascii'),
                        'source_key': current_source_key,
                    }

            previous_source_key = request.session.get('classify_input_source_key')
            source_changed = bool(
                previous_source_key and current_source_key and current_source_key != previous_source_key)
            if source_changed:
                params.update(default_params)
            request.session['classify_input_source_key'] = current_source_key

            try:
                # Reuse the service logic
                spectrum_service = get_spectrum_service()
                processing_service = get_spectrum_processing_service()
                classification_service = get_classification_service()

                # 1. Read Spectrum
                # If file is provided, use it. Otherwise use supernova_name (osc_ref)
                spectrum = async_to_sync(spectrum_service.get_spectrum_data)(
                    file=uploaded_file,
                    osc_ref=supernova_name
                )

                # 2. Process Spectrum
                processed = async_to_sync(processing_service.process_spectrum_with_params)(
                    spectrum=spectrum,
                    params=params,
                )

                # 3. Classify
                classification = async_to_sync(classification_service.classify_spectrum)(
                    spectrum=processed,
                    model_type=model_type,
                    user_model_id=selected_model_id,
                    params=params,
                )

                # Wavelength range for plot customization (element lines, etc.)
                plot_wave_min = float(min(processed.x)) if hasattr(processed, 'x') and len(processed.x) else None
                plot_wave_max = float(max(processed.x)) if hasattr(processed, 'x') and len(processed.x) else None

                # Workaround for template filter issue: Format in view
                formatted_results = _format_results(classification.results)

                # Display name for "Model Used": user model name or classification type
                display_model_type = (
                    selected_model_display
                    if (classification.model_type == 'user_uploaded' and selected_model_display)
                    else classification.model_type
                )
                # Capability gates come from the classified model's definition
                # (DASH today). A user-uploaded model has no definition -> gates off.
                classified_def = get_definition(classification.model_type)

                # Template overlays only available for models that support them
                # (templates are DASH-specific).
                show_templates_section = bool(
                    classified_def and classified_def.supports_template_overlays
                )
                _annotate_best_match_template_variant_counts(formatted_results, show_templates_section)

                # Store the embedding in session for "Find Twins" only when the
                # classified model declares the twins surface (the declared
                # list is the sole authority, R31) and the embedding is present.
                if (classified_def is not None
                        and SURFACE_DASH_TWINS in classified_def.surfaces
                        and isinstance(classification.results.get('embedding'), list)
                        and len(classification.results['embedding']) == 1024):
                    request.session['classify_dash_embedding'] = classification.results['embedding']
                else:
                    request.session.pop('classify_dash_embedding', None)

                # Store in session for "Apply overlays" re-renders (so we don't re-run classification)
                request.session['classify_processed'] = {
                    'x': list(getattr(processed, 'x', [])),
                    'y': list(getattr(processed, 'y', [])),
                }
                request.session['classify_results'] = formatted_results
                request.session['classify_model_type'] = display_model_type
                request.session['classify_show_templates_section'] = show_templates_section
                request.session['classify_plot_wave_min'] = plot_wave_min
                request.session['classify_plot_wave_max'] = plot_wave_max
                request.session['classify_last_params'] = {
                    'supernova_name': supernova_name,
                    'smoothing': params['smoothing'],
                    'min_wave': params['minWave'],
                    'max_wave': params['maxWave'],
                    'known_z': params['knownZ'],
                    'redshift': params['zValue'],
                }

                # Overlay state from POST (when user clicked Apply in Customize modal), else empty
                overlay_elements = request.POST.getlist('overlay_elements') or []
                overlay_templates = request.POST.getlist('overlay_templates') or []  # sn_type|age_bin or |variant_index

                # Build overlay data and plot
                element_lines_data = []
                template_spectra_data = []
                if overlay_elements or overlay_templates:
                    line_svc = get_line_list_service()
                    wave_min_s = plot_wave_min
                    wave_max_s = plot_wave_max
                    if wave_min_s is not None and wave_max_s is not None:
                        filtered = line_svc.filter_wavelengths_by_range(wave_min_s, wave_max_s)
                        for el in overlay_elements:
                            if el and el in filtered:
                                element_lines_data.append((el, filtered[el]))
                    if overlay_templates and show_templates_section:
                        template_svc = get_template_analysis_service()
                        for spec in overlay_templates:
                            _append_template_overlay_from_spec(template_svc, spec, template_spectra_data)

                # 4. Generate Plot (with overlays if any)
                plot_script, plot_div = _create_bokeh_plot(
                    processed,
                    element_lines=element_lines_data if element_lines_data else None,
                    template_spectra=template_spectra_data if template_spectra_data else None,
                    wave_min=plot_wave_min,
                    wave_max=plot_wave_max,
                )

                # API base for plot customization (relative path so fetch is same-origin)
                plot_api_base = reverse('astrodash_api:line_list_elements').rsplit('/', 1)[0]
                try:
                    available_elements = get_line_list_service().get_available_elements()
                except Exception:
                    available_elements = []

                context.update({
                    'results': formatted_results,
                    'plot_script': plot_script,
                    'plot_div': plot_div,
                    'model_type': display_model_type,
                    'success': True,
                    'has_dash_embedding': bool(request.session.get('classify_dash_embedding')),
                    'plot_wave_min': plot_wave_min,
                    'plot_wave_max': plot_wave_max,
                    'show_templates_section': show_templates_section,
                    'plot_api_base': plot_api_base,
                    'overlay_elements': overlay_elements,
                    'overlay_templates': overlay_templates,
                    'available_elements': available_elements,
                    'persisted_file_name': (request.session.get('classify_uploaded_file') or {}).get('name'),
                })
                if source_changed:
                    context['form'] = _build_classify_form(
                        selected_model_type,
                        initial=(
                            {'supernova_name': supernova_name} if supernova_name else None
                        ),
                    )

            except AppException as e:
                messages.error(request, f"Processing Error: {e.message}")
            except Exception as e:
                messages.error(request, f"An unexpected error occurred: {str(e)}")
        else:
            logger.warning(
                "Classify form invalid: errors=%s",
                form.errors.as_json() if form.errors else None,
            )
    return render(request, 'astrodash/classify.html', context)


def batch_process(request):
    """
    Handles batch processing UI.
    Support for both ZIP file uploads and multiple individual file uploads.
    """
    # A scoped session reaches classification only (R24), and a session whose
    # model stopped being selectable is returned to the picker (R35).
    refusal = scoped_flow_refusal(request)
    if refusal is not None:
        return refusal
    stale = revalidate_session_model(request, action='batch')
    if stale is not None:
        return stale

    # Get model selection from session (set by model_selection view)
    selected_model_type = request.session.get('selected_model_type')
    selected_model_id = request.session.get('selected_model_id', None)

    # If no model selected, redirect to model selection
    if selected_model_type is None:
        return HttpResponseRedirect(reverse('astrodash:model_selection') + '?action=batch')

    form = BatchForm(request.POST or None, request.FILES or None)
    context = {
        'form': form,
        # Same policy the classification flow renders on: a model that takes no
        # redshift shows neither redshift control here either.
        'show_redshift_controls': takes_redshift_input(selected_model_type),
    }

    if request.method == 'POST':
        logger.info(
            "Batch UI request received: user=%s, selected_model_type=%s, selected_model_id=%s",
            getattr(request.user, "username", "anonymous"),
            selected_model_type,
            selected_model_id,
        )
        # Manually attach files to form for validation if needed, though form.is_valid handles request.FILES
        # For the 'files' field which uses ClearableFileInput key 'files', we need to check request.FILES.getlist
        files = request.FILES.getlist('files')

        if form.is_valid():
            # This gate is the batch flow's own, separate from the classify
            # form's, but reads the same declared policy: only a model whose
            # definition requires a redshift is refused for missing one.
            if (redshift_input_policy(selected_model_type) == REDSHIFT_INPUT_REQUIRED
                    and not form.cleaned_data.get('redshift')):
                form.add_error('redshift', REDSHIFT_REQUIRED_MESSAGE)
            else:
                try:
                    model_type = selected_model_type
                    redshifts = form.cleaned_data.get('redshift') or []

                    # Prepare params
                    params = {
                        'smoothing': form.cleaned_data['smoothing'],
                        'minWave': form.cleaned_data['min_wave'],
                        'maxWave': form.cleaned_data['max_wave'],
                        'knownZ': form.cleaned_data['known_z'],
                        'zValue': redshifts[0] if redshifts else None,
                        'zValues': redshifts,
                        'calculateRlap': form.cleaned_data['calculate_rlap'],
                        'modelType': model_type if model_type != 'user_uploaded' else 'dash',  # Fallback for display
                    }

                    logger.info(
                        "Batch UI parameters: "
                        f'''smoothing={params['smoothing']} '''
                        f'''minWave={params['minWave']} '''
                        f'''maxWave={params['maxWave']} '''
                        f'''knownZ={params['knownZ']} '''
                        f'''zValue={params['zValue']} '''
                        f'''zValues={params['zValues']} '''
                        f'''calculateRlap={params['calculateRlap']} '''
                        f'''modelType={params['modelType']} '''
                    )

                    batch_service = get_batch_processing_service()

                    zip_file = form.cleaned_data.get('zip_file')

                    results = {}

                    files_to_process = None
                    if zip_file:
                        files_to_process = zip_file
                    elif files:
                        files_to_process = files
                    else:
                        messages.error(request, "Please upload a ZIP file or select multiple files.")
                        return render(request, 'astrodash/batch.html', context)

                    if isinstance(files_to_process, list):
                        logger.info("Batch UI will process %d individual files", len(files_to_process))
                    else:
                        logger.info("Batch UI will process ZIP file: %s", getattr(files_to_process, "name", "unknown"))

                    results = async_to_sync(batch_service.process_batch)(
                        files=files_to_process,
                        params=params,
                        model_type=model_type,
                        model_id=selected_model_id
                    )

                    # Format results for template. Run metadata is attached to
                    # every row so the CSV is self-contained, and also passed
                    # once for the results-page summary.
                    run_metadata = _batch_run_metadata(model_type, params)
                    formatted_results = _format_batch_results(
                        results, params, metadata=run_metadata
                    )
                    logger.info("Batch UI processing completed successfully for %d items", len(formatted_results))
                    context['results'] = formatted_results
                    context['run_metadata'] = run_metadata
                    context['success'] = True

                except AppException as e:
                    logger.error("Batch UI processing failed with AppException: %s", e.message)
                    messages.error(request, f"Batch Processing Error: {e.message}")
                except Exception as e:
                    logger.error("Batch UI processing failed with unexpected error", exc_info=True)
                    messages.error(request, f"An unexpected error occurred during batch processing: {str(e)}")

    return render(request, 'astrodash/batch.html', context)


def _batch_input_redshift_display(params):
    """Format submitted batch redshifts for the results banner and CSV fallback.

    A single value stays a number (or ``None``). Several values become a
    comma-separated string so the banner can show the whole list.
    """
    z_values = params.get('zValues')
    if z_values is None:
        z = params.get('zValue')
        z_values = [] if z is None else [z]
    if not z_values:
        return None
    if len(z_values) == 1:
        return z_values[0]
    return ', '.join(str(z) for z in z_values)


def _batch_run_metadata(model_type, params, classified_at=None):
    """
    Build the run-level fields attached to a batch result set.

    ``input_redshift`` is the submitted redshift or list of redshifts, or
    ``None`` when none was given. ``model_type`` is the selected model, not the
    display fallback stored on ``params['modelType']``.
    """
    if classified_at is None:
        classified_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    return {
        'classified_at': classified_at,
        'model_type': model_type,
        'smoothing': params.get('smoothing'),
        'min_wave': params.get('minWave'),
        'max_wave': params.get('maxWave'),
        'input_redshift': _batch_input_redshift_display(params),
    }


def _format_batch_results(results, params, metadata=None):
    """
    Format batch results for display in the template.

    When ``metadata`` is supplied (classified_at, model_type, smoothing,
    wavelength window, input_redshift), those fields are copied onto every
    row, including error rows, so the CSV download is self-contained.
    Per-spectrum ``input_redshift`` prefers the redshift actually applied to
    that file when the batch service recorded one.
    """
    formatted = {}
    run_fields = dict(metadata) if metadata else {}
    banner_input_redshift = run_fields.pop('input_redshift', None)
    for filename, result in results.items():
        formatted_item = dict(run_fields)

        # Check for error
        if result.get('error'):
            formatted_item['error'] = result['error']
        else:
            # Extract classification data
            classification = result.get('classification', {})
            best_match = classification.get('best_match', {})

            formatted_item['type'] = best_match.get('type', '-')
            formatted_item['age'] = best_match.get('age', '-')

            prob = best_match.get('probability')
            formatted_item['probability'] = f"{prob:.4f}" if prob is not None else '-'

            formatted_item['redshift'] = best_match.get('redshift', '-')

            # RLAP only for models whose definition supports it (Dash today) and
            # only if requested.
            rlap_def = get_definition(params.get('modelType'))
            if rlap_def is not None and rlap_def.supports_rlap and params.get('calculateRlap'):
                formatted_item['rlap'] = best_match.get('rlap', '-')
            else:
                formatted_item['rlap'] = '-'

        if 'applied_redshift' in result:
            formatted_item['input_redshift'] = result.get('applied_redshift')
        else:
            formatted_item['input_redshift'] = banner_input_redshift

        formatted[filename] = formatted_item

    return formatted


def _parse_overlay_template_spec(spec: str):
    """
    Parse overlay_templates entry: 'sn_type|age_bin' or 'sn_type|age_bin|variant_index'.
    If the last segment is all digits, it is treated as a 0-based variant index.
    """
    if not spec or '|' not in spec:
        return None
    parts = spec.split('|')
    if len(parts) < 2:
        return None
    last = parts[-1]
    if last.isdigit():
        sn_type = parts[0]
        age_bin = '|'.join(parts[1:-1])
        variant_index = int(last)
        return sn_type, age_bin, variant_index
    return parts[0], '|'.join(parts[1:]), 0


def _append_template_overlay_from_spec(template_svc, spec, template_spectra_data):
    parsed = _parse_overlay_template_spec(spec)
    if not parsed:
        return
    sn_type, age_bin, variant_index = parsed
    try:
        wave, flux = template_svc.template_handler.get_template_spectrum(
            sn_type, age_bin, variant_index=variant_index
        )
        label = f"{sn_type} {age_bin}"
        if variant_index:
            label = f"{label} (#{variant_index + 1})"
        template_spectra_data.append((label, wave, flux))
    except Exception:
        pass


def _annotate_best_match_template_variant_counts(results: dict, enabled: bool) -> None:
    """Attach template_variant_count to each best match (DASH snInfo row count)."""
    matches = results.get('best_matches') or []
    if not matches:
        return
    if not enabled:
        return
    template_svc = get_template_analysis_service()
    handler = template_svc.template_handler
    for m in matches:
        try:
            n = handler.get_template_variant_count(m['type'], m['age'])
            m['template_variant_count'] = max(1, int(n))
        except Exception:
            m['template_variant_count'] = 1


# Colors for element-line and template overlays (consistent palette)
_PLOT_OVERLAY_COLORS = [
    "#e41a1c", "#377eb8", "#4daf4a", "#984ea3", "#ff7f00",
    "#a65628", "#f781bf", "#999999", "#66c2a5", "#fc8d62",
    "#8da0cb", "#e78ac3", "#a6d854", "#ffd92f", "#e5c494", "#b3b3b3",
]


def _create_bokeh_plot(spectrum, element_lines=None, template_spectra=None, wave_min=None, wave_max=None):
    """
    Creates a Bokeh plot for the spectrum with optional element-line and template overlays.
    element_lines: list of (element_name, list of wavelengths in Å).
    template_spectra: list of (label, x_array, y_array).
    wave_min, wave_max: clip overlays to this wavelength range (from spectrum if None).
    """
    x = getattr(spectrum, 'x', None)
    y = getattr(spectrum, 'y', None)
    if x is None or y is None:
        x, y = [], []
    source = ColumnDataSource(data=dict(x=x, y=y))

    if wave_min is None and len(x):
        wave_min = float(min(x))
    if wave_max is None and len(x):
        wave_max = float(max(x))

    p = figure(
        title="Spectrum",
        x_axis_label='Wavelength (Å)',
        y_axis_label='Flux',
        height=400,
        sizing_mode="stretch_width",
        tools="pan,box_zoom,reset,save",
        x_range=(wave_min, wave_max) if (wave_min is not None and wave_max is not None) else None,
    )

    p.line('x', 'y', source=source, line_width=2, color="#1976d2", legend_label="Observed")

    p.add_tools(HoverTool(
        tooltips=[
            ('Wavelength', '@x{0.0}'),
            ('Flux', '@y{0.00e}'),
        ],
        mode='vline'
    ))

    # Element/ion lines: vertical spans at each wavelength in range; each element gets a distinct color
    # Labels go next to the line (first wavelength in range) instead of in a legend
    if element_lines and wave_min is not None and wave_max is not None:
        y_max = max(y) * 1.02 if y else 0
        for idx, (element_name, wavelengths) in enumerate(element_lines):
            color = _PLOT_OVERLAY_COLORS[idx % len(_PLOT_OVERLAY_COLORS)]
            wls_in_range = [wl for wl in wavelengths if wave_min <= wl <= wave_max]
            for wl in wls_in_range:
                span = Span(location=wl, dimension="height", line_color=color, line_dash="4 4", line_width=1)
                p.add_layout(span)
            if wls_in_range:
                first_wl = wls_in_range[0]
                label = Label(
                    x=first_wl,
                    y=y_max,
                    text=element_name,
                    x_units="data",
                    y_units="data",
                    text_color=color,
                    text_font_size="9pt",
                    text_alpha=0.9,
                    x_offset=4,
                    y_offset=-8,
                )
                p.add_layout(label)

    # Template spectra: extra lines
    if template_spectra:
        for idx, (label, tx, ty) in enumerate(template_spectra):
            if tx is None or ty is None or len(tx) == 0:
                continue
            tx, ty = list(tx), list(ty)
            if wave_min is not None and wave_max is not None:
                filtered = [(wx, wy) for wx, wy in zip(tx, ty) if wave_min <= wx <= wave_max]
                if not filtered:
                    continue
                tx, ty = zip(*filtered)
                tx, ty = list(tx), list(ty)
            color = _PLOT_OVERLAY_COLORS[idx % len(_PLOT_OVERLAY_COLORS)]
            p.line(tx, ty, line_width=2, color=color, legend_label=label)

    p.legend.location = "bottom_center"
    p.legend.orientation = "horizontal"
    p.legend.click_policy = "hide"

    p.background_fill_color = "#f5f5f5"
    p.border_fill_color = "#ffffff"

    return components(p)


def _format_results(results):
    """
    Format results for display in the template to avoid filter issues.
    """
    formatted_matches = []

    # helper to get attributes from dict or object
    def get_attr(obj, attr, default=None):
        if isinstance(obj, dict):
            return obj.get(attr, default)
        return getattr(obj, attr, default)

    # Check if results has best_matches
    best_matches = get_attr(results, 'best_matches', [])

    for match in best_matches:
        # Create a dict representation
        match_dict = {}

        # Extract fields needed for template
        for field in ['type', 'age', 'probability', 'redshift', 'reliable']:
            match_dict[field] = get_attr(match, field)

        # Add formatted probability
        if match_dict['probability'] is not None:
            match_dict['formatted_probability'] = f"{match_dict['probability']:.4f}"
        else:
            match_dict['formatted_probability'] = ""

        formatted_matches.append(match_dict)

    return {'best_matches': formatted_matches}
