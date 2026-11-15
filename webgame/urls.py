from django.urls import path

from webgame import views

urlpatterns = [
    path("", views.page, name="home"),
    path("action/<slug:command>/", views.action, name="action"),
    path("preview/", views.preview, name="preview"),
    path("result/<uuid:operation>/", views.result, name="result"),
    path("<slug:section>/", views.page, name="page"),
]
