"""Simple tests (without pytest): python tests/test_engine.py"""
import shutil
import sys
import tempfile
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from rotation_engine import Engine  # noqa: E402

CFG, STUDIES, DATA = ROOT / "config/engine_config.json", ROOT / "etudes_output", ROOT / "data/base_agronomique"


def test_pas_de_meme_famille_successive():
    eng = Engine(STUDIES, DATA, CFG)
    for prev in ["ble_dur", "tomate", "feve", "colza", "oignon"]:
        chosen, _, _ = eng.propose(34.0, -5.0, prev)
        assert len(chosen) == 2, prev
        for r in chosen:
            seq = [prev] + r["sequence"]
            fam = [eng.crops.loc[c].botanical_family for c in seq]
            assert all(a != b for a, b in zip(fam, fam[1:])), (prev, seq)
        assert not set(chosen[0]["sequence"]) & set(chosen[1]["sequence"])


def test_r03_non_double_avec_r09():
    eng = Engine(STUDIES, DATA, CFG)
    hard, soft = eng.evaluate_rules(["feve", "ble_dur"], 1, False)
    ids = [s[0] for s in soft]
    assert "R03" in ids and "R09" not in ids and not hard


def test_r11_jachere_medicago():
    eng = Engine(STUDIES, DATA, CFG)
    _, soft = eng.evaluate_rules([ "jachere_paturee", "medicago_annuelles"], 1, False)
    assert any(s[0] == "R11" for s in soft)


def test_le_csv_pilote_le_moteur():
    tmp = Path(tempfile.mkdtemp())
    shutil.copytree(DATA, tmp / "d")
    f = tmp / "d/rotation_rules_moteur.csv"
    df = pd.read_csv(f, encoding="utf-8-sig")
    df.loc[df.rule_id == "R01", "engine_mode"] = "non_appliquee"   # we disable R01
    df.to_csv(f, index=False, encoding="utf-8-sig")
    eng = Engine(STUDIES, tmp / "d", CFG)
    hard, _ = eng.evaluate_rules(["ble_dur", "orge"], 1, False)
    assert not hard                                                  # no more exclusion
    df.loc[df.rule_id == "R01", "engine_mode"] = "exclusion"
    df.to_csv(f, index=False, encoding="utf-8-sig")
    assert Engine(STUDIES, tmp / "d", CFG).evaluate_rules(["ble_dur", "orge"], 1, False)[0]


def test_fragilite_modis_augmente_le_poids_de_leau():
    import json
    eng = Engine(STUDIES, DATA, CFG)
    z = eng.zones.set_index("zone")
    fragile, robuste = z.fragilite_zone.idxmax(), z.fragilite_zone.idxmin()
    assert 0 <= z.fragilite_zone.min() and z.fragilite_zone.max() <= 1
    eng.crop_scores(fragile); w_fragile = eng.poids_eau_zone
    eng.crop_scores(robuste); w_robuste = eng.poids_eau_zone
    assert w_fragile > w_robuste
    cfg = json.loads(CFG.read_text(encoding="utf-8")); cfg["fragilite_zone"]["actif"] = False
    tmp = Path(tempfile.mkdtemp()) / "c.json"; tmp.write_text(json.dumps(cfg), encoding="utf-8")
    e2 = Engine(STUDIES, DATA, tmp)
    e2.crop_scores(fragile)
    assert abs(e2.poids_eau_zone - 0.30 / 0.90) < 1e-9          # disabled: base weights


def test_superviseur_grace():
    import json
    eng = Engine(STUDIES, DATA, CFG)
    z = eng.zones.set_index("zone")
    assert set(z.grace_niveau) <= {"normal", "vigilance", "alerte", "critique"}
    row = z.iloc[0].copy(); row["grace_niveau"] = "critique"
    sup = eng.supervisor(row)
    assert sup["actif"] and sup["r05"] == "exclusion" and eng.force_limitee
    h, _ = eng.evaluate_rules(["tomate", "pasteque"], 1, True)           # 2 very water-hungry crops: excluded
    assert h and h[0].startswith("R05")
    row["grace_niveau"] = "normal"
    sup = eng.supervisor(row)
    assert eng.override == {} and not eng.force_limitee
    h, s = eng.evaluate_rules(["tomate", "pasteque"], 1, False)
    assert not h
    cfg = json.loads(CFG.read_text(encoding="utf-8")); cfg["superviseur_grace"]["actif"] = False
    tmp = Path(tempfile.mkdtemp()) / "c.json"; tmp.write_text(json.dumps(cfg), encoding="utf-8")
    e2 = Engine(STUDIES, DATA, tmp); row["grace_niveau"] = "critique"
    assert e2.supervisor(row) == {"actif": False} and e2.override == {}


def test_r08_non_appliquee():
    eng = Engine(STUDIES, DATA, CFG)
    assert not eng.active("R08")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("OK", name)
