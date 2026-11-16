"""A second, independent task demonstrating the same runtime without ASR."""
from flow_base.contracts import Workflow, Step
from flow_base.engine import Adapter as BaseAdapter, merge
from flow_base.runtime import atomic_copy


def copy_file(ctx, step, dependencies, target):
    atomic_copy(ctx.source, target)
    return target


class Adapter(BaseAdapter):
    handlers = {'file.copy': copy_file}

    def configuration(self, overrides):
        defaults = Workflow(id='file-copy', steps=[Step(id='copy', handler='file.copy')])
        config = Workflow.model_validate(merge(defaults.model_dump(), overrides))
        if [(s.id, s.handler, s.needs) for s in config.steps] != [('copy', 'file.copy', [])]:
            raise ValueError('This task accepts the single file.copy step only')
        return config
