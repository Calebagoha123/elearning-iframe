from django.contrib import admin
from django.conf import settings
from django.contrib.staticfiles.urls import staticfiles_urlpatterns
from django.urls import include, path

urlpatterns = [
    path("django-admin/", admin.site.urls),
    path("", include("course_iframe.urls")),
]

if settings.DEBUG:
    urlpatterns += staticfiles_urlpatterns()
