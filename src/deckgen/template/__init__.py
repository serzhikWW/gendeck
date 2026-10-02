"""Профайлер шаблонов (ML2). Публичный вход: `TemplateProfilerImpl().profile(path) -> TemplateCatalog`.

Автовыбор адаптера: есть макеты по README организаторов (title*/section*/slide*/table*/last* + TITLE_FIELD)
-> ConventionAdapter, иначе ищется YAML-профиль -> ProfileAdapter, иначе TemplateError.
"""
from __future__ import annotations

from deckgen.contracts import TemplateCatalog

from .convention import ConventionAdapter, is_convention, open_presentation
from .errors import TemplateError
from .profile import ProfileAdapter, find_profile


class TemplateProfilerImpl:
    def profile(self, template_path: str) -> TemplateCatalog:
        prs = open_presentation(template_path)
        if is_convention(prs):
            return ConventionAdapter().profile(template_path)
        return ProfileAdapter().profile(template_path)


def profile(template_path: str) -> TemplateCatalog:
    return TemplateProfilerImpl().profile(template_path)


__all__ = ["TemplateProfilerImpl", "ConventionAdapter", "ProfileAdapter", "TemplateError",
           "profile", "find_profile"]
