from django.urls import path

from . import views

urlpatterns = [
    path("", views.iframe_entry, name="iframe-entry"),
    path("iframe/", views.iframe_entry, name="iframe-entry"),
    path("activities/", views.activity_landing, name="activity-landing"),
    path("activities/relationship/", views.relationship_activity, name="relationship-activity"),
    path("activities/relationship/results/", views.relationship_results, name="relationship-results"),
    path("activities/housing/", views.housing_activity, name="housing-activity"),
    path("activities/housing/results/", views.housing_results, name="housing-results"),
    path("research/login/", views.research_login, name="research-login"),
    path("research/logout/", views.research_logout, name="research-logout"),
    path("research/dashboard/", views.research_dashboard, name="research-dashboard"),
    path("research/export.csv", views.research_export_csv, name="research-export-csv"),
]
