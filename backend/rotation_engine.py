"""Prototype - STEP 2: propose 2 rotations from the previous crop.

CSV-DRIVEN engine:
  - data/base_agronomique/rotation_rules_moteur.csv : which rules are active, their mode (exclusion / score),
    their points and their parameter (columns engine_mode, engine_points, engine_param).
  - data/base_agronomique/crop_associations.csv     : associations table (rules R09, R10, R11).
  - config/engine_config.json             : weights of the axes and of the rotation.
The CONDITION of each rule (e.g. "same family") stays in the code; the CSV decides whether it is
applied, how, and with what intensity.

Usage:
    python rotation_engine.py --lat 34.0 --lon -5.0 --prev ble_dur
    python rotation_engine.py --lat 33.5 --lon -3.75 --prev tomate --prev2 ble_dur --length 2 --json sortie.json
    python rotation_engine.py --lat 34.0 --lon -5.0 --prev jachere_paturee      (case R11)
Status: never "recommande" (R13) - agronomic sources to be validated.
"""
import argparse
import itertools
import json
import math
import unicodedata
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
import sys
sys.path.insert(0, str(HERE))
MONTHS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
          "septembre", "octobre", "novembre", "décembre"]
CEREAL, LEGUME = "Poaceae", "Fabaceae"
JACHERE = "jachere_paturee"


def norm(x):
    return unicodedata.normalize("NFKD", str(x)).encode("ascii", "ignore").decode().strip().lower()


# ------------------------------------------------------------------ loading
def ensure_studies(studies, data):
    """Computes the studies from the CSV files (data/donnees_nasa + data/base_agronomique) if they are missing
    or older than the input CSV files. No result is written by hand."""
    studies, data = Path(studies), Path(data)
    donnees_nasa = data.parent / "donnees_nasa"
    needed = [studies / "etude1_3_profil_zones.csv", studies / "etude2_3_scores_culture_zone.csv"]
    inputs = list(donnees_nasa.glob("*.csv")) + [data / "base_cultures_Saiss_Fes.csv", data / "final_fao.csv"] if donnees_nasa.exists() else []
    missing = [f for f in needed if not f.exists()]
    stale = bool(inputs) and not missing and max(f.stat().st_mtime for f in inputs if f.exists()) > min(f.stat().st_mtime for f in needed)
    if not (missing or stale):
        return
    if not donnees_nasa.exists():
        raise SystemExit(f"Etudes absentes et dossier {donnees_nasa} introuvable : placez vos CSV NASA dans data/donnees_nasa/ (voir README.txt).")
    import run_studies
    print("Calcul des etudes a partir des CSV...")
    run_studies.run(donnees_nasa, data, studies, verbose=False)


class Engine:
    def __init__(self, studies, data, config):
        ensure_studies(studies, data)
        self.studies, self.data = Path(studies), Path(data)
        self.cfg = json.loads(Path(config).read_text(encoding="utf-8"))
        self.crops = pd.read_csv(self.data / "base_cultures_Saiss_Fes.csv", encoding="utf-8-sig").set_index("crop_name")
        # pseudo-crop usable only as a previous crop (rule R11)
        self.crops.loc[JACHERE] = {c: np.nan for c in self.crops.columns}
        self.crops.loc[JACHERE, ["botanical_family", "water_need", "planting_period", "crop_cycle_days"]] = ["Jachère", "faible", "", 0]
        rules = pd.read_csv(self.data / "rotation_rules_moteur.csv", encoding="utf-8-sig").set_index("rule_id")
        self.rules = rules
        self.override = {}          # temporary overrides decided by the GRACE supervisor
        self.poids_eau_zone = None
        self.assoc = pd.read_csv(self.data / "crop_associations.csv", encoding="utf-8-sig")
        self.scores = pd.read_csv(self.studies / "etude2_3_scores_culture_zone.csv")
        self.zones = pd.read_csv(self.studies / "etude1_3_profil_zones.csv")
        # caches (speed-up: avoids re-reading the pandas tables for every evaluated rotation)
        self._rows = {c: self.crops.loc[c] for c in self.crops.index}
        self._mode = self.rules["engine_mode"].to_dict()
        self._pts = self.rules["engine_points"].to_dict()
        self._par = self.rules["engine_param"].to_dict()
        self._assoc = list(self.assoc.itertuples(index=False))
        self._match_cache = {}

    # --- reading the rules from the CSV
    def mode(self, rid):
        return self.override.get(rid, self._mode[rid])

    def active(self, rid):
        return self.mode(rid) in ("exclusion", "score")

    def points(self, rid):
        return float(self._pts[rid])

    def param(self, rid, default=None):
        v = self._par[rid]
        return float(v) if pd.notna(v) and str(v) != "" else default

    def apply(self, rid, text, hard, soft):
        if not self.active(rid):
            return
        if self.mode(rid) == "exclusion":
            hard.append(f"{rid} : {text}")
        else:
            soft.append((rid, self.points(rid), text))

    # --- GRACE supervision layer (climate change)
    LEVELS = ["normal", "vigilance", "alerte", "critique"]

    def supervisor(self, zone_row):
        sg = self.cfg.get("superviseur_grace", {})
        level = zone_row.get("grace_niveau", None)
        if not sg.get("actif") or level not in self.LEVELS:
            self.override, self.grace_coef, self.force_limitee = {}, 0.0, False
            return dict(actif=False)
        self.grace_coef = float(sg["coef_poids_eau"][level])
        self.override = {}
        self.force_limitee = sg.get("ressource_limitee_a_partir_de") in self.LEVELS and \
            self.LEVELS.index(level) >= self.LEVELS.index(sg["ressource_limitee_a_partir_de"])
        seuil = sg.get("r05_exclusion_a_partir_de")
        if seuil in self.LEVELS and self.LEVELS.index(level) >= self.LEVELS.index(seuil):
            self.override["R05"] = "exclusion"
        return dict(actif=True, niveau=level, tendance_cm_an=round(float(zone_row.grace_tendance_cm_an), 2),
                    baisse_totale_cm=round(float(zone_row.grace_baisse_totale_cm), 1),
                    significative=bool(zone_row.grace_tendance_significative), coef_poids_eau=self.grace_coef,
                    r05=self.override.get("R05", "score"), message=sg["messages"][level])

    # --- zone and scores
    def nearest_zone(self, lat, lon):
        c = self.zones.zone.str.split("_", expand=True).astype(float)
        return self.zones.loc[((c[0] - lat) ** 2 + (c[1] - lon) ** 2).idxmin()]

    def crop_scores(self, zone):
        s = self.scores[self.scores.zone == zone].set_index("crop_name").copy()
        s["climat"] = 0.8 * s.s_temperature + 0.2 * s.s_altitude
        s = s.rename(columns={"s_eau": "eau", "s_sol": "sol", "score_rendement": "rendement"})
        W = dict(self.cfg["poids_axes_culture"])
        fr = self.cfg.get("fragilite_zone", {})
        frag = self.zones.loc[self.zones.zone == zone, "fragilite_zone"].iloc[0] if "fragilite_zone" in self.zones else np.nan
        boost = getattr(self, "grace_coef", 0.0)                # GRACE: supervision (climate change)
        if fr.get("actif") and pd.notna(frag):             # MODIS: fragile zone -> water weighs more
            boost += fr.get("coef_poids_eau", 0.5) * frag
        W["eau"] = W["eau"] * (1 + boost)
        self.poids_eau_zone = W["eau"] / sum(W.values())
        tot, miss = [], []
        for _, r in s.iterrows():
            av = {k: w for k, w in W.items() if pd.notna(r[k])}
            tot.append(sum(r[k] * w for k, w in av.items()) / sum(av.values()))
            miss.append([k for k in W if k not in av])
        s["score_culture"], s["axes_manquants"] = tot, miss
        return s

    # --- associations table (R09 / R10 / R11)
    def _match(self, kind, value, crop):
        key = (kind, value, crop)
        if key not in self._match_cache:
            row = self._rows[crop]
            if norm(kind) == "family":
                res = norm(row.botanical_family) == norm(value)
            else:
                res = norm(crop) == norm(value) or norm(crop).startswith(norm(value))
            self._match_cache[key] = res
        return self._match_cache[key]

    def association_rules(self, prev, nxt, r03_fired, hard, soft):
        for a in self._assoc:
            if not (self._match(a.predecessor_type, a.predecessor, prev) and self._match(a.successor_type, a.successor, nxt)):
                continue
            rel = norm(a.relation)
            if rel.startswith("favorable") or rel.startswith("tres favorable"):
                if not r03_fired:              # avoids counting the same relation twice (R03 = R09 legume -> cereal)
                    self.apply("R09", f"{prev} -> {nxt} favorable ({a.source})", hard, soft)
            elif rel.startswith("defavorable"):
                rid = "R11" if norm(a.predecessor) == JACHERE else "R10"
                self.apply(rid, f"{prev} -> {nxt} defavorable ({a.source})", hard, soft)

    # --- all the rules for a sequence
    def evaluate_rules(self, seq, start, eau_limitee):
        hard, soft = [], []
        for i in range(start, len(seq)):
            prev, nxt = seq[i - 1], seq[i]
            p, n = self._rows[prev], self._rows[nxt]
            if p.botanical_family == n.botanical_family:
                self.apply("R01", f"meme famille ({n.botanical_family}) : {prev} -> {nxt}", hard, soft)
            r03 = p.botanical_family == LEGUME and n.botanical_family == CEREAL
            if r03:
                self.apply("R03", f"legumineuse ({prev}) avant cereale ({nxt})", hard, soft)
            if p.water_need == "élevé" and n.water_need == "élevé" and eau_limitee:
                self.apply("R05", f"deux cultures tres gourmandes en eau ({prev} -> {nxt}) en zone a eau limitee", hard, soft)
            self.association_rules(prev, nxt, r03 and self.active("R03"), hard, soft)
        # R04: successive cereals
        limit, run = int(self.param("R04", 2)), 0
        for c in seq:
            run = run + 1 if self._rows[c].botanical_family == CEREAL else 0
            if run >= limit:
                self.apply("R04", f"{limit} cereales successives ou plus", hard, soft)
                break
        # R06: favour a low or medium water requirement
        if any(self._rows[c].water_need in ("faible", "moyen") for c in seq[start:]):
            self.apply("R06", "au moins une culture a besoin en eau faible ou moyen", hard, soft)
        return hard, soft

    # --- calendar: INFORMATIVE only (R08 not applied)
    @staticmethod
    def months_of(txt):
        return [MONTHS.index(m.strip().lower()) + 1 for m in str(txt).split(";") if m.strip().lower() in MONTHS]

    @staticmethod
    def harvest_month(sow, cycle):
        return (sow - 1 + math.ceil(min(cycle, 365) / 30)) % 12 + 1

    def timeline(self, h0, combo):
        h, out, bare = h0, [], 0
        for c in combo:
            opts = sorted((((m - h) % 12), m) for m in self.months_of(self._rows[c].planting_period))
            if not opts:
                out.append(dict(culture=c, semis=None, recolte=None, sol_nu_mois=None))
                continue
            gap, sow = opts[0]
            h = self.harvest_month(sow, self._rows[c].crop_cycle_days)
            bare += gap
            out.append(dict(culture=c, semis=MONTHS[sow - 1], recolte=MONTHS[h - 1], sol_nu_mois=gap))
        return out, bare

    # --- proposal
    def propose(self, lat, lon, prev, prev2=None, prev_month=None, length=2):
        z = self.nearest_zone(lat, lon)
        sup = self.supervisor(z)
        cs = self.crop_scores(z.zone)
        eau_limitee = bool(z.aridite < self.param("R05", 0.35) or getattr(self, "force_limitee", False)) if self.active("R05") else False
        base = ([prev2] if prev2 else []) + [prev]
        p = self._rows[prev]
        months = self.months_of(p.planting_period)
        sow0 = prev_month or (months[0] if months else 6)
        h0 = self.harvest_month(sow0, p.crop_cycle_days or 0)
        cmax = self.cfg["cycle_max_jours"]
        cand = [c for c in cs.index if self._rows[c].crop_cycle_days <= cmax]
        W, neutral = self.cfg, self.cfg["score_regles_neutre"]
        results, excl = [], 0
        for combo in itertools.product(cand, repeat=length):
            hard, soft = self.evaluate_rules(base + list(combo), len(base), eau_limitee)
            if hard:
                excl += 1
                continue
            rule_score = float(np.clip(neutral + sum(x[1] for x in soft), 0, 1))
            crop_part = float(np.mean([cs.loc[c, "score_culture"] for c in combo]))
            tl, bare = self.timeline(h0, combo)
            results.append(dict(sequence=list(combo), score=W["poids_cultures"] * crop_part + W["poids_regles"] * rule_score,
                                score_cultures=crop_part, score_regles=rule_score, sol_nu_mois=bare,
                                regles=[f"{i} ({pts:+.2f}) {t}" for i, pts, t in soft], calendrier=tl))
        results.sort(key=lambda r: -r["score"])
        chosen = []
        for r in results:                       # the 2nd proposal does not reuse any crop from the 1st
            if not chosen or not (set(r["sequence"]) & set(chosen[0]["sequence"])):
                chosen.append(r)
            if len(chosen) == self.cfg["nb_propositions"]:
                break
        info = dict(zone=z, superviseur=sup, eau_limitee=eau_limitee, n_evaluees=len(results), n_exclues=excl,
                    recolte_precedente=MONTHS[h0 - 1], regles_actives=[r for r in self.rules.index if self.active(r)])
        return chosen, info, cs

    def describe(self, r, cs):
        out = []
        for t in r["calendrier"]:
            c, row, al = t["culture"], cs.loc[t["culture"]], []
            if t["sol_nu_mois"] is not None:
                al.append(f"sol nu : {t['sol_nu_mois']} mois entre la recolte precedente et le semis (information)")
            else:
                al.append("donnee non disponible : periode de semis (R12)")
            if row.eau < 0.30:
                al.append(f"irrigation ~{row.irrigation_normal_mm:.0f} mm (annee normale), ~{row.irrigation_annee_seche_mm:.0f} mm (annee seche)")
            if row.s_texture < 0.7:
                al.append("texture du sol peu adaptee (R07)")
            if row.s_ph < 0.6:
                al.append(f"pH du sol eloigne de la plage de la culture (score {row.s_ph:.2f})")
            if row.axes_manquants:
                al.append("donnee non disponible : " + ", ".join(row.axes_manquants) + " (R12)")
            out.append(dict(culture=c, famille=self._rows[c].botanical_family, semis=t["semis"], recolte=t["recolte"],
                            sol_nu_mois=t["sol_nu_mois"], score=round(float(row.score_culture), 2),
                            detail={k: (None if pd.isna(row[k]) else round(float(row[k]), 2)) for k in self.cfg["poids_axes_culture"]},
                            alertes=al))
        return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lat", type=float, required=True)
    ap.add_argument("--lon", type=float, required=True)
    ap.add_argument("--prev", required=True, help="culture precedente (crop_name), ou jachere_paturee")
    ap.add_argument("--prev2", default=None)
    ap.add_argument("--prev-month", type=int, default=None)
    ap.add_argument("--length", type=int, default=2, choices=[1, 2, 3])
    ap.add_argument("--studies", default=str(HERE / "etudes_output"))
    ap.add_argument("--data", default=str(HERE / "data" / "base_agronomique"))
    ap.add_argument("--config", default=str(HERE / "config" / "engine_config.json"))
    ap.add_argument("--json", default=None)
    a = ap.parse_args()

    eng = Engine(a.studies, a.data, a.config)
    for c in (a.prev, a.prev2):
        if c and c not in eng.crops.index:
            raise SystemExit(f"Culture inconnue : {c}")
    chosen, info, cs = eng.propose(a.lat, a.lon, a.prev, a.prev2, a.prev_month, a.length)
    z = info["zone"]
    print(f"Zone la plus proche : {z.zone} | pluie {z.pluie_moy_mm:.0f} mm | aridite {z.aridite:.2f} | "
          f"ressource en eau {'LIMITEE' if info['eau_limitee'] else 'correcte'} | {int(z.nb_annees_seches)} annee(s) seche(s)/6")
    sup = info["superviseur"]
    if sup.get("actif"):
        print(f"SUPERVISEUR GRACE : niveau {sup['niveau'].upper()} | tendance {sup['tendance_cm_an']} cm/an "
              f"(baisse totale {sup['baisse_totale_cm']} cm) | R05 : {sup['r05']}")
        print(f"  {sup['message']}")
    if pd.notna(z.get("fragilite_zone", np.nan)):
        print(f"Fragilite de la zone (MODIS) : {z.fragilite_zone:.2f} (sensibilite NDVI/pluie {z.sensibilite_ndvi_pluie:.2f}) "
              f"-> poids de l'eau {eng.poids_eau_zone:.0%} au lieu de {eng.cfg['poids_axes_culture']['eau'] / sum(eng.cfg['poids_axes_culture'].values()):.0%}")
    print(f"Culture precedente : {a.prev}" + (f" (avant : {a.prev2})" if a.prev2 else "") + f" | recolte estimee : {info['recolte_precedente']}")
    print(f"Regles actives (CSV) : {', '.join(info['regles_actives'])}")
    print(f"Rotations evaluees : {info['n_evaluees']} (exclues par une regle d'exclusion : {info['n_exclues']})\n")
    out = []
    for i, r in enumerate(chosen, 1):
        det = eng.describe(r, cs)
        print(f"=== Proposition {i} : {a.prev} -> " + " -> ".join(r["sequence"]) +
              f"  | score {r['score']:.2f} (cultures {r['score_cultures']:.2f}, regles {r['score_regles']:.2f}) | sol nu total : {r['sol_nu_mois']} mois")
        for d in det:
            print(f"  - {d['culture']} ({d['famille']}) : semis {d['semis']}, recolte {d['recolte']} | score {d['score']} {d['detail']}")
            for al in d["alertes"]:
                print(f"      ! {al}")
        for rg in r["regles"]:
            print(f"  regle : {rg}")
        print("  statut : favorable sous reserve - A VALIDER par un agronome (R13)\n")
        out.append(dict(proposition=i, score=round(r["score"], 3), sol_nu_total_mois=r["sol_nu_mois"], cultures=det,
                        regles=r["regles"], statut="favorable sous réserve - à valider (R13)"))
    if a.json:
        Path(a.json).write_text(json.dumps(dict(zone=z.zone, precedente=a.prev, superviseur_grace=sup, propositions=out), ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
