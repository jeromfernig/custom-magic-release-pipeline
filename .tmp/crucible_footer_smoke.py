from pathlib import Path
from PIL import Image, ImageChops
from mtg_pipeline.config import load_config
from mtg_pipeline.models import CardDefinition, SetDefinition
from mtg_pipeline.renderers.crucible import CrucibleRendererClient, build_crucible_card

root = Path.cwd()
out = root / '.tmp/crucible-footer-preview'
out.mkdir(parents=True, exist_ok=True)
art = out / 'art.png'
Image.new('RGB', (600, 440), '#5b8c63').save(art)
config = load_config(root / 'config.example.yaml')
config.crucible.quality = 'medium'
set_def = SetDefinition(set_id='test', set_name='Test', set_code='PKM', version='0.3.0', draft_mode=False)
card = CardDefinition(id='CU07', name='Draft Footer Test', set_code='PKM', rarity='common',
                      mana_cost='{1}{U}', card_types=['Creature'], subtypes=['Bird'],
                      power='2', toughness='2', collector_number='042', artist='Test')
client = CrucibleRendererClient(config, root)
normal = client.render(build_crucible_card(card, set_def, config), art)
(out / 'non-draft.jpg').write_bytes(normal)
set_def.draft_mode = True
draft = client.render(build_crucible_card(card, set_def, config), art)
(out / 'draft.jpg').write_bytes(draft)
assert normal != draft, 'Draft footer did not change image'
with Image.open(out / 'draft.jpg') as image:
    image.crop((25, 950, 370, 1035)).resize((1035, 255)).save(out / 'footer.png')
print('Draft and non-draft renders succeeded; preview:', out / 'draft.jpg')
