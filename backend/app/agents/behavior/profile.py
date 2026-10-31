"""Explicit profile opt-in; the platform's default remains 1.0.0."""
from ..profiles import ProfileLoader, ProfileSource


def behavior_profile():
    return ProfileLoader(registry={('web-fastapi-angular', '1.1.0'): ProfileSource(
        'templates/profiles/web-fastapi-angular/1.1.0/profile.json',
        'templates/profiles/web-fastapi-angular/1.1.0/profile.schema.json')}).load('web-fastapi-angular', version='1.1.0')
