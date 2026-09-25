"""HTTP views for the DOGFOOD portal.

The gallery and health endpoints are plain Django views. Everything
under /api/ is Django REST Framework, so role isolation is enforced by
DRF permissions and lives in the backend, not the template.
"""
from django.http import JsonResponse
from django.shortcuts import render


def health(request):
    """Liveness probe. Always public, always JSON."""
    return JsonResponse({"status": "ok", "service": "dogfood-portal"})


def home(request):
    """Landing page. Points a visitor at the public gallery."""
    return render(request, "core/home.html")


def gallery(request):
    """Public project gallery. No authentication required.

    Search with ?q= over title and summary, filter with ?track=<track id>.
    The default view lists every submitted project ordered by its id, so
    page one always includes the first fixture project.
    """
    from .models import Project, Track

    projects = (
        Project.objects.filter(status=Project.Status.SUBMITTED)
        .select_related("team", "track")
        .order_by("external_id")
    )
    query = request.GET.get("q", "").strip()
    track_id = request.GET.get("track", "").strip()
    if query:
        from django.db.models import Q

        projects = projects.filter(
            Q(title__icontains=query) | Q(summary__icontains=query)
        )
    if track_id:
        projects = projects.filter(track__external_id=track_id)
    context = {
        "projects": projects,
        "tracks": Track.objects.order_by("external_id"),
        "query": query,
        "track_id": track_id,
    }
    return render(request, "core/gallery.html", context)
