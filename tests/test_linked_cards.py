import csv
import base64
import json
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import xml.etree.ElementTree as ET

from PIL import Image
import pytest

from mtg_pipeline.canonicalize import canonicalize, write_canonical_json
from mtg_pipeline.compare_versions import load_release, compare_cards
from mtg_pipeline.config import AppConfig
from mtg_pipeline.exporters.draftmancer import export_draftmancer, sheet_counts
from mtg_pipeline.exporters.cubecobra import export_cubecobra
from mtg_pipeline.exporters.cockatrice import export_cockatrice
from mtg_pipeline.exporters.mana_curve import analyze_mana_curve
from mtg_pipeline.exporters.print_pdf import _print_image_paths
from mtg_pipeline.linked_cards import physical_cards, image_cards, display_cards
from mtg_pipeline.models import SourceData
from mtg_pipeline.pipeline import PipelineContext
from mtg_pipeline.renderers.crucible import render_crucible_cards, CrucibleRendererClient, build_crucible_card
from mtg_pipeline.sources.sheet_differences import difference_formula
from mtg_pipeline.validation import validate_source


def source(kind="prepare"):
    return SourceData(set_metadata=dict(set_id="test",set_name="Test",set_code="PKM",version="0.3.0",draft_mode=True), card_rows=[
        dict(id="UG04",name="Tenacious Tangle",mana_cost="2G",mana_value=3,card_types="Creature",subtypes="Tangela",
             oracle_text="Reach\nThis creature enters prepared.",power="3",toughness="3",rarity="uncommon",
             render_profile="standard",link_id="UG04_linked",link_type=kind,archetype="A, B",__row_number=2),
        dict(id="UG04_linked",name="Crushing Vines",mana_cost="{2}{G}",card_types="Instant",
             oracle_text="Choose one —\n• Destroy target creature with flying.\n• Destroy target artifact.",
             record_type="linked_face",color_indicator="G",__row_number=3),
    ])


def jpeg(color="green"):
    buffer=BytesIO()
    Image.new("RGB",(100,140),color).save(buffer,format="JPEG")
    return buffer.getvalue()


def context(tmp_path, kind="prepare"):
    config=AppConfig()
    set_def,cards=canonicalize(source(kind),config)
    for card in image_cards(cards):
        path=tmp_path / f"{card.id}.png"
        Image.new("RGB",(200,140),"green").save(path)
        card.artwork_path=path.name
    return PipelineContext(config=config,set_definition=set_def,cards=cards,project_root=tmp_path,release_dir=Path("release"))


def test_canonical_links_share_number_and_metadata_but_not_cost_or_stats():
    data=source();data.card_rows[1]["mana_cost"]=""
    assert validate_source(data,AppConfig()).ok
    _,cards=canonicalize(data,AppConfig())
    parent,child=cards
    assert child.collector_number==parent.collector_number=="001"
    assert child.rarity=="uncommon" and child.archetype==["A","B"]
    assert child.mana_cost is None and child.power is None
    assert child.color_indicator==child.colors==["G"]
    assert len(physical_cards(cards))==len(image_cards(cards))==len(display_cards(cards))==1
    assert child.source_row_number==3


@pytest.mark.parametrize("problem",["missing","self","orphan","duplicate","cycle","multiple","unpaired","badtype","notface","oracle","creature","loyalty","defense","color"])
def test_invalid_linked_rows_are_rejected(problem):
    data=source();parent,child=data.card_rows
    if problem=="missing": parent["link_id"]="missing"
    if problem=="self": parent["link_id"]=parent["id"]
    if problem=="orphan": parent["link_id"]="";parent["link_type"]=""
    if problem=="duplicate": child["id"]=parent["id"]
    if problem=="cycle": child["link_id"]=parent["id"];child["link_type"]="prepare"
    if problem=="multiple": data.card_rows.append({**parent,"id":"UG05"})
    if problem=="unpaired": parent["link_type"]=""
    if problem=="badtype": parent["link_type"]="battle"
    if problem=="notface": child["record_type"]="card"
    if problem=="oracle": child["oracle_text"]=""
    if problem=="creature": child["card_types"]="Creature"
    if problem=="loyalty": child["card_types"]="Planeswalker"
    if problem=="defense": parent["card_types"]="Battle";parent["link_type"]="transform"
    if problem=="color": child["color_indicator"]="Purple"
    assert not validate_source(data,AppConfig()).ok


def test_prepared_render_child_edits_and_removal_invalidate_cache(tmp_path):
    ctx=context(tmp_path);parent,child=ctx.cards
    client=Mock(identity="runtime");client.render.return_value=jpeg()
    with patch("mtg_pipeline.renderers.crucible.CrucibleRendererClient",return_value=client):
        assert len(render_crucible_cards(ctx))==1
        first=parent.render_cache_key
        assert child.rendered_image_filename==parent.rendered_image_filename
        payload=client.render.call_args.args[0]
        assert payload["linkType"]=="prepare" and payload["linkedCard"]["name"]=="Crushing Vines"
        render_crucible_cards(ctx)
        assert client.render.call_count==1
        child.oracle_text="New spell effect"
        render_crucible_cards(ctx)
        assert parent.render_cache_key!=first and client.render.call_count==2
        ctx.cards=[parent];parent.link_id=parent.link_type=None
        render_crucible_cards(ctx)
        assert client.render.call_count==3 and "linkedCard" not in client.render.call_args.args[0]


def test_transform_back_art_and_missing_cached_back_invalidate_pair(tmp_path):
    ctx=context(tmp_path,"transform");parent,child=ctx.cards
    parent.card_types=["Battle"];parent.type_line="Battle - Siege";parent.battle_defense="4"
    client=Mock(identity="runtime");client.render_faces.return_value=(jpeg(),jpeg("blue"))
    with patch("mtg_pipeline.renderers.crucible.CrucibleRendererClient",return_value=client):
        paths=render_crucible_cards(ctx)
        assert len(paths)==2 and paths[1].name.endswith("-back.jpg")
        assert client.render_faces.call_args.args[0]["battleDefense"]=="4"
        assert child.render_cache_key==parent.render_cache_key
        render_crucible_cards(ctx)
        assert client.render_faces.call_count==1
        Image.new("RGB",(200,140),"red").save(tmp_path / child.artwork_path)
        render_crucible_cards(ctx)
        assert client.render_faces.call_count==2
        (tmp_path / child.rendered_image_path).unlink()
        render_crucible_cards(ctx)
        assert client.render_faces.call_count==3


def test_secondary_records_not_booster_cards_and_exports_preserve_faces(tmp_path):
    config=AppConfig();set_def,cards=canonicalize(source("transform"),config)
    assert sheet_counts(cards)["Uncommon"]==1
    config.draft.booster_layout={"layouts":{"Default":{"weight":1,"slots":{"Uncommon":1}}}}
    assert analyze_mana_curve(cards,config.draft.booster_layout)["card_asfan"]=={"UG04":1}
    output=export_draftmancer(set_def,cards,config,tmp_path / "draft.txt").read_text()
    definitions=json.loads(output.split("[CustomCards]\n")[1].split("\n[Settings]")[0])
    assert len(definitions)==1 and definitions[0]["back"]["name"]=="Crushing Vines"
    assert "Crushing Vines (PKM)" not in output
    cube=export_cubecobra(set_def,cards,config,tmp_path / "cube.csv")
    with cube.open() as handle: assert len(list(csv.DictReader(handle)))==1
    xml=ET.parse(export_cockatrice(set_def,cards,config,tmp_path / "cards.xml"))
    nodes=xml.findall("./cards/card")
    assert len(nodes)==2 and nodes[1].find("prop/side").text=="back"
    assert nodes[0].find("related").attrib=={"attach":"transform"}
    (tmp_path / "cards").mkdir()
    for card in cards: (tmp_path / "cards" / card.expected_rendered_filename).write_bytes(jpeg())
    config.print.copies_by_rarity["uncommon"]=2
    assert len(_print_image_paths(cards,tmp_path,config.print))==4
    cards[0].link_type="prepare"
    assert len(_print_image_paths(cards,tmp_path,config.print))==2


def test_add_remove_link_between_saved_versions(tmp_path):
    config=AppConfig();linked=source();ordinary=source();ordinary.card_rows=ordinary.card_rows[:1]
    ordinary.card_rows[0].update(link_id="",link_type="")
    for version,data in (("0.2.0",ordinary),("0.3.0",linked),("0.4.0",ordinary)):
        data.set_metadata["version"]=version
        set_def,cards=canonicalize(data,config)
        write_canonical_json(set_def,cards,tmp_path / version)
    before,during,after=[load_release(tmp_path / version) for version in ("0.2.0","0.3.0","0.4.0")]
    assert {item.id:item.status for item in compare_cards(before,during)}=={"UG04":"changed","UG04_linked":"added"}
    assert {item.id:item.status for item in compare_cards(during,after)}=={"UG04":"changed","UG04_linked":"removed"}
    assert all(item.status=="unchanged" for item in compare_cards(before,after))


def test_difference_formula_keys_on_ids_and_headers():
    formula=difference_formula()
    for side in ("left","right"):
        assert f'MATCH(key,{side}ids,0)' in formula
        assert f'MATCH(header,{side}headers,0)' in formula
    assert 'UNIQUE(TOCOL(VSTACK(leftids,rightids),1))' in formula
    assert 'UNIQUE(TOCOL(VSTACK(leftheaders,rightheaders),3))' in formula
    assert formula.count("(")==formula.count(")") and "Duplicate ID" in formula


@pytest.mark.parametrize("kind, count",[("prepare",1),("transform",2)])
def test_artwork_and_hosting_do_not_duplicate_prepared_cards(tmp_path,kind,count):
    from mtg_pipeline.steps import _artwork
    from mtg_pipeline.artwork.r2 import publish_rendered_cards
    ctx=context(tmp_path,kind)
    result=SimpleNamespace(warnings=[],generated_updates=[],artist_updates=[],output_files=[])
    with patch("mtg_pipeline.steps.ArtworkPreparer") as preparer:
        preparer.return_value.prepare.return_value=result
        _artwork(ctx)
        assert len(preparer.return_value.prepare.call_args.args[0])==count
    client=Mock(identity="runtime")
    client.render.return_value=jpeg()
    client.render_faces.return_value=(jpeg(),jpeg("blue"))
    with patch("mtg_pipeline.renderers.crucible.CrucibleRendererClient",return_value=client):
        render_crucible_cards(ctx)
    publisher=Mock()
    publisher.public_url.side_effect=lambda key: "https://cdn.example/"+key
    with patch("mtg_pipeline.artwork.r2.CloudflareR2Client",return_value=publisher):
        publish_rendered_cards(ctx.cards,ctx.set_definition,ctx.config,tmp_path / ctx.release_dir,lambda _:None)
    assert publisher.upload_file.call_count==count
    assert bool(ctx.cards[0].rendered_public_url==ctx.cards[1].rendered_public_url)==(kind=="prepare")


def test_missing_back_response_never_saves_a_successful_front(tmp_path):
    ctx=context(tmp_path,"transform")
    client=Mock(identity="runtime");client.render_faces.return_value=(jpeg(),None)
    with patch("mtg_pipeline.renderers.crucible.CrucibleRendererClient",return_value=client):
        with pytest.raises(RuntimeError,match="no back face"):
            render_crucible_cards(ctx)
    assert not (tmp_path / ctx.release_dir / "cards").exists()
    assert not (tmp_path / ".cache/mtg_crucible/renders").exists()


def test_bridge_sends_back_art_bytes_and_normalizes_landscape_jpeg(tmp_path):
    ctx=context(tmp_path,"transform")
    runtime=tmp_path / ".cache/mtg_crucible/runtime"
    (runtime / "dist").mkdir(parents=True)
    (runtime / "dist/index.js").write_text("module.exports={}")
    (runtime / "ready.json").write_text(json.dumps({"fingerprint":"test","repository":str((tmp_path / ctx.config.crucible.repository_path).resolve())}))
    landscape=BytesIO()
    Image.new("RGB",(140,100),"red").save(landscape,format="JPEG")
    response={"image":base64.b64encode(landscape.getvalue()).decode(),"back":base64.b64encode(jpeg()).decode()}
    with patch("mtg_pipeline.renderers.crucible.node_path",return_value="node"), patch("mtg_pipeline.renderers.crucible.subprocess.run",return_value=SimpleNamespace(returncode=0,stdout=json.dumps(response))) as run:
        client=CrucibleRendererClient(ctx.config,tmp_path)
        parent,child=ctx.cards
        payload=build_crucible_card(parent,ctx.set_definition,ctx.config)
        payload["linkType"]="transform"
        payload["linkedCard"]=build_crucible_card(child,ctx.set_definition,ctx.config)
        front,back=client.render_faces(payload,tmp_path / parent.artwork_path,tmp_path / child.artwork_path)
        assert Image.open(BytesIO(front)).size==(100,140) and back==jpeg()
        request=json.loads(run.call_args.kwargs["input"])
        assert base64.b64decode(request["linkedArtBase64"])==(tmp_path / child.artwork_path).read_bytes()
