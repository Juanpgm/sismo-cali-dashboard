"""`backend/app/services/survey_link.py` — design.md ADR-6 of the
`planeacion-asignaciones` change; spec `getEnlaceSurvey builds a prefilled
Survey123 URL from configuration`.

Pure URL construction: `build_survey_urls` takes the form URL and the
optional field-app item id as ARGUMENTS, never reading `Settings()` itself,
so it is fully testable with no environment and no FastAPI dependency. The
caller (`routers/planeacion_asignaciones.py`'s `get_enlace_survey`) is the
one that reads configuration and decides to fail loud (503) when the form
URL is unset — this module never emits a placeholder or partial URL, it
simply is not called in that case.

Only `codigoapp` is ever prefilled — no other Survey123 question. Prefilling
content fields would fight the form's cascading logic and make the survey's
provenance ambiguous (design.md ADR-6's scope boundary).
"""
from __future__ import annotations

from urllib.parse import quote


def build_survey_urls(
    clave: str, *, form_url: str, field_app_item_id: str | None
) -> dict[str, str | None]:
    """`{'web': <url>, 'app': <url> | None}`.

    `web` appends `field:codigoapp=<clave>` (URL-encoded) to `form_url`,
    using `&` when `form_url` already carries a query string and `?`
    otherwise. `app` is the `arcgis-survey123:///` deep link, present only
    when `field_app_item_id` is configured.
    """
    encoded_clave = quote(clave, safe="")
    separator = "&" if "?" in form_url else "?"
    web = f"{form_url}{separator}field:codigoapp={encoded_clave}"
    app = (
        f"arcgis-survey123:///?itemID={field_app_item_id}&field:codigoapp={encoded_clave}"
        if field_app_item_id
        else None
    )
    return {"web": web, "app": app}
