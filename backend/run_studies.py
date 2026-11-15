"""Prototype - STEP 1: the studies (climate, water, soil, yield, GRACE).

Usage (from this folder):
    python run_studies.py            # reads data/donnees_nasa and data/base_agronomique, writes to etudes_output/

Everything is a prototype ESTIMATE. The assumptions are in HYP (to be adjusted).
Each study produces a score between 0 and 1; no value is invented:
if the data is missing, the score is NaN (it will be excluded from the final combination).
"""
import argparse
import math
from pathlib import Path

import numpy as np
import pandas as pd

# ---------------------------------------------------------------- assumptions
HYP = {
    # average Kc per water-requirement category (generic FAO estimate)
    "kc": {"faible": 0.50, "moyen": 0.80, "élevé": 1.05},
    "eff_rain": 0.80,        # share of rainfall that is useful for the crop
    "ph_slope": 1.5,         # score = 0 when the pH is 1.5 units outside the range
    "temp_slope": 10.0,      # monthly score = 0 at 10 degC outside the range
    "alt_slope": 500.0,      # score = 0 at 500 m outside the range
    "dry_year_ratio": 0.80,  # dry year if rainfall < 80 % of the mean
    "grace_rayon_deg": 1.0,                  # GRACE: grid points kept around each zone (deg)
    "grace_seuils_cm_an": (-0.5, -1.5, -2.5),  # storage trend: watch / alert / critical (cm per year)
    "grace_signif": 2.0,                     # |trend| / standard error >= 2 to speak of a significant trend
    "fragilite_ref": 0.50,   # NDVI/rainfall sensitivity corresponding to a fragility of 1 (maximum observed on the grid)
}
MONTH_DAYS = 30

# ---------------------------------------------------------------- utilities
def clip01(x):
    return float(max(0.0, min(1.0, x)))


def zone_id(lat, lon):
    return f"{lat:.3f}_{lon:.3f}"


def extraterrestrial_radiation(lat_deg, doy):
    """Ra in MJ/m2/day (FAO-56)."""
    phi = math.radians(lat_deg)
    dr = 1 + 0.033 * math.cos(2 * math.pi * doy / 365)
    delta = 0.409 * math.sin(2 * math.pi * doy / 365 - 1.39)
    ws = math.acos(max(-1, min(1, -math.tan(phi) * math.tan(delta))))
    return (24 * 60 / math.pi) * 0.0820 * dr * (
        ws * math.sin(phi) * math.sin(delta)
        + math.cos(phi) * math.cos(delta) * math.sin(ws)
    )


def et0_hargreaves(row):
    ra = extraterrestrial_radiation(row.LAT, row.doy) * 0.408  # mm/day
    return max(0.0, 0.0023 * (row.T2M + 17.8) * math.sqrt(max(row.T2M_MAX - row.T2M_MIN, 0)) * ra)


def texture_class(sand, silt, clay):
    """Simplified texture class -> vocabulary of the crop database."""
    if clay >= 40:
        return "argileux"
    if sand >= 85:
        return "sableux"
    if clay >= 27 and sand >= 45:
        return "argilo_sableux"
    if clay >= 27:
        return "argilo_limoneux"      # = limono_argileux (same class, 2 names)
    if clay >= 20 and sand >= 45:
        return "argilo_sableux"
    if sand >= 50:
        return "sablo_limoneux"
    return "limoneux"


# "proximity" order of the textures for the score (prototype)
TEX_POS = {"sableux": 0, "sablo_limoneux": 1, "argilo_sableux": 1.5, "limoneux": 2,
           "limono_argileux": 3, "argilo_limoneux": 3, "argileux": 4}


def texture_score(zone_tex, crop_tex_list):
    z = TEX_POS[zone_tex]
    best = min(abs(z - TEX_POS[t]) for t in crop_tex_list)
    return clip01(1 - 0.3 * best)       # 1 / 0.7 / 0.4 / 0.1 ...


def range_score(value, lo, hi, slope):
    if lo <= value <= hi:
        return 1.0
    dist = lo - value if value < lo else value - hi
    return clip01(1 - dist / slope)


# ---------------------------------------------------------------- STUDY 1: climate
def study_climate(donnees_nasa):
    p = pd.read_csv(donnees_nasa / "NASA_POWER_Fes_Meknes_2019_2024.csv", parse_dates=["DATE"])
    p["doy"] = p.DATE.dt.dayofyear
    p["ET0"] = p.apply(et0_hargreaves, axis=1)
    p["zone"] = [zone_id(a, b) for a, b in zip(p.LAT, p.LON)]
    gpm = pd.read_csv(donnees_nasa / "GPM_IMERG_Fes_Meknes_2019_2024.csv")
    gpm["zone"] = [zone_id(a, b) for a, b in zip(gpm.LAT, gpm.LON)]
    smap = pd.read_csv(donnees_nasa / "SMAP_Fes_Meknes_2019_2024 (1).csv" if (donnees_nasa / "SMAP_Fes_Meknes_2019_2024 (1).csv").exists()
                       else donnees_nasa / "SMAP_Fes_Meknes_2019_2024.csv")
    smap["zone"] = [zone_id(a, b) for a, b in zip(smap.LAT, smap.LON)]
    modis = pd.read_csv(donnees_nasa / "MODIS_NDVI_EVI_Fes_Meknes_2019_2024.csv")
    modis["zone"] = [zone_id(a, b) for a, b in zip(modis.LAT, modis.LON)]

    yearly = p.groupby(["zone", "YEAR"]).agg(
        pluie_mm=("PRECTOTCORR", "sum"), et0_mm=("ET0", "sum"),
        t_moy=("T2M", "mean"), jours_chauds=("T2M_MAX", lambda s: int((s >= 35).sum())),
        jours_gel=("T2M_MIN", lambda s: int((s <= 0).sum()))).reset_index()
    yearly = yearly.merge(gpm.groupby(["zone", "YEAR"]).GPM_IMERG_PRECIP.sum().rename("pluie_gpm_mm").reset_index())
    yearly = yearly.merge(smap.groupby(["zone", "YEAR"]).SM_ROOTZONE.mean().rename("sm_racine").reset_index())
    yearly = yearly.merge(modis.groupby(["zone", "YEAR"]).NDVI.max().rename("ndvi_max").reset_index())
    mean_rain = yearly.groupby("zone").pluie_mm.transform("mean")
    yearly["ratio_pluie"] = yearly.pluie_mm / mean_rain
    yearly["annee_seche"] = yearly.ratio_pluie < HYP["dry_year_ratio"]
    yearly["indice_aridite"] = yearly.pluie_mm / yearly.et0_mm      # P / ET0

    zones = yearly.groupby("zone").agg(
        pluie_moy_mm=("pluie_mm", "mean"), pluie_min_mm=("pluie_mm", "min"),
        et0_moy_mm=("et0_mm", "mean"), aridite=("indice_aridite", "mean"),
        t_moy=("t_moy", "mean"), nb_annees_seches=("annee_seche", "sum"),
        sm_racine=("sm_racine", "mean"), ndvi_max=("ndvi_max", "mean")).reset_index()
    zones["annee_la_plus_seche"] = yearly.loc[yearly.groupby("zone").pluie_mm.idxmin()].set_index("zone").YEAR.reindex(zones.zone).values
    # --- MODIS: zone fragility = how much the actual vegetation depends on rainfall
    # hydrological year (Sept -> Aug); annual max NDVI and annual rainfall, each relative to the zone mean
    modis["H"] = modis.YEAR + (modis.MO >= 9).astype(int)
    p["H"] = p.DATE.dt.year + (p.DATE.dt.month >= 9).astype(int)
    nh = modis.groupby(["zone", "H"]).NDVI.max().rename("ndvi").reset_index()
    rh = p.groupby(["zone", "H"]).PRECTOTCORR.sum().rename("pluie").reset_index()
    fh = nh.merge(rh, on=["zone", "H"])
    d0, d1 = p.DATE.min(), p.DATE.max()                      # only COMPLETE hydrological years (Sept -> Aug) are kept
    sept1 = pd.Timestamp(d0.year, 9, 1)
    h_first = (d0.year if d0 <= sept1 else d0.year + 1) + 1          # 1st hydrological year that starts after the beginning of the data
    h_last = d1.year if d1 >= pd.Timestamp(d1.year, 8, 31) else d1.year - 1
    fh = fh[fh.H.between(h_first, h_last)]
    fh["ndvi_rel"] = fh.ndvi / fh.groupby("zone").ndvi.transform("mean")
    fh["pluie_rel"] = fh.pluie / fh.groupby("zone").pluie.transform("mean")
    sens = fh.groupby("zone").apply(lambda g: np.polyfit(g.pluie_rel, g.ndvi_rel, 1)[0] if len(g) >= 4 else np.nan)
    zones["sensibilite_ndvi_pluie"] = zones.zone.map(sens)
    zones["fragilite_zone"] = zones.sensibilite_ndvi_pluie.apply(lambda v: clip01(v / HYP["fragilite_ref"]) if v == v else np.nan)
    # zone climate score (0 = very dry, 1 = favourable): aridity P/ET0, 0.6 = very favourable
    zones["score_climat_zone"] = zones.aridite.apply(lambda a: clip01(a / 0.6))

    # monthly climatology (mm/day, degC) for the normal scenario and the driest year
    p["M"] = p.DATE.dt.month
    clim = {}
    for z, g in p.groupby("zone"):
        normal = g.groupby("M").agg(P=("PRECTOTCORR", "mean"), ET0=("ET0", "mean"), T=("T2M", "mean"))
        dry_year = int(zones.loc[zones.zone == z, "annee_la_plus_seche"].iloc[0])
        gd = g[g.YEAR == dry_year].groupby("M").agg(P=("PRECTOTCORR", "mean"), ET0=("ET0", "mean"), T=("T2M", "mean"))
        clim[z] = {"normal": normal, "sec": gd}
    return zones, yearly, clim


# ---------------------------------------------------------------- STUDY 2: water requirement
def parse_months(txt):
    names = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
             "septembre", "octobre", "novembre", "décembre"]
    return [names.index(m.strip().lower()) + 1 for m in str(txt).split(";") if m.strip().lower() in names]


def crop_water_balance(crop, clim_zone):
    """For each sowing month, ETc vs useful rainfall balance over the cycle (annualised to 365 days)."""
    kc = HYP["kc"][crop.water_need]
    cycle = int(min(crop.crop_cycle_days, 365))
    out = {}
    for scen in ("normal", "sec"):
        tab = clim_zone[scen]
        etc_tot, covered_tot, tmp_scores = 0.0, 0.0, []
        starts = parse_months(crop.planting_period) or [1]
        for s in starts:
            etc_s = cov_s = 0.0
            for d in range(0, cycle, MONTH_DAYS):
                m = (s - 1 + d // MONTH_DAYS) % 12 + 1
                days = min(MONTH_DAYS, cycle - d)
                if m not in tab.index:
                    continue
                etc = kc * tab.loc[m, "ET0"] * days
                peff = HYP["eff_rain"] * tab.loc[m, "P"] * days
                etc_s += etc
                cov_s += min(etc, peff)
                if scen == "normal":
                    tmp_scores.append(range_score(tab.loc[m, "T"], crop.temperature_min_c,
                                                  crop.temperature_max_c, HYP["temp_slope"]))
            etc_tot += etc_s
            covered_tot += cov_s
        n = len(starts)
        out[scen] = dict(etc=etc_tot / n, couv=(covered_tot / etc_tot) if etc_tot else np.nan,
                         irrig=(etc_tot - covered_tot) / n)
        if scen == "normal":
            out["temp"] = float(np.mean(tmp_scores)) if tmp_scores else np.nan
    return out


# ---------------------------------------------------------------- STUDY 3: soil / relief
def study_soil(donnees_nasa, zones):
    # SoilGrids file in standard units (nitrogen corrected: g/kg); the original file is no longer read
    sg = pd.read_csv(donnees_nasa / "SOILGRIDS_Fes_Meknes_standard.csv")
    sg = sg.rename(columns={"lat": "LAT", "lon": "LON", "ph_eau": "PH", "argile_pct": "ARGILE_PCT",
                            "sable_pct": "SABLE_PCT", "limon_pct": "LIMON_PCT",
                            "carbone_org_g_kg": "CARBONE_ORG_G_KG", "azote_total_g_kg": "AZOTE_G_KG",
                            "pct_cultive": "PCT_CULTIVE"})
    sg["zone"] = [zone_id(a, b) for a, b in zip(sg.LAT, sg.LON)]
    sg["texture"] = [texture_class(a, b, c) for a, b, c in zip(sg.SABLE_PCT, sg.LIMON_PCT, sg.ARGILE_PCT)]
    srtm = pd.read_csv(donnees_nasa / "SRTM_Fes_Meknes.csv")
    coords = zones.zone.str.split("_", expand=True).astype(float)
    zones = zones.assign(LAT=coords[0].values, LON=coords[1].values)
    # soil: exact match; otherwise nearest SoilGrids point
    def nearest(df, lat, lon):
        d = (df.LAT - lat) ** 2 + (df.LON - lon) ** 2
        return df.loc[d.idxmin()]
    rows = []
    for _, z in zones.iterrows():
        s = nearest(sg, z.LAT, z.LON)
        r = nearest(srtm, z.LAT, z.LON)
        rows.append(dict(zone=z.zone, texture=s.texture, ph=s.PH, argile=s.ARGILE_PCT, sable=s.SABLE_PCT,
                         limon=s.LIMON_PCT, carbone_org=s.CARBONE_ORG_G_KG, azote=s.AZOTE_G_KG,
                         pct_cultive=s.PCT_CULTIVE, elev_moy_m=r.ELEV_MOY_M, pente_moy_deg=r.PENTE_MOY_DEG))
    return zones.merge(pd.DataFrame(rows), on="zone")


# ---------------------------------------------------------------- STUDY 4: yield
def study_yield(base_agronomique, crops):
    f = pd.read_csv(base_agronomique / "final_fao.csv", encoding="utf-8-sig")
    g = f.dropna(subset=["area_harvested_ha", "production_t"]).groupby(["crop_id", "year"]).agg(
        area=("area_harvested_ha", "sum"), prod=("production_t", "sum"),
        groupe=("is_grouped", "max")).reset_index()
    g["yield_kg_ha"] = g["prod"] * 1000 / g.area
    rows = []
    for cid, d in g.groupby("crop_id"):
        d = d.sort_values("year")
        slope = np.polyfit(d.year, d.yield_kg_ha, 1)[0] if len(d) >= 3 else np.nan
        rows.append(dict(crop_id=cid, annees=len(d), rendement_moy_kg_ha=d.yield_kg_ha.mean(),
                         cv=d.yield_kg_ha.std() / d.yield_kg_ha.mean() if len(d) > 1 else np.nan,
                         tendance_pct_an=100 * slope / d.yield_kg_ha.mean() if slope == slope else np.nan,
                         rendement_derniere_annee=d.yield_kg_ha.iloc[-1],
                         donnee_groupee=bool(d.groupe.any())))
    y = crops[["crop_id", "crop_name", "crop_type"]].merge(pd.DataFrame(rows), on="crop_id", how="left")
    # score: rank of the yield within the crop TYPE (comparing wheat and tomato makes no sense)
    def rank_in_type(s):
        if s.notna().sum() < 2:
            return s.apply(lambda v: 0.5 if v == v else np.nan)
        return (s - s.min()) / (s.max() - s.min())
    y["rang_rendement"] = y.groupby("crop_type").rendement_moy_kg_ha.transform(rank_in_type)
    y["stabilite"] = y.cv.apply(lambda c: clip01(1 - c) if c == c else np.nan)
    y["score_rendement"] = 0.7 * y.rang_rendement + 0.3 * y.stabilite
    y["fiabilite"] = np.where(y.rendement_moy_kg_ha.isna(), "non disponible",
                              np.where(y.donnee_groupee, "faible (groupe FAO)", "ok"))
    return y


# ---------------------------------------------------------------- STUDY 5: GRACE
def study_grace(donnees_nasa):
    g = pd.read_csv(donnees_nasa / "GRACE_Maroc_2019_2024.csv", parse_dates=["DATE"])
    g = g[g.GRACE_LWE_CM > -900]
    t = g.groupby("DATE").GRACE_LWE_CM.mean().reset_index()
    t["annee_dec"] = t.DATE.dt.year + (t.DATE.dt.dayofyear - 1) / 365
    slope, intercept = np.polyfit(t.annee_dec, t.GRACE_LWE_CM, 1)
    by_year = g.groupby(g.DATE.dt.year).GRACE_LWE_CM.mean().rename("lwe_moy_cm").reset_index()
    by_year.columns = ["annee", "lwe_moy_cm"]
    fm = g[(g.LAT.between(33, 34.5)) & (g.LON.between(-5.7, -3.0))]
    fm_slope = np.nan
    if fm.DATE.nunique() > 3:
        ft = fm.groupby("DATE").GRACE_LWE_CM.mean().reset_index()
        fm_slope = np.polyfit(ft.DATE.dt.year + (ft.DATE.dt.dayofyear - 1) / 365, ft.GRACE_LWE_CM, 1)[0]
    zones_lat = g.assign(bande=pd.cut(g.LAT, [20, 28, 31, 34, 37], labels=["sud<28N", "28-31N", "31-34N", "nord>34N"]))
    bands = zones_lat.groupby("bande", observed=True).apply(
        lambda d: np.polyfit(d.groupby("DATE").GRACE_LWE_CM.mean().index.year +
                             (d.groupby("DATE").GRACE_LWE_CM.mean().index.dayofyear - 1) / 365,
                             d.groupby("DATE").GRACE_LWE_CM.mean().values, 1)[0]).rename("tendance_cm_an").reset_index()
    summary = dict(tendance_maroc_cm_an=slope, variation_2019_2024_cm=slope * 5,
                   tendance_fes_meknes_cm_an=fm_slope, nb_dates=len(t),
                   incertitude_moy_cm=g.GRACE_UNC_CM.mean())
    return t, by_year, bands, summary


# ---------------------------------------------------------------- GRACE SUPERVISOR (per zone)
def grace_niveau(slope, significatif):
    vig, ale, cri = HYP["grace_seuils_cm_an"]
    if slope <= cri and significatif:
        return "critique"
    if slope <= ale and significatif:
        return "alerte"
    if slope <= vig:
        return "vigilance"
    return "normal"


def add_grace_supervisor(donnees_nasa, zones):
    """For each zone: water storage trend (GRACE) around the zone, significance and supervision level."""
    g = pd.read_csv(donnees_nasa / "GRACE_Maroc_2019_2024.csv", parse_dates=["DATE"])
    g = g[g.GRACE_LWE_CM > -900]
    rows = []
    for _, z in zones.iterrows():
        d2 = (g.LAT - z.LAT) ** 2 + (g.LON - z.LON) ** 2
        sub = g[d2 <= HYP["grace_rayon_deg"] ** 2]
        ts = sub.groupby("DATE").GRACE_LWE_CM.mean()
        x = ts.index.year + (ts.index.dayofyear - 1) / 365
        (slope, _), cov = np.polyfit(x, ts.values, 1, cov=True)
        se = float(np.sqrt(cov[0, 0]))
        signif = abs(slope) / se >= HYP["grace_signif"]
        first, last = ts[ts.index <= ts.index.min() + pd.DateOffset(months=12)], ts[ts.index > ts.index.max() - pd.DateOffset(months=12)]
        rows.append(dict(zone=z.zone, grace_n_points=sub[["LAT", "LON"]].drop_duplicates().shape[0],
                         grace_tendance_cm_an=slope, grace_erreur_type=se, grace_tendance_significative=bool(signif),
                         grace_baisse_totale_cm=last.mean() - first.mean(), grace_niveau=grace_niveau(slope, signif)))
    return zones.merge(pd.DataFrame(rows), on="zone")


# ---------------------------------------------------------------- main
def _run(donnees_nasa, base_agronomique, out):
    donnees_nasa, base_agronomique, out = Path(donnees_nasa), Path(base_agronomique), Path(out)
    out.mkdir(exist_ok=True)

    crops = pd.read_csv(base_agronomique / "base_cultures_Saiss_Fes.csv", encoding="utf-8-sig")

    zones, yearly, clim = study_climate(donnees_nasa)
    zones = study_soil(donnees_nasa, zones)
    zones = add_grace_supervisor(donnees_nasa, zones)
    yearly.to_csv(out / "etude1_climat_annuel.csv", index=False)

    yld = study_yield(base_agronomique, crops)
    yld.to_csv(out / "etude4_rendement_cultures.csv", index=False)

    rows = []
    for _, z in zones.iterrows():
        for _, c in crops.iterrows():
            wb = crop_water_balance(c, clim[z.zone])
            tex_list = [t.strip() for t in c.soil_type.split(";")]
            s_tex = texture_score(z.texture, tex_list)
            s_ph = range_score(z.ph, c.soil_ph_min, c.soil_ph_max, HYP["ph_slope"])
            rows.append(dict(
                zone=z.zone, crop_id=c.crop_id, crop_name=c.crop_name,
                etc_mm=wb["normal"]["etc"], irrigation_normal_mm=wb["normal"]["irrig"],
                irrigation_annee_seche_mm=wb["sec"]["irrig"],
                s_eau_normal=wb["normal"]["couv"], s_eau_sec=wb["sec"]["couv"],
                s_eau=np.nanmean([wb["normal"]["couv"], wb["sec"]["couv"]]),
                s_temperature=wb["temp"], s_texture=s_tex, s_ph=s_ph,
                s_sol=0.6 * s_tex + 0.4 * s_ph,
                s_altitude=range_score(z.elev_moy_m, c.altitude_min_m, c.altitude_max_m, HYP["alt_slope"])))
    cz = pd.DataFrame(rows).merge(yld[["crop_id", "score_rendement", "fiabilite"]], on="crop_id", how="left")
    cz.round(3).to_csv(out / "etude2_3_scores_culture_zone.csv", index=False)

    zones.drop(columns=["LAT", "LON"]).round(3).to_csv(out / "etude1_3_profil_zones.csv", index=False)

    t, by_year, bands, summary = study_grace(donnees_nasa)
    t.to_csv(out / "etude5_grace_serie_maroc.csv", index=False)
    by_year.round(3).to_csv(out / "etude5_grace_par_annee.csv", index=False)
    bands.round(3).to_csv(out / "etude5_grace_par_bande_latitude.csv", index=False)
    pd.Series(summary).round(3).to_csv(out / "etude5_grace_resume.csv", header=["valeur"])

    print("== Zones ==")
    print(zones[["zone", "pluie_moy_mm", "et0_moy_mm", "aridite", "nb_annees_seches", "texture", "ph",
                 "elev_moy_m"]].round(2).to_string(index=False))
    print("\n== GRACE ==")
    print(pd.Series(summary).round(3).to_string())
    print("\n== Top 8 cultures par score eau+sol+temperature (moyenne des 13 zones) ==")
    m = cz.groupby("crop_name")[["s_eau", "s_temperature", "s_sol", "s_altitude", "score_rendement"]].mean()
    m["moy"] = m[["s_eau", "s_temperature", "s_sol", "s_altitude"]].mean(axis=1)
    print(m.sort_values("moy", ascending=False).round(2).head(8).to_string())
    print("\n== 8 derniers ==")
    print(m.sort_values("moy").round(2).head(8).to_string())


def run(donnees_nasa, base_agronomique, out, verbose=True):
    """Reads the CSV files (donnees_nasa, base_agronomique) and computes all the studies -> CSV files in `out`."""
    if verbose:
        _run(donnees_nasa, base_agronomique, out)
    else:
        import contextlib, io
        with contextlib.redirect_stdout(io.StringIO()):
            _run(donnees_nasa, base_agronomique, out)


def main():
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser()
    ap.add_argument("--donnees_nasa", default=str(here / "data" / "donnees_nasa"))
    ap.add_argument("--base_agronomique", default=str(here / "data" / "base_agronomique"))
    ap.add_argument("--out", default=str(here / "etudes_output"))
    a = ap.parse_args()
    run(a.donnees_nasa, a.base_agronomique, a.out)


if __name__ == "__main__":
    main()
