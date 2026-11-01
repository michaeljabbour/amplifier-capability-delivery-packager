#!/usr/bin/env python3
"""Local package closure checks, not behavioral or human-effectiveness validation."""
from pathlib import Path
import ast,json,re
ROOT=Path(__file__).resolve().parents[1]
manifest=json.loads((ROOT/'capability.json').read_text())
assert manifest['contract_version']=='amplifier-capability/v1'
assert manifest['version']=='0.1.0' and manifest['dependencies']['required']==[]
assert (ROOT/'LICENSE').read_text().startswith('MIT License')
for p in ROOT.rglob('*.py'):
 if '.git' not in p.parts:ast.parse(p.read_text(),filename=str(p))
portable=manifest.get('portable_method')
if portable:
 folder=ROOT/portable['path'];skill=folder/'SKILL.md';text=skill.read_text()
 assert text.startswith('---\n') and '\nname: '+portable['name']+'\n' in text
 assert (folder/'LICENSE').read_bytes()==(ROOT/'LICENSE').read_bytes()
 for link in re.findall(r'\]\(([^)]+)\)',text):
  if '://' not in link and not link.startswith('#'):
   target=(folder/link.split('#')[0]).resolve()
   assert target.is_relative_to(folder.resolve()) and target.is_file(),link
related=json.loads((ROOT/'docs/related.json').read_text())
assert 2<=len(related['sources'])<=4
assert all(c['url'].startswith('https://') for c in related['sources'])
print(json.dumps({'package':'passed','scope':'Source syntax, local portable reference closure, MIT and research metadata only.'}))
