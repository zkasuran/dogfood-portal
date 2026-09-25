"""DRF permissions. Role isolation lives here, in the backend, so a curl
as the wrong role is refused before any view code runs."""
from rest_framework.permissions import BasePermission

from .models import Role

JUDGE_ROLES = {Role.JUDGE, Role.ORGANIZER, Role.ADMIN}
ORGANIZER_ROLES = {Role.ORGANIZER, Role.ADMIN}


def _role(user):
    return getattr(user, "role", None)


class IsJudge(BasePermission):
    """Judge routes are for judges, organizers and admins. A participant or
    an anonymous caller is refused."""

    message = "This route is for judges only."

    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and _role(request.user) in JUDGE_ROLES
        )


class IsOrganizer(BasePermission):
    """Organizer routes are for organizers and admins only."""

    message = "This route is for organizers only."

    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and _role(request.user) in ORGANIZER_ROLES
        )
