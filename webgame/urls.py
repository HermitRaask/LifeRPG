from django.urls import path

from webgame import views, onboarding, catalog_views

urlpatterns = [
    path("catalog/", catalog_views.listing, name="catalog"),
    path("archive/", catalog_views.listing, {"archived": True},
         name="archive"),
    path("catalog/<slug:command>/", catalog_views.mutate,
         name="catalog-action"),
    path("onboarding/", onboarding.welcome, name="welcome"),
    path("onboarding/control/", onboarding.control, name="onboarding-action"),
    path("tutorial/", onboarding.tutorial, name="tutorial"),
    path("", views.page, name="home"),
    path("action/<slug:command>/", views.action, name="action"),
    path("preview/", views.preview, name="preview"),
    path("result/<uuid:operation>/", views.result, name="result"),
    path("<slug:section>/", views.page, name="page"),
]
