"""Keep the edit baseline separate from the latest project shown by the workbench."""

from dataclasses import dataclass

from ..project.models import Project


def project_values(project):
    return entered_values(
        project.name,
        project.goal.get("description", ""),
        "\n".join(project.goal.get("success_criteria", [])),
    )


def entered_values(name, description, success):
    return name.strip(), description, tuple(s.strip() for s in success.splitlines() if s.strip())


@dataclass
class ProjectEdits:
    base: Project | None = None
    conflict: bool = False

    def dirty(self, values):
        return values != project_values(self.base) if self.base else any(values)

    def accept(self, project):
        self.base, self.conflict = project, False

    def observe(self, project, values):
        """Return whether fields may reload; never move a conflicting draft's CAS baseline."""
        if (
            self.base is None
            or self.base.project_id != project.project_id
            or not self.dirty(values)
        ):
            self.accept(project)
            return True
        if project.status == self.base.status and (
            project_values(project) == project_values(self.base)
            or values == project_values(project)
        ):
            # Source/build activity changed the project version but not these editable fields.
            self.accept(project)
        else:
            self.conflict = True
        return False
