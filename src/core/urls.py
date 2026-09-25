"""API routes under /api/."""
from django.urls import path
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularSwaggerView,
)

from . import api_views

urlpatterns = [
    path("projects", api_views.ProjectListCreateView.as_view(), name="api-projects"),
    path("projects/", api_views.ProjectListCreateView.as_view()),
    path("judge/scores", api_views.JudgeScoresView.as_view(), name="api-judge-scores"),
    path("judge/scores/", api_views.JudgeScoresView.as_view()),
    path("export/results.csv", api_views.CsvExportView.as_view(), name="api-export-csv"),
    path("progress", api_views.ProgressView.as_view(), name="api-progress"),
    path("results/publish", api_views.PublishResultsView.as_view(), name="api-results-publish"),
    path("results/publish/", api_views.PublishResultsView.as_view()),
    path("results/bundle", api_views.BundleView.as_view(), name="api-results-bundle"),
    path("results/bundle/", api_views.BundleView.as_view()),
    path("verification-key", api_views.VerificationKeyView.as_view(), name="api-verification-key"),
    path("verification-key/", api_views.VerificationKeyView.as_view()),
    path("schema", SpectacularAPIView.as_view(), name="schema"),
    path("docs", SpectacularSwaggerView.as_view(url_name="schema"), name="docs"),
]
