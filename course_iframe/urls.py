from django.urls import path
from django.views.generic import RedirectView

from . import builder_views, views

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
    path("research/activities/<slug:activity_slug>/delete/", builder_views.delete_activity, name="research-delete-activity"),
    path("research/activities/new/", builder_views.new_activity, name="research-add-activity"),
    path("research/activities/<slug:activity_slug>/edit/", builder_views.activity_editor, name="research-edit-activity"),
    path("research/activities/<slug:activity_slug>/preview/", builder_views.activity_preview, name="research-preview-activity"),
    path("activities/<slug:activity_slug>/", builder_views.student_activity, name="activity-student"),
    path("activities/<slug:activity_slug>/results/", builder_views.student_results, name="activity-results"),
    path("research/activities/<slug:activity_slug>/plots/", builder_views.activity_plots, name="research-activity-plots"),
    path("research/activities/<slug:activity_slug>/responses/", builder_views.activity_responses, name="research-activity-responses"),
    path("activities/<slug:activity_slug>/entries/<int:response_id>/delete/", builder_views.delete_entry, name="activity-delete-entry"),
    path("research/export.csv", views.research_export_csv, name="research-export-csv"),
]
