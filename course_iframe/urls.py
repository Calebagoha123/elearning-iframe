from django.urls import path
from django.views.generic import RedirectView

from . import views

urlpatterns = [
    path("healthz", views.healthz, name="healthz"),
    # Bare domain is only ever typed by researchers; students reach activities
    # via the embedded /iframe/<activity>/ URLs. Send the root to the login gate.
    # Temporary (302) so it stays easy to change later without browser caching.
    path("", RedirectView.as_view(pattern_name="research-login", permanent=False), name="root"),
    path("iframe/<slug:activity_slug>/", views.activity_iframe_entry, name="activity-iframe-entry"),
    path("activities/", views.activity_landing, name="activity-landing"),
    path("activities/relationship/", views.relationship_activity, name="relationship-activity"),
    path("activities/relationship/results/", views.relationship_results, name="relationship-results"),
    path("activities/housing/", views.housing_activity, name="housing-activity"),
    path("activities/housing/entries/<int:response_id>/delete/", views.housing_delete_entry, name="housing-delete-entry"),
    path("activities/housing/results/", views.housing_results, name="housing-results"),
    path("activities/summary/", views.student_summary, name="student-summary"),
    path("research/login/", views.research_login, name="research-login"),
    path("research/logout/", views.research_logout, name="research-logout"),
    path("research/dashboard/", views.research_dashboard, name="research-dashboard"),
    path("research/activities/relationship/edit/", views.edit_relationship, name="research-edit-relationship"),
    path("research/activities/relationship/plots/", views.research_relationship_plots, name="research-relationship-plots"),
    path("research/activities/housing/edit/", views.edit_housing, name="research-edit-housing"),
    path("research/activities/housing/plots/", views.research_housing_plots, name="research-housing-plots"),
    path("research/export.csv", views.research_export_csv, name="research-export-csv"),
]
