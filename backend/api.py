# -*- coding: utf-8 -*-
"""
CULTIVA — FastAPI API that exposes the rotation engine (rotation_engine.Engine).

Two endpoints:
  GET /api/reference                      -> reference data (meta, zones, crops, axes, rules)
  GET /api/rotations?zone=&prev=&prev2=   -> rotations computed LIVE by the Python engine

The ../frontend folder (index.html) is served statically at the root "/".
Run:  uvicorn api:app --reload   (from the backend/ folder)
Then open http://127.0.0.1:8000/
"""
import os
import math
import re

import numpy as np
import pandas as pd
from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from rotation_engine import Engine, MONTHS

BASE = os.path.dirname(os.path.abspath(__file__))
FRONT = os.path.normpath(os.path.join(BASE, "..", "frontend"))

# --- engine initialised only once, at startup ---
eng = Engine(
    os.path.join(BASE, "etudes_output"),
    os.path.join(BASE, "data", "base_agronomique"),
    os.path.join(BASE, "config", "engine_config.json"),
)

PICK = ['ble_dur', 'ble_tendre', 'orge', 'avoine', 'mais', 'feve', 'lentille', 'tournesol',
        'colza', 'luzerne', 'pomme_de_terre', 'tomate', 'oignon', 'jachere_paturee']
_RE_RULE = re.compile(r'^(R\d+)\s*\(([+-][\d.]+)\)\s*(.*)$')


def _num(v, nd=None):
    if v is None:
        return None
    try:
        if isinstance(v, (list, dict)):
            return v
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(v, np.floating):
        v = float(v)
    if isinstance(v, np.integer):
        v = int(v)
    if nd is not None and isinstance(v, float):
        v = round(v, nd)
    return v


def _fmt_props(chosen, cs):
    out = []
    for r in chosen:
        regs = []
        for s in r["regles"]:
            m = _RE_RULE.match(s)
            if m:
                regs.append({"id": m.group(1), "pts": float(m.group(2)), "texte": m.group(3)})
            else:
                regs.append({"id": "", "pts": 0, "texte": s})
        cultures = eng.describe(r, cs)
        for cu in cultures:
            cu["score"] = _num(cu["score"], 2)
            cu["detail"] = {k: _num(v, 2) for k, v in cu["detail"].items()}
        out.append({
            "sequence": r["sequence"],
            "score": _num(r["score"], 3),
            "sol_nu_total_mois": _num(r["sol_nu_mois"]),
            "regles": regs,
            "cultures": cultures,
            "statut": "favorable sous réserve - à valider (R13)",
        })
    return out


def _build_reference():
    cfg = eng.cfg
    meta = {
        "source": "prototype_rotation_saiss (moteur Python, corrige)",
        "periode_donnees": "2019-2024", "version": "api-fastapi", "grille_deg": 0.5,
        "poids": {k: float(v) for k, v in cfg["poids_axes_culture"].items()},
    }
    zdf = pd.read_csv(os.path.join(BASE, "etudes_output", "etude1_3_profil_zones.csv"), encoding="utf-8-sig")
    zones = {}
    for _, r in zdf.iterrows():
        zid = str(r["zone"]); lat, lon = [float(x) for x in zid.split("_")]
        zones[zid] = {
            "lat": lat, "lon": lon,
            "pluie_mm": _num(r["pluie_moy_mm"], 0), "pluie_min_mm": _num(r["pluie_min_mm"], 0),
            "et0_mm": _num(r["et0_moy_mm"], 0), "aridite": _num(r["aridite"], 2), "t_moy": _num(r["t_moy"], 1),
            "annees_seches": _num(r["nb_annees_seches"]), "annee_seche": _num(r["annee_la_plus_seche"]),
            "sm_racine": _num(r["sm_racine"], 3), "ndvi_max": _num(r["ndvi_max"], 2),
            "fragilite": _num(r.get("fragilite_zone"), 2), "texture": str(r["texture"]), "ph": _num(r["ph"], 1),
            "elev_m": _num(r["elev_moy_m"], 0), "pente_deg": _num(r["pente_moy_deg"], 1),
            "grace": {"niveau": str(r["grace_niveau"]), "tendance_cm_an": _num(r["grace_tendance_cm_an"], 2),
                       "baisse_cm": _num(r["grace_baisse_totale_cm"], 1),
                       "significative": bool(r["grace_tendance_significative"])},
        }
    crops = {}
    for cname, row in eng.crops.iterrows():
        if cname == "jachere_paturee":
            crops[cname] = {"famille": "Jachère", "type": "jachère", "eau": "faible", "cycle": 0, "semis": []}
            continue
        semis = [m.strip() for m in str(row.get("planting_period", "")).split(";") if m.strip().lower() in MONTHS]
        crops[cname] = {"famille": str(row["botanical_family"]), "type": str(row.get("crop_type", "")),
                         "eau": str(row["water_need"]), "cycle": _num(row["crop_cycle_days"]), "semis": semis}
    axes = {}
    for zid, z in zones.items():
        zrow = eng.nearest_zone(z["lat"], z["lon"]); eng.supervisor(zrow); cs = eng.crop_scores(zrow.zone)
        axes[zid] = {c: {"eau": _num(cs.loc[c].get("eau"), 2), "sol": _num(cs.loc[c].get("sol"), 2),
                         "climat": _num(cs.loc[c].get("climat"), 2), "rend": _num(cs.loc[c].get("rendement"), 2),
                         "irr_n": _num(cs.loc[c].get("irrigation_normal_mm"), 0),
                         "irr_s": _num(cs.loc[c].get("irrigation_annee_seche_mm"), 0),
                         "score": _num(cs.loc[c].get("score_culture"), 2)} for c in cs.index}
    rdf = pd.read_csv(os.path.join(BASE, "data", "base_agronomique", "rotation_rules_moteur.csv"),
                      encoding="utf-8-sig").set_index("rule_id")
    regles = {rid: {"nom": str(r["rule_name"]), "effet": str(r["effect"]),
                     "statut": str(r["validation_status"]), "mode": str(r["engine_mode"])}
              for rid, r in rdf.iterrows()}
    return {"meta": meta, "zones": zones, "crops": crops, "axes": axes, "regles": regles}


REFERENCE = _build_reference()

app = FastAPI(title="CULTIVA API", version="1.0",
              description="Moteur de rotation des cultures (Saïss / Fès-Meknès) exposé en API.")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.get("/api/reference")
def reference():
    """Reference data (zones, crops, scores, rules). Static, computed at startup."""
    return REFERENCE


@app.get("/api/rotations")
def rotations(zone: str = Query(..., description="id de zone, ex. 34.000_-5.000"),
              prev: str = Query(..., description="culture précédente (crop_name)"),
              prev2: str = Query("", description="avant-dernière culture (facultatif)")):
    """Computes LIVE, with the Python engine, the 2 proposed rotations."""
    try:
        lat, lon = [float(x) for x in zone.split("_")]
    except Exception:
        raise HTTPException(400, "Paramètre 'zone' invalide (format attendu: lat_lon, ex. 34.000_-5.000)")
    if prev not in eng.crops.index:
        raise HTTPException(404, f"Culture inconnue: {prev}")
    p2 = prev2 if (prev2 and prev2 in eng.crops.index) else None
    chosen, info, cs = eng.propose(lat, lon, prev, p2, None, 2)
    return {
        "recolte_precedente": info["recolte_precedente"],
        "eau_limitee": bool(info["eau_limitee"]),
        "n_evaluees": int(info["n_evaluees"]),
        "n_exclues": int(info["n_exclues"]),
        "propositions": _fmt_props(chosen, cs),
    }


@app.get("/api/health")
def health():
    return {"ok": True, "zones": len(REFERENCE["zones"]), "cultures": len(REFERENCE["crops"])}


# Static frontend (index.html) served at the root. Must be mounted AFTER the /api routes.
if os.path.isdir(FRONT):
    app.mount("/", StaticFiles(directory=FRONT, html=True), name="frontend")
