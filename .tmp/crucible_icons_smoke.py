from io import BytesIO
import base64
from pathlib import Path

from PIL import Image, ImageDraw

from mtg_pipeline.config import load_config
from mtg_pipeline.compare_versions import load_release
from mtg_pipeline.models import CardDefinition
from mtg_pipeline.renderers.crucible import CrucibleRendererClient, build_crucible_card

root = Path.cwd()
out = root / '.tmp/crucible-icons-preview'
out.mkdir(parents=True, exist_ok=True)
art = out / 'smoke-art.png'
Image.new('RGB', (600, 440), '#5b8c63').save(art)
config = load_config(root / 'config.example.yaml')
config.crucible.quality = 'medium'
release = load_release(root / 'releases/pokemon-mtg-set/0.2.0')
card = CardDefinition(id='UW-SMOKE', name='Set Icon Test', set_code='PKM', rarity='uncommon',
                      mana_cost='{1}{U}', card_types=['Creature'], subtypes=['Bird'],
                      power='2', toughness='2', collector_number='001', artist='Test')
payload = build_crucible_card(card, release.set_definition, config)
client = CrucibleRendererClient(config, root)
symbol = payload.pop('setSymbol')
builtin = client.render(payload, art)
(out / 'builtin.jpg').write_bytes(builtin)
icon = Image.new('RGBA', (100, 100), (0, 0, 0, 0))
ImageDraw.Draw(icon).ellipse((5, 5, 95, 95), fill='red', outline='black', width=4)
buffer = BytesIO()
icon.save(buffer, format='PNG')
payload['setSymbol'] = 'data:image/png;base64,' + base64.b64encode(buffer.getvalue()).decode('ascii')
custom = client.render(payload, art)
assert builtin != custom, 'Custom set symbol did not change the rendered card'
(out / 'custom.jpg').write_bytes(custom)
print('Local custom icon rendered successfully', flush=True)
payload['setSymbol'] = 'data:image/png;base64,bm90LWFuLWltYWdl'
try:
    client.render(payload, art)
except RuntimeError:
    print('Invalid custom icon correctly rejected', flush=True)
else:
    raise AssertionError('Invalid custom icon was silently accepted')
payload['setSymbol'] = symbol
remote = client.render(payload, art)
(out / 'pkm-uncommon.jpg').write_bytes(remote)
print('Configured PKM uncommon URL rendered successfully:', symbol, flush=True)
