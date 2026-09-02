from django.urls import path
from astrodash import ui_views

app_name = "astrodash"

urlpatterns = [
    # App static images (logo, favicon) served from app/astrodash/static/images/
    path("static/images/<path:path>", ui_views.serve_app_static_image, name="app_static_images"),
    # UI Views
    path("", ui_views.landing_page, name="landing_page"),
    path("select-model/", ui_views.model_selection, name="model_selection"),
    # The explicit end of a model-scoped session. Declared before the entry-link
    # route below, whose token pattern would otherwise swallow this path.
    path("model-access/end/", ui_views.end_model_scope, name="end_model_scope"),
    # The entry link for a gated model. This is the redeem side only: no route
    # mints a link, because minting is an operator action (see the
    # mint_model_link management command).
    path("model-access/<str:token>/", ui_views.model_gate, name="model_gate"),
    path("classify/", ui_views.classify, name="classify"),
    path("batch/", ui_views.batch_process, name="batch_process_ui"),
    path("leaderboard/", ui_views.leaderboard, name="leaderboard"),
    path("team/", ui_views.team_members, name="team_members"),
    path("classify/twins/", ui_views.dash_twins, name="dash_twins"),
    path("classify/twins/data/", ui_views.dash_twins_data, name="dash_twins_data"),
    path("classify/twins/search/", ui_views.twins_search, name="twins_search"),
]
