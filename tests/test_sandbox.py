from pathlib import Path
import shutil
import pytest
from agent_foundry.sandbox import DockerSandbox, SandboxUnavailable


def test_sandbox_never_falls_back_to_host_execution(monkeypatch,tmp_path):
    monkeypatch.setattr(shutil,'which',lambda name:None)
    sandbox=DockerSandbox()
    marker=tmp_path/'should-not-exist'
    with pytest.raises(SandboxUnavailable):sandbox.run_python(f'open({str(marker)!r},"w").write("executed")')
    assert not marker.exists()


def test_container_command_has_explicit_boundaries_and_only_snapshot_mount(monkeypatch,tmp_path):
    monkeypatch.setattr(shutil,'which',lambda name:'/usr/bin/docker')
    command=DockerSandbox().build_command(tmp_path,'foundry-'+'a'*32)
    joined=' '.join(command)
    assert '--network none' in joined
    assert '--read-only' in command
    assert '--cap-drop ALL' in joined
    assert '--security-opt no-new-privileges' in joined
    assert '--pull never' in joined
    assert '--pids-limit 32' in joined
    assert '--memory 128m' in joined
    assert '--user 65534:65534' in joined
    assert sum(x=='--mount' for x in command)==1
    assert command[command.index('--mount')+1].endswith('dst=/input,readonly')
    assert not any(x in command for x in ['--privileged','--network=host','/var/run/docker.sock'])
    assert command[-3:]==['python','-I','-']


def test_sandbox_registration_requires_execution_permission_and_approval():
    tool=DockerSandbox().tool()
    assert tool.capability=='code.execute'
    assert tool.side_effects and not tool.cacheable


@pytest.mark.parametrize('image',['--privileged','python:3.13; echo bad','name\n--network=host'])
def test_image_cannot_inject_docker_options(image):
    with pytest.raises(ValueError):DockerSandbox(image)


def test_optional_real_container_isolation():
    sandbox=DockerSandbox(max_seconds=3)
    if not sandbox.available:pytest.skip('Docker is not installed; command boundaries tested without claiming runtime isolation')
    result=sandbox.run_python('from pathlib import Path\nprint(Path("/input/example.txt").read_text())\nprint(Path("/etc/passwd").exists())',{'example.txt':'synthetic input'})
    if result.content['exit_code']==125:pytest.skip('Docker daemon or preinstalled python:3.13-slim image is unavailable')
    assert result.content['exit_code']==0
    assert 'synthetic input' in result.content['stdout']
    assert result.metadata['network']=='none'

@pytest.mark.parametrize('name',['../outside.txt','/tmp/outside.txt','.env','nested/.token',''])
def test_snapshot_rejects_unapproved_paths_before_starting_a_process(monkeypatch,name):
    monkeypatch.setattr(shutil,'which',lambda executable:'/usr/bin/docker')
    def forbidden(*args,**kwargs):
        raise AssertionError('Unsafe snapshot must be rejected before subprocess creation')
    monkeypatch.setattr('subprocess.Popen',forbidden)
    with pytest.raises(ValueError):DockerSandbox().run_python('print("test")',{name:'synthetic'})
