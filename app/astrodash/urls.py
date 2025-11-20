from django.urls import path
from astrodash import views
from astrodash import ui_views

app_name = "astrodash"

urlpatterns = [
    # UI Views
    path("", ui_views.landing_page, name="landing_page"),
    path("classify/", ui_views.classify, name="classify"),
    path("batch/", ui_views.batch_process, name="batch_process_ui"),
]
