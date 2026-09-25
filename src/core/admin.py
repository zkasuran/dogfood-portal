"""Admin registrations. The Django admin doubles as an organizer console
for the offline demo, so the core tables are all browsable."""
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import (
    AuditLog,
    Comment,
    Criterion,
    Event,
    JudgeAssignment,
    Membership,
    Prize,
    Project,
    Rubric,
    Score,
    ScoreValue,
    Team,
    Track,
    User,
    Vote,
)


@admin.register(User)
class DogfoodUserAdmin(UserAdmin):
    list_display = ("username", "email", "role", "external_id", "is_staff")
    list_filter = ("role", "is_staff", "is_superuser")
    fieldsets = UserAdmin.fieldsets + (
        ("DOGFOOD", {"fields": ("role", "external_id", "display_name")}),
    )


class CriterionInline(admin.TabularInline):
    model = Criterion
    extra = 0


class ScoreValueInline(admin.TabularInline):
    model = ScoreValue
    extra = 0


@admin.register(Rubric)
class RubricAdmin(admin.ModelAdmin):
    inlines = [CriterionInline]


@admin.register(Score)
class ScoreAdmin(admin.ModelAdmin):
    list_display = ("judge", "project", "created_at")
    list_filter = ("judge",)
    inlines = [ScoreValueInline]


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ("external_id", "title", "team", "track", "status", "is_duplicate")
    list_filter = ("status", "is_duplicate", "track")
    search_fields = ("title", "summary", "external_id")


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("at", "actor", "action", "target")
    list_filter = ("action",)


admin.site.register(Event)
admin.site.register(Prize)
admin.site.register(Track)
admin.site.register(Team)
admin.site.register(Membership)
admin.site.register(JudgeAssignment)
admin.site.register(Criterion)
admin.site.register(Vote)
admin.site.register(Comment)
