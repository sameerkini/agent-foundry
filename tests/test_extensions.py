from pathlib import Path
import json
import pytest
from agent_foundry.extensions import Skills, PluginLoader
from agent_foundry.registry import ToolRegistry
from agent_foundry.types import Tool


def basic_tool(name='safe_lookup'):
    return Tool(name,'Read approved demo facts',{'type':'object','properties':{},'additionalProperties':False},lambda ctx,args:{'ok':True},capability='data.read')


def manifest(root, **updates):
    value={'name':'station','version':1,'skills':[],'tools':[{'factory':'safe_lookup','options':{}}]}
    value.update(updates)
    path=root/'plugin.json';path.write_text(json.dumps(value));return path


def test_skill_catalog_only_loads_approved_markdown_on_request(tmp_path):
    folder=tmp_path/'quality';folder.mkdir();(folder/'SKILL.md').write_text('---\ndescription: Preserve units\n---\nCheck units before comparing measurements.')
    (tmp_path/'not-code.py').write_text('raise Exception("not executed")')
    skills=Skills(tmp_path)
    assert skills.list()[0]['name']=='quality'
    assert skills.list()[0]['description']=='Preserve units'
    assert 'Check units' in skills.load('quality')
    with pytest.raises(ValueError):skills.load('../outside')
    with pytest.raises(ValueError):skills.load('not-code')


def test_skills_reject_symlink_escape_and_ambiguous_names(tmp_path):
    root=tmp_path/'approved';root.mkdir();outside=tmp_path/'outside.md';outside.write_text('Secret')
    (root/'escape.md').symlink_to(outside)
    with pytest.raises(ValueError):Skills(root).list()
    (root/'escape.md').unlink();(root/'same.md').write_text('One');(root/'same').mkdir();(root/'same/SKILL.md').write_text('Two')
    with pytest.raises(ValueError):Skills(root).list()


def test_plugin_publishes_only_approved_factory_tool_without_permission_escalation(tmp_path):
    (tmp_path/'instructions.md').write_text('Check observed facts.')
    path=manifest(tmp_path,skills=['instructions.md'])
    registry=ToolRegistry()
    result=PluginLoader(tmp_path).load(path,registry,{'safe_lookup':lambda options:basic_tool()})
    assert result['tools']==['safe_lookup']
    assert result['skills']==['instructions.md']
    assert registry.get('safe_lookup').capability=='data.read'
    assert registry.get('safe_lookup').provenance=='plugin:station:safe_lookup'


@pytest.mark.parametrize('tools',[
    [{'factory':'unknown'}],
    [{'factory':'safe_lookup','capability':'code.execute'}],
    [{'factory':'safe_lookup','options':{'capability':'code.execute'}}],
    [{'factory':'safe_lookup','options':{'nested':{'env':{'TOKEN':'value'}}}}],
])
def test_plugins_cannot_import_unknown_code_or_self_grant_capabilities(tmp_path,tools):
    registry=ToolRegistry()
    with pytest.raises(ValueError):PluginLoader(tmp_path).load(manifest(tmp_path,tools=tools),registry,{'safe_lookup':lambda options:basic_tool()})
    assert registry.names()==[]


def test_plugin_collision_does_not_partially_publish_bundle(tmp_path):
    registry=ToolRegistry();registry.register(basic_tool('existing'))
    factories={'new':lambda options:basic_tool('new'),'old':lambda options:basic_tool('existing')}
    path=manifest(tmp_path,tools=[{'factory':'new'},{'factory':'old'}])
    with pytest.raises(ValueError):PluginLoader(tmp_path).load(path,registry,factories)
    assert registry.names()==['existing']


def test_plugin_skills_cannot_traverse_or_escape_root(tmp_path):
    registry=ToolRegistry()
    with pytest.raises(ValueError):PluginLoader(tmp_path).load(manifest(tmp_path,skills=['../outside.md']),registry,{})
