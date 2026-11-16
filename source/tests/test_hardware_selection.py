import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from flow_base import hardware, runtime as runtime_module
from flow_base.runtime import FlowError, Runtime
from flow_base.workspace import Manifest, Task
from ai_flow01.adapter import Adapter, resolve_environment


def environment(status='available', detected='detected'):
    return {'schema_version':1,'os':'Windows','machine':'AMD64',
            'gpu':{'status':detected,'devices':[],'backend_verified':False,
                   'cuda':{'status':status,'device_count':1 if status=='available' else 0}}}


def test_detector_handles_csv_names_and_unknown_memory():
    rows=hardware.parse_devices('"NVIDIA, Test GPU", 560.00, 8192, 4096\nOther, 560.00, N/A, N/A\n')
    assert rows[0]['name']=='NVIDIA, Test GPU'
    assert rows[0]['memory_total_bytes']==8192*1024**2 and rows[1]['memory_free_bytes'] is None


def test_driver_available_without_smi_is_detected(monkeypatch):
    monkeypatch.setattr(hardware,'nvidia_smi_path',lambda:None)
    calls=[]
    def run(args,timeout):
        calls.append((args,timeout));return json.dumps({'status':'available','device_count':1,'reason':'cuda_driver_api'})
    gpu=hardware.inspect_gpu(run)
    assert gpu['status']=='detected' and not gpu['backend_verified']
    assert calls[0][0][-1]=='--cuda-driver' and calls[0][1]==10


def test_failed_probe_stays_unknown(monkeypatch):
    monkeypatch.setattr(hardware,'nvidia_smi_path',lambda:None)
    def run(*a,**kw):raise FlowError('PROCESS_TIMEOUT','timeout')
    gpu=hardware.inspect_gpu(run)
    assert gpu['cuda']['status']=='unknown' and gpu['cuda']['reason']=='PROCESS_TIMEOUT'


@pytest.mark.parametrize('visible',['','-1'])
def test_environment_can_disable_cuda_before_loading_driver(monkeypatch,visible):
    monkeypatch.setattr(hardware.platform,'system',lambda:'Linux')
    monkeypatch.setenv('CUDA_VISIBLE_DEVICES',visible)
    monkeypatch.setattr(hardware.ctypes,'CDLL',lambda *_:pytest.fail('disabled driver must not be loaded'))
    assert hardware.cuda_driver_probe()['status']=='disabled'


def test_smi_failure_does_not_hide_working_cuda_driver(monkeypatch):
    monkeypatch.setattr(hardware,'nvidia_smi_path',lambda:'smi')
    def run(args,timeout):
        if args[0]=='smi':raise FlowError('PROCESS_FAILED','smi broken')
        return json.dumps({'status':'available','device_count':1})
    result=hardware.inspect_gpu(run)
    assert result['status']=='detected' and result['diagnostics'][0]['error']=='PROCESS_FAILED'


@pytest.mark.parametrize('status,detected,device,reason',[
    ('available','detected','cuda','cuda_candidate_detected'),
    ('unavailable','not_detected','cpu','cuda_not_detected'),
    ('disabled','detected','cpu','cuda_disabled_by_environment'),
    ('unknown','detected','cuda','cuda_candidate_detected'),
])
def test_flow_selects_profile_from_host_snapshot(status,detected,device,reason,monkeypatch):
    import ai_flow01.adapter as module
    monkeypatch.setattr(module,'inspect_environment',lambda *_:pytest.fail('flow selection must not probe'))
    flow=Adapter().configuration({})
    assert flow.profile==flow.options.device=='auto'
    resolved=resolve_environment(flow,environment(status,detected))
    assert resolved.profile=='asr-'+device and resolved.options.device==device
    assert resolved.execution_selection['reason']==reason and not resolved.execution_selection['backend_verified']
    assert flow.profile=='auto'  # host resolution returns a separate configuration


def test_unknown_detection_never_silently_becomes_cpu():
    with pytest.raises(FlowError) as exc:
        resolve_environment(Adapter().configuration({}),environment('unknown','not_detected'))
    assert exc.value.code=='HARDWARE_UNCERTAIN'


@pytest.mark.parametrize('config,device',[
    ({'options':{'device':'cpu'}},'cpu'),
    ({'profile':'asr-cpu'},'cpu'),
    ({'profile':'asr-cuda'},'cuda'),
    ({'options':{'device':'cuda'}},'cuda'),
])
def test_explicit_choice_has_priority(config,device):
    result=resolve_environment(Adapter().configuration(config),environment())
    assert result.options.device==device and result.execution_selection['reason']=='explicit_setting'


def test_conflicting_profile_and_device_are_rejected():
    with pytest.raises(FlowError) as exc:
        resolve_environment(Adapter().configuration({'profile':'asr-cuda','options':{'device':'cpu'}}),environment())
    assert exc.value.code=='DEVICE_PROFILE_CONFLICT'


def test_host_passes_snapshot_through_optional_generic_hook(tmp_path,monkeypatch):
    manifest=Manifest(id='example',version='1.0.0',title='Example',description='Generic',
                      package='example.whl',sha256='0'*64,keywords=['example'])
    task=Task(tmp_path,manifest)
    snapshot={'schema_version':1,'gpu':{'status':'unknown'}}
    observed=[]
    task._adapter=SimpleNamespace(configuration=lambda values:values,
        resolve_environment=lambda config,env:observed.append(env) or {'resolved':config})
    monkeypatch.setattr(runtime_module,'inspect_environment',lambda *_:snapshot)
    assert task.configuration(Runtime(tmp_path/'state'),{'x':1})=={'resolved':{'x':1}}
    assert observed==[snapshot]


def test_hardware_memory_fluctuations_do_not_invalidate_asr_cache():
    adapter=Adapter();flow=resolve_environment(adapter.configuration({}),environment())
    a={'device':'cuda','compute_type':'float16','execution_selection':{'memory_free':1}}
    b=copy.deepcopy(a);b['execution_selection']['memory_free']=2
    assert adapter.step_settings(flow,'speech.transcribe',a)==adapter.step_settings(flow,'speech.transcribe',b)


def test_packaged_default_and_sample_both_choose_auto():
    root=Path(__file__).resolve().parents[1]/'ai_flow01'
    template=json.loads((root/'flow.template.json').read_text())['defaults']
    example=json.loads((root/'settings.example.json').read_text())
    for config in (template,example):
        assert config['profile']==config['options']['device']=='auto'
