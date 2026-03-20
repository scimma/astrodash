from django.shortcuts import render
from django.contrib import messages
from django.conf import settings
from django.http import HttpResponseRedirect, FileResponse, Http404, JsonResponse
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.urls import reverse
from pathlib import Path

from astrodash.forms import ClassifyForm, BatchForm, ModelSelectionForm
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


logger = get_logger(__name__)

# Directory containing logo, favicon, etc. (app/astrodash/static/images/)
APP_STATIC_IMAGES_DIR = Path(__file__).resolve().parent / "static" / "images"

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


def team_members(request):
    """
    Renders the Team Members page: affiliations (labs/universities) with
    members (picture, name, description).
    """
    from astrodash.models import TeamAffiliation
    affiliations = TeamAffiliation.objects.prefetch_related("members").all()
    return render(request, "astrodash/team_members.html", {"affiliations": affiliations})


@xframe_options_sameorigin
def dash_twins(request):
    """
    Renders the DASH Twins Explorer (embedding visualization).
    UI is in astrodash/explorer/dash_twins.html; data is loaded via dash_twins_data.
    """
    return render(request, "astrodash/explorer/dash_twins.html")


def dash_twins_data(request):
    """
    Serves the DASH Twins payload JSON from {data_dir}/explorer (same as models, templates).
    Generate with extract_payload.py --build-artifacts (optionally --out-dir to data_dir/explorer).
    """
    path = Path(get_config().data_dir) / "explorer" / "dash_twins_payload.json"
    if not path.is_file():
        raise Http404("DASH Twins data not found. Run extract_payload.py --build-artifacts and ensure files are in ASTRODASH_DATA_DIR/explorer/.")
    return FileResponse(
        open(path, "rb"),
        content_type="application/json",
        as_attachment=False,
    )


def twins_search(request):
    """
    POST or GET: run twins search using the DASH embedding stored in session
    (set after classifying a spectrum with DASH). Returns JSON with query_umap,
    query_pca, twin_indices, twin_similarities, and optionally user_spectrum.
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


def model_selection(request):
    """
    Handles model selection page - allows choosing between dash/transformer or uploading a custom model.
    """
    # Clear messages carried over from other pages (e.g. classify/batch processing errors)
    # so the model selection page doesn't show unrelated errors.
    list(messages.get_messages(request))

    action_type = request.GET.get('action', 'classify')  # 'classify' or 'batch'
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
                    messages.error(request, "No file was received. Ensure the form uses enctype='multipart/form-data' and you selected a file.")
                    return render(
                        request,
                        'astrodash/model_selection.html',
                        {
                            'form': form,
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
                            "Forward-pass validation failed (dummy run had a shape mismatch). You can still use this model for classification—real inputs may work."
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
        'action_type': action_type,
        'existing_models_count': len(existing_models),
        'show_upload_section': show_upload_section,
    }
    return render(request, 'astrodash/model_selection.html', context)

def classify(request):
    """
    Handles spectrum classification via the UI.
    """
    # Get model selection from session (set by model_selection view)
    selected_model_type = request.session.get('selected_model_type')
    selected_model_id = request.session.get('selected_model_id') or None
    if selected_model_id == '':
        selected_model_id = None

    # If no model selected, redirect to model selection
    if selected_model_type is None:
        return HttpResponseRedirect(reverse('astrodash:model_selection') + '?action=classify')

    # User chose "Use uploaded model" but no id in session → redirect to re-pick
    if selected_model_type == 'user_uploaded' and not selected_model_id:
        request.session.pop('selected_model_type', None)
        request.session.pop('selected_model_id', None)
        messages.warning(request, "Please select an uploaded model again.")
        return HttpResponseRedirect(reverse('astrodash:model_selection') + '?action=classify')

    form = ClassifyForm(request.POST or None, request.FILES or None)
    # Set the model from session (for validation and display)
    form.fields['model'].initial = (
        'user_uploaded' if selected_model_type == 'user_uploaded' else selected_model_type
    )
    # When showing the dropdown, only offer dash/transformer; user_uploaded is sent via hidden input
    if selected_model_type != 'user_uploaded':
        form.fields['model'].choices = [
            c for c in form.fields['model'].choices if c[0] != 'user_uploaded'
        ]
    # Display label for "Model Used" when a user model is selected
    selected_model_display = None
    if selected_model_type == 'user_uploaded' and selected_model_id:
        try:
            model_service = get_model_service()
            um = async_to_sync(model_service.get_model)(selected_model_id)
            selected_model_display = (um.name or "").strip() or selected_model_id
        except Exception:
            selected_model_display = "User uploaded model"
    context = {
        'form': form,
        'selected_model_type': selected_model_type,
        'selected_model_id': selected_model_id,
        'selected_model_display': selected_model_display,
    }

    # GET with overlay params: re-render plot from session with new overlays (no re-classification)
    if request.method == 'GET' and request.session.get('classify_processed'):
        overlay_elements_get = request.GET.getlist('overlay_elements')
        overlay_templates_get = request.GET.getlist('overlay_templates')
        if overlay_elements_get or overlay_templates_get:
            stored = request.session['classify_processed']
            try:
                from types import SimpleNamespace
                processed = SimpleNamespace(x=stored['x'], y=stored['y'])
            except (KeyError, TypeError):
                processed = None
            if processed and (overlay_elements_get or overlay_templates_get):
                plot_wave_min = request.session.get('classify_plot_wave_min')
                plot_wave_max = request.session.get('classify_plot_wave_max')
                show_templates_section = request.session.get('classify_show_templates_section', False)
                formatted_results = request.session.get('classify_results', {'best_matches': []})
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
                        if not spec or '|' not in spec:
                            continue
                        sn_type, age_bin = spec.split('|', 1)
                        try:
                            wave, flux = template_svc.template_handler.get_template_spectrum(sn_type, age_bin)
                            label = f"{sn_type} {age_bin}"
                            template_spectra_data.append((label, wave, flux))
                        except Exception:
                            pass
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
                })
                return render(request, 'astrodash/classify.html', context)
            # Fall through to normal render if no overlays or restore failed

    if request.method == 'POST':
        if form.is_valid():
            uploaded_file = request.FILES.get('file')
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
                # Template overlays only available for DASH model (templates are DASH-specific)
                show_templates_section = classification.model_type == 'dash'

                # Store DASH embedding in session for "Find Twins" (only when DASH and embedding present)
                if classification.model_type == 'dash' and isinstance(classification.results.get('embedding'), list) and len(classification.results['embedding']) == 1024:
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

                # Overlay state from POST (when user clicked Apply in Customize modal), else empty
                overlay_elements = request.POST.getlist('overlay_elements') or []
                overlay_templates = request.POST.getlist('overlay_templates') or []  # each item "sn_type|age_bin"

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
                            if not spec or '|' not in spec:
                                continue
                            sn_type, age_bin = spec.split('|', 1)
                            try:
                                wave, flux = template_svc.template_handler.get_template_spectrum(sn_type, age_bin)
                                label = f"{sn_type} {age_bin}"
                                template_spectra_data.append((label, wave, flux))
                            except Exception:
                                pass

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
                })
                
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


from astrodash.services import get_batch_processing_service

def batch_process(request):
    """
    Handles batch processing UI.
    Support for both ZIP file uploads and multiple individual file uploads.
    """
    # Get model selection from session (set by model_selection view)
    selected_model_type = request.session.get('selected_model_type')
    selected_model_id = request.session.get('selected_model_id', None)

    # If no model selected, redirect to model selection
    if selected_model_type is None:
        return HttpResponseRedirect(reverse('astrodash:model_selection') + '?action=batch')

    form = BatchForm(request.POST or None, request.FILES or None)
    context = {'form': form}

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
            # Transformer requires redshift when known_z is not set
            if selected_model_type == 'transformer' and form.cleaned_data.get('redshift') is None:
                form.add_error('redshift', "Redshift is required for Transformer model.")
            else:
                try:
                    model_type = selected_model_type

                    # Prepare params
                    params = {
                        'smoothing': form.cleaned_data['smoothing'],
                        'minWave': form.cleaned_data['min_wave'],
                        'maxWave': form.cleaned_data['max_wave'],
                        'knownZ': form.cleaned_data['known_z'],
                        'zValue': form.cleaned_data['redshift'],
                        'calculateRlap': form.cleaned_data['calculate_rlap'],
                        'modelType': model_type if model_type != 'user_uploaded' else 'dash',  # Fallback for display
                    }

                    logger.info(
                        "Batch UI parameters: smoothing=%s, minWave=%s, maxWave=%s, knownZ=%s, zValue=%s, calculateRlap=%s, modelType=%s",
                        params['smoothing'],
                        params['minWave'],
                        params['maxWave'],
                        params['knownZ'],
                        params['zValue'],
                        params['calculateRlap'],
                        params['modelType'],
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

                    # Format results for template
                    formatted_results = _format_batch_results(results, params)
                    logger.info("Batch UI processing completed successfully for %d items", len(formatted_results))
                    context['results'] = formatted_results
                    context['success'] = True

                except AppException as e:
                    logger.error("Batch UI processing failed with AppException: %s", e.message)
                    messages.error(request, f"Batch Processing Error: {e.message}")
                except Exception as e:
                    logger.error("Batch UI processing failed with unexpected error", exc_info=True)
                    messages.error(request, f"An unexpected error occurred during batch processing: {str(e)}")

    return render(request, 'astrodash/batch.html', context)

def _format_batch_results(results, params):
    """
    Format batch results for display in the template.
    """
    formatted = {}
    for filename, result in results.items():
        formatted_item = {}
        
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
            
            # RLAP only for Dash model and if requested
            if params.get('modelType') == 'dash' and params.get('calculateRlap'):
                formatted_item['rlap'] = best_match.get('rlap', '-')
            else:
                 formatted_item['rlap'] = '-'
                 
        formatted[filename] = formatted_item
        
    return formatted

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
