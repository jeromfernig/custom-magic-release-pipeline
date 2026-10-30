import json
from pathlib import Path
from PIL import Image
from mtg_pipeline.config import load_config
from mtg_pipeline.canonicalize import canonicalize, write_canonical_json
from mtg_pipeline.sources.google_sheets import _parse_table_values, _parse_set_values
from mtg_pipeline.models import SourceData
from mtg_pipeline.validation import validate_source
from mtg_pipeline.pipeline import PipelineContext
from mtg_pipeline.renderers.crucible import render_crucible_cards
from mtg_pipeline.exporters.print_pdf import export_print_pdf
from mtg_pipeline.exporters.notes_pdf import export_notes_pdf
from mtg_pipeline.exporters.archetypes_pdf import export_archetypes_pdf

root=Path.cwd()
raw=json.loads((root / '.tmp/linked_live_values.json').read_text(encoding='utf-8'))
raw['copy']=[[value if value is not None else '' for value in row] for row in raw['copy']]
source=SourceData(set_metadata=_parse_set_values(raw['set']),card_rows=_parse_table_values(raw['copy']))
config=load_config(root / 'config.example.yaml');config.crucible.quality='medium'
validation=validate_source(source,config)
assert validation.ok, [issue.message for issue in validation.errors]
set_def,all_cards=canonicalize(source,config)
print('Live copy validation:',validation.card_count,'physical cards;',len(all_cards),'records',flush=True)
cards=[card for card in all_cards if card.id in {'UG04','UG04_linked'}]
parent=next(card for card in cards if card.id=='UG04')
parent.artwork_path=parent.artwork_url
out=root / '.tmp/linked-cards-preview/tangela'
ctx=PipelineContext(config=config,set_definition=set_def,cards=cards,project_root=root,release_dir=out)
print('Tangela images:',render_crucible_cards(ctx),flush=True)
write_canonical_json(set_def,cards,out)
export_print_pdf(set_def,cards,out,config.print,out / 'print.pdf')
export_notes_pdf(set_def,cards,out,config.print,out / 'notes.pdf')

art=root / '.tmp/linked-cards-preview/battle-art.png'
Image.new('RGB',(600,440),'#447755').save(art)
battle_source=SourceData(set_metadata=dict(set_id='test',set_name='Battle Test',set_code='PKM',version='0.3.0',draft_mode=True),card_rows=[
dict(id='RR01',name='Test Battle',mana_cost='3R',card_types='Battle',subtypes='Siege',oracle_text='When this battle enters, draw a card.',
     battle_defense='4',rarity='rare',render_profile='standard',link_id='RR01_linked',link_type='transform',archetype='Battle'),
dict(id='RR01_linked',name='Battle Reward',card_types='Creature',subtypes='Dragon',oracle_text='Flying',power='4',toughness='4',record_type='linked_face')])
assert validate_source(battle_source,config).ok
set_def,cards=canonicalize(battle_source,config)
for card in cards: card.artwork_path=art.relative_to(root).as_posix()
out=root / '.tmp/linked-cards-preview/battle'
ctx=PipelineContext(config=config,set_definition=set_def,cards=cards,project_root=root,release_dir=out)
print('Battle images:',render_crucible_cards(ctx),flush=True)
write_canonical_json(set_def,cards,out)
export_print_pdf(set_def,cards,out,config.print,out / 'print.pdf')
export_notes_pdf(set_def,cards,out,config.print,out / 'notes.pdf')
export_archetypes_pdf(set_def,cards,out,config.print,out / 'archetypes.pdf')
print('Linked-card smoke tests succeeded',flush=True)
